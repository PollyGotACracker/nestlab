"""PreToolUse hook(SubagentHandback): researcher 보고는 조사 문서에, reviewer 반증 보고는 반증 문서에 저장하고 보고를 경로 한 줄로 바꾼다.

호출자: SubagentHandback PreToolUse hook 설정(shared/settings)
반환: 
- stdout에 `hookSpecificOutput` JSON(allow와 updatedInput, 또는 deny와 사유)을 출력한다.
- replace()는 그 dict 또는 None을 반환한다.
입력: SubagentHandback PreToolUse hook 입력 JSON을 stdin으로 받는다.
한계: 보고 원문의 사실 여부는 판정하지 않고 저장 형식만 검사한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))

from return_research_path import RESEARCH_LINE, SLOT_FINAL, SLOT_ROUND
from save_subagent_result import write_result
from sub_call import refute_mode
from sub_docs import SAVED_LINE, doc, loop_count, saved_report, task_id
from sub_role import first_prompt, resolve_role
from sub_session import agent_transcript


# 출력할 JSON이다. 바꿀 것이 없으면 None이다.
def replace(data: dict) -> dict | None:
    # SubagentHandback의 researcher·reviewer 보고만 대상으로 한다.
    tin = data.get("tool_input") or {}
    message = str(tin.get("message") or "").strip()
    role = resolve_role(data)
    if data.get("tool_name") != "SubagentHandback" or not message \
            or role not in ("researcher", "reviewer"):
        return None

    # 하위 요청에서 조사 문서를 찾고, 이미 저장된 보고나 대상 아닌 호출은 건너뛴다.
    prompt = first_prompt(agent_transcript(data))
    line = RESEARCH_LINE.search(prompt)
    refute = role == "reviewer" and refute_mode(prompt)
    if saved_report(message, data.get("cwd"), refute) or not line or (role == "reviewer" and not refute):
        return None
    tid = task_id(prompt)
    target = doc(tid, "refute" if refute else "research", data.get("cwd")) if tid else None
    if not target or not target.is_file():
        return None

    # 보고를 조사 문서에 저장하고, 실패하면 deny한다.
    reason = write_result(target, prompt, message, refute)
    if reason:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}

    # 보고를 저장 위치 안내 문장으로 바꿔 허용한다.
    slot = SLOT_ROUND.format(n=loop_count(target.read_text(encoding="utf-8"))) if refute else SLOT_FINAL
    text = SAVED_LINE.format(path=line.group(0).split(": ", 1)[1].strip(), slot=slot)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "updatedInput": {**tin, "message": text}}}


REASON_SAVE_ERROR = "조사 결과를 저장하는 중 {error} 오류가 났습니다. 보고를 다시 제출하십시오."

# stdin의 hook 입력으로 보고를 저장하고 allow 또는 deny JSON을 출력한다. 항상 0을 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞추고 입력을 읽는다. 읽지 못하면 통과한다.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    # 저장 중 오류는 SubagentHandback에 한해 deny한다.
    try:
        out = replace(data)
    except Exception as error:
        # 저장 중 오류는 통과시키지 않는다. 보고가 저장되지 않으면 조사 원문 칸이 비어 다음 단계 검사가 막는다.
        if data.get("tool_name") != "SubagentHandback":
            return 0
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                      "permissionDecisionReason": REASON_SAVE_ERROR.format(error=type(error).__name__)}}

    # 결과 JSON을 출력한다.
    if out:
        json.dump(out, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
