#!/usr/bin/env python3
"""UserPromptSubmit hook: 미전달 사건 문서 링크를 에이전트에 주입한다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))

import check_invalid_response
from sub_paths import INCIDENT_REL

NOTICE_AGENT = ("직전 응답에서 문제가 있거나 문제를 인정한 표현을 찾았습니다.\n"
                "별도의 세션이 해당 문제를 분석하는 중입니다. 사건 문서 링크: {link}.\n"
                "감지 기록의 응답 발췌를 읽고, 누락된 정보를 작성하거나 작업을 수행하여 문제를 수정하십시오.\n"
                "현재 진행 중인 작업은 중단하지 않고 계속 진행하십시오. 응답으로 이미 전달된 내용을 재응답하지 마십시오.")


# 이 세션의 사건 문서 중 세션 기록에 링크가 아직 없는 문서의 안내 문장 목록이다.
def undelivered_incidents(data: dict) -> list[str]:
    sid = (data.get("session_id") or "unknown")[:8]
    folder = check_invalid_response.INCIDENT_ROOT / INCIDENT_REL
    try:
        sent = Path(data.get("transcript_path") or "").read_text(encoding="utf-8")
    except (OSError, ValueError):
        sent = ""
    notices = []
    for path in sorted(folder.glob(f"*-{sid}.md")):
        notice = NOTICE_AGENT.format(link=check_invalid_response.incident_link(path, data.get("cwd")))
        # 세션 기록의 줄바꿈은 이스케이프되므로 첫 줄로 전달 여부를 판정한다.
        if notice.splitlines()[0] not in sent:
            notices.append(notice)
    return notices


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        context = "\n".join(undelivered_incidents(json.load(sys.stdin)))
    except Exception:
        return 0
    if context:
        json.dump({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}},
                  sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
