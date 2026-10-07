#!/usr/bin/env python3
"""PreToolUse hook: 하위 에이전트 호출과 쓰기 도구 사용을 검사한다.

사용: python check_tool_use.py --runner <claude|codex>
hook 입력 JSON을 stdin으로 받는다. 위반이면 permissionDecision deny를 출력하고 나머지는 아무것도 출력하지 않는다.
오류는 통과한다(fail-open).

검사는 같은 이름 폴더의 기능별 모듈이 맡는다.
- sub_approval: 하위 에이전트 호출 승인, 승인 명령 판정
- sub_input: 하위 에이전트 입력 형식
- sub_scope: 역할별 쓰기 범위, director Bash 허용 목록
- sub_plan: 실행 승인 전 입력 문서 계획
- sub_research: 조사 문서 원문 보존, 조사 결과 없는 확정 결정 차단

검사 순서
1. 하위 에이전트 호출(Agent·spawn_agent): 호출 승인(Claude Agent만) → 입력 형식
2. Codex 하위 thread의 도구 호출: 부모 director 승인
3. director Bash: 허용 목록
4. 쓰기 도구: 역할별 쓰기 범위
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 같은 이름 폴더의 모듈을 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))

from sub_approval import check_agent_call, check_codex_child
from sub_call import SPAWN_TOOLS
from sub_input import check_input
from sub_role import resolve_role
from sub_scope import check_director_bash, check_write
from sub_write import targets


def deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def check(data: dict, runner: str) -> str | None:
    """위반 사유를 반환한다. 통과면 None이다."""
    if data.get("tool_name") in SPAWN_TOOLS:
        reason = check_agent_call(data) if data.get("tool_name") == "Agent" else None
        return reason or check_input(data, runner)
    role = resolve_role(data)
    if runner == "codex" and role not in (None, "director", "assistant"):
        reason = check_codex_child(data)
        if reason:
            return reason
    if role == "director" and data.get("tool_name") == "Bash":
        reason = check_director_bash(data)
        if reason:
            return reason
    paths = targets(data)
    if paths is None:
        return None
    return check_write(data, role, paths)


def main() -> int:
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    runner = parser.parse_args().runner
    try:
        reason = check(json.load(sys.stdin), runner)
    except Exception:
        return 0
    if reason:
        json.dump(deny(reason), sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
