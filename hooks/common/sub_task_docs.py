"""고정 작업 문서 커맨드의 설치 내용과 커맨드 해석을 확인한다.

입력: 사용자 홈과 고정 커맨드 이름.
반환: 실행 파일별 원본 내용 또는 설치된 커맨드의 일치 여부.
"""
from __future__ import annotations

from pathlib import Path
import re
import shlex
import shutil
import sys

from sub_paths import CREATE_TASK_DOCS, TASK_REL

SCRIPT = CREATE_TASK_DOCS
MARKER = "nestlab task docs launcher"
STAGES = {"plan": ("status", "research", "refute", "spec")}
DOCUMENT_STAGES = {"status": ("계획", "구현", "완료"),
                   "research": ("계획",), "refute": ("계획",), "spec": ("계획", "구현")}
TASK_ID = re.compile(r"\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*")
STAGE_LINE = re.compile(r"(?m)^- 작업 단계:[ \t]*(.*)$")


# 문서의 작성 단계가 아니면 차단 사유를 반환한다.
def document_stage_reason(kind: str, stage: str | None) -> str | None:
    allowed = DOCUMENT_STAGES[kind]
    if stage in allowed:
        return None
    return f"작업 단계 [{stage or '미지정'}]에서는 {kind}.md를 쓸 수 없습니다. 허용 단계: {', '.join(allowed)}."


# 출력 경계를 확인하고 현재 단계에서 빠진 문서만 생성한다.
def create(root: Path, task: str, title: str, stage: str) -> list[Path]:
    from sub_docs import template_text
    if not TASK_ID.fullmatch(task):
        raise ValueError("작업 ID는 YYYYMMDD-HHMMSS-kebab-case 형식이어야 합니다.")
    if not title.strip() or "\n" in title or "\r" in title:
        raise ValueError("제목은 비어 있지 않은 한 줄이어야 합니다.")
    if stage not in STAGES:
        raise ValueError(f"단계는 {', '.join(STAGES)} 중 하나로 지정하십시오.")
    root = root.resolve()
    folder = root / TASK_REL / task
    paths = [folder / f"{kind}.md" for kind in STAGES[stage]]
    status = folder / "status.md"
    for path in [folder, status, *paths]:
        if not path.resolve().is_relative_to(root):
            raise ValueError("저장소 밖 출력 경로는 사용할 수 없습니다.")
    current = None
    if status.is_file():
        match = STAGE_LINE.search(status.read_text(encoding="utf-8-sig"))
        current = match[1].strip() if match else None
    folder.mkdir(parents=True, exist_ok=True)
    created = []
    for path in paths:
        text = template_text(path.stem).replace("<작업-id>", task).replace("<작업명>", title.strip())
        if path.stem == "status":
            from sub_task_index import stamp_status
            text = STAGE_LINE.sub("- 작업 단계: 계획", text, count=1)
            text = stamp_status(path, text)
        elif path.stem == "research":
            text = text.replace("# 조사 문서: <조사 주제명>", f"# 조사 문서: {title.strip()}", 1)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(text + "\n")
        except FileExistsError:
            continue
        created.append(path)
    from sub_task_index import refresh_index
    refresh_index(root)
    refresh_index(root, task, current=True)
    return created


# 구현 승인 요청에 표시하는 명세 요구사항 항목의 칸이다.
APPROVAL_FIELDS = ("목표", "기대 결과", "한계", "잠재 위험")


# 경과 문서 경로로 구현 승인 요청 응답 본문을 생성한다. 작업 문서 링크와 요구사항 절만 쓴다.
def approval_text(status: Path) -> str:
    folder = status.parent
    title = re.search(r"(?m)^# (.+)$", status.read_text(encoding="utf-8"))[1].strip()
    rel = f"{TASK_REL}/{folder.name}"
    out = ["# 구현 승인 요청", "", "## 작업 문서", ""]
    out += [f"- [{title} {name}]({rel}/{file})" for name, file in
            (("경과 문서", "status.md"), ("명세 문서", "spec.md"), ("조사 문서", "research.md"), ("반증 문서", "refute.md"))
            if (folder / file).is_file()]
    return "\n".join(out + ["", *approval_items((folder / "spec.md").read_text(encoding="utf-8"))]) + "\n"


# 명세 문서 본문으로 구현 승인 요청의 `## 요구사항` 절 줄 목록을 생성한다. 폐기되지 않은 요구사항의 제목과 표시 칸 원문만 쓴다.
# 기존 구조(`- 칸:` 줄) 명세는 칸 아래 들여쓴 줄을 한 단계 내어 쓰고, 칸이 없으면 `- 기록 없음`을 쓴다.
def approval_items(spec: str) -> list[str]:
    from sub_docs import section, strip_comments
    from sub_spec import blocks, heading_form, span, text_of
    out = ["## 요구사항"]

    # 요구사항 항목마다 제목과 표시 칸을 원문 그대로 옮긴다.
    body = strip_comments(section(spec.replace("\r\n", "\n"), "요구사항 목록"))
    for number, block in blocks(body):
        heading = block.split("\n", 1)[0].strip()
        if number is None or heading.endswith(". 폐기"):
            continue
        out += ["", heading]
        for name in APPROVAL_FIELDS:
            # 기존 구조의 `- 결과:` 칸은 기대 결과 칸 자리에 기존 이름 그대로 쓴다. 기존 구조의 `- 기대 결과:`는 안 목록이라 쓰지 않는다.
            if not heading_form(block) and name == "기대 결과":
                name = "결과"
            raw, start, end = span(block, name)
            lines = text_of(block, name) if heading_form(block) else "\n".join(l[2:].rstrip() for l in raw[start:end]).strip()
            out += ["", f"#### {name}", "", lines or "- 기록 없음"]
    return out


# 두 셸에서 같은 커맨드 이름을 쓰도록 설치할 실행 파일 내용을 반환한다.
def launcher_files(home: Path) -> dict[Path, str]:
    folder = home / ".local" / "bin"
    python, script = str(Path(sys.executable).resolve()), str(SCRIPT.resolve())
    shell = f'#!/bin/sh\n# {MARKER}\nexec {shlex.quote(Path(python).as_posix())} -B -X utf8 {shlex.quote(Path(script).as_posix())} "$@"\n'
    windows = f'@echo off\r\n@chcp 65001>nul\r\nrem {MARKER}\r\n"{python.replace("%", "%%")}" -B -X utf8 "{script.replace("%", "%%")}" %*\r\n'
    return {folder / "nestlab-task-docs": shell, folder / "nestlab-task-docs.cmd": windows}


# 커맨드가 설치된 원본 실행 파일과 정확히 같은 내용으로 해석되는지 확인한다.
def trusted_launcher(command: str, home: Path) -> bool:
    if command not in ("nestlab-task-docs", "nestlab-task-docs.cmd"):
        return False
    found = shutil.which(command)
    if not found:
        return False
    resolved = Path(found).resolve()
    try:
        return any(resolved == path.resolve() and path.read_bytes() == body.encode("utf-8")
                   for path, body in launcher_files(home).items())
    except OSError:
        return False
