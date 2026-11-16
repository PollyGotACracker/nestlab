"""작업 문서의 생성·완료 시각과 작업 선택 색인(.agent-logs/task/index.md)을 다룬다.
입력은 프로젝트 루트와 작업 ID이며 경계 밖 경로는 거부한다.
반환: 기록한 문서 본문, 선택된 경과 문서 경로, 색인 편집 차단 사유.
한계: 작업을 지정한 색인 갱신은 그 작업 한 줄만 바꾸고, 전체 재구성은 모든 경과 문서를 읽기만 한다.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import tempfile

from sub_paths import INDEX_TEMPLATE, TASK_REL, TASK_INDEX_REL

TASK = r"\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*"
CURRENT, PENDING, COMPLETE = "## 현재 진행 작업", "## 미완료 작업 목록", "## 완료 작업 목록"
SECTIONS = (CURRENT, PENDING, COMPLETE)
ITEM = re.compile(rf"^- \[([^\[\]\n]+)\]\(({TASK})/status\.md\)(?:, (계획|구현|완료))?$")
STAMP = r"(\d{4}\.\d\d\.\d\d \d\d:\d\d:\d\d)"
LEGACY_STAGES = {"착수": "계획", "조사": "계획", "진행": "구현"}
REASON_INDEX_SECTION = f"{TASK_INDEX_REL}: `{CURRENT}` 절만 편집하고 다른 절과 HTML 주석은 유지하십시오."
REASON_INDEX_LINE = (f"{TASK_INDEX_REL}: `{CURRENT}` 절에는 `- [<작업명>](<작업-id>/status.md), <작업 단계>` 형식의 "
                     "줄 하나만 쓰십시오.")
REASON_INDEX_TASK = f"{TASK_INDEX_REL}: 작업 폴더 {TASK_REL}/{{task}}/status.md가 있는 작업을 선택하십시오."


# 지정한 루트 안의 실제 경로만 반환한다.
def local(root: Path, path: Path) -> Path:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("작업 경로가 현재 프로젝트 밖입니다.")
    return path


# KST 현재 시각을 `YYYY.MM.DD HH:MM:SS`로 반환한다.
def now_text() -> str:
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y.%m.%d %H:%M:%S")


# 작업 ID 바로 아래에 생성 시각과 완료 시각을 기록한 본문을 반환한다. 이미 기록한 시각은 유지한다.
def stamp_status(path: Path, text: str, completed: bool = False) -> str:
    created = re.search(rf"(?m)^- 생성 시각: {STAMP}$", text)
    stat = path.stat() if path.is_file() else None
    birth = getattr(stat, "st_birthtime", stat.st_ctime) if stat else None
    start = created[1] if created else (datetime.fromtimestamp(birth, timezone(timedelta(hours=9))).strftime(
        "%Y.%m.%d %H:%M:%S") if birth else now_text())
    finished = re.search(rf"(?m)^- 완료 시각: {STAMP}$", text)
    end = (finished[1] if finished else now_text()) if completed else "없음"
    text = re.sub(r"(?m)^- (?:생성|완료) 시각:.*\n?", "", text)
    return re.sub(r"(?m)^- 작업 ID:.*$", lambda m: m[0] + f"\n- 생성 시각: {start}\n- 완료 시각: {end}", text, count=1)


# 색인 본문을 반환한다. 색인이 없으면 양식 본문이다.
def index_text(root: Path) -> str:
    index = local(root, root / TASK_INDEX_REL)
    return (index if index.is_file() else INDEX_TEMPLATE).read_text(encoding="utf-8-sig").replace("\r\n", "\n")


# 본문을 (머리말 줄, {절 제목: 절 줄 목록}) 으로 나눈다. 양식의 절이 없으면 ValueError이다.
def split_index(text: str) -> tuple[list[str], dict[str, list[str]]]:
    lines = text.rstrip("\n").split("\n")
    marks = [lines.index(title) if title in lines else -1 for title in SECTIONS]
    if -1 in marks or marks != sorted(marks):
        raise ValueError(REASON_INDEX_SECTION)
    ends = marks[1:] + [len(lines)]
    return lines[:marks[0]], {title: lines[start + 1:end] for title, start, end in zip(SECTIONS, marks, ends)}


# 절 줄 목록에서 항목 줄의 작업 ID 목록을 반환한다.
def item_tasks(lines: list[str]) -> list[str]:
    return [m[2] for line in lines if (m := ITEM.match(line))]


# 선택된 작업이 하나이면 해당 경과 문서를 반환한다. 없거나 여러 개면 None이다.
def selected_status(root: Path) -> Path | None:
    if not (root / TASK_INDEX_REL).is_file():
        return None
    try:
        tasks = item_tasks(split_index(index_text(root))[1][CURRENT])
    except ValueError:
        return None
    if len(tasks) != 1:
        return None
    path = local(root, root / TASK_REL / tasks[0] / "status.md")
    return path if path.is_file() else None


# 경과 문서에서 (제목, 단계, 생성 시각, 완료 시각)을 읽는다. 시각이 없으면 작업 ID의 시각으로 대신한다.
def status_row(root: Path, task: str) -> tuple[str, str, str, str]:
    path = local(root, root / TASK_REL / task / "status.md")
    text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    title = re.search(r"(?m)^# (.+)$", text)
    stage = re.search(r"(?m)^- 작업 단계:[ \t]*(\S+)", text)
    phase = LEGACY_STAGES.get(stage[1], stage[1]) if stage else "계획"
    fallback = f"{task[:4]}.{task[4:6]}.{task[6:8]} {task[9:11]}:{task[11:13]}:{task[13:15]}"
    created = re.search(rf"(?m)^- 생성 시각: {STAMP}$", text)
    finished = re.search(rf"(?m)^- 완료 시각: {STAMP}$", text)
    label = (title[1] if title else task).replace("[", "(").replace("]", ")")
    return label, phase, created[1] if created else fallback, finished[1] if finished else fallback


# 절 줄 목록의 항목 줄을 시각 내림차순으로 다시 배치한다. 주석과 목록이 아닌 줄은 항목 앞에 그대로 두고,
# 항목 형식이 아닌 목록 줄(`- <작업-id>, <단계>` 등)은 지운다.
def place(root: Path, lines: list[str], items: list[str], key: int) -> list[str]:
    others = [line for line in lines if not line.startswith("- ")]
    while others and not others[-1].strip():
        others.pop()
    items = sorted(items, key=lambda line: (status_row(root, ITEM.match(line)[2])[key], ITEM.match(line)[2]), reverse=True)
    return others + ([""] if others else []) + items + [""]


# 지정 작업의 색인 줄을 추가, 이동, 갱신한다. task가 None이면 작업 폴더의 모든 경과 문서로 목록을 다시 구성한다.
# current가 True이면 현재 진행 작업으로 선택하고, 아니면 이미 선택된 작업의 줄만 갱신한다. 완료된 작업은 현재 절에서 뺀다.
# 색인 경로를 반환한다.
def refresh_index(root: Path, task: str | None = None, current: bool | None = None) -> Path:
    folder = local(root, root / TASK_REL)
    folder.mkdir(parents=True, exist_ok=True)
    head, sections = split_index(index_text(root))
    tasks = [task] if task is not None else [p.parent.name for p in sorted(folder.glob("*/status.md"))
                                             if re.fullmatch(TASK, p.parent.name)]
    if task is None:
        # 전체 재구성은 폴더가 없는 작업 줄을 지운다.
        for title in SECTIONS:
            sections[title] = [x for x in sections[title] if not ITEM.match(x) or ITEM.match(x)[2] in tasks]
    for name in tasks:
        label, phase, _, _ = status_row(root, name)
        line = f"- [{label}]({name}/status.md)" + ("" if phase == "완료" else f", {phase}")
        rows = {title: [x for x in body if ITEM.match(x) and ITEM.match(x)[2] != name] for title, body in sections.items()}
        chosen = item_tasks(sections[CURRENT])
        if phase != "완료" and (current or name in chosen):
            rows[CURRENT] = [line]
        rows[COMPLETE if phase == "완료" else PENDING].append(line)
        for title, key in ((CURRENT, 2), (PENDING, 2), (COMPLETE, 3)):
            sections[title] = place(root, sections[title], rows[title], key)
    body = "\n".join(head + [line for title in SECTIONS for line in [title, *sections[title]]]).rstrip("\n") + "\n"

    # 임시 파일에 쓴 뒤 교체해 부분 기록을 남기지 않는다.
    index = local(root, root / TASK_INDEX_REL)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=folder, delete=False) as stream:
        stream.write(body)
        temporary = Path(stream.name)
    try:
        temporary.replace(index)
    finally:
        temporary.unlink(missing_ok=True)
    return index


# 색인 편집이 현재 진행 작업 절만 바꾸고 줄 형식과 작업 폴더 조건을 지키는지 판정한다.
# old는 편집 전 본문(없으면 None), new는 편집 후 본문이다. 위반이면 사유, 통과면 None을 반환한다.
def index_edit_reason(root: Path, old: str | None, new: str | None) -> str | None:
    if new is None:
        return None
    try:
        before = split_index((old if old is not None else INDEX_TEMPLATE.read_text(encoding="utf-8-sig")).replace("\r\n", "\n"))
        after = split_index(new.replace("\r\n", "\n"))
    except ValueError as error:
        return str(error)
    strip = lambda lines: [line for line in lines if line.strip()]
    if strip(before[0]) != strip(after[0]) or any(strip(before[1][t]) != strip(after[1][t]) for t in (PENDING, COMPLETE)):
        return REASON_INDEX_SECTION
    kept = [line for line in strip(after[1][CURRENT]) if not line.startswith("- ")]
    if kept != [line for line in strip(before[1][CURRENT]) if not line.startswith("- ")]:
        return REASON_INDEX_SECTION
    items = [line for line in strip(after[1][CURRENT]) if line.startswith("- ")]
    if len(items) > 1 or any(not (m := ITEM.match(line)) or not m[3] or m[3] == "완료" for line in items):
        return REASON_INDEX_LINE
    for line in items:
        task = ITEM.match(line)[2]
        if not local(root, root / TASK_REL / task / "status.md").is_file():
            return REASON_INDEX_TASK.format(task=task)
    return None
