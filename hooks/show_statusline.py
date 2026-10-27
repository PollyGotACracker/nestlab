#!/usr/bin/env python3
"""에이전트 이름을 표시하고 Orca의 상태 정보 전송을 유지한다.

호출자: Claude Code statusLine 설정(shared/settings/claude/settings.example.json)
입력: statusLine JSON을 stdin으로 받는다.
반환: stdout에 `Agent: <이름>` 한 줄을 출력하고 종료 코드 0을 반환한다. 차단 사유 문자열은 없다.
한계: Orca 전달은 Windows에서 전달 스크립트가 있을 때만 동작한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


# Orca에서 실행할 때 같은 상태 입력을 기존 스크립트에 전달한다.
def forward_orca(payload: str) -> None:
    # Orca 창이 아니거나 Windows가 아니거나 전달 스크립트가 없으면 건너뛴다.
    if not os.environ.get("ORCA_PANE_KEY"):
        return

    # 전달 스크립트 경로를 만들고 Windows가 아니거나 스크립트가 없으면 건너뛴다.
    hook = Path.home() / ".orca" / "agent-hooks" / "claude-statusline.cmd"
    if os.name != "nt" or not hook.is_file():
        return

    # 상태 입력을 전달 스크립트에 넘긴다. 실패는 무시한다.
    try:
        subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(hook)],
            input=payload,
            encoding="utf-8",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


# 상태 입력에서 에이전트 이름을 출력한다.
def main() -> int:
    # 상태 입력에서 에이전트 이름을 읽어 출력한다.
    payload = sys.stdin.read()
    data = json.loads(payload)
    name = (data.get("agent") or {}).get("name") or "default"
    print(f"Agent: {name}", flush=True)

    # 같은 입력을 Orca 전달 스크립트에도 넘긴다.
    forward_orca(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())