"""Claude transcript와 Codex rollout 대화 기록을 읽는 공용 함수이다.

호출자: 
- hooks/check_tool_use.py
- hooks/check_invalid_response.py
- hooks/save_handback_report.py
- hooks/check_tool_use/ 모듈
입력: hook 입력 dict, 대화 기록 경로
반환:
- read_session은 항목 dict 목록, 
- session_path와 agent_transcript는 경로 문자열
- answered_questions는 (질문 문장, 답 문자열) 목록
- turn은 (마지막 사용자 메시지, 직전 assistant 텍스트, 현재 턴 항목) 튜플
- 차단 사유 문자열은 없음
한계: 읽을 수 없는 기록 줄은 건너뛰고 빈 목록을 반환할 수 있다. Codex 질문 답은 출력 문자열 전체이다(답 필드 이름은 실측 전).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

CODEX_SESSIONS = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
INJECTED = ("# AGENTS.md instructions", "<environment_context", "<user_instructions")
QUESTION_TOOLS = ("AskUserQuestion", "request_user_input")
# Claude Code가 사용자 메시지 자리에 넣는 하네스 알림이다. 사용자 발화로 보지 않는다.
HARNESS = ("<task-notification>", "<local-command-", "<command-name>", "<command-message>",
           "Base directory for this skill", "Stop hook feedback", "PreToolUse:", "PostToolUse:", "SubagentStop hook feedback",
           "Skill /", "(Re-invocation of /", "Another Claude session sent a message", "<system-reminder>")


# 공백을 한 칸으로 줄이고 앞뒤를 자른다.
def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# runner에 맞는 대화 기록 경로를 반환한다. Codex는 rollout 파일을 먼저 찾는다.
def session_path(data: dict, runner: str) -> str:
    if runner == "codex":
        found = next(CODEX_SESSIONS.glob(f"**/rollout-*-{data.get('session_id', '')}.jsonl"), None)
        if found:
            return str(found)
    return data.get("transcript_path", "")


# 서브에이전트 기록 경로이다. agent_transcript_path가 없으면 <세션 폴더>/subagents/agent-<agent_id>.jsonl이다.
def agent_transcript(data: dict) -> str:
    # 기록 경로가 입력에 있으면 그대로 쓴다.
    path = data.get("agent_transcript_path")
    if path:
        return path

    # 부모 기록 폴더의 subagents 폴더에서 agent id로 찾는다.
    parent, agent = data.get("transcript_path"), data.get("agent_id")
    if parent and agent:
        found = Path(parent).with_suffix("") / "subagents" / f"agent-{agent}.jsonl"
        if found.is_file():
            return str(found)
    return ""


# Claude transcript 한 줄을 {role, text, calls, results, ids, replies} 항목으로 바꾼다. 하네스가 넣은 항목은 None이다.
# ids는 calls와 같은 순서의 호출 id, replies는 {호출 id: {질문 문장: 답}}이다.
def _claude_entry(d: dict) -> dict | None:
    # 메시지 본문을 꺼낸다.
    msg = d.get("message") or {}
    content = msg.get("content") or []
    # isMeta와 sourceToolUseID가 붙은 user 항목은 스킬 본문처럼 하네스가 넣은 내용이다.
    if d.get("type") not in ("user", "assistant") or d.get("isMeta") or d.get("sourceToolUseID"):
        return None

    # 내용 블록에서 도구 결과 문자열을 모은다.
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else [b for b in content if isinstance(b, dict)]
    results = []
    replies = {}
    for b in blocks:
        if b.get("type") == "tool_result":
            # 질문 도구 결과의 answers 값을 호출 id로 보관한다.
            meta = d.get("toolUseResult")
            answers = meta.get("answers") if isinstance(meta, dict) else None
            replies[b.get("tool_use_id")] = answers if isinstance(answers, dict) else {}
            body = b.get("content")
            results.append(body if isinstance(body, str) else
                           "\n".join(x.get("text", "") for x in body or [] if isinstance(x, dict)))

    # 텍스트, 도구 호출, 결과를 한 항목으로 묶는다.
    return {"role": d["type"], "text": "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text"),
            "calls": [(b.get("name"), b.get("input") or {}) for b in blocks if b.get("type") == "tool_use"],
            "results": [r for r in results if r.strip()],
            "ids": [b.get("id") for b in blocks if b.get("type") == "tool_use"], "replies": replies}


# Codex rollout 한 줄을 {role, text, calls, results, ids, replies} 항목으로 바꾼다. 대화와 관계없는 줄은 None이다.
def _codex_entry(d: dict) -> dict | None:
    # 응답 항목이 아닌 줄은 건너뛴다.
    p = d.get("payload") or {}
    if d.get("type") != "response_item":
        return None

    # 함수 호출은 도구 호출 항목으로 바꾼다.
    if p.get("type") == "function_call":
        try:
            args = json.loads(p.get("arguments") or "{}")
        except ValueError:
            args = {}
        return {"role": "assistant", "text": "", "calls": [(p.get("name"), args)], "results": [],
                "ids": [p.get("call_id")], "replies": {}}

    # 사용자 정의 도구 호출(셸 exec)은 입력 문자열을 도구 호출 항목으로 바꾼다.
    if p.get("type") == "custom_tool_call":
        return {"role": "assistant", "text": "", "calls": [(p.get("name"), {"input": p.get("input") or ""})], "results": [],
                "ids": [p.get("call_id")], "replies": {}}

    # 함수 호출 출력은 결과 항목으로 바꾼다.
    if p.get("type") in ("function_call_output", "custom_tool_call_output"):
        out = p.get("output")
        out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False) if out else ""
        return {"role": "user", "text": "", "calls": [], "results": [out] if out.strip() else [],
                "ids": [], "replies": {p.get("call_id"): {"": out}} if out.strip() else {}}

    # 사용자와 assistant 메시지는 텍스트 항목으로 바꾸고, 주입된 지침은 뺀다.
    if p.get("type") == "message" and p.get("role") in ("user", "assistant"):
        text = "\n".join(b.get("text", "") for b in p.get("content") or [] if isinstance(b, dict))
        if p["role"] == "user" and text.startswith(INJECTED):
            return None
        return {"role": p["role"], "text": text, "calls": [], "results": [], "ids": [], "replies": {}}
    return None


# 대화 기록 파일을 항목 목록으로 읽는다. 읽을 수 없으면 빈 목록이다.
def read_session(path: str) -> list[dict]:
    # 기록 파일을 줄마다 읽어 항목으로 바꾼다.
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                entry = (_codex_entry if "payload" in d else _claude_entry)(d)
                if entry and (entry["text"].strip() or entry["calls"] or entry.get("results")):
                    entries.append(entry)
    except (OSError, ValueError):
        pass
    return entries


# 질문 도구 호출과 같은 id의 결과에 답이 있는 (header와 질문 문장, 답 문자열) 목록이다.
# 다른 도구 결과와 답 없는 결과는 답으로 보지 않는다.
def answered_questions(path: str) -> list[tuple[str, str]]:
    # 호출 id별 결과 답을 모은다.
    entries = read_session(path)
    replies = {}
    for entry in entries:
        replies.update(entry.get("replies") or {})
    out = []

    # 질문 도구 호출마다 같은 id의 결과에서 질문별 답을 찾는다.
    for entry in entries:
        for (name, args), call_id in zip(entry["calls"], entry.get("ids") or []):
            if name not in QUESTION_TOOLS or call_id not in replies:
                continue
            for q in (args or {}).get("questions") or []:
                if not isinstance(q, dict):
                    continue
                answer = replies[call_id].get(q.get("question")) or replies[call_id].get("")
                if answer and str(answer).strip():
                    out.append((f"{q.get('header') or ''}\n{q.get('question') or ''}", str(answer)))
    return out


# 서브에이전트가 마지막으로 SubagentHandback에 넘긴 보고이다.
# SubagentStop의 last_assistant_message에는 이 보고가 없다.
def handback_report(path: str) -> str:
    calls = [c for e in read_session(path) for c in e["calls"] if c[0] == "SubagentHandback"]
    return str(calls[-1][1].get("message") or "").strip() if calls else ""


# (마지막 사용자 메시지, 그 직전 assistant 텍스트, 그 뒤 현재 턴 항목)을 반환한다.
def turn(entries: list[dict]) -> tuple[str, str, list[dict]]:
    # 마지막 사용자 발화 위치를 찾는다.
    said = lambda e: e["role"] == "user" and e["text"].strip() and not e["text"].lstrip().startswith(HARNESS)
    last = max((i for i, e in enumerate(entries) if said(e)), default=-1)

    # 그 직전 턴의 assistant 텍스트를 모은다.
    before = entries[:last]
    first = max((i for i, e in enumerate(before) if e["role"] == "user" and e["text"].strip()), default=-1)
    previous = "\n".join(e["text"] for e in before[first + 1:] if e["role"] == "assistant")
    return (entries[last]["text"] if last >= 0 else ""), previous, entries[last + 1:]
