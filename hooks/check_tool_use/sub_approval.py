"""서브에이전트 호출 승인과 승인 명령을 판정한다.

호출자: hooks/check_tool_use.py
입력: hook 입력 dict, 사용자 마지막 메시지, 하위 요청 본문
반환:
- check_agent_call, check_codex_child, approval_reason은 deny 사유 문자열 또는 통과 시 None
- approved, executed_approved는 bool
- user_message는 사용자 마지막 메시지 문자열
한계: Codex spawn_agent는 PreToolUse가 실행되지 않아 하위 thread의 도구 호출 시점에 승인을 확인한다.
"""
from __future__ import annotations

import re

from sub_call import call_info
from sub_input import LEAK, session_leak
from sub_docs import doc, section, task_id
from sub_role import codex_rollout, first_line, first_prompt, parent_thread, resolve_role
from sub_session import read_session, session_path, turn
from sub_write import read

# 사용자 메시지의 마지막이 승인 명령으로 끝날 때만 승인이다.
# 승인 명령 뒤에 오는 문자는 `.`, `!`, `~`만 허용된다.
# 승인 명령은 계획 수정을 의도한 프롬프트와 구별되어야 한다.
APPROVAL = re.compile(r"(?:^|\s)\S*승인\s*[.!~]*\s*\Z")
APPROVED = re.compile(r"^\s*-\s*(?:사용자 )?승인 상태:\s*승인\s*$")
IN_SCOPE = ("작업 종류: 재작업", "작업 종류: 재검증")
# 착수 승인 뒤 조사와 반증은 사용자 대기 없이 이어진다(상태 문서 착수 승인 범위가 승인일 때).
RESEARCH_SCOPE = ("작업 종류: 조사", "작업 종류: 반증")
NEGATION = ("안", "못")


# (사용자 마지막 메시지, 그 직전 assistant 응답)이다.
def last_user(path: str | None) -> tuple[str, str]:
    user, shown, _ = turn(read_session(path or ""))
    return user, shown


# 사용자 마지막 메시지를 구한다. Codex 하위 thread는 부모 rollout의 메시지이다.
def user_message(data: dict, runner: str) -> str:
    path = data.get("transcript_path") or session_path(data, runner)
    parent = parent_thread(path)
    if parent:
        path = codex_rollout(parent)
    return last_user(path)[0]


# 사용자 메시지의 비어 있지 않은 마지막 줄이 승인 명령이면 True이다. 부정어가 바로 앞에 오면 False이다.
def approved(user: str) -> bool:
    lines = [line for line in user.splitlines() if line.strip()]
    if not lines:
        return False
    words = lines[-1].split()
    if len(words) >= 2 and words[-2] in NEGATION:
        return False
    return bool(APPROVAL.search(lines[-1]))


# 상태 문서의 승인 절(기본은 실행 승인 범위)에 승인 상태가 승인으로 기록돼 있으면 True이다.
def executed_approved(task: str | None, cwd: str | None = None, title: str = "실행 승인 범위") -> bool:
    body = section(read(doc(task, "state", cwd)), title) if task else ""
    return any(APPROVED.match(line) for line in body.splitlines())


REASON_APPROVAL = (
    "사용자 마지막 메시지에 승인 명령이 없습니다. 작업 전 사용자 승인을 받으십시오. "
    "사용자가 모든 사항을 검토한 상태라면 사용자에게 승인 명령을 안내하십시오. "
    "사용자의 검토 없이 작업을 시도한 경우 `nestlab-log-incident`, `nestlab-rule` 스킬을 호출해 그 절차대로 수행하십시오."
)

# 사용자 승인 명령이나 승인 범위 예외가 없으면 deny 사유를 반환한다.
def approval_reason(user: str, prompt: str, cwd: str | None = None) -> str | None:
    # 사용자 승인이 이미 있으면 사유 없이 통과시킨다.
    if approved(user):
        return None

    # 재작업과 재검증은 실행 승인, 조사와 반증은 착수 승인이 기록돼 있으면 허용한다.
    first = next((line.strip() for line in prompt.splitlines() if line.strip()), "")
    if first in IN_SCOPE and executed_approved(task_id(prompt), cwd):
        return None
    if first in RESEARCH_SCOPE and executed_approved(task_id(prompt), cwd, "착수 승인 범위"):
        return None
    return REASON_APPROVAL


# Claude Agent 호출 검사이다.
def check_agent_call(data: dict) -> str | None:
    # director의 호출만 검사한다.
    if data.get("agent_id") or resolve_role(data) != "director":
        return None

    # 승인 여부를 판정한다.
    _, prompt = call_info(data)
    user, _ = last_user(data.get("transcript_path"))
    return approval_reason(user, prompt, data.get("cwd"))


# Codex 하위 thread의 도구 호출에서 부모 director의 승인을 검사한다.
def check_codex_child(data: dict) -> str | None:
    # 하위 thread 요청에 세션 기록 정보가 섞였는지 확인한다.
    path = data.get("transcript_path")
    leak = session_leak(first_prompt(path), data.get("cwd"), parent_thread(path), data.get("session_id"))
    if leak:
        return LEAK.format(leak=leak)

    # 부모 thread의 사용자 마지막 메시지로 승인을 판정한다.
    parent = codex_rollout(parent_thread(path))
    if not parent or first_line(parent).get("agent_role"):
        return None  # 부모가 메인 thread가 아님
    user, _ = last_user(parent)
    return approval_reason(user, first_prompt(path), data.get("cwd"))
