"""hook이 쓰는 작업 문서 경로와 문서 절을 읽는다.
작업 문서는 작업 폴더의 status, spec, research, refute 파일이며 양식은 작업 문서 양식 폴더에서 읽는다.

호출자:
- hooks/check_tool_use.py와 그 하위 모듈
- hooks/check_invalid_response.py
- hooks/check_workflow_response.py
- hooks/save_subagent_result.py
- hooks/common/sub_document.py, hooks/common/sub_gate.py
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
- review_pending은 경과 문서 검토 필요 절에서 확정 답이 없는 항목 이름 목록
- review_done_numbers는 확정 답이 있는 검토 번호 집합
- measure_pending은 모델 호출 실측 필요 절의 미완료 항목 이름 목록
- saved_report는 bool
- 차단 사유 문자열은 없음
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from sub_paths import SKILL_DIR, TASK_REL, TEMPLATE_DIR

ROOT = Path(__file__).resolve().parents[2]
# 문서 종류별 작업 폴더 안 파일 이름이다.
FILE = {"state": "status", "input": "spec", "research": "research", "refute": "refute"}
TASK_ID = re.compile(
    rf"^input: {TASK_REL}/([\w.\-]+)/(?:status|spec|research|refute)\.md\s*$", re.M)
DOC_PATH = re.compile(rf"^{TASK_REL}/[\w.\-]+/(status|spec|research|refute)\.md$")
LOOP_ROW = re.compile(r"^\| *(\d+) *\|", re.M)
HEADING = re.compile(r"^(#{1,6}) (.*)$")
TOKEN = re.compile(r"\"(?:\\.|[^\"\\])*\"|'[^']*'|[^\s;&|<>()=\"']+")
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


# 기존 문서의 이전 절 이름이다. `구현 승인`의 이전 명칭 `실행 승인`과 임시 기존 이름 `검수`를 읽는다.
def legacy(title: str) -> str:
    return title.replace("검증", "검수").replace("구현 승인", "실행 승인").replace("조사 승인", "착수 승인")


# 저장소 루트, 작업 ID, 문서 종류(state, input, research, refute)로 작업 문서 경로를 반환한다.
def task_doc(root: Path, task: str, kind: str) -> Path:
    return root / TASK_REL / task / f"{FILE[kind]}.md"


# 작업 ID와 문서 종류로 작업 문서 경로를 반환한다.
def doc(task: str, kind: str, cwd: str | Path | None = None) -> Path:
    return task_doc(project_root(cwd), task, kind)


# 같은 작업의 다른 종류 문서 경로이다.
def sibling(path: Path, kind: str) -> Path:
    return path.with_name(f"{FILE[kind]}.md")


# 저장소 기준 상대 경로가 작업 문서면 종류(state, input, research, refute)이다. 아니면 None이다.
def doc_kind(rel: str) -> str | None:
    m = DOC_PATH.match(rel)
    return next(k for k, v in FILE.items() if v == m.group(1)) if m else None


# 반증 문서(또는 기존 조사 문서) 본문에서 `조사 및 반증 루프` 절 안의 회차 행 수이다.
def loop_count(text: str) -> int:
    return len(LOOP_ROW.findall(section(text, "조사 및 반증 루프")))


# 문장에 적힌 작업 문서 경로에서 작업 ID를 반환한다. 없으면 None이다.
def task_id(text: str) -> str | None:
    m = TASK_ID.search(text or "")
    return m.group(1) if m else None


SAVED_LINE = "조사 결과를 {path}의 `{slot}` 칸에 저장했습니다."
SAVED_REPORT = re.compile(
    rf"^조사 결과를 ({TASK_REL}/[\w.\-]+/(?:research|refute)\.md)"
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

    # 결과 절이면 절 본문에 자리표시자가 아닌 결과 블록이 있는지, 기존 칸이면 원문 코드 블록이 비어 있지 않은지 확인한다.
    # 절은 제목 문자열로 찾고 `#` 개수는 보지 않는다.
    slot = m.group(2).strip()
    if slot == "조사 결과" or slot.endswith("회차 반증 결과"):
        found = re.search(rf"^(#{{1,6}}) {re.escape(slot)}[ \t]*$", text, re.M)
        rest = text[found.end():] if found else ""
        nxt = re.search(rf"^#{{1,{len(found[1])}}} ", rest, re.M) if found else None
        inner = rest[:nxt.start()] if nxt else rest
        if not (re.search(r"(?m)^### (?![^\n]*<)\S", inner) if slot == "조사 결과" else inner.strip()):
            return False
    else:
        block = re.search(rf"^#{{1,6}} {re.escape(slot)}[^\n]*\n+(`{{3,}})[^\n]*\n(.*?)\n\1[ \t]*$", text, re.M | re.S)
        body = block.group(2).strip() if block else ""
        if not body or body == "기록 없음" or body.startswith("<"):
            return False

    # 반증 보고는 마지막 회차 칸이어야 한다.
    if refute:
        rounds = [int(n) for n in re.findall(r"^#{1,6} (\d+)회차 반증 (?:원문|결과)", text, re.M)]
        return bool(rounds) and slot in (f"{max(rounds)}회차 반증 원문", f"{max(rounds)}회차 반증 결과")
    return True


SKILLS = SKILL_DIR
TEMPLATES = TEMPLATE_DIR
TEMPLATE_NAMES = {"state": "log-status", "input": "log-spec", "research": "log-research", "refute": "log-refute",
                  "nestlab-log-incident": "log-incident", "nestlab-approval": "approval-implement",
                  "nestlab-kickoff": "approval-research"}
# 응답 양식 이름이다. 생성기의 `--print-template` 인자와 같다.
RESPONSE_TEMPLATES = ("question-kickoff", "question-choice", "request-choice", "request-stop", "approval-complete",
                      "approval-research", "approval-implement", "approval-measure")
FENCE_LINE = re.compile(r"^\s*(`{3,}|~{3,})")


# 코드 블록 밖 제목 문자열 목록이다. `#` 개수는 보지 않는다.
def titles(text: str) -> list[str]:
    # 코드 블록 밖의 제목 줄만 추출한다.
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


# 독립 양식의 제목 중 자리표시가 없는 제목을 반환한다.
def template_titles(skill: str) -> list[str]:
    return [t for t in titles(template_text(skill)) if "<" not in t]


# 독립 원본의 파일 전체 내용을 반환한다. 알 수 없는 양식은 오류로 보고한다.
# 작업 문서 종류 이름(status, spec, research, refute, incident)은 `log-` 양식으로 연결한다.
def template_text(skill: str) -> str:
    name = TEMPLATE_NAMES.get(skill, skill)
    if f"log-{name}" in TEMPLATE_NAMES.values():
        name = f"log-{name}"
    if name not in (*TEMPLATE_NAMES.values(), *RESPONSE_TEMPLATES):
        raise ValueError(f"알 수 없는 양식: {skill}")
    return (TEMPLATES / f"{name}.md").read_text(encoding="utf-8").rstrip("\n")


# 독립 양식의 지정 절 본문을 반환한다.
def template_section(skill: str, title: str) -> str:
    return section(template_text(skill), title)


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
    # 새 승인 제목이 있으면 파일 순서와 무관하게 새 절을 기준으로 읽는다.
    if title == "조사 승인 범위" and title in titles(text):
        text = re.sub(r"(?m)^(#{1,6}) 착수 승인 범위\s*$", r"\1 이전 승인 범위", text)
    # 제목 줄부터 같은 수준 이상의 다음 제목 전까지 줄을 추출한다.
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


CODE_OR_OPEN = re.compile(r"(`+).+?(?<!`)\1(?!`)|<!--")


# 코드 블록과 인라인 코드 밖의 HTML 주석을 지운 본문이다. 주석만 있던 줄은 지우고, 닫히지 않은 주석이 있으면 원문을 반환한다.
def strip_comments(text: str) -> str:
    out: list[str] = []
    fence = ""
    comment = False
    dropped = False
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        eol = raw[len(body):]

        # 코드 블록 안의 줄은 그대로 둔다.
        if fence:
            out.append(raw)
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", body):
                fence = ""
            dropped = False
            continue

        # 인라인 코드를 건너뛰며 주석 구간을 뺀 줄을 만든다.
        line, i, had = "", 0, comment
        while i < len(body):
            if comment:
                end = body.find("-->", i)
                if end < 0:
                    break
                i, comment = end + 3, False
                continue
            m = CODE_OR_OPEN.search(body, i)
            if not m:
                line += body[i:]
                break
            if m.group(0) == "<!--":
                line += body[i:m.start()]
                comment, had = True, True
            else:
                line += body[i:m.end()]
            i = m.end()

        # 주석만 있던 줄은 지우고, 그 때문에 빈 줄이 겹치면 하나만 남긴다.
        if had:
            line = line.rstrip()
            if not line:
                dropped = True
                continue
        elif not line.strip() and dropped and out and not out[-1].strip():
            continue
        dropped = False
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if opening:
            fence = opening.group(1)
        out.append(line + eol if had else raw)
    return text if comment else "".join(out)


# 작업 폴더 아래 작업 문서나 사건 폴더 아래 사건 문서면 코드 블록 밖 HTML 주석을 지워 파일에 다시 쓴다. 문서가 아니거나 읽지 못하면 건너뛴다.
def strip_task_comments(path: Path) -> None:
    task_doc_path = path.parent.parent.name == "task" and path.parent.parent.parent.name == "log"
    incident_doc_path = path.parent.name == "incident" and path.parent.parent.name == "log"
    if path.suffix != ".md" or not (task_doc_path or incident_doc_path):
        return
    try:
        old = path.read_bytes().decode("utf-8")
        new = strip_comments(old)
        if new != old:
            path.write_bytes(new.encode("utf-8"))
    except (OSError, UnicodeDecodeError):
        pass


# 경과 문서 검토 필요 절의 확정 줄 형식이다. 확정 안 자리에는 안 라벨 또는 `폐기`가 온다.
REVIEW_LINE = re.compile(r"^- 검토 (\d+)\. ([^:\n]+): (.+?) \(사용자 검토 시각: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\)\s*$")
LEGACY_REVIEW_DONE = re.compile(r"(?m)^\s*- 상태:\s*(?:검토 완료|폐기)\s*$")


# 경과 문서 검토 필요 절의 (항목 첫 줄, 확정 여부) 목록이다. 양식 자리표시 항목은 뺀다.
# 기존 문서의 `- 상태: 검토 완료` 하위 줄도 확정으로 읽는다.
def review_entries(text: str) -> list[tuple[str, bool]]:
    body = strip_comments(section(text, "검토 필요"))
    out = []
    for item in re.split(r"(?m)^(?=- )", body):
        first = item.splitlines()[0] if item.strip() else ""
        if first.startswith("- ") and not re.search(r"<[^<>\n]+>", first):
            out.append((first, bool(REVIEW_LINE.match(first) or LEGACY_REVIEW_DONE.search(item))))
    return out


# 경과 문서 검토 필요 절에서 확정 답이 없는 항목 이름 목록이다.
def review_pending(text: str) -> list[str]:
    return [m[1] if (m := re.match(r"- (검토 \d+\.[^:\n]*)", line)) else line[2:42] for line, done in review_entries(text) if not done]


# 경과 문서 검토 필요 절에서 확정 답이 있는 검토 번호 집합이다.
def review_done_numbers(text: str) -> set[str]:
    return {m[1] for line, done in review_entries(text) if done and (m := re.match(r"- 검토 (\d+)\.", line))}


# 경과 문서 문제 정의 절의 {문제 번호: (확인 시각 기록 여부, 폐기 여부)}이다. 양식 자리표시 항목은 뺀다.
# 확인 시각이 `기록 없음`이면 확인 시각이 없는 것으로 읽는다.
def problem_entries(text: str) -> dict[int, tuple[bool, bool]]:
    out = {}
    for item in re.split(r"(?m)^(?=- )", strip_comments(section(text, "문제 정의"))):
        m = re.match(r"- 문제 (\d+)\. (폐기: )?(.+)", item)
        if not m or re.search(r"<[^<>\n]+>", item.splitlines()[0]):
            continue
        stamp = re.search(r"(?m)^\s+- 사용자 확인 시각:\s*(\S.*?)\s*$", item)
        out[int(m[1])] = (bool(stamp and stamp[1] != "기록 없음" and not stamp[1].startswith("<")), bool(m[2]))
    return out


# 경과 문서 구현 승인 범위의 연결 대상에 적힌 요구사항 번호 집합이다. 번호가 없으면 None이다.
def approval_targets(text: str) -> set[str] | None:
    link = re.search(r"(?m)^\s*-\s*연결 대상:\s*(.+)$", section(text, "구현 승인 범위"))
    numbers = {n for group in re.findall(r"요구사항\s*((?:\d+\s*[,~와과및-]?\s*)+)", link[1]) for n in re.findall(r"\d+", group)} if link else set()
    return numbers or None


# 경과 문서 모델 호출 실측 필요 절(기존 이름 `실측 필요`도 읽는다)에서 상태가 `모델 호출 실측 완료`(기존 `실측 완료`)가 아닌 항목 이름 목록을 반환한다. times는 포함할 시점(사전, 사후)이며 `사후`가 아닌 시점은 사전으로 본다.
# targets가 있으면 `- 요구사항:` 칸의 번호가 targets와 겹치지 않는 항목은 승인 대상과 무관하므로 뺀다.
def measure_pending(text: str, times: tuple[str, ...] = ("사전",), targets: set[str] | None = None) -> list[str]:
    # 항목은 0칸 들여쓴 `- ` 줄이고, 본문은 다음 항목 줄 전까지이다.
    body = section(text, "모델 호출 실측 필요") or section(text, "실측 필요")
    starts = list(re.finditer(r"(?m)^- (.+)$", body))
    out = []
    for i, m in enumerate(starts):
        if re.fullmatch(r"<[^<>]*>", m[1].strip()):  # 양식 자리표시 항목은 실측 항목이 아니다.
            continue
        item = body[m.end():(starts[i + 1].start() if i + 1 < len(starts) else len(body))]
        when = re.search(r"^\s*- 시점:\s*(.+?)\s*$", item, re.M)
        state = re.search(r"^\s*- 상태:\s*(.+?)\s*$", item, re.M)
        linked = re.search(r"^\s*- 요구사항:\s*(.+?)\s*$", item, re.M)
        if targets and linked and re.findall(r"\d+", linked[1]) and not targets & set(re.findall(r"\d+", linked[1])):
            continue
        kind = "사후" if when and when[1] == "사후" else "사전"
        if kind in times and not (state and state[1] in ("모델 호출 실측 완료", "실측 완료")):
            out.append(m[1].strip())
    return out
