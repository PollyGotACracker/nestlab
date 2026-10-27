#!/usr/bin/env python3
"""프로젝트의 에이전트 원본 폴더를 사용자 전역 경로에 연결한다.

호출자: 사용자(`python tools/set_agents.py check|link|unlink`)
입력: 명령줄 인자 하나(check, link, unlink)
반환: 연결 상태를 줄 단위로 stdout에 출력하고, 문제가 있으면 종료 코드 1을 반환한다.
한계: 링크 생성은 OS 권한에 따라 실패할 수 있고, 실패하면 결과 줄에 오류를 출력한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

# 저장소 모듈 import 오류 메시지도 UTF-8로 나오도록 먼저 설정한다.
for stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
from sub_symlink import link_target, make_link, remove_link

ROOT = Path(__file__).resolve().parent.parent
LINKS = [
    ("Claude Code", ROOT / ".claude" / "agents", Path.home() / ".claude" / "agents"),
    ("Codex", ROOT / ".codex" / "agents", Path.home() / ".codex" / "agents"),
]


# 모드(check, link, unlink)에 맞게 연결마다 처리하고 문제가 있으면 1, 없으면 0을 반환한다.
def run(mode: str) -> int:
    # 연결 대상마다 문제 수를 센다.
    problems = 0
    for label, source, destination in LINKS:
        print(f"[{label}] {destination}")

        # unlink 모드는 제거만 처리한다.
        if mode == "unlink":
            problems += unlink(source, destination)
            continue

        # 원본 폴더와 대상 설정 폴더가 있는지 확인한다.
        if not source.is_dir():
            print(f"  원본 없음: {source}")
            problems += 1
            continue
        if not destination.parent.is_dir():
            print("  건너뜀: 설정 폴더 없음")
            continue

        # 이미 연결됐는지, 기존 항목과 충돌하는지 확인한다.
        target = link_target(destination)
        if target is not None and target.resolve() == source.resolve():
            print(f"  연결됨: {source}")
            continue
        if target is not None or destination.exists():
            print("  충돌: 기존 항목을 유지함")
            problems += 1
            continue

        # check 모드는 미연결을 보고하고, link 모드는 연결을 만든다.
        if mode == "check":
            print(f"  미연결: {source}")
            problems += 1
            continue
        try:
            kind = make_link(source, destination)
        except OSError as exc:
            print(f"  연결 실패: {exc}")
            problems += 1
        else:
            print(f"  생성 ({kind}): {source}")

    # 문제가 있으면 종료 코드 1을 반환한다.
    return 1 if problems else 0


# 이 저장소 원본을 가리키는 링크만 지운다.
# 원본 폴더가 없어도 링크 대상 경로로 판정한다.
# 충돌이나 제거 실패면 1, 그 외에는 0을 반환한다.
def unlink(source: Path, destination: Path) -> int:
    # 이 저장소 원본을 가리키는 링크면 제거한다.
    target = link_target(destination)
    if target is not None and target.resolve() == source.resolve():
        try:
            remove_link(destination)
        except OSError as exc:
            print(f"  제거 실패: {exc}")
            return 1
        print(f"  제거: {source}")
        return 0

    # 다른 항목이면 유지하고, 항목이 없으면 미연결로 보고한다.
    if target is not None or destination.exists():
        print("  충돌: 기존 항목을 유지함")
        return 1
    print(f"  미연결: {source}")
    return 0


# 명령줄 인자를 확인해 run을 실행하고 종료 코드를 반환한다.
def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"check", "link", "unlink"}:
        print("사용법: python tools/set_agents.py check|link|unlink", file=sys.stderr)
        return 2
    return run(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
