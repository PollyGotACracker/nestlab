"""공용 skill 원본의 skill 목록을 읽고 skill 자리 하나의 상태를 판정한다."""
from __future__ import annotations

import filecmp
from pathlib import Path

from sub_config import SOURCE
from sub_symlink import link_target


# 두 폴더의 파일 구성과 내용이 모두 같은지 확인한다.
def same_tree(left: Path, right: Path) -> bool:
    cmp = filecmp.dircmp(left, right)
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    _, mismatch, errors = filecmp.cmpfiles(left, right, cmp.common_files, shallow=False)
    if mismatch or errors:
        return False
    return all(same_tree(left / name, right / name) for name in cmp.common_dirs)


# 공용 skill 원본에서 SKILL.md가 있는 폴더를 skill로 읽는다.
def read_skills() -> list[Path]:
    if not SOURCE.is_dir():
        raise SystemExit(f"{SOURCE}가 없습니다.")
    return sorted(p for p in SOURCE.iterdir() if (p / "SKILL.md").is_file())


# 한 자리의 상태를 판정한다.
def entry_state(entry: Path, skill: Path) -> str:
    target = link_target(entry)
    if target is not None:
        if target == skill.resolve():
            return "연결됨"
        # 저장소를 옮기기 전의 같은 이름 skill 원본을 가리키는 링크이다.
        if (
            not target.exists()
            and target.name == skill.name
            and target.parent.name == "skills"
            and target.parent.parent.name == "shared"
        ):
            return f"끊어진 링크 (예전 경로: {target})"
        return f"충돌 (다른 곳을 가리키는 링크: {target})"
    if entry.exists():
        same = "shared와 내용 같음" if entry.is_dir() and same_tree(entry, skill) else "shared와 내용 다름"
        return f"중복 (실제 폴더, {same})"
    return "없음"


# 공용 skill 원본을 가리키지만 원본이 없어진 링크를 찾는다.
def stale_links(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    result = []
    for entry in folder.iterdir():
        target = link_target(entry)
        if target is not None and target.parent == SOURCE and not target.exists():
            result.append(entry)
    return result
