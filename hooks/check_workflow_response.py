"""Stop hook: 응답의 결과물, 출처, 작업 흐름 위반과 책임 회피 표현을 검사하고 응답을 다시 쓰게 한다.

호출자:
- Claude Code와 Codex의 Stop hook 설정(shared/settings)
- 호출 명령은 `python check_workflow_response.py --runner <claude|codex>`이고, PostToolUse hook은 `--record`를 더한다.
입력: hook 입력 JSON(last_assistant_message, stop_hook_active 등)을 stdin으로 받는다.
반환:
- 위반이면 stdout에 `{"decision": "block", "reason": ..., "systemMessage": ...}` JSON을 출력하고 통과하면 아무것도 출력하지 않는다.
- `--record` 모드는 질문 도구 답과 작업 상태를 경과 문서에 기록하고 출력하지 않는다.
- check()는 block 사유 문자열 또는 None을 반환한다.
- director 메인 세션이 세션 기록에 nestlab-kickoff 호출 없이 응답을 끝내면 한 번 block한다.
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

from check_invalid_response import (MAX_BLOCKS, admissions, context, hit_count, incident_link,
                             incident_path, is_child, plain, record_hits, transcript_of)
from sub_claim import PATH, REASON_SOURCE, URL, unsourced_claims
from sub_approval import APPROVED
from sub_docs import FENCE_LINE, HEADING, decision_path, doc, measure_pending, project_root, review_done, section, sibling, strip_task_comments, template_titles, titles
from sub_judgment import ASK, DONE, confirmed, open_tasks, research_pending, review_items, status_of, unanswered
from sub_question import DROP, RECOMMENDED, REQUIREMENT, REVIEW, TABLE_OR_HEADING, bare_label, split_tag, tasks_of
from sub_role import resolve_role
from sub_scope import PROBLEM_QUESTION, STATE_LINE
from sub_spec import apply as apply_spec, cells as spec_cells
from sub_session import QUESTION_TOOLS, question_answers, read_session, response_answers, turn
from sub_workflow import (R_NUM_MISMATCH, Document, check_complete, check_numbers, dropped, filled, number_of, numbered,
                          reported, requirements, transition_reason, values)
from sub_write import PATCH_FILE, read, strings

MAX_LEN = 300
FENCE = re.compile(r"^\s*(```|~~~)")
TABLE = re.compile(r"^\s*\|.*\|\s*$", re.M)
LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S", re.M)
DIFF = re.compile(r"^(?:[+-]{3} |@@ )", re.M)
# 예고: 미래형 약속과 미실행 선언
ANNOUNCE = re.compile(
    r"겠습니다|하겠어요|할게요|드릴게요|예정입니다|(?:아직|따로|별도로)[^.\n]{0,20}(?:변경|수정|실행|적용)하지 않았")
# 인정과 자기 평가
ADMIT = re.compile(
    r"맞습니다[!.]?|죄송|제가 잘못|제 잘못|제 실수|해결하는 대신|못 했|못했")
# 위치 이동을 뜻하는 `앞으로 <이동 동사>`는 다짐이 아니므로 책임 회피에서 뺀다.
MOVE = r"(?!\s*(?:옮|당기|당겨|두|둔|배치|이동))"
# 책임 회피: 후속 작업을 사용자에게 넘기거나 해결을 포기하는 표현
SHIFT = re.compile(
    r"하실 (?:일은 없|필요는 없)|추가로 [^.\n]*?은 없"
    r"|해결됐다고 할 수 없|멈추겠습니다|멈출게요"
    r"|앞으로" + MOVE + r"|앞으로는|다음(?: \S+)?부터는? [^.\n]*?(?:하겠습니다|할게요)|이미 충분")
APPROVAL_BLOCK = re.compile(r"^#{1,6} (?:작업 승인 필요|승인 요청)\s*$", re.M)
CHOICE_BLOCK = re.compile(r"^#{1,6} (?:사용자 )?(?:선택|판단)(?: 요청| 필요)\s*$", re.M)
# 둘째 갈래는 임시 경로(log/state)이다. 기존 로그 삭제 뒤 제거한다.
STATE_REF = re.compile(r"log/task/([\w.\-]+)/(?:status|spec|research|refute)\.md|log/state/([\w.\-]+?)(?:-input|-research)?\.md")
QUESTION_END = re.compile(r"[?？]\s*\**\s*$")
UNRESOLVED = "중단 판정 미해결"


# 코드 블록과 인용 줄을 뺀 본문을 반환한다.
def body_of(message: str) -> str:
    out, fence = [], False
    for line in message.splitlines():
        if FENCE.match(line):
            fence = not fence
        elif not fence and not line.lstrip().startswith(">"):
            out.append(line)
    return "\n".join(out)


# 코드 블록, 표, 목록, diff, 경로, 링크 중 하나라도 있으면 True이다.
def has_deliverable(message: str, body: str) -> bool:
    if any(FENCE.match(line) for line in message.splitlines()):
        return True
    return any(p.search(body) for p in (TABLE, LIST, DIFF, PATH, URL))


REASON_PROPOSAL = ("조사 문서의 최종 조사 원문이 비어 있는데 조사 도구 호출 없이 해결안을 제안했습니다.\n"
                   "조사 단계로 돌아가 저장소와 웹을 조사한 뒤 결과를 보고하십시오.")
# 해결안 표지: 추천 문장. 안 비교 표나 heading은 TABLE_OR_HEADING이 판정한다.
PROPOSAL = re.compile(r"추천(?:은|합니다|드립니다| 안)")
RESEARCH_TOOLS = {"Read", "Grep", "Glob", "WebSearch", "WebFetch", "Agent"}


# 이번 턴에 조사 도구를 호출했으면 True이다.
def researched(data: dict, runner: str) -> bool:
    entries = turn(read_session(transcript_of(data, runner)))[2]
    return any(name in RESEARCH_TOOLS for e in entries for name, _ in e["calls"])


REASON_APPROVAL_DOC ="승인 요청에 작업의 경과 문서 경로(log/task/<작업-id>/status.md)를 링크로 작성하십시오."
REASON_APPROVAL_PENDING = ("경과 문서 '{doc}'의 검토 필요 또는 사전 실측 필요에 끝나지 않은 항목이 있어 승인을 요청할 수 없습니다. 해당 항목은 '{items}'입니다.\n"
                           "모든 안의 사용자 검토가 끝난 후 승인 요청을 진행하십시오.")
REASON_APPROVAL_TRANSITION = "경과 문서 '{doc}'에 실행 승인을 기록하면 차단됩니다. 승인 요청 전에 수정하십시오. 사유: {reason}"
REASON_CHOICE ="판단 지점 밖의 선택 요청입니다. 아직 답을 받지 않은 조사 문서 검토 보고(`### 검토 <번호>. <이름>`) 이름을 본문에 쓰십시오."
REASON_QUESTION = ("응답을 질문 문장으로 끝냈습니다. 지침이나 요구로 정해지는 사안이면 근거를 적고 진행하십시오.\n"
                   "사용자 판단이 필요하면 조사 문서 검토 보고를 쓰고 질문 도구로 검토 <번호>. 질문을 하십시오.")

REASON_APPROVAL_FORM = "승인 요청 서식의 필수 절과 구체적인 본문을 채워 응답을 다시 작성하십시오."
REASON_COMPLETE = "경과 문서 '{doc}'의 모든 요구사항 검증이 PASS이고 남은 문제가 없는데 완료로 바뀌지 않습니다. 사유: {reason}"


# 인라인 코드에 쓴 실제 승인 입력은 읽고, 코드 블록과 인용 및 예시 줄은 제외한다.
def requests_approval(message: str) -> bool:
    text = re.sub(r"(?<!`)`([^`\n]+)`(?!`)", r"\1", body_of(message))
    for line in plain(text).splitlines():
        if re.match(r"^\s*(?:[-*]\s*)?(?:e\.g\.|예시\s*[:：]|예\s*[:：])", line):
            continue
        if re.search(r"승인(?:으로|을|이라고|이라고만)?\s*(?:입력(?:을)?\s*)?(?:답|응답|회신|보내|전송|입력)", line) and re.search(
                r"(?:주십|주세요|주세|달라|바랍|하십시오|하시면|하면|보내면|보내시면|입력하면|입력하시면)", line):
            return True
    return False


# 승인 스킬 양식의 필수 절 제목이나 구체적인 본문이 빠진 경우 누락 목록을 반환한다.
def approval_form_gap(body: str) -> list[str]:
    missing = [] if APPROVAL_BLOCK.search(body) else ["승인 요청"]
    for title in template_titles("nestlab-approval"):
        content = re.sub(r"<!--.*?-->", "", section(body, title), flags=re.S).strip()
        if title != "승인 요청" and (title not in titles(body) or not filled(content)):
            missing.append(title)
    return missing


REASON_UNFINISHED = ("승인된 작업이 남아 있습니다. 응답을 끝내지 말고 연결된 작업을 계속하십시오. "
                     "작업 위치: {status} > 검증 결과, {spec} > 재작업 요청 및 재검증 요청. 사유: {reason}")
WORK_REQUEST = re.compile(r"승인\s*[.!~]*$|고쳐|고치|마저|끝내|완료해|"
                          r"(?m:^\s*계속[.!~]*\s*$)|"
                          r"계속(?:\s*(?:진행|작업|구현|수정|검증|실행))?\s*"
                          r"(?:해(?:\s*(?:주세요|주십시오|줘|달라|라))?|하(?:세요|십시오|라|자))(?=\s*(?:[.!?~]|\n|$))|"
                          r"(?:구현|수정|검증|재작업|재검증|처리|작업|실행)(?:해|하|(?:을|를)\s*(?:해|하|진행|실행))")
STOP_REQUEST = re.compile(r"(?:중단|종료|취소)(?:해|하|합|요|\s*$)|멈춰|그만(?:해|하|\s*$)|^stop\b", re.I)
TOOL_DENIED = re.compile(r"permission denied|access (?:is )?denied|not permitted|command blocked|"
                         r"권한[^\n]*(?:없|부족|거부|오류)|승인[^\n]*(?:필요|차단)", re.I)


# 현재 요청의 작업 문서를 우선하고, 없으면 현재 턴 도구 입력이 가리킨 경과 문서를 반환한다.
def linked_statuses(root: Path, user: str, entries: list[dict]) -> list[Path]:
    refs = STATE_REF.findall(user)
    if not refs:
        refs = [(task, "") for task in re.findall(r"\b\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*\b", user)]
    if not refs:
        refs = [ref for entry in entries for _, args in entry["calls"]
                for value in strings(args) for ref in STATE_REF.findall(value.replace("\\", "/"))]
    return [doc(a or b, "state", root) for a, b in dict.fromkeys(refs)]


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
            stopped.update(set(paths) & {doc(a or b, "state", root) for a, b in STATE_REF.findall(text)})
        else:
            targets = tasks_of(data, runner)
            if len(targets) == 1:
                stopped.update(set(paths) & set(targets))
    return stopped


REASON_PROBLEM_WAIT = ("문제 확인 질문의 실제 답이 비었거나 실패했습니다. 작업 위치: {path}. "
                       "미확정인 내용과 답할 방법을 응답에 명시하고 사용자 답을 기다리십시오. 확인할 질문: {question}")
# 답변 행동에 붙은 요청과 입력 방법을 검사한다. 답이 비었다는 설명이나 기다린다는 선언과 구분한다.
PROBLEM_REPLY_GUIDANCE = re.compile(
    r"(?:답(?:변)?|응답|선택|입력)(?:을|를)?\s*"
    r"(?:(?:해|보내|적어|써)\s*(?:주(?:세요|십시오|십쇼)|주시면\s*(?:됩|돼|됨)|줘|달라)|주(?:세요|십시오|십쇼)"
    r"|하(?:세요|십시오)|하(?:면|시면)\s*(?:됩|돼|됨)|할\s*수\s*있|해도\s*(?:됩|돼|됨))"
    r"|(?:맞는지|동의 여부|수정할 내용)(?:를|을)?\s*알려\s*주(?:세요|십시오|십쇼)"
    r"|선택지(?:에서|\s*중(?:에서)?)\s*[^.!?\n]{0,40}골라\s*주(?:세요|십시오|십쇼)")


# 마지막 문제 확인 질문의 위반 사유와 구체적인 안내가 있어 사용자 답을 기다리는 작업을 반환한다.
def problem_wait_reason(data: dict, runner: str, message: str, user: str, entries: list[dict]) -> tuple[str | None, set[Path | str]]:
    root = project_root(data.get("cwd"))
    paths = linked_statuses(root, user, entries)
    stopped = stopped_statuses(data, runner, entries, paths)
    waiting = set()
    latest = {}
    for entry in entries:
        for (name, args), call_id in zip(entry["calls"], entry.get("ids") or []):
            if name not in QUESTION_TOOLS:
                continue
            for question in (args or {}).get("questions", []):
                if not isinstance(question, dict):
                    continue
                text = " ".join(str(question.get("question") or "").split())
                match = PROBLEM_QUESTION.fullmatch(text)
                if match:
                    for path in paths:
                        if path.parent.name[4:15] == match[1] and path not in stopped:
                            latest[path] = (question, {"calls": [(name, args)], "ids": [call_id]}, call_id)
                    # 문서 생성 전에는 현재 턴의 질문 태그를 사용한다. 다른 작업의 문서로 연결하지 않는다.
                    if not any(p.parent.name[4:15] == match[1]
                                             for p in (root / "log/task").glob("*/status.md")):
                        latest[f"작업 {match[1]} (경과 문서 생성 전)"] = (question, {"calls": [(name, args)], "ids": [call_id]}, call_id)
    for path, (question, call, call_id) in latest.items():
        replies = [entry for entry in entries if call_id in (entry.get("replies") or {})]
        actual = False
        for asked, answer in question_answers([call] + replies):
            if asked != question:
                continue
            actual = bool(picked_labels(asked, answer))
            for entry in replies:
                response = entry["replies"][call_id]
                if question.get("question") in response:
                    actual = True
                try:
                    output = json.loads(response.get("") or "{}")
                    actual |= isinstance(output, dict) and isinstance(output.get("answers"), dict) and not (output.get("error") or output.get("isError"))
                except (ValueError, TypeError):
                    pass
        if actual:
            continue
        body = plain(body_of(message))
        if (re.search(r"문제 정의|범위|초안|질문|선택지|수정할 내용", body)
                and PROBLEM_REPLY_GUIDANCE.search(body)):
            waiting.add(path)
            continue
        return REASON_PROBLEM_WAIT.format(path=path.relative_to(root).as_posix() if isinstance(path, Path) else path,
                                         question=question.get("question")), waiting
    return None, waiting


# 현재 요청과 도구 입력이 연결한 승인 작업의 완료 조건을 검사한다. 일반 질문과 실제 사용자 대기는 허용한다.
def unfinished_reason(data: dict, message: str, runner: str, waiting: set[Path | str] | None = None) -> str | None:
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
            and re.search(r"사용자(?:가|의|\s*(?:조작|작업|승인|설정|입력))|폴리님", message)):
        return None
    for path in paths:
        if path in stopped or path in (waiting or ()):
            continue
        state, plan = read(path), read(sibling(path, "input"))
        if not REQUEST_APPROVED(state) or not plan.strip():
            continue
        # 실제 질문 도구가 아직 확정되지 않은 검토 항목을 요청했으면 사용자 답을 기다린다.
        ready = review_done()
        pending = [name for name, item in review_items(read(decision_path(path))).items() if status_of(item) not in ready]
        pending += unanswered(path, ready)
        asked = [q for entry in entries for name, args in entry["calls"] if name in QUESTION_TOOLS
                 for q in (args or {}).get("questions", []) if isinstance(q, dict)]
        if any((m := REVIEW.match(split_tag(q.get("question") or "")[0])) and
               any(re.match(rf"검토 {m[1]}[. ]", name) for name in pending) for q in asked):
            continue
        expected = spec_cells(section(plan, "요구사항 목록"))
        if any((m := REQUIREMENT.match(split_tag(q.get("question") or "")[0])) and
               expected.get(int(m[1])) != f"- {m[3]}" for q in asked):
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
    # 실행 승인이 있고 아직 완료가 아닌 새 양식 명세 문서만 본다.
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
    return transition_reason(path, as_state("완료"), as_state("진행"), plan)


# (작업 흐름 위반 사유, 관련 경과 문서)이다. log 폴더가 없는 저장소는 검사하지 않는다.
def workflow_reason(data: dict, message: str, body: str, runner: str) -> tuple[str | None, list[Path]]:
    requesting = APPROVAL_BLOCK.search(body) or requests_approval(message)
    if requesting and (missing := approval_form_gap(body)):
        return REASON_APPROVAL_FORM + " 누락 절: " + ", ".join(missing), []
    # 작업 문서 폴더가 없으면 검사하지 않는다.
    root = project_root(data.get("cwd"))
    if not (root / "log").is_dir():
        return None, []

    # 응답이 가리킨 경과 문서를 찾고, 없으면 진행 중인 작업을 쓴다.
    refs = [doc(a or b, "state", root) for a, b in dict.fromkeys(STATE_REF.findall(message))]
    docs = [p for p in refs if p.is_file()] or open_tasks(root)

    # 선택 요청 블록은 사용자 검토 필요 항목과 연결돼야 한다.
    if CHOICE_BLOCK.search(body):
        try:
            ready = review_done()
        except ValueError as error:
            return str(error), docs
        if not any(title in body for p in docs for title in unanswered(p, ready)):
            return REASON_CHOICE, docs

    # 승인 요청 블록은 director의 상태 문서 경로를 요구하고 참조 문서의 검토 필요 항목을 검사한다.
    if APPROVAL_BLOCK.search(body):
        if not refs and resolve_role(data) == "director":
            return REASON_APPROVAL_DOC, docs
        try:
            ready = review_done()
        except ValueError as error:
            return str(error), docs
        for path in refs:
            text = read(path)
            pending = ([name for name, item in review_items(read(decision_path(path))).items() if status_of(item) not in ready]
                       + unanswered(path, ready) + measure_pending(text))
            if pending:
                return REASON_APPROVAL_PENDING.format(doc=path.name, items=", ".join(pending[:3])), [path]

            # 같은 작업 폴더의 명세 문서 요구사항 번호가 요구사항 목록 번호와 다르면 막는다. 명세 문서가 없거나 읽을 수 없으면 건너뛴다.
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

                # 실행 승인을 받은 것으로 가정한 전이 검사가 막히면 승인 요청 시점에 재작성하게 한다.
                assumed = splice(text, "실행 승인 범위", lambda body: re.sub(
                    r"(?m)^(\s*-\s*(?:사용자 )?승인 상태:\s*).*$", r"\1승인", body))
                reason = transition_reason(path, assumed, text, read(spec))
                if reason:
                    return REASON_APPROVAL_TRANSITION.format(doc=path.name, reason=reason), [path]

    # 완료 전이가 거부되는 경과 문서는 거부 사유를 안내한다.
    for path in docs:
        reason = complete_refusal(path)
        if reason:
            return REASON_COMPLETE.format(doc=path.name, reason=reason), [path]

    # 조사 원문이 빈 작업에서 조사 도구 호출 없이 해결안을 제안한 director 응답은 조사 단계로 되돌린다.
    if (resolve_role(data) == "director" and research_pending(docs) and PROPOSAL.search(body)
            and TABLE_OR_HEADING.search(body) and not researched(data, runner)):
        return REASON_PROPOSAL, docs

    # Claude 응답이 질문 문장으로 끝나면 질문 도구를 쓰게 한다.
    lines = [line for line in body.splitlines() if line.strip()]
    if runner == "claude" and lines and QUESTION_END.search(lines[-1]):
        return REASON_QUESTION, docs
    return None, docs


# text에서 title로 시작하는 제목 절의 본문(다음 같은 수준 이상 제목 전까지)에 edit을 적용한 전문을 반환한다. 절이 없으면 text 그대로이다.
def splice(text: str, title: str, edit) -> str:
    # 제목 줄을 찾아 본문의 시작과 끝 위치를 구한다. 코드 블록 안의 줄은 제목으로 보지 않는다.
    start = end = None
    level, fence, pos = 0, "", 0
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        f = FENCE_LINE.match(bare)
        if f and not fence:
            fence = f.group(1)
        elif f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence):
            fence = ""
        m = None if fence or f else HEADING.match(bare)
        if m and start is not None and len(m.group(1)) <= level:
            end = pos
            break
        if m and start is None and m.group(2).startswith(title):
            level, start = len(m.group(1)), pos + len(line)
        pos += len(line)
    if start is None:
        return text

    # 본문만 바꾸고 나머지는 그대로 둔다.
    end = len(text) if end is None else end
    return text[:start] + edit(text[start:end]) + text[end:]


# 파일 본문에 edit(전문 -> 전문)을 적용해 쓴다. 줄바꿈 형식과 인코딩은 원본을 유지하고 바뀐 내용이 없으면 쓰지 않는다.
def rewrite(path: Path, edit) -> None:
    raw = path.read_bytes().decode("utf-8")
    text = raw.replace("\r\n", "\n")
    new = edit(text)
    if new != text:
        path.write_bytes((new.replace("\n", "\r\n") if "\r\n" in raw else new).encode("utf-8"))


# 같은 위반이 반복되면 경과 문서 중단 사유 절 끝에 미해결 줄을 남긴다. 경과 문서가 하나로 정해질 때만 쓴다.
def record_unresolved(docs: list[Path], reason: str) -> None:
    # 기록할 작업 문서가 하나로 정해지지 않으면 기록하지 않는다.
    if len(docs) != 1:
        return

    # 중단 사유 절 끝에 미해결 줄을 덧붙인다. 같은 줄이 있으면 덧붙이지 않는다.
    line = f"- {UNRESOLVED}: {' '.join(reason.split())}"
    tail = lambda body: body.rstrip("\n") + "\n" + line + ("\n\n" if body.endswith("\n\n") else "\n")
    rewrite(docs[0], lambda text: splice(text, "중단 사유", lambda body: body if line in body.splitlines() else tail(body)))


REVIEW_KEY = re.compile(r"(검토 \d+)(?=[. ])")
ANCHOR = re.compile(r"안 (\d+)\. (.+)$")
STATUS_PATH = re.compile(r"log/task/[\w.\-]+/(?:status|spec)\.md")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "apply_patch", "exec")
TIME_LINE = re.compile(r"^(  - 사용자 검토 시각: ).*$", re.M)
APPLY_CALL = re.compile(r"tools\.apply_patch\(")


# Codex exec 입력 안 `tools.apply_patch("...")` 호출의 패치 본문 목록이다. 디코드에 실패한 호출은 건너뛴다.
# ponytail: 큰따옴표 문자열만 읽는다. 템플릿 리터럴과 작은따옴표 인자는 놓치므로, 관측되면 디코더를 추가한다.
def exec_patches(source: str) -> list[str]:
    out = []
    for m in APPLY_CALL.finditer(source):
        try:
            value = json.JSONDecoder().raw_decode(source, m.end())[0]
        except ValueError:
            continue
        if isinstance(value, str):
            out.append(value)
    return out


# 쓰기 도구 호출 한 건이 쓴 파일 경로 목록이다. 쓰기 도구가 아니면 빈 목록이다.
def call_paths(name: str, args) -> list[str]:
    if name not in WRITE_TOOLS:
        return []
    patches = ([t for s in strings(args) for t in exec_patches(s)] if name == "exec"
               else list(strings(args)) if name == "apply_patch" else None)
    return ([a or b for s in patches for a, b in PATCH_FILE.findall(s)] if patches is not None
            else [str((args or {}).get("file_path") or "")])


# 세션 기록에서 에이전트가 마지막으로 쓴 경과 문서 경로를 반환한다. PostToolUse 입력이면 지금 호출도 포함한다. 없으면 None이다.
def written_status(data: dict, runner: str) -> Path | None:
    calls = [c for entry in read_session(transcript_of(data, runner)) for c in entry["calls"]]
    if data.get("tool_name"):
        calls.append((data["tool_name"], data.get("tool_input")))
    last = None
    for name, args in calls:
        for p in call_paths(name, args):
            m = STATUS_PATH.search(p.replace("\\", "/"))
            last = m.group(0) if m else last
    path = sibling(project_root(data.get("cwd")) / last, "state") if last else None
    return path if path and path.is_file() else None


# 요구사항 목록 절 본문에 line들을 번호 순서로 넣는다. 같은 번호는 바꾸고 양식 자리표시 줄은 지운다.
def put_requirements(body: str, lines: list[str]) -> str:
    items = {int(m.group(1)): m.group(0) for m in re.finditer(r"(?m)^(?:요구사항 )?(\d+)\. (?!<).*$", body)}
    items.update({int(re.search(r"\d+", l).group(0)): l for l in lines})
    first = re.search(r"(?m)^(?:요구사항 )?\d+\. ", body)
    head = body[:first.start()] if first else "\n"
    return head + "\n".join(items[k] for k in sorted(items)) + ("\n\n" if body.endswith("\n\n") else "\n")


# 검토 필요 항목의 구분 키이다. `검토 <번호>.` 항목은 번호로, 그 밖의 항목은 이름 전체로 구분한다.
def review_key(name: str) -> str:
    m = REVIEW_KEY.match(name)
    return m[1] if m else name


# 검토 필요 절 본문에 (키, 항목 본문) 항목들을 넣는다. 같은 키는 바꾸고 새 키는 끝에 덧붙이며 양식 자리표시 항목은 지운다.
def put_reviews(body: str, blocks: dict[str, str]) -> str:
    starts = list(re.finditer(r"(?m)^- (.+)$", body))
    items = {review_key(m.group(1).strip()): body[m.start():(starts[i + 1].start() if i + 1 < len(starts) else len(body))].strip("\n")
             for i, m in enumerate(starts) if not m.group(1).startswith("<")}
    for name, block in blocks.items():
        # 시각을 뺀 내용이 같으면 기존 항목(시각 포함)을 둔다.
        if not (name in items and TIME_LINE.sub("", items[name]) == TIME_LINE.sub("", block)):
            items[name] = block
    head = body[:starts[0].start()] if starts else body.rstrip("\n") + "\n\n"
    return head + "\n".join(items.values()) + ("\n\n" if body.endswith("\n\n") else "\n")


REQUEST_APPROVED = lambda text: any(APPROVED.match(l) for l in section(text, "실행 승인 범위").splitlines())


# 경과 문서 내용에서 작업 상태를 계산한다. 중단 유형 기록이면 중단, 실행 승인과 완료 검사 통과면 완료, 실행 승인이면 진행, 그 밖에는 미개시이다.
def work_state(path: Path, text: str) -> str:
    # 중단 유형이 기록돼 있으면 중단이다.
    if any(filled(v) for v in values(section(text, "중단 사유"), "중단 유형")):
        return "중단"
    if not REQUEST_APPROVED(text):
        return "미개시"

    # 완료 전이 검사를 통과하면 완료이고, 아니면 진행이다.
    plan = read(sibling(path, "input"))
    as_state = lambda state: STATE_LINE.sub(f"- 작업 상태: {state}", text, count=1)
    return "완료" if plan.strip() and transition_reason(path, as_state("완료"), as_state("진행"), plan) is None else "진행"


# 검토 보고 절에서 검토 number의 `안 <번호>. <안 이름>` 제목 중 label과 같은 이름의 (번호, 이름)이다. 없으면 None이다.
def anchor_of(research: str, number: int, label: str) -> tuple[str, str] | None:
    block = section(section(research, "검토 보고"), f"검토 {number}.")
    return next(((m[1], m[2].strip()) for t in titles(block) if (m := ANCHOR.match(t)) and m[2].strip() == bare_label(label)), None)


# 조사 문서에서 `### 검토 <번호>.` 보고를 `### 검토 <번호>. 폐기`로 바꾸고 본문을 지운다. 보고가 없으면 그대로 둔다.
def drop_report(text: str, number: int) -> str:
    text = splice(text, f"검토 {number}.", lambda body: "\n")
    return re.sub(rf"(?m)^(#{{1,6}}) 검토 {number}\..*$", rf"\1 검토 {number}. 폐기", text, count=1)


# 답이 선택지 라벨과 정확히 같을 때만 고른 라벨 목록을 반환한다. 여러 개를 고르면 ", "로 이은 라벨이다.
# 자유 입력, Codex CLI `user_note:` 메모가 든 답은 수정 입력이므로 빈 목록이다.
def picked_labels(question: dict, answer: str) -> list[str]:
    labels = {RECOMMENDED.sub("", " ".join(str(o["label"]).split())): o for o in question.get("options") or []
              if isinstance(o, dict) and o.get("label")}
    answer = RECOMMENDED.sub("", answer.strip())
    parts = [answer] if answer in labels else [RECOMMENDED.sub("", p.strip()) for p in answer.split(", ")]
    return parts if parts and all(p in labels for p in parts) else []


# 이번 턴 질문 도구 답을 경과 문서에 기록하고 작업 상태 줄을 다시 계산한다. answers가 있으면 그 답을 쓴다.
# 질문 문장 끝 작업 식별자의 경과 문서에 기록하고, 식별자가 없으면 진행 중인 작업이 하나일 때 그 작업, 여럿이면 마지막으로 쓴 경과 문서에 기록한다.
def record_answers(data: dict, runner: str, answers: list | None = None) -> None:
    # 이번 턴 질문 답을 읽고 식별자 꼬리를 뗀 질문과 대상 경과 문서로 묶는다.
    if answers is None:
        answers = question_answers(turn(read_session(transcript_of(data, runner)))[2])
    groups: dict[Path, list] = {}
    for q, answer in answers:
        # 문제 확인 답은 transcript에서 직접 대조한다. 문서 생성 전 태그를 기존 작업에 기록하지 않는다.
        if PROBLEM_QUESTION.fullmatch(" ".join(str(q.get("question") or "").split())):
            continue
        text, tag = split_tag(q.get("question") or "")
        target = (tasks_of(data, runner, tag) or [None])[0]
        if target is not None:
            groups.setdefault(target, []).append(({**q, "question": text}, answer))
    if not answers and (path := written_status(data, runner)) is not None:
        groups.setdefault(path, [])
    for path, items in groups.items():
        record_to(path, items)


# 형식을 지킨 요구사항, 폐기, 검토 질문의 선택지 답만 path 경과 문서에 기록하고 직접 입력 답과 번호 접두가 없는 질문은 기록하지 않는다.
def record_to(path: Path, answers: list) -> None:
    # 질문마다 요구사항 줄, 폐기 줄, 명세 문서 기록 작업 또는 검토 필요 항목을 만든다.
    now = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    requirements, drops, ops, names, reviews, review_drops = [], [], [], {}, {}, []
    flat = lambda text: " ".join(str(text).split())
    review = lambda name, state, label, where: (f"- {name}\n  - 상태: {state}\n  - 사용자 검토 시각: {now}\n"
                                                f"  - 확정: {label}\n  - 근거 위치: {where}")
    records = decision_path(path)
    consolidated = records != path
    report = decision_path(path, "research")
    research = read(report)
    for q, answer in answers:
        # 선택지 답이 아니면 기록하지 않는다.
        question, ok = flat(q.get("question") or ""), confirmed(answer)
        picked = picked_labels(q, answer)
        if not picked:
            continue
        req, drop, rev = REQUIREMENT.match(question), DROP.match(question), REVIEW.match(question)

        # 검토 질문은 고른 안 이름을 확정에, 조사 문서 검토 보고의 안 제목을 근거 위치에 쓴다.
        if rev and picked == ["폐기"]:
            review_drops.append(int(rev[1]))
            reviews[f"검토 {rev[1]}"] = f"- 검토 {rev[1]}. 폐기\n  - 상태: 폐기"
        elif rev and picked == ["재조사"]:
            continue
        elif rev:
            number = int(rev[1])
            title = reported(research).get(number) or f"검토 {number}. {rev[2]}"
            anchor = anchor_of(research, number, picked[0])
            location = "명세 문서 > 검토 > " if consolidated else "조사 문서 > "
            where = f"{location}검토 보고 > {title} > " + (f"안 {anchor[0]}. {anchor[1]}" if anchor else picked[0])
            reviews[f"검토 {number}"] = review(question, DONE, ", ".join(picked), where)

        # 요구사항과 폐기 질문은 확정 답일 때만 기록한다.
        elif ok and req:
            requirements.append(question[len("요구사항 "):])
            names[int(req[1])] = req[2]
            ops.append(("req", int(req[1]), req[3]))
        elif ok and drop and drop[1] == "요구사항":
            drops.append(int(drop[2]))
            ops.append(("drop", int(drop[2]), drop[3]))
        elif ok and drop:
            review_drops.append(int(drop[2]))
            reviews[f"검토 {drop[2]}"] = f"- 검토 {drop[2]}. 폐기\n  - 상태: 폐기"

    # 폐기한 검토는 조사 문서 보고를 `### 검토 <번호>. 폐기`로 바꾼다.
    for number in review_drops:
        try:
            rewrite(report, lambda text: drop_report(text, number))
        except (OSError, ValueError):
            pass

    # 같은 작업 폴더의 명세 문서가 있으면 요구사항 문장을 기대 결과 칸에 쓴다.
    spec = path.with_name("spec.md")
    spec_changed = False
    if ops and spec.is_file():
        names = {**{int(m[1]): m[2] for m in re.finditer(r"(?m)^(\d+)\. ([^:\n]+):", section(read(path), "요구사항 목록"))}, **names}
        try:
            before = read(spec)
            rewrite(spec, lambda text: splice(text, "요구사항 목록", lambda body: apply_spec(body, ops, names)))
            spec_changed = read(spec) != before
        except (OSError, ValueError):
            pass

    if consolidated and reviews:
        rewrite(records, lambda text: splice(text, "검토 필요", lambda body: put_reviews(body, reviews)))

    # 기존 형식의 확정 기록과 실행 승인 및 작업 상태를 갱신한다.
    def edit(text: str) -> str:
        # 폐기 줄은 `요구사항 <번호>. 폐기`만 쓴다.
        lines = requirements + [f"요구사항 {n}. 폐기" for n in drops]
        if lines and not consolidated:
            before = text
            text = splice(text, "요구사항 목록", lambda body: put_requirements(body, lines))
            # 요구사항 목록이 바뀌었으면 실행 승인을 승인 대기로 되돌린다.
            if text != before:
                text = splice(text, "실행 승인 범위", lambda body: re.sub(
                    r"(?m)^(\s*-\s*(?:사용자 )?승인 상태:\s*)승인\s*$", r"\1승인 대기", body))
        if consolidated and spec_changed:
            text = splice(text, "실행 승인 범위", lambda body: re.sub(
                r"(?m)^(\s*-\s*(?:사용자 )?승인 상태:\s*)승인\s*$", r"\1승인 대기", body))
        if reviews and not consolidated:
            text = splice(text, "검토 필요", lambda body: put_reviews(body, reviews))
        return STATE_LINE.sub(lambda _: f"- 작업 상태: {work_state(path, text)}", text, count=1)
    rewrite(path, edit)


REASON_SHIFT = ("직전 응답에 책임을 회피하는 표현({hits})이 있습니다. 사건 분석은 별도 세션이 진행하므로 사건 문서를 작업하지 마십시오.\n"
                "사용자의 요청에 맞는 내용을 포함하여 재작성하십시오.")
WARNING = "직전 응답은 응답 검사에서 무효 처리됐습니다. 다시 작성합니다."


# 응답 미흡으로 판정할 책임 회피 표현을 찾는다. 서브에이전트 응답은 검사하지 않는다.
def shift_hits(data: dict, runner: str) -> list[str]:
    if data.get("agent_id") or is_child(data):
        return []
    message = data.get("last_assistant_message") or ""
    text = plain(message)
    if not SHIFT.search(text):
        return []
    user = context(transcript_of(data, runner), message, data.get("cwd"))[0]
    return admissions(text, user, SHIFT)


REASON_EMPTY = ("직전 응답에서 사용자 요청에 대한 결과물 없이 예고나 해명만 작성되었습니다.\n"
                "결과물(코드, 표, 목록, 파일 경로, diff)을 포함하여 다시 작성하십시오.")
# block 사유를 반환한다. 통과하면 None이다. record가 거짓이면 사건 기록과 분석 실행 없이 판정만 한다.
def check(data: dict, runner: str = "claude", record: bool = True) -> str | None:
    return inspect(data, runner, record)[0]


# (block 사유, 사용자 경고)를 반환한다. 경고는 책임 회피 감지 때만 있고 없으면 빈 문자열이다.
def inspect(data: dict, runner: str = "claude", record: bool = True) -> tuple[str | None, str]:
    # 서브에이전트 응답은 통과한다.
    if is_child(data):
        return None, ""
    message = data.get("last_assistant_message") or ""
    body = body_of(message)

    # 이번 턴 질문 도구 답을 경과 문서에 기록한다. 백그라운드 작업이 남은 응답도 기록하고, 기록 오류는 판정에 영향을 주지 않는다.
    if record:
        try:
            record_answers(data, runner)
        except Exception:
            pass
    if data.get("background_tasks"):
        return None, ""
    user, _, entries = turn(read_session(transcript_of(data, runner)))
    if STOP_REQUEST.search(user):
        return None, ""
    reason, waiting = problem_wait_reason(data, runner, message, user, entries)
    if reason:
        return reason, ""

    # 미완료 종료는 재작성 횟수와 관계없이 계속 검사한다.
    reason = unfinished_reason(data, message, runner, waiting)
    if reason:
        return reason, ""
    if stopped_statuses(data, runner, entries, linked_statuses(project_root(data.get("cwd")), user, entries)):
        return None, ""

    # 작업 흐름 위반은 한 번 block하고, 반복되면 미해결로 기록한 뒤 통과한다.
    reason, docs = workflow_reason(data, message, body, runner)
    if reason and data.get("stop_hook_active"):
        record_unresolved(docs, reason)
        return None, ""
    if reason:
        return reason, ""
    if waiting:
        return None, ""

    # 책임 회피 표현은 사건 문서에 기록하고 재작성하게 한다. 같은 사용자 메시지의 block이 한도에 닿으면 기록만 한다.
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
        return (None if count > MAX_BLOCKS else REASON_SHIFT.format(hits=", ".join(hits), link=link)), notice

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
def record_tool(data: dict, runner: str) -> None:
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
    finally:
        status = written_status(data, runner)
        if status:
            for kind in ("state", "input", "research", "refute"):
                strip_task_comments(sibling(status, kind))


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
            record_tool(json.load(sys.stdin), runner)
        except Exception:
            pass
        return 0

    # 검사 오류는 통과시키고, 위반은 block으로 출력한다.
    try:
        reason, notice = inspect(json.load(sys.stdin), runner)
    except Exception:
        return 0
    if reason:
        output = {"decision": "block", "reason": reason}
        if notice:
            output["systemMessage"] = notice
        elif runner == "codex":
            output["systemMessage"] = WARNING  # Codex는 응답 표시를 바꿀 수 없어 경고로 알린다.
        json.dump(output, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
