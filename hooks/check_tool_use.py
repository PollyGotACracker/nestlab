#!/usr/bin/env python3
"""PreToolUse hook: 서브에이전트 호출과 쓰기 도구 사용을 검사한다.

호출자:
- Claude Code와 Codex의 PreToolUse hook 설정(설정 원본)
- 호출 커맨드는 `python check_tool_use.py --runner <claude|codex>`
입력: hook 입력 JSON을 stdin으로 받는다.
반환:
- 위반이면 stdout에 `hookSpecificOutput`(`permissionDecision: deny`, `permissionDecisionReason`) JSON을 출력한다.
- 승인 창을 띄우는 `ask`는 출력하지 않는다.
- 통과하면 아무것도 출력하지 않는다.
- check()는 사유 문자열 또는 None을 반환한다.
한계:
- 검사 오류는 쓰기 도구(Edit, Write, NotebookEdit, apply_patch, 쓰기 Bash)만 deny하고 나머지는 통과한다.
- 대기 메시지와 같은 턴 재실행 검사는 Claude 메인 세션만 판정한다. Codex Tab 대기 입력은 hook 입력에 정보가 없어 차단하지 못한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 같은 이름 폴더의 모듈과 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))

from allow_approved_request import in_allow_list, task_docs_command
from sub_approval import WRITE_STAGES, approved, check_agent_call, check_codex_child, sync_stage, user_message
from sub_call import is_followup, is_spawn
from sub_docs import project_root
from sub_input import check_input, research_retry_reason
from sub_role import director_main, main_session, resolve_role, project_session
from sub_path import check_paths, norm
from sub_scope import check_write, log_bash_reason, write_bash_free
from sub_question import WAITING_HEAD, check_question, waiting_messages
from sub_session import QUESTION_TOOLS, denied_calls, read_session, session_path
from sub_write import (APPROVAL_RETRY, REASON_CD, REASON_INLINE, RETRY, SHELLS, bash_command, bash_write_reason, command_parts, description_reason,
                       git_write_reason, interpreter_kind, is_write_bash, prompt_reason, targets)
from sub_gate import call_reason


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


REASON_MAIN_BASH = "이 커맨드는 파일을 변경합니다. " + RETRY

# 메인 세션의 쓰기 Bash 커맨드이면 사유를 반환한다. 아니면 None이다.
def main_write_reason(command: str) -> str | None:
    return bash_write_reason(command) or (REASON_MAIN_BASH if is_write_bash(command) else None)


REASON_EDIT_GUIDE = "작업 문서는 Edit 도구로 수정하고, 구현은 담당에게 배정하십시오."

# director 메인 세션의 쓰기 Bash 커맨드이면 모든 단계에서 사유를 반환한다. 허용 목록과 작업 문서 생성기, 전달 도구 커맨드는 통과한다.
def director_write_reason(command: str, cwd: str | Path | None = None) -> str | None:
    from sub_worktree import transfer_command
    if in_allow_list(command, cwd) or transfer_command(command, project_root(cwd)):
        return None
    reason = main_write_reason(command)
    return f"{reason} {REASON_EDIT_GUIDE}" if reason else None


REASON_SCRIPT = "승인 전에는 허용 목록 밖 python, node 스크립트를 실행할 수 없습니다. " + APPROVAL_RETRY

# 하위 커맨드 중 허용 목록 밖 python, node 스크립트(파일, 모듈) 실행이 있으면 사유를 반환한다. 아니면 None이다.
def script_reason(command: str, cwd: str | Path | None = None) -> str | None:
    if in_allow_list(command, cwd):
        return None
    for part in command_parts(command, preserve_quotes=True):
        if re.match(r"^nestlab-task-docs(?:\.cmd)?(?=\s|$)", part):
            if task_docs_command(part, cwd):
                continue
            return REASON_SCRIPT
        # 원래 따옴표와 인자로 단독 호출과 같은 생성기 검사를 적용한다.
        if task_docs_command(part, cwd):
            continue
        if interpreter_kind(part) == "script" and not in_allow_list(part, cwd):
            return REASON_SCRIPT
    return None


# Codex 셸 호출의 workdir가 세션 작업 위치와 다르면 REASON_CD를 반환한다. 아니면 None이다.
def workdir_reason(data: dict) -> str | None:
    workdir, cwd = (data.get("tool_input") or {}).get("workdir"), data.get("cwd")
    if not workdir or not cwd:
        return None
    return REASON_CD if norm(str(workdir), Path(cwd)) != norm(str(cwd), Path(cwd)) else None


REASON_TEST_RUN = "구현 배정 세션은 시험을 실행하지 않습니다. 시험 코드만 작성하고 실행은 검증 담당에게 남기십시오."
TEST_RUN = re.compile(r"^(?:(?:python3?|py)(?:\.exe)?\s+-m\s+(?:unittest|pytest)|pytest|py\.test)(?=\s|$)")

# implement와 reimplement 배정 서브에이전트의 Bash가 unittest나 pytest를 실행하면 REASON_TEST_RUN을 반환한다. 아니면 None이다.
def run_test_reason(data: dict, command: str) -> str | None:
    if main_session(data):
        return None
    from sub_role import first_prompt
    from sub_session import agent_transcript
    first = first_prompt(agent_transcript(data) or data.get("transcript_path")).strip().splitlines()[:1]
    if not first or first[0].strip() not in ("task: implement", "task: reimplement"):
        return None
    return REASON_TEST_RUN if any(TEST_RUN.match(part) for part in command_parts(command)) else None


REASON_SUB_SPAWN ="서브에이전트는 다른 에이전트를 호출할 수 없습니다. " + RETRY
REASON_ASYNC = "경과 문서를 쓴 세션에서는 request_user_input_async의 답을 기록할 수 없습니다. request_user_input을 사용하십시오."
REASON_REPEAT = "이번 턴에 hook이 거부한 호출과 도구 이름과 입력이 같습니다. 직전 거부 사유에 맞게 입력을 고쳐 다시 실행하십시오."


# Claude 메인 세션에서 같은 턴 거부 호출의 재실행이면 사유를 반환한다. Codex와 서브에이전트는 None이다.
def turn_reason(data: dict, runner: str) -> str | None:
    path = data.get("transcript_path") or ""
    if runner != "claude" or not path or not main_session(data):
        return None
    call = (data.get("tool_name"), data.get("tool_input") or {})
    return REASON_REPEAT if call in denied_calls(read_session(path)) else None


# Claude 메인 세션의 대기 사용자 메시지를 도구 결과와 함께 전달할 문장이다. 없으면 None이다.
# 주입 기록(attachment)에 원문이 남으므로 다음 호출에서는 전달된 메시지로 본다.
# Codex는 hook 입력과 app-server에 대기 입력 정보가 없어 판정하지 않는다.
def waiting_context(data: dict, runner: str) -> str | None:
    path = data.get("transcript_path") or ""
    if runner != "claude" or not path or not main_session(data):
        return None
    messages = waiting_messages(path)
    return (WAITING_HEAD + "\n\n".join(messages)) if messages else None


# 위반 사유를 반환한다. 통과면 None이다.
def check(data: dict, runner: str) -> str | None:
    # Codex는 기록 경로가 없으면 세션 id로 찾는다.
    if runner == "codex" and not data.get("transcript_path") and data.get("session_id"):
        data = {**data, "transcript_path": session_path(data, runner)}

    # 같은 턴 거부 호출의 재실행은 모든 도구에서 가장 먼저 차단한다.
    reason = turn_reason(data, runner)
    if reason:
        return reason

    # 서브에이전트 호출은 승인, fork, 입력 형식, 단계 선행 조건 순으로 검사한다.
    if is_spawn(data.get("tool_name")) or is_followup(data.get("tool_name")):
        if not director_main(data):
            return None
        if is_followup(data.get("tool_name")):
            return check_agent_call(data) or check_input(data, runner) or call_reason(data)
        reason = check_agent_call(data)
        return (reason or check_fork(data, runner) or check_input(data, runner) or research_retry_reason(data, runner)
                or call_reason(data))

    # 경과 문서를 쓴 세션의 비동기 질문 도구는 답을 기록할 수 없으므로 차단된다.
    if re.match(r"^\w*request_user_input_async$", str(data.get("tool_name"))):
        return REASON_ASYNC if project_session(data, runner) else None

    # 질문 도구는 재질문과 안 비교 유무를 검사한다.
    if data.get("tool_name") in QUESTION_TOOLS:
        return check_question(data, runner) if project_session(data, runner) else None

    # 셸 도구의 cd, git -c, workdir는 모든 세션과 승인 상태에서 가장 먼저 차단된다.
    shell = data.get("tool_name") in SHELLS
    command = bash_command(data) if shell else ""
    if shell:
        reason = prompt_reason(command) or workdir_reason(data) or run_test_reason(data, command)
        if reason:
            return reason

    # 경로 접근은 모든 세션에 검사한다.
    role = resolve_role(data)
    reason = check_paths(data, runner)
    if reason:
        return reason

    # director 메인의 승인 전 쓰기와 공통 description을 검사한다.
    if shell:
        stage = sync_stage(data, runner) if director_main(data) else None
        turn_approved = stage in WRITE_STAGES if stage else approved(user_message(data, runner))
        write = director_write_reason(command, data.get("cwd")) if director_main(data) else None
        before = git_write_reason(command) or script_reason(command, data.get("cwd")) if director_main(data) and not turn_approved else None
        reason = write or before or description_reason(data, runner)
        if reason:
            return reason

    # 쓰기 도구는 역할별 쓰기 범위를 검사한다.
    paths = targets(data)
    if paths is None:
        return None
    return check_write(data, role, paths, main_session(data), runner)


REASON_ERROR = "쓰기 도구 검사 중 오류가 발생하여 쓰기를 차단하였습니다. " + RETRY

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

    # 대기 메시지는 호출을 막지 않고 도구 결과 옆에 전달한다.
    try:
        context = waiting_context(data, runner)
    except Exception:
        context = None
    if context:
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}},
                  sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
