"""문제 정의 질문과 번호 접두 검토 질문(폐지된 `요구사항`, `구현`, `조사` 포함)의 형식, 선택지 라벨, 폐기 번호를 질문 창이 뜨기 전에 검사한다.

호출자: hooks/check_tool_use.py, hooks/check_workflow_response.py(질문 형식 정규식, 작업 식별자 꼬리 분리, 작업 선택)
입력: 질문 도구(AskUserQuestion, request_user_input)의 hook 입력 dict
반환: check_question은 deny 사유 문자열 또는 통과 시 None
한계:
- 문제 정의 질문, 검토 질문, `[작업 선택]` 질문과 `중단 판정`이 든 질문 밖의 질문은 양식 위반으로 차단한다.
- 검토 질문은 형식, 라벨 형식과 `안 <번호>.` 선택지의 미리보기(기대 결과 `- ` 목록만 허용)를 판정하고, 문서의 비교표와 안 블록은 읽지 않는다.
- 작업은 질문 문장 끝 `(작업 MMDD-HHMMSS)`로 정하고, 없으면 진행 중인 작업이 하나일 때 그 작업, 여럿이면 세션이 마지막으로 쓴 경과 문서이다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from sub_claim import REASON_SOURCE, limit_risk_gap, unsourced_claims
from sub_call import VERDICT
from sub_docs import REVIEW_LINE, loop_count, problem_entries, project_root, review_entries, sibling
from sub_judgment import CONFIRM, open_tasks
from sub_path import REASON_SOURCE_PATH, source_leak
from sub_paths import TASK_REL
from sub_session import HARNESS, read_session, turn
from sub_write import read

PREFIX = re.compile(r"^(요구사항|구현|검토|조사|계획) \d+")
REVIEW = re.compile(r"^검토 (\d+)\. ([^:\n]+): (.+?) \(문제 (\d+(?:, ?\d+)*)\)$")
DROP = re.compile(r"^검토 (\d+) 폐기: (.+)$")
TASK_TAG = re.compile(r"\s*\(작업 (\d{4}-\d{6})\)\s*$")
OPTION_LABEL = re.compile(r"^안 (\d+)\. (.+)$")
QUESTION_END = re.compile(r"[?？]\s*$")
SINGLE_EXTRA = ("폐기", "재조사")
RECOMMENDED = re.compile(r"\s*\(Recommended\)\s*$")

REVIEW_FORM = "question-choice"
PLAN_FORM = "question-plan"
PROBLEM_HEAD = r"(?:\[문제 정의\]|문제 정의 확인:)"
# 어긴 양식 줄의 원문이다. 차단 사유에 위반한 줄만 보인다.
QUESTION_LINE = "검토 <번호>. <항목 이름>: <정할 대상>을 정한다. (문제 <번호>[, <번호>])"
LABEL_LINE = "- 안 <번호>. <안 이름>"
PROBLEM_LINE = "[문제 정의] 문제 <번호>. <확정할 문제와 범위 한 문장>"
REASON_FORMAT = f"검토 질문 문장 형식 `{QUESTION_LINE}`: {REVIEW_FORM} 양식을 따르십시오."
REASON_ENDING = f"검토 질문 문장 끝의 물음표 `{QUESTION_LINE}`: {REVIEW_FORM} 양식을 따르십시오."
STOP_QUESTION = "중단 판정"
REASON_KIND = "`{kind} <번호>` 질문 종류는 쓸 수 없습니다. " + REVIEW_FORM + " 양식의 검토 질문이나 " + PLAN_FORM + " 양식의 문제 정의 질문을 쓰십시오."
REASON_LABEL_FORMAT = "검토 질문 선택지 라벨 `{label}`, 양식 줄 `" + LABEL_LINE + "`: " + REVIEW_FORM + " 양식을 따르십시오."
REASON_LABEL_ORDER = f"검토 질문 선택지 라벨 순서 `{LABEL_LINE}`(번호 오름차순): {REVIEW_FORM} 양식을 따르십시오."
REASON_DROPPED = "`검토 {number}.`은 폐기된 번호입니다. 새 번호 `검토 {fresh}.`로 질문하십시오."
REASON_NO_LABELS = f"검토 질문 선택지 라벨 없음: {REVIEW_FORM} 양식을 따르십시오."
REASON_NO_CONFIRM = f"검토 폐기 질문 선택지의 확정 라벨 없음: {REVIEW_FORM} 양식을 따르십시오."
REASON_PROBLEM_LABEL = f"문제 정의 질문 선택지의 확정 라벨 없음: {PLAN_FORM} 양식을 따르십시오."
REASON_PROBLEM_TAG = (f"문제 정의 질문의 대상 작업: 질문 끝에 작업 폴더가 있는 작업의 `(작업 MMDD-HHMMSS)`가 필요합니다{{hint}}. "
                      f"{PLAN_FORM} 양식을 따르십시오.")
REASON_PROBLEM_FORMAT = f"문제 정의 질문 머리말과 문제 번호 형식 `{PROBLEM_LINE}`: {PLAN_FORM} 양식을 따르십시오."
PROBLEM_FORMAT = re.compile(rf"^{PROBLEM_HEAD} 문제 (\d+)(\. (?!폐기: |추가 합의: |교체: )|\.? 폐기: |\.? 추가 합의: |\.? 교체: )\S")
PROBLEM_LABELS = CONFIRM + ("보류", "폐기")
REASON_PROBLEM_LABEL_SET = "문제 정의 질문 선택지 라벨 `{label}`: " + PLAN_FORM + " 양식을 따르십시오."
REASON_PROBLEM_REPEAT = "문제 정의 질문 `문제 {number}.`: 이미 확정된 번호입니다. 추가 합의나 교체 질문은 " + PLAN_FORM + " 양식을 따르십시오."
REASON_PROBLEM_MISSING = "문제 {number}은 문제 정의에 없거나 폐기되었습니다. 추가 합의와 교체는 확정된 문제 번호에만 질문할 수 있습니다."
REASON_PROBLEM_UNCONFIRMED = "검토 질문 `(문제 {number})`: 사용자 확인 시각이 없는 문제 번호입니다. " + REVIEW_FORM + " 양식을 따르십시오."
REASON_PROBLEM_DROPPED = "검토 질문 `(문제 {number})`: 폐기된 문제 번호입니다. " + REVIEW_FORM + " 양식을 따르십시오."
WAITING_HEAD = "작업 중 받은 사용자 메시지입니다. 현재 작업에 반영하고, 질문이면 이번 응답에서 답하십시오.\n\n"
REFERENCE = re.compile(r"안 \d+\s*(?:에 더해|[과와] 같)|위와 같|앞서 말한|문서 하단|참조|작업 경과|(?:status|research|refute|spec)\.md")
UNCONFIRMED = ("미확인", "확인하지 않", "확인되지 않", "확인 못", "검증하지 않", "검증되지 않", "미측정", "측정하지 않", "알 수 없")
REASON_REFERENCE = "질문 문장과 선택지 설명이 다른 안이나 문서를 가리킵니다. 참조한 내용을 질문과 설명에 직접 작성하십시오. 문제 표현: `{found}`"
REASON_UNCONFIRMED = "검토 질문의 문장과 선택지 설명에 확인하지 않은 내용이 있습니다. 직접 확인한 뒤 다시 작성하십시오. 문제 표현: `{found}`"
PREVIEW_FIELD = re.compile(r"^-\s*(?:장점|단점|한계|잠재 위험|위험|근거|출처|비용)\s*:")
PREVIEW_LINE = "- <기대 결과 한 문장>"
REASON_NO_PREVIEW = "검토 질문 선택지 `{label}`의 미리보기 없음, 양식 줄 `" + PREVIEW_LINE + "`: " + REVIEW_FORM + " 양식을 따르십시오."
REASON_PREVIEW_FORMAT = "검토 질문 선택지 `{label}`의 미리보기 줄 `{line}`, 양식 줄 `" + PREVIEW_LINE + "`: " + REVIEW_FORM + " 양식을 따르십시오."
REASON_PREVIEW_FIELD = "검토 질문 선택지 `{label}`의 미리보기 줄 `{line}`: 기대 결과만 쓸 수 있습니다. " + REVIEW_FORM + " 양식을 따르십시오."
REASON_EDIT_LABEL = f"검토 질문 선택지 라벨 `수정`: {REVIEW_FORM} 양식을 따르십시오."
# 현재 진행 작업이 없을 때 진행할 작업을 묻는 작업 선택 질문 머리말과 선택지 라벨이다.
SELECT_FORM = "question-task"
TASK_SELECT = re.compile(r"^\[작업 선택\] \S")
TASK_ID = re.compile(r"\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*")
NEW_TASK_LABEL = "신규 작업"
REASON_NO_FORM = ("질문 문장 형식: " + PLAN_FORM + " 양식의 문제 정의 질문, " + REVIEW_FORM + " 양식의 검토 질문, "
                  + SELECT_FORM + " 양식의 작업 선택 질문, 중단 판정 질문만 쓸 수 있습니다.")
REASON_SELECT_NEW = f"작업 선택 질문 선택지에 `{NEW_TASK_LABEL}` 라벨이 없습니다. {SELECT_FORM} 양식을 따르십시오."
REASON_SELECT_LABEL = "작업 선택 질문 선택지 라벨 `{label}`: 작업 폴더가 있는 작업 ID나 `" + NEW_TASK_LABEL + "`만 쓸 수 있습니다. " + SELECT_FORM + " 양식을 따르십시오."
REASON_SELECT_DONE = "작업 선택 질문 선택지 `{label}`: 완료된 작업입니다. 미완료 작업만 선택지로 쓰십시오."


# 질문 도구 호출 입력의 질문 dict 목록이다.
def questions_of(args: dict) -> list[dict]:
    return [q for q in (args or {}).get("questions") or [] if isinstance(q, dict)]


# 질문 도구 호출 입력의 질문 문장, 선택지 설명, 미리보기를 이은 문자열이다.
def input_text(args: dict) -> str:
    parts = []
    for q in questions_of(args):
        parts.append(str(q.get("question") or ""))
        parts += [str(o.get(k) or "") for o in q.get("options") or [] if isinstance(o, dict) for k in ("description", "preview")]
    return "\n".join(parts)


# 질문의 선택지 라벨 목록이다. `(Recommended)` 꼬리는 뺀다.
def labels_of(question: dict) -> list[str]:
    return [RECOMMENDED.sub("", str(o.get("label") or "")).strip() for o in question.get("options") or []
            if isinstance(o, dict) and str(o.get("label") or "").strip()]


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


# 경과 문서 검토 필요 절에서 폐기 답이 기록된 검토 번호 집합과 새 번호이다.
def used_numbers(status: Path) -> tuple[set[int], int]:
    lines = [line for line, _ in review_entries(read(status))]
    every = [m[1] for line in lines if (m := re.match(r"- 검토 (\d+)\.", line))]
    gone = [m[1] for line in lines if (m := REVIEW_LINE.match(line)) and m[3] == "폐기"]
    return {int(n) for n in gone}, max((int(n) for n in every), default=0) + 1


# 선택지 라벨에서 `안 <번호>. ` 접두를 뗀 이름이다.
def bare_label(label: str) -> str:
    m = OPTION_LABEL.match(label)
    return m[2].strip() if m else label


# 검토 질문 라벨이 `안 <번호>. <안 이름>` 형식이 아니거나 번호가 오름차순이 아니면 사유를 반환한다. 맞으면 None이다.
def label_gap(labels: list[str]) -> str | None:
    numbers = []
    for label in labels:
        m = OPTION_LABEL.match(label)
        if not m and label not in SINGLE_EXTRA:
            return REASON_LABEL_FORMAT.format(label=label)
        numbers += [int(m[1])] if m else []
    return None if all(a < b for a, b in zip(numbers, numbers[1:])) else REASON_LABEL_ORDER


# director 메인 세션의 확정 질문에서 확정 라벨이 없거나 `수정` 라벨이 있으면 사유를 반환한다. 아니면 None이다.
def check_labels(labels: list[str], data: dict, runner: str) -> str | None:
    if not labels:
        return None

    # director 메인 세션만 검사한다.
    from sub_role import director_main
    if not director_main(data):
        return None
    if any(l == "수정" for l in labels):
        return REASON_EDIT_LABEL
    return None if any(w in l for l in labels for w in CONFIRM) else REASON_NO_CONFIRM


# 질문 문장, 선택지 설명과 미리보기를 이은 문자열이다.
def sentence_text(question: dict) -> str:
    options = [str(o.get(k) or "") for o in question.get("options") or [] if isinstance(o, dict) for k in ("description", "preview")]
    return "\n".join([str(question.get("question") or "")] + options)


# 검토 질문의 `안 <번호>.` 선택지 미리보기가 없거나, 빈 줄이 아닌 줄이 `- ` 목록이 아니거나, 기대 결과 밖 항목(`장점:` 등)이 있으면 사유를 반환한다.
# `폐기`, `재조사` 선택지는 기대 결과가 없으므로 판정하지 않는다. 통과하면 None이다.
def preview_gap(question: dict) -> str | None:
    for o in question.get("options") or []:
        label = RECOMMENDED.sub("", str(o.get("label") or "")).strip() if isinstance(o, dict) else ""
        if not OPTION_LABEL.match(label):
            continue
        lines = [l.strip() for l in str(o.get("preview") or "").splitlines() if l.strip()]
        if not lines:
            return REASON_NO_PREVIEW.format(label=label)
        bad = next((l for l in lines if not l.startswith("- ")), None)
        if bad:
            return REASON_PREVIEW_FORMAT.format(label=label, line=bad)
        field = next((l for l in lines if PREVIEW_FIELD.match(l)), None)
        if field:
            return REASON_PREVIEW_FIELD.format(label=label, line=field)
    return None


# 문제 정의 확인 질문과 검토 질문의 문장과 설명에 참조 표현이나 문서 경로가 있으면 사유를 반환한다.
# 검토 질문이면 확인하지 않은 내용을 뜻하는 표현도 검사한다. 통과하면 None이다.
def wording_gap(question: dict, review: bool) -> str | None:
    text = sentence_text(question)
    found = REFERENCE.search(text)
    if found:
        return REASON_REFERENCE.format(found=found[0])
    word = next((w for w in UNCONFIRMED if w in text), None) if review else None
    return REASON_UNCONFIRMED.format(found=word) if word else None


# 같은 번호 재확정, 없는 번호의 추가 합의와 교체 질문이면 사유를 반환한다. 작업 식별자로 경과 문서를 찾지 못하면 판정하지 않는다.
def problem_gap(form, tag: str | None, data: dict) -> str | None:
    root = project_root(data.get("cwd"))
    status = next((p for p in sorted((root / TASK_REL).glob("*/status.md")) if p.parent.name[4:15] == tag), None)
    if not tag:
        from sub_task_index import selected_status
        status = selected_status(root)
    if status is None:
        return None
    # 구분자 원문(`. `, `. 폐기: `, `. 추가 합의: `, `. 교체: `)에서 점, 콜론과 공백을 뗀 종류이며 확정 질문은 빈 문자열이다.
    number, kind = int(form[1]), form[2].strip(" .:")
    known = problem_entries(read(status), read(sibling(status, "input"))).get(number)
    if not kind and known is not None:
        return REASON_PROBLEM_REPEAT.format(number=number)
    if kind in ("추가 합의", "교체") and (known is None or known[1]):
        return REASON_PROBLEM_MISSING.format(number=number)
    return None


REFUTE_ROUNDS = 2
REASON_REFUTE_PENDING = "반증이 끝나지 않았습니다. 반증 문서의 최종 회차 판정이 전부 지지이거나 반증이 2회에 도달한 뒤에 검토 질문을 하십시오."
PLAN_ITEM = re.compile(r"(?m)^#{3,} 조사 \d+\. .*\(문제 (\d+(?:, ?\d+)*)\)\s*$")


# 조사 문서 조사 계획 절의 항목 제목에 적힌 문제 번호 집합이다. 조사 문서가 없으면 빈 집합이다.
def planned_problems(research: Path) -> set[str]:
    from sub_docs import section
    return {n for m in PLAN_ITEM.finditer(section(read(research), "조사 계획")) for n in re.findall(r"\d+", m[1])}


# 계획 단계 작업에서 검토 질문의 문제 번호가 조사 계획 항목과 연결됐는데 반증이 끝나지 않았으면 사유를 반환한다.
# 반증 2회에 도달했거나 최종 회차 판정이 전부 지지면 끝난 것이다. 계획 단계가 아니거나 조사 계획 항목과 연결되지 않은 검토는 판정하지 않는다.
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


# 검토 질문의 문제 번호가 대상 경과 문서 문제 정의에서 확인 시각이 있고 폐기되지 않은 번호가 아니면 사유를 반환한다. 대상 경과 문서가 없으면 판정하지 않는다.
def problem_link_gap(numbers: str, tasks: list) -> str | None:
    if not tasks:
        return None
    entries = problem_entries(read(tasks[0]), read(sibling(tasks[0], "input")))
    for number in re.findall(r"\d+", numbers):
        known = entries.get(int(number))
        if known is not None and known[1]:
            return REASON_PROBLEM_DROPPED.format(number=number)
        if known is None or not known[0]:
            return REASON_PROBLEM_UNCONFIRMED.format(number=number)
    return None


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


# 질문 하나가 양식(문제 정의, 검토, 작업 선택, 중단 판정)을 어기면 사유를 반환한다. 통과하면 None이다.
def check_one(question: dict, data: dict, runner: str) -> str | None:
    # 문장 끝 작업 식별자는 형식 검사 전에 뗀다. 문제 정의, 검토, 중단 판정 질문이 아니면 양식 위반이다.
    text, tag = split_tag(question.get("question") or "")
    head = PREFIX.match(text)
    problem = bool(re.match(PROBLEM_HEAD, text))
    if problem or (head and head[1] == "검토"):
        reason = wording_gap(question, review=not problem)
        if reason:
            return reason
    if problem:
        # 확정 답의 기록 대상이 질문 식별자로만 정해지도록 식별자와 작업 폴더를 요구한다.
        if tag is None or not tasks_of(data, runner, tag):
            selected = tasks_of(data, runner)
            hint = f"(현재 진행 작업: `(작업 {selected[0].parent.name[4:15]})`)" if selected else ""
            return REASON_PROBLEM_TAG.format(hint=hint)
        form = PROBLEM_FORMAT.match(text)
        if not form:
            return REASON_PROBLEM_FORMAT
        labels = labels_of(question)
        bad = next((l for l in labels if l not in PROBLEM_LABELS), None)
        if bad:
            return REASON_PROBLEM_LABEL_SET.format(label=bad)
        if not any(l in CONFIRM for l in labels):
            return REASON_PROBLEM_LABEL
        return problem_gap(form, tag, data)
    if TASK_SELECT.match(text):
        return task_select_gap(question, data)
    if not head:
        return None if STOP_QUESTION in text else REASON_NO_FORM
    kind = head[1]
    if kind != "검토":
        return REASON_KIND.format(kind=kind)

    # 물음표로 끝나거나 검토 질문 형식이 아니면 차단한다.
    if QUESTION_END.search(text):
        return REASON_ENDING
    review = REVIEW.match(text)
    if not (review or DROP.match(text)):
        return REASON_FORMAT
    labels = labels_of(question)
    tasks = tasks_of(data, runner, tag)

    # 대상 작업에서 폐기된 번호의 질문은 막고 새 번호를 안내한다.
    number = head.group(0).split()[1]
    if tasks and not DROP.match(text):
        gone, fresh = used_numbers(tasks[0])
        if int(number) in gone:
            return REASON_DROPPED.format(number=number, fresh=fresh)

    # 검토 질문은 안 번호 라벨 형식과 순서만 검사하고, 확정 라벨 검사에서는 빠진다.
    if review:
        return refute_gap(tasks, review[4]) or problem_link_gap(review[4], tasks) or (label_gap(labels) if labels else REASON_NO_LABELS) or preview_gap(question)

    # 폐기 질문은 확정 라벨을 요구한다.
    return check_labels(labels, data, runner)


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


# 질문 도구 호출이 출처 없는 확인 주장이거나 번호 접두 질문의 형식, 라벨, 폐기 번호를 어기면 사유를 반환한다.
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

    # 질문마다 번호 접두 질문 검사를 한다.
    for question in questions_of(data.get("tool_input") or {}):
        reason = check_one(question, data, runner)
        if reason:
            return reason

    # 문제 정의 확인 질문(폐기 형식 제외) 전 같은 응답 본문에 한계와 위험 두 줄이 있어야 한다.
    asked = [split_tag(q.get("question") or "")[0] for q in questions_of(data.get("tool_input") or {})]
    if data.get("transcript_path") and any(re.match(PROBLEM_HEAD, a) and " 폐기: " not in a for a in asked):
        entries = turn(read_session(data["transcript_path"]))[2]
        reason = limit_risk_gap("\n".join(e["text"] for e in entries if e["role"] == "assistant"))
        if reason:
            return reason
    return None
