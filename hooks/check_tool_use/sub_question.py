"""번호 접두 질문(`요구사항`, `검토`, 폐지된 `구현`, `조사`)의 형식, 검토 보고, 선택지 라벨, 폐기 번호를 질문 창이 뜨기 전에 검사한다.

호출자: hooks/check_tool_use.py, hooks/check_workflow_response.py(질문 형식 정규식, 작업 식별자 꼬리 분리, 작업 선택)
입력: 질문 도구(AskUserQuestion, request_user_input)의 hook 입력 dict
반환: check_question은 deny 사유 문자열 또는 통과 시 None
한계:
- 번호 접두가 없는 질문은 출처 주장 외에 검사하지 않는다.
- 검토 보고는 조사 문서의 비교표 행과 안 블록 제목의 라벨 문자열 일치만 판정하고, 응답 본문은 검사하지 않는다.
- 작업은 질문 문장 끝 `(작업 MMDD-HHMMSS)`로 정하고, 없으면 진행 중인 작업이 하나일 때 그 작업, 여럿이면 세션이 마지막으로 쓴 경과 문서이다.
"""
from __future__ import annotations

import re
from pathlib import Path

from sub_claim import REASON_SOURCE, unsourced_claims
from sub_docs import decision_path, project_root, section, sibling, titles
from sub_judgment import CONFIRM, open_tasks, review_items
from sub_write import read

PREFIX = re.compile(r"^(요구사항|구현|검토|조사) \d+")
REQUIREMENT = re.compile(r"^요구사항 (\d+)\. ([^:\n]+): (.+)$")
REVIEW = re.compile(r"^검토 (\d+)\. ([^:\n]+): (.+)$")
DROP = re.compile(r"^(요구사항|검토) (\d+) 폐기: (.+)$")
TASK_TAG = re.compile(r"\s*\(작업 (\d{4}-\d{6})\)\s*$")
OPTION_LABEL = re.compile(r"^안 (\d+)\. (.+)$")
QUESTION_END = re.compile(r"[?？]\s*$")
TABLE_OR_HEADING = re.compile(r"(?m)^(?:\|.+\|[ \t]*\n[ \t]*\|[ \t:|-]*-[ \t:|-]*$|#{1,6} \S.*\n\s*\S)")
SEPARATOR = re.compile(r"^\s*\|[ \t:|-]*-[ \t:|-]*\|?\s*$")
SINGLE_EXTRA = ("폐기", "재조사")
RECOMMENDED = re.compile(r"\s*\(Recommended\)\s*$")

FORMAT = {"요구사항": "`요구사항 <번호>. <이름>: <확인 가능한 결과 한 문장>` 또는 `요구사항 <번호> 폐기: <사유>`",
          "검토": "`검토 <번호>. <항목 이름>: <정할 대상>을 정한다.` 또는 `검토 <번호> 폐기: <사유>`"}
REASON_FORMAT = "`{kind} <번호>` 질문 형식이 맞지 않습니다. {format} 형식의 서술문으로 쓰십시오."
REASON_ENDING = "번호 접두 질문은 서술문이어야 합니다. 물음표로 끝나지 않게 쓰십시오."
REASON_RESEARCH = "조사 항목은 질문 도구로 묻지 않습니다. 착수 보고에 조사 계획을 나열하고 착수 승인으로 확정하십시오."
REASON_IMPLEMENTATION = ("`구현 <번호>` 질문은 없습니다. 작업 내용은 착수 보고에 쓰고, "
                         "`요구사항 <번호>. <이름>: <확인 가능한 결과 한 문장>` 질문으로 요구사항을 확정받으십시오.")
REASON_LABEL_FORMAT = "`검토 <번호>.` 질문의 선택지 라벨은 `안 <번호>. <안 이름>` 형식이어야 합니다(`폐기`와 `재조사`는 예외). 문제 라벨: `{label}`"
REASON_LABEL_ORDER = "`검토 <번호>.` 질문의 선택지는 `안 <번호>.` 번호 순서(오름차순)로 두십시오."
REASON_DROPPED = "`{kind} {number}.`은 폐기된 번호입니다. 새 번호 `{kind} {fresh}.`로 질문하십시오."
REASON_NO_LABELS = "`검토 <번호>.` 질문에는 안 이름 선택지가 있어야 합니다."
REASON_NO_REPORT = "검토 보고 절에 `검토 {number}.` 제목이 없습니다. 명세 문서에 비교표와 안 블록을 먼저 쓰십시오."
REASON_NO_TABLE = "`검토 {number}.` 보고에 구분선 있는 비교표가 없습니다. 비교표를 먼저 쓰십시오."
REASON_NO_ROW = "`검토 {number}.` 비교표에 선택지 라벨 {labels}의 행이 없습니다."
REASON_NO_BLOCK = "`검토 {number}.` 보고에 선택지 라벨 {labels}와 번호와 이름이 같은 안 블록이 없습니다."
REASON_NO_TASK = "진행 중인 작업이 없어 `검토 {number}.` 보고를 찾을 수 없습니다."
REASON_NO_CONFIRM = "경과 문서를 쓴 세션의 질문은 선택지 라벨 하나 이상에 확정, 채택, 승인, 확인, 동의 중 하나를 넣으십시오."
REASON_EDIT_LABEL = "라벨이 `수정`인 선택지는 둘 수 없습니다. 내용 수정은 질문 도구의 Other 입력으로 받으십시오."


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


# 질문이 다루는 경과 문서 목록이다. 식별자가 있으면 그 작업 폴더의 경과 문서이고,
# 없으면 진행 중인 작업이 하나일 때 그 작업, 여럿이면 세션이 마지막으로 쓴 경과 문서이다.
def tasks_of(data: dict, runner: str, tag: str | None = None) -> list:
    # 작업 식별자(MMDD-HHMMSS)를 작업 폴더 이름(YYYYMMDD-HHMMSS-...)과 대조한다.
    root = project_root(data.get("cwd"))
    if tag:
        found = [p for p in sorted((root / "log" / "task").glob("*/status.md")) if p.parent.name[4:15] == tag]
        if found:
            return found[:1]

    # check_workflow_response가 이 모듈을 불러오므로 호출 시점에 불러온다.
    from check_workflow_response import written_status
    opened = open_tasks(root)
    if len(opened) == 1:
        return opened
    written = written_status(data, runner)
    return [written] if written else opened


# 경과 문서에서 폐기된 kind(`요구사항` 또는 `검토`) 번호 집합과 새 번호이다.
def used_numbers(status: Path, kind: str) -> tuple[set[int], int]:
    text = read(decision_path(status))
    if kind == "요구사항":
        body = section(text, "요구사항 목록")
        every = re.findall(r"(?m)^(?:### )?(?:요구사항 )?(\d+)\. ", body)
        gone = re.findall(r"(?m)^(?:### )?(?:요구사항 )?(\d+)\. 폐기\s*$", body)
    else:
        names = review_items(text)
        every = [m[1] for n in names if (m := re.match(r"검토 (\d+)", n))]
        gone = [m[1] for n in names if (m := re.match(r"검토 (\d+)\. 폐기", n))]
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


# 조사 문서에서 `검토 number.` 보고의 비교표와 안 블록이 labels를 모두 갖추지 못했으면 사유를 반환한다. 갖췄으면 None이다.
def report_gap(research: str, number: str, labels: list[str]) -> str | None:
    # 검토 보고 절 아래 해당 번호의 보고 본문을 찾는다.
    block = section(section(research, "검토 보고"), f"검토 {number}.")
    if not block.strip():
        return REASON_NO_REPORT.format(number=number)

    # 구분선 있는 비교표와 라벨별 행을 확인한다.
    rows = [l for l in block.splitlines() if l.lstrip().startswith("|")]
    if not any(SEPARATOR.match(l) for l in rows):
        return REASON_NO_TABLE.format(number=number)
    body = [l for l in rows if not SEPARATOR.match(l)]

    # 비교표 데이터 행이 하나인 검토는 `폐기`, `재조사` 라벨을 행과 안 블록 없이 허용한다.
    cut = next(i for i, l in enumerate(rows) if SEPARATOR.match(l))
    if len(rows) - cut - 1 == 1:
        labels = [l for l in labels if bare_label(l) not in SINGLE_EXTRA]
    lost = [l for l in labels if not any(bare_label(l) in row for row in body)]
    if lost:
        return REASON_NO_ROW.format(number=number, labels=", ".join(lost))

    # 라벨별 안 블록 제목을 확인한다.
    named = {m[2].strip(): m[1] for t in titles(block) if (m := OPTION_LABEL.match(t))}
    lost = [l for l in labels if bare_label(l) not in named or ((m := OPTION_LABEL.match(l)) and named[bare_label(l)] != m[1])]
    return REASON_NO_BLOCK.format(number=number, labels=", ".join(lost)) if lost else None


# 경과 문서를 쓴 세션의 확정 질문에서 확정 라벨이 없거나 `수정` 라벨이 있으면 사유를 반환한다. 아니면 None이다.
def check_labels(labels: list[str], data: dict, runner: str) -> str | None:
    if not labels:
        return None

    # 경과 문서를 쓴 세션이 아니면 검사하지 않는다. check_workflow_response가 이 모듈을 불러오므로 호출 시점에 불러온다.
    from check_workflow_response import written_status
    if written_status(data, runner) is None:
        return None
    if any(l == "수정" for l in labels):
        return REASON_EDIT_LABEL
    return None if any(w in l for l in labels for w in CONFIRM) else REASON_NO_CONFIRM


# 번호 접두 질문 하나를 검사한다. 위반이면 사유를, 통과면 None을 반환한다.
def check_one(question: dict, data: dict, runner: str) -> str | None:
    # 번호 접두가 없는 질문은 검사하지 않는다. 문장 끝 작업 식별자는 형식 검사 전에 뗀다.
    text, tag = split_tag(question.get("question") or "")
    head = PREFIX.match(text)
    if not head:
        return None
    kind = head[1]
    if kind == "조사":
        return REASON_RESEARCH
    if kind == "구현":
        return REASON_IMPLEMENTATION

    # 물음표로 끝나거나 종류별 형식이 아니면 막는다.
    if QUESTION_END.search(text):
        return REASON_ENDING
    review = REVIEW.match(text) if kind == "검토" else None
    formats = {"요구사항": (REQUIREMENT, DROP), "검토": (REVIEW, DROP)}[kind]
    if not any(p.match(text) for p in formats):
        return REASON_FORMAT.format(kind=kind, format=FORMAT[kind])
    labels = labels_of(question)
    tasks = tasks_of(data, runner, tag)

    # 대상 작업에서 폐기된 번호의 질문은 막고 새 번호를 안내한다.
    number = head.group(0).split()[1]
    if tasks and not DROP.match(text):
        gone, fresh = used_numbers(tasks[0], kind)
        if int(number) in gone:
            return REASON_DROPPED.format(kind=kind, number=number, fresh=fresh)

    # 검토 질문은 안 번호 라벨과 조사 문서 검토 보고에 모든 라벨의 비교표 행과 안 블록이 있어야 하고, 확정 라벨 검사에서는 빠진다.
    if review:
        if not labels:
            return REASON_NO_LABELS
        reason = label_gap(labels)
        if reason:
            return reason
        if not tasks:
            return REASON_NO_TASK.format(number=review[1])
        gaps = [report_gap(read(decision_path(t, "research")), review[1], labels) for t in tasks]
        return None if None in gaps else gaps[0]

    # 요구사항과 폐기 질문은 확정 라벨을 요구한다.
    return check_labels(labels, data, runner)


# 질문 도구 호출이 출처 없는 확인 주장이거나 번호 접두 질문의 형식, 검토 보고, 라벨, 폐기 번호를 어기면 사유를 반환한다.
def check_question(data: dict, runner: str = "claude") -> str | None:
    # 질문 입력에 출처 없는 확인 주장이 있으면 막는다.
    text = input_text(data.get("tool_input") or {})
    claims = unsourced_claims(text, text)
    if claims:
        return REASON_SOURCE.format(sentences=" / ".join(claims[:3]))

    # 질문마다 번호 접두 질문 검사를 한다.
    for question in questions_of(data.get("tool_input") or {}):
        reason = check_one(question, data, runner)
        if reason:
            return reason
    return None
