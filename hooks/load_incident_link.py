#!/usr/bin/env python3
"""UserPromptSubmit hook: 미전달 감지 내용을 에이전트에 주입한다."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))

import check_invalid_response
from sub_paths import INCIDENT_REL

NOTICE_AGENT = ("직전 응답에서 에이전트 문제({hits}, 유형: {kinds})를 찾았습니다. 감지 시각: {stamp}.\n"
                "이 세션의 대화 맥락을 확인하여 무엇이 문제인지 파악하고, 누락된 정보를 작성하거나 작업을 수행하여 문제를 수정하십시오.\n"
                "현재 진행 중인 작업은 중단하지 않고 계속 진행하십시오. 응답으로 이미 전달된 내용을 재응답하지 마십시오.")
# 감지 기록 항목의 감지 시각과 감지 표현
ENTRY = re.compile(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\n  - 감지 표현: (.+)$", re.M)
# 인정 표현 유형에 없는 감지 표현의 유형 이름
UNKNOWN_KIND = "응답 미흡"


# 사건 문서의 감지 기록에서 감지 시각별 감지 표현 목록을 읽는다. 같은 시각의 항목은 한 목록으로 합친다.
def detections(path: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for stamp, hits in ENTRY.findall(path.read_text(encoding="utf-8")):
        for hit in hits.split(", "):
            if hit not in found.setdefault(stamp, []):
                found[stamp].append(hit)
    return found


# 감지 표현이 속한 인정 표현 유형 이름을 쉼표로 이어 반환한다. 어느 유형에도 없는 표현은 응답 미흡으로 센다.
def kinds_of(hits: list[str]) -> str:
    kinds: list[str] = []
    for hit in hits:
        kind = next((name for name, pattern in check_invalid_response.ADMIT_TYPES.items()
                     if re.fullmatch(pattern, hit)), UNKNOWN_KIND)
        if kind not in kinds:
            kinds.append(kind)
    return ", ".join(kinds)


# 이 세션의 사건 문서 중 세션 기록에 감지 시각이 아직 없는 감지 항목의 안내 문장 목록이다.
def undelivered_incidents(data: dict) -> list[str]:
    sid = (data.get("session_id") or "unknown")[:8]
    folder = check_invalid_response.INCIDENT_ROOT / INCIDENT_REL
    try:
        sent = Path(data.get("transcript_path") or "").read_text(encoding="utf-8")
    except (OSError, ValueError):
        sent = ""
    notices = []
    for path in sorted(folder.glob(f"*-{sid}.md")):
        for stamp, hits in detections(path).items():
            # 세션 기록의 줄바꿈은 이스케이프되므로 첫 줄에 든 감지 시각으로 전달 여부를 판정한다.
            if f"감지 시각: {stamp}" not in sent:
                notices.append(NOTICE_AGENT.format(hits=", ".join(f"`{hit}`" for hit in hits),
                                                   kinds=kinds_of(hits), stamp=stamp))
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
