"""Stop hook: 응답의 양식, 구현 승인 요청, 완료 판정과 경과 문서의 작업 흐름 위반을 검사하고 응답을 다시 쓰게 한다."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_workflow_response"))

from check_invalid_response import transcript_of
from sub_approval import stage_prefix
from sub_docs import doc, project_root
from sub_role import director_main, project_session
from sub_session import previous_stop_reason, read_session, turn
from sub_gate import REQUEST_LINE, approval_request
from sub_completion import STATE_REF, STOP_REQUEST, complete_refusal, completion_reason, linked_statuses, stopped_statuses, unfinished_reason
from sub_form import APPROVAL_BLOCK, body_of, completed_response, form_gap, form_of, source_gaps
from sub_record import record_answers, record_times, record_unresolved, splice  # noqa: F401 sub_approval이 불러온다.


# (작업 흐름 위반 사유, 관련 경과 문서)이다. .agent-logs 폴더가 없는 저장소는 검사하지 않는다.
def workflow_reason(data: dict, message: str, body: str, runner: str) -> tuple[str | None, list[Path]]:
    # 보고와 조사 및 실측 승인 요청은 제목이 가리키는 양식의 누락을 차단한다.
    form = form_of(body)
    if form and (missing := form_gap(body, form, message)):
        return REASON_FORM.format(form=form, missing=", ".join(missing)), []
    root = project_root(data.get("cwd"))
    if form and (gaps := source_gaps(body, form, root)):
        return REASON_CHOICE.format(missing="; ".join(gaps[:5])), []
    # 작업 문서 폴더가 없으면 검사하지 않는다.
    if not (root / ".agent-logs").is_dir():
        return None, []

    # 응답이 가리킨 경과 문서를 찾고, 없으면 진행 중인 작업을 쓴다.
    refs = [doc(task, "state", root) for task in dict.fromkeys(STATE_REF.findall(message))]
    from sub_task_index import selected_status
    selected = selected_status(root)
    docs = [p for p in refs if p.is_file()] or ([selected] if selected else [])

    # 완료 전이가 거부되는 경과 문서는 거부 사유를 안내한다.
    for path in docs:
        reason = complete_refusal(path)
        if reason:
            return REASON_COMPLETE.format(doc=path.name, reason=reason), [path]

    # Claude 응답이 질문 문장으로 끝나면 질문 도구를 쓰게 한다.
    lines = [line for line in body.splitlines() if line.strip()]
    if runner == "claude" and lines and QUESTION_END.search(lines[-1]):
        return REASON_QUESTION, docs
    return None, docs


# block 사유를 반환한다. 통과하면 None이다. record가 거짓이면 사건 기록과 분석 실행 없이 판정만 한다.
def check(data: dict, runner: str = "claude", record: bool = True) -> str | None:
    return inspect(data, runner, record)[0]


# (block 사유, 사용자 경고)를 반환한다. 경고는 책임 회피 감지 때만 있고 없으면 빈 문자열이다.
def inspect(data: dict, runner: str = "claude", record: bool = True) -> tuple[str | None, str]:
    data = {**data, "transcript_path": transcript_of(data, runner)}
    # 서브에이전트 응답은 통과한다.
    if not project_session(data, runner):
        return None, ""
    message = data.get("last_assistant_message") or ""
    message = completed_response(data, runner, message)
    data = {**data, "last_assistant_message": message}
    body = body_of(message)
    # 작업 문서를 수정한 director 외 세션은 양식 검사만 적용한다.
    if not director_main(data):
        form = form_of(body)
        missing = form_gap(body, form, message) if form else []
        return (REASON_FORM.format(form=form, missing=", ".join(missing)) if missing else None), ""

    # 이번 턴 질문 도구 답을 경과 문서에 기록한다. 백그라운드 작업이 남은 응답도 기록하고, 기록 오류는 판정에 영향을 주지 않는다.
    if record:
        try:
            record_answers(data, runner)
            record_times(data, runner)
        except Exception:
            pass
    # 구현 승인 요청은 `구현 승인 요청: <작업 ID>` 한 줄이고, 검사를 통과하면 생성한 승인 요청 본문을 사용자에게 표시한다.
    # Claude는 MessageDisplay hook(show_approval_request)이 요청 줄을 본문으로 바꾸고, Codex는 본문을 block reason으로 표시한다.
    if APPROVAL_BLOCK.search(body):
        return REASON_REQUEST_FORM, ""
    request = REQUEST_LINE.search(body)
    if request:
        reason, output = approval_request(project_root(data.get("cwd")), request[1])
        return reason, ("" if reason or runner != "codex" else output + NOTICE_REQUEST)
    user, _, entries = turn(read_session(transcript_of(data, runner)))
    # 백그라운드 작업이 남은 응답은 완료 판정을 건너뛴다.
    if data.get("background_tasks") and form_of(body) != "approval-complete" and stage_prefix(user) != "완료":
        return None, ""
    if STOP_REQUEST.search(user):
        return None, ""
    reason = completion_reason(data, runner, message, user, entries)
    if reason:
        return reason, ""

    # 미완료 종료는 같은 사유가 연속으로 반복되면 한 번만 막고 통과시킨다.
    reason = unfinished_reason(data, message, runner)
    if reason and not (data.get("stop_hook_active") and previous_stop_reason(transcript_of(data, runner)).startswith(reason)):
        return reason, ""
    if stopped_statuses(data, runner, entries, linked_statuses(project_root(data.get("cwd")), user, entries)):
        return None, ""

    # 작업 흐름 위반은 한 번 block하고, 반복되면 미해결로 기록한 뒤 통과한다.
    reason, docs = workflow_reason(data, message, body, runner)
    strict_form = reason and reason.startswith("응답: ")
    prior = previous_stop_reason(transcript_of(data, runner)) if reason and data.get("stop_hook_active") else ""
    if reason and not strict_form and prior and (prior == reason or prior.startswith(reason + "\n")):
        record_unresolved(docs, reason)
        return None, ""
    return reason, ""


# stdin의 hook 입력을 검사하고 위반이면 block JSON을 출력한다. 항상 0을 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞추고 인자를 읽는다.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    args = parser.parse_args()
    runner = args.runner

    # 검사 오류는 통과시키고, 위반은 block으로 출력한다.
    try:
        reason, notice = inspect(json.load(sys.stdin), runner)
    except Exception:
        return 0
    output = stop_output(reason, notice, runner)
    if output:
        json.dump(output, sys.stdout, ensure_ascii=False)
    return 0


# 차단 사유 뒤의 작성 지시. 차단 응답은 화면에 그대로 남으므로 같은 내용을 반복하지 않게 한다.
CONTINUE = ("직전 응답은 사용자에게 그대로 표시되어 있습니다. 직전 응답의 내용을 반복하지 말고, "
            "위 사유가 지적한 누락 내용만 이어서 작성하십시오.")


# 판정 결과를 Stop 출력 JSON으로 바꾼다. 출력할 내용이 없으면 None이다.
# Codex는 systemMessage를 대화 기록에 표시하지 않으므로 사용자 안내를 block reason(Hook feedback)에 담는다.
def stop_output(reason: str | None, notice: str, runner: str) -> dict | None:
    if reason:
        reason = f"{reason}\n{CONTINUE}"
        if notice and runner == "codex":
            return {"decision": "block", "reason": f"{notice}\n\n{reason}"}
        return {"decision": "block", "reason": reason, **({"systemMessage": notice} if notice else {})}
    if notice and runner == "codex":
        return {"decision": "block", "reason": notice}
    return {"systemMessage": notice} if notice else None


QUESTION_END = re.compile(r"[?？]\s*\**\s*$")


REASON_REQUEST_FORM = ("구현 승인 요청 본문은 hook이 생성해 표시합니다. 응답에는 `구현 승인 요청: <작업 ID>` 한 줄만 작성하십시오.")
NOTICE_REQUEST = "\n\n구현을 승인하려면 `승인`을 입력하십시오."
REASON_QUESTION = ("응답을 질문 문장으로 끝냈습니다. 지침이나 요구로 정해지는 사안이면 근거를 적고 진행하십시오.\n"
                   "사용자 판단이 필요하면 후보 비교를 응답에 쓰고 질문 도구로 검토 <번호>. 질문을 하십시오.")
REASON_COMPLETE = "경과 문서 '{doc}'의 모든 요구사항 검증이 PASS이고 남은 문제가 없는데 완료로 바뀌지 않습니다. 사유: {reason}"
REASON_FORM = "응답: 관련 지침과 {form} 템플릿을 준수하십시오. 보완 대상: {missing}"
REASON_CHOICE = "후보 보고에서 누락된 안 블록 항목을 보완하십시오. {missing}"



if __name__ == "__main__":
    sys.exit(main())
