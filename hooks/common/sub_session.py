"""Claude transcript와 Codex rollout 대화 기록을 읽는 공용 함수이다.

기록은 read_session이 [{"role", "text", "calls": [(도구, 입력)]}] 순서 목록으로 읽는다.
- Claude: hook 입력의 transcript_path.
- Codex: ~/.codex/sessions/**/rollout-*-<session_id>.jsonl (CODEX_HOME이 있으면 그 아래).

사용: check_refute_verdict.py, check_tool_use.py, record_incident.py
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

CODEX_SESSIONS = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
INJECTED = ("# AGENTS.md instructions", "<environment_context", "<user_instructions")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def session_path(data: dict, runner: str) -> str:
    if runner == "codex":
        found = next(CODEX_SESSIONS.glob(f"**/rollout-*-{data.get('session_id', '')}.jsonl"), None)
        if found:
            return str(found)
    return data.get("transcript_path", "")


def _claude_entry(d: dict) -> dict | None:
    msg = d.get("message") or {}
    content = msg.get("content") or []
    if d.get("type") not in ("user", "assistant") or d.get("isMeta"):
        return None
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else [b for b in content if isinstance(b, dict)]
    return {"role": d["type"], "text": "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text"),
            "calls": [(b.get("name"), b.get("input") or {}) for b in blocks if b.get("type") == "tool_use"]}


def _codex_entry(d: dict) -> dict | None:
    p = d.get("payload") or {}
    if d.get("type") != "response_item":
        return None
    if p.get("type") == "function_call":
        try:
            args = json.loads(p.get("arguments") or "{}")
        except ValueError:
            args = {}
        return {"role": "assistant", "text": "", "calls": [(p.get("name"), args)]}
    if p.get("type") == "message" and p.get("role") in ("user", "assistant"):
        text = "\n".join(b.get("text", "") for b in p.get("content") or [] if isinstance(b, dict))
        if p["role"] == "user" and text.startswith(INJECTED):
            return None
        return {"role": p["role"], "text": text, "calls": []}
    return None


def read_session(path: str) -> list[dict]:
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                entry = (_codex_entry if "payload" in d else _claude_entry)(d)
                if entry and (entry["text"].strip() or entry["calls"]):
                    entries.append(entry)
    except (OSError, ValueError):
        pass
    return entries


def handback_report(path: str) -> str:
    """하위 에이전트가 마지막으로 SubagentHandback에 넘긴 보고이다. SubagentStop의 last_assistant_message에는 이 보고가 없다."""
    calls = [c for e in read_session(path) for c in e["calls"] if c[0] == "SubagentHandback"]
    return str(calls[-1][1].get("message") or "").strip() if calls else ""


# Claude Code가 사용자 메시지 자리에 넣는 하네스 알림이다. 사용자 발화로 보지 않는다.
HARNESS = ("<task-notification>", "<local-command-", "<command-name>")


def turn(entries: list[dict]) -> tuple[str, str, list[dict]]:
    """(마지막 사용자 메시지, 그 직전 assistant 텍스트, 그 뒤 현재 턴 항목)을 반환한다."""
    said = lambda e: e["role"] == "user" and e["text"].strip() and not e["text"].lstrip().startswith(HARNESS)
    last = max((i for i, e in enumerate(entries) if said(e)), default=-1)
    before = entries[:last]
    first = max((i for i, e in enumerate(before) if e["role"] == "user" and e["text"].strip()), default=-1)
    previous = "\n".join(e["text"] for e in before[first + 1:] if e["role"] == "assistant")
    return (entries[last]["text"] if last >= 0 else ""), previous, entries[last + 1:]


