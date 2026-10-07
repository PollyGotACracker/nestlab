"""조사 문서 원문 보존과 조사 결과 없는 확정 결정 차단이다.

- 원문 보존: 조사 문서 `최종 조사 원문`과 `<n>회차 반증 요청`·`<n>회차 반증 원문` 블록 변경을 deny한다.
  새 조사 문서는 원문 칸이 비었거나 자리표시·`기록 없음`일 때만 허용한다.
- 조사 확인: 외부 조사가 필요한 작업에서 조사 문서 `최종 조사 원문`이 비어 있으면
  `조사 문서 결정`을 출처로 하는 `확정 결정` 추가를 deny한다.
"""
from __future__ import annotations

import re
from pathlib import Path

from sub_docs import section
from sub_write import read

RAW = ("최종 조사 원문", "최종 반증 원문")
ROUND = re.compile(r"^### (\d+회차 반증 (?:요청|원문))\s*$", re.M)
RAW_EMPTY = re.compile(r"^(`{3,})[^\n]*\n\s*(?:기록 없음|<[^<>\n]*>)?\s*\n\1$")


def rounds(text: str) -> list[tuple[str, str]]:
    """회차 반증 블록의 (제목, 본문) 목록이다."""
    return [(t, section(text, t)) for t in ROUND.findall(text)]


def raw_empty(text: str, title: str) -> bool:
    """원문 칸이 비었거나 자리표시·`기록 없음`이면 True이다."""
    body = section(text, title).strip()
    return not body or bool(RAW_EMPTY.match(body))


def research_needed(research: str) -> bool:
    return "외부 조사: 필요" in section(research, "조사 계획")


def check_raw(new: str | None, old: str) -> str | None:
    """조사 문서 쓰기에서 하네스 전용 칸 변경을 막는다."""
    changed = [t for t in RAW if new is not None and section(new, t) != section(old, t)]
    if changed and not (not old and all(raw_empty(new, t) for t in changed)):
        return "조사 문서의 최종 원문 칸은 하네스만 씁니다."
    if new is not None and rounds(new) != rounds(old):
        return "조사 문서의 회차 반증 블록은 하네스만 씁니다."
    return None


def check_decision(path: Path, new: str | None, old: str) -> str | None:
    """입력 문서 쓰기에서 조사 결과 없이 조사 문서 결정을 확정하는 것을 막는다."""
    added = [l for l in section(new or "", "확정 결정").splitlines()
        if l.strip() and l not in section(old, "확정 결정").splitlines()]
    if new is not None and any("조사 문서 결정" in l for l in added):
        research = read(path.with_name(path.name.replace("-input.md", "-research.md")))
        if research and research_needed(research) and raw_empty(research, "최종 조사 원문"):
            return "조사 문서 최종 조사 원문이 비어 있어 확정 결정을 기록할 수 없습니다. 조사가 필요합니다."
    return None
