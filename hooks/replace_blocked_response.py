#!/usr/bin/env python3
"""MessageDisplay hook: 응답 검사(check_workflow_response.check)에 걸릴 응답을 화면에서 교체 문구로 바꾼다.

호출자:
- Claude Code의 MessageDisplay hook 설정(shared/settings/claude)
- 호출 명령은 `python replace_blocked_response.py`
입력: hook 입력 JSON(message_id, final, delta, cwd, transcript_path)을 stdin으로 받는다.
반환:
- stdout에 `{"hookSpecificOutput": {"hookEventName": "MessageDisplay", "displayContent": ...}}` JSON을 출력한다.
- final이 아닌 batch: 같은 message_id의 delta를 OS 임시 폴더 파일에 모으고 빈 displayContent를 출력한다.
- final batch: 모은 원문을 last_assistant_message로 check_workflow_response.check에 넣어 판정한다.
  위반이면 교체 문구를, 통과면 모은 원문 전체를 displayContent로 출력하고 임시 파일을 지운다.
  transcript 끝 20줄에서 원문과 같은 assistant text 항목의 stop_reason이 tool_use이면 도구 호출 앞의 중간 메시지로 보고 검사 없이 원문을 출력한다.
  stop_reason이 tool_use가 아니어도 transcript 전체에 완료 알림(`<task-id>`)이 없는 백그라운드 작업(async_launched agentId, backgroundTaskId)이 있으면 검사 없이 원문을 출력한다.
한계:
- 완료 알림이 기록되지 않은 작업이 있으면 그 세션의 이후 최종 응답도 교체 없이 표시된다. transcript 기록 형식에 의존한다.
- 중간 메시지는 transcript(파일 끝 256KB)의 stop_reason으로 판별한다. 항목이 없으면 0.2초 간격으로 최대 3초 다시 읽으므로 최종 응답 표시가 최대 3초 늦어진다.
- transcript_path가 없거나 읽기에 실패하면 중간 메시지 판별 없이 기존 판정을 적용한다.
- 화면 표시만 바꾸고 대화 기록은 원문 그대로이다.
- 판정에 stop_hook_active를 알 수 없어 재작성 응답도 같은 기준으로 판정한다.
- 응답이 끝날 때까지 화면에 글이 나오지 않는다. 오류는 원문을 표시한다(fail-open).
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path

from check_workflow_response import check

REPLACEMENT = "(직전 응답의 내용이 미흡하여 다시 작성합니다.)"


# message_id별 delta 모음 파일 경로를 반환한다.
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
    # 같은 메시지의 delta를 모은다.
    path = batch_file(data.get("message_id") or "unknown")
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(data.get("delta") or "")
    if not data.get("final"):
        return ""

    # 마지막 batch에서 모은 원문을 응답 검사로 판정하고 임시 파일을 지운다.
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
        # 백그라운드 작업이 끝나지 않은 채 끝난 턴은 Stop hook처럼 검사 없이 통과시킨다.
        if has_pending_background(data["transcript_path"]):
            return text
    reason = check({**data, "last_assistant_message": text}, "claude", record=False)
    return REPLACEMENT if reason else text


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
