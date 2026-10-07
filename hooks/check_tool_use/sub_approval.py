"""하위 에이전트 호출 승인과 승인 명령 판정이다.

- Claude: director가 Agent를 호출할 때 사용자 마지막 메시지가 승인 명령(APPROVAL)으로 끝나지 않으면 deny한다.
- Codex: spawn_agent에는 PreToolUse가 실행되지 않는다(openai/codex#49736).
  그래서 하위 thread의 도구 호출마다 부모(director) thread의 사용자 마지막 메시지를 확인해 deny한다.
- 예외: 하위 요청 첫 줄이 `작업 종류: 재작업`이고 입력 문서 `실행 승인 범위`의 `승인 상태`가 `승인`이면 허용한다.
- researcher가 reviewer를 부르는 하위 호출은 검사하지 않는다.
- Claude Agent 호출은 대상 역할의 작업 ID를 ACTIVE/<session_id>.json에 남긴다(save_agent_result가 쓴다).
"""
from __future__ import annotations

import json
import re

from sub_call import call_info
from sub_docs import ACTIVE, doc, section, task_id
from sub_role import codex_rollout, first_line, first_prompt, parent_thread, resolve_role, to_role
from sub_session import read_session, turn
from sub_write import read

# 사용자 메시지의 마지막 줄이 승인 명령으로 끝날 때만 승인이다.
# 승인 명령은 계획 수정을 의도한 프롬프트와 구별되어야 한다.
# 동사 바로 뒤에 어미가 와야 하므로 `진행 안 해`·`구현하지 마`는 승인이 아니다.
APPROVAL = re.compile(
    r"(?:^|\s)(?:승인|시작|진행|그래|응"
    r"|(?:시작|진행|구현|작성|검수|적용|실행|반영)\s?(?:해|해줘|해 줘|해라|하세요|해 주세요|합니다))"
    r"\s*[.!~]*\s*$")
APPROVED = re.compile(r"^\s*-\s*승인 상태:\s*승인\s*$")
REWORK = "작업 종류: 재작업"
NEGATION = ("안", "못")


def last_user(path: str | None) -> tuple[str, str]:
    """(사용자 마지막 메시지, 그 직전 assistant 응답)이다."""
    user, shown, _ = turn(read_session(path or ""))
    return user, shown


def approved(user: str) -> bool:
    """사용자 메시지의 비어 있지 않은 마지막 줄이 승인 명령이면 True이다. 부정어가 바로 앞에 오면 False이다."""
    lines = [line for line in user.splitlines() if line.strip()]
    if not lines:
        return False
    words = lines[-1].split()
    if len(words) >= 2 and words[-2] in NEGATION:
        return False
    return bool(APPROVAL.search(lines[-1]))


def executed_approved(task: str | None) -> bool:
    body = section(read(doc(task, "input")), "실행 승인 범위") if task else ""
    return any(APPROVED.match(line) for line in body.splitlines())


def approval_reason(user: str, prompt: str) -> str | None:
    if approved(user):
        return None
    first = next((line.strip() for line in prompt.splitlines() if line.strip()), "")
    if first == REWORK and executed_approved(task_id(prompt)):
        return None
    return "사용자 마지막 메시지에 승인이 없습니다. 하위 에이전트 호출 전에 승인을 받으십시오."


def remember(data: dict, target_role: str, prompt: str) -> None:
    tid = task_id(prompt)
    if not tid:
        return
    path = ACTIVE / f"{data.get('session_id')}.json"
    ACTIVE.mkdir(parents=True, exist_ok=True)
    active = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    active[target_role] = tid  # ponytail: 역할당 작업 1개. 같은 역할 병렬 작업이 생기면 agent_id 키로 바꾼다.
    path.write_text(json.dumps(active, ensure_ascii=False), encoding="utf-8")


def check_agent_call(data: dict) -> str | None:
    """Claude Agent 호출 검사이다."""
    if data.get("agent_id") or resolve_role(data) != "director":
        return None
    target, prompt = call_info(data)
    role = to_role(target)
    if role:
        remember(data, role, prompt)
    user, _ = last_user(data.get("transcript_path"))
    return approval_reason(user, prompt)


def check_codex_child(data: dict) -> str | None:
    """Codex 하위 thread의 도구 호출에서 부모 director의 승인을 검사한다."""
    path = data.get("transcript_path")
    parent = codex_rollout(parent_thread(path))
    if not parent or first_line(parent).get("agent_role"):
        return None  # 부모가 director 메인 thread가 아니다(e.g. researcher가 부른 reviewer).
    user, _ = last_user(parent)
    return approval_reason(user, first_prompt(path))
