"""hook이 쓰는 작업 문서 경로와 문서 절 읽기 함수이다.

경로 값은 공통 지침 `경로 목록`의 상태·입력·조사 문서 행과 같다.

사용: check_tool_use.py, record_incident.py, save_agent_result.py
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUFFIX = {"state": "", "input": "-input", "research": "-research"}
TASK_ID = re.compile(r"^(?:입력|상태|조사) 문서: log/state/([\w.\-]+?)(?:-input|-research)?\.md\s*$", re.M)
HEADING = re.compile(r"^(#{1,6}) (.*)$")


def project_root(cwd: str | Path | None = None) -> Path:
    """도구 원본과 별개로 입력 cwd의 Git 루트 또는 cwd를 사용한다."""
    base = Path(cwd or Path.cwd()).resolve()
    try:
        result = subprocess.run(["git", "-C", str(base), "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, encoding="utf-8", timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return base


def active(cwd: str | Path | None = None) -> Path:
    return project_root(cwd) / "log" / "state" / ".active"


def doc(task: str, kind: str, cwd: str | Path | None = None) -> Path:
    return project_root(cwd) / "log" / "state" / f"{task}{SUFFIX[kind]}.md"


def task_id(text: str) -> str | None:
    m = TASK_ID.search(text or "")
    return m.group(1) if m else None


SAVED_REPORT = re.compile(r"^결과 저장: (log/state/[\w.\-]+-research\.md) (\S.*)$")


def saved_report(report: str, cwd: str | Path | None = None, refute: bool = False) -> bool:
    """report가 하네스가 만든 `결과 저장:` 한 줄이고 그 칸이 조사 문서에 실제로 있을 때만 True이다.

    반증이면 칸이 마지막 회차 `<n>회차 반증 원문`이어야 한다.
    """
    m = SAVED_REPORT.match((report or "").strip())
    if not m:
        return False
    try:
        text = (project_root(cwd) / m.group(1)).read_text(encoding="utf-8")
    except OSError:
        return False
    slot = m.group(2).strip()
    block = re.search(rf"^#{{2,3}} {re.escape(slot)}[^\n]*\n+(`{{3,}})[^\n]*\n(.*?)\n\1[ \t]*$", text, re.M | re.S)
    body = block.group(2).strip() if block else ""
    if not body:
        return False
    if refute:
        rounds = [int(n) for n in re.findall(r"^#{2,3} (\d+)회차 반증 원문", text, re.M)]
        return bool(rounds) and slot == f"{max(rounds)}회차 반증 원문"
    return True


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
