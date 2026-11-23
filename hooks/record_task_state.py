"""PostToolUse hook: 질문 도구 답, 작업 단계와 사용자 승인 시각을 경과 문서에 기록하고 작업 문서의 HTML 주석을 지운 뒤 작업 색인을 갱신한다."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_workflow_response"))

from check_invalid_response import transcript_of
from sub_docs import project_root, sibling, strip_task_comments
from sub_record import call_paths, call_status, record_answers, record_times
from sub_role import director_main, project_session
from sub_session import QUESTION_TOOLS, response_answers


# 작업 선택 질문의 답이 작업 ID이면 그 작업을 색인의 현재 진행 작업으로 기록한다. `신규 작업` 답은 기록하지 않는다.
def select_answers(root: Path, answers: list[tuple[dict, str]]) -> None:
    from sub_question import TASK_ID, TASK_SELECT, split_tag
    from sub_paths import TASK_REL
    from sub_task_index import refresh_index
    for question, answer in answers:
        found = TASK_ID.search(answer)
        if TASK_SELECT.match(split_tag(question.get("question") or "")[0]) and found \
                and (root / TASK_REL / found[0] / "status.md").is_file():
            refresh_index(root, found[0], current=True)


# PostToolUse 입력의 질문 도구 답을 기록하고 작업 단계를 정규화한다. 서브에이전트 입력은 건너뛴다.
def record_tool(data: dict, runner: str) -> str | None:
    data = {**data, "transcript_path": transcript_of(data, runner)}
    if not project_session(data, runner):
        return
    # 이번 호출이 쓴 작업 문서의 코드 블록 밖 HTML 주석을 지운다.
    root = project_root(data.get("cwd"))
    written = [root / p for p in call_paths(data.get("tool_name") or "", data.get("tool_input")) if p]
    for p in written:
        if p.resolve().is_relative_to(root.resolve()):
            strip_task_comments(p)
    if not director_main(data):
        from sub_task_index import refresh_index
        status = call_status(data)
        if status:
            refresh_index(root, status.parent.name)
        return

    # 질문 답을 기록한 뒤 경과 문서와 같은 작업의 문서에서 hook이 덧붙인 주석을 지운다.
    answers = (response_answers((data.get("tool_input") or {}).get("questions"), data.get("tool_response"))
               if data.get("tool_name") in QUESTION_TOOLS else [])
    try:
        select_answers(root, answers)
        record_answers(data, runner, answers)
        notice = record_times(data, runner)
    finally:
        status = call_status(data)
        if status:
            from sub_task_index import refresh_index
            refresh_index(root, status.parent.name)
            for kind in ("state", "input", "research", "refute"):
                strip_task_comments(sibling(status, kind))
    return notice


# stdin의 hook 입력을 기록하고 사용자 승인 시각을 확인하지 못했으면 systemMessage JSON을 출력한다. 항상 0을 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞추고 인자를 읽는다.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    runner = parser.parse_args().runner

    # 기록 오류는 통과시킨다.
    try:
        notice = record_tool(json.load(sys.stdin), runner)
        if notice:
            json.dump({"systemMessage": notice}, sys.stdout, ensure_ascii=False)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
