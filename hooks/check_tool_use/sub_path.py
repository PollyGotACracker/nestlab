"""경로 판정이다. Codex rules는 명령 앞부분만 비교해 경로를 판정할 수 없어 hook이 맡는다.
- Claude와 Codex 모두 읽기·쓰기를 판정한다. Claude의 읽기 대상에는 Read·Grep·Glob 도구가 포함된다.

- 보안 문서는 읽기·쓰기를 deny한다. 목록은 `~/.claude/settings.json` `permissions.deny`의 `Read(...)` 항목이다.
- 세션 경로(cwd) 밖 경로는 deny한다. `~/.claude`·`~/.codex`는 읽기만 허용한다.
- OS 임시 폴더(`tempfile.gettempdir()`)는 읽기·쓰기를 허용한다. 보안 문서 deny는 임시 폴더에도 적용한다.
- 대상은 Bash 명령의 경로 토큰, Read·Grep·Glob 경로, apply_patch 대상이다. 스크립트가 직접 여는 파일은 막지 못한다.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import tempfile
from pathlib import Path

from sub_write import bash_command, is_write_bash, targets

HOME = Path.home()
GLOBAL = (HOME / ".claude", HOME / ".codex")
SETTINGS = HOME / ".claude" / "settings.json"
TEMP = Path(tempfile.gettempdir())
READ_RULE = re.compile(r"^Read\((.+)\)$")
SPLIT = re.compile(r"[\s;&|<>()=]+")
TOKEN = re.compile(r"\"[^\"]*\"|'[^']*'|[^\s;&|<>()=\"']+")
GIT_BASH = re.compile(r"^/([a-zA-Z])/")


def secret_patterns() -> list[str]:
    try:
        deny = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["deny"]
    except (OSError, ValueError, KeyError):
        return []
    return [m.group(1) for rule in deny if (m := READ_RULE.match(rule))]


def norm(path: str, cwd: Path) -> str:
    """경로를 cwd 기준 절대 경로로 만들고 슬래시 표기·대소문자를 통일한다."""
    if path == "~" or path.startswith(("~/", "~\\")):
        path = str(HOME) + path[1:]
    if os.name == "nt":
        path = GIT_BASH.sub(lambda m: m.group(1).upper() + ":/", path)
    return os.path.normcase(os.path.normpath(os.path.join(cwd, path))).replace("\\", "/")


def inside(path: str, base: Path) -> bool:
    base_s = os.path.normcase(os.path.normpath(base)).replace("\\", "/")
    return path == base_s or path.startswith(base_s + "/")


def in_temp(path: str) -> bool:
    """8.3 짧은 이름과 긴 이름을 같은 경로로 보도록 realpath로 풀어 비교한다."""
    return inside(norm(os.path.realpath(path), Path(".")), Path(os.path.realpath(TEMP)))


def is_secret(path: str, patterns: list[str]) -> bool:
    parts = path.split("/")
    for pattern in patterns:
        pattern = os.path.normcase(pattern).replace("\\", "/")
        pattern = pattern.removeprefix("//") if pattern.startswith("//**") else pattern
        if pattern.startswith(("~", "/")):
            if fnmatch.fnmatchcase(path, norm(pattern[1:] if pattern.startswith("//") else pattern, HOME)):
                return True
            continue
        pattern = pattern.removeprefix("**/")
        if any(fnmatch.fnmatchcase("/".join(parts[i:]), pattern) for i in range(len(parts))):
            return True
    return False


def command_paths(command: str) -> list[tuple[str, bool]]:
    """(토큰, 경로 표기 여부)이다."""
    out = []
    for token in TOKEN.findall(command):
        quoted = token[0] in "\"'"
        token = token.strip("\"'")
        if quoted and SPLIT.search(token):  # 따옴표 안 문장·패턴은 경로가 아니다.
            continue
        if not token or token.startswith("-") or "://" in token or token == "/dev/null":
            continue
        out.append((token, "/" in token or "\\" in token or token.startswith(("~", ".."))))
    return out


def check_paths(data: dict, runner: str) -> str | None:
    cwd = Path(data.get("cwd") or os.getcwd())
    patterns = secret_patterns()
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
    for path, token, looks in items:
        if is_secret(path, patterns):
            return f"보안 문서는 읽기·쓰기를 허용하지 않습니다: {token}"
        if not looks or inside(path, cwd):
            continue
        if in_temp(path):
            continue
        if any(inside(path, g) for g in GLOBAL):
            if write:
                return f"~/.claude·~/.codex는 읽기만 허용합니다: {token}"
            continue
        return f"세션 경로 밖 접근은 허용하지 않습니다: {token}"
    return None
