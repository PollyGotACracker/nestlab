#!/usr/bin/env python3
"""runner가 생성한 worktree의 변경 결과를 원본 checkout에 반영한다."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
sys.path[:0] = [str(HOOKS / "common"), str(HOOKS / "check_tool_use")]
from sub_worktree import apply, origin


# apply를 실행하고 실패 시 원본과 worktree를 보존한 오류를 반환한다.
def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply",))
    parser.add_argument("--source", type=Path, help="생략하면 worktree의 원본 checkout을 사용한다.")
    parser.add_argument("--worktree", type=Path, required=True)
    args = parser.parse_args()
    try:
        applied, held = apply(args.source or origin(args.worktree), args.worktree)
    except (OSError, ValueError) as error:
        print(f"{error}\n원본 checkout과 worktree를 보존했습니다.", file=sys.stderr)
        return 1
    print("\n".join(applied))
    if held:
        print("원본이 기준 커밋과 달라 보류했습니다. 직접 병합하십시오: " + ", ".join(held), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
