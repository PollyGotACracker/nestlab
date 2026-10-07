"""hook 입력에서 쓰기 대상 경로와 새로 들어가는 내용을 읽는 공용 함수이다."""
from __future__ import annotations

import re
from pathlib import Path

# ponytail: Bash 쓰기 판정은 패턴 기반이다. 우회는 sandbox_mode·readonly가 2차로 막는다.
WRITE_BASH = re.compile(r"(^|[;&|]\s*)(rm|mv|cp|mkdir|touch|tee|Set-Content|Out-File|New-Item|Remove-Item)\b"
                        r"|\bsed\s+-i\b|(?<![0-9&>])>{1,2}\s*[^&\s>]")
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.M)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def bash_command(data: dict) -> str:
    cmd = (data.get("tool_input") or {}).get("command")
    return " ".join(cmd) if isinstance(cmd, list) else cmd or ""


def targets(data: dict) -> list[Path] | None:
    """쓰기 대상 경로를 반환한다. 쓰기가 아니면 None, 대상을 모르는 쓰기면 빈 목록이다."""
    name, tin = data.get("tool_name"), data.get("tool_input") or {}
    cwd = Path(data.get("cwd") or ".")
    if name in ("Edit", "Write", "NotebookEdit"):
        return [cwd / (tin.get("file_path") or tin.get("notebook_path") or "")]
    if name == "apply_patch":
        return [cwd / (a or b).strip() for s in strings(tin) for a, b in PATCH_FILE.findall(s)]
    if name == "Bash":
        return [] if WRITE_BASH.search(bash_command(data)) else None
    return None


def added_lines(data: dict, path: Path) -> tuple[list[str], str | None]:
    """(새로 들어가는 줄, 쓰기 후 전문 또는 None)이다."""
    tin = data.get("tool_input") or {}
    old = read(path)
    name = data.get("tool_name")
    if name == "Write":
        new = tin.get("content", "")
    elif name == "Edit":
        new = old.replace(tin.get("old_string", ""), tin.get("new_string", ""), -1 if tin.get("replace_all") else 1)
    else:
        # ponytail: apply_patch는 + 줄만 본다. 전문이 필요한 원문 보존 검사는 생략한다.
        plus = [l[1:] for s in strings(tin) for l in s.splitlines() if l.startswith("+") and not l.startswith("+++")]
        return plus, None
    old_lines = set(old.splitlines())
    return [l for l in new.splitlines() if l.strip() and l not in old_lines], new
