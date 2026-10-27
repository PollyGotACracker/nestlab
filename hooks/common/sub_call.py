"""서브에이전트 호출 입력의 공용 정의이다.

호출자:
- hooks/check_tool_use.py
- hooks/save_subagent_result.py
- hooks/save_handback_report.py
- hooks/common/sub_workflow.py
입력: hook 입력 dict, 호출 본문 문자열
반환:
- call_info는 (대상, 입력 본문) 문자열 쌍
- is_spawn과 refute_mode는 bool
- 차단 사유 문자열은 없음
- ROLES 등 역할 이름 묶음은 집합 상수
- VERDICT는 반증 판정 줄 정규식
한계: Codex 도구 이름은 버전마다 달라 정규식이 알려진 이름만 인식한다.
"""
from __future__ import annotations

import re

# Codex는 버전에 따라 spawn_agent, collaborationspawn_agent처럼 이름 앞부분이 달라진다(openai/codex#49736).
SPAWN = re.compile(r"^(?:Agent|\w*spawn_agent)$")
DOCUMENTER = {"documenter", "pepper"}
RESEARCHER = {"researcher", "nico"}
REVIEWER = {"reviewer", "ricky"}
WORKER_REVIEWER = {"worker", "jelly"} | REVIEWER
ROLES = DOCUMENTER | RESEARCHER | WORKER_REVIEWER
TARGET_KEYS = ("subagent_type", "agent_type", "agent_role", "role")
PROMPT_KEYS = ("prompt", "message")
# reviewer 반증 판정 줄 형식이다.
VERDICT = re.compile(r"^[\s\-*]*주장\s*(\d+)\s*[:：]\s*(지지|반증|미확인)\s*\|\s*출처\s*[:：]\s*(.+?)[\s*]*$")
REFUTE_MARKER = "작업 종류: 반증"


# 서브에이전트 생성 도구 이름이면 True이다.
def is_spawn(tool_name: object) -> bool:
    return isinstance(tool_name, str) and bool(SPAWN.match(tool_name))


# 서브에이전트 호출의 (대상, 입력 본문)을 반환한다. 서브에이전트 호출이 아니면 빈 값이다.
def call_info(data: dict) -> tuple[str, str]:
    # 서브에이전트 호출이 아니면 빈 값을 반환한다.
    if not is_spawn(data.get("tool_name")):
        return "", ""

    # 대상 키와 본문 키 중 문자열인 첫 값을 고른다.
    tin = data.get("tool_input") or {}
    pick = lambda keys: next((tin[k] for k in keys if isinstance(tin.get(k), str)), "")
    return pick(TARGET_KEYS), pick(PROMPT_KEYS)


# 정확한 요청 표식이 한 번 있을 때만 반증 mode이다.
def refute_mode(prompt: str) -> bool:
    return sum(line.strip() == REFUTE_MARKER for line in prompt.splitlines()) == 1
