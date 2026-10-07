"""hook이 쓰는 작업 문서 경로와 문서 절 읽기 함수이다.

경로 값은 공통 지침 `경로 목록`의 상태·입력·조사 문서 행과 같다.

사용: check_tool_use.py, record_incident.py, save_agent_result.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "log" / "state"
ACTIVE = STATE / ".active"
SUFFIX = {"state": "", "input": "-input", "research": "-research"}
TASK_ID = re.compile(r"^(?:입력|상태|조사) 문서: log/state/([\w.\-]+?)(?:-input|-research)?\.md\s*$", re.M)
HEADING = re.compile(r"^(#{1,6}) (.*)$")


def doc(task: str, kind: str) -> Path:
    return STATE / f"{task}{SUFFIX[kind]}.md"


def task_id(text: str) -> str | None:
    m = TASK_ID.search(text or "")
    return m.group(1) if m else None


def section(text: str, title: str) -> str:
    """title로 시작하는 제목 절의 본문을 같은 수준 이상의 다음 제목 전까지 반환한다. 없으면 빈 문자열이다."""
    out: list[str] = []
    level = None
    for line in text.splitlines():
        m = HEADING.match(line)
        if m and level is not None and len(m.group(1)) <= level:
            break
        if m and level is None and m.group(2).startswith(title):
            level = len(m.group(1))
            continue
        if level is not None:
            out.append(line)
    return "\n".join(out)
