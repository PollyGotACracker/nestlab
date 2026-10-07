"""절 참조의 대상 파일을 정하고 대상 파일의 제목을 읽는다."""
from __future__ import annotations

from pathlib import Path

from sub_config import (
    FENCE,
    HEADING,
    INLINE_CODE,
    LINE_SUFFIX,
    ROOT,
    SCHEMA_PATTERN,
    SECTION_EXTRA_FILES,
)


# 코드 블록 밖의 제목을 읽어 캐시에 두고 반환한다.
# 양식 파일(SCHEMA_PATTERN)은 코드 블록 안의 제목도 읽는다.
def read_headings(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path not in cache:
        headings: set[str] = set()
        schema_files = {p.resolve() for p in ROOT.glob(SCHEMA_PATTERN)}
        read_in_fence = path.resolve() in schema_files
        in_fence = False
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if FENCE.match(line):
                in_fence = not in_fence
                continue
            match = None if in_fence and not read_in_fence else HEADING.match(line)
            if match:
                title = match.group(2).strip()
                headings.add(title)
                headings.add(title.replace("`", ""))
        cache[path] = headings
    return cache[path]


# 절 제목을 찾을 파일 목록을 반환한다.
# 같은 줄에서 참조 앞의 가장 가까운 백틱 .md 경로를 우선한다.
# 그런 경로가 없으면 현재 파일, 추가 파일, 양식 파일을 차례로 찾는다.
# 적힌 .md 경로가 있으면 None을 반환한다. 지침은 다른 파일의 절을 참조하지 않기 때문이다.
def section_targets(
    line: str, ref_start: int, current: Path, names: set[str]
) -> list[Path] | None:
    target_token = None
    for match in INLINE_CODE.finditer(line[:ref_start]):
        token = LINE_SUFFIX.sub("", match.group(1).strip())
        if token.endswith(".md"):
            target_token = token
    if target_token is None:
        schema = sorted(p for p in ROOT.glob(SCHEMA_PATTERN) if p.is_file())
        rel = current.relative_to(ROOT).as_posix()
        extra = [ROOT / e for e in SECTION_EXTRA_FILES.get(rel, []) if (ROOT / e).is_file()]
        return [current] + [path for path in extra + schema if path != current]
    return None
