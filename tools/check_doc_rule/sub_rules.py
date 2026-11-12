"""검사 대상 파일을 찾고 경로, 절 참조와 문구를 검사한다."""
from __future__ import annotations

from pathlib import Path

from sub_config import (
    FENCE,
    INLINE_CODE,
    KIND_CROSS,
    KIND_NEGATIVE,
    KIND_PATH,
    KIND_RULE_ID,
    KIND_SECTION,
    NEGATIVE,
    ROOT,
    RULE_ID,
    RULE_ID_SCOPE,
    SECTION_REF,
    TARGET_PATTERNS,
)
from sub_paths import as_repo_path, path_exists
from sub_sections import read_headings, section_targets


# 파일 하나를 검사해 위반 목록을 반환한다.
def check_file(
    rel: str,
    names: set[str],
    heading_cache: dict[Path, set[str]],
    text: str | None = None,
) -> list[tuple[str, int, str, str]]:
    # 파일 종류별 검사 조건을 정한다. text가 있으면 파일 대신 그 내용을 검사한다.
    path = ROOT / rel
    violations: list[tuple[str, int, str, str]] = []
    in_fence = False
    check_rule_id = rel.startswith(RULE_ID_SCOPE)
    # 줄마다 코드 블록 밖에서 경로와 절 참조를 검사한다.
    for lineno, line in enumerate((path.read_text(encoding="utf-8-sig") if text is None else text).splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            # 코드 표기 경로가 실제로 있는지 확인한다.
            for match in INLINE_CODE.finditer(line):
                candidate = as_repo_path(match.group(1), names)
                if candidate and not path_exists(candidate, path.parent, names):
                    violations.append((rel, lineno, KIND_PATH, match.group(1)))

            # 절 참조가 가리키는 제목이 있는지 확인한다.
            for match in SECTION_REF.finditer(line):
                targets = section_targets(line, match.start(), path, names)
                if targets is None:
                    violations.append((rel, lineno, KIND_CROSS, match.group(0)))
                    continue
                if not targets:
                    continue
                for title in INLINE_CODE.findall(match.group(0)):
                    title = title.strip()
                    if any(title in read_headings(t, heading_cache) for t in targets):
                        continue
                    shown = ", ".join(t.relative_to(ROOT).as_posix() for t in targets)
                    violations.append((rel, lineno, KIND_SECTION, f"{title} -> {shown}"))

        # Rule ID와 부정형 표현은 코드 블록 안에서도 검사한다.
        if check_rule_id:
            for match in RULE_ID.finditer(line):
                violations.append((rel, lineno, KIND_RULE_ID, match.group(0)))

        # 부정 표현을 위반으로 판정한다.
        for match in NEGATIVE.finditer(line):
            violations.append((rel, lineno, KIND_NEGATIVE, match.group(0)))

    # 위반 목록을 반환한다.
    return violations


# (검사 대상 파일, 일치하는 파일이 없는 패턴)을 반환한다.
# 파일은 패턴 순서를 유지한다.
def find_targets() -> tuple[list[str], list[str]]:
    # 검사 대상과 누락 목록을 시작한다.
    targets: list[str] = []
    missing: list[str] = []

    # 패턴마다 일치하는 파일을 선별하고, 없으면 missing에 넣는다.
    for pattern in TARGET_PATTERNS:
        found = sorted(
            p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern) if p.is_file()
        )
        if not found:
            missing.append(pattern)
        targets.extend(rel for rel in found if rel not in targets)
    return targets, missing
