"""경로를 판정한다.

호출자: hooks/check_tool_use.py
입력: hook 입력 dict, runner 이름
반환: 
- check_paths는 deny 사유 문자열 또는 통과 시 None
- 사유 문구의 `{token}`에 경로 토큰이 들어간다.
한계: 
- 대상은 Bash 명령의 경로 토큰, Read·Grep·Glob 경로, apply_patch 대상이다.
- 스크립트가 직접 여는 파일은 막지 못한다.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import tempfile
from pathlib import Path

from sub_call import refute_mode
from sub_write import bash_command, is_write_bash, targets
from sub_docs import TOKEN, doc_kind, project_root
from sub_role import first_prompt, main_session, resolve_role
from sub_session import agent_transcript

HOME = Path.home()
GLOBAL = (HOME / ".claude", HOME / ".codex")
SETTINGS = HOME / ".claude" / "settings.json"
TEMP = Path(tempfile.gettempdir())
ROOT = Path(__file__).resolve().parents[2]
LOCAL_SETTINGS = ROOT / "shared" / "settings" / "local.json"
WIKI_SKILL = ROOT / "shared" / "skills" / "wiki" / "SKILL.md"
SKILLS = ROOT / "shared" / "skills"
INCIDENT = ROOT / "log" / "incident"
READ_RULE = re.compile(r"^Read\((.+)\)$")
SPLIT = re.compile(r"[\s;&|<>()=]+")
GIT_BASH = re.compile(r"^/([a-zA-Z])/")
DRIVE = re.compile(r"^[a-zA-Z]:")
ESCAPE = re.compile(r"\\[ntr0abfv\\]")
REGEX_ESCAPE = re.compile(r"\\[.^$*+?()\[\]{}|]")
OPTION_ARG = re.compile(r"/{1,2}[A-Za-z?]{1,2}:?")


# 사용자 설정 deny 목록에서 Read 규칙의 경로 패턴을 읽는다. 설정을 읽지 못하면 빈 목록이다.
def secret_patterns() -> list[str]:
    try:
        deny = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["deny"]
    except (OSError, ValueError, KeyError):
        return []
    return [m.group(1) for rule in deny if (m := READ_RULE.match(rule))]


# 설정은 매 호출에 읽고 누락·오류에는 외부 접근을 허용하지 않는다.
def archive_root() -> Path | None:
    # 설정 파일을 읽지 못하면 보관 폴더 없음으로 처리한다.
    try:
        # 설정의 archive_root 값을 읽어 절대 경로 폴더인지 확인한다.
        value = json.loads(LOCAL_SETTINGS.read_text(encoding="utf-8"))["archive_root"]
        if not isinstance(value, str) or not value.strip():
            return None
        root = Path(value)
        if not root.is_absolute() or not root.is_dir():
            return None

        # 드라이브 루트 전체는 허용하지 않는다.
        root = root.resolve()
        return root if root != Path(root.anchor) else None
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None


# 경로를 cwd 기준 절대 경로로 만들고 슬래시 표기·대소문자를 통일한다.
def norm(path: str, cwd: Path) -> str:
    if path == "~" or path.startswith(("~/", "~\\")):
        path = str(HOME) + path[1:]
    if os.name == "nt":
        path = GIT_BASH.sub(lambda m: m.group(1).upper() + ":/", path)
    return os.path.normcase(os.path.normpath(os.path.join(cwd, path))).replace("\\", "/")


# 정규화한 경로가 base 폴더 자신이거나 그 안이면 True이다.
def inside(path: str, base: Path) -> bool:
    base_s = os.path.normcase(os.path.normpath(base)).replace("\\", "/")
    return path == base_s or path.startswith(base_s + "/")


# 8.3 짧은 이름과 긴 이름을 같은 경로로 보도록 realpath로 풀어 비교한다.
def in_temp(path: str) -> bool:
    return inside(norm(os.path.realpath(path), Path(".")), Path(os.path.realpath(TEMP)))


# 어느 저장소 세션이든 nestlab 사건 문서 폴더는 읽기·쓰기를 허용한다. 링크 탈출은 realpath로 대조한다.
def in_incident(path: str) -> bool:
    path = norm(path, Path("."))
    return inside(path, INCIDENT) and inside(norm(os.path.realpath(path), Path(".")), INCIDENT.resolve())


# 경로가 보안 문서 패턴 중 하나와 맞으면 True이다.
def is_secret(path: str, patterns: list[str]) -> bool:
    # 경로를 구간으로 나눈다.
    parts = path.split("/")
    for pattern in patterns:
        # 패턴을 정규화하고 홈 기준 또는 절대 패턴은 전체 경로로 비교한다.
        pattern = os.path.normcase(pattern).replace("\\", "/")
        pattern = pattern.removeprefix("//") if pattern.startswith("//**") else pattern
        if pattern.startswith(("~", "/")):
            if fnmatch.fnmatchcase(path, norm(pattern[1:] if pattern.startswith("//") else pattern, HOME)):
                return True
            continue

        # 상대 패턴은 경로의 어느 하위 구간과도 비교한다.
        pattern = pattern.removeprefix("**/")
        if any(fnmatch.fnmatchcase("/".join(parts[i:]), pattern) for i in range(len(parts))):
            return True
    return False


# `'a\nb'`처럼 이스케이프 시퀀스뿐인 문자열이다. 드라이브·UNC·상위·홈 표기와 슬래시 경로는 해당하지 않는다.
def is_escape_text(token: str) -> bool:
    if "/" in token or DRIVE.match(token) or token.startswith(("..", "~", "\\\\")):
        return False
    return "\\" in token and "\\" not in ESCAPE.sub("", token)


# `"\.active"`처럼 정규식 이스케이프(`\.`, `\(`, `\[` 등)가 든 따옴표 문자열이다. 드라이브·UNC·상위·홈 표기는 해당하지 않는다.
def is_regex_text(token: str) -> bool:
    if DRIVE.match(token) or token.startswith(("..", "~", "\\\\")):
        return False
    return bool(REGEX_ESCAPE.search(token))


# (토큰, 경로 표기 여부)이다.
def command_paths(command: str) -> list[tuple[str, bool]]:
    # 경로 후보 목록을 시작한다.
    out = []
    for token in TOKEN.findall(command):
        # 따옴표를 벗기고 문장·패턴·이스케이프 문자열은 경로에서 뺀다.
        quoted = token[0] in "\"'"
        token = token.strip("\"'")
        if quoted and SPLIT.search(token):  # 따옴표 안 문장·패턴은 경로가 아니다.
            continue
        if quoted and (is_escape_text(token) or is_regex_text(token)):
            continue

        # 옵션, URL, null 장치와 짧은 슬래시 옵션 인자를 뺀다.
        if not token or token.startswith("-") or "://" in token or token == "/dev/null":
            continue
        # `cmd //c`, `findstr /s`처럼 슬래시로 시작하는 짧은 옵션 인자와 단독 구분자는 경로가 아니다.
        if OPTION_ARG.fullmatch(token) or token in ("/", "\\"):
            continue
        out.append((token, "/" in token or "\\" in token or token.startswith(("~", ".."))))
    return out


# 설치된 스킬과 원본의 경계를 개별 스킬 폴더로 유지한다.
def skill_paths() -> list[tuple[Path, Path]]:
    out = []
    for base in (SKILLS, HOME / ".agents" / "skills", HOME / ".claude" / "skills", HOME / ".codex" / "skills"):
        if base.is_dir():
            out.append((base, base.resolve()))
            out.extend((child, child.resolve()) for child in base.iterdir() if child.is_dir())
    return out


REASON_SECRET = "{token}에는 읽기와 쓰기를 허용하지 않습니다. 보안 문서입니다."
REASON_SKILL_LINK = "{token}에는 접근을 허용하지 않습니다. 스킬 밖을 가리키는 링크입니다."
REASON_SKILL_WRITE = "{token}에는 쓸 수 없습니다. 외부 스킬은 읽기만 허용합니다."
REASON_NODEBASE_LINK = "{token}에는 접근을 허용하지 않습니다. Nodebase 밖을 가리키는 링크입니다."
REASON_OUTSIDE = "{token}에는 접근을 허용하지 않습니다. 세션 경로 밖입니다."
REASON_SUB_OUTSIDE = "{token}에는 접근을 허용하지 않습니다. 서브에이전트는 저장소 안(사건 문서 폴더 제외)만 접근할 수 있습니다."
REASON_SUB_DOC = "{token}에는 접근을 허용하지 않습니다. {role}은 {allowed} 작업 문서만 읽을 수 있습니다."
REASON_REFUTE_BASH = "반증 reviewer는 Bash를 쓸 수 없습니다. 반증 문서와 출처만 읽고 판정하십시오."
# 서브에이전트 역할별로 읽을 수 있는 작업 문서 종류이다. 경과 문서(state)는 어느 역할도 읽지 못한다.
DOC_ACCESS = {"researcher": {"research"}, "worker": {"input"}, "documenter": {"input"}, "reviewer": {"input"}}


# 서브에이전트가 읽을 수 있는 작업 문서 종류와 반증 reviewer 여부이다. 역할을 모르면 (None, False)이다.
def doc_access(data: dict, role: str | None) -> tuple[set[str] | None, bool]:
    # 반증 reviewer는 첫 요청의 작업 종류로 가리고 반증 문서만 읽는다.
    refute = role == "reviewer" and refute_mode(first_prompt(agent_transcript(data) or data.get("transcript_path")))
    return ({"refute"} if refute else DOC_ACCESS.get(role)), refute

# 도구 입력의 경로가 보안 문서나 세션 경로 밖이면 deny 사유를 반환한다. 통과면 None이다.
def check_paths(data: dict, runner: str) -> str | None:
    # 판정에 쓰는 기준 경로와 설정을 읽는다.
    cwd = Path(data.get("cwd") or os.getcwd())
    project = project_root(cwd)
    skills = skill_paths()
    patterns = secret_patterns()
    nodebase = archive_root()
    sub = not main_session(data)
    role = resolve_role(data) if sub else None
    allowed, refute = doc_access(data, role) if sub else (None, False)
    if sub and refute and data.get("tool_name") == "Bash":
        return REASON_REFUTE_BASH

    # 도구별로 검사할 경로 항목과 쓰기 여부를 구한다.
    name = data.get("tool_name")
    if name == "Bash":
        write = is_write_bash(bash_command(data))
        items = [(norm(t, cwd), t, looks) for t, looks in command_paths(bash_command(data))]
    elif name in ("apply_patch", "Edit", "Write", "NotebookEdit"):
        write = True
        items = [(norm(str(p), cwd), str(p), True) for p in targets(data) or []]
    elif name in ("Read", "Grep", "Glob"):
        tin = data.get("tool_input") or {}
        write = False
        p = tin.get("file_path") or tin.get("path") or "."
        items = [(norm(str(p), cwd), str(p), True)]
    else:
        return None

    # 항목마다 보안 문서, 스킬, Nodebase, 저장소와 허용 폴더 순으로 판정한다.
    for path, token, looks in items:
        real = norm(os.path.realpath(path), cwd)
        if is_secret(path, patterns) or is_secret(real, patterns):
            return REASON_SECRET.format(token=token)
        # 서브에이전트는 저장소 안이고 사건 문서 폴더 밖인 경로만 허용한다.
        if sub and looks:
            if not inside(real, project) or inside(real, INCIDENT) or inside(real, INCIDENT.resolve()):
                return REASON_SUB_OUTSIDE.format(token=token)
            # 작업 문서는 역할별 허용 종류만 읽을 수 있다.
            kind = doc_kind(os.path.relpath(real, norm(str(project), cwd)).replace("\\", "/"))
            if allowed is not None and kind and kind not in allowed:
                return REASON_SUB_DOC.format(token=token, role=role, allowed=" ".join(sorted(allowed)))
            continue
        # 짧은 루트 표기와 링크 탈출은 실제 상위 경로도 대조한다.
        skill = next(((base, target) for base, target in reversed(skills)
                      if inside(path, base) or inside(path, target)), None)
        if skill:
            if not inside(real, skill[1]):
                return REASON_SKILL_LINK.format(token=token)
            if write and not inside(real, project):
                return REASON_SKILL_WRITE.format(token=token)
            continue
        if nodebase and (inside(path, nodebase) or inside(real, nodebase)
                         or any(norm(os.path.realpath(parent), cwd) == norm(str(nodebase), cwd)
                                for parent in Path(path).parents)):
            if not inside(real, nodebase):
                return REASON_NODEBASE_LINK.format(token=token)
            continue
        if not looks or inside(real, project) or in_incident(path):
            continue
        if not write and any(real == norm(os.path.realpath(p), cwd)
                             for p in (LOCAL_SETTINGS, WIKI_SKILL)):
            continue
        if in_temp(path):
            continue
        if any(inside(path, g) for g in GLOBAL):  # 쓰기는 설정 ask와 샌드박스 승인에 맡기고, 인증 파일은 보안 문서 판정이 막는다.
            continue
        return REASON_OUTSIDE.format(token=token)
    return None
