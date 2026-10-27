#!/usr/bin/env python3
"""작업 문서(경과, 명세, 조사, 반증 문서)의 단계 선행 조건을 판정하는 공용 모듈이다.

호출자:
- hooks/check_tool_use.py, hooks/check_tool_use/sub_plan.py
- hooks/check_tool_use/sub_judgment.py
- hooks/save_handback_report.py, hooks/save_subagent_result.py, tools/run_workflow.py
입력: 작업 문서 본문 또는 저장소 루트, 작업 ID, 단계 이름
반환:
- 검사 함수(validate, check_plan, check_research, check_complete 등)는 통과하면 None을 반환하고
  미달이면 사유 문자열을 담은 ValueError를 던진다.
- transition_reason, call_reason은 차단 사유 문자열 또는 통과 시 None을 반환한다.
- plan_gaps는 사유 문자열 목록을 반환한다(통과는 빈 목록).
한계: 문서의 구조와 칸 형식만 판정하고 자연어 내용의 정확성은 판정하지 않는다.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from sub_docs import legacy, review_done, section, sibling, task_doc, titles


STAGES = ("kickoff", "research", "refute", "implement", "document", "rework", "complete")
# 서브에이전트 호출 검사에만 쓰는 단계이다. 단계 실행기는 실행하지 않는다.
CALL_STAGES = ("verify", "reverify")
TASK_ID = re.compile(r"\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*")
EMPTY = {"", "기록 없음", "없음", "미확인", "승인 대기"}
CASE_ROW = re.compile(r"^\|\s*(차단|허용)\s*\|([^|\n]*)\|([^|\n]*)\|([^|\n]*)\|\s*$", re.M)


# 조건이 거짓이면 사유를 담은 ValueError를 던진다.
def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


# 값이 비어 있지 않고 자리표시자가 남지 않았으면 True를 반환한다.
def filled(value: str) -> bool:
    # 코드 표기와 HTML 주석, 경로 자리표시자를 뺀 본문을 만든다.
    prose = re.sub(r"`[^`\n]+`|<!--.*?-->", "", value, flags=re.S)
    prose = re.sub(r"(?<=[/\\])<[^<>\n]+>(?=[/\\])", "경로", prose)

    # 빈 값과 남은 자리표시자를 판정한다.
    return value.strip().rstrip(".") not in EMPTY and not re.search(r"<[^<>\n]+>", prose)


R_UNCLOSED = "닫히지 않은 코드 블록 또는 HTML 주석이 있습니다."
R_DUP_TITLE = "제목이 중복돼 문서 절을 확정할 수 없습니다."
R_NO_SECTION = "필수 절 `{title}`을 문서에서 찾지 못했습니다."
R_RAW_BLOCK = "`{title}` 절에는 원문 코드 블록이 1개 있어야 합니다."
R_RAW_OUTSIDE = "`{title}` 절에 원문 코드 블록 밖의 내용이 있습니다."

class Document:
    # 구조 검사에서 fenced code와 HTML 주석을 제외하고 원문은 별도로 보존한다.

    # 줄과 코드 블록, 제목을 분류해 보관하고 닫히지 않은 구간과 중복 제목은 ValueError로 막는다.
    def __init__(self, text: str):
        # 문서 줄을 읽어 코드 블록과 주석 구간 상태를 초기화한다.
        self.raw = text.splitlines()
        self.lines = []
        self.blocks = []
        fence = None
        start = 0
        comment = False

        # 줄마다 코드 블록과 HTML 주석 안 내용을 빼고 구조 검사용 줄을 만든다.
        for index, raw in enumerate(self.raw):
            if fence:
                if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", raw):
                    self.blocks.append((start, index, "\n".join(self.raw[start + 1:index])))
                    fence = None
                self.lines.append("")
                continue
            line = ""
            rest = raw
            while rest:
                if comment:
                    _, end, rest = rest.partition("-->")
                    if not end:
                        break
                    comment = False
                else:
                    before, begin, rest = rest.partition("<!--")
                    line += before
                    if not begin:
                        break
                    comment = True
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if opening:
                fence, start = opening.group(1), index
                line = ""
            # 양식의 들여쓰기는 2칸이다. Markdown의 들여쓴 코드는 제외한다.
            self.lines.append("" if line.startswith(("    ", "\t")) else line)
        require(not fence and not comment, R_UNCLOSED)

        # 제목 위치와 수준을 모은다.
        self.headings = []
        for index, line in enumerate(self.lines):
            match = re.match(r"^(#{1,6}) (.+?)\s*$", line)
            if match:
                self.headings.append((index, len(match[1]), match[2]))

        # 제목 문자열이 중복되면 절을 확정할 수 없으므로 막는다.
        titles = [title for _, _, title in self.headings]
        require(len(titles) == len(set(titles)), R_DUP_TITLE)

    # 제목 문자열로 절의 줄 범위(시작, 끝)를 찾는다. 수준(`#` 개수)은 보지 않는다.
    def bounds(self, title: str) -> tuple[int, int]:
        # 제목이 일치하는 절을 찾는다.
        for index, level, name in self.headings:
            if name in (title, legacy(title)):
                # 하위 항목 제목은 절 안에 포함하는 절의 패턴이다.
                children = {
                    "요구사항 목록": r"\d+\. .+",
                    "작업 계획": r"변경 범위|검증 계획|" + legacy("검증 계획") + r"|잠재적 위험|진행 순서|작업 기준 자료|배정 범위|조회 대상|변경 대상|생성 대상|작업 유지 대상|작업 금지 대상|문서 갱신 대상",
                    "조사 계획": r"검토 \d+\. .+",
                }
                pattern = children.get(title)

                # 더 깊은 하위 제목은 절 안에 두고, 같거나 높은 수준 중 하위 패턴이 아닌 다음 제목 앞까지를 절 끝으로 삼는다.
                end = next((i for i, depth, following in self.headings if i > index and depth <= level
                            and (not pattern or not re.fullmatch(pattern, following))), len(self.lines))
                return index + 1, end
        raise ValueError(R_NO_SECTION.format(title=title))

    # 절 본문에서 코드 블록과 주석을 뺀 텍스트를 반환한다.
    def section(self, title: str) -> str:
        start, end = self.bounds(title)
        return "\n".join(self.lines[start:end])

    # 절 안의 원문 코드 블록 하나를 반환한다. 블록 밖 내용이나 빈 원문은 막는다.
    def raw_block(self, title: str) -> str:
        # 절의 시작과 끝 줄을 구한다.
        start, end = self.bounds(title)

        # 절 안의 코드 블록이 하나이고 비어 있지 않은지 확인한다.
        blocks = [body for first, last, body in self.blocks if start <= first < last < end]
        require(len(blocks) == 1 and filled(blocks[0]), R_RAW_BLOCK.format(title=title))

        # 코드 블록 밖에 다른 내용이 없는지 확인한다.
        require(not self.section(title).strip(), R_RAW_OUTSIDE.format(title=title))
        return blocks[0]

    # 첫 제목 앞의 머리말(작업 ID, 상태 칸) 텍스트를 반환한다.
    def header(self) -> str:
        end = self.headings[1][0] if len(self.headings) > 1 else len(self.lines)
        return "\n".join(self.lines[:end])


R_FIELD = "`{name}` 칸의 값이 없거나 중복됐습니다."

# `이름: 값` 칸의 값을 반환한다. 칸이 없거나 둘 이상이면 막는다.
def field(body: str, name: str) -> str:
    values = re.findall(r"^\s*(?:- )?" + re.escape(name) + r":\s*(.*?)\s*$", body, re.M)
    require(len(values) == 1 and bool(values[0]), R_FIELD.format(name=name))
    return values[0]


# 한 줄 값과 칸 아래 들여쓴 목록 값을 모두 읽고 반복 칸도 보존한다. 값 안의 콜론은 칸 경계로 보지 않는다.
def values(body: str, name: str) -> list[str]:
    found = []
    lines = body.splitlines()
    names = "|".join(re.escape(n) for n in dict.fromkeys((name, legacy(name))))
    for index, line in enumerate(lines):
        # 칸 이름 줄이면 들여쓰기 깊이와 한 줄 값을 읽는다.
        match = re.match(r"^(\s*)(?:- )?(?:" + names + r"):[ \t]*(.*?)\s*$", line)
        if not match:
            continue
        parts = [match[2]]

        # 칸보다 깊게 들여쓴 줄을 값으로 모은다.
        for following in lines[index + 1:]:
            if following.strip() and len(following) - len(following.lstrip()) <= len(match[1]):
                break
            parts.append(following.strip().removeprefix("- ").strip())
        found.append(" ".join(p for p in parts if p))
    return found


R_TIME_FORMAT = "`{name}` 칸의 값이 `YYYY-MM-DD HH:MM:SS` 형식이 아닙니다. 현재 값은 `{value}`입니다."

# 시각 문자열이 `YYYY-MM-DD HH:MM:SS` 형식이고 실제 존재하는 시각인지 확인한다.
def timestamp(name: str, value: str) -> None:
    require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value)), R_TIME_FORMAT.format(name=name, value=value))
    datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


R_NOT_APPROVED = "`{title}`의 승인 상태가 승인이 아닙니다."
R_APPROVAL_FIELD = "`{title}`의 `{name}` 칸이 비어 있습니다."

# 상태 문서의 승인 절이 승인 상태와 시각, 필수 칸을 갖췄는지 확인한다.
def approval(state: Document, title: str) -> None:
    # 절 본문을 꺼낸다.
    body = state.section(title)

    # 승인 상태와 시각을 확인한다.
    require(field(body, "사용자 승인 상태") == "승인", R_NOT_APPROVED.format(title=title))
    timestamp("사용자 승인 시각", field(body, "사용자 승인 시각"))

    # 연결 대상, 승인 행동, 영향 범위, 범위 밖 칸을 확인한다.
    for name in ("연결 대상", "승인 행동", "영향 범위"):
        require(filled(field(body, name)), R_APPROVAL_FIELD.format(title=title, name=name))
    field(body, "범위 밖")


R_NO_REQ = "요구사항 목록에 요구사항이 없습니다."
R_REQ_NAME = "요구사항 제목이 `<번호>. <이름>` 형식이 아닙니다."

# 입력 문서의 요구사항 (제목, 본문) 목록을 반환한다.
def requirements(plan: Document) -> list[tuple[str, str]]:
    # 요구사항 목록 절 안의 `<번호>. <이름>` 제목을 모은다.
    start, end = plan.bounds("요구사항 목록")
    items = [(name, plan.section(name)) for i, level, name in plan.headings
             if start <= i < end and re.fullmatch(r"\d+\. .+", name)]

    # 요구사항이 있는지, 제목 형식이 맞는지 확인한다.
    require(bool(items), R_NO_REQ)
    require(all(re.fullmatch(r"\d+\. .+", name) for name, _ in items), R_REQ_NAME)
    return items


REQ_REF = re.compile(r"\(요구사항 (\d+(?:, ?\d+)*)\)")
R_REQ_REF = "`{title}`의 항목 `{item}`에 요구사항 목록에 있는 번호의 `(요구사항 <번호>)` 표기가 없습니다."


# 항목 한 줄에 `(요구사항 <번호>)` 표기가 있고 모든 번호가 numbers에 있으면 True이다.
def has_ref(item: str, numbers: set[str]) -> bool:
    refs = [n for group in REQ_REF.findall(item) for n in re.split(r", ?", group)]
    return bool(refs) and all(n in numbers for n in refs)


# 새 양식(변경 범위 절이 없는 명세 문서)이면 True이다. 기존 log/state 문서의 변경 범위 양식은 번호 표기를 요구하지 않는다.
def numbered(plan: Document) -> bool:
    return "변경 범위" not in titles("\n".join(plan.raw))


# 새 양식 명세 문서의 요구사항 번호 집합을 반환한다. 문서가 없거나 기존 양식이거나 요구사항을 읽지 못하면 None이다.
def requirement_numbers(plan: Document | None) -> set[str] | None:
    if plan is None or not numbered(plan):
        return None
    try:
        return {name.split(".")[0] for name, _ in requirements(plan)}
    except ValueError:
        return None


# 경과 문서 `요구사항 목록` 절의 `<번호>. ` 줄에서 번호 집합을 반환한다. 절이 없거나 번호가 없으면 None이다.
def state_numbers(state: Document | None) -> set[str] | None:
    if state is None:
        return None
    try:
        body = state.section("요구사항 목록")
    except ValueError:
        return None
    return set(re.findall(r"^(\d+)\. ", body, re.M)) or None


R_NUM_MISMATCH = "명세 문서 요구사항 번호가 경과 문서 요구사항 목록 번호와 다릅니다."


# 경과 문서에 요구사항 목록이 있고 새 양식 명세 문서이면 두 번호 집합이 같아야 한다. 어느 쪽이든 없으면 건너뛴다.
def check_numbers(state: Document, plan: Document | None) -> None:
    listed, written = state_numbers(state), requirement_numbers(plan)
    if listed is not None and written is not None:
        require(listed == written, R_NUM_MISMATCH)


# 절의 최상위 목록 항목마다 요구사항 번호 표기를 요구한다. `없음`과 `기록 없음` 항목은 제외한다.
def check_refs(body: str, numbers: set[str], title: str) -> None:
    for line in body.splitlines():
        item = line[2:].strip() if line.startswith("- ") else ""
        if item and item.rstrip(".") not in ("없음", "기록 없음"):
            require(has_ref(item, numbers), R_REQ_REF.format(title=title, item=item[:40]))


R_ITEM_FIELD = "`{name}`의 `{key}` 칸이 비어 있습니다."
R_REVIEW_TYPE = "`{name}`의 검증에 (정적) 또는 (실제 동작) 유형이 없습니다."
R_PLACEHOLDER = "`{title}`에 채우지 않은 자리표시자가 있습니다."
R_SECTION_EMPTY = "`{title}` 절이 비어 있습니다."
R_SCOPE_KEY = "변경 범위에 `{key}` 항목이 없습니다."
R_DOC_TARGET = "문서 갱신 대상에 `파일:행` 또는 검색어와 일치 없음 기록이 없습니다."
R_CHECK_PLACE = "검증 계획에 실행 위치가 없습니다."
R_CHECK_ACTUAL = "검증 계획의 실제 검증에 확인 수단 출처가 없습니다."
R_CHECK_CMDS = "검증 계획에 검증 명령 목록이 없습니다."

# 명세 문서가 실행 승인 전 필수 칸을 갖췄는지 확인하고 요구사항 이름 목록을 반환한다.
def check_plan(plan: Document) -> list[str]:
    # 요구사항마다 필수 칸, 검증 유형, 한계와 Test Case 표를 확인한다.
    items = requirements(plan)
    for name, body in items:
        for key in ("목표", "결과", "작업", "검증", "기대 결과"):
            entries = values(body, key)
            require(bool(entries) and all(filled(v) for v in entries), R_ITEM_FIELD.format(name=name, key=key))
        require(all(re.search(r"\((?:정적|실제 동작)\)", v) for v in values(body, "검증")), R_REVIEW_TYPE.format(name=name))
        require(not case_gaps(name, body), "; ".join(case_gaps(name, body)))

    # 필수 절에 자리표시자나 빈 내용이 없는지 확인한다.
    for title in ("요구사항 목록", "작업 계획"):
        require(filled(plan.section(title)), R_PLACEHOLDER.format(title=title))
    for title in ("검증 계획", "잠재적 위험", "진행 순서", "작업 기준 자료"):
        require(bool(plan.section(title).strip()), R_SECTION_EMPTY.format(title=title))

    # 변경 범위 양식이면 범위 항목을, 아니면 대상별 절을 확인하고 문서 갱신 대상을 구한다.
    names = titles("\n".join(plan.raw))
    if "변경 범위" in names:
        require(bool(plan.section("작업 금지 목록").strip()), R_SECTION_EMPTY.format(title="작업 금지 목록"))
        scope = plan.section("변경 범위")
        for key in ("읽기 범위", "변경 대상", "유지 대상", "배정 범위"):
            require(bool(re.search(r"^- " + key + r"\s*\n  - \S", scope, re.M)), R_SCOPE_KEY.format(key=key))
        for key in ("문구 diff", "변경 전 원본", "문서 갱신 대상"):
            field(scope, key)
        target = field(scope, "문서 갱신 대상")
    else:
        for key in ("배정 범위", "조회 대상", "변경 대상", "생성 대상", "작업 유지 대상", "작업 금지 대상"):
            require(bool(plan.section(key).strip()), R_SECTION_EMPTY.format(title=key))
        target = plan.section("문서 갱신 대상")

        # 항목마다 요구사항 목록에 있는 `(요구사항 <번호>)` 표기가 있는지 확인한다.
        numbers = {name.split(".")[0] for name, _ in items}
        for key in ("배정 범위", "변경 대상", "생성 대상", "문서 갱신 대상", "재작업 요청", "재검증 요청"):
            if key in names:
                check_refs(plan.section(key), numbers, key)
    require(bool(re.search(r"\S:\d+", target)) or ("검색어와 일치 없음" in target and "검색어:" in target),
            R_DOC_TARGET)

    # 검증 계획의 실행 위치, 실제 검증, 검증 명령을 확인한다.
    checks = plan.section("검증 계획")
    require(filled(field(checks, "실행 위치")), R_CHECK_PLACE)
    actual = field(checks, "실제 검증")
    require(filled(actual) and "출처:" in actual, R_CHECK_ACTUAL)
    require(bool(re.search(r"^- 검증 명령\s*\n(?:  - `[^`<>\n]+`[^\n]*\n?)+", checks, re.M)),
            R_CHECK_CMDS)
    return [name.split(".")[0] if "변경 범위" not in names else name.split(". ", 1)[1] for name, _ in items]


R_GAP_LIMIT = "요구사항 `{name}`에 한계, 출처와 확인 방법이 없습니다."
R_GAP_KINDS = "요구사항 `{name}`의 Test Case 표에 차단 행과 허용 행이 1행 이상 있어야 합니다."
R_GAP_FIELDS = "요구사항 `{name}`의 Test Case 행마다 입력, 명령과 기대 결과가 있어야 합니다."

# 요구사항 하나의 한계 칸과 Test Case 표 누락 사유 목록을 반환한다.
def case_gaps(name: str, body: str) -> list[str]:
    # 빠진 항목 목록을 시작한다.
    gaps = []

    # 한계에 출처와 확인 방법이 있는지 확인한다.
    limits = values(body, "한계")
    if not limits or not all(filled(v) and "출처:" in v and "확인 방법:" in v for v in limits):
        gaps.append(R_GAP_LIMIT.format(name=name))

    # Test Case 표에 차단 행과 허용 행이 있는지 확인한다.
    rows = CASE_ROW.findall(body)
    kinds = {kind for kind, *_ in rows}
    if kinds != {"차단", "허용"}:
        gaps.append(R_GAP_KINDS.format(name=name))

    # 사례 행마다 입력, 명령, 기대 결과가 채워졌는지 확인한다.
    if any(not all(filled(v.strip().strip('`')) for v in row[1:]) for row in rows):
        gaps.append(R_GAP_FIELDS.format(name=name))
    return gaps


# 입력 문서 본문이 계획 검사에 미달하면 사유 목록을, 통과하면 빈 목록을 반환한다.
def plan_gaps(text: str) -> list[str]:
    try:
        check_plan(Document(text))
    except ValueError as error:
        return [str(error)]
    return []


R_ITEM_FORMAT = "조사 계획의 검토 항목 이름이 `검토 <번호>. <이름>` 형식이 아닙니다."
R_NO_EXCLUDE = "조사 계획에 제외 범위가 없습니다."
R_EXCLUDE_SOURCE = "조사 계획의 제외 조건에 URL 또는 `파일:행` 형식의 출처가 없습니다."
SOURCE_FORMAT = re.compile(r"https?://\S+|[^\s:]*[./\\][^\s:]*:\d+")
SOURCE_VALUE = re.compile(r"출처:\s*([^)\n]*)")
R_SOURCE_FORMAT = "조사 문서의 출처가 URL 또는 `파일:행` 형식이 아닙니다. 출처 `{value}`를 고치십시오."


# 줄의 `출처:` 값 중 URL 또는 `파일:행` 형식이 아닌 첫 값을 반환한다. 모두 형식에 맞으면 None이다.
def bad_source(line: str) -> str | None:
    return next((v.strip() for v in SOURCE_VALUE.findall(line) if not SOURCE_FORMAT.search(v)), None)


# 조사 문서의 조사 계획을 검사하고 검토 항목 이름 목록을 반환한다.
def research_items(research: Document, plan: Document | None = None, state: Document | None = None) -> list[str]:
    # 검토 항목 제목의 형식을 확인한다.
    body = research.section("조사 계획")
    start, end = research.bounds("조사 계획")
    items = [name for i, level, name in research.headings if start <= i < end and name.startswith("검토 ")]
    require(all(re.fullmatch(r"검토 \d+\. .+", name) for name in items), R_ITEM_FORMAT)

    # 새 양식 명세 문서가 주어지면 항목마다 요구사항 목록에 있는 번호 표기를 요구한다. 명세 문서가 없으면 경과 문서 요구사항 목록 번호를 쓴다.
    numbers = requirement_numbers(plan) if plan is not None else state_numbers(state)
    if numbers is not None:
        for name in items:
            require(has_ref(name, numbers), R_REQ_REF.format(title="조사 계획", item=name[:40]))

    # 제외 범위마다 출처가 있는지 확인한다.
    excluded = values(body, "제외 범위")
    require(bool(excluded), R_NO_EXCLUDE)
    for exclusion in excluded:
        require(exclusion.rstrip(".") == "없음" or (filled(exclusion) and bool(SOURCE_VALUE.search(exclusion))
                                                    and bad_source(exclusion) is None), R_EXCLUDE_SOURCE)
    return items


R_RESULT_EMPTY = "조사 원문이 비어 있습니다."
R_RESULT_ITEM = "조사 원문에 `{item}` 항목이 없습니다."
R_RESULT_LABEL = "조사 원문의 `{item}`에 `{label}` 항목이 없습니다."
R_RESULT_FIT = "조사 원문의 `{item}`에 후보 충족 여부가 없습니다."

# 조사 원문이 검토 항목마다 제안, 후보, 출처, 충족 여부를 담았는지 확인한다.
def check_research_result(message: str, items: list[str]) -> None:
    # 결과가 비었는지 확인하고 확인할 항목이 없으면 끝낸다.
    require(filled(message), R_RESULT_EMPTY)
    if not items:
        return

    # 항목마다 마지막 회차 구간에서 필수 표시를 찾는다.
    for item in items:
        segments = [part for part in message.split("\n\n---\n\n") if item in part]
        require(bool(segments), R_RESULT_ITEM.format(item=item))
        latest = segments[-1]
        for label in ("제안", "후보", "출처"):
            require(label in latest, R_RESULT_LABEL.format(item=item, label=label))
        require("충족" in latest or "결론 불가" in latest, R_RESULT_FIT.format(item=item))


R_NO_VERDICT = "반증 판정과 출처가 없습니다."
R_VERDICT_SOURCE = "반증 판정의 출처가 URL 또는 `파일:행` 형식이 아닙니다."

# 반증 원문에 판정 줄이 있고 출처가 URL 또는 `파일:행`인지 확인한다.
def check_refutation(message: str) -> None:
    # 판정 상수를 불러온다.
    from sub_call import VERDICT

    # 판정 줄을 모은다.
    verdicts = [VERDICT.match(line) for line in message.splitlines()]
    verdicts = [m for m in verdicts if m]
    require(bool(verdicts), R_NO_VERDICT)

    # 미확인이 아닌 판정의 출처 형식을 확인한다.
    require(all(m[2] == "미확인" or SOURCE_FORMAT.search(m[3]) for m in verdicts),
            R_VERDICT_SOURCE)


R_VERDICT_SOURCE_LOOP = "반증 판정의 출처가 URL 또는 `파일:행` 형식이 아닙니다."
R_NO_ROUNDS = "반증 문서에 반증 회차 기록이 없습니다."
R_ROUND_ORDER = "반증 회차는 1회차와 2회차 순서로만 기록할 수 있습니다."
R_STOP_MISMATCH = "반증 건수와 정지 여부가 일치하지 않습니다."
R_AFTER_STOP = "정지한 뒤에 반증 회차가 더 기록돼 있습니다."
R_COUNT_MISMATCH = "반증 원문의 반증 건수가 기록한 건수와 다릅니다."
R_NOT_FINISHED = "조사와 반증이 아직 정지하지 않았습니다."
R_USER_CONFIRM = "조사 결과를 사용자가 확인하지 않았습니다."
R_REVIEW_ITEM = "상태 문서의 검토 필요에 `{item}` 항목이 없거나 중복돼 있습니다."
R_REVIEW_UNDONE = "`{item}`의 사용자 검토가 끝나지 않았습니다."
R_NO_BASIS = "`{item}`의 대응 근거가 비어 있습니다."
R_UNVERIFIED = "반증 2회에 도달했으므로 검증 미완을 사용자 보고로 기록해야 합니다."

# 조사 원문, 반증 회차, 사용자 확인과 검토 항목 기록이 갖춰졌는지 확인한다. reviewed가 False면 사용자 확인 전 검사만 한다.
def check_research(state: Document, research: Document, items: list[str], reviewed: bool = True,
                   refute: Document | None = None) -> None:
    # 확인할 항목이 없으면 끝낸다.
    if not items:
        return

    # 최종 조사 원문과 반증 회차 표가 있는지 확인한다. 반증 문서가 없으면(기존 log/state 문서) 조사 문서에서 읽는다.
    refute = refute or research
    check_research_result(research.raw_block("최종 조사 원문: 조사 단계"), items)
    rows = re.findall(r"^\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([^|]+)\s*\|\s*$",
                      refute.section("조사 및 반증 루프"), re.M)
    require(bool(rows), R_NO_ROUNDS)

    # 회차마다 순서와 정지 조건, 반증 원문의 판정과 출처를 확인한다.
    for index, (number, count, stop) in enumerate(rows, 1):
        if not reviewed:  # 사용자 선택이 기록된 뒤에는 회차 수와 정지 순서로 차단하지 않는다.
            require(int(number) == index and index <= 2, R_ROUND_ORDER)
            expected = "정지(반증 0건)" if int(count) == 0 else ("정지(2회 도달)" if index == 2 else "계속")
            require(stop.strip() == expected, R_STOP_MISMATCH)
            require(index == len(rows) or expected == "계속", R_AFTER_STOP)
        raw = refute.raw_block(f"{number}회차 반증 원문")
        from sub_call import VERDICT
        verdicts = [(m[2], m[3]) for m in map(VERDICT.match, raw.splitlines()) if m]
        require(bool(verdicts), R_NO_VERDICT)
        require(sum(verdict == "반증" for verdict, _ in verdicts) == int(count), R_COUNT_MISMATCH)
        require(all(verdict == "미확인" or bool(re.search(r"https?://\S+|\S+:\d+", source))
                    for verdict, source in verdicts), R_VERDICT_SOURCE_LOOP)

    # 사용자 확인 전 검사라면 조사와 반증이 정지했는지만 확인하고 끝낸다.
    if not reviewed:
        require(rows[-1][2].strip().startswith("정지("), R_NOT_FINISHED)
        return

    # 조사 문서의 사용자 확인 상태와 시각을 확인한다.
    require(field(research.header(), "확인 상태") == "확인", R_USER_CONFIRM)
    timestamp("확인 시각", field(research.header(), "확인 시각"))

    # 검토 항목마다 상태 문서의 검토 필요 기록이 끝났는지 확인한다.
    review = state.section("검토 필요")
    entries = re.split(r"(?m)^- ", review)[1:]
    for item in items:
        number = item.split(". ", 1)[0]
        name = item.split(". ", 1)[1]
        matches = [entry for entry in entries if number in entry.splitlines()[0] or name in entry.splitlines()[0]]
        require(len(matches) == 1, R_REVIEW_ITEM.format(item=item))
        status = field(matches[0], "상태")
        require(status in review_done(), R_REVIEW_UNDONE.format(item=item))
        if status == "검토 완료":  # 사후 항목은 템플릿대로 시각 `기록 없음`을 허용한다.
            timestamp("사용자 검토 시각", field(matches[0], "사용자 검토 시각"))
            require(filled(field(matches[0], "대응 근거")), R_NO_BASIS.format(item=item))

    # 반증이 남은 채 2회에 도달했으면 검증 미완 보고가 있는지 확인한다.
    if int(rows[-1][1]):
        require("검증 미완" in review, R_UNVERIFIED)


R_REVIEW_NOT_STARTED = "검증을 시작하지 않은 작업은 완료로 쓸 수 없습니다."
R_REVIEW_MISSING = "`{name}`의 검증 결과가 없습니다."
R_REVIEW_NOT_PASS = "`{name}`의 최종 검증 결과가 PASS가 아닙니다."
R_REMAINING = "검증 결과의 남은 문제가 없음으로 기록돼 있지 않습니다."
R_PENDING = "`{title}`에 처리하지 않은 항목이 있습니다."

# 요구사항마다 최종 검증 PASS 기록이 있고 남은 문제와 재작업 요청이 없는지 확인한다.
def check_complete(state: Document, plan: Document, names: list[str]) -> None:
    # 검증을 시작했는지 확인하고 검증 결과 항목을 나눈다.
    review = state.section("검증 결과")
    require("검증 미개시" not in review and legacy("검증 미개시") not in review, R_REVIEW_NOT_STARTED)
    entries = re.split(r"(?m)^- (?=\d{4}-\d{2}-\d{2} )", review)[1:]

    # 새 양식 명세 문서면 검증 항목마다 요구사항 목록에 있는 번호 표기를 요구하고, 요구사항을 번호로 대조한다.
    by_number = numbered(plan)
    if by_number:
        for entry in entries:
            require(has_ref(entry.splitlines()[0], set(names)), R_REQ_REF.format(title="검증 결과", item=entry.splitlines()[0][:40]))

    # 요구사항마다 마지막 검증 항목이 PASS인지 확인한다.
    for name in names:
        label = r".+ \(요구사항 (?:\d+, ?)*" + name + r"(?:, ?\d+)*\)" if by_number else re.escape(name)
        matches = [entry for entry in entries if re.match(
            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}: " + label
            + r" \((정적|실제 동작), (?:검증|" + legacy("검증") + r"|재검증 [12]/2|" + legacy("재검증") + r" [12]/2)\)\s*$",
            entry.splitlines()[0])]
        shown = f"요구사항 {name}" if by_number else name
        require(bool(matches), R_REVIEW_MISSING.format(name=shown))
        result = matches[-1]
        require(bool(re.search(r"^  - PASS: \S", result, re.M)) and
                not re.search(r"^  - (?:FAIL|미측정):", result, re.M), R_REVIEW_NOT_PASS.format(name=shown))

    # 남은 문제가 없고 처리하지 않은 재작업과 재검증 요청이 없는지 확인한다.
    require(bool(entries) and field(entries[-1], "남은 문제") in ("없음", "없음."), R_REMAINING)
    for title in ("재작업 요청", "재검증 요청"):
        # 주석은 section이 이미 빼므로, 하위 절 제목 줄을 뺀 남은 줄이 모두 빈 표기인지만 본다.
        lines = [line.strip().rstrip(".") for line in plan.section(title).splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
        require(all(line in ("없음", "- 없음", "기록 없음", "- 기록 없음") for line in lines),
                R_PENDING.format(title=title))


R_TASK_ID = "작업 ID 형식이 올바르지 않습니다."
R_STAGE = "지원하지 않는 단계 `{stage}`입니다."
R_OUTSIDE = "문서 경로가 저장소 밖을 가리킵니다."
R_ID_MISMATCH = "`{name}`의 작업 ID가 요청한 작업 ID와 다릅니다."
R_UNRESOLVED_STOP = "결정 기록에 미해결 중단 판정이 있어 다음 단계로 진행할 수 없습니다."
R_BAD_STATUS = "작업 상태가 중단이거나 이 단계에서 쓸 수 없는 값입니다."
R_NO_ITEMS = "조사할 검토 항목이 없습니다."
R_EXEC_STATUS = "실행 승인 뒤 작업 상태는 진행이어야 합니다."
R_REVIEW_LEFT = "상태 문서의 검토 필요에 검토가 끝나지 않은 항목이 있습니다."
R_REWORK_TARGET = "`{title}`에 대상과 `{key}` 칸이 있어야 합니다."
R_REWORK_POSITION = "`{title}`에 대상 위치가 없습니다."

R_NO_REFUTE = "반증 문서가 없습니다."
R_NO_SPEC = "명세 문서가 없습니다."
SPECLESS_STAGES = ("kickoff", "research", "refute")

# 작업 문서를 읽어 단계 선행 조건을 확인한다. 미달이면 사유를 담은 ValueError를 던진다.
def validate(root: Path, task: str, stage: str) -> None:
    # 작업 ID와 단계 이름을 확인한다.
    require(bool(TASK_ID.fullmatch(task)), R_TASK_ID)
    require(stage in STAGES + CALL_STAGES, R_STAGE.format(stage=stage))

    # 경과, 명세, 조사 문서를 읽고 작업 ID가 일치하는지 확인한다. 반증 문서는 있을 때만 읽는다.
    documents = []
    for kind in ("state", "input", "research", "refute"):
        path = task_doc(root, task, kind)
        require(path.resolve().is_relative_to(root.resolve()), R_OUTSIDE)
        if kind == "refute" and not path.is_file():
            documents.append(None)
            continue
        if kind == "input" and not path.is_file():
            require(stage in SPECLESS_STAGES, R_NO_SPEC)
            documents.append(None)
            continue
        document = Document(path.read_text(encoding="utf-8-sig"))
        require(field(document.header(), "작업 ID") == task, R_ID_MISMATCH.format(name=path.name))
        documents.append(document)
    state, plan, research, refute = documents

    # 미해결 중단 판정과 작업 상태를 확인한다.
    require("중단 판정 미해결" not in state.section("결정 기록"), R_UNRESOLVED_STOP)
    status = field(state.header(), "작업 상태")
    require(status in ("미개시", "진행") or (stage == "complete" and status == "완료"), R_BAD_STATUS)

    # 착수 단계는 착수 종료 조건만 확인한다.
    if stage == "kickoff":
        check_kickoff(state, plan, research)
        return

    # 착수 승인과 조사 계획을 확인한다. 조사와 반증 단계는 여기서 끝난다.
    approval(state, "착수 승인 범위")
    items = research_items(research, plan, state)
    if stage in ("research", "refute"):
        require(bool(items), R_NO_ITEMS)
        if stage == "refute":
            require(refute is not None or "조사 및 반증 루프" in [t for _, _, t in research.headings], R_NO_REFUTE)
            check_research_result(research.raw_block("최종 조사 원문: 조사 단계"), items)
        return

    # 실행 승인, 입력 문서 계획, 조사 기록, 검토 항목을 확인한다.
    approval(state, "실행 승인 범위")
    require(status in ("진행", "완료"), R_EXEC_STATUS)
    check_numbers(state, plan)
    names = check_plan(plan)
    check_research(state, research, items, refute=refute)
    review = state.section("검토 필요")
    for value in re.findall(r"^\s*- 상태:\s*(.+)$", review, re.M):
        require(value in review_done(), R_REVIEW_LEFT)

    # 재작업과 재검증 단계는 요청 절의 대상과 위치를 확인한다.
    if stage in ("rework", "reverify"):
        title, key = ("재작업 요청", "기대 결과") if stage == "rework" else ("재검증 요청", "필요한 근거")
        body = plan.section(title)
        require(filled(body) and bool(values(body, key)) and all(filled(v) for v in values(body, key)), R_REWORK_TARGET.format(title=title, key=key))
        require(bool(re.search(r"^- .+: .+", body, re.M)), R_REWORK_POSITION.format(title=title))

    # 완료 단계는 최종 검증 기록을 확인한다.
    if stage == "complete":
        check_complete(state, plan, names)


R_KICKOFF_ENV = "조사 문서의 프로젝트 환경에 확인 내용과 출처가 없습니다."

# 착수 종료 조건으로 프로젝트 환경, 요구사항, 조사 계획을 확인한다.
def check_kickoff(state: Document, plan: Document | None, research: Document) -> None:
    environment = research.section("프로젝트 환경")
    require(filled(environment) and "출처:" in environment, R_KICKOFF_ENV)

    # 명세 문서가 없으면 경과 문서 요구사항 목록을, 있으면 명세 문서 요구사항을 요구한다.
    if plan is None:
        require(state_numbers(state) is not None, R_NO_REQ)
    else:
        requirements(plan)
    research_items(research, plan, state)


R_IO = "작업 문서를 읽지 못했습니다({error})."

# 상태 문서 새 내용이 착수 승인, 실행 승인, 완료로 바뀌면 선행 조건을 확인해 사유를 반환한다. 해당하지 않으면 None이다.
def transition_reason(path: Path, new: str | None, old: str, plan: str) -> str | None:
    # 새 문서 내용이 없으면 전환 검사를 건너뛴다.
    if new is None:
        return None

    # 이번 쓰기로 새로 승인되거나 완료되는 전이를 찾는다.
    approved = lambda text, title: re.search(r"^\s*- (?:사용자 )?승인 상태:\s*승인\s*$", section(text, title), re.M)
    kickoff = approved(new, "착수 승인 범위") and not approved(old, "착수 승인 범위")
    execute = approved(new, "실행 승인 범위") and not approved(old, "실행 승인 범위")
    complete = bool(re.search(r"^- 작업 상태: 완료\s*$", new, re.M)) and not re.search(r"^- 작업 상태: 완료\s*$", old, re.M)
    if not (kickoff or execute or complete):
        return None

    # 전이에 맞는 선행 조건을 확인하고 미달 사유를 반환한다.
    try:
        state_doc, plan_doc = Document(new), Document(plan) if plan.strip() else None
        research = Document(sibling(path, "research").read_text(encoding="utf-8-sig"))
        refute = sibling(path, "refute")
        refute_doc = Document(refute.read_text(encoding="utf-8-sig")) if refute.is_file() else None
        if kickoff:
            # 착수 승인은 명세 문서 없이 경과 문서 요구사항만 검사한다.
            check_kickoff(state_doc, None, research)
        if execute or complete:
            require(plan_doc is not None, R_NO_SPEC)
            check_numbers(state_doc, plan_doc)
            require("중단 판정 미해결" not in state_doc.section("결정 기록"), R_UNRESOLVED_STOP)
            names = check_plan(plan_doc)
            check_research(state_doc, research, research_items(research, plan_doc, state_doc), refute=refute_doc)
            pending = re.findall(r"^\s*- 상태:\s*(.+)$", state_doc.section("검토 필요"), re.M)
            require(all(v in review_done() for v in pending), R_REVIEW_LEFT)
            if complete:
                check_complete(state_doc, plan_doc, names)
    except OSError as error:
        return R_IO.format(error=error)
    except ValueError as error:
        return str(error)
    return None


R_KIND = "입력의 작업 종류가 없거나 지원하지 않는 값입니다."

# 서브에이전트 호출이 단계 선행 조건을 채웠는지 확인해 사유를 반환한다. 대상 호출이 아니면 None이다.
def call_reason(data: dict) -> str | None:
    # 호출 판정에 쓰는 모듈을 불러온다.
    from sub_call import call_info, ROLES
    from sub_docs import project_root, task_id

    # 역할 에이전트 호출이고 입력에 작업 ID가 있을 때만 검사한다.
    target, prompt = call_info(data)
    if target not in ROLES:
        return None
    task = task_id(prompt)
    if not task:
        return None

    # 작업 종류를 단계 이름으로 바꿔 선행 조건을 확인한다.
    kind = next((l.removeprefix("작업 종류: ") for l in prompt.splitlines() if l.startswith("작업 종류: ")), "")
    stages = {"조사": "research", "반증": "refute", "구현": "implement", "문서 작성": "document",
              "검증": "verify", "재작업": "rework", "재검증": "reverify"}
    try:
        require(kind in stages, R_KIND)
        validate(project_root(data.get("cwd")), task, stages[kind])
    except OSError as error:
        return R_IO.format(error=error)
    except ValueError as error:
        return str(error)
    return None
