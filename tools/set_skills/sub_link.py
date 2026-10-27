"""link 명령이다.
빈 자리에 링크를 만들고 원본이 없어진 shared 링크를 지운다.
"""
from __future__ import annotations

from sub_config import LINK_DIRS
from sub_status import entry_state, read_skills, stale_links
from sub_symlink import make_link, remove_link


# 빈 자리에 링크를 만들고 원본이 없어진 링크를 정리한다.
# 중복 및 충돌 항목은 그대로 두고 충돌로 출력한다.
# 링크 생성 및 제거에 실패한 항목은 실패로 출력하고 다음 항목을 계속 처리한다.
# 충돌과 실패가 없으면 0, 하나라도 있으면 1을 반환한다.
def link() -> int:
    # 스킬 목록을 읽고 충돌과 실패 수를 센다.
    skills = read_skills()
    conflicts = 0
    failures = 0

    # 도구 설정 폴더가 있는 링크 폴더만 처리한다.
    for label, folder, _, homes in LINK_DIRS:
        print(f"[{label}] {folder}")
        if not any(h.is_dir() for h in homes):
            print("  건너뜀: 사용하는 도구 설정 폴더 없음")
            continue
        folder.mkdir(parents=True, exist_ok=True)

        # skill마다 상태에 맞게 링크를 만들거나 다시 연결한다. 충돌은 그대로 둔다.
        for skill in skills:
            dst = folder / skill.name
            state = entry_state(dst, skill)
            if state == "없음":
                try:
                    kind = make_link(skill, dst)
                except OSError as exc:
                    failures += 1
                    print(f"  {skill.name:<20} 연결 실패: {exc}")
                    continue
                print(f"  {skill.name:<20} 생성 ({kind})")
            elif state == "연결됨":
                print(f"  {skill.name:<20} 유지")
            elif state.startswith("끊어진 링크"):
                try:
                    remove_link(dst)
                except OSError as exc:
                    failures += 1
                    print(f"  {skill.name:<20} 제거 실패: {exc}")
                    continue
                try:
                    kind = make_link(skill, dst)
                except OSError as exc:
                    failures += 1
                    print(f"  {skill.name:<20} 연결 실패: {exc}")
                    continue
                print(f"  {skill.name:<20} 재연결 ({kind})")
            else:
                conflicts += 1
                print(f"  {skill.name:<20} {state}. 그대로 둠")

        # 원본이 없어진 shared 링크를 지운다.
        for entry in stale_links(folder):
            try:
                remove_link(entry)
            except OSError as exc:
                failures += 1
                print(f"  {entry.name:<20} 제거 실패: {exc}")
                continue
            print(f"  {entry.name:<20} 정리 (원본 없는 링크 삭제)")

    # 충돌과 실패 건수를 출력한다.
    print(f"\n충돌 {conflicts}건. 충돌 항목은 직접 확인 후 정리한다." if conflicts else "\n완료")
    if failures:
        print(f"실패 {failures}건")
    return 1 if conflicts or failures else 0
