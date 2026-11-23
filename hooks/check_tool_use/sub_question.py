"""확정 질문(`요구사항 <번호>.`), 재구현 질문(`재구현 요청 <번호>.`)과 작업 선택 질문의 형식을 질문 창이 뜨기 전에 검사한다.

호출자: hooks/check_tool_use.py, hooks/check_workflow_response.py(질문 형식 정규식, 작업 식별자 꼬리 분리, 작업 선택)
입력: 질문 도구(AskUserQuestion, request_user_input)의 hook 입력 dict
반환: check_question은 deny 사유 문자열 또는 통과 시 None
한계:
- 양식 머리말(`요구사항 <번호>`, `재구현 요청 <번호>` 등)로 시작하지 않는 질문은 검사하지 않고, 머리말로 시작해 양식을 어긴 질문만 차단한다.
- 확정 질문은 번호, 이름, 목표와 확정 설명이 직전 확정안의 줄 원문과 같은지만 판정하고, 확정안 내용이 합의된 것인지는 판정하지 않는다.
- 작업은 질문 문장 끝 `(작업 MMDD-HHMMSS)`로 정하고, 없으면 진행 중인 작업이 하나일 때 그 작업, 여럿이면 세션이 마지막으로 쓴 경과 문서이다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from sub_claim import REASON_SOURCE, unsourced_claims
from sub_call import VERDICT
from sub_display import confirmed_lines, find_confirmed, norm, session_confirmed
from sub_docs import loop_count, project_root, section, sibling
from sub_path import REASON_SOURCE_PATH, source_leak
from sub_paths import TASK_REL
from sub_session import HARNESS, read_session
from sub_spec import blocks
from sub_write import read

PREFIX = re.compile(r"^(요구사항|재구현 요청|구현|검토|조사|계획) \d+")
REQUIREMENT = re.compile(r"^요구사항 (\d+)\. ([^:\n]+): (.+)$")
REIMPLEMENT = re.compile(r"^재구현 요청 (\d+)\. ([^:\n]+): (.+)$")
TASK_TAG = re.compile(r"\s*\(작업 (\d{4}-\d{6})\)\s*$")
RECOMMENDED = re.compile(r"\s*\(Recommended\)\s*$")

CONFIRMED_FORM = "question-confirmed"
CONFIRM_LABELS = ("확정", "폐기")
REIMPLEMENT_LABELS = ("승인", "보류")
# 어긴 양식 줄의 원문이다. 차단 사유에 위반한 줄만 보인다.
QUESTION_LINE = "요구사항 <번호>. <요구사항 이름>: <목표>."
REIMPLEMENT_LINE = "재구현 요청 <요구사항 번호>. <수정 대상 경로>: <수정 위치>"
REASON_FORMAT = f"확정 질문 문장 형식 `{QUESTION_LINE}`: {CONFIRMED_FORM} 양식을 따르십시오."
REASON_KIND = "`{kind} <번호>` 질문 종류는 쓸 수 없습니다. " + CONFIRMED_FORM + " 양식의 확정 질문을 쓰십시오."
REASON_LABELS = f"확정 질문 선택지 라벨은 `확정`, `폐기` 두 개여야 합니다: {CONFIRMED_FORM} 양식을 따르십시오."
REASON_NO_DISPLAY = ("질문의 번호, 이름과 목표가 직전 확정안의 번호, 이름과 목표 줄 원문과 같지 않습니다. 확정안 원문을 그대로 쓰십시오.\n"
                     "확정안 원문:\n{shown}")
REASON_NO_SHOWN = "직전 응답에 확정안이 없습니다. 확정 질문 전에 info-confirmed 양식의 확정안을 응답에 작성하십시오."
REASON_DESCRIPTION = ("`확정` 선택지 설명이 직전 확정안의 기대 결과 줄 원문과 같지 않습니다. 기대 결과 줄을 그대로 쓰십시오.\n"
                      "확정안 원문:\n{shown}")
REASON_REIMPLEMENT_FORMAT = f"재구현 질문 문장 형식 `{REIMPLEMENT_LINE}`을 따르십시오. 승인 선택지 설명에 기대 결과를 쓰십시오."
REASON_REIMPLEMENT_LABELS = "재구현 질문 선택지 라벨은 `승인`을 포함하고 `승인`, `보류`만 쓸 수 있습니다."
REASON_REIMPLEMENT_NUMBER = "재구현 질문의 요구사항 {number}은 명세 문서 요구사항 목록에 없거나 폐기되었습니다."
REASON_REIMPLEMENT_DESC = "재구현 질문의 `승인` 선택지 설명에 수정 후 관측할 기대 결과를 쓰십시오."
WAITING_HEAD = ("작업 중 받은 사용자 메시지입니다. 현재 작업에 반영하고, 질문이면 이번 응답에서 답하십시오. "
                "이 메시지는 턴이 끝난 뒤 다시 전송될 수 있으니, 다시 받으면 이미 답한 내용은 반복하지 마십시오.\n\n")
# 현재 진행 작업이 없을 때 진행할 작업을 묻는 작업 선택 질문 머리말과 선택지 라벨이다.
SELECT_FORM = "question-task"
TASK_SELECT = re.compile(r"^\[작업 선택\] \S")
TASK_ID = re.compile(r"\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*")
NEW_TASK_LABEL = "신규 작업"
REASON_SELECT_NEW = f"작업 선택 질문 선택지에 `{NEW_TASK_LABEL}` 라벨이 없습니다. {SELECT_FORM} 양식을 따르십시오."
REASON_SELECT_LABEL = "작업 선택 질문 선택지 라벨 `{label}`: 작업 폴더가 있는 작업 ID나 `" + NEW_TASK_LABEL + "`만 쓸 수 있습니다. " + SELECT_FORM + " 양식을 따르십시오."
REASON_SELECT_DONE = "작업 선택 질문 선택지 `{label}`: 완료된 작업입니다. 미완료 작업만 선택지로 쓰십시오."


# 질문 도구 호출 입력의 질문 dict 목록이다.
def questions_of(args: dict) -> list[dict]:
    return [q for q in (args or {}).get("questions") or [] if isinstance(q, dict)]


# 질문 도구 호출 입력의 질문 문장과 선택지 설명을 이은 문자열이다.
def input_text(args: dict) -> str:
    parts = []
    for q in questions_of(args):
        parts.append(str(q.get("question") or ""))
        parts += [str(o.get("description") or "") for o in q.get("options") or [] if isinstance(o, dict)]
    return "\n".join(parts)


# 질문의 선택지 라벨 목록이다. `(Recommended)` 꼬리는 뺀다.
def labels_of(question: dict) -> list[str]:
    return [RECOMMENDED.sub("", str(o.get("label") or "")).strip() for o in question.get("options") or []
            if isinstance(o, dict) and str(o.get("label") or "").strip()]


# 질문의 라벨 선택지 설명이다. 같은 라벨이 없으면 빈 문자열이다.
def description_of(question: dict, label: str) -> str:
    for o in question.get("options") or []:
        if isinstance(o, dict) and RECOMMENDED.sub("", str(o.get("label") or "")).strip() == label:
            return " ".join(str(o.get("description") or "").split())
    return ""


# 질문 문장의 (작업 식별자 꼬리를 뗀 문장, 식별자)이다. 식별자가 없으면 None이다.
def split_tag(text: str) -> tuple[str, str | None]:
    text = " ".join(str(text).split())
    m = TASK_TAG.search(text)
    return (text[:m.start()], m[1]) if m else (text, None)


# 질문이 다루는 경과 문서 목록이다. 식별자가 있으면 그 작업 폴더의 경과 문서이고(폴더가 없으면 빈 목록),
# 없으면 색인의 현재 진행 작업이다.
def tasks_of(data: dict, runner: str, tag: str | None = None) -> list:
    # 작업 식별자(MMDD-HHMMSS)를 작업 폴더 이름(YYYYMMDD-HHMMSS-...)과 대조한다.
    root = project_root(data.get("cwd"))
    if tag:
        return [p for p in sorted((root / TASK_REL).glob("*/status.md")) if p.parent.name[4:15] == tag][:1]

    from sub_task_index import selected_status
    selected = selected_status(root)
    return [selected] if selected else []


REFUTE_ROUNDS = 2
REASON_REFUTE_PENDING = "반증이 끝나지 않았습니다. 반증 문서의 최종 회차 판정이 전부 지지이거나 반증이 2회에 도달한 뒤에 확정 질문을 하십시오."
PLAN_ITEM = re.compile(r"(?m)^#{3,} 조사 \d+\. .*\(문제 (\d+(?:, ?\d+)*)\)\s*$")


# 조사 문서 조사 계획 절의 항목 제목에 적힌 문제 번호 집합이다. 조사 문서가 없으면 빈 집합이다.
def planned_problems(research: Path) -> set[str]:
    return {n for m in PLAN_ITEM.finditer(section(read(research), "조사 계획")) for n in re.findall(r"\d+", m[1])}


# 계획 단계 작업에서 확정 질문의 요구사항 번호가 조사 계획 항목과 연결됐는데 반증이 끝나지 않았으면 사유를 반환한다.
# 반증 2회에 도달했거나 최종 회차 판정이 전부 지지면 끝난 것이다. 계획 단계가 아니거나 조사 계획 항목과 연결되지 않은 질문은 판정하지 않는다.
def refute_gap(tasks: list, numbers: str = "") -> str | None:
    from sub_approval import stage_of
    if not tasks or stage_of(read(tasks[0])) != "계획":
        return None
    if not planned_problems(sibling(tasks[0], "research")) & set(re.findall(r"\d+", numbers)):
        return None
    text = read(sibling(tasks[0], "refute"))
    if loop_count(text) >= REFUTE_ROUNDS:
        return None
    rounds = (re.findall(r"(?ms)^### \d+회차 반증 결과[^\n]*\n(.*?)(?=^##? |^### |\Z)", text)
              or re.findall(r"(?ms)^### \d+회차 반증 원문\s*\n+`{3,}[^\n]*\n(.*?)\n`{3,}", text))
    verdicts = [m[2] for line in (rounds[-1].splitlines() if rounds else []) if (m := VERDICT.match(line))]
    return None if verdicts and all(v == "지지" for v in verdicts) else REASON_REFUTE_PENDING


# 대상 작업 명세 문서에서 요구사항 번호의 (절 본문, 폐기 여부)이다. 명세 문서나 항목이 없으면 None이다.
def spec_block(tasks: list, number: int) -> tuple[str, bool] | None:
    if not tasks:
        return None
    for k, block in blocks(section(read(sibling(tasks[0], "input")), "요구사항 목록")):
        if k == number:
            return block, block.splitlines()[0].rstrip().endswith(". 폐기")
    return None


# 확정 질문의 번호, 이름과 목표가 직전 확정안과 같고 확정 설명이 확정안의 기대 결과 줄과 같은지 검사한다. 위반이면 사유, 통과면 None이다.
def display_gap(question: dict, number: int, name: str, goal: str, data: dict) -> str | None:
    items = session_confirmed(read_session(data.get("transcript_path") or "")) if data.get("transcript_path") else []
    if not items:
        return REASON_NO_SHOWN
    confirmed = find_confirmed(items, number, name, goal)
    if confirmed is None:
        return REASON_NO_DISPLAY.format(shown=confirmed_lines(items[-1]))
    if norm(description_of(question, "확정")) != norm(confirmed["fields"]["기대 결과"]):
        return REASON_DESCRIPTION.format(shown=confirmed_lines(confirmed))
    return None


# 확정 질문의 라벨, 반증 상태와 확정안 원문 대조 사유를 순서대로 검사한다. 통과하면 None이다.
def confirmed_gap(question: dict, match: re.Match, tag: str | None, data: dict, runner: str) -> str | None:
    if sorted(labels_of(question)) != sorted(CONFIRM_LABELS):
        return REASON_LABELS
    number = int(match[1])
    return (refute_gap(tasks_of(data, runner, tag), str(number))
            or display_gap(question, number, match[2].strip(), match[3].strip(), data))


# 재구현 질문의 형식, 라벨, 요구사항 번호와 기대 결과 설명을 검사한다. 통과하면 None이다.
def reimplement_gap(question: dict, match: re.Match, tag: str | None, data: dict, runner: str) -> str | None:
    labels = labels_of(question)
    if "승인" not in labels or any(l not in REIMPLEMENT_LABELS for l in labels):
        return REASON_REIMPLEMENT_LABELS
    found = spec_block(tasks_of(data, runner, tag), int(match[1]))
    if found is None or found[1]:
        return REASON_REIMPLEMENT_NUMBER.format(number=match[1])
    return None if description_of(question, "승인") else REASON_REIMPLEMENT_DESC


# 작업 선택 질문의 선택지가 미완료 작업 ID와 `신규 작업` 라벨로만 이뤄졌는지 검사한다. 위반이면 사유, 통과면 None이다.
def task_select_gap(question: dict, data: dict) -> str | None:
    from sub_task_index import status_row
    root = project_root(data.get("cwd"))
    labels = labels_of(question)
    if NEW_TASK_LABEL not in labels:
        return REASON_SELECT_NEW
    for label in labels:
        if label == NEW_TASK_LABEL:
            continue
        if not TASK_ID.fullmatch(label) or not (root / TASK_REL / label / "status.md").is_file():
            return REASON_SELECT_LABEL.format(label=label)
        if status_row(root, label)[1] == "완료":
            return REASON_SELECT_DONE.format(label=label)
    return None


# 질문 하나가 양식 머리말로 시작해 양식(확정, 재구현, 작업 선택)을 어기면 사유를 반환한다. 머리말이 없는 질문과 통과한 질문은 None이다.
def check_one(question: dict, data: dict, runner: str) -> str | None:
    # 문장 끝 작업 식별자는 형식 검사 전에 뗀다.
    text, tag = split_tag(question.get("question") or "")
    if TASK_SELECT.match(text):
        return task_select_gap(question, data)
    head = PREFIX.match(text)
    if not head:
        return None
    if head[1] == "요구사항":
        match = REQUIREMENT.match(text)
        return confirmed_gap(question, match, tag, data, runner) if match else REASON_FORMAT
    if head[1] == "재구현 요청":
        match = REIMPLEMENT.match(text)
        return reimplement_gap(question, match, tag, data, runner) if match else REASON_REIMPLEMENT_FORMAT
    return REASON_KIND.format(kind=head[1])


# 하네스 계속 요청 문장이다. 사용량 한도 재설정 안내 뒤에 붙는 형식도 포함한다.
SYSTEM_REQUEST = re.compile(r"^\s*(?:I hit my usage limit while you were working, but it has reset now\.\s*Please\s+)?"
                            r"continue from where you left off\.?\s*$", re.I)


# 빈 응답 뒤 하네스가 넣는 계속 요청이나 하네스 알림이면 True이다. 사용자가 입력한 메시지가 아니다.
def system_request(content: str) -> bool:
    return bool(SYSTEM_REQUEST.match(content)) or content.lstrip().startswith(HARNESS)


# 세션 기록에서 응답하지 않은 queue-operation 사용자 메시지 목록이다. 이후 dequeue, remove, popAll 기록이나
# 같은 내용의 user, attachment 항목이 있으면 전달된 메시지로 본다. 하네스의 계속 요청은 사용자 메시지로 보지 않는다.
# 읽지 못하면 빈 목록이다.
def waiting_messages(path: str) -> list[str]:
    pending: list[str] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError, ValueError):
        return pending
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "queue-operation":
            operation, content = entry.get("operation"), str(entry.get("content") or "")
            if operation == "enqueue" and content.strip():
                pending.append(content)
            elif operation in ("dequeue", "remove") and pending:
                pending.pop(0)
            elif operation == "popAll":
                pending.clear()
        elif pending and entry.get("type") in ("user", "attachment"):
            dumped = json.dumps(entry, ensure_ascii=False)
            pending = [c for c in pending if json.dumps(c, ensure_ascii=False)[1:-1] not in dumped]
    return [c for c in pending if not system_request(c)]


# 질문 도구 호출이 출처 없는 확인 주장이거나 질문의 형식, 라벨, 검토 표시 원문 대조를 어기면 사유를 반환한다.
def check_question(data: dict, runner: str = "claude") -> str | None:
    # 질문 입력에 출처 없는 확인 주장이 있으면 차단한다.
    text = input_text(data.get("tool_input") or {})
    claims = unsourced_claims(text, text)
    if claims:
        return REASON_SOURCE.format(sentences=" / ".join(claims[:3]))

    # 대기 메시지는 check_tool_use.py가 모든 도구에 대해 먼저 판정한다.
    # Codex는 hook 입력과 app-server에 대기 입력 정보가 없어 판정하지 않으며, Tab으로 넣은 대기 입력은 차단하지 못한다.
    # 질문 문장과 선택지에 출처로 쓸 수 없는 경로나 식별자가 있으면 차단한다.
    found = source_leak(text, data, project_root(data.get("cwd")))
    if found:
        return REASON_SOURCE_PATH.format(found=found)

    # 질문마다 양식 검사를 한다.
    for question in questions_of(data.get("tool_input") or {}):
        reason = check_one(question, data, runner)
        if reason:
            return reason
    return None

