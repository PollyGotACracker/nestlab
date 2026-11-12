"""Stop 시점에 승인된 작업이 끝나지 않았는지, 완료 승인 요청의 선행 검사를 통과했는지 판정한다.

호출자: hooks/check_workflow_response.py
입력: hook 입력 dict, runner, 세션 기록, 사용자 메시지
반환: 차단 사유 문자열 또는 None, 연결된 경과 문서 경로 목록
한계: 세션 기록에서 읽을 수 있는 사용자 메시지와 도구 결과만 근거로 삼는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from check_invalid_response import is_child, plain, transcript_of
from sub_approval import APPROVED, stage_prefix
from sub_docs import doc, project_root, review_done_numbers, section, sibling
from sub_judgment import open_tasks
from sub_question import REVIEW, split_tag, tasks_of
from sub_scope import STATE_LINE
from sub_session import QUESTION_TOOLS, question_answers, read_session, turn
from sub_document import Document, dropped, number_of, numbered, requirements
from sub_gate import check_complete, transition_reason
from sub_write import read
from sub_form import APPROVAL_BLOCK, body_of, form_of, requests_approval
from sub_paths import TASK_REL
from sub_record import REQUEST_APPROVED, picked_labels, written_status


# 현재 요청이 가리킨 작업의 경과 문서 목록이다. 도구 입력에 적힌 다른 작업 경로는 연결하지 않는다.
def linked_statuses(root: Path, user: str, entries: list[dict]) -> list[Path]:
    refs = STATE_REF.findall(user)
    if not refs:
        refs = re.findall(r"\b\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*\b", user)
    return [doc(task, "state", root) for task in dict.fromkeys(refs)]


# 실제 선택지와 일치하는 현재 턴의 중단 답이 연결된 작업만 반환한다.
def stopped_statuses(data: dict, runner: str, entries: list[dict], paths: list[Path]) -> set[Path]:
    stopped = set()
    for question, answer in question_answers(entries):
        labels = picked_labels(question, answer)
        if len(labels) != 1 or not STOP_REQUEST.search(labels[0]):
            continue
        if re.search(r"(?:중단|종료|취소)하지", labels[0]):
            continue
        text, tag = split_tag(question.get("question") or "")
        if tag:
            stopped.update(path for path in paths if path.parent.name[4:15] == tag)
        elif STATE_REF.search(text):
            root = project_root(data.get("cwd"))
            stopped.update(set(paths) & {doc(task, "state", root) for task in STATE_REF.findall(text)})
        else:
            targets = tasks_of(data, runner)
            if len(targets) == 1:
                stopped.update(set(paths) & set(targets))
    return stopped


# 현재 요청과 도구 입력이 연결한 승인 작업의 완료 조건을 검사한다. 일반 질문과 실제 사용자 대기는 허용한다.
def unfinished_reason(data: dict, message: str, runner: str) -> str | None:
    user, _, entries = turn(read_session(transcript_of(data, runner)))
    if not WORK_REQUEST.search(plain(body_of(user))) or STOP_REQUEST.search(user) or is_child(data):
        return None
    root = project_root(data.get("cwd"))
    paths = linked_statuses(root, user, entries)
    stopped = stopped_statuses(data, runner, entries, paths)
    if APPROVAL_BLOCK.search(body_of(message)) or requests_approval(message):
        return None
    # 권한 거부 결과와 그 영향 및 사용자 조작을 함께 보고한 경우만 실행 차단 대기로 본다.
    if (any(TOOL_DENIED.search(result) for entry in entries for result in entry["results"])
            and re.search(r"차단|권한|거부", message) and re.search(r"영향|불가|못|중단", message)
            and re.search(r"사용자(?:가|의|\s*(?:조작|작업|승인|설정|입력))", message)):
        return None
    for path in paths:
        if path in stopped:
            continue
        state, plan = read(path), read(sibling(path, "input"))
        if not REQUEST_APPROVED(state) or not plan.strip():
            continue
        # 실제 질문 도구가 아직 확정되지 않은 검토 항목을 요청했으면 사용자 답을 기다린다.
        done = review_done_numbers(state)
        asked = [q for entry in entries for name, args in entry["calls"] if name in QUESTION_TOOLS
                 for q in (args or {}).get("questions", []) if isinstance(q, dict)]
        if any((m := REVIEW.match(split_tag(q.get("question") or "")[0])) and m[1] not in done for q in asked):
            continue
        try:
            state_doc, plan_doc = Document(state), Document(plan)
            names = [number_of(name) if numbered(plan_doc) else name.split(". ", 1)[-1]
                     for name, body in requirements(plan_doc) if not dropped(name, body)]
            check_complete(state_doc, plan_doc, names)
        except ValueError as error:
            return REASON_UNFINISHED.format(status=path.relative_to(root).as_posix(),
                                            spec=sibling(path, "input").relative_to(root).as_posix(), reason=error)
    return None


# 모든 요구사항 검증이 PASS이고 남은 문제가 없는데 완료 전이 검사가 거부하면 그 사유를 반환한다. 아니면 None이다.
def complete_refusal(path: Path) -> str | None:
    # 구현 승인이 있고 아직 완료가 아닌 새 양식 명세 문서만 본다.
    text, plan = read(path), read(sibling(path, "input"))
    if not plan.strip() or not REQUEST_APPROVED(text) or re.search(r"^- 작업 상태: 완료\s*$", text, re.M):
        return None

    # 요구사항 검증이 모두 PASS이고 남은 문제가 없는지 확인한다.
    try:
        state_doc, plan_doc = Document(text), Document(plan)
        if not numbered(plan_doc):
            return None
        check_complete(state_doc, plan_doc, [number_of(n) for n, b in requirements(plan_doc) if not dropped(n, b)])
    except ValueError:
        return None

    # 그런데도 완료 전이가 거부되면 사유를 반환한다.
    as_state = lambda state: STATE_LINE.sub(f"- 작업 상태: {state}", text, count=1)
    return transition_reason(path, as_state("완료"), as_state("구현"), plan)


# 정식 완료 승인 요청과 실제 완료 접두어는 기존 완료 검사에 통과한 문서만 요청할 수 있다.
def completion_reason(data: dict, runner: str, message: str, user: str, entries: list[dict]) -> str | None:
    form = form_of(body_of(message))
    if form == "request-stop" or (form != "approval-complete" and stage_prefix(user) != "완료"):
        return None
    root = project_root(data.get("cwd"))
    paths = linked_statuses(root, message, entries) or linked_statuses(root, user, entries)
    if not paths:
        written = written_status(data, runner)
        paths = [written] if written else open_tasks(root)
    if not paths:
        return REASON_COMPLETION.format(path="기록 없음", reason="완료 검사할 경과 문서와 명세 문서가 없습니다")
    for path in paths:
        try:
            state, plan = Document(read(path)), Document(read(sibling(path, "input")))
            names = [number_of(name) if numbered(plan) else name.split(". ", 1)[-1]
                     for name, body in requirements(plan) if not dropped(name, body)]
            check_complete(state, plan, names)
        except ValueError as error:
            return REASON_COMPLETION.format(path=path.relative_to(root).as_posix(), reason=error)
    return None


REASON_UNFINISHED = ("승인된 작업이 남아 있습니다. 응답을 종료하지 않고 연결된 작업을 계속하십시오. "
                     "작업 위치: {status} > 검증 결과, {spec} > 재구현 요청 및 재검증 요청. 사유: {reason}")
WORK_REQUEST = re.compile(r"승인\s*[.!~]*$|고쳐|고치|마저|끝내|완료해|"
                          r"(?m:^\s*계속[.!~]*\s*$)|"
                          r"계속(?:\s*(?:진행|작업|구현|수정|검증|실행))?\s*"
                          r"(?:해(?:\s*(?:주세요|주십시오|줘|달라|라))?|하(?:세요|십시오|라|자))(?=\s*(?:[.!?~]|\n|$))|"
                          r"(?:구현|수정|검증|재구현|재검증|처리|작업|실행)(?:해|하|(?:을|를)\s*(?:해|하|진행|실행))")
TOOL_DENIED = re.compile(r"permission denied|access (?:is )?denied|not permitted|command blocked|"
                         r"권한[^\n]*(?:없|부족|거부|오류)|승인[^\n]*(?:필요|차단)", re.I)


REASON_COMPLETION = "완료 선행 검사에 실패했습니다. 작업 위치: {path}. 사유: {reason}. 기존 검증 기록을 보완하고 이미 받은 완료 접두어는 다시 요구하지 마십시오."


STATE_REF = re.compile(rf"{TASK_REL}/([\w.\-]+)/(?:status|spec|research|refute)\.md")


STOP_REQUEST = re.compile(r"(?:중단|종료|취소)(?:해|하|합|요|\s*$)|멈춰|그만(?:해|하|\s*$)|^stop\b", re.I)
