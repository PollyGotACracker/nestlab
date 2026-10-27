"""질문 도구 호출의 재질문과 안 비교 없는 질문을 차단한다.

호출자: hooks/check_tool_use.py
입력: 질문 도구(AskUserQuestion, request_user_input)의 hook 입력 dict
반환: check_question은 deny 사유 문자열 또는 통과 시 None
한계: 이미 물은 질문은 확정 답을 받은 질문만 센다. 안 비교는 같은 턴 assistant 텍스트와 질문 입력의 문장, 선택지 설명, 미리보기에서 구분선 있는 표나 본문 있는 heading 존재로만 판정한다. 항목 이름은 `검토 <번호>. <이름>` 형식만 인식한다.
"""
from __future__ import annotations

import re

from sub_docs import project_root
from sub_judgment import confirmed, open_tasks, research_pending
from sub_session import answered_questions, read_session, turn

ITEM = re.compile(r"검토 \d+\.[^:\n?]*")
TABLE_OR_HEADING = re.compile(r"(?m)^(?:\|.+\|[ \t]*\n[ \t]*\|[ \t:|-]*-[ \t:|-]*$|#{1,6} \S.*\n\s*\S)")
FINAL = "최종 확인"
REASON_REPEAT = "검토 항목 `{name}`은 이미 질문 도구로 물었습니다. 같은 항목을 다시 묻지 말고 최종 확인 한 번에서 함께 물으십시오."
REASON_COMPARE = "검토 항목 `{name}`의 안 비교(표나 heading)가 이번 턴 응답 본문에 없습니다. 안 비교를 먼저 쓰고 질문하십시오."
REASON_FINAL = "최종 확인 질문은 한 번만 할 수 있습니다."
REASON_NO_RESEARCH = "진행 중인 작업의 최종 조사 원문이 비어 있어 검토 항목 `{name}`을 물을 수 없습니다. 조사 단계를 먼저 끝내십시오."


# 질문 도구 호출 입력의 (header, 질문 문장) 목록이다.
def asked(args: dict) -> list[tuple[str, str]]:
    return [(str(q.get("header") or ""), str(q.get("question") or ""))
            for q in (args or {}).get("questions") or [] if isinstance(q, dict)]


# 질문 도구 호출 입력의 질문 문장, 선택지 설명, 미리보기를 이은 문자열이다.
def input_text(args: dict) -> str:
    parts = []
    for q in (args or {}).get("questions") or []:
        if isinstance(q, dict):
            parts.append(str(q.get("question") or ""))
            parts += [str(o.get(k) or "") for o in q.get("options") or [] if isinstance(o, dict) for k in ("description", "preview")]
    return "\n".join(parts)


# 질문 도구 호출이 재질문이거나 안 비교 없는 질문이면 사유를 반환한다. 최종 확인 한 번은 통과한다.
def check_question(data: dict) -> str | None:
    # 확정 답을 받은 이전 질문과, 현재 턴 assistant 텍스트 및 질문 입력 텍스트를 모은다.
    path = data.get("transcript_path") or ""
    before = [tuple(hq.split("\n", 1)) for hq, a in answered_questions(path) if confirmed(a)]
    shown = "\n".join(e["text"] for e in turn(read_session(path))[2] if e["role"] == "assistant")
    shown += "\n" + input_text(data.get("tool_input") or {})

    for header, question in asked(data.get("tool_input") or {}):
        # 최종 확인은 한 번만 통과한다.
        if header == FINAL:
            if any(h == FINAL for h, _ in before):
                return REASON_FINAL
            continue

        # 항목 이름이 없는 질문은 검사하지 않는다.
        m = ITEM.search(f"{header}\n{question}")
        if not m:
            continue
        name = m.group(0).strip()

        # 진행 중인 작업의 조사 원문이 비어 있으면 조사 전 질문이므로 막는다.
        if research_pending(open_tasks(project_root(data.get("cwd")))):
            return REASON_NO_RESEARCH.format(name=name)

        # 같은 항목 이름으로 이미 띄운 질문이 있으면 막는다.
        if any(name in f"{h}\n{q}" and h != FINAL for h, q in before):
            return REASON_REPEAT.format(name=name)

        # 같은 턴 응답에 안 비교가 없으면 막는다.
        if not TABLE_OR_HEADING.search(shown):
            return REASON_COMPARE.format(name=name)
    return None
