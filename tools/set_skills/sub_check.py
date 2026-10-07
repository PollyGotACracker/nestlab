"""check 명령이다.
skill별 연결 상태, 중복, Hermes 설정을 출력하고 파일을 바꾸지 않는다.
"""
from __future__ import annotations

from sub_config import DUPLICATE_DIRS, HERMES_CONFIG, HERMES_LINE, LINK_DIRS
from sub_status import entry_state, read_skills, stale_links
from sub_symlink import is_link


# skill마다 연결 상태, 중복, Hermes 설정을 출력한다.
# 파일을 바꾸지 않는다.
# 모두 연결됨이면 0, 아니면 1을 반환한다.
def check() -> int:
    skills = read_skills()
    counts: dict[str, int] = {}

    def count(state: str) -> None:
        counts[state] = counts.get(state, 0) + 1

    for label, folder, tools in LINK_DIRS:
        print(f"[{label}] {folder}  ({tools})")
        if not folder.parent.is_dir():
            print("  미설치")
            continue
        for skill in skills:
            state = entry_state(folder / skill.name, skill)
            count("끊어진 링크" if state.startswith("끊어진 링크") else state.split(" ")[0])
            print(f"  {skill.name:<20} {state}")
        for entry in stale_links(folder):
            count("끊어진 링크")
            print(f"  {entry.name:<20} 끊어진 링크 (원본 없음)")

    for label, folder in DUPLICATE_DIRS:
        found = [s for s in skills if (folder / s.name).exists() or is_link(folder / s.name)]
        if not found:
            continue
        print(f"[{label}] {folder}")
        for skill in found:
            count("중복")
            print(f"  {skill.name:<20} {entry_state(folder / skill.name, skill)}")

    print(f"[Hermes] {HERMES_CONFIG}")
    if not HERMES_CONFIG.parent.is_dir():
        print("  미설치")
    else:
        text = HERMES_CONFIG.read_text(encoding="utf-8") if HERMES_CONFIG.is_file() else ""
        if "external_dirs" in text and ".agents/skills" in text:
            print("  external_dirs에 ~/.agents/skills 있음")
        else:
            count("Hermes 설정 없음")
            print("  external_dirs에 ~/.agents/skills 없음. config.yaml에 아래 내용을 추가한다.")
            print("    " + HERMES_LINE.replace("\n", "\n    "))

    print()
    for state, n in counts.items():
        print(f"{state}: {n}건")
    return 0 if set(counts) <= {"연결됨"} else 1
