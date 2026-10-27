"""hook 입력에서 쓰기 대상 경로와 새로 들어가는 내용을 읽고, Bash 쓰기 명령을 판정하는 공용 함수이다.

호출자:
- hooks/check_tool_use.py
- hooks/check_tool_use/ 모듈
- tools/check_doc_rule.py
반환:
- bash_write_reason, prompt_reason, git_write_reason, description_reason: 차단 사유 문자열 또는 통과 시 None
- is_write_bash: bool
- command_parts: 하위 명령 문자열 목록
- interpreter_kind: "inline", "script" 또는 None
- targets: 쓰기 대상 Path 목록(쓰기가 아니면 None, 대상을 모르는 쓰기는 빈 목록)
- added_lines: (새로 들어가는 줄 목록, 쓰기 후 전문 또는 None)
한계: 정적 패턴 판정이라 임의 프로그램 내부의 쓰기는 판별하지 못한다. 인라인 코드의 쓰기 호출 판정은 문자열 패턴 검사라 함수 이름을 조립하는 코드는 잡지 못한다.
"""
from __future__ import annotations

import re
from pathlib import Path

# Bash 쓰기 판정은 패턴 기반이다.
# 우회한 저장소 밖 쓰기는 Codex 권한 프로필과 Claude Code 샌드박스가 2차로 막는다.
# 따옴표 안 문자열과 heredoc 본문은 지우고 확인한다.
# 리다이렉트 대상이 &(2>&1)거나 null 장치(/dev/null, $null, NUL)면 쓰기로 보지 않는다.
SHELLS = ("Bash", "PowerShell")
RETRY = "다른 방식으로 작업하십시오."
QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\b", re.S)
WRITE_BASH = re.compile(r"(^|[;&|]\s*)(rm|rmdir|mv|cp|mkdir|touch|tee|Set-Content|Out-File|New-Item|Remove-Item)\b"
                        r"|\bgit\s+(?:checkout\s+--(?=\s|$)|(?:restore|apply|reset\s+--hard)\b)|\bfind\b[^;&|]*\s-delete\b|\bxargs\s+rm\b"
                        r"|\bsed\s+-i\b|(?<![&>])>{1,2}\s*(?!&|/dev/null\b|\$null\b|NUL\b)[^\s>]", re.I)
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.M)


# 따옴표 안과 heredoc 본문을 지운 명령을 반환한다.
def plain(command: str) -> str:
    return QUOTED.sub('""', HEREDOC.sub("", command))


# 따옴표 안과 heredoc 본문을 지운 명령에 쓰기 패턴이 있으면 True를 반환한다.
def is_write_bash(command: str) -> bool:
    return bool(WRITE_BASH.search(plain(command)))


# 전용 도구(Claude의 Edit과 Write, Codex의 apply_patch)로 대신할 수 있는 내용 쓰기이다.
# 하위 명령 하나라도 맞으면 deny한다.
# rm, mv, cp, mkdir은 대신할 도구가 없어 이 판정이 아닌 is_write_bash가 맡는다.
SUBCOMMAND = re.compile(r"\|\||&&|\|&|[;|&\n]")
SHELL_C = re.compile(r"(?:^|[\s;&|(])(?:(?:ba|z)?sh\s+-[a-z]*c|(?:powershell|pwsh)(?:\.exe)?\s+-(?:c|command))"
                     r"\s+(\"[^\"]*\"|'[^']*')", re.I)
CONTENT_WRITE = re.compile(
    r"^(?:tee|touch|Set-Content|Add-Content|Out-File|New-Item)\b|^sed\b[^\n]*?\s-[a-zA-Z]*i", re.I)
# 인터프리터 옵션이다. `-X utf8`과 `-W ignore`만 값을 따로 받는다.
OPTIONS = r"(?:\s+(?:-[XW]\s+[\w=,.:]+|-[A-Za-z]+))*"
INLINE_CODE = re.compile(rf"^(?:python3?|py|node|perl|ruby)(?:\.exe)?\b{OPTIONS}\s+-(?:c|e)\b"
                         rf"|^(?:python3?|py|node)(?:\.exe)?{OPTIONS}\s+-\s*(?:<<|$)")
# 인라인 코드 안의 파일 쓰기, 삭제, 프로세스 실행 호출이다.
WRITE_CALL = re.compile(
    r"\bopen\s*\(\s*[^,)]+,\s*(?:mode\s*=\s*)?['\"][rbt]*[wax+][rbt+]*['\"]|\.open\(\s*['\"][rbt]*[wax+]"
    r"|\.write_text\(|\.write_bytes\(|\bos\.(?:remove|unlink|rename|rmdir|removedirs|replace|makedirs|mkdir|system|popen)\b"
    r"|\bshutil\b|\bsubprocess\b|\bchild_process\b"
    r"|\b(?:writeFile|appendFile|writeFileSync|appendFileSync|unlink|unlinkSync|rmSync|renameSync|mkdirSync)\b")
ARITH = re.compile(r"\$\(\([^)]*\)\)")
REDIRECT = re.compile(r"(?<![<&>\d=-])\d?>{1,2}(?!&)\s*([^\s>]+)")
NULL = re.compile(r"^(?:/dev/null|\$null|NUL)$", re.I)
PREFIX = re.compile(r"^(?:(?:sudo|command|exec|env|time|nohup)\s+|[A-Za-z_]\w*=\S*\s+)+")
AMPERSAND = re.compile(r"\d*>&\d*|&>")

REASON_INLINE = "이 명령은 파일을 쓰거나 지우는 인라인 코드를 실행합니다. " + RETRY
REASON_REDIRECT = "이 명령은 리다이렉트로 `{target}`에 내용을 씁니다. " + RETRY
REASON_CONTENT = "이 명령은 `{command}`로 파일 내용을 씁니다. " + RETRY


# 전용 도구로 대신할 내용 쓰기면 사유를 반환한다. 읽기와 테스트, 쓰기 호출이 없는 인라인 코드는 None이다.
def bash_write_reason(command: str) -> str | None:
    # sh -c 안의 명령을 먼저 같은 규칙으로 검사한다.
    for inner in SHELL_C.findall(command):
        reason = bash_write_reason(inner[1:-1])
        if reason:
            return reason

    # 인라인 코드의 쓰기 호출은 heredoc 본문까지 포함한 원문에서 찾고, 나머지 판정은 heredoc 본문을 뺀 명령으로 한다.
    writes = bool(WRITE_CALL.search(command))
    command = HEREDOC.sub("", command)
    quoted: list[str] = []

    # 따옴표 문자열을 보관하고 자리표시자를 반환한다.
    def hold(m):
        quoted.append(m[0][1:-1])
        return f'"\x00{len(quoted) - 1}\x00"'

    # 자리표시자로 바꾼 명령에서 산술 표현을 지우고 복원 함수를 만든다.
    text = ARITH.sub("", QUOTED.sub(hold, command))
    restore = lambda s: re.sub(r'"?\x00(\d+)\x00"?', lambda m: quoted[int(m[1])], s)

    # 첫 줄이 쓰기 호출이 있는 인라인 코드 실행이면 막는다.
    if writes and INLINE_CODE.search(PREFIX.sub("", text.strip().split("\n", 1)[0])):
        return REASON_INLINE

    # 하위 명령마다 내용 쓰기, 인라인 코드, 리다이렉트를 검사한다.
    for part in SUBCOMMAND.split(text):
        part = PREFIX.sub("", part.strip())
        if not part:
            continue
        if CONTENT_WRITE.search(part):
            return REASON_CONTENT.format(command=part.split()[0])
        if writes and INLINE_CODE.search(part):
            return REASON_INLINE
        for target in REDIRECT.findall(part):
            target = restore(target)
            if not NULL.match(target):
                return REASON_REDIRECT.format(target=target)
    return None


# 명령과 그 안의 셸 -c 스크립트를 모두 내보낸다.
def scripts(command: str):
    yield command
    for inner in SHELL_C.findall(command):
        yield from scripts(inner[1:-1])


# 하위 명령 목록이다. 따옴표 안과 heredoc 본문은 지우고 `2>&1`의 &는 구분자로 보지 않는다.
def command_parts(command: str) -> list[str]:
    return [PREFIX.sub("", p.strip()) for s in scripts(command) for p in SUBCOMMAND.split(AMPERSAND.sub("", plain(s)))
            if p.strip()]


INTERPRETER = re.compile(r"^(?:python3?|py|node)(?:\.exe)?(?=\s|$)")

# 하위 명령이 인터프리터 호출이면 "inline"(-c, -e, 표준 입력) 또는 "script"(파일, 모듈)를 반환한다. 아니면 None이다.
def interpreter_kind(part: str) -> str | None:
    if not INTERPRETER.match(part):
        return None
    if INLINE_CODE.search(part):
        return "inline"
    if re.search(r"\s(?:-V|--version|-h|--help)$", part):
        return None
    return "script"


# 승인 전 메인 세션에서 막는 git 쓰기 하위 명령이다.
GIT_WRITE = re.compile(
    r"^git(?:\s+-C\s+\S+)?\s+(?:commit|add|push|rm|mv|checkout|switch|stash(?!\s+(?:list|show)\b)|tag|pull|fetch|apply|merge|am"
    r"|init|clone|submodule|worktree|gc|reset|update-index|read-tree|checkout-index|sparse-checkout|update-ref"
    r"|symbolic-ref|replace|notes|config\s+--(?:add|unset|unset-all|replace-all|rename-section|remove-section|edit)"
    r"|branch\s+(?:-[dDmMcC]|--(?:delete|move|copy|force|set-upstream-to|unset-upstream))"
    r"|remote\s+(?:add|remove|rename|set-url))(?=\s|$)")
REASON_GIT_WRITE = "승인 전에는 git 쓰기 명령을 실행할 수 없습니다. " + RETRY


# 하위 명령 하나라도 git 쓰기 명령이면 사유를 반환한다. 아니면 None이다.
def git_write_reason(command: str) -> str | None:
    return REASON_GIT_WRITE if any(GIT_WRITE.match(p) for p in command_parts(command)) else None


REASON_GIT_C = "이 명령은 git -c로 설정을 바꿔 실행합니다. " + RETRY
GIT_LOWER_C = re.compile(r"\bgit\s+-c\b")
REASON_CD = "이 명령은 cd로 작업 위치를 바꿉니다. " + RETRY
CD_CMD = re.compile(
    r"(?:^|[;&|(\n])\s*(?:(?:builtin|command)\s+)?(?:cd|pushd|chdir|Set-Location|sl|Push-Location)(?=[\s;&|)]|$)", re.I)


# cd류 명령이나 git -c가 든 명령이면 사유를 반환한다. 아니면 None이다. cd를 가장 먼저 검사한다.
def prompt_reason(command: str) -> str | None:
    # 따옴표 안 스크립트(bash -c 등)까지 같은 규칙으로 찾는다.
    texts = [plain(s) for s in scripts(command)]
    if any(CD_CMD.search(t) for t in texts):
        return REASON_CD
    if any(GIT_LOWER_C.search(t) for t in texts):
        return REASON_GIT_C
    return None


REASON_DESC_EMPTY = "Bash 설명(description)이 비었습니다. " + RETRY
REASON_DESC_COPY = "Bash 설명(description)이 명령을 그대로 옮겼습니다. " + RETRY

# Claude Bash 설명 칸이 비었거나 명령을 그대로 옮겼으면 사유를 반환한다.
def description_reason(data: dict, runner: str) -> str | None:
    # Claude Bash 호출만 검사한다.
    if runner != "claude" or data.get("tool_name") != "Bash":
        return None

    # 설명과 명령의 공백을 정리한다.
    tin = data.get("tool_input") or {}
    desc = re.sub(r"\s+", " ", str(tin.get("description") or "")).strip()
    command = re.sub(r"\s+", " ", bash_command(data)).strip()

    # 빈 설명과 명령 복사를 막는다.
    if not desc:
        return REASON_DESC_EMPTY
    if desc == command or (len(command) > 12 and command in desc):
        return REASON_DESC_COPY
    return None


# 중첩된 dict와 list 안의 문자열을 모두 내보낸다.
def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


# 파일이면 내용을, 아니면 빈 문자열을 반환한다.
def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


# Bash 입력의 명령을 한 문자열로 반환한다.
def bash_command(data: dict) -> str:
    cmd = (data.get("tool_input") or {}).get("command")
    return " ".join(cmd) if isinstance(cmd, list) else cmd or ""


# 쓰기 대상 경로를 반환한다. 쓰기가 아니면 None, 대상을 모르는 쓰기면 빈 목록이다.
def targets(data: dict) -> list[Path] | None:
    # 도구 이름과 기준 폴더를 읽는다.
    name, tin = data.get("tool_name"), data.get("tool_input") or {}
    cwd = Path(data.get("cwd") or ".")

    # 도구별로 쓰기 대상 경로를 구한다.
    if name in ("Edit", "Write", "NotebookEdit"):
        return [cwd / (tin.get("file_path") or tin.get("notebook_path") or "")]
    if name == "apply_patch":
        return [cwd / (a or b).strip() for s in strings(tin) for a, b in PATCH_FILE.findall(s)]
    if name in SHELLS:
        return [] if is_write_bash(bash_command(data)) else None
    return None


# (새로 들어가는 줄, 쓰기 후 전문 또는 None)이다.
def added_lines(data: dict, path: Path) -> tuple[list[str], str | None]:
    # 기존 내용과 도구 입력을 읽는다.
    tin = data.get("tool_input") or {}
    old = read(path)
    name = data.get("tool_name")

    # 도구별로 쓰기 후 전문을 구한다.
    if name == "Write":
        new = tin.get("content", "")
    elif name == "Edit":
        new = old.replace(tin.get("old_string", ""), tin.get("new_string", ""), -1 if tin.get("replace_all") else 1)
    else:
        # apply_patch는 path와 같은 파일 구간의 + 줄만 본다. 파일 머리줄 앞의 + 줄은 모든 path의 줄로 본다.
        # 전문이 필요한 원문 보존 검사는 생략한다.
        cwd = Path(data.get("cwd") or ".")
        plus, mine = [], None
        for s in strings(tin):
            for line in s.splitlines():
                head = PATCH_FILE.match(line)
                if head:
                    target = cwd / (head[1] or head[2]).strip()
                    # Move to 줄은 앞선 Update 구간에 속한다.
                    mine = (path == target) if head[1] else bool(mine) or path == target
                elif line.startswith("+") and not line.startswith("+++") and mine is not False:
                    plus.append(line[1:])
        return plus, None

    # 기존에 없던 줄만 골라 반환한다.
    old_lines = set(old.splitlines())
    return [l for l in new.splitlines() if l.strip() and l not in old_lines], new
