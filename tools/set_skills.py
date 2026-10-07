#!/usr/bin/env python3
"""공용 skill 원본을 사용자 전역 skill 폴더에 연결하고 연결 상태를 확인한다.

check: 연결 상태, 중복, Hermes 설정을 출력한다. 파일을 바꾸지 않는다.
link:  빈 자리에 링크를 만들고, 원본이 없어진 shared 링크를 지운다.
       실제 폴더와 다른 곳을 가리키는 링크는 건드리지 않는다.
unlink: 공용 skill 원본을 가리키는 링크와 원본이 없어진 링크를 지운다.
        실제 폴더와 다른 곳을 가리키는 링크는 건드리지 않는다.

모듈(set_skills/): sub_check 확인, sub_link 연결, sub_unlink 제거, sub_config 경로, sub_status 연결 상태
공용 모듈(common/): sub_symlink
"""
from __future__ import annotations

import sys
from pathlib import Path

# 같은 이름 폴더의 모듈과 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "set_skills"))

from sub_check import check
from sub_link import link
from sub_unlink import unlink


# 명령줄 인자로 check, link, unlink 중 하나를 실행한다.
# 인자가 잘못되면 사용법을 출력하고 2를 반환한다.
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    commands = {"check": check, "link": link, "unlink": unlink}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print("사용법: python tools/set_skills.py check|link|unlink", file=sys.stderr)
        return 2
    return commands[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
