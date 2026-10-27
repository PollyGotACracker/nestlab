"""실행 승인 전 명세 문서 계획을 검사한다.
판정은 hooks/common/sub_workflow.py의 plan_gaps에 위임한다.

호출자: 
- hooks/check_tool_use/sub_scope.py(check_plan)
입력: 경과 문서 새 내용과 이전 내용, 같은 작업의 명세 문서 본문
반환: 
- check_plan은 deny 사유 문자열 또는 통과 시 None
- plan_gaps는 미충족 칸 설명 목록(통과는 빈 목록)
한계: 칸의 존재와 형식만 검사하고 한계 내용의 완전성과 사실성은 판정하지 않는다.
"""
from __future__ import annotations

import re

from sub_approval import APPROVED
from sub_docs import legacy, section
from sub_workflow import case_gaps

# 코드, 인라인 코드, HTML 주석 안의 꺾쇠는 자리표시로 보지 않는다.
FENCE = r"^[ \t]*(`{3,})[^\n]*\n.*?\n[ \t]*\1"
CODE = re.compile(rf"{FENCE}|`[^`\n]*`|<!--.*?-->", re.S | re.M)
FENCED = re.compile(rf"{FENCE}|<!--.*?-->", re.S | re.M)
PLACEHOLDER = re.compile(r"<[^<>\n]+>")
DOC_TARGET = re.compile(r"^\s*- 문서 갱신 대상:(.*)$", re.M)
LINE_REF = re.compile(r"\S:\d+")
COMMAND = re.compile(r"^\s*- `[^`<>\n]+`", re.M)
REQUIREMENT = re.compile(r"^#{1,6} (\d+\. .+?)\s*$", re.M)


# title 절의 `승인 상태: 승인` 줄이 이번 쓰기로 새로 생기면 True이다.
def newly_approved(new: str | None, old: str, title: str) -> bool:
    has = lambda text: any(APPROVED.match(l) for l in section(text, title).splitlines())
    return new is not None and has(new) and not has(old)


# 요구사항 목록의 (`<번호>. <이름>`, 본문)이다. 제목 문자열로 찾고 `#` 개수는 보지 않는다.
def requirements(text: str) -> list[tuple[str, str]]:
    body = section(text, "요구사항 목록")
    found = list(REQUIREMENT.finditer(body))
    return [(m.group(1), body[m.end():(found[i + 1].start() if i + 1 < len(found) else len(body))])
            for i, m in enumerate(found)]


GAP_NO_REQ = "요구사항 목록에 요구사항이 없습니다."
GAP_REVIEW = "요구사항 `{name}`에 검증과 기대 결과가 없습니다."
GAP_COMMAND = "검증 계획에 검증 명령이 없습니다."
GAP_PLACEHOLDER = "`{title}`에 채우지 않은 칸이 있습니다. 해당 칸은 {hits}입니다."
GAP_DOC_TARGET = "문서 갱신 대상 `{target}`에 `파일:행` 또는 검색어 없음 기록이 없습니다."

# 실행 승인 전에 채워야 할 명세 문서 칸 목록이다. 비어 있으면 통과이다.
def plan_gaps(text: str) -> list[str]:
    # 빠진 항목 목록을 시작한다.
    gaps = []

    # 요구사항마다 검증과 기대 결과, 한계, Test Case 표를 확인한다.
    reqs = requirements(text)
    if not reqs:
        gaps.append(GAP_NO_REQ)
    for name, req in reqs:
        if ("검증:" not in req and legacy("검증:") not in req) or "기대 결과:" not in req:
            gaps.append(GAP_REVIEW.format(name=name))
        gaps.extend(case_gaps(name, req))

    # 검증 계획의 검증 명령과 자리표시자를 확인한다.
    if not COMMAND.search(section(text, "검증 계획")):
        gaps.append(GAP_COMMAND)
    for title in ("요구사항 목록", "작업 계획"):
        hits = sorted(set(PLACEHOLDER.findall(CODE.sub("", section(text, title)))))
        if hits:
            gaps.append(GAP_PLACEHOLDER.format(title=title, hits=", ".join(hits[:3])))

    # 문서 갱신 대상마다 `파일:행` 또는 검색어 없음 기록을 확인한다.
    plan = FENCED.sub("", section(text, "작업 계획"))
    found = DOC_TARGET.findall(plan) + [l for l in section(plan, "문서 갱신 대상").splitlines() if l.strip()]
    for target in found:
        if not LINE_REF.search(target) and not ("검색어와 일치 없음" in target and "검색어:" in target):
            gaps.append(GAP_DOC_TARGET.format(target=target.strip()[:40]))
    return gaps


GAP_NO_INPUT = "명세 문서가 없습니다."
REASON_PLAN = "실행 승인 전에 명세 문서 계획을 채우십시오. {gaps}"

# 경과 문서 쓰기 검사이다. plan은 같은 작업의 명세 문서 본문이다.
def check_plan(new: str | None, old: str, plan: str) -> str | None:
    if newly_approved(new, old, "실행 승인 범위"):
        from sub_workflow import plan_gaps as workflow_gaps
        gaps = workflow_gaps(plan) if plan else [GAP_NO_INPUT]
        if gaps:
            return REASON_PLAN.format(gaps=" ".join(gaps[:5]))
    return None
