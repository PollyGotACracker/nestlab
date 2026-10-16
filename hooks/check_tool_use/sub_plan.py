"""실행 승인 전 입력 문서 계획 검사이다.

입력 문서 `실행 승인 범위`를 승인으로 바꾸려면 요구사항마다 검수·기대 결과가 있고,
검수 계획에 검증 명령이 있고, 요구사항 목록·작업 계획에 채우지 않은 자리표시가 없어야 한다.
작업 계획의 `문서 갱신 대상` 줄은 `파일:행`을 포함하거나 `검색어와 일치 없음`과 `검색어:`를 함께 포함해야 한다.
"""
from __future__ import annotations

import re

from sub_approval import APPROVED
from sub_docs import section

# 코드·인라인 코드·HTML 주석 안의 꺾쇠는 자리표시로 보지 않는다.
FENCE = r"^[ \t]*(`{3,})[^\n]*\n.*?\n[ \t]*\1"
CODE = re.compile(rf"{FENCE}|`[^`\n]*`|<!--.*?-->", re.S | re.M)
FENCED = re.compile(rf"{FENCE}|<!--.*?-->", re.S | re.M)
PLACEHOLDER = re.compile(r"<[^<>\n]+>")
DOC_TARGET = re.compile(r"^\s*- 문서 갱신 대상:(.*)$", re.M)
LINE_REF = re.compile(r"\S:\d+")
COMMAND = re.compile(r"^\s*- `[^`<>\n]+`", re.M)


def newly_approved(new: str | None, old: str, title: str) -> bool:
    """title 절의 `승인 상태: 승인` 줄이 이번 쓰기로 새로 생기면 True이다."""
    has = lambda text: any(APPROVED.match(l) for l in section(text, title).splitlines())
    return new is not None and has(new) and not has(old)


def plan_gaps(text: str) -> list[str]:
    """실행 승인 전에 채워야 할 입력 문서 칸 목록이다. 비어 있으면 통과이다."""
    gaps = []
    reqs = re.split(r"^#### ", section(text, "요구사항 목록"), flags=re.M)[1:]
    if not reqs:
        gaps.append("요구사항 목록에 요구사항이 없다")
    for req in reqs:
        if "- 검수:" not in req or "기대 결과:" not in req:
            gaps.append(f"요구사항 {req.splitlines()[0].strip()}: 검수·기대 결과 없음")
    if not COMMAND.search(section(text, "검수 계획")):
        gaps.append("검수 계획: 검증 명령 없음")
    for title in ("요구사항 목록", "작업 계획"):
        hits = sorted(set(PLACEHOLDER.findall(CODE.sub("", section(text, title)))))
        if hits:
            gaps.append(f"{title}: 채우지 않은 칸 {', '.join(hits[:3])}")
    for target in DOC_TARGET.findall(FENCED.sub("", section(text, "작업 계획"))):
        if not LINE_REF.search(target) and not ("검색어와 일치 없음" in target and "검색어:" in target):
            gaps.append(f"문서 갱신 대상: 파일:행 또는 검색어 없음 ({target.strip()[:40]})")
    return gaps


def check_plan(new: str | None, old: str) -> str | None:
    if newly_approved(new, old, "실행 승인 범위"):
        gaps = plan_gaps(new)
        if gaps:
            return "실행 승인 전에 입력 문서 계획을 채우십시오: " + "; ".join(gaps[:5])
    return None
