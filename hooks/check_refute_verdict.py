"""SubagentStop hook: reviewer의 명시적 반증 출력에 근거 있는 판정 줄이 있는지 확인한다.

사용: python check_refute_verdict.py
hook 입력 JSON을 stdin으로 받는다. 위반이면 {"decision": "block", "reason": ...}를 출력한다.
오류·반증 mode 외 결과는 통과한다(fail-open).

판정 줄 형식 (reviewer의 마지막 메시지를 검사한다)
    주장 <번호>: <지지|반증|미확인> | 출처: <근거>
- 줄은 `-` 목록 기호와 `**` 강조가 앞뒤에 붙어도 된다.
- 지지·반증의 근거에는 http(s) URL 또는 `파일:행`(e.g. hooks/a.py:12)이 하나 이상 있어야 한다.
- 미확인의 근거는 `없음`이어도 된다.
- 판정 줄이 하나도 없거나 근거 없는 지지·반증이 있으면 매번 차단하고 다시 쓰게 한다.
예)
    - 주장 1: 지지 | 출처: https://example.com/doc
    - 주장 2: 반증 | 출처: https://example.com/a, https://example.com/b
    - 주장 3: 미확인 | 출처: 없음

루프 횟수·정지는 이 hook이 관리하지 않는다. researcher 지침이 정한다.

hook 입력 필드 확인 상태
- 미확인: SubagentStop 입력의 agent_type(Claude)·agent_role(Codex)·last_assistant_message·stop_hook_active.
- mode는 tool_input의 prompt/message 또는 하위 transcript 최초 사용자 요청에서 명시 표식으로 판별한다.
- tool_input 전달과 agent_transcript_path·transcript_path·Codex session_id의 하위 기록 지정은 fixture 가정이다.
  실제 통합 관측 전에는 확인된 필드로 취급하지 않는다.
- 요청 본문을 얻지 못하면 기존 fail-open을 적용하며 역할명만으로 mode를 추정하지 않는다.

공용 모듈(common/): sub_call, sub_session
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))

from sub_call import PROMPT_KEYS, REVIEWER, VERDICT, refute_mode
from sub_session import read_session, session_path

EVIDENCE = re.compile(r"https?://\S+|[\w./\\-]+\.\w+:\d+")


def request_prompt(data: dict) -> str:
    tin = data.get("tool_input") or {}
    prompt = next((tin[key] for key in PROMPT_KEYS if isinstance(tin.get(key), str)), None)
    if prompt is not None:
        return prompt
    path = data.get("agent_transcript_path") or session_path(data, "codex")
    entries = read_session(path)
    return next((entry["text"] for entry in entries if entry["role"] == "user"), "")


def check(data: dict) -> str | None:
    """위반 사유를 반환한다. 통과면 None이다."""
    if (data.get("agent_type") or data.get("agent_role")) not in REVIEWER or not refute_mode(request_prompt(data)):
        return None
    found = [(m.group(2), m.group(3)) for m in map(VERDICT.match, (data.get("last_assistant_message") or "").splitlines()) if m]
    bad = [v for v, src in found if v != "미확인" and not EVIDENCE.search(src)]
    if found and not bad:
        return None
    why = "판정 줄이 없습니다." if not found else "근거(URL 또는 파일:행)가 없는 지지·반증이 있습니다."
    return f"{why} `주장 <번호>: <지지|반증|미확인> | 출처: <URL 또는 파일:행>` 형식으로 다시 쓰십시오."


def main() -> int:
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        reason = check(json.load(sys.stdin))
    except Exception:
        return 0
    if reason:
        json.dump({"decision": "block", "reason": reason}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
