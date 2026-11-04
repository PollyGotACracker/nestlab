"""조사 문서 원문 보존, 조사 문서 확인 상태, 검토 진행 상태를 검사한다.

호출자:
- hooks/check_tool_use/sub_scope.py(check_raw, check_judgment, check_stage, early_items, unanswered)
- hooks/check_workflow_response.py(open_tasks, review_items, status_of, unanswered)
입력: 상태 문서와 조사 문서의 새 내용과 이전 내용, 질문 도구 호출 입력, 세션 기록 경로
반환:
- check_raw, check_judgment, check_stage는 deny 사유 문자열 또는 통과 시 None
- review_items는 {항목 이름: 본문} dict
- open_tasks는 상태 문서 경로 목록
- unanswered는 아직 답을 받지 않은 조사 문서 `검토 <번호>.` 보고 이름 목록
- early_items는 명세 문서 쓰기 전에 끝나지 않은 검토 이름 목록
한계: 검토 보고 절이 없는 조사 문서는 기록된 검토 필요 항목만 검사한다.
"""
from __future__ import annotations

import re
from pathlib import Path

from sub_docs import decision_path, review_done, section, sibling, titles
from sub_session import answered_questions
from sub_workflow import Document, approval, unanswered_titles
from sub_write import read

RAW = ("최종 조사 원문", "최종 반증 원문")
ROUND = re.compile(r"^#{1,6} (\d+회차 반증 (?:요청|원문))\s*$", re.M)
ITEM = re.compile(r"(?m)^- (.+)$")
OPEN = ("미개시", "진행")
RAW_EMPTY = re.compile(r"^(`{3,})[^\n]*\n\s*(?:기록 없음|<[^<>\n]*>)?\s*\n\1$")
DONE = "검토 완료"
ASK = "사용자 검토 필요"


# 회차 반증 블록의 (제목, 본문) 목록이다.
def rounds(text: str) -> list[tuple[str, str]]:
    return [(t, section(text, t)) for t in ROUND.findall(text)]


# 원문 칸이 비었거나 자리표시 및 `기록 없음`이면 True이다.
def raw_empty(text: str, title: str) -> bool:
    body = section(text, title).strip()
    return not body or bool(RAW_EMPTY.match(body))


# 진행 중인 작업이 있고 그 작업 모두 조사 문서의 최종 조사 원문이 비어 있으면 True이다.
def research_pending(tasks: list[Path]) -> bool:
    return bool(tasks) and all(raw_empty(read(sibling(p, "research")), "최종 조사 원문") for p in tasks)


REASON_RAW ="조사 문서의 최종 원문 칸은 결과 저장 hook만 씁니다."
REASON_ROUND = "조사 문서의 회차 반증 블록은 결과 저장 hook만 씁니다."

# 조사 문서와 반증 문서 쓰기에서 결과 저장 hook 전용 칸 변경을 막는다.
# blank_ok가 참이면 원문 칸이 모두 비고 회차 블록이 0개인 빈 양식 전체 덮어쓰기는 허용한다.
def check_raw(new: str | None, old: str, blank_ok: bool = False) -> str | None:
    # 반증 문서를 빈 양식 전체로 덮어쓰는 쓰기는 허용한다.
    if blank_ok and new is not None and not rounds(new) and all(raw_empty(new, t) for t in RAW):
        return None

    # 최종 원문 칸이 바뀌면 막는다. 새 문서의 빈 원문 칸은 허용한다.
    changed = [t for t in RAW if new is not None and section(new, t) != section(old, t)]
    if changed and not (not old and all(raw_empty(new, t) for t in changed)):
        return REASON_RAW

    # 회차 반증 블록이 바뀌면 막는다.
    if new is not None and rounds(new) != rounds(old):
        return REASON_ROUND
    return None


# 상태 문서 검토 필요 절의 (항목 이름, 본문)이다. 항목은 0칸 들여쓴 `- ` 줄이다.
def review_items(text: str) -> dict[str, str]:
    body = section(text, "검토 필요")
    starts = list(ITEM.finditer(body))
    return {m.group(1).strip(): body[m.end():(starts[i + 1].start() if i + 1 < len(starts) else len(body))]
            for i, m in enumerate(starts)}


# 검토 필요 항목 본문의 `상태` 칸 값을 반환한다. 없으면 빈 문자열이다.
def status_of(body: str) -> str:
    m = re.search(r"^\s*- 상태:\s*(.+?)\s*$", body, re.M)
    return m.group(1) if m else ""


# 확정 선택지 라벨. 라벨에 이 낱말이 있고 제외 낱말이 없으면 확정 답이다.
CONFIRM = ("확정", "채택", "승인", "확인", "동의")
NOT_CONFIRM = ("No preference", "보류", "기각", "미확정")


# 질문 답이 확정 선택지이면 True이다.
def confirmed(answer: str) -> bool:
    return any(w in answer for w in CONFIRM) and not any(w in answer for w in NOT_CONFIRM)


# 질문 문장에 key가 있고 답이 확정 선택지인 질문이 세션 기록에 있으면 True이다.
def confirmed_by(transcript: str, key: str) -> bool:
    return any(key in q and confirmed(a) for q, a in answered_questions(transcript or ""))


REASON_CONFIRM = "조사 문서 확인 상태를 확인으로 쓰기 전에 {items}의 검토 질문 확정 답이 경과 문서에 기록돼야 합니다."
REASON_BEFORE = "조사 문서 확인 상태를 쓰기 전에 상태 문서의 착수 승인이 기록돼야 합니다({error})."
CHECKED = re.compile(r"^\s*(?:- )?확인 상태:\s*(\S+)", re.M)

# 조사 문서 확인 상태가 새로 확인이 되면 앞 단계(착수 승인)와 검토 질문 확정 답 기록을 요구한다.
def check_judgment(path: Path, new: str | None, old: str, transcript: str) -> str | None:
    # 확인 상태가 확인으로 바뀐 쓰기만 검사한다.
    pick = lambda text: (CHECKED.search(text or "") or [None, ""])[1]
    if new is None or pick(new) != "확인" or pick(old) == "확인":
        return None

    # 상태 문서의 착수 승인 칸이 채워졌는지 단계 함수로 확인한다.
    state = sibling(path, "state")
    try:
        approval(Document(read(state)), "착수 승인 범위")
    except ValueError as error:
        return REASON_BEFORE.format(error=error)

    # 검토 질문 확정 답이 모두 기록됐는지 확인한다. 경과 문서가 없으면 건너뛴다.
    try:
        left = early_items(state) if state.is_file() else []
    except ValueError as error:
        return str(error)
    return REASON_CONFIRM.format(items=", ".join(left[:3])) if left else None


REASON_STOP_TYPE = ("중단 유형 칸을 쓸 수 없습니다. `중단 유형`이 든 질문 도구 질문의 확정 선택지 답이 세션 기록에 없습니다.")
REASON_STOP_APPROVAL = "중단 유형 칸을 쓰기 전에 상태 문서의 실행 승인이 기록돼야 합니다({error})."
STOP_TYPE = re.compile(r"^\s*- 중단 유형:[ \t]*(.*?)\s*$", re.M)


# 칸 값이 자리표시가 아닌 실제 값이면 True이다.
def stop_filled(value: str) -> bool:
    return bool(value) and not value.startswith("<")


# 상태 문서의 중단 유형 칸 변경이 실행 승인과 질문 확정 답을 갖췄는지 검사한다.
def check_stage(path: Path, new: str | None, old: str, transcript: str) -> str | None:
    if new is None:
        return None

    # 중단 유형 칸이 새로 쓰이거나 바뀌면 실행 승인과 질문 확정 답을 요구한다.
    kinds = lambda text: [v for v in STOP_TYPE.findall(text) if stop_filled(v)]
    if kinds(new) != kinds(old) and kinds(new):
        try:
            approval(Document(new), "실행 승인 범위")
        except ValueError as error:
            return REASON_STOP_APPROVAL.format(error=error)
        if not confirmed_by(transcript, "중단 유형"):
            return REASON_STOP_TYPE
    return None


# 진행 중인 작업의 경과 문서이다. log/state의 기존 문서도 포함한다(임시, 기존 로그 삭제 뒤 제거).
def open_tasks(root: Path) -> list[Path]:
    log = root / "log"
    out = []
    for path in sorted(log.glob("task/*/status.md")) + sorted(log.glob("state/*.md")):
        if path.name.endswith(("-input.md", "-research.md")):
            continue
        m = re.search(r"^- 작업 상태:\s*(\S+)", read(path), re.M)
        if m and m.group(1) in OPEN:
            out.append(path)
    return out


# 조사 문서 검토 보고의 제목 중 경과 문서에 done 상태로 없는 것의 목록이다. done은 끝난 상태 값이다.
def unanswered(status: Path, done: tuple[str, ...]) -> list[str]:
    return unanswered_titles(read(decision_path(status)), read(decision_path(status, "research")), done)


# 명세 문서 쓰기 전에 끝나지 않은 검토 이름 목록이다. 허용 값을 읽지 못하면 ValueError이다.
def early_items(status: Path) -> list[str]:
    # 검토 필요 항목과 답이 없는 검토 보고를 선별한다.
    done = review_done()
    out = [name for name, item in review_items(read(decision_path(status))).items() if status_of(item) not in done]
    return out + unanswered(status, done)
