#!/usr/bin/env python3
"""UserPromptSubmit hook: 미전달 사건 문서 링크를 주입하고, 사용자 메시지 머리줄의 단계 키워드를 대상 작업의 경과 문서에 기록한다.

호출자: Claude Code와 Codex의 UserPromptSubmit hook 설정(설정 원본)
입력: hook 입력 JSON(prompt, transcript_path, cwd)을 stdin으로 받는다.
반환:
- 이 세션의 사건 문서 중 세션 기록에 링크가 아직 없는 것이 있으면 그 링크를 `additionalContext`로 stdout에 출력한다.
- 머리줄이 있으면 기록 결과나 미기록 사유도 `additionalContext`에 더한다.
- 둘 다 없으면 아무것도 출력하지 않고, 어느 경우에도 사용자 메시지를 차단하지 않는다.
한계:
- 머리줄은 첫 줄 전체가 `[kickoff|research|implement|complete]` 또는 `[키워드 작업ID]`일 때만 인정한다.
- 작업ID 없는 `[kickoff]`는 새 작업으로 보고 기존 문서에 기록하지 않는다.
- 작업ID 없는 다른 키워드는 세션 기록의 가장 나중 작업 지정(머리줄 작업ID, 문서 생성기 --task 값, log/task 경로)을 대상으로 한다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))

import check_invalid_response
from sub_approval import apply_stage, head_line
from sub_docs import project_root
from sub_paths import INCIDENT_REL, TASK_REL
from sub_session import read_session
from sub_write import strings

TASK = r"\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*"
DESIGNATION = re.compile(rf"--task[ =]+({TASK})|{re.escape(TASK_REL)}/({TASK})/")
REASON_FORMAT = ("첫 줄이 머리줄 형식이 아니라 작업 단계를 기록하지 않았습니다. "
                 "첫 줄에는 `[kickoff]`, `[research]`, `[implement]`, `[complete]` 중 하나 또는 `[키워드 작업ID]`만 쓰고 본문은 둘째 줄부터 쓰십시오.")
REASON_NO_DOC = "작업ID {task}의 경과 문서가 없어 작업 단계를 기록하지 않았습니다."
REASON_NEW = "작업ID 없는 `[kickoff]`는 새 작업으로 보고 기존 문서에 기록하지 않았습니다. 문서 생성기로 새 작업 폴더를 만드십시오."
REASON_NO_TARGET = "세션 기록에서 대상 작업을 찾지 못해 작업 단계를 기록하지 않았습니다. 머리줄에 `[키워드 작업ID]`를 쓰십시오."
RECORDED = "작업 {task}의 작업 단계를 `{stage}`로 기록했습니다."
NOTICE_INCIDENT = "사건 문서 링크: {link}. 직전 응답 미흡으로 기록된 사건입니다. 문서의 감지 기록을 읽고 같은 문제를 반복하지 마십시오."


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
        notice = NOTICE_INCIDENT.format(link=check_invalid_response.incident_link(path, data.get("cwd")))
        if notice not in sent:
            notices.append(notice)
    return notices


# 세션 기록에서 가장 나중의 작업 지정(사용자 머리줄 작업ID, 도구 호출의 --task 값, log/task 경로)이 가리키는 작업ID이다. 없으면 None이다.
def last_designation(path: str) -> str | None:
    found = None
    for entry in read_session(path):
        if entry["role"] == "user":
            found = head_line(entry["text"])[1] or found
        for _, args in entry["calls"]:
            for text in strings(args):
                for match in DESIGNATION.finditer(text):
                    found = match[1] or match[2]
    return found


# 미전달 사건 문서 링크와 머리줄 단계 기록 결과를 이은 additionalContext 문장을 반환한다. 둘 다 없으면 None이다.
def decide(data: dict) -> str | None:
    parts = undelivered_incidents(data)
    staged = decide_stage(data)
    return "\n".join(parts + ([staged] if staged else [])) or None


# 머리줄을 판정해 경과 문서에 작업 단계를 기록하고 결과 문장을 반환한다. 머리줄이 없으면 None이다.
def decide_stage(data: dict) -> str | None:
    stage, task = head_line(data.get("prompt") or "")
    if stage is None:
        return None
    if stage == "보류":
        return REASON_FORMAT

    # 대상 작업은 머리줄 작업ID, 세션 기록의 마지막 작업 지정 순으로 정한다. 작업ID 없는 [kickoff]는 새 작업이다.
    if task is None and stage == "착수":
        return REASON_NEW
    task = task or last_designation(data.get("transcript_path") or "")
    if task is None:
        return REASON_NO_TARGET
    status = project_root(data.get("cwd")) / TASK_REL / task / "status.md"
    if not status.is_file():
        return REASON_NO_DOC.format(task=task)
    return RECORDED.format(task=task, stage=apply_stage(status, stage))


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        context = decide(json.load(sys.stdin))
    except Exception:
        return 0
    if context:
        json.dump({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}},
                  sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
