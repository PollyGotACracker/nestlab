"""Stop hook: 메인 에이전트 응답에서 실수·위반 인정 표현을 찾아 사건 문서 초안에 조용히 기록한다.

사용: python record_incident.py --runner <claude|codex>
- 검사 대상은 hook 입력의 last_assistant_message이다. 코드 블록 안은 검사하지 않는다.
- 일치하면 세션별 사건 문서 `log/incident/<YYYYMMDD>-<session 앞 8자>.md`에 항목을 추가한다.
  파일이 없으면 사건 양식의 머리 항목으로 만들고 조치 상태는 `미착수`로 둔다.
- 하위 에이전트 응답(Claude agent_id, Codex 하위 rollout)은 검사하지 않는다.
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

from sub_docs import ROOT
from sub_role import first_line, resolve_role
from sub_session import read_session, turn

INCIDENT = ROOT / "log" / "incident"
# ponytail: 표현 목록 기반 감지이다. 놓치는 표현이 쌓이면 이 목록에 추가한다.
ADMIT = re.compile(
    r"죄송|제 실수|제 잘못|제가 잘못|잘못했|잘못 (?:이해|해석|읽|봤|판단|말씀|맞췄|잡았)|틀렸"
    r"|놓쳤|놓치고|빠뜨|빼먹|누락(?:했|입니다|시켰)|위반했|어겼|어긴"
    r"|확인하지 않고|확인 안 하고|확인 없이|허락 없이|근거 없이|임의로|제 판단으로"
    r"|착오|엉뚱|망가뜨|철회|낭비(?:했|한 것|시켰)|안 했습니다|안 했어요")
FENCE = re.compile(r"^\s*(```|~~~)")


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
    return bool((first_line(data.get("transcript_path")).get("source") or {}).get("subagent"))


def record(data: dict) -> None:
    message = plain(data.get("last_assistant_message") or "")
    hits = sorted(set(ADMIT.findall(message)))
    if not hits or is_child(data):
        return
    now = datetime.now()
    sid = (data.get("session_id") or "unknown")[:8]
    path = INCIDENT / f"{now:%Y%m%d}-{sid}.md"
    role = resolve_role(data) or "미확인"
    user, _, _ = turn(read_session(data.get("transcript_path") or ""))
    if not path.is_file():
        INCIDENT.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# 자동 감지 {now:%Y-%m-%d} {sid}\n\n"
            f"- 사건 ID: {now:%Y%m%d}-{sid}\n"
            f"- 관련 작업 ID: 미확인\n"
            f"- 발생·확인 시점: {now:%Y-%m-%d %H:%M}\n"
            f"- 관련 담당: {role}\n"
            f"- 조치 상태: 미착수\n\n"
            f"## 감지 기록\n\n", encoding="utf-8")
    excerpt = " ".join(message.split())[:300]
    request = " ".join(user.split())[:200]
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- {now:%Y-%m-%d %H:%M}\n"
                f"  - 감지 표현: {', '.join(hits)}\n"
                f"  - 사용자 메시지: {request}\n"
                f"  - 응답 발췌: {excerpt}\n")


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    parser.parse_args()
    try:
        record(json.load(sys.stdin))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
