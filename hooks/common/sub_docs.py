"""hook이 쓰는 작업 문서 경로와 문서 절을 읽는다.
경로 값은 공통 지침 `경로 목록`의 경과, 명세, 조사, 반증 문서 행과 같다.
log/state의 기존 작업 문서는 임시로 기존 경로와 기존 절 이름으로 읽는다.

호출자:
- hooks/check_tool_use.py와 그 하위 모듈
- hooks/check_invalid_response.py
- hooks/check_workflow_response.py
- hooks/save_subagent_result.py
- hooks/common/sub_workflow.py
입력: 작업 ID, 작업 문서 본문, 저장소 경로
반환:
- record_leak는 사건 문서 경로나 세션 기록 정보가 든 첫 줄 또는 None
- project_root, doc, task_doc, sibling은 Path
- doc_kind는 문서 종류(state, input, research, refute) 또는 None
- task_id는 작업 ID 또는 None
- legacy는 기존 절 이름 문자열
- loop_count는 반증 회차 수
- titles와 missing_titles는 제목 목록
- section은 절 본문 문자열
- review_done은 검토 필요 상태의 허용 값 튜플(스킬 원본을 읽지 못하면 ValueError)
- saved_report는 bool
- 차단 사유 문자열은 없음
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# 문서 종류별 작업 폴더 안 파일 이름이다.
FILE = {"state": "status", "input": "spec", "research": "research", "refute": "refute"}
# 임시: log/state 기존 작업 문서의 파일 접미사이다. 기존 로그 삭제 뒤 제거한다.
LEGACY_SUFFIX = {"state": "", "input": "-input", "research": "-research"}
# 둘째 갈래는 임시 경로이다. 기존 로그 삭제 뒤 제거한다.
TASK_ID = re.compile(
    r"^(?:명세|입력|상태|조사|반증) 문서: (?:log/task/([\w.\-]+)/(?:status|spec|research|refute)\.md"
    r"|log/state/([\w.\-]+?)(?:-input|-research)?\.md)\s*$", re.M)
# 둘째 갈래는 임시 경로이다. 기존 로그 삭제 뒤 제거한다.
DOC_PATH = re.compile(
    r"^log/task/[\w.\-]+/(status|spec|research|refute)\.md$"
    r"|^log/state/(?!\.)[\w.\-]+?(-input|-research)?\.md$")
LOOP_ROW = re.compile(r"^\| *(\d+) *\|", re.M)
HEADING = re.compile(r"^(#{1,6}) (.*)$")
TOKEN = re.compile(r"\"[^\"]*\"|'[^']*'|[^\s;&|<>()=\"']+")
# 세션 기록 경로, 기록 파일, 세션, thread, 서브에이전트 id, 사건 ID, 사건 문서 파일 경로 형식이다.
SESSION = re.compile(
    r"\.claude[\\/]+projects|\.codex[\\/]+sessions|\.jsonl\b|\brollout-\d|\bagent-[0-9a-f]{8,}\b"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"  # 세션 및 thread UUID
    r"|\b\d{8}(?:-\d{4})?-(?:[0-9a-f]{8}|unknown)\b"  # 사건 ID: 날짜, 선택 시분, 세션 접두어
    r"|\blog[\\/]+incident[\\/]+[\w.\-]+\.md\b", re.I)  # 사건 문서 파일 경로


# text에서 사건 문서 파일 경로나 세션 기록 정보가 든 첫 줄이다. 없으면 None이다.
def record_leak(text: str) -> str | None:
    return next((line.strip()[:120] for line in (text or "").splitlines() if SESSION.search(line)), None)


# 도구 원본과 별개로 입력 cwd의 Git 루트 또는 cwd를 사용한다.
def project_root(cwd: str | Path | None = None) -> Path:
    # 입력 경로 또는 현재 폴더를 기준 폴더로 삼는다.
    base = Path(cwd or Path.cwd()).resolve()

    # git 최상위 폴더를 찾고, 없으면 기준 폴더를 반환한다.
    try:
        result = subprocess.run(["git", "-C", str(base), "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, encoding="utf-8", timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return base


# 임시: log/state 기존 문서의 기존 절 이름이다. 기존 로그 삭제 뒤 제거한다.
def legacy(title: str) -> str:
    return title.replace("검증", "검수").replace("결정 기록", "작업 기록")  # 임시 기존 이름


# 저장소 루트, 작업 ID, 문서 종류(state, input, research, refute)로 작업 문서 경로를 반환한다.
# 작업 폴더에 문서가 없고 log/state에 기존 문서가 있으면 기존 경로를 반환한다.
def task_doc(root: Path, task: str, kind: str) -> Path:
    new = root / "log" / "task" / task / f"{FILE[kind]}.md"
    old = root / "log" / "state" / f"{task}{LEGACY_SUFFIX.get(kind, '-' + kind)}.md"  # 임시 기존 경로
    return old if not new.exists() and old.exists() else new


# 작업 ID와 문서 종류로 작업 문서 경로를 반환한다.
def doc(task: str, kind: str, cwd: str | Path | None = None) -> Path:
    return task_doc(project_root(cwd), task, kind)


# 같은 작업의 다른 종류 문서 경로이다. 기존 log/state 문서는 기존 경로를 쓴다.
def sibling(path: Path, kind: str) -> Path:
    if path.parent.parent.name == "task":
        return path.with_name(f"{FILE[kind]}.md")
    task = re.sub(r"-(?:input|research)$", "", path.stem)  # 임시 기존 경로
    return path.with_name(f"{task}{LEGACY_SUFFIX.get(kind, '-' + kind)}.md")


# 저장소 기준 상대 경로가 작업 문서면 종류(state, input, research, refute)이다. 아니면 None이다.
def doc_kind(rel: str) -> str | None:
    m = DOC_PATH.match(rel)
    if not m:
        return None
    if m.group(1):
        return next(k for k, v in FILE.items() if v == m.group(1))
    return {"-input": "input", "-research": "research", None: "state"}[m.group(2)]  # 임시 기존 경로


# 반증 문서(또는 기존 조사 문서) 본문에서 `조사 및 반증 루프` 절 안의 회차 행 수이다.
def loop_count(text: str) -> int:
    return len(LOOP_ROW.findall(section(text, "조사 및 반증 루프")))


# 문장에 적힌 작업 문서 경로에서 작업 ID를 반환한다. 없으면 None이다.
def task_id(text: str) -> str | None:
    m = TASK_ID.search(text or "")
    return (m.group(1) or m.group(2)) if m else None


SAVED_LINE = "조사 결과를 {path}의 `{slot}` 칸에 저장했습니다."
# 둘째 갈래는 임시 경로이다. 기존 로그 삭제 뒤 제거한다.
SAVED_REPORT = re.compile(
    r"^조사 결과를 (log/task/[\w.\-]+/(?:research|refute)\.md|log/state/[\w.\-]+-research\.md)"
    r"의 `([^`\n]+)` 칸에 저장했습니다\.$")


# report가 하네스가 만든 SAVED_LINE 문장 한 줄이고 그 칸이 조사 문서에 실제로 있을 때만 True이다.
def saved_report(report: str, cwd: str | Path | None = None, refute: bool = False) -> bool:
    # 보고 문장 형식을 확인하고 조사 문서를 읽는다.
    m = SAVED_REPORT.match((report or "").strip())
    if not m:
        return False
    try:
        text = (project_root(cwd) / m.group(1)).read_text(encoding="utf-8")
    except OSError:
        return False

    # 칸의 원문 코드 블록이 비어 있지 않은지 확인한다. 절은 제목 문자열로 찾고 `#` 개수는 보지 않는다.
    slot = m.group(2).strip()
    block = re.search(rf"^#{{1,6}} {re.escape(slot)}[^\n]*\n+(`{{3,}})[^\n]*\n(.*?)\n\1[ \t]*$", text, re.M | re.S)
    body = block.group(2).strip() if block else ""
    if not body:
        return False

    # 반증 보고는 마지막 회차 칸이어야 한다.
    if refute:
        rounds = [int(n) for n in re.findall(r"^#{1,6} (\d+)회차 반증 원문", text, re.M)]
        return bool(rounds) and slot == f"{max(rounds)}회차 반증 원문"
    return True


SKILLS = ROOT / "shared" / "skills"
FENCE_LINE = re.compile(r"^\s*(`{3,}|~{3,})")


# 코드 블록 밖 제목 문자열 목록이다. `#` 개수는 보지 않는다.
def titles(text: str) -> list[str]:
    # 코드 블록 밖의 제목 줄만 모은다.
    out, fence = [], ""
    for line in (text or "").splitlines():
        f = FENCE_LINE.match(line)
        if f:
            mark = f.group(1)
            if not fence:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = ""
            continue
        m = HEADING.match(line)
        if m and not fence:
            out.append(m.group(2).strip())
    return out


TEMPLATE_TITLE = "Template"


# 본문 첫 코드 블록의 안쪽 줄이다. 코드 블록이 없으면 빈 문자열이다.
def first_block(text: str) -> str:
    out, fence = [], ""
    for line in text.splitlines():
        f = FENCE_LINE.match(line)
        if f and not fence:
            fence = f.group(1)
        elif f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence):
            break
        elif fence:
            out.append(line)
    return "\n".join(out)


# 스킬 본문 Template 절 첫 코드 블록의 제목 중 자리표시(`<...>`)가 없는 것이다. 없으면 빈 목록이다.
def template_titles(skill: str) -> list[str]:
    path = SKILLS / skill / "SKILL.md"
    body = section(path.read_text(encoding="utf-8"), TEMPLATE_TITLE) if path.is_file() else ""
    return [t for t in titles(first_block(body)) if "<" not in t]


# 템플릿 필수 제목 중 new에 없는 것이다. old가 있으면 old에 있던 제목만 따진다(이전 양식 문서 보존).
def missing_titles(new: str, skill: str, old: str = "") -> list[str]:
    # 템플릿 필수 제목과 새 문서의 제목을 구한다.
    required = template_titles(skill)
    have = set(titles(new))

    # 이전 문서가 있으면 이전에 있던 제목만 필수로 본다.
    if old:
        before = set(titles(old))
        required = [t for t in required if t in before]
    return [t for t in required if t not in have]


# title로 시작하는 제목 절의 본문을 같은 수준 이상의 다음 제목 전까지 반환한다. 코드 블록 안의 줄은 제목이 아니다. 없으면 빈 문자열이다.
def section(text: str, title: str) -> str:
    # 제목 줄부터 같은 수준 이상의 다음 제목 전까지 줄을 모은다.
    out: list[str] = []
    level = None
    fence = ""
    for line in text.splitlines():
        # 코드 블록 안의 줄은 제목으로 보지 않는다.
        f = FENCE_LINE.match(line)
        if f and not fence:
            fence = f.group(1)
        elif f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence):
            fence = ""
        m = None if fence or f else HEADING.match(line)
        if m and level is not None and len(m.group(1)) <= level:
            break
        if m and level is None and m.group(2).startswith((title, legacy(title))):
            level = len(m.group(1))
            continue
        if level is not None:
            out.append(line)
    return "\n".join(out)


REVIEW_COMMENT = re.compile(r"<!--\s*모든 목록이\s*(.+?)\s*일 때 승인 가능\s*-->")

# nestlab-log-state 템플릿 주석에서 검토 필요 상태의 허용 값 튜플을 읽는다. 읽지 못하거나 주석이 없으면 ValueError를 던진다.
def review_done() -> tuple[str, ...]:
    try:
        text = (SKILLS / "nestlab-log-state" / "SKILL.md").read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"nestlab-log-state 스킬 원본을 읽지 못했습니다({error}).")
    match = REVIEW_COMMENT.search(text)
    if not match:
        raise ValueError("nestlab-log-state 템플릿에 `모든 목록이 ... 일 때 승인 가능` 주석이 없습니다.")
    return tuple(re.split(r"\s*(?:또는|,)\s*", match[1]))
