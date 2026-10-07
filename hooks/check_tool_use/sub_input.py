"""하위 에이전트 호출 입력을 문서 경로와 식별자로 제한한다.

문서 입력 형식 (documenter는 상태 문서 1개와 입력 문서 0개 또는 1개를 받는다)
    입력 문서: log/state/<작업-id>-input.md   (worker·reviewer 대상, documenter 대상은 0개 또는 1개)
    상태 문서: log/state/<작업-id>.md         (documenter 대상)
    조사 문서: log/state/<작업-id>-research.md (researcher 대상, 이 줄 하나만)
    절: <절 식별자>      (0회 이상)
    항목: <항목 식별자>  (0회 이상)
- documenter·worker·reviewer·researcher와 해당 별칭만 검사한다. 다른 에이전트는 통과한다.
- worker·reviewer 일반 검수는 `작업 종류` 줄과 입력 문서 경로 한 줄만 허용한다.
- 첫 줄이 `작업 종류: <값>`이면 그 줄을 뺀 나머지 줄로 입력 형식을 검사한다.
- reviewer 반증 입력은 `작업 종류: 반증` 한 줄과 `주장:`·`증거:`·`출처:`·`판정 기준:`·`원문 발췌:` 필드만 허용한다.
  필드 값은 한 줄이고 여러 줄 발췌는 JSON 문자열의 줄바꿈 이스케이프 등으로 표현한다.
  표식은 정확히 1개이며 비어 있지 않은 `주장:` 필드가 1개 이상 있어야 한다.
  다른 입력 형식·대화·도구 로그 줄을 섞으면 차단한다.
- 대상이 documenter이고 형식이 아니면 기록·문서 요청이므로 통과한다.
- Claude 하위 호출은 reviewer 대상일 때만 입력 경계를 검사한다.
- Codex는 호출자의 미관측 필드를 추정하지 않고 대상과 요청 본문의 명시적 표식으로 검사한다.

hook 입력 필드 확인 상태
- 확인: Claude Agent 도구 이름 `Agent`, tool_input의 subagent_type·prompt, 서브에이전트 안 hook 입력의 agent_id(공식 hooks 문서).
- 확인: Codex 협업 도구 이름 `spawn_agent`, rollout JSONL 구조(response_item, payload.role·content).
- 확인: Codex 0.159.2에서 spawn_agent에는 PreToolUse가 실행되지 않는다(openai/codex#49736). 수정되면 그대로 동작한다.
- 미확인: Codex spawn_agent 인자의 역할·본문 필드명(agent_type·message로 가정).
"""
from __future__ import annotations

import re

from sub_call import DOCUMENTER, REFUTE_MARKER, RESEARCHER, REVIEWER, ROLES, WORKER_REVIEWER, call_info, refute_mode
from sub_role import resolve_role

INPUT_LINE = re.compile(r"^입력 문서: log/state/[\w.\-]+-input\.md$")
STATE_LINE = re.compile(r"^상태 문서: log/state/[\w.\-]+(?<!-input)\.md$")
RESEARCH_LINE = re.compile(r"^조사 문서: log/state/[\w.\-]+-research\.md$")
ID_LINE = re.compile(r"^(절|항목): .+$")
KIND_LINE = re.compile(r"^작업 종류: .+$")
REFUTE_LINE = re.compile(r"^(주장|증거|출처|판정 기준|원문 발췌): \S.*$")


def is_main(data: dict, target: str, runner: str) -> bool:
    if runner == "codex":
        return True
    return not data.get("agent_id")


def is_format(prompt: str, target: str = "") -> bool:
    """prompt가 대상별 입력 형식이면 True이다. target이 없으면 경로 줄이 1개 이상인 형식 모양만 확인한다."""
    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
    if lines and KIND_LINE.match(lines[0]):
        lines = lines[1:]
    if target in RESEARCHER:
        return (bool(lines) and bool(RESEARCH_LINE.match(lines[0]))
            and all(ID_LINE.match(ln) and ln.startswith("항목: ") for ln in lines[1:]))
    n_in = sum(bool(INPUT_LINE.match(ln)) for ln in lines)
    n_st = sum(bool(STATE_LINE.match(ln)) for ln in lines)
    if not all(INPUT_LINE.match(ln) or STATE_LINE.match(ln) or ID_LINE.match(ln) for ln in lines):
        return False
    if target in WORKER_REVIEWER:
        return len(lines) == 1 and (n_in, n_st) == (1, 0)
    if target in DOCUMENTER:
        return n_st == 1 and n_in <= 1
    return n_in + n_st >= 1 if not target else (n_in, n_st) == (0, 1)


def is_refute_format(prompt: str) -> bool:
    lines = [line.strip() for line in prompt.splitlines() if line.strip()]
    return (refute_mode(prompt) and any(line.startswith("주장: ") for line in lines)
            and all(line == REFUTE_MARKER or REFUTE_LINE.fullmatch(line) for line in lines))


def check_input(data: dict, runner: str) -> str | None:
    """위반 사유를 반환한다. 통과면 None이다."""
    target, prompt = call_info(data)
    if target not in ROLES:
        return None
    if data.get("agent_id") and resolve_role(data) == "researcher" and target not in REVIEWER:
        return "researcher는 reviewer만 호출할 수 있습니다."
    if target in REVIEWER and any(line.strip() == REFUTE_MARKER for line in prompt.splitlines()):
        return None if is_refute_format(prompt) else "반증 요청은 `작업 종류: 반증` 한 줄과 주장·증거·출처·판정 기준·원문 발췌 필드만 허용합니다."
    if target not in REVIEWER and not is_main(data, target, runner) or target in DOCUMENTER and not is_format(prompt):
        return None
    if is_format(prompt, target):
        return None
    if target in WORKER_REVIEWER:
        return "worker·reviewer 일반 검수 입력은 `입력 문서: log/state/<작업-id>-input.md` 한 줄만 허용합니다."
    if target in RESEARCHER:
        return "researcher 조사 입력은 `조사 문서: log/state/<작업-id>-research.md` 한 줄만 허용합니다."
    return "서브에이전트 입력은 `상태 문서: log/state/<작업-id>.md`와 `절:`·`항목:` 줄만 허용합니다."
