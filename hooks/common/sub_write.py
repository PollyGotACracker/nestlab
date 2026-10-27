"""hook 입력에서 쓰기 대상 경로와 새로 들어가는 내용을 읽고, Bash 쓰기 명령을 판정하는 공용 함수이다.

호출자:
- hooks/check_tool_use.py
- hooks/check_tool_use/ 모듈
- tools/check_doc_rule.py
반환:
- bash_write_reason, description_reason: 차단 사유 문자열 또는 통과 시 None
- is_write_bash: bool
- targets: 쓰기 대상 Path 목록(쓰기가 아니면 None, 대상을 모르는 쓰기는 빈 목록)
- added_lines: (새로 들어가는 줄 목록, 쓰기 후 전문 또는 None)
한계: 정적 패턴 판정이라 임의 프로그램 내부의 쓰기는 판별하지 못한다.
"""
from __future__ import annotations

import re
from pathlib import Path

# Bash 쓰기 판정은 패턴 기반이다.
# 우회한 저장소 밖 쓰기는 Codex workspace-write 샌드박스와 Claude Code readonly가 2차로 막는다.
# 따옴표 안 문자열은 지우고 확인한다. 
# 리다이렉트 대상이 &(2>&1)거나 null 장치(/dev/null, $null, NUL)면 쓰기로 보지 않는다.
QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
WRITE_BASH = re.compile(r"(^|[;&|]\s*)(rm|mv|cp|mkdir|touch|tee|Set-Content|Out-File|New-Item|Remove-Item)\b"
                        r"|\bgit\s+(?:checkout\s+--(?=\s|$)|(?:restore|apply|reset\s+--hard)\b)|\bfind\b[^;&|]*\s-delete\b|\bxargs\s+rm\b"
                        r"|\bsed\s+-i\b|(?<![&>])>{1,2}\s*(?!&|/dev/null\b|\$null\b|NUL\b)[^\s>]", re.I)
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.M)


# 따옴표 안을 지운 명령에 쓰기 패턴이 있으면 True를 반환한다.
def is_write_bash(command: str) -> bool:
    return bool(WRITE_BASH.search(QUOTED.sub('""', command)))


# 전용 도구(Claude의 Edit과 Write, Codex의 apply_patch)로 대신할 수 있는 내용 쓰기이다.
# 하위 명령 하나라도 맞으면 deny한다.
# rm, mv, cp, mkdir은 대신할 도구가 없어 이 판정이 아닌 is_write_bash와 권한 규칙(ask, prompt)이 맡는다.
SUBCOMMAND = re.compile(r"\|\||&&|\|&|[;|&\n]")
SHELL_C = re.compile(r"(?:^|[\s;&|(])(?:ba|z)?sh\s+-c\s+(\"[^\"]*\"|'[^']*')")
CONTENT_WRITE = re.compile(
    r"^(?:tee|touch|Set-Content|Add-Content|Out-File|New-Item)\b|^sed\b[^\n]*?\s-[a-zA-Z]*i", re.I)
INLINE_CODE = re.compile(r"^(?:python3?|py|node|perl|ruby)(?:\.exe)?\b(?:\s+-[A-Za-z]+)*\s+-(?:c|e)\b"
                         r"|^(?:python3?|py|node)(?:\.exe)?\s+-\s*(?:<<|$)")
ARITH = re.compile(r"\$\(\([^)]*\)\)")
REDIRECT = re.compile(r"(?<![<&>\d=-])\d?>{1,2}(?!&)\s*([^\s>]+)")
NULL = re.compile(r"^(?:/dev/null|\$null|NUL)$", re.I)
PREFIX = re.compile(r"^(?:(?:sudo|command|exec|env|time|nohup)\s+|[A-Za-z_]\w*=\S*\s+)+")


TOOL_GUIDE = "다른 쓰기 도구를 선택하여 작업하십시오."
REASON_INLINE = "이 명령은 인라인 코드(python -c, node -e)를 실행합니다."
REASON_REDIRECT = "이 명령은 리다이렉트로 `{target}`에 내용을 씁니다. " + TOOL_GUIDE
REASON_CONTENT = "이 명령은 `{command}`로 파일 내용을 씁니다. " + TOOL_GUIDE

# 전용 도구로 대신할 내용 쓰기면 사유를 반환한다. 읽기와 테스트, 권한 규칙이 맡는 명령은 None이다.
def bash_write_reason(command: str) -> str | None:
    # sh -c 안의 명령을 먼저 같은 규칙으로 검사한다.
    for inner in SHELL_C.findall(command):
        reason = bash_write_reason(inner[1:-1])
        if reason:
            return reason

    # 따옴표 안 문자열은 자리표시자로 바꿔 구분자 판정에서 뺀다.
    quoted: list[str] = []

    # 따옴표 문자열을 보관하고 자리표시자를 반환한다.
    def hold(m):
        quoted.append(m[0][1:-1])
        return f'"\x00{len(quoted) - 1}\x00"'

    # 자리표시자로 바꾼 명령에서 산술 표현을 지우고 복원 함수를 만든다.
    plain = ARITH.sub("", QUOTED.sub(hold, command))
    restore = lambda s: re.sub(r'"?\x00(\d+)\x00"?', lambda m: quoted[int(m[1])], s)

    # 첫 줄이 인라인 코드 실행이면 막는다.
    if INLINE_CODE.search(PREFIX.sub("", plain.strip().split("\n", 1)[0])):
        return REASON_INLINE

    # 하위 명령마다 내용 쓰기, 인라인 코드, 리다이렉트를 검사한다.
    for part in SUBCOMMAND.split(plain):
        part = PREFIX.sub("", part.strip())
        if not part:
            continue
        if CONTENT_WRITE.search(part):
            return REASON_CONTENT.format(command=part.split()[0])
        if INLINE_CODE.search(part):
            return REASON_INLINE
        for target in REDIRECT.findall(part):
            target = restore(target)
            if not NULL.match(target):
                return REASON_REDIRECT.format(target=target)
    return None


SCRIPT_RUN = re.compile(r"(?:python3?|py|node)(?:\.exe)?\s+[^-\s]")
REASON_SHELL_VAR = "이 명령은 셸 변수나 명령 치환을 씁니다."
REASON_LOOP = "이 명령은 반복문을 씁니다."
REASON_TEMP = "이 명령은 OS 임시 폴더 경로를 씁니다."

REASON_GIT_OPTION = "이 명령은 git -C 또는 git -c를 씁니다."
GIT_OPTION = re.compile(r"\bgit\s+-[Cc]\b")
REASON_CD = "이 명령은 cd로 작업 위치를 바꿉니다. cd 없이 경로를 인자로 쓰십시오."
CD_CMD = re.compile(r"(?:^|[;&|(\n])\s*cd\b")
REASON_HEREDOC ="이 명령은 heredoc을 씁니다."
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\b", re.S)
SHELL_ASSIGN = re.compile(r"(?:^|[;&|\n])\s*[A-Za-z_]\w*=[^\s;&|]*\s*(?:;|&&|\n)")
SHELL_EXPAND = re.compile(r"\$(?!null\b)[A-Za-z_{(]")
SHELL_LOOP = re.compile(r"(?:^|[;&|\n])\s*(?:for|while|until)\s")

# 승인 창을 띄우는 셸 변수, 반복문, 임시 폴더 경로가 든 명령이면 사유를 반환한다. 아니면 None이다.
def prompt_reason(command: str) -> str | None:
    # 여러 줄 heredoc은 허용 규칙과 맞지 않아 승인 창이 뜨므로 막는다.
    if HEREDOC.search(command):
        return REASON_HEREDOC

    # 작은따옴표 문자열을 지운 명령으로 변수와 반복문을 확인한다.
    plain = re.sub(r"'[^']*'", "''", HEREDOC.sub("", command))
    if SHELL_ASSIGN.search(plain) or SHELL_EXPAND.search(plain):
        return REASON_SHELL_VAR
    if SHELL_LOOP.search(plain):
        return REASON_LOOP

    # 따옴표 안을 지운 명령에 git -C, git -c가 있으면 승인 창이 뜨므로 막는다.
    if GIT_OPTION.search(QUOTED.sub('""', command)):
        return REASON_GIT_OPTION

    # cd는 명령 사이 작업 위치를 바꿔 상대 경로와 승인 규칙을 어긋나게 하므로 막는다.
    if CD_CMD.search(QUOTED.sub('""', command)):
        return REASON_CD

    # 첫 하위 명령이 스크립트 실행이면 승인 창 규칙(ask, prompt)이 묻도록 임시 폴더 검사를 건너뛴다.
    first = PREFIX.sub("", SUBCOMMAND.split(plain.strip())[0].strip())
    if SCRIPT_RUN.match(first):
        return None

    # 경로 토큰 중 OS 임시 폴더 안을 가리키는 것이 있는지 확인한다. sub_path가 이 모듈을 불러오므로 함수 안에서 불러온다.
    from sub_path import command_paths, in_temp
    if any(is_path and in_temp(token) for token, is_path in command_paths(command)):
        return REASON_TEMP
    return None


REASON_DESC_EMPTY ="Bash 설명(description)이 비었습니다. 명령이 하는 일, 필요한 이유, 파일 변경 여부를 적으십시오."
REASON_DESC_COPY = "Bash 설명(description)이 명령을 그대로 옮겼습니다. 명령이 하는 일을 문장으로 적으십시오."

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
    if name == "Bash":
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
        # apply_patch는 + 줄만 본다. 전문이 필요한 원문 보존 검사는 생략한다.
        plus = [l[1:] for s in strings(tin) for l in s.splitlines() if l.startswith("+") and not l.startswith("+++")]
        return plus, None

    # 기존에 없던 줄만 골라 반환한다.
    old_lines = set(old.splitlines())
    return [l for l in new.splitlines() if l.strip() and l not in old_lines], new
