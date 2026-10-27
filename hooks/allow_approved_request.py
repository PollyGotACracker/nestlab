#!/usr/bin/env python3
"""PermissionRequest hook: 승인 창이 뜨기 직전에 승인 턴이면 허용하고 승인 전이면 거부한다.

호출자:
- Claude Code와 Codex의 PermissionRequest hook 설정(shared/settings)
- 호출 명령은 `python allow_approved_request.py --runner <claude|codex>`
입력: hook 입력 JSON을 stdin으로 받는다.
반환:
- 승인 턴이거나 허용 목록 명령이거나 승인 전 PreToolUse 검사를 통과한 읽기 셸 명령이면 stdout에 `hookSpecificOutput`(`decision.behavior: allow`) JSON을 출력한다.
- 승인 전이면 `decision.behavior: deny`와 `decision.message` JSON을 출력한다.
- 질문 도구(AskUserQuestion, ExitPlanMode, request_user_input)와 오류는 아무것도 출력하지 않고 종료 코드 0으로 끝낸다.
- decide()는 "allow", "deny" 또는 None을 반환한다.
- in_allow_list()는 허용 목록 일치 여부 bool을 반환한다.
한계: 승인 창을 띄우는 도구 호출이 모두 대상이다. 보안 위험과 외부 경로는 PreToolUse hook과 설정 deny 규칙이 먼저 막는다.
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

from sub_approval import approved, user_message
from sub_session import QUESTION_TOOLS
from sub_write import RETRY, SHELLS, SUBCOMMAND, bash_command

# 승인 여부와 무관하게 허용하는 검증 명령 목록이다.
ALLOW_LIST = ("python -m unittest *", "python3 -m unittest *", "py -m unittest *", "python hooks/tests/test_*.py",
              "python -B tools/tests/test_set_hooks.py", "python tools/check_doc_rule.py",
              "python tools/set_skills.py check", "python ../tools/check_doc_rule.py",
              "python ../tools/set_skills.py check")
# 단일 명령이 아님을 나타내는 문자(하위 명령 구분자, 치환, 리다이렉트)이다.
COMPOUND = re.compile(r"[`<>\n]|\$\(")
# 승인 창 없이 질문을 전달해야 하는 도구이다.
QUESTION = QUESTION_TOOLS + ("ExitPlanMode",)
REASON_DENY = "승인 전에는 승인이 필요한 도구 호출을 허용할 수 없습니다. " + RETRY


# 글롭 패턴(`*`만 와일드카드)을 전체 일치 정규식으로 바꾼다.
def glob_re(pattern: str) -> re.Pattern:
    return re.compile(".*".join(re.escape(p) for p in pattern.split("*")), re.S)


# 명령이 허용 목록과 일치하면 True이다. 하위 명령이 여럿인 명령은 일치로 보지 않는다.
def in_allow_list(command: str) -> bool:
    command = command.strip()
    if SUBCOMMAND.search(command) or COMPOUND.search(command):
        return False
    return any(glob_re(a).fullmatch(command) for a in ALLOW_LIST)


# 요청 판정을 반환한다. 질문 도구는 None, 허용 목록 명령과 승인 턴은 "allow", 그 밖은 "deny"이다.
def decide(data: dict, runner: str) -> str | None:
    # 질문 도구는 출력 없이 둔다.
    name = data.get("tool_name")
    if name in QUESTION:
        return None

    # 허용 목록 명령은 승인 여부와 무관하게 허용한다.
    if name == "Bash" and in_allow_list(bash_command(data)):
        return "allow"

    # 승인 전에도 PreToolUse 검사를 통과한 읽기 셸 명령은 허용한다.
    if name in SHELLS and not approved(user_message(data, runner)):
        # check_tool_use가 이 모듈을 불러오므로 순환을 피해 호출 시점에 불러온다.
        from check_tool_use import check, main_write_reason
        command = bash_command(data)
        if check(data, runner) is None and main_write_reason(command) is None:
            return "allow"

    # 승인 턴이면 허용하고 승인 전이면 거부한다.
    return "allow" if approved(user_message(data, runner)) else "deny"


# stdin의 hook 입력을 판정하고 허용이나 거부 JSON을 출력한다. 항상 0을 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞추고 인자를 읽는다.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    runner = parser.parse_args().runner

    # 입력을 읽거나 판정하지 못하면 출력 없이 끝낸다.
    try:
        verdict = decide(json.load(sys.stdin), runner)
    except Exception:
        return 0
    if verdict:
        decision = {"behavior": verdict, **({"message": REASON_DENY} if verdict == "deny" else {})}
        json.dump({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}},
                  sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
