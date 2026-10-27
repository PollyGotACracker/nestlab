#!/usr/bin/env python3
"""사건 문서의 분석을 hook을 끈 director 세션으로 실행하는 사건 분석 실행기이다.
조사와 반증은 director의 Agent 도구 호출로 하며 이 실행기에 단계 실행 경로가 없다.

호출자:
- hooks/check_invalid_response.py(`python tools/run_workflow.py --incident <사건 문서> --runner <claude|codex>`, 분리 프로세스)
입력:
- 사건 문서 경로와 runner 이름(claude 또는 codex).
- 사건 분석은 hook을 끈 director 세션이며 Bash를 거부하고 문서 및 웹 도구만 허용한다.
반환:
- 성공하면 최종 메시지를 stdout에 출력하고 종료 코드 0을 반환한다.
- 사건 문서 없음, 실행 실패는 사유 문장 뒤에 "단계를 실행하지 않았습니다."를 붙여 stderr에 출력하고 종료 코드 1을 반환한다.
한계: 외부 CLI 실행은 인증과 런타임 설치 상태에 영향을 받는다.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

# 저장소 모듈 import 오류 메시지도 UTF-8로 나오도록 먼저 설정한다.
for stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hooks"), str(ROOT / "hooks/common")]
from sub_workflow import require

ROLES = {"incident": "director"}
KINDS = {"incident": "사건 분석"}


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
    parser.add_argument("--incident", required=True, help="분석할 사건 문서 경로")
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    args = parser.parse_args()

    # 단계를 실행하고 실패 사유를 문장으로 출력한다.
    try:
        print(run_incident(ROOT, args.incident, args.runner))
    except subprocess.CalledProcessError as error:
        print(BLOCKED.format(error=ERR_PROCESS.format(code=error.returncode)), file=sys.stderr)
        return 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(BLOCKED.format(error=error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
