"""Stop hook: 응답의 결과물, 출처, 작업 흐름 위반과 책임 회피 표현을 검사하고 응답을 다시 쓰게 한다.

호출자:
- Claude Code와 Codex의 Stop hook 설정(설정 원본)
- 호출 커맨드는 `python check_workflow_response.py --runner <claude|codex>`이고, PostToolUse hook은 `--record`를 더한다.
입력: hook 입력 JSON(last_assistant_message, stop_hook_active 등)을 stdin으로 받는다.
반환:
- 위반이면 stdout에 `{"decision": "block", "reason": ..., "systemMessage": ...}` JSON을 출력하고 통과하면 아무것도 출력하지 않는다.
- Codex는 systemMessage를 대화 기록에 표시하지 않아, 사용자 안내(구현 승인 요청 본문, 사건 링크)를 block reason에 담는다.
- `--record` 모드는 질문 도구 답과 작업 단계를 경과 문서에 기록하고 출력하지 않는다.
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
from sub_approval import stage_prefix
from sub_claim import REASON_SOURCE, unsourced_claims
from sub_docs import doc, project_root, sibling, strip_task_comments
from sub_role import director_main, project_session
from sub_session import QUESTION_TOOLS, previous_stop_reason, read_session, response_answers, turn
from sub_gate import REQUEST_LINE, approval_request
from sub_completion import STATE_REF, STOP_REQUEST, complete_refusal, completion_reason, linked_statuses, stopped_statuses, unfinished_reason
from sub_form import APPROVAL_BLOCK, body_of, completed_response, form_gap, form_of, source_gaps
from sub_record import call_paths, call_status, record_answers, record_times, record_unresolved, splice


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
    if not project_session(data, runner):
        return None, ""
    message = data.get("last_assistant_message") or ""
    if limit_hit(message) and not data.get("stop_hook_active"):
        return REASON_LIMIT, ""
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
    # Claude는 MessageDisplay hook(replace_blocked_response)이 요청 줄을 본문으로 바꾸고, Codex는 본문을 block reason으로 표시한다.
    if APPROVAL_BLOCK.search(body):
        return REASON_REQUEST_FORM, ""
    request = REQUEST_LINE.search(body)
    if request:
        reason, output = approval_request(project_root(data.get("cwd")), request[1])
        return reason, ("" if reason or runner != "codex" else output + NOTICE_REQUEST)
    user, _, entries = turn(read_session(transcript_of(data, runner)))
    # 백그라운드 작업이 남은 응답은 완료 판정을 건너뛰지만 다짐과 책임 회피 같은 응답 미흡 표현은 판정한다.
    if data.get("background_tasks") and form_of(body) != "approval-complete" and stage_prefix(user) != "완료":
        return response_reason(data, runner, message, body, False, background=True)
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
    if reason:
        return reason, ""

    # 검토 제목, 추가 제안, 에이전트 제안이 든 응답은 웹 문서 링크가 없으면 한 번 block한다.
    if PROPOSAL.search(message) and not WEB_LINK.search(message) and not data.get("stop_hook_active"):
        return REASON_LINK, ""
    return response_reason(data, runner, message, body, record)


# 응답 미흡 표현과 출처 없는 주장의 (block 사유, 사용자 경고)를 반환한다. 통과하면 (None, "")이다.
# background이면 대기 안내 같은 짧은 예고와 출처 판정은 건너뛰고 다짐과 책임 회피 표현만 판정한다.
def response_reason(data: dict, runner: str, message: str, body: str, record: bool,
                    background: bool = False) -> tuple[str | None, str]:
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
        notice = f"사건 감지({', '.join(hits)}): {incident_link(path, data.get('cwd'))}"
        return (None if count > MAX_BLOCKS or data.get("stop_hook_active") else REASON_EMPTY), notice

    # 예고로 끝난 응답과 출처 없는 주장은 턴당 한 번만 block한다.
    if data.get("stop_hook_active") or background:
        return None, ""
    if ANNOUNCE.search(body):
        return REASON_EMPTY, ""
    claims = unsourced_claims(message, body)
    if claims:
        return REASON_SOURCE.format(sentences=" / ".join(claims[:3])), ""
    return None, ""


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

    # PostToolUse 기록 모드는 질문 답과 작업 단계를 기록하고 출력 없이 끝낸다.
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

# 예고: 미래형 약속, 당위형 종결과 미실행 선언
ANNOUNCE = re.compile(
    r"겠습니다|하겠어요|할게요|드릴게요|예정입니다|해야 합니다|해야 하는 상황입니다"
    r"|(?:아직|따로|별도로)[^.\n]{0,20}(?:변경|수정|실행|적용)하지 않았")
# 위치 이동을 뜻하는 `앞으로 <이동 동사>`는 다짐이 아니므로 책임 회피에서 뺀다.
MOVE = r"(?!\s*(?:옮|당기|당겨|두|둔|배치|이동))"
# 응답 미흡: 후속 작업을 사용자에게 넘기거나 해결을 포기하는 표현, 다짐 문장, 확인하지 않은 내용을 담은 문장
SHIFT = re.compile(
    r"하실 (?:일은 없|필요는 없|필요 없)|추가로 [^.\n]*?은 없"
    r"|해결됐다고 할 수 없|멈추겠|멈출게요|그만두겠|그만하겠|중단하겠|여기서 마치|이미 충분"
    r"|앞으로" + MOVE + r"(?:는)?(?=[^.\n]*?(?:겠습니다|게요))|이제부터|다음(?: \S+)?부터는? [^.\n]*?(?:겠습니다|게요)"
    r"|(?:이후 처리|이후로는|다음 세션|재발하지 않|반복되지 않)[^.\n]*?(?:겠습니다|게요)"
    r"|미확인(?!\s*(?:표기|표현|단어|문자열))|확인하지 않|확인되지 않")
# 한도 표현과 작업 불가 표현이다. 한 줄에 둘이 함께 있으면 한도를 이유로 한 작업 불가 응답으로 판정한다.
LIMIT_WORD = re.compile(r"한도|사용량 제한|rate limit|usage limit", re.I)
LIMIT_REFUSE = re.compile(r"(?:할|될|있을|드릴) 수 없|불가능|불가합니다|못 합니다|못합니다|어렵습니다")
REASON_LIMIT = "한도를 이유로 작업 불가를 알리는 응답은 표시할 수 없습니다. 한도 언급 없이 가능한 작업을 계속하고 결과를 작성하십시오."


# 한 줄에 한도 표현과 작업 불가 표현이 함께 있으면 True이다. 사건 문서에는 기록하지 않는다.
def limit_hit(message: str) -> bool:
    return any(LIMIT_WORD.search(line) and LIMIT_REFUSE.search(line) for line in plain(message).splitlines())


# 응답 미흡 판정에서 제외하는 줄이다. 검색 결과 요약의 [미조사] 줄과 사후 모델 호출 실측 줄이다.
EXEMPT_LINE = re.compile(r"\[미조사\]|^\s*[-*]?\s*사후 모델 호출 실측:")
QUESTION_END = re.compile(r"[?？]\s*\**\s*$")
# 검토 제목, 추가 제안, 에이전트 제안 표현과 웹 문서 링크이다.
PROPOSAL = re.compile(r"(?m)^#{1,6}\s*검토 \d+|추가 제안|에이전트 제안")
WEB_LINK = re.compile(r"https?://\S")
REASON_LINK = "검토나 제안을 제시한 응답에 웹 문서 링크 출처가 없습니다. 출처가 필요한 제안과 판단마다 해당 추론과 근거 문서의 http 링크만 작성하십시오."


REASON_REQUEST_FORM = ("구현 승인 요청 본문은 hook이 생성해 표시합니다. 응답에는 `구현 승인 요청: <작업 ID>` 한 줄만 작성하십시오.")
NOTICE_REQUEST = "\n\n구현을 승인하려면 `승인`을 입력하십시오."
REASON_QUESTION = ("응답을 질문 문장으로 끝냈습니다. 지침이나 요구로 정해지는 사안이면 근거를 적고 진행하십시오.\n"
                   "사용자 판단이 필요하면 후보 비교를 응답에 쓰고 질문 도구로 검토 <번호>. 질문을 하십시오.")
REASON_COMPLETE = "경과 문서 '{doc}'의 모든 요구사항 검증이 PASS이고 남은 문제가 없는데 완료로 바뀌지 않습니다. 사유: {reason}"
REASON_FORM = "응답: 관련 지침과 {form} 템플릿을 준수하십시오. 보완 대상: {missing}"
REASON_CHOICE = "후보 보고에서 누락된 안 블록 항목을 보완하십시오. {missing}"


REASON_EMPTY = "직전 응답이 예고나 해명으로 끝났습니다."


if __name__ == "__main__":
    sys.exit(main())
