"""백틱 토큰을 저장소 경로로 해석하고 존재 여부를 확인한다."""
from __future__ import annotations

import re
from pathlib import Path

from sub_config import (
    DIRECT_PATH_ROOTS,
    FILE_EXT,
    LINE_SUFFIX,
    PATH_CHARS,
    REPO_EXTS,
    ROOT,
    SKIP_DIRS,
)


# SKIP_DIRS를 뺀 저장소의 파일·폴더 이름을 소문자로 모은다.
def build_name_index() -> set[str]:
    names: set[str] = set()
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        names.add(path.name.lower())
    return names


# 중괄호 선택지(`{a,b}`)를 모든 조합의 패턴으로 펼친다.
def expand_braces(pattern: str) -> list[str]:
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    head, tail = pattern[: match.start()], pattern[match.end() :]
    results: list[str] = []
    for option in match.group(1).split(","):
        results.extend(expand_braces(head + option + tail))
    return results


# 구간에 비어 있지 않은 이름과 REPO_EXTS 확장자가 있는지 확인한다.
def has_repo_ext(segment: str) -> bool:
    match = FILE_EXT.search(segment)
    return bool(match) and match.start() > 0 and match.group(1).lower() in REPO_EXTS


# 정규화한 경로 후보를 반환한다.
# 토큰이 경로가 아니면 None이다.
# 슬래시 없는 이름은 저장소 파일 확장자가 있어야 한다.
# 슬래시 경로는 끝 슬래시, 저장소 파일 확장자, 저장소에 있는 첫 구간 중 하나가 있어야 한다.
def as_repo_path(token: str, names: set[str]) -> str | None:
    token = LINE_SUFFIX.sub("", token.strip())
    if not token or "<" in token or ">" in token or "$" in token:
        return None
    if "://" in token or not PATH_CHARS.match(token):
        return None
    if "/" not in token:
        return token if has_repo_ext(token) else None
    if not re.search(r"[A-Za-z]", token):
        return None
    first = token.split("/", 1)[0]
    last = token.rstrip("/").rsplit("/", 1)[-1]
    if (
        token.endswith("/")
        or any(has_repo_ext(option) for option in expand_braces(last))
        or first in (".", "..")
        or first.lower() in names
    ):
        return token
    return None


# 토큰이 DIRECT_PATH_ROOTS 아래 경로이면 첫 구간을 반환한다.
# 자리표시자 경로도 포함한다.
def direct_path_root(token: str) -> str | None:
    token = LINE_SUFFIX.sub("", token.strip()).replace("\\", "/")
    while token.startswith("./"):
        token = token[2:]
    if "/" not in token or "://" in token:
        return None
    first = token.split("/", 1)[0]
    return first if first in DIRECT_PATH_ROOTS else None


# 패턴 하나가 저장소 루트나 문서 폴더 기준으로 있는지 확인한다.
def single_path_exists(pattern: str, base_dir: Path, names: set[str]) -> bool:
    pattern = pattern.rstrip("/") or pattern
    is_glob = any(c in pattern for c in "*?")
    if "/" not in pattern and not is_glob:
        # 슬래시 없는 파일 이름은 문서 옆이나 저장소 전체에서 찾는다.
        return (base_dir / pattern).exists() or pattern.lower() in names
    for base in (ROOT, base_dir):
        if is_glob and any(base.glob(pattern)):
            return True
        if not is_glob and (base / pattern).exists():
            return True
    return False


# 경로 후보가 있는지 확인한다.
# 중괄호 선택지는 모두 있어야 하고 glob은 하나 이상 일치해야 한다.
def path_exists(candidate: str, base_dir: Path, names: set[str]) -> bool:
    return all(
        single_path_exists(pattern, base_dir, names)
        for pattern in expand_braces(candidate)
    )


# 저장소 루트나 문서 폴더 기준으로 파일을 찾는다.
# 파일이 없으면 None이다.
def resolve_file(candidate: str, base_dir: Path) -> Path | None:
    for base in (ROOT, base_dir):
        path = base / candidate
        if path.is_file():
            return path
    return None
