"""PreToolUse hook(SubagentHandback): researcher·reviewer 보고를 조사 문서에 저장하고 보고를 경로 한 줄로 바꾼다.

사용: python save_research.py
- 요청 본문에 `조사 문서: log/state/<작업-id>-research.md` 줄이 있는 researcher, 또는 같은 줄이 있는 반증 reviewer만 바꾼다.
  요청 본문은 하위 에이전트 기록의 첫 사용자 요청이다(agent_transcript_path, 없으면 <transcript_path 폴더>/subagents/agent-<agent_id>.jsonl).
- 반증 보고는 판정 줄 검사 없이 저장한다.
- 저장은 save_agent_result.write_result를 쓴다. 바뀐 보고: `결과 저장: <조사 문서 위치> <칸 이름>`
- 출력: tool_input 전체를 복사해 message만 바꾼 updatedInput과 permissionDecision allow.
- 대상이 아니거나 오류면 아무것도 출력하지 않는다(fail-open).

공용 모듈(common/): sub_call, sub_docs, sub_role
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))

from return_research_path import LOOP_ROW, RESEARCH_LINE
from save_agent_result import SAVED, write_result
from sub_call import refute_mode
from sub_docs import doc, saved_report, task_id
from sub_role import first_prompt, resolve_role
from sub_session import agent_transcript


def replace(data: dict) -> dict | None:
    """출력할 JSON이다. 바꿀 것이 없으면 None이다."""
    tin = data.get("tool_input") or {}
    message = str(tin.get("message") or "").strip()
    role = resolve_role(data)
    if data.get("tool_name") != "SubagentHandback" or not message \
            or role not in ("researcher", "reviewer"):
        return None
    prompt = first_prompt(agent_transcript(data))
    line = RESEARCH_LINE.search(prompt)
    refute = role == "reviewer" and refute_mode(prompt)
    if saved_report(message, data.get("cwd"), refute) or not line or (role == "reviewer" and not refute):
        return None
    tid = task_id(prompt)
    target = doc(tid, "research", data.get("cwd")) if tid else None
    if not target or not target.is_file():
        return None
    write_result(target, prompt, message, refute)
    slot = f"{len(LOOP_ROW.findall(target.read_text(encoding='utf-8')))}회차 반증 원문" if refute else "최종 조사 원문"
    text = f"{SAVED} {line.group(0).split(': ', 1)[1].strip()} {slot}"
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "updatedInput": {**tin, "message": text}}}


def main() -> int:
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        out = replace(json.load(sys.stdin))
    except Exception:
        return 0
    if out:
        json.dump(out, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
