#!/usr/bin/env python3
"""MessageDisplay hook: 구현 승인 요청 줄을 생성 본문으로 바꿔 표시한다."""
from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path

from check_workflow_response import NOTICE_REQUEST, check
from sub_docs import project_root
from sub_gate import REQUEST_LINE, approval_request
from sub_role import director_main


# message_id별 delta 누적 파일 경로를 반환한다.
def batch_file(message_id: str) -> Path:
    return Path(tempfile.gettempdir()) / f"nestlab-display-{re.sub(r'[^A-Za-z0-9-]', '', message_id)}.txt"


# transcript 끝 20줄에서 text와 같은 assistant text 항목 중 마지막 항목의 stop_reason을 반환한다. 읽기 실패는 None, 항목 없음은 ""이다.
def find_stop_reason(path: str, text: str):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 256 * 1024))
            lines = f.read().decode("utf-8", errors="replace").splitlines()[-20:]
    except OSError:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
            if entry.get("type") != "assistant":
                continue
            msg = entry.get("message") or {}
            blocks = msg.get("content")
            if not isinstance(blocks, list) or msg.get("role") != "assistant":
                continue
        except (ValueError, AttributeError):
            continue
        if any(isinstance(b, dict) and b.get("type") == "text" and b.get("text") == text for b in blocks):
            return msg.get("stop_reason") or ""
    return ""


# transcript 전체에서 실행됐지만 완료 알림이 없는 백그라운드 작업이 있으면 True를 반환한다. 읽기 실패는 False이다.
def has_pending_background(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return False
    launched, done = set(), set()
    for line in lines:
        # 실행 ID는 async_launched 줄의 agentId와 모든 줄의 backgroundTaskId이다.
        if '"status":"async_launched"' in line:
            launched.update(re.findall(r'"agentId":"([^"\\]+)"', line))
        launched.update(re.findall(r'"backgroundTaskId":"([^"\\]+)"', line))
        # 완료 ID는 task-notification의 task-id이다.
        done.update(re.findall(r"<task-id>([^<\s]+)</task-id>", line))
    return bool(launched - done)


# 표시할 내용을 반환한다. 비어 있는 문자열은 이 batch를 화면에 표시하지 않는다.
def display(data: dict) -> str:
    if not director_main(data):
        return data.get("delta") or ""
    # 같은 메시지의 delta를 누적한다.
    path = batch_file(data.get("message_id") or "unknown")
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(data.get("delta") or "")
    if not data.get("final"):
        return ""

    # 마지막 batch에서 누적한 원문을 응답 검사로 판정하고 임시 파일을 지운다.
    text = path.read_text(encoding="utf-8")
    path.unlink(missing_ok=True)
    # 도구 호출 앞의 중간 메시지는 검사 없이 원문을 표시한다. 항목이 없으면 최대 3초 다시 읽는다.
    if data.get("transcript_path"):
        for _ in range(15):
            stop = find_stop_reason(data["transcript_path"], text)
            if stop != "":
                break
            time.sleep(0.2)
        if stop == "tool_use":
            return text
        # 백그라운드 작업이 끝나지 않은 채 끝난 턴은 Stop hook과 같이 완료 판정만 건너뛰고 응답 미흡 표현은 판정한다.
        if has_pending_background(data["transcript_path"]):
            data = {**data, "background_tasks": [True]}
    reason = check({**data, "last_assistant_message": text}, "claude", record=False)
    return text if reason else with_request(text, data.get("cwd"))


# 통과한 응답의 `구현 승인 요청: <작업 ID>` 줄을 hook이 생성한 승인 요청 본문으로 바꾼다. 생성에 실패하면 원문이다.
def with_request(text: str, cwd: str | None) -> str:
    request = REQUEST_LINE.search(text)
    if not request:
        return text
    reason, output = approval_request(project_root(cwd), request[1])
    return text if reason else text[:request.start()] + output + NOTICE_REQUEST + text[request.end():]


# stdin의 batch를 처리해 displayContent JSON을 출력한다. 항상 0을 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞춘다.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    # 오류는 출력 없이 통과시켜 원문을 표시한다.
    try:
        content = display(json.load(sys.stdin))
    except Exception:
        return 0
    json.dump({"hookSpecificOutput": {"hookEventName": "MessageDisplay", "displayContent": content}},
              sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
