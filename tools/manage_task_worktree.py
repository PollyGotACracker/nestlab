#!/usr/bin/env python3
"""runner가 생성한 worktree에 배정 파일을 전달하고 결과를 원본 checkout에 반영한다.

입력: prepare/apply/recover 동작, 원본과 worktree 경로 및 prepare의 명세와 역할 및 파일 목록.
반환: 처리한 경로 목록과 종료 코드 0, 실패 원문과 종료 코드 1.
한계: worktree 생성은 runner의 기본 기능으로 완료해야 한다.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path[:0] = [str(HOOKS / "common"), str(HOOKS / "check_tool_use")]
from sub_worktree import apply, origin, prepare, recover


# 명시한 동작을 실행하고 실패 시 원본과 worktree를 보존한 오류를 반환한다.
def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "apply", "recover"))
    parser.add_argument("--source", type=Path, help="생략하면 worktree의 원본 checkout을 사용한다.")
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--spec")
    parser.add_argument("--role", choices=("worker", "documenter"))
    parser.add_argument("--file", action="append", default=[])
    args = parser.parse_args()
    if args.action == "prepare" and not (args.spec and args.role and args.file):
        parser.error("prepare에는 --spec, --role, --file이 필요합니다.")
    try:
        args.source = args.source or origin(args.worktree)
        paths = (prepare(args.source, args.worktree, args.spec, args.role, args.file) if args.action == "prepare"
                 else {"apply": apply, "recover": recover}[args.action](args.source, args.worktree))
    except (OSError, ValueError, KeyError) as error:
        print(f"{error}\n원본 checkout과 worktree를 보존했습니다.", file=sys.stderr)
        return 1
    print("\n".join(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
