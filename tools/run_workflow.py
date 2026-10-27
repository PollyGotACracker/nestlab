#!/usr/bin/env python3
"""승인된 단계의 CLI 실행, 결과 저장과 종료 조건 검사를 맡는 단계 실행기이다.
검증은 reviewer 호출로만 하며 이 실행기에 검증 단계가 없다.

호출자:
- director(`python tools/run_workflow.py --task <작업-id> --stage <단계> --runner <claude|codex>`)
- hooks/check_invalid_response.py(`python tools/run_workflow.py --incident <사건 문서> --runner <claude|codex>`, 분리 프로세스)
입력:
- 작업 ID, 단계 이름, runner 이름(claude 또는 codex). 사건 분석은 사건 문서 경로와 runner 이름이다.
- 사건 분석(`--incident`)은 hook을 끈 director 세션이며 Bash를 거부하고 문서 및 웹 도구만 허용한다.
반환:
- 성공하면 단계 결과 문자열(최종 메시지, 또는 "조사 결과를 <문서>의 `<칸 이름>` 칸에 저장했습니다." 한 줄)을 stdout에 출력하고 종료 코드 0을 반환한다.
- 선행 조건 미달, 실행 실패, 검사 실패는 사유 문장 뒤에 "단계를 실행하지 않았습니다."를 붙여 stderr에 출력하고 종료 코드 1을 반환한다.
한계: 외부 CLI 실행은 인증과 런타임 설치 상태에 영향을 받는다. 선행 조건 사유는 hooks/common/sub_workflow.py의 상수를 따른다.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

# 저장소 모듈 import 오류 메시지도 UTF-8로 나오도록 먼저 설정한다.
for stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hooks"), str(ROOT / "hooks/common")]
from return_research_path import SLOT_ROUND
from save_subagent_result import write_result
from sub_docs import SAVED_LINE, task_doc
from sub_workflow import (Document, STAGES, check_research, check_research_result,
                          check_refutation, research_items, require, validate)

ROLES = {"kickoff": "director", "research": "researcher", "refute": "reviewer",
         "implement": "worker", "document": "documenter", "rework": "worker", "incident": "director"}
KINDS = {"kickoff": "착수", "research": "조사", "refute": "반증", "implement": "구현",
         "document": "문서 작성", "rework": "재작업", "incident": "사건 분석"}


ERR_CLAUDE = "Claude 실행이 실패했습니다."
ERR_CODEX = "Codex 실행이 실패했습니다."
ERR_EMPTY_MESSAGE = "최종 메시지가 비어 있습니다."

# 단계 역할의 지침과 요청을 외부 CLI에 넘겨 실행하고 최종 메시지를 반환한다.
def invoke(root: Path, runner: str, stage: str, prompt: str) -> str:
    # 역할 지침과 요청을 합치고 역할별 허용 도구를 정한다.
    role = ROLES[stage]
    instructions = (root / ".claude/agents" / role / "AGENTS.md").read_text(encoding="utf-8-sig")
    request = instructions + "\n\n" + prompt
    allowed = ["Read", "Grep", "Glob", "Bash", "WebFetch", "WebSearch"]
    if role in ("director", "worker", "documenter"):
        allowed += ["Write", "Edit"]

    # 사건 분석 세션은 hook을 끄므로 Bash 없이 문서 읽기와 쓰기 도구만 허용한다.
    incident = stage == "incident"
    if incident:
        allowed = ["Skill", "Read", "Grep", "Glob", "Write", "Edit", "WebSearch", "WebFetch"]

    # runner에 맞는 CLI 명령을 만들어 실행한다.
    command = (["claude", "-p", "--permission-mode", "dontAsk", "--allowedTools", ",".join(allowed),
                "--output-format", "json"]
               + (["--disallowedTools", "Bash", "--settings", '{"disableAllHooks": true}'] if incident else [])
               + ["--", request] if runner == "claude" else
               ["codex", "exec"] + (["--disable", "hooks"] if incident else [])
               + ["--sandbox", "workspace-write", "--json", "--", request])
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", check=True,
                            creationflags=flags)

    # 출력에서 오류 여부를 확인하고 최종 메시지를 꺼낸다.
    if runner == "claude":
        response = json.loads(result.stdout)
        require(not response.get("is_error"), ERR_CLAUDE)
        message = response.get("result", "")
    else:
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        require(not any(e.get("type") in ("error", "turn.failed") for e in events), ERR_CODEX)
        messages = [e["item"].get("text", "") for e in events
                    if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "agent_message"]
        message = messages[-1] if messages else ""
    require(isinstance(message, str) and bool(message.strip()), ERR_EMPTY_MESSAGE)
    return message.strip()


# 반증 문서 경로이다. 반증 문서가 없는 기존 작업(log/state)은 조사 문서를 쓴다.
def refute_path(root: Path, task: str) -> Path:
    path = task_doc(root, task, "refute")
    return path if path.is_file() else task_doc(root, task, "research")


# 단계 요청이다. 작업 종류 한 줄과 단계에 맞는 문서 경로 한 줄만 둔다. 상태 문서 경로는 싣지 않는다.
def request_text(root: Path, task: str, stage: str) -> str:
    if stage == "research":
        label, path = "조사 문서", task_doc(root, task, "research")
    elif stage == "refute":
        path = refute_path(root, task)
        label = "반증 문서" if path.name == "refute.md" else "조사 문서"  # 기존 작업은 조사 문서에 회차를 쓴다.
    else:
        label, path = "명세 문서", task_doc(root, task, "input")
    return f"작업 종류: {KINDS[stage]}\n{label}: {path.relative_to(root).as_posix()}"


ERR_NO_ROUND = "반증 결과 회차가 반증 문서에 저장되지 않았습니다."
FEEDBACK_FAILED = "\n직전 결과가 조사 단계 확인 조건을 채우지 못했습니다. {error}\n누락 조건을 고쳐 전체 결과를 다시 반환한다."
FEEDBACK_REFUTED = "\n반증 원문의 지적을 반영해 전체 조사 결과를 반환한다."
RESULT_COMPLETE = "완료 조건을 모두 채웠습니다."
RESULT_UNVERIFIED = "\n반증이 2회에 도달했으나 검증을 마치지 못했습니다."

# 단계 선행 조건을 확인하고 단계를 실행해 결과 문자열을 반환한다. 조사 단계는 반증까지 반복한다.
def run(root: Path, task: str, stage: str, runner: str) -> str:
    # 선행 조건을 확인한다. 완료 단계는 확인만 하고 끝낸다.
    validate(root, task, stage)
    if stage == "complete":
        return RESULT_COMPLETE

    # 조사와 반증이 아닌 단계는 한 번 실행하고 종료 조건을 다시 확인한다.
    prompt = request_text(root, task, stage)
    if stage not in ("research", "refute"):
        message = invoke(root, runner, stage, prompt)
        validate(root, task, stage)
        return message

    # 조사와 반증 단계는 확인 조건을 채울 때까지 번갈아 반복한다.
    research_path = task_doc(root, task, "research")
    items = research_items(Document(research_path.read_text(encoding="utf-8-sig")))
    feedback = ""
    while True:
        # 단계를 실행하고 결과가 확인 조건을 채우지 못하면 같은 단계를 다시 실행한다.
        message = invoke(root, runner, stage, prompt + feedback)
        try:
            if stage == "research":
                check_research_result(message, items)
            else:
                check_refutation(message)
        except ValueError as error:
            feedback = FEEDBACK_FAILED.format(error=error)
            continue

        # 조사 결과는 조사 문서에, 반증 결과는 반증 문서에 저장한다.
        target = research_path if stage == "research" else refute_path(root, task)
        reason = write_result(target, prompt, message, stage == "refute")
        require(reason is None, reason or "")

        # 조사 결과가 저장되면 반증 단계로 넘어간다.
        if stage == "research":
            validate(root, task, "refute")
            stage = "refute"
            prompt = request_text(root, task, stage)
            feedback = ""
            continue

        # 반증 회차를 읽고, 반증이 없거나 2회에 도달했으면 사용자 확인 전 조건을 검사해 끝낸다.
        loop = Document(target.read_text(encoding="utf-8-sig"))
        rows = re.findall(r"^\|\s*(\d+)\s*\|\s*(\d+)\s*\|", loop.section("조사·반증 루프"), re.M)
        require(bool(rows), ERR_NO_ROUND)
        if int(rows[-1][1]) == 0 or int(rows[-1][0]) >= 2:
            state = Document(task_doc(root, task, "state").read_text(encoding="utf-8-sig"))
            research = Document(research_path.read_text(encoding="utf-8-sig"))
            check_research(state, research, items, reviewed=False, refute=None if target == research_path else loop)
            saved = SAVED_LINE.format(path=target.relative_to(root).as_posix(), slot=SLOT_ROUND.format(n=rows[-1][0]))
            return saved + (RESULT_UNVERIFIED if int(rows[-1][1]) else "")

        # 반증이 남았으면 반증 원문을 반영해 조사를 다시 한다.
        stage = "research"
        prompt = request_text(root, task, stage)
        feedback = FEEDBACK_REFUTED


ERR_NO_INCIDENT = "사건 문서 {path}가 없습니다."

# 사건 문서의 분석을 hook을 끈 director 세션에서 실행하고 최종 메시지를 반환한다.
def run_incident(root: Path, incident: str, runner: str) -> str:
    # 사건 문서가 있는지 확인한다.
    path = Path(incident)
    require((path if path.is_absolute() else root / path).is_file(), ERR_NO_INCIDENT.format(path=incident))

    # 작업 종류, 사건 문서, 절차를 담은 요청으로 director 세션을 실행한다.
    now = datetime.now(timezone(timedelta(hours=9)))
    prompt = (f"작업 종류: {KINDS['incident']}\n사건 문서: {incident}\n현재 시각(KST): {now:%Y-%m-%d %H:%M:%S}\n"
              "nestlab-log-incident 절차")
    return invoke(root, runner, "incident", prompt)


ERR_PROCESS = "외부 CLI가 종료 코드 {code}로 끝났습니다."
BLOCKED = "{error} 단계를 실행하지 않았습니다."

# 인자를 읽어 단계를 실행하고 결과를 출력한다. 실패하면 사유를 stderr에 쓰고 1을 반환한다.
def main() -> int:
    # 인자를 읽는다.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task")
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("--incident", help="분석할 사건 문서 경로(--task, --stage와 함께 쓰지 않는다)")
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    args = parser.parse_args()
    if not ((args.incident and not (args.task or args.stage)) or (args.task and args.stage and not args.incident)):
        parser.error("--incident 또는 --task와 --stage 중 하나만 지정하십시오.")

    # 단계를 실행하고 실패 사유를 문장으로 출력한다.
    try:
        print(run_incident(ROOT, args.incident, args.runner) if args.incident
              else run(ROOT, args.task, args.stage, args.runner))
    except subprocess.CalledProcessError as error:
        print(BLOCKED.format(error=ERR_PROCESS.format(code=error.returncode)), file=sys.stderr)
        return 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(BLOCKED.format(error=error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
