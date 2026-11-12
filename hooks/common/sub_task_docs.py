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

from sub_paths import CREATE_TASK_DOCS

SCRIPT = CREATE_TASK_DOCS
MARKER = "nestlab task docs launcher"
STAGES = {"kickoff": ("status",), "research": ("research",), "review": ("spec",), "refute": ("refute",)}
DOCUMENT_STAGES = {"status": ("착수", "조사", "구현", "완료"),
                   "research": ("조사",), "refute": ("조사",), "spec": ("조사", "구현")}
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
    root = root.resolve()
    folder = root / "log" / "task" / task
    paths = [folder / f"{kind}.md" for kind in STAGES[stage]]
    status = folder / "status.md"
    for path in [folder, status, *paths]:
        if not path.resolve().is_relative_to(root):
            raise ValueError("저장소 밖 출력 경로는 사용할 수 없습니다.")
    current = None
    if status.is_file():
        match = STAGE_LINE.search(status.read_text(encoding="utf-8-sig"))
        current = match[1].strip() if match else None
    if stage != "kickoff":
        for kind in STAGES[stage]:
            reason = document_stage_reason(kind, current)
            if reason:
                raise ValueError(reason)
        if stage == "review" and current != "조사":
            raise ValueError("spec.md 생성은 research 단계(작업 단계 조사)에서만 허용됩니다.")
    folder.mkdir(parents=True, exist_ok=True)
    created = []
    for path in paths:
        text = template_text(path.stem).replace("<작업-id>", task).replace("<작업명>", title.strip())
        if path.stem == "status":
            text = STAGE_LINE.sub("- 작업 단계: 착수", text, count=1)
        elif path.stem == "research":
            text = text.replace("# 조사 문서: <조사 주제명>", f"# 조사 문서: {title.strip()}", 1)
        elif path.stem == "refute":
            text = text.replace("# 반증 문서\n", f"# 반증 문서: {title.strip()}\n", 1)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(text + "\n")
        except FileExistsError:
            continue
        created.append(path)
    return created


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
