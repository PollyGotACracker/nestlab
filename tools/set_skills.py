#!/usr/bin/env python3
"""공용 skill 원본을 사용자 전역 skill 폴더에 연결하고 연결 상태를 확인한다."""
from __future__ import annotations

import sys
from pathlib import Path

# 같은 이름 폴더의 모듈과 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "set_skills"))

from sub_check import check
from sub_link import link
from sub_unlink import unlink


# 커맨드라인 인자로 check, link, unlink 중 하나를 실행한다.
# 인자가 잘못되면 사용법을 출력하고 2를 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞춘다.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    # 인자가 check, link, unlink 중 하나인지 확인하고 해당 커맨드를 실행한다.
    commands = {"check": check, "link": link, "unlink": unlink}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print("사용법: python tools/set_skills.py check|link|unlink", file=sys.stderr)
        return 2
    return commands[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
