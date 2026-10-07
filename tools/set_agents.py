#!/usr/bin/env python3
"""프로젝트의 에이전트 원본 폴더를 사용자 전역 경로에 연결한다.

check는 연결 상태만 확인한다.
link는 비어 있는 경로에 연결을 만든다.
unlink는 이 저장소 원본을 가리키는 링크만 지운다.
원본 폴더가 없어진 링크도 지운다.
기존 파일, 실제 폴더, 다른 원본을 가리키는 링크는 변경하지 않는다.

공용 모듈(common/): sub_symlink
"""
from __future__ import annotations

import sys
from pathlib import Path

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
from sub_symlink import link_target, make_link, remove_link

ROOT = Path(__file__).resolve().parent.parent
LINKS = [
    ("Claude Code", ROOT / ".claude" / "agents", Path.home() / ".claude" / "agents"),
    ("Codex", ROOT / ".codex" / "agents", Path.home() / ".codex" / "agents"),
]


def run(mode: str) -> int:
    problems = 0
    for label, source, destination in LINKS:
        print(f"[{label}] {destination}")
        if mode == "unlink":
            problems += unlink(source, destination)
            continue
        if not source.is_dir():
            print(f"  원본 없음: {source}")
            problems += 1
            continue

        target = link_target(destination)
        if target is not None and target.resolve() == source.resolve():
            print(f"  연결됨: {source}")
            continue
        if target is not None or destination.exists():
            print("  충돌: 기존 항목을 유지함")
            problems += 1
            continue
        if mode == "check":
            print(f"  미연결: {source}")
            problems += 1
            continue

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            kind = make_link(source, destination)
        except OSError as exc:
            print(f"  연결 실패: {exc}")
            problems += 1
        else:
            print(f"  생성 ({kind}): {source}")

    return 1 if problems else 0


# 이 저장소 원본을 가리키는 링크만 지운다.
# 원본 폴더가 없어도 링크 대상 경로로 판정한다.
# 충돌이나 제거 실패면 1, 그 외에는 0을 반환한다.
def unlink(source: Path, destination: Path) -> int:
    target = link_target(destination)
    if target is not None and target.resolve() == source.resolve():
        try:
            remove_link(destination)
        except OSError as exc:
            print(f"  제거 실패: {exc}")
            return 1
        print(f"  제거: {source}")
        return 0
    if target is not None or destination.exists():
        print("  충돌: 기존 항목을 유지함")
        return 1
    print(f"  미연결: {source}")
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"check", "link", "unlink"}:
        print("사용법: python tools/set_agents.py check|link|unlink", file=sys.stderr)
        return 2
    return run(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())