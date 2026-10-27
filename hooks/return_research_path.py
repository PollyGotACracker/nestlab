"""PostToolUse hook: researcher·reviewer 호출 결과를 조사 문서와 반증 문서 경로 한 줄로 바꾼다.

호출자: PostToolUse hook 설정(shared/settings), hooks/save_handback_report.py(RESEARCH_LINE)
반환:
- stdout에 `updatedToolOutput` JSON(Claude) 또는 `{"decision": "block", "reason": <한 줄>}`(Codex)을 출력하거나 아무것도 출력하지 않는다.
- saved_line은 결과 한 줄 문자열 또는 None이다.
입력: PostToolUse hook 입력 JSON을 stdin으로 받는다(tool_input, tool_response).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))

from sub_call import RESEARCHER, REVIEWER, call_info
from sub_docs import SAVED_LINE, doc, loop_count, task_id

# 둘째 갈래는 임시 경로이다. 기존 로그 삭제 뒤 제거한다.
RESEARCH_LINE = re.compile(
    r"^(?:조사 문서: (?:log/task/[\w.\-]+/research|log/state/[\w.\-]+-research)|반증 문서: log/task/[\w.\-]+/refute)\.md\s*$", re.M)
SLOT_FINAL = "최종 조사 원문"
SLOT_ROUND = "{n}회차 반증 원문"


# 바꿀 결과 한 줄이다. 대상이 아니면 None이다.
def saved_line(data: dict) -> str | None:
    # researcher·reviewer 호출이고 조사 문서 줄이 있을 때만 바꾼다.
    target, prompt = call_info(data)
    line = RESEARCH_LINE.search(prompt)
    if target not in RESEARCHER | REVIEWER or not line:
        return None

    # 조사 문서(반증은 반증 문서)가 실제로 있는지 확인한다.
    tid = task_id(prompt)
    path = doc(tid, "refute" if target in REVIEWER else "research", data.get("cwd")) if tid else None
    if not path or not path.is_file():
        return None

    # researcher는 최종 조사 원문 칸, reviewer는 회차 반증 원문 칸을 알린다.
    slot = SLOT_FINAL
    if target in REVIEWER:
        slot = SLOT_ROUND.format(n=loop_count(path.read_text(encoding="utf-8")))
    return SAVED_LINE.format(path=line.group(0).split(": ", 1)[1].strip(), slot=slot)


# 출력할 JSON이다. 바꿀 것이 없으면 None이다.
def replace(data: dict, runner: str) -> dict | None:
    # 바꿀 결과 문장이 없으면 건너뛴다.
    text = saved_line(data)
    if not text:
        return None

    # Codex는 block 사유로, Claude는 updatedToolOutput으로 결과를 바꾼다.
    if runner == "codex":
        return {"decision": "block", "reason": text}
    resp = data.get("tool_response")
    if not isinstance(resp, dict) or resp.get("status", "completed") != "completed":
        return None
    new = {**resp, "content": [{"type": "text", "text": text}] if isinstance(resp.get("content"), list) else text}
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": new}}


# stdin의 hook 입력으로 결과 교체 JSON을 출력한다. 항상 0을 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞추고 runner를 읽는다.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    runner = sys.argv[sys.argv.index("--runner") + 1] if "--runner" in sys.argv else "claude"

    # 교체할 결과가 있으면 출력하고, 오류는 통과시킨다.
    try:
        out = replace(json.load(sys.stdin), runner)
    except Exception:
        return 0
    if out:
        json.dump(out, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
