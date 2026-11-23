"""컨텍스트 기준에 도달한 메인 세션에 작업 기록과 compact 안내를 지시한다."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_workflow_response"))

from sub_docs import project_root
from sub_paths import TASK_REL
from sub_role import director_main, main_session
from sub_session import session_path
from sub_task_index import selected_status

LIMIT = 200_000


# 마지막 사용량의 컨텍스트 토큰 합계를 반환한다. 누적 사용량은 제외한다.
def context_tokens(path: str, runner: str) -> int:
    total = 0
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if runner == "claude" and row.get("type") == "assistant":
                    usage = (row.get("message") or {}).get("usage")
                    if isinstance(usage, dict):
                        total = sum(usage.get(key, 0) or 0 for key in (
                            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
                elif runner == "codex" and row.get("type") == "event_msg":
                    payload = row.get("payload") or {}
                    if payload.get("type") == "token_count":
                        usage = (payload.get("info") or {}).get("last_token_usage")
                        if isinstance(usage, dict):
                            total = usage.get("total_tokens", 0) or 0
    except (OSError, ValueError, TypeError, AttributeError):
        return 0
    return total


# 색인의 현재 진행 작업이 미완료이면 (저장소 기준 작업 폴더 경로, 작업 단계) 하나를 담은 목록을 반환한다.
def open_statuses(data: dict) -> list[tuple[str, str]]:
    from sub_approval import stage_of
    status = selected_status(project_root(data.get("cwd")))
    if status is None:
        return []
    stage = stage_of(status.read_text(encoding="utf-8-sig")) or ""
    return [] if stage == "완료" else [(f"{TASK_REL}/{status.parent.name}/", stage)]


NOTICE = f"컨텍스트가 {LIMIT:,} 토큰 이상입니다. compact를 실행하십시오."


# 메인 세션이 기준에 도달했으면 stop_hook_active가 아닌 Stop마다 (에이전트 차단 사유 또는 None, 사용자 알림 또는 None)을 반환한다.
# 작업 문서 기록은 director 메인에만 지시하고, 모든 메인 세션의 compact 실행은 사용자 알림으로만 전달한다.
# director의 색인 현재 진행 작업이 구현 단계이면 구현과 검증 배정 흐름을 끊지 않도록 통과한다.
def check(data: dict, runner: str = "claude") -> tuple[str | None, str | None]:
    path = session_path(data, runner)
    data = {**data, "transcript_path": path}
    if data.get("stop_hook_active") or not main_session(data) or context_tokens(path, runner) < LIMIT:
        return None, None
    if not director_main(data):
        return None, NOTICE
    opened = open_statuses(data)
    if opened and opened[0][1] == "구현":
        return None, None
    docs = [d for d, _ in opened]
    lines = [f"컨텍스트가 {LIMIT:,} 토큰 이상입니다. 대화에만 있는 작업 정보를 현재 진행 중인 작업 폴더의 문서에 기록하십시오.\n현재 작업 폴더가 없는 경우 관련 지침 또는 스킬에 따라 작업 폴더를 생성하십시오."]
    for d in docs:
        lines += [f"- 작업 폴더: {d}",
                  "  - 조사 결과: research.md",
                  "  - 계획 변경: spec.md",
                  "  - 사용자 지시와 다음 작업: status.md의 작업 경과 절"]
    if not docs:
        lines.append("- 진행 중인 작업 폴더 없음")
    return "\n".join(lines), NOTICE


# Stop 입력을 검사하고 결과를 JSON으로 출력한다. Claude는 알림을 systemMessage로, Codex는 차단 사유에 담는다. 검사 오류는 통과한다.
def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    args = parser.parse_args()
    try:
        reason, notice = check(json.load(sys.stdin), args.runner)
    except Exception:
        return 0
    if args.runner == "codex" and notice:
        reason = f"{reason}\n{notice}" if reason else notice
        notice = None
    out = {}
    if reason:
        out.update(decision="block", reason=reason)
    if notice:
        out["systemMessage"] = notice
    if out:
        json.dump(out, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
