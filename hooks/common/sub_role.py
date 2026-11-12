"""hook 입력에서 역할을 판별하고 대화 기록 위치를 찾는다. 판별 실패는 None이다.

호출자: hooks/check_tool_use.py, hooks/check_invalid_response.py, hooks/check_workflow_response.py, hooks/save_subagent_result.py, hooks/save_handback_report.py
입력: hook 입력 dict, rollout 경로, thread id
반환:
- resolve_role, to_role은 역할 이름 또는 None
- first_line은 payload dict
- first_prompt, codex_rollout은 문자열
- parent_thread는 thread id 또는 None
- main_session은 bool
- 차단 사유 문자열은 없음
한계: Codex rollout 첫 줄 형식은 설치 후 실측 전이라 부모 thread 판정은 단위 테스트의 가정을 따른다.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from sub_agents import discover_agents, read_folder_agent
from sub_call import CIPHER, task_prompt
from sub_docs import ROOT
from sub_session import CODEX_SESSIONS, read_session


# rollout 첫 줄(session_meta)의 payload이다. 읽을 수 없으면 빈 dict이다.
def first_line(path: str | None) -> dict:
    try:
        with open(path or "", encoding="utf-8") as f:
            return json.loads(f.readline()).get("payload") or {}
    except (OSError, ValueError, AttributeError):
        return {}


# 역할 이름이나 에이전트 이름을 역할 이름으로 바꾼다. 알 수 없는 이름은 None이다.
def to_role(name: str | None) -> str | None:
    roles = discover_agents(ROOT)
    role = {n: r for r, n in roles.items()}.get(name, name)
    return role if role in roles else None


# hook 입력, rollout, 환경 변수, 폴더 설정 순으로 역할을 찾는다.
def resolve_role(data: dict, default_role: str | None = None) -> str | None:
    name = (data.get("agent_type") or first_line(data.get("transcript_path")).get("agent_role")
            or os.environ.get("NESTLAB_AGENT") or read_folder_agent(Path(data.get("cwd") or os.getcwd())) or default_role)
    return to_role(name)


# 조정 절차는 역할이 확인된 director 메인에만 적용한다.
def director_main(data: dict) -> bool:
    return resolve_role(data) == "director" and main_session(data)


# 하위 실행의 부모 역할이 director로 확인된 배정만 결과 저장 대상으로 삼는다.
def director_assignment(data: dict, child_path: str) -> bool:
    parent = codex_rollout(parent_thread(child_path)) or data.get("transcript_path")
    if not parent or parent == child_path:
        return False
    role = (data.get("parent_agent_type") or first_line(parent).get("agent_role") or agent_setting(parent)
            or injected_agent(parent))
    return to_role(role) == "director"


PERSONA = re.compile(r"# Persona\\n\\n---\\ntitle: (\w+) persona")


# Codex 메인 rollout 앞부분에 SessionStart가 주입한 성향 문서의 에이전트 이름이다. 없으면 None이다.
def injected_agent(path: str | None) -> str | None:
    try:
        with open(path or "", encoding="utf-8") as f:
            m = PERSONA.search("".join(line for _, line in zip(range(60), f)))
    except OSError:
        return None
    return m.group(1).lower() if m else None


# Claude 세션 기록 앞부분의 agent-setting 줄이 가리키는 에이전트 이름이다. 없으면 None이다.
def agent_setting(path: str | None) -> str | None:
    try:
        with open(path or "", encoding="utf-8") as f:
            for _, line in zip(range(10), f):
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("type") == "agent-setting":
                    return entry.get("agentSetting")
    except OSError:
        pass
    return None


# 대화 기록의 첫 사용자 메시지를 반환한다. 없으면 빈 문자열이다.
def first_prompt(path: str | None) -> str:
    text = next((e["text"] for e in read_session(path or "") if e["role"] == "user"), "")
    if text.strip() and not CIPHER.match(text.strip()):
        return text

    # 첫 메시지가 비었거나 암호문이면 rollout 첫 줄 전체에서 task_name을 찾아 요청을 복원한다.
    return task_prompt(json.dumps(first_line(path), ensure_ascii=False))


# Codex thread id에 해당하는 rollout 파일 경로를 반환한다. 없으면 빈 문자열이다.
def codex_rollout(thread_id: str | None) -> str:
    found = next(CODEX_SESSIONS.glob(f"**/rollout-*-{thread_id}.jsonl"), None) if thread_id else None
    return str(found) if found else ""


# 메인 세션이면 True이다. Claude는 agent_id가 없을 때, Codex는 rollout에 부모 thread가 없을 때이다.
def main_session(data: dict) -> bool:
    source = first_line(data.get("transcript_path")).get("source")
    return not data.get("agent_id") and not (isinstance(source, dict) and bool(source.get("subagent")))


# Codex 하위 thread rollout의 부모 thread id이다. 하위 thread가 아니면 None이다.
def parent_thread(path: str | None) -> str | None:
    source = first_line(path).get("source")
    spawn = (source.get("subagent") or {}) if isinstance(source, dict) else {}
    return spawn.get("thread_spawn", {}).get("parent_thread_id") if isinstance(spawn, dict) else None
