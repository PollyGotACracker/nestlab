#!/usr/bin/env python3
"""PreToolUse hook: 서브에이전트 호출과 쓰기 도구 사용을 검사한다.

호출자:
- Claude Code와 Codex의 PreToolUse hook 설정(shared/settings)
- 호출 명령은 `python check_tool_use.py --runner <claude|codex>`
입력: hook 입력 JSON을 stdin으로 받는다.
반환:
- 위반이면 stdout에 `hookSpecificOutput`(`permissionDecision: deny`, `permissionDecisionReason`) JSON을 출력한다.
- 승인 창을 띄우는 `ask`는 출력하지 않는다.
- 통과하면 아무것도 출력하지 않는다.
- check()는 사유 문자열 또는 None을 반환한다.
한계: 검사 오류는 쓰기 도구(Edit, Write, NotebookEdit, apply_patch, 쓰기 Bash)만 deny하고 나머지는 통과한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 같은 이름 폴더의 모듈과 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))

from allow_approved_request import in_allow_list
from sub_approval import approved, check_agent_call, check_codex_child, user_message
from sub_call import is_spawn
from sub_input import check_input
from sub_role import main_session, resolve_role
from sub_path import check_paths, norm
from sub_scope import check_write, kickoff_missing, log_bash_reason, reads_skill, write_bash_free
from sub_question import check_question
from sub_session import QUESTION_TOOLS, session_path
from sub_write import (REASON_CD, REASON_INLINE, RETRY, SHELLS, bash_command, bash_write_reason, command_parts, description_reason,
                       git_write_reason, interpreter_kind, is_write_bash, prompt_reason, targets)
from sub_workflow import call_reason


# 사유를 PreToolUse deny 출력 dict로 만든다.
def deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


REASON_FORK = 'fork_turns를 "none"으로 지정하지 않은 spawn_agent는 대화 맥락을 복제하므로 허용하지 않습니다. ' + RETRY

# Codex spawn_agent가 대화 맥락을 복제하면 차단한다(Claude `Agent(fork)` deny 대응).
def check_fork(data: dict, runner: str) -> str | None:
    if runner != "codex" or data.get("tool_name") == "Agent" or not is_spawn(data.get("tool_name")):
        return None
    if (data.get("tool_input") or {}).get("fork_turns") != "none":
        return REASON_FORK
    return None


REASON_MAIN_BASH = "이 명령은 파일을 바꿉니다. " + RETRY

# 메인 세션의 쓰기 Bash 명령이면 사유를 반환한다. 아니면 None이다.
def main_write_reason(command: str) -> str | None:
    return bash_write_reason(command) or (REASON_MAIN_BASH if is_write_bash(command) else None)


REASON_SCRIPT = "승인 전에는 허용 목록 밖 python, node 스크립트를 실행할 수 없습니다. " + RETRY

# 하위 명령 중 허용 목록 밖 python, node 스크립트(파일, 모듈) 실행이 있으면 사유를 반환한다. 아니면 None이다.
def script_reason(command: str) -> str | None:
    for part in command_parts(command):
        if interpreter_kind(part) == "script" and not in_allow_list(part):
            return REASON_SCRIPT
    return None


# Codex 셸 호출의 workdir가 세션 작업 위치와 다르면 REASON_CD를 반환한다. 아니면 None이다.
def workdir_reason(data: dict) -> str | None:
    workdir, cwd = (data.get("tool_input") or {}).get("workdir"), data.get("cwd")
    if not workdir or not cwd:
        return None
    return REASON_CD if norm(str(workdir), Path(cwd)) != norm(str(cwd), Path(cwd)) else None


REASON_SUB_SPAWN = "서브에이전트는 다른 에이전트를 호출할 수 없습니다. " + RETRY
REASON_KICKOFF = "director는 nestlab-kickoff 스킬을 호출하기 전에 Skill 외 도구를 호출할 수 없습니다. " + RETRY

# 위반 사유를 반환한다. 통과면 None이다.
def check(data: dict, runner: str) -> str | None:
    # Codex는 기록 경로가 없으면 세션 id로 찾는다.
    if runner == "codex" and not data.get("transcript_path") and data.get("session_id"):
        data = {**data, "transcript_path": session_path(data, runner)}

    # director 메인 세션은 nestlab-kickoff 호출 전에 Skill 외 도구를 쓸 수 없다. Codex는 kickoff SKILL.md를 읽는 명령만 통과한다.
    if data.get("tool_name") != "Skill" and kickoff_missing(data, runner):
        if not (runner == "codex" and reads_skill(data.get("tool_input"), "nestlab-kickoff")):
            return REASON_KICKOFF

    # 서브에이전트 세션의 Agent 호출은 막는다.
    if not main_session(data) and is_spawn(data.get("tool_name")):
        return REASON_SUB_SPAWN

    # 서브에이전트 호출은 승인, fork, 입력 형식, 단계 선행 조건 순으로 검사한다.
    if is_spawn(data.get("tool_name")):
        reason = check_agent_call(data) if data.get("tool_name") == "Agent" else None
        return reason or check_fork(data, runner) or check_input(data, runner) or call_reason(data)

    # 질문 도구는 재질문과 안 비교 유무를 검사한다.
    if data.get("tool_name") in QUESTION_TOOLS:
        return check_question(data)

    # 셸 도구의 cd, git -c, workdir는 모든 세션과 승인 상태에서 가장 먼저 막는다.
    shell = data.get("tool_name") in SHELLS
    command = bash_command(data) if shell else ""
    if shell:
        reason = prompt_reason(command) or workdir_reason(data)
        if reason:
            return reason

    # 경로 접근은 모든 세션에 검사한다.
    role = resolve_role(data)
    reason = check_paths(data, runner)
    if reason:
        return reason

    # 셸 도구는 쓰기 명령과 description을 검사한다. director 메인 세션은 쓰기 Bash를 모두 막고, 그 밖의 메인 세션은 bash_write_reason을 받고,
    # 사용자 승인 명령이 있는 assistant와 실행 승인 뒤 worker, documenter는 쓰기 판정 대신 log/ 경로만 막는다.
    if shell:
        main = main_session(data)
        turn_approved = approved(user_message(data, runner))
        if main and role == "director":
            # 승인 턴의 쓰기 Bash는 보안, 외부 경로, git 비가역 작업만 막는다(앞 검사와 설정 deny 규칙이 맡는다).
            write = None if turn_approved else main_write_reason(command)
        elif main and role == "assistant" and turn_approved:
            write = log_bash_reason(data)
        elif main:
            write = bash_write_reason(command)
        elif write_bash_free(data, role):
            write = log_bash_reason(data)
        else:
            write = bash_write_reason(command)

        # 승인 턴의 메인 세션은 쓰기 호출이 있는 인라인 코드를 막지 않는다.
        if main and turn_approved and write == REASON_INLINE:
            write = None

        # 승인 전 메인 세션은 git 쓰기 명령과 허용 목록 밖 스크립트 실행도 막는다.
        before = git_write_reason(command) or script_reason(command) if main and not turn_approved else None
        reason = write or before or description_reason(data, runner)
        if reason:
            return reason

    # Codex 하위 역할의 도구 호출은 부모 승인을 확인한다.
    if runner == "codex" and role not in (None, "director", "assistant"):
        reason = check_codex_child(data)
        if reason:
            return reason

    # 쓰기 도구는 역할별 쓰기 범위를 검사한다.
    paths = targets(data)
    if paths is None:
        return None
    return check_write(data, role, paths, main_session(data), runner)


REASON_ERROR = "쓰기 도구 검사 중 오류가 나 쓰기를 허용하지 않습니다. " + RETRY

# 쓰기 도구이거나 쓰기로 판정된 Bash 호출이면 True를 반환한다.
def is_write_call(data) -> bool:
    if not isinstance(data, dict):
        return False
    name = data.get("tool_name")
    return name in ("Edit", "Write", "NotebookEdit", "apply_patch") or (
        name in SHELLS and is_write_bash(bash_command(data)))


# stdin의 hook 입력을 검사하고 위반이면 deny JSON을 출력한다. 항상 0을 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞추고 인자를 읽는다.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    runner = parser.parse_args().runner

    # 입력을 읽지 못하면 통과하고, 검사 오류는 쓰기 호출만 deny한다.
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        reason = check(data, runner)
    except Exception:
        if is_write_call(data):
            json.dump(deny(REASON_ERROR), sys.stdout, ensure_ascii=False)
        return 0
    if reason:
        json.dump(deny(reason), sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
