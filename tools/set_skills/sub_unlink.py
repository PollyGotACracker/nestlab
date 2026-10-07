"""unlink 명령이다.
공용 skill 원본을 가리키는 링크만 지운다.
"""
from __future__ import annotations

from pathlib import Path

from sub_config import LINK_DIRS, SOURCE
from sub_symlink import link_target, remove_link
from sub_status import entry_state, read_skills


# 공용 skill 원본을 가리키는 링크인지 확인한다.
# 원본이 없어진 링크도 포함한다.
def is_shared_link(entry: Path) -> bool:
    target = link_target(entry)
    return target is not None and target.parent == SOURCE


# 공용 skill 원본을 가리키는 링크와 원본이 없어진 링크를 지운다.
# 실제 폴더와 다른 곳을 가리키는 링크는 그대로 두고 충돌로 출력한다.
# 링크 제거에 실패한 항목은 실패로 출력하고 다음 항목을 계속 처리한다.
# 충돌과 실패가 없으면 0, 하나라도 있으면 1을 반환한다.
def unlink() -> int:
    skills = read_skills()
    conflicts = 0
    failures = 0

    for label, folder, _ in LINK_DIRS:
        print(f"[{label}] {folder}")
        if not folder.is_dir():
            print("  폴더 없음")
            continue
        failed = set()
        for skill in skills:
            dst = folder / skill.name
            state = entry_state(dst, skill)
            if state == "없음":
                print(f"  {skill.name:<20} 미연결")
            elif state.startswith("끊어진 링크") or is_shared_link(dst):
                try:
                    remove_link(dst)
                except OSError as exc:
                    failures += 1
                    failed.add(dst)
                    print(f"  {skill.name:<20} 제거 실패: {exc}")
                    continue
                print(f"  {skill.name:<20} 제거")
            else:
                conflicts += 1
                print(f"  {skill.name:<20} {state}. 그대로 둠")
        # skill 목록에 없는 이름이나 원본이 없어진 shared 링크를 지운다.
        # 위에서 제거에 실패한 링크는 다시 시도하지 않는다.
        for entry in list(folder.iterdir()):
            if entry in failed or not is_shared_link(entry):
                continue
            try:
                remove_link(entry)
            except OSError as exc:
                failures += 1
                print(f"  {entry.name:<20} 제거 실패: {exc}")
                continue
            print(f"  {entry.name:<20} 제거 (shared 링크)")

    print(f"\n충돌 {conflicts}건. 충돌 항목은 직접 확인 후 정리한다." if conflicts else "\n완료")
    if failures:
        print(f"실패 {failures}건")
    return 1 if conflicts or failures else 0
