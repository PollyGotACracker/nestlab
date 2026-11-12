#!/usr/bin/env python3
"""PermissionRequest hook에서 세션별 자동 허용 범위를 판정한다.

입력: runner 인자와 stdin의 hook 입력 JSON이다.
반환: PreToolUse를 통과한 하위 실행, 메인 읽기와 director 작업 문서 쓰기는 allow JSON이다.
그 밖의 호출과 질문 도구, 오류는 출력 없이 runner 권한 판정을 따른다.
한계: 보안 위험과 외부 경로는 PreToolUse와 runner의 deny 규칙으로 검사한다.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

# 같은 이름 폴더의 모듈과 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))

from sub_docs import RESPONSE_TEMPLATES, project_root
from sub_paths import CREATE_TASK_DOCS
from sub_role import director_main, main_session
from sub_session import QUESTION_TOOLS
from sub_write import SHELLS, SUBCOMMAND, bash_command, command_parts, git_write_reason, plain, targets
from sub_task_docs import STAGES as TASK_DOCS_STAGES, trusted_launcher

# 승인 여부와 무관하게 허용하는 검증 커맨드 목록이다.
ALLOW_LIST = ("python -m unittest *", "python3 -m unittest *", "py -m unittest *", "python hooks/tests/test_*.py",
              "python -B tools/tests/test_set_hooks.py", "python tools/check_doc_rule.py",
              "python tools/set_skills.py check", "python ../tools/check_doc_rule.py",
              "python ../tools/set_skills.py check")
# 단일 커맨드가 아님을 나타내는 문자(하위 커맨드 구분자, 치환, 리다이렉트)이다.
COMPOUND = re.compile(r"[`<>\n]|\$\(")
# 승인 창 없이 질문을 전달해야 하는 도구이다.
QUESTION = QUESTION_TOOLS + ("ExitPlanMode",)
TASK_DOCS_SCRIPT = CREATE_TASK_DOCS
TASK_DOCS_HOME = Path.home()
TASK_DOCS_ID = re.compile(r"\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*")


# 원본 생성기의 단일 호출이며 실제 출력 경로가 현재 프로젝트 안이면 True이다.
def task_docs_command(command: str, cwd: str | Path | None = None) -> bool:
    command = command.strip()
    if command.startswith("& "):
        command = command[2:].lstrip()
    if SUBCOMMAND.search(plain(command)) or COMPOUND.search(command) or "$" in command or "%" in command:
        return False
    try:
        tokens = shlex.split(command, posix=False)
        for token in tokens:
            quoted = len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'"
            if not quoted and any(mark in token for mark in "*?[]{}"):
                return False
        args = [token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'" else token
                for token in tokens]
        if not args:
            return False
        executable = args.pop(0)
        base = Path(cwd or Path.cwd()).resolve()
        if not trusted_launcher(executable, TASK_DOCS_HOME):
            if executable not in ("python", "python3", "py", "python.exe", "python3.exe", "py.exe") \
                    and Path(executable).resolve() != Path(sys.executable).resolve():
                return False
            if args and args[0] == "-B":
                args.pop(0)
            if args[:2] == ["-X", "utf8"]:
                del args[:2]
            if not args or (base / args.pop(0)).resolve() != TASK_DOCS_SCRIPT.resolve():
                return False
            if not TASK_DOCS_SCRIPT.resolve().is_relative_to(project_root(base).resolve()):
                return False
        if len(args) == 2 and args[0] == "--print-template":
            return args[1] in RESPONSE_TEMPLATES
        if len(args) not in (6, 8):
            return False
        options = {}
        for key, value in zip(args[::2], args[1::2]):
            if key not in ("--task", "--title", "--stage", "--root") or key in options:
                return False
            options[key] = value
        task, title, stage = (options.get(key, "") for key in ("--task", "--title", "--stage"))
        if not TASK_DOCS_ID.fullmatch(task) or not title.strip() or stage not in TASK_DOCS_STAGES:
            return False
        root_value = options.get("--root", "")
        if root_value.startswith("~") or any(mark in root_value for mark in "*?[]{}"):
            return False
        project = project_root(base).resolve()
        root = (base / options.get("--root", ".")).resolve()
        folder = root / "log" / "task" / task
        return root == project and all(
            path.resolve().is_relative_to(root) for path in [folder, *(folder / f"{kind}.md" for kind in TASK_DOCS_STAGES[stage])])
    except (OSError, ValueError):
        return False


# 글롭 패턴(`*`만 와일드카드)을 전체 일치 정규식으로 바꾼다.
def glob_re(pattern: str) -> re.Pattern:
    return re.compile(".*".join(re.escape(p) for p in pattern.split("*")), re.S)


# 커맨드가 허용 목록과 일치하면 True이다. 하위 커맨드가 여럿인 커맨드는 일치로 보지 않는다.
def in_allow_list(command: str, cwd: str | Path | None = None) -> bool:
    command = command.strip()
    if task_docs_command(command, cwd):
        return True
    if SUBCOMMAND.search(command) or COMPOUND.search(command):
        return False
    return any(glob_re(a).fullmatch(command) for a in ALLOW_LIST)


# 메인 세션의 읽기 도구와 읽기 셸 커맨드는 allow이다.
def read_allow(data: dict, runner: str) -> str | None:
    name = data.get("tool_name")
    if name in ("Read", "Grep", "Glob", "WebFetch", "WebSearch"):
        return "allow"
    if name not in SHELLS:
        return None
    # check_tool_use가 이 모듈을 불러오므로 순환을 피해 호출 시점에 불러온다.
    from check_tool_use import main_write_reason, script_reason
    command = bash_command(data)
    if any(task_docs_command(part, data.get("cwd")) and "--print-template" not in part
           for part in command_parts(command, preserve_quotes=True)):
        return None
    return "allow" if not (main_write_reason(command) or git_write_reason(command)
                           or script_reason(command, data.get("cwd"))) else None


REASON_CHECK_ERROR = "훅 검사에서 오류가 발생하여 호출이 차단되었습니다. 같은 호출을 다시 시도하고 반복되면 hook 입력과 로그를 확인하십시오."


# 질문 도구는 출력 없이 둔다(None). PreToolUse 검사를 어긴 호출은 사유와 함께 deny dict, 통과한 호출은 allow이다.
# git 비가역 작업은 통과해도 None으로 두어 승인 창을 유지한다.
def decide(data: dict, runner: str) -> str | dict | None:
    name = data.get("tool_name")
    if name in QUESTION:
        return None
    from check_tool_use import check
    reason = check(data, runner)
    if reason is not None:
        return {"behavior": "deny", "message": reason}
    if name in SHELLS and git_write_reason(bash_command(data)):
        return None
    return "allow"


# stdin의 hook 입력을 판정하고 allow 또는 deny이면 JSON을 출력한다. 판정 중 예외는 검사 오류 사유의 deny이다. 항상 0을 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞추고 인자를 읽는다.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    runner = parser.parse_args().runner

    # 판정 중 예외는 검사 오류 사유로 deny한다.
    try:
        verdict = decide(json.load(sys.stdin), runner)
    except Exception:
        verdict = {"behavior": "deny", "message": REASON_CHECK_ERROR}
    if verdict:
        decision = {"behavior": verdict} if isinstance(verdict, str) else verdict
        json.dump({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}},
                  sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
