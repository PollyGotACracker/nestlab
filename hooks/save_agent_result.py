"""SubagentStop hook: researcher 최종 결과와 reviewer 반증 결과를 조사 문서의 기존 칸에 원문 그대로 쓴다.

사용: python save_agent_result.py --runner <claude|codex>
- researcher: `최종 조사 원문` 코드 블록 내용을 교체한다. 보고는 SubagentHandback 입력이며 없으면 last_assistant_message이다.
- reviewer 반증: `조사 단계 원문` 절 끝에 `<n>회차 반증 요청`(반증 요청 본문)과 `<n>회차 반증 원문`을 덧붙이고
  `조사·반증 루프` 표에 회차 행을 추가한다.
- reviewer 검수는 저장하지 않는다. 판정은 director가 상태 문서 `검수 결과`에 기록한다.
- 작업 ID는 하위 기록 첫 요청의 `입력 문서:`·`상태 문서:` 줄에서 읽는다.
  반증 요청처럼 문서 줄이 없으면 Claude는 ACTIVE의 researcher 값, Codex는 부모 thread 첫 요청에서 찾는다.
- 조사 문서가 없으면 아무것도 하지 않는다. 출력은 없다. 오류는 통과한다(fail-open).

공용 모듈(common/): sub_call, sub_docs, sub_role
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))

from sub_call import VERDICT, refute_mode, verdict_problem
from sub_docs import ACTIVE, doc, task_id
from sub_role import codex_rollout, first_prompt, parent_thread, resolve_role
from sub_session import handback_report

LOOP_ROW = re.compile(r"^\| *(\d+) *\|", re.M)
MAX_ROUNDS = 2


def transcript(data: dict, runner: str) -> str:
    path = data.get("agent_transcript_path")
    if path:
        return path
    return codex_rollout(data.get("agent_id")) if runner == "codex" else ""


def find_task(data: dict, runner: str, path: str, prompt: str) -> str | None:
    tid = task_id(prompt)
    if tid:
        return tid
    if runner == "codex":
        return task_id(first_prompt(codex_rollout(parent_thread(path))))
    try:
        return json.loads((ACTIVE / f"{data.get('session_id')}.json").read_text(encoding="utf-8")).get("researcher")
    except (OSError, ValueError):
        return None


def fence_for(body: str) -> str:
    """본문 안의 가장 긴 backtick 연속보다 1개 긴 펜스이다. 최소 3개이다."""
    longest = max((len(run) for run in re.findall(r"`{3,}", body)), default=2)
    return "`" * max(3, longest + 1)


def replace_block(text: str, title: str, body: str) -> str:
    """제목 아래 코드 블록을 펜스까지 통째로 교체한다. 펜스 길이는 본문에 맞춘다."""
    pattern = re.compile(rf"(^#{{2,3}} {re.escape(title)}[^\n]*\n+)(`{{3,}})[^\n]*\n.*?\n\2[ \t]*$", re.M | re.S)
    fence = fence_for(body)
    return pattern.sub(lambda m: f"{m.group(1)}{fence}\n{body}\n{fence}", text, count=1)


def add_round_blocks(text: str, request: str, message: str) -> str:
    """`조사 단계 원문` 절 끝에 이번 회차의 반증 요청과 반증 원문 블록을 덧붙인다."""
    n = len(LOOP_ROW.findall(text)) + 1
    blocks = "".join(f"\n### {n}회차 반증 {kind}\n\n{fence_for(body)}\n{body}\n{fence_for(body)}\n"
                     for kind, body in (("요청", request.strip()), ("원문", message)))
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.strip() == "## 조사 단계 원문"), None)
    if start is None:
        return text
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    head = "".join(lines[:end]).rstrip("\n") + "\n"
    tail = "".join(lines[end:])
    return head + blocks + ("\n" + tail if tail else "")


def add_loop_row(text: str, message: str) -> str:
    n = len(LOOP_ROW.findall(text)) + 1
    refuted = sum(1 for line in message.splitlines() if (m := VERDICT.match(line)) and m.group(2) == "반증")
    stop = "정지(반증 0건)" if refuted == 0 else f"정지({MAX_ROUNDS}회 도달)" if n >= MAX_ROUNDS else "계속"
    row = f"| {n} | {refuted} | {stop} |"
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == "## 조사·반증 루프"), None)
    if start is None:
        return text
    end = start + 1
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    last = max((i for i in range(start, end) if lines[i].strip().startswith("|")), default=end - 1)
    lines.insert(last + 1, row)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def save(data: dict, runner: str) -> None:
    role = resolve_role(data)
    if role not in ("researcher", "reviewer"):
        return
    path = transcript(data, runner)
    # SubagentHandback으로 넘긴 보고가 최종 결과이다. 뒤따르는 후속 응답으로 덮어쓰지 않는다.
    message = handback_report(path) or (data.get("last_assistant_message") or "").strip()
    if not message:
        return
    prompt = first_prompt(path) or "\n".join(str(v) for v in (data.get("tool_input") or {}).values())
    refute = role == "reviewer" and refute_mode(prompt)
    if role == "reviewer" and not refute:
        return
    if refute and verdict_problem(message):  # check_refute_verdict가 차단하는 보고는 다시 쓴 뒤에 저장한다.
        return
    tid = find_task(data, runner, path, prompt)
    target = doc(tid, "research") if tid else None
    if not target or not target.is_file():
        return
    text = target.read_text(encoding="utf-8")
    if refute:
        if message in text:  # 같은 보고로 다시 멈춘 경우는 이미 기록했다.
            return
        text = add_loop_row(add_round_blocks(text, prompt, message), message)
    else:
        text = replace_block(text, "최종 조사 원문", message)
    target.write_text(text, encoding="utf-8")


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    runner = parser.parse_args().runner
    try:
        save(json.load(sys.stdin), runner)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
