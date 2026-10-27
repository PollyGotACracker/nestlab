"""서브에이전트 호출 입력을 문서 경로와 식별자로 제한한다.

호출자: 
- hooks/check_tool_use.py
- hooks/check_tool_use/sub_approval.py(LEAK, session_leak)
입력: hook 입력 dict, 하위 요청 본문, runner 이름
반환: 
- check_input, session_leak은 deny 사유 문자열(session_leak은 해당 줄) 또는 통과 시 None
- is_format, is_refute_format, is_main은 bool
"""
from __future__ import annotations

import re

import sub_docs
from sub_call import DOCUMENTER, REFUTE_MARKER, RESEARCHER, REVIEWER, ROLES, WORKER_REVIEWER, call_info
from sub_role import resolve_role
from sub_write import read

# 둘째 갈래는 임시 경로(log/state)이다. 기존 로그 삭제 뒤 제거한다.
DOC_LINE = re.compile(r"^(?:명세|입력|조사|반증) 문서: (log/(?:task|state)/[\w.\-/]+\.md)\s*$", re.M)
INPUT_LINE = re.compile(r"^(?:명세|입력) 문서: (?:log/task/[\w.\-]+/spec|log/state/[\w.\-]+-input)\.md$")
RESEARCH_LINE = re.compile(r"^조사 문서: (?:log/task/[\w.\-]+/research|log/state/[\w.\-]+-research)\.md$")
REFUTE_LINE = re.compile(r"^반증 문서: log/task/[\w.\-]+/refute\.md$")
ID_LINE = re.compile(r"^항목: .+$")
KIND_LINE = re.compile(r"^작업 종류: .+$")


# 호출한 쪽이 메인 세션이면 True이다. Codex는 항상 True, Claude는 agent_id가 없을 때 True이다.
def is_main(data: dict, target: str, runner: str) -> bool:
    if runner == "codex":
        return True
    return not data.get("agent_id")


# prompt가 대상별 입력 형식이면 True이다. target이 없으면 경로 줄이 1개 이상인 형식 모양만 확인한다.
def is_format(prompt: str, target: str = "") -> bool:
    # 빈 줄과 `작업 종류` 줄을 뺀 줄을 구한다.
    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
    if lines and KIND_LINE.match(lines[0]):
        lines = lines[1:]

    # 대상 역할별 입력 형식을 판정한다.
    if target in RESEARCHER:
        return (bool(lines) and bool(RESEARCH_LINE.match(lines[0]))
            and all(ID_LINE.match(ln) for ln in lines[1:]))
    if target in WORKER_REVIEWER | DOCUMENTER:
        return len(lines) == 1 and bool(INPUT_LINE.match(lines[0]))
    return any(INPUT_LINE.match(ln) for ln in lines)


# prompt가 `작업 종류: 반증` 한 줄과 반증 문서 경로 한 줄이면 True이다.
def is_refute_format(prompt: str) -> bool:
    lines = [line.strip() for line in prompt.splitlines() if line.strip()]
    return len(lines) == 2 and lines[0] == REFUTE_MARKER and bool(REFUTE_LINE.match(lines[1]))


LEAK = "서브에이전트 입력과 전달되는 문서에서 사건 문서 경로와 세션 기록 정보(경로 및 세션 id)를 제거하십시오. 해당 부분은 {leak}입니다."

# text와 text가 가리키는 작업 문서(조사 단계 원문 절 제외)에서 기록 정보 형식이나 주어진 세션 id(전체 및 앞 8자)가 든 첫 줄이다.
def session_leak(text: str, cwd: str | None, *session_ids: str | None) -> str | None:
    # 검사할 세션 id와 본문(요청과 가리킨 문서)을 구한다.
    root = sub_docs.project_root(cwd)
    ids = {i for s in session_ids if s for i in (s, s[:8]) if len(i) >= 8}

    # 가리킨 문서는 조사 단계 원문 절과 반증 회차 기록 절을 빼고, 본문 줄마다 기록 정보와 세션 id를 찾는다.
    docs = [read(root / rel) for rel in DOC_LINE.findall(text)]
    for body in [text] + [d.replace(sub_docs.section(d, "조사 단계 원문"), "").replace(sub_docs.section(d, "회차 기록"), "")
                          for d in docs]:
        leak = sub_docs.record_leak(body)
        if leak:
            return leak
        for line in body.splitlines():
            if any(i in line for i in ids):
                return line.strip()[:120]
    return None


REASON_RESEARCHER_CALL = "researcher는 다른 에이전트를 호출할 수 없습니다."
REASON_REFUTE_FORMAT = "반증 요청은 `작업 종류: 반증` 한 줄과 `반증 문서: log/task/<작업-id>/refute.md` 한 줄만 허용합니다."
REASON_WORKER_FORMAT = "worker, reviewer, documenter 입력은 `작업 종류: <종류>` 한 줄과 `명세 문서: log/task/<작업-id>/spec.md` 한 줄만 허용합니다."
REASON_RESEARCHER_FORMAT = ("researcher 조사 입력은 `작업 종류: 조사` 줄(선택), "
                            "`조사 문서: log/task/<작업-id>/research.md` 한 줄, 이어서 `항목: <검토 항목>` 줄만 허용합니다.")
REASON_INPUT_FORMAT = "서브에이전트 입력 형식이 아닙니다."

# 위반 사유를 반환한다. 통과면 None이다.
def check_input(data: dict, runner: str) -> str | None:
    # 대상 역할 호출만 검사한다.
    target, prompt = call_info(data)
    if target not in ROLES:
        return None

    # 요청 본문과 가리킨 문서에 세션 기록 정보가 섞였는지 확인한다.
    text = "\n".join(v for v in (data.get("tool_input") or {}).values() if isinstance(v, str))
    leak = session_leak(text, data.get("cwd"), data.get("session_id"))
    if leak:
        return LEAK.format(leak=leak)

    # researcher가 서브에이전트를 부르면 막는다.
    if data.get("agent_id") and resolve_role(data) == "researcher":
        return REASON_RESEARCHER_CALL

    # 반증 요청은 반증 형식만 허용한다.
    if target in REVIEWER and any(line.strip() == REFUTE_MARKER for line in prompt.splitlines()):
        return None if is_refute_format(prompt) else REASON_REFUTE_FORMAT

    # 메인 세션이 아닌 호출은 reviewer 외에는 검사하지 않고, 나머지는 대상별 입력 형식을 확인한다.
    if target not in REVIEWER and not is_main(data, target, runner):
        return None
    if is_format(prompt, target):
        return None
    if target in WORKER_REVIEWER | DOCUMENTER:
        return REASON_WORKER_FORMAT
    if target in RESEARCHER:
        return REASON_RESEARCHER_FORMAT
    return REASON_INPUT_FORMAT
