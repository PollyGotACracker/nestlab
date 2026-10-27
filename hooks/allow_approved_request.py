#!/usr/bin/env python3
"""PermissionRequest hook: 승인 턴과 허용 목록 명령의 승인 창을 자동 허용한다.

호출자:
- Claude Code와 Codex의 PermissionRequest hook 설정(shared/settings)
- 호출 명령은 `python allow_approved_request.py --runner <claude|codex>`
입력: hook 입력 JSON을 stdin으로 받는다.
반환:
- 허용이면 stdout에 `hookSpecificOutput`(`decision.behavior: allow`) JSON을 출력한다.
- 그 밖에는 아무것도 출력하지 않고 종료 코드 0으로 끝낸다. 오류도 출력 없이 종료한다.
- decide()는 허용 여부 bool을 반환한다.
한계: Bash 호출만 판정한다. ask 패턴은 settings.example.json의 Bash 항목이다.
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
from sub_docs import ROOT
from sub_write import INLINE_CODE, PREFIX, SUBCOMMAND, bash_command

SETTINGS = ROOT / "shared" / "settings" / "claude" / "settings.example.json"
# 승인 턴에도 묻지 않는 대신 허용 목록으로 판정하는 스크립트 실행 ask 패턴이다.
SCRIPT_ASK = {"Bash(python *)", "Bash(python3 *)", "Bash(py *)", "Bash(node *)"}
ALLOW_LIST = ("python -m unittest *", "python3 -m unittest *", "py -m unittest *", "python hooks/tests/test_*.py",
              "python -B tools/tests/test_set_hooks.py", "python tools/check_doc_rule.py",
              "python tools/set_skills.py check", "python ../tools/check_doc_rule.py",
              "python ../tools/set_skills.py check")
# 단일 명령이 아님을 나타내는 문자(하위 명령 구분자, 치환, 리다이렉트)이다.
COMPOUND = re.compile(r"[`<>\n]|\$\(")
GIT_LOWER_C = re.compile(r"\bgit\s+-c\b")
GIT_UPPER_C = re.compile(r"\bgit\s+-C\s+(?:\"[^\"]*\"|'[^']*'|\S+)")
# 승인 턴에도 자동 허용하지 않는 인라인 코드의 위험 패턴이다(권한 규칙이 지키는 경로, 자격 증명, 프로세스 실행).
RISKY = re.compile(r"\.claude|\.codex|\.env|secrets|\.pem|\.key|id_rsa|id_ed25519|credentials|subprocess|os\.system"
                   r"|os\.popen|child_process|exec")


# 글롭 패턴(`*`만 와일드카드)을 전체 일치 정규식으로 바꾼다.
def glob_re(pattern: str) -> re.Pattern:
    return re.compile(".*".join(re.escape(p) for p in pattern.split("*")), re.S)


# 설정 원본 permissions.ask의 Bash 패턴에서 스크립트 실행 4개와 git -C 패턴을 뺀 정규식 목록을 반환한다.
def ask_patterns() -> list[re.Pattern]:
    ask = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["ask"]
    return [glob_re(a[5:-1]) for a in ask if a.startswith("Bash(") and a not in SCRIPT_ASK | {"Bash(git -C *)"}]


# 명령이 허용 목록과 일치하면 True이다. 하위 명령이 여럿인 명령은 일치로 보지 않는다.
def in_allow_list(command: str) -> bool:
    command = command.strip()
    if SUBCOMMAND.search(command) or COMPOUND.search(command):
        return False
    return any(glob_re(a).fullmatch(command) for a in ALLOW_LIST)


# 명령에 git -c가 있거나, git -C <경로>를 git으로 바꾼 전체 명령과 하위 명령 하나라도 ask 패턴과 일치하면 True이다.
def is_ask(command: str) -> bool:
    if GIT_LOWER_C.search(command):
        return True
    command = GIT_UPPER_C.sub("git", command)
    parts = [command.strip()] + [PREFIX.sub("", p.strip()) for p in SUBCOMMAND.split(command)]
    # `git push *`처럼 끝이 `*`인 패턴은 인자 없는 명령(`git push`)과도 일치한다.
    return any(p.fullmatch(part) or p.fullmatch(part + " ") for p in ask_patterns() for part in parts if part)


# 인라인 코드 명령이고 원문에 위험 패턴이 있으면 True이다.
def risky_inline(command: str) -> bool:
    return bool(INLINE_CODE.search(PREFIX.sub("", command.strip()))) and bool(RISKY.search(command))


# 요청을 허용하면 True를 반환한다.
def decide(data: dict, runner: str) -> bool:
    # Bash 호출만 판정한다.
    if data.get("tool_name") != "Bash":
        return False

    # 화면 없는 Claude 세션(dontAsk)은 ask 규칙 명령을 거부하도록 허용하지 않는다.
    if runner == "claude" and data.get("permission_mode") == "dontAsk":
        return False
    command = bash_command(data)

    # 허용 목록 명령은 승인 여부와 무관하게 허용한다.
    if in_allow_list(command):
        return True

    # 승인 턴이면 ask 패턴과 일치하지 않는 명령을 허용한다.
    return approved(user_message(data, runner)) and not is_ask(command) and not risky_inline(command)


# stdin의 hook 입력을 판정하고 허용이면 allow JSON을 출력한다. 항상 0을 반환한다.
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
        allow = decide(json.load(sys.stdin), runner)
    except Exception:
        return 0
    if allow:
        json.dump({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                          "decision": {"behavior": "allow"}}}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
