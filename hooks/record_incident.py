"""Stop hook: 메인 에이전트 응답에서 실수·위반 인정 표현을 찾아 사건 문서 초안에 기록한다.

사용: python record_incident.py --runner <claude|codex>
- 검사 대상은 hook 입력의 last_assistant_message이다. 코드 블록 안은 검사하지 않는다.
- 일치하면 세션별 사건 문서 `log/incident/<YYYYMMDD>-<session 앞 8자>.md`에 항목을 추가한다.
  파일이 없으면 사건 양식의 머리 항목으로 만들고 조치 상태는 `미착수`로 둔다.
- 하위 에이전트 응답(Claude agent_id, Codex 하위 rollout)은 검사하지 않는다.
- 전체 세션 ID는 머리에, 작업 근거와 응답 위치는 원문 행 번호만 남긴다.
- 세션 경로를 사용하여 내부적으로 원문을 조회하고, 사용자 메시지와 응답은 경로 제거 뒤 코드 블록에 원문대로 기록한다.
- 출력은 없다. 대화에 아무것도 넣지 않는다. 오류는 통과한다(fail-open).

공용 모듈(common/): sub_docs, sub_role, sub_session
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))

from sub_docs import ROOT, task_id
from sub_role import first_line, resolve_role
from sub_session import _claude_entry, _codex_entry, norm, session_path, turn
from save_agent_result import fence_for

INCIDENT = ROOT / "log" / "incident"
# 표현 목록 기반 감지이다. 놓치는 표현이 쌓이면 이 목록에 추가한다.
ADMIT = re.compile(
    r"죄송|제 실수|제 잘못|제가 실수|제가 잘못|실수했|잘못했|잘못 (?:이해|해석|읽|봤|판단|말씀|맞췄|잡았)|틀렸"
    r"|놓쳤|놓치고|빠뜨|빼먹|누락(?:했|입니다|시켰)|위반했|어겼|어긴|건너뛰었|부적절했"
    r"|확인하지 않고|확인 안 하고|확인 없이|허락 없이|근거 없이|임의로|제 판단으로|확인하지 않"
    r"|착오|엉뚱|망가뜨|철회|낭비(?:했|한 것|시켰)|단정(?:했|한 것)|안 했(?:습니다|어요)")
FENCE = re.compile(r"^\s*(```|~~~)")
TASK_REF = re.compile(r"log/state/[\w.\-]+\.md")
PATH = re.compile(
    r"(?<![\w:/\\])(?:/?[A-Za-z]:[\\/]|\\\\|~[\\/]|\.{1,2}[\\/]|/)[^\s`<>\"'\[\](){}*,;]+"
    r"|(?<![\w:/\\])(?:[\w.@~-]+[\\/])+[^\s`<>\"'\[\](){}*,;]+"
    r"|(?<![\w.])\b[\w.-]+\.(?:md|py|jsonl?|ya?ml|toml|txt|csv|log|js|tsx?|jsx|html|css)\b(?::\d+)?")


def without_paths(text: str) -> str:
    """경로 링크의 설명은 보존한다. 잘린 링크와 인용된 공백 포함 경로도 제거한다."""
    parts = re.split(r"(https?://[^\s`<>\"'\[\]()]+)", text, flags=re.I)
    if len(parts) > 1:
        return "".join(part if i % 2 else without_paths(part) for i, part in enumerate(parts))
    text = re.sub(r"\[([^\]\n]*)\]\(([^)\n]*\)|[^\s)\n]+)",
                  lambda m: without_paths(m[1]) if PATH.search(m[2]) else m[0], text)
    text = re.sub(r"(`+|[\"'])([^\n]*?)\1",
                  lambda m: "" if PATH.match(m[2]) else m[1] + PATH.sub("", m[2]) + m[1], text)
    return PATH.sub("", text)


def plain(text: str) -> str:
    out, fence = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fence = not fence
        elif not fence:
            out.append(line)
    return "\n".join(out)


def is_child(data: dict) -> bool:
    if data.get("agent_id"):
        return True
    source = first_line(data.get("transcript_path")).get("source")
    return isinstance(source, dict) and bool(source.get("subagent"))


def context(path: str, message: str) -> tuple[str, str, str, int | None]:
    """현재 턴의 응답 위치와 가장 최근 명시된 작업 참조를 실제 원문에서 확인한다."""
    entries, lines = [], []
    try:
        with open(path, encoding="utf-8") as f:
            for number, line in enumerate(f, 1):
                try:
                    data = json.loads(line)
                    entry = (_codex_entry if "payload" in data else _claude_entry)(data)
                except (ValueError, AttributeError, TypeError):
                    continue
                if entry and (entry["text"].strip() or entry["calls"]):
                    entries.append(entry)
                    lines.append(number)
    except OSError:
        pass
    user, _, current = turn(entries)
    assistants = [i for i in range(len(entries) - len(current), len(entries))
                  if entries[i]["role"] == "assistant" and entries[i]["text"].strip()]
    index = assistants[-1] if assistants else None
    located = index is not None and norm(entries[index]["text"]) == norm(message)
    response_line = lines[index] if located else None
    # 응답 뒤의 참조나 다른 턴의 같은 문구를 이번 응답의 근거로 쓰지 않는다.
    considered = entries[:index + 1] if located else entries
    for i in range(len(considered) - 1, -1, -1):
        entry = considered[i]
        refs = set(TASK_REF.findall(entry["text"] + "\n" +
                                    json.dumps(entry["calls"], ensure_ascii=False)))
        if not refs:
            continue
        tasks = {task_id(f"입력 문서: {ref}") for ref in refs}
        evidence = f"{lines[i]}"
        if len(tasks) == 1 and all((ROOT / ref).is_file() for ref in refs):
            return user, next(iter(tasks)) or "미확인", evidence, response_line
        return user, "미확인", evidence + " · 모호하거나 문서 없음", response_line
    return user, "미확인", "미확인", response_line


def record(data: dict, runner: str = "claude") -> None:
    transcript = data.get("transcript_path") or (
        session_path(data, runner) if data.get("session_id") else "")
    data = {**data, "transcript_path": transcript}
    message = data.get("last_assistant_message") or ""
    hits = sorted(set(ADMIT.findall(plain(message))))
    if not hits or is_child(data):
        return
    now = datetime.now()
    session_id = data.get("session_id") or "unknown"
    sid = session_id[:8]
    path = INCIDENT / f"{now:%Y%m%d}-{sid}.md"
    role = resolve_role(data) or "미확인"
    user, task, evidence, response_line = context(transcript, data.get("last_assistant_message") or "")
    if not path.is_file():
        INCIDENT.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# 자동 감지 {now:%Y-%m-%d} {sid}\n\n"
            f"- 사건 ID: {now:%Y%m%d}-{sid}\n"
            f"- 세션 ID: {session_id}\n"
            f"- 관련 작업 ID: {task}\n"
            f"- 발생·확인 시점: {now:%Y-%m-%d %H:%M}\n"
            f"- 관련 담당: {role}\n"
            f"- 조치 상태: 미착수\n\n"
            f"## 감지 기록\n\n", encoding="utf-8")
    excerpt = without_paths(message)
    request = without_paths(user)
    request_fence, excerpt_fence = fence_for(request), fence_for(excerpt)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- {now:%Y-%m-%d %H:%M}\n"
                f"  - 감지 표현: {', '.join(hits)}\n"
                f"  - 관련 작업 ID: {task}\n"
                f"  - 근거 작업 위치: {evidence}\n"
                f"  - runner: {runner}\n"
                f"  - 응답 위치: "
                + (str(response_line) if response_line else "미확인") + "\n"
                f"  - 사용자 메시지:\n\n"
                f"    {request_fence}\n    " + request.replace("\n", "\n    ") + "\n"
                f"    {request_fence}\n"
                f"  - 응답 발췌:\n\n"
                f"    {excerpt_fence}\n    " + excerpt.replace("\n", "\n    ") + "\n"
                f"    {excerpt_fence}\n")


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    args = parser.parse_args()
    try:
        record(json.load(sys.stdin), args.runner)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
