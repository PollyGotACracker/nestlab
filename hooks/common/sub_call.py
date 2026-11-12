"""서브에이전트 호출 입력의 공용 정의이다.

호출자:
- hooks/check_tool_use.py
- hooks/save_subagent_result.py
- hooks/save_handback_report.py
- hooks/common/sub_gate.py
입력: hook 입력 dict, 호출 본문 문자열
반환:
- call_info는 (대상, 입력 본문) 문자열 쌍
- is_spawn과 refute_mode는 bool
- 차단 사유 문자열은 없음
- task_stage는 `task:` 줄의 단계 이름 또는 빈 문자열
- ROLES 등 역할 이름 묶음은 집합 상수
- VERDICT는 반증 판정 줄 정규식
한계: Codex 도구 이름은 버전마다 달라 정규식이 알려진 이름만 인식한다.
"""
from __future__ import annotations

import re

from sub_paths import TASK_REL

# Codex는 버전에 따라 spawn_agent, collaborationspawn_agent처럼 이름 앞부분이 달라진다(openai/codex#49736).
SPAWN = re.compile(r"^(?:Agent|\w*spawn_agent)$")
FOLLOWUP = re.compile(r"^(?:\w*followup_task)$")
DOCUMENTER = {"documenter", "pepper"}
RESEARCHER = {"researcher", "nico"}
REVIEWER = {"reviewer", "ricky"}
WORKER_REVIEWER = {"worker", "jelly"} | REVIEWER
ROLES = DOCUMENTER | RESEARCHER | WORKER_REVIEWER
TARGET_KEYS = ("subagent_type", "agent_type", "agent_role", "role")
PROMPT_KEYS = ("prompt", "message")
# reviewer 반증 판정 줄 형식이다.
VERDICT = re.compile(r"^[\s\-*]*주장\s*(\d+)\s*[:：]\s*(지지|반증|미확인)\s*\|\s*출처\s*[:：]\s*(.+?)[\s*]*$")
# 배정 요청은 소문자 키 두 줄 `task: <단계 이름>`, `input: <문서 경로>`이다.
STAGE_NAMES = ("research", "refute", "implement", "verify", "reimplement", "reverify")
# 단계가 읽는 작업 문서 파일 이름이다. 나머지 단계는 spec.md이다.
STAGE_FILE = {"research": "research", "refute": "refute"}
# Codex 에이전트 이름의 역할 이름이다.
AGENT_ROLES = {"jelly": "worker", "pepper": "documenter", "nico": "researcher", "ricky": "reviewer"}
TASK_LINE = re.compile(r"^task: (" + "|".join(STAGE_NAMES) + r")$")
REFUTE_MARKER = "task: refute"
# Codex spawn_agent의 message는 암호문이라 task_name에서 요청을 복원한다.
CIPHER = re.compile(r"^gAAAAA[\w\-]+=*$")
TASK_NAME = re.compile(r"(" + "|".join(STAGE_NAMES) + r")__(\d{8})_(\d{6})_([a-z0-9_]+)")


# 요청 본문의 `task:` 줄이 가리키는 단계 이름이다. 줄이 없거나 둘 이상이면 빈 문자열이다.
def task_stage(prompt: str) -> str:
    found = [m[1] for line in (prompt or "").splitlines() if (m := TASK_LINE.match(line.strip()))]
    return found[0] if len(found) == 1 else ""


# 문자열에서 task_name을 찾아 평문 요청 두 줄을 만든다. task_name이 없으면 빈 문자열이다.
def task_prompt(text: str) -> str:
    m = TASK_NAME.search(text or "")
    if not m:
        return ""
    stage, day, clock, name = m.groups()
    file = STAGE_FILE.get(stage, "spec")
    return f"task: {stage}\ninput: {TASK_REL}/{day}-{clock}-{name.replace('_', '-')}/{file}.md"


# 서브에이전트 생성 도구 이름이면 True이다.
def is_spawn(tool_name: object) -> bool:
    return isinstance(tool_name, str) and bool(SPAWN.match(tool_name))


# 기존 에이전트에 새 작업을 배정하는 호출이면 True이다.
def is_followup(tool_name: object) -> bool:
    return isinstance(tool_name, str) and bool(FOLLOWUP.match(tool_name))


# 서브에이전트 호출의 (대상, 입력 본문)을 반환한다. 서브에이전트 호출이 아니면 빈 값이다.
def call_info(data: dict) -> tuple[str, str]:
    # 서브에이전트 호출이 아니면 빈 값을 반환한다.
    followup = is_followup(data.get("tool_name"))
    if not is_spawn(data.get("tool_name")) and not followup:
        return "", ""

    # 대상 키와 본문 키 중 문자열인 첫 값을 고른다.
    tin = data.get("tool_input") or {}
    pick = lambda keys: next((tin[k] for k in keys if isinstance(tin.get(k), str)), "")
    prompt = pick(PROMPT_KEYS)

    # 암호문 본문은 형식에 맞는 task_name으로 복원한 요청으로 바꾼다.
    name = tin.get("task_name")
    if CIPHER.match(prompt.strip()) and isinstance(name, str) and TASK_NAME.fullmatch(name):
        prompt = task_prompt(name)
    if followup:
        target = str(tin.get("target") or "")
        return followup_role(data, target), task_prompt(target) if CIPHER.match(prompt.strip()) else prompt
    return pick(TARGET_KEYS), prompt


# follow-up 대상 task_name의 담당 역할이다. 같은 세션 기록에서 같은 task_name의 spawn 호출 agent_type으로 정하며, 찾지 못하면 빈 문자열이다.
def followup_role(data: dict, target: str) -> str:
    from sub_session import read_session
    name = target.rsplit("/", 1)[-1]
    for entry in reversed(read_session(data.get("transcript_path") or "")):
        for tool, args in reversed(entry["calls"]):
            if is_spawn(tool) and args.get("task_name") == name:
                agent = str(args.get("agent_type") or args.get("subagent_type") or "")
                return AGENT_ROLES.get(agent, agent)
    return ""


# 정확한 요청 표식이 한 번 있을 때만 반증 mode이다.
def refute_mode(prompt: str) -> bool:
    return sum(line.strip() == REFUTE_MARKER for line in prompt.splitlines()) == 1
