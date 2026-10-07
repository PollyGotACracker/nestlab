"""hook 입력에서 역할을 판별하고 대화 기록 위치를 찾는다. 판별 실패는 None이다.

사용: check_tool_use.py, record_incident.py, save_agent_result.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from sub_agents import discover_agents, read_folder_agent
from sub_docs import ROOT
from sub_session import CODEX_SESSIONS, read_session


def first_line(path: str | None) -> dict:
    """rollout 첫 줄(session_meta)의 payload이다. 읽을 수 없으면 빈 dict이다."""
    try:
        with open(path or "", encoding="utf-8") as f:
            return json.loads(f.readline()).get("payload") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def to_role(name: str | None) -> str | None:
    roles = discover_agents(ROOT)
    role = {n: r for r, n in roles.items()}.get(name, name)
    return role if role in roles else None


def resolve_role(data: dict) -> str | None:
    name = (data.get("agent_type") or first_line(data.get("transcript_path")).get("agent_role")
            or os.environ.get("BRAINSTACK_AGENT") or read_folder_agent(Path(data.get("cwd") or os.getcwd())))
    return to_role(name)


def first_prompt(path: str | None) -> str:
    return next((e["text"] for e in read_session(path or "") if e["role"] == "user"), "")


def codex_rollout(thread_id: str | None) -> str:
    found = next(CODEX_SESSIONS.glob(f"**/rollout-*-{thread_id}.jsonl"), None) if thread_id else None
    return str(found) if found else ""


def parent_thread(path: str | None) -> str | None:
    """Codex 하위 thread rollout의 부모 thread id이다. 하위 thread가 아니면 None이다."""
    spawn = ((first_line(path).get("source") or {}).get("subagent") or {})
    return spawn.get("thread_spawn", {}).get("parent_thread_id") if isinstance(spawn, dict) else None
