"""Stop hook: 응답의 결과물, 출처, 작업 흐름 위반과 책임 회피 표현을 검사하고 응답을 다시 쓰게 한다.

호출자:
- Claude Code와 Codex의 Stop hook 설정(설정 원본)
- 호출 커맨드는 `python check_workflow_response.py --runner <claude|codex>`이고, PostToolUse hook은 `--record`를 더한다.
입력: hook 입력 JSON(last_assistant_message, stop_hook_active 등)을 stdin으로 받는다.
반환:
- 위반이면 stdout에 `{"decision": "block", "reason": ..., "systemMessage": ...}` JSON을 출력하고 통과하면 아무것도 출력하지 않는다.
- `--record` 모드는 질문 도구 답과 작업 상태를 경과 문서에 기록하고 출력하지 않는다.
- check()는 block 사유 문자열 또는 None을 반환한다.
- 조정 검사와 작업 문서 기록은 director 메인에만 적용한다.
한계: 외부 모델을 호출하지 않는 정규식 판정이라 자연어 의미는 판정하지 못한다. 오류는 통과한다(fail-open).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_workflow_response"))

from check_invalid_response import MAX_BLOCKS, admissions, context, hit_count, incident_link, incident_path, is_child, plain, record_hits, transcript_of
from sub_approval import stage_of, stage_prefix
from sub_claim import REASON_SOURCE, unsourced_claims
from sub_docs import doc, measure_pending, project_root, review_pending, sibling, strip_task_comments
from sub_judgment import open_tasks
from sub_paths import TASK_REL
from sub_question import UNCONFIRMED
from sub_role import director_main, resolve_role
from sub_session import QUESTION_TOOLS, previous_stop_reason, read_session, response_answers, turn
from sub_document import Document, check_numbers
from sub_gate import check_plan, measurement_targets, transition_reason
from sub_write import read
from sub_completion import STATE_REF, STOP_REQUEST, complete_refusal, completion_reason, linked_statuses, stopped_statuses, unfinished_reason
from sub_form import APPROVAL_BLOCK, REASON_APPROVAL_FORM, REASON_APPROVAL_PLAN, approval_form_gap, approval_original_gap, body_of, choice_gaps, completed_response, form_gap, form_of, has_deliverable, requests_approval, source_gaps
from sub_record import call_paths, record_answers, record_times, record_unresolved, splice, written_status


# (작업 흐름 위반 사유, 관련 경과 문서)이다. log 폴더가 없는 저장소는 검사하지 않는다.
def workflow_reason(data: dict, message: str, body: str, runner: str) -> tuple[str | None, list[Path]]:
    # 보고와 착수 및 실측 승인 요청은 제목이 가리키는 양식의 누락을 차단한다.
    form = form_of(body)
    if form and (missing := form_gap(body, form, message)):
        return REASON_FORM.format(form=form, missing=", ".join(missing)), []
    requesting = (APPROVAL_BLOCK.search(body) or requests_approval(message)) and form not in ("approval-research", "approval-measure", "approval-complete")
    root = project_root(data.get("cwd"))
    if form and (gaps := source_gaps(body, form, root)):
        return REASON_CHOICE.format(missing="; ".join(gaps[:5])), []
    if form == "request-choice" and (gaps := choice_gaps(body, root)):
        return REASON_CHOICE.format(missing="; ".join(gaps[:5])), []
    if requesting and (word := next((w for w in UNCONFIRMED if w in body), None)):
        return REASON_CHOICE.format(missing=f"구현 승인 응답에 확인하지 않은 내용 `{word}`가 있습니다. 직접 확인한 뒤 다시 작성하십시오"), []
    refs = [doc(task, "state", root) for task in dict.fromkeys(STATE_REF.findall(body))]
    plans = [read(sibling(path, "input")) for path in refs if sibling(path, "input").is_file()]
    # 단계 없는 기존 작업의 명세 부재 호환은 유지하며 새 단계 작업은 명세 대조를 요구한다.
    if not plans and not any(stage_of(read(path)) for path in refs):
        plans = None
    if requesting and (missing := approval_form_gap(body, plans)):
        return REASON_APPROVAL_FORM + " 누락 절: " + ", ".join(missing), []
    if requesting:
        for path in refs:
            if not sibling(path, "input").is_file():
                continue
            try:
                check_plan(Document(read(sibling(path, "input"))), Document(read(path)))
            except ValueError as error:
                return REASON_APPROVAL_PLAN + str(error), refs
    # 작업 문서 폴더가 없으면 검사하지 않는다.
    root = project_root(data.get("cwd"))
    if not (root / "log").is_dir():
        return None, []

    # 응답이 가리킨 경과 문서를 찾고, 없으면 진행 중인 작업을 쓴다.
    refs = [doc(task, "state", root) for task in dict.fromkeys(STATE_REF.findall(message))]
    docs = [p for p in refs if p.is_file()] or open_tasks(root)

    # 승인 요청 블록은 director의 상태 문서 경로를 요구하고 참조 문서의 검토 필요 항목을 검사한다.
    if APPROVAL_BLOCK.search(body):
        if not refs and resolve_role(data) == "director":
            return REASON_APPROVAL_DOC, docs
        for path in refs:
            text = read(path)
            pending = (review_pending(text) + measure_pending(text, targets=measurement_targets(text,
                           Document(read(sibling(path, "input"))) if sibling(path, "input").is_file() else None)))
            if pending:
                return REASON_APPROVAL_PENDING.format(doc=path.name, items=", ".join(pending[:3])), [path]

            # 같은 작업 폴더의 명세 문서 요구사항 번호가 요구사항 목록 번호와 다르면 차단한다. 명세 문서가 없거나 읽을 수 없으면 건너뛴다.
            spec = path.with_name("spec.md")
            if spec.is_file():
                try:
                    state_doc, plan_doc = Document(text), Document(read(spec))
                except ValueError:
                    continue
                try:
                    check_numbers(state_doc, plan_doc)
                except ValueError as error:
                    return str(error), [path]

                # 구현 승인을 받은 것으로 가정한 전이 검사가 막히면 승인 요청 시점에 재작성하게 한다.
                assumed = splice(text, "구현 승인 범위", lambda body: re.sub(
                    r"(?m)^(\s*-\s*(?:사용자 )?승인 상태:\s*).*$", r"\1승인", body))
                reason = transition_reason(path, assumed, text, read(spec))
                if reason:
                    return REASON_APPROVAL_TRANSITION.format(doc=path.name, reason=reason), [path]

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


# 응답 미흡으로 판정할 표현(책임 회피, 다짐, 확인하지 않은 내용)을 찾는다. 검색 결과 요약의 [미조사] 줄과 사후 모델 호출 실측 줄은 제외한다. 서브에이전트 응답은 검사하지 않는다.
def shift_hits(data: dict, runner: str) -> list[str]:
    if data.get("agent_id") or is_child(data):
        return []
    message = data.get("last_assistant_message") or ""
    text = chr(10).join(line for line in plain(message).splitlines() if not EXEMPT_LINE.search(line))
    if not SHIFT.search(text):
        return []
    user = context(transcript_of(data, runner), message, data.get("cwd"))[0]
    return admissions(text, user, SHIFT)
# block 사유를 반환한다. 통과하면 None이다. record가 거짓이면 사건 기록과 분석 실행 없이 판정만 한다.
def check(data: dict, runner: str = "claude", record: bool = True) -> str | None:
    return inspect(data, runner, record)[0]


# (block 사유, 사용자 경고)를 반환한다. 경고는 책임 회피 감지 때만 있고 없으면 빈 문자열이다.
def inspect(data: dict, runner: str = "claude", record: bool = True) -> tuple[str | None, str]:
    data = {**data, "transcript_path": transcript_of(data, runner)}
    # 서브에이전트 응답은 통과한다.
    if not director_main(data):
        return None, ""
    message = data.get("last_assistant_message") or ""
    reason = approval_original_gap(data, runner, message)
    if reason:
        return reason, ""
    message = completed_response(data, runner, message)
    data = {**data, "last_assistant_message": message}
    body = body_of(message)

    # 이번 턴 질문 도구 답을 경과 문서에 기록한다. 백그라운드 작업이 남은 응답도 기록하고, 기록 오류는 판정에 영향을 주지 않는다.
    if record:
        try:
            record_answers(data, runner)
            record_times(data, runner)
        except Exception:
            pass
    if APPROVAL_BLOCK.search(body) or requests_approval(message):
        reason, _ = workflow_reason(data, message, body, runner)
        if reason and reason.startswith((REASON_APPROVAL_FORM, REASON_APPROVAL_PLAN, "`approval-")):
            return reason, ""
    user, _, entries = turn(read_session(transcript_of(data, runner)))
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
    strict_form = reason and reason.startswith((REASON_APPROVAL_FORM, REASON_APPROVAL_PLAN, "`approval-"))
    prior = previous_stop_reason(transcript_of(data, runner)) if reason and data.get("stop_hook_active") else ""
    if reason and not strict_form and prior and (prior == reason or prior.startswith(reason + "\n")):
        record_unresolved(docs, reason)
        return None, ""
    if reason:
        return reason, ""

    # 응답 미흡 표현은 사건 문서에 기록하고 재작성하게 한다. 같은 사용자 메시지의 block이 한도에 닿으면 기록만 한다.
    hits = shift_hits(data, runner)
    if hits:
        user = context(transcript_of(data, runner), message, data.get("cwd"))[0]
        if record:
            path = record_hits(data, runner, hits)
            count = hit_count(path, user)
        else:
            path = incident_path(data, datetime.now())
            count = hit_count(path, user) + 1
        notice = f"사건 감지({', '.join(hits)}): {path}"
        link = incident_link(path, data.get("cwd"))
        return (None if count > MAX_BLOCKS or data.get("stop_hook_active") else REASON_EMPTY), notice

    # 결과물 없는 미흡 응답과 출처 없는 주장은 턴당 한 번만 block한다.
    if data.get("stop_hook_active"):
        return None, ""
    if (not has_deliverable(message, body) and len(body.strip()) <= MAX_LEN
            and (ANNOUNCE.search(body) or ADMIT.search(body))):
        return REASON_EMPTY, ""
    claims = unsourced_claims(message, body)
    if claims:
        return REASON_SOURCE.format(sentences=" / ".join(claims[:3])), ""
    return None, ""


# PostToolUse 입력의 질문 도구 답을 경과 문서에 기록하고 작업 상태를 다시 계산한다. 서브에이전트 입력은 건너뛴다.
def record_tool(data: dict, runner: str) -> str | None:
    data = {**data, "transcript_path": transcript_of(data, runner)}
    if not director_main(data):
        return
    # 이번 호출이 쓴 작업 문서의 코드 블록 밖 HTML 주석을 지운다.
    root = project_root(data.get("cwd"))
    written = [root / p for p in call_paths(data.get("tool_name") or "", data.get("tool_input")) if p]
    for p in written:
        strip_task_comments(p if p.is_absolute() else root / p)
    if data.get("agent_id"):
        return

    # 질문 답을 기록한 뒤 경과 문서와 같은 작업의 문서에서 hook이 덧붙인 주석을 지운다.
    answers = (response_answers((data.get("tool_input") or {}).get("questions"), data.get("tool_response"))
               if data.get("tool_name") in QUESTION_TOOLS else [])
    try:
        record_answers(data, runner, answers)
        notice = record_times(data, runner)
    finally:
        status = written_status(data, runner)
        if status:
            for kind in ("state", "input", "research", "refute"):
                strip_task_comments(sibling(status, kind))
    return notice


# stdin의 hook 입력을 검사하고 위반이면 block JSON을 출력한다. 항상 0을 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞추고 인자를 읽는다.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    runner = args.runner

    # PostToolUse 기록 모드는 질문 답과 작업 상태를 기록하고 출력 없이 끝낸다.
    if args.record:
        try:
            notice = record_tool(json.load(sys.stdin), runner)
            if notice:
                json.dump({"systemMessage": notice}, sys.stdout, ensure_ascii=False)
        except Exception:
            pass
        return 0

    # 검사 오류는 통과시키고, 위반은 block으로 출력한다.
    try:
        reason, notice = inspect(json.load(sys.stdin), runner)
    except Exception:
        return 0
    if reason:
        output = {"decision": "block", "reason": f"{reason}\n위 사유에 적힌 제목과 필드를 포함해 보완할 절 전체만 이어서 작성하십시오."}
        if notice:
            output["systemMessage"] = notice
        elif runner == "codex":
            output["systemMessage"] = WARNING  # Codex는 응답 표시를 바꿀 수 없어 경고로 알린다.
        json.dump(output, sys.stdout, ensure_ascii=False)
    return 0

MAX_LEN = 300
# 예고: 미래형 약속과 미실행 선언
ANNOUNCE = re.compile(
    r"겠습니다|하겠어요|할게요|드릴게요|예정입니다|(?:아직|따로|별도로)[^.\n]{0,20}(?:변경|수정|실행|적용)하지 않았")
# 인정과 자기 평가
ADMIT = re.compile(
    r"맞습니다[!.]?|죄송|제가 잘못|제 잘못|제 실수|해결하는 대신|못 했|못했")
# 위치 이동을 뜻하는 `앞으로 <이동 동사>`는 다짐이 아니므로 책임 회피에서 뺀다.
MOVE = r"(?!\s*(?:옮|당기|당겨|두|둔|배치|이동))"
# 응답 미흡: 후속 작업을 사용자에게 넘기거나 해결을 포기하는 표현, 다짐 문장, 확인하지 않은 내용을 담은 문장
SHIFT = re.compile(
    r"하실 (?:일은 없|필요는 없|필요 없)|추가로 [^.\n]*?은 없"
    r"|해결됐다고 할 수 없|멈추겠|멈출게요|그만두겠|그만하겠|중단하겠|여기서 마치|이미 충분"
    r"|앞으로" + MOVE + r"|앞으로는|이제부터|다음(?: \S+)?부터는? [^.\n]*?(?:겠습니다|게요)"
    r"|미확인|확인하지 않|확인되지 않")
# 응답 미흡 판정에서 제외하는 줄이다. 검색 결과 요약의 [미조사] 줄과 사후 모델 호출 실측 줄이다.
EXEMPT_LINE = re.compile(r"\[미조사\]|^\s*[-*]?\s*사후 모델 호출 실측:")
QUESTION_END = re.compile(r"[?？]\s*\**\s*$")


REASON_APPROVAL_DOC = f"승인 요청에 작업의 경과 문서 경로({TASK_REL}/<작업-id>/status.md)를 링크로 작성하십시오."
REASON_APPROVAL_PENDING = ("경과 문서 '{doc}'의 검토 필요 또는 사전 모델 호출 실측 필요에 끝나지 않은 항목이 있어 승인을 요청할 수 없습니다. 해당 항목은 '{items}'입니다.\n"
                           "검토 필요 항목은 확정 답을, 모델 호출 실측 필요 항목은 상태 `모델 호출 실측 완료`를 기록한 뒤 승인 요청을 진행하십시오.")
REASON_APPROVAL_TRANSITION = "경과 문서 '{doc}'에 구현 승인을 기록하면 차단됩니다. 승인 요청 전에 수정하십시오. 사유: {reason}"
REASON_QUESTION = ("응답을 질문 문장으로 끝냈습니다. 지침이나 요구로 정해지는 사안이면 근거를 적고 진행하십시오.\n"
                   "사용자 판단이 필요하면 후보 비교를 응답에 쓰고 질문 도구로 검토 <번호>. 질문을 하십시오.")
REASON_COMPLETE = "경과 문서 '{doc}'의 모든 요구사항 검증이 PASS이고 남은 문제가 없는데 완료로 바뀌지 않습니다. 사유: {reason}"
REASON_FORM = "`{form}` 양식의 필수 제목이나 항목이 비어 있습니다. 누락: {missing}. 생성기의 `--print-template {form}` 출력 양식에 맞춰 응답을 다시 작성하십시오."
REASON_CHOICE = "후보 보고의 안 블록을 고쳐 다시 작성하십시오. {missing}"


WARNING = "직전 응답은 응답 검사에서 무효 처리됐습니다. 다시 작성합니다."


REASON_EMPTY = ("직전 응답에서 사용자 요청에 대한 결과물 없이 예고나 해명만 작성되었습니다.\n"
                "결과물(코드, 표, 목록, 파일 경로, diff)을 포함하여 다시 작성하십시오.")


if __name__ == "__main__":
    sys.exit(main())
