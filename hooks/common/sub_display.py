"""응답 본문의 확정안(`## 확정안 <번호>. <요구사항 이름>` 절)에서 칸 줄 원문을 읽는 공용 모듈이다.

호출자:
- hooks/check_tool_use/sub_question.py(확정 질문과 확정안 원문 대조)
- hooks/check_workflow_response/sub_record.py(확정 답의 확정안 기록)
입력: assistant 응답 본문 문자열 또는 대화 기록 항목 목록
반환: 확정안 dict 목록(`number`, `name`, `fields`)과 확정안 원문 문자열
한계: 줄 형식 `- 칸 이름: 값`만 읽고 값의 의미는 판정하지 않는다. 코드 블록 안의 줄은 읽지 않는다.
"""
from __future__ import annotations

import re

HEAD = re.compile(r"^## 확정안 (\d+)\. (.+?)\s*$")
SECTION = re.compile(r"^## \S")
FIELD = re.compile(r"^- ([^:\n]+):[ \t]*(.*?)\s*$")
FENCE = re.compile(r"^\s*(?:```|~~~)")
# 질문 대조와 명세 기록에 필요한 확정안 칸이다.
CELLS = ("문제", "목표", "기대 결과", "한계", "잠재 위험")


# 응답 본문의 확정안 목록이다. 확정안마다 번호, 이름과 칸 이름별 첫 줄 값 dict를 담는다.
def parse_confirmed(text: str) -> list[dict]:
    out, current, fence = [], None, False
    for line in (text or "").splitlines():
        if FENCE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        head = HEAD.match(line)
        if head:
            current = {"number": int(head[1]), "name": head[2], "fields": {}}
            out.append(current)
        elif SECTION.match(line):
            current = None
        elif current is not None and (field := FIELD.match(line)):
            current["fields"].setdefault(field[1], field[2])
    return [c for c in out if all(c["fields"].get(cell) for cell in CELLS)]


# 대화 기록 항목에 있는 모든 assistant 응답의 확정안을 오래된 순서로 모은 목록이다.
def session_confirmed(entries: list[dict]) -> list[dict]:
    return [c for e in entries if e["role"] == "assistant" for c in parse_confirmed(e["text"])]


# 문장 끝 마침표와 공백 차이를 없앤 비교용 문자열이다.
def norm(value: str) -> str:
    return " ".join(str(value).split()).rstrip(".")


# 확정안 하나의 번호, 이름, 목표와 기대 결과 줄 원문이다. 차단 사유에 원문을 보여 주는 용도이다.
def confirmed_lines(confirmed: dict) -> str:
    return "\n".join([f"## 확정안 {confirmed['number']}. {confirmed['name']}", f"- 목표: {confirmed['fields']['목표']}",
                      f"- 기대 결과: {confirmed['fields']['기대 결과']}"])


# 번호, 이름과 목표가 같은 가장 최근 확정안이다. 없으면 None이다.
def find_confirmed(items: list[dict], number: int, name: str, goal: str) -> dict | None:
    return next((c for c in reversed(items) if c["number"] == number and norm(c["name"]) == norm(name)
                 and norm(c["fields"]["목표"]) == norm(goal)), None)
