#!/usr/bin/env python3
"""brainstack 전역 설정을 사용자 전역 파일에 병합하고 반영 상태를 확인한다.

설정 원본은 sub_settings 모듈이다.
원본의 <BRAINSTACK> 자리표시는 이 저장소의 실제 경로로 바꾼다.

install:           settings 원본을 전역 파일에 반영하고 설치 기록을 남긴다.
                   기존 설치도 settings 변경을 모든 관리 항목에 재반영한다.
                   변경 전 파일은 설치 기록 폴더에 백업하고, 실패하면 전체 원복한다.
install --dry-run: 전역 파일에 추가·교체·삭제할 내용만 출력한다. 파일을 바꾸지 않는다.
check:             전역 파일의 항목별 반영 여부를 출력한다. 파일을 바꾸지 않는다.
uninstall:         설치 기록에 있는 항목만 제거한다.

모듈(set_hooks/): sub_settings 설정 원본, sub_install 설치·제거, sub_check 반영 확인, sub_claude·sub_codex 도구별 반영, sub_merge 병합, sub_paths 경로
테스트: tools/tests/test_set_hooks.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# sub_check·sub_codex가 쓰는 tomllib는 Python 3.11부터 있다.
if sys.version_info < (3, 11):
    sys.exit("Python 3.11 이상이 필요합니다.")

# 같은 이름 폴더의 모듈을 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "set_hooks"))

from sub_check import check
from sub_install import install, uninstall


# 명령줄 인자로 install, check, uninstall을 실행한다.
# 인자가 잘못되면 사용법을 출력하고 2를 반환한다.
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if args == ["install"]:
        return install(dry_run=False)
    if args == ["install", "--dry-run"]:
        return install(dry_run=True)
    if args == ["check"]:
        return check()
    if args == ["uninstall"]:
        return uninstall()
    print("사용법: python tools/set_hooks.py install [--dry-run]|check|uninstall", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
