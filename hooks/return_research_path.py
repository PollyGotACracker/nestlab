"""PostToolUse hook: researcher·reviewer 호출 결과를 조사 문서 경로 한 줄로 바꾼다.

사용: python return_research_path.py --runner <claude|codex>
- 요청 본문에 `조사 문서: log/state/<작업-id>-research.md` 줄이 있는 researcher·reviewer 호출만 바꾼다.
  결과 원문은 SubagentStop의 save_agent_result.py가 조사 문서에 저장했다.
- 바뀐 결과: `결과 저장: <조사 문서 위치> <칸 이름>`
  칸 이름은 researcher는 `최종 조사 원문`, reviewer 반증은 `<n>회차 반증 원문`이다.
- Claude: tool_response를 복사해 content만 바꾼 `updatedToolOutput`을 출력한다.
  status가 completed가 아닌 응답(백그라운드 async_launched 등)은 바꾸지 않는다.
- Codex: {"decision": "block", "reason": <한 줄>}로 결과를 바꾼다.
- 조사 문서가 없거나 `조사 문서:` 줄이 없으면 아무것도 출력하지 않는다. 오류는 통과한다(fail-open).

공용 모듈(common/): sub_call, sub_docs
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))

from sub_call import RESEARCHER, REVIEWER, call_info
from sub_docs import doc, task_id

RESEARCH_LINE = re.compile(r"^조사 문서: log/state/[\w.\-]+-research\.md\s*$", re.M)
LOOP_ROW = re.compile(r"^\| *(\d+) *\|", re.M)


def saved_line(data: dict) -> str | None:
    """바꿀 결과 한 줄이다. 대상이 아니면 None이다."""
    target, prompt = call_info(data)
    line = RESEARCH_LINE.search(prompt)
    if target not in RESEARCHER | REVIEWER or not line:
        return None
    tid = task_id(prompt)
    path = doc(tid, "research", data.get("cwd")) if tid else None
    if not path or not path.is_file():
        return None
    slot = "최종 조사 원문"
    if target in REVIEWER:
        slot = f"{len(LOOP_ROW.findall(path.read_text(encoding='utf-8')))}회차 반증 원문"
    return f"결과 저장: {line.group(0).split(': ', 1)[1].strip()} {slot}"


def replace(data: dict, runner: str) -> dict | None:
    """출력할 JSON이다. 바꿀 것이 없으면 None이다."""
    text = saved_line(data)
    if not text:
        return None
    if runner == "codex":
        return {"decision": "block", "reason": text}
    resp = data.get("tool_response")
    if not isinstance(resp, dict) or resp.get("status", "completed") != "completed":
        return None
    new = {**resp, "content": [{"type": "text", "text": text}] if isinstance(resp.get("content"), list) else text}
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": new}}


def main() -> int:
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    runner = sys.argv[sys.argv.index("--runner") + 1] if "--runner" in sys.argv else "claude"
    try:
        out = replace(json.load(sys.stdin), runner)
    except Exception:
        return 0
    if out:
        json.dump(out, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
