"""검사 대상 파일을 찾고 파일 하나를 검사1~5로 검사한다."""
from __future__ import annotations

from pathlib import Path

from sub_config import (
    FENCE,
    HEADING,
    INLINE_CODE,
    KIND_CROSS,
    KIND_DIRECT,
    KIND_NEGATIVE,
    KIND_PATH,
    KIND_RULE_ID,
    KIND_SECTION,
    NEGATIVE,
    CROSS_REF,
    PATH_LIST_FILE,
    PATH_LIST_SECTION,
    ROOT,
    RULE_ID,
    RULE_ID_SCOPE,
    SECTION_REF,
    TARGET_PATTERNS,
)
from sub_paths import as_repo_path, direct_path_root, path_exists
from sub_sections import read_headings, section_targets


# 파일 하나를 검사해 위반 목록을 반환한다.
def check_file(
    rel: str,
    names: set[str],
    heading_cache: dict[Path, set[str]],
) -> list[tuple[str, int, str, str]]:
    path = ROOT / rel
    violations: list[tuple[str, int, str, str]] = []
    in_fence = False
    check_rule_id = rel.startswith(RULE_ID_SCOPE)
    is_path_list_file = rel == PATH_LIST_FILE
    is_skill = path.name == "SKILL.md"
    # 열려 있는 경로 목록 제목의 수준. 절 밖이면 None이다.
    path_list_level: int | None = None

    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            heading = HEADING.match(line) if is_path_list_file else None
            if heading:
                level = len(heading.group(1))
                if heading.group(2).strip() == PATH_LIST_SECTION:
                    path_list_level = level
                elif path_list_level is not None and level <= path_list_level:
                    path_list_level = None

            for match in INLINE_CODE.finditer(line):
                candidate = as_repo_path(match.group(1), names)
                if candidate and not path_exists(candidate, path.parent, names):
                    violations.append((rel, lineno, KIND_PATH, match.group(1)))

                # 명령은 어느 토큰에든 경로가 있을 수 있다.
                for token in match.group(1).split():
                    root = direct_path_root(token)
                    if (
                        root
                        and path_list_level is None
                        and not (is_skill and (path.parent / root).is_dir())
                    ):
                        violations.append((rel, lineno, KIND_DIRECT, token))

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

        if check_rule_id:
            for match in RULE_ID.finditer(line):
                violations.append((rel, lineno, KIND_RULE_ID, match.group(0)))

        if not in_fence:
            for match in CROSS_REF.finditer(line):
                violations.append((rel, lineno, KIND_CROSS, match.group(0)))

        for match in NEGATIVE.finditer(line):
            violations.append((rel, lineno, KIND_NEGATIVE, match.group(0)))

    return violations


# (검사 대상 파일, 일치하는 파일이 없는 패턴)을 반환한다.
# 파일은 패턴 순서를 유지한다.
def find_targets() -> tuple[list[str], list[str]]:
    targets: list[str] = []
    missing: list[str] = []
    for pattern in TARGET_PATTERNS:
        found = sorted(
            p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern) if p.is_file()
        )
        if not found:
            missing.append(pattern)
        targets.extend(rel for rel in found if rel not in targets)
    return targets, missing
