"""역할별 쓰기 범위이다.

- director 메인 세션:
  - log/ 밖 쓰기(Bash 쓰기 포함)는 사용자 마지막 메시지가 승인 명령으로 끝날 때만 허용한다. log/state/.active는 hook 전용이다.
  - Bash 읽기 명령은 검사하지 않는다.
  - `승인 상태: 승인` 줄을 새로 쓰려면 사용자 마지막 메시지가 승인 명령으로 끝나야 한다.
  - 조사 문서는 sub_research 원문 보존, 입력 문서는 sub_plan 계획 검사와 sub_research 결정 확인을 거친다.
- researcher·reviewer: deny (readonly)
- 그 외 역할과 역할 판별 실패: 통과
"""
from __future__ import annotations

from pathlib import Path

from sub_approval import APPROVED, approved, last_user
from sub_docs import ROOT
from sub_plan import check_plan
from sub_research import check_decision, check_raw
from sub_write import added_lines, read

READONLY = {"researcher", "reviewer"}
DIRECTOR = ("log/",)


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return ""


def check_director_write(data: dict, paths: list[Path]) -> str | None:
    user, _ = last_user(data.get("transcript_path"))
    outside = not paths or any(not rel(path).startswith(DIRECTOR) for path in paths)
    if outside and not approved(user):
        return "director는 승인 없이 log/ 밖에 쓸 수 없습니다. 사용자 마지막 메시지에 승인이 없습니다."
    if any(rel(path).startswith("log/state/.active") for path in paths):
        return "log/state/.active는 hook 전용입니다."
    for path in paths:
        if not rel(path).startswith(DIRECTOR):
            continue
        added, new = added_lines(data, path)
        old = read(path)
        if any(APPROVED.match(l) for l in added) and not approved(user):
            return "사용자 마지막 메시지에 승인이 없습니다. 승인 상태를 승인으로 쓸 수 없습니다."
        if path.name.endswith("-research.md"):
            reason = check_raw(new, old)
        elif path.name.endswith("-input.md"):
            reason = check_plan(new, old) or check_decision(path, new, old)
        else:
            reason = None
        if reason:
            return reason
    return None


def check_write(data: dict, role: str | None, paths: list[Path]) -> str | None:
    if role in READONLY:
        return f"{role}은 읽기 전용입니다. 결과는 반환하면 하네스가 저장합니다."
    if role == "director":
        return check_director_write(data, paths)
    return None
