"""컨텍스트 기준에 도달한 메인 세션에 작업 기록과 compact 안내를 지시한다.

입력: stdin의 Stop hook JSON과 --runner 인자이다.
반환: 기록이 필요하면 block JSON을 출력하고, 나머지는 출력 없이 0을 반환한다.
한계: 세션 기록을 읽을 수 없거나 토큰 정보가 없으면 통과한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))

import check_workflow_response as wf
from check_workflow_response import STATUS_PATH, call_paths
from sub_role import main_session
from sub_session import read_session, session_path

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


# 세션이 쓴 경과 문서 중 작업 상태가 완료가 아닌 작업 폴더의 저장소 기준 상대 경로를 쓴 순서대로 반환한다.
def open_statuses(data: dict, path: str) -> list[str]:
    found: dict[str, None] = {}
    for entry in read_session(path):
        for name, args in entry["calls"]:
            for written in call_paths(name, args):
                match = STATUS_PATH.search(written.replace("\\", "/"))
                if match:
                    found[wf.sibling(Path(match.group(0)), "state").as_posix()] = None
    root = wf.project_root(data.get("cwd"))
    out = []
    for rel in found:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        state = re.search(r"(?m)^- 작업 상태: *(.+?) *$", text)
        if not state or state.group(1) != "완료":
            out.append(rel.rsplit("/", 1)[0] + "/")
    return out


# 메인 세션이 기준에 도달했으면 stop_hook_active가 아닌 Stop마다 작업 기록과 compact를 지시한다.
def check(data: dict, runner: str = "claude") -> str | None:
    path = session_path(data, runner)
    data = {**data, "transcript_path": path}
    if data.get("stop_hook_active") or not main_session(data) or context_tokens(path, runner) < LIMIT:
        return None
    docs = open_statuses(data, path)
    lines = [f"컨텍스트가 {LIMIT:,} 토큰 이상입니다. 대화에만 있는 작업 정보를 진행 중인 작업 폴더의 문서에 기록하십시오."]
    for d in docs:
        lines += [f"- 작업 폴더: {d}",
                  "  - 조사 결과: research.md",
                  "  - 계획 변경: spec.md",
                  "  - 사용자 지시와 다음 작업: status.md의 작업 경과 절"]
    if not docs:
        lines.append("- 진행 중인 작업 폴더 없음")
    lines.append("기록을 마친 응답 끝에 사용자가 compact를 실행하도록 안내하십시오.")
    return "\n".join(lines)


# Stop 입력을 검사하고 차단 사유가 있으면 JSON으로 출력한다. 검사 오류는 통과한다.
def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    args = parser.parse_args()
    try:
        reason = check(json.load(sys.stdin), args.runner)
    except Exception:
        return 0
    if reason:
        json.dump({"decision": "block", "reason": reason}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
