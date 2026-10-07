"""역할별 쓰기 범위와 director Bash 허용 목록이다.

- director 메인 세션:
  - log/state/ 밖이면 deny한다. log/state/.active는 hook 전용이다.
  - Bash는 git 조회(status·log·show·diff·rev-parse·config --get·branch --show-current)와 date 단일 명령만 허용하고 나머지는 deny한다.
  - `승인 상태: 승인` 줄을 새로 쓰려면 사용자 마지막 메시지가 승인 명령으로 끝나야 한다.
  - 조사 문서는 sub_research 원문 보존, 입력 문서는 sub_plan 계획 검사와 sub_research 결정 확인을 거친다.
- researcher·reviewer: deny (readonly)
- assistant 메인 세션: 사용자 마지막 메시지가 승인 명령으로 끝나지 않으면 쓰기를 deny한다.
- 그 외 역할과 역할 판별 실패: 통과
"""
from __future__ import annotations

import re
from pathlib import Path

from sub_approval import APPROVED, approved, last_user
from sub_docs import ROOT
from sub_plan import check_plan
from sub_research import check_decision, check_raw
from sub_write import added_lines, bash_command, read

READONLY = {"researcher", "reviewer"}
DIRECTOR = ("log/state/",)
# director Bash는 git 조회와 date 단일 명령만 허용한다.
# 연결·치환·리다이렉션 문자, 파일 출력·외부 실행 옵션이 있으면 deny한다.
DIRECTOR_BASH = re.compile(
    r"\s*(?!.*(?:--output|--ext-diff))"
    r"(?:git (?:status|log|show|diff|rev-parse|config --get)(?:\s[^;&|<>`$()\n]*)?"
    r"|git branch --show-current"
    r"|date(?:\s+[\"']?\+[^;&|<>`$()\n]*)?)\s*")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return ""


def check_director_bash(data: dict) -> str | None:
    if all(DIRECTOR_BASH.fullmatch(part) for part in bash_command(data).split("&&")):
        return None
    return ("director Bash는 git 조회(status·log·show·diff·rev-parse·config --get·branch --show-current)와 date만 허용합니다. "
        "파일 목록은 Glob, 내용은 Read, 검색은 Grep을 쓰십시오.")


def check_director_write(data: dict, paths: list[Path]) -> str | None:
    if not paths:
        return "director의 Bash 쓰기는 허용하지 않습니다. Write·Edit로 기록하십시오."
    for path in paths:
        r = rel(path)
        if not r.startswith(DIRECTOR) or r.startswith("log/state/.active"):
            return f"director 쓰기 범위는 상태·입력·조사 문서입니다: {r or path}"
    user, _ = last_user(data.get("transcript_path"))
    for path in paths:
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
    if role == "assistant" and not data.get("agent_id"):
        user, _ = last_user(data.get("transcript_path"))
        if not approved(user):
            return "사용자 마지막 메시지에 승인이 없습니다. 변경 diff를 먼저 제시하고 승인을 받으십시오."
    return None
