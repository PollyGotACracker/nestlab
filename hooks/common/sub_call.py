"""하위 에이전트 호출 입력의 공용 정의이다.

대상 역할 이름 묶음, 호출 대상·본문 추출, 반증 요청 표식 판별을 여러 hook이 함께 쓴다.

사용: check_refute_verdict.py, check_tool_use.py, save_agent_result.py
"""
from __future__ import annotations

import re

SPAWN_TOOLS = {"Agent", "spawn_agent"}
DOCUMENTER = {"documenter", "pepper"}
RESEARCHER = {"researcher", "nico"}
REVIEWER = {"reviewer", "ricky"}
WORKER_REVIEWER = {"worker", "jelly"} | REVIEWER
ROLES = DOCUMENTER | RESEARCHER | WORKER_REVIEWER

TARGET_KEYS = ("subagent_type", "agent_type", "agent_role", "role")
PROMPT_KEYS = ("prompt", "message")
REFUTE_MARKER = "작업 종류: 반증"
# reviewer 반증 판정 줄 형식이다.
VERDICT = re.compile(r"^[\s\-*]*주장\s*(\d+)\s*[:：]\s*(지지|반증|미확인)\s*\|\s*출처\s*[:：]\s*(.+?)[\s*]*$")


def call_info(data: dict) -> tuple[str, str]:
    """서브에이전트 호출의 (대상, 입력 본문)을 반환한다. 서브에이전트 호출이 아니면 빈 값이다."""
    if data.get("tool_name") not in SPAWN_TOOLS:
        return "", ""
    tin = data.get("tool_input") or {}
    pick = lambda keys: next((tin[k] for k in keys if isinstance(tin.get(k), str)), "")
    return pick(TARGET_KEYS), pick(PROMPT_KEYS)


def refute_mode(prompt: str) -> bool:
    """정확한 요청 표식이 한 번 있을 때만 반증 mode이다."""
    return sum(line.strip() == REFUTE_MARKER for line in prompt.splitlines()) == 1
