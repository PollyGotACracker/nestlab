"""작업 문서 본문을 절과 칸 단위로 읽고 요구사항 목록과 번호 대응을 판정하는 공용 모듈이다.

호출자: hooks/common/sub_gate.py, hooks/check_tool_use 하위 모듈, hooks/check_workflow_response.py, hooks/save_subagent_result.py, tools/run_workflow.py
입력: 작업 문서 본문 문자열
반환: Document, 칸 값 목록, 요구사항 (이름, 본문) 목록이며 미달이면 사유 문자열을 담은 ValueError를 던진다.
한계: 문서의 구조와 칸 형식만 읽고 자연어 내용은 판정하지 않는다.
"""

from __future__ import annotations

import re
from datetime import datetime

from sub_docs import legacy, titles


# 조건이 거짓이면 사유를 담은 ValueError를 던진다.
def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


# 백틱으로 감싼 자리표시자에 넣을 문서 값에서 백틱을 지운다.
def bare(value) -> str:
    return str(value).replace("`", "")


# 값이 비어 있지 않고 자리표시자가 남지 않았으면 True를 반환한다.
def filled(value: str) -> bool:
    # 코드 표기와 HTML 주석, 경로 자리표시자를 뺀 본문을 만든다.
    prose = re.sub(r"`[^`\n]+`|<!--.*?-->", "", value, flags=re.S)
    prose = re.sub(r"(?<=[/\\])<[^<>\n]+>(?=[/\\])", "경로", prose)

    # 빈 값과 남은 자리표시자를 판정한다.
    return value.strip().rstrip(".") not in EMPTY and not re.search(r"<[^<>\n]+>", prose)

class Document:
    # 구조 검사에서 fenced code와 HTML 주석을 제외하고 원문은 별도로 보존한다.

    # 줄과 코드 블록, 제목을 분류해 보관하고 닫히지 않은 구간과 중복 제목은 ValueError로 차단한다.
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

        # 제목 위치와 수준을 추출한다.
        self.headings = []
        for index, line in enumerate(self.lines):
            match = re.match(r"^(#{1,6}) (.+?)\s*$", line)
            if match:
                self.headings.append((index, len(match[1]), match[2]))

        # 제목 문자열이 중복되면 절을 확정할 수 없으므로 차단한다.
        # 안 블록과 결과 블록의 하위 제목(주장 목록, 주장 판정, 열람 범위, 한계)은 직전 검토 또는 결과 제목 범위에서 비교한다.
        keys, review = [], ""
        for _, _, title in self.headings:
            review = title if re.match(r"검토 \d+\.|조사 \d+\. .+ 결과$|\d+회차 반증 결과$", title) else review
            keys.append((review, title) if re.match(r"안 \d+\.|(?:주장 목록|주장 판정|열람 범위|한계)$", title) else ("", title))
        require(len(keys) == len(set(keys)), R_DUP_TITLE)

    # 제목 문자열로 절의 줄 범위(시작, 끝)를 찾는다. 수준(`#` 개수)은 보지 않는다.
    def bounds(self, title: str) -> tuple[int, int]:
        # 제목이 일치하는 절을 찾는다.
        if title == "조사 승인 범위" and any(name == title for _, _, name in self.headings):
            matches = (title,)
        else:
            matches = (title, legacy(title))
        for index, level, name in self.headings:
            if name in matches:
                # 하위 항목 제목은 절 안에 포함하는 절의 패턴이다.
                children = {
                    "요구사항 목록": r"(?:요구사항 )?\d+\. .+",
                    "작업 계획": r"변경 범위|검증 계획|" + legacy("검증 계획") + r"|잠재적 위험|진행 순서|작업 기준 자료|배정 범위|조회 대상|변경 대상|생성 대상|문서 갱신 대상",
                    "조사 계획": r"조사 \d+\. .+",
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

    # 절 안의 원문 코드 블록 하나를 반환한다. 블록 밖 내용이나 빈 원문은 차단한다.
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

# `이름: 값` 칸의 값을 반환한다. 칸이 없거나 둘 이상이면 차단한다.
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

        # 칸보다 깊게 들여쓴 줄을 값으로 추출한다.
        for following in lines[index + 1:]:
            if following.strip() and len(following) - len(following.lstrip()) <= len(match[1]):
                break
            parts.append(following.strip().removeprefix("- ").strip())
        found.append(" ".join(p for p in parts if p))
    return found

# 시각 문자열이 `YYYY-MM-DD HH:MM:SS` 형식이고 실제 존재하는 시각인지 확인한다.
def timestamp(name: str, value: str) -> None:
    require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value)), R_TIME_FORMAT.format(name=bare(name), value=bare(value)))
    datetime.strptime(value, "%Y-%m-%d %H:%M:%S")

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

# 입력 문서의 요구사항 (제목, 본문) 목록을 반환한다.
def requirements(plan: Document) -> list[tuple[str, str]]:
    # 요구사항 목록 절 안의 `<번호>. <이름>` 제목을 추출한다.
    start, end = plan.bounds("요구사항 목록")
    items = [(name, plan.section(name)) for i, level, name in plan.headings
             if start <= i < end and re.fullmatch(REQ_TITLE, name)]

    # 요구사항이 있는지, 제목 형식이 맞는지 확인한다.
    require(bool(items), R_NO_REQ)
    require(all(re.fullmatch(REQ_TITLE, name) for name, _ in items), R_REQ_NAME)
    return items


# 요구사항 제목에서 번호를 읽는다. 폐기한 절의 제목은 `요구사항 <번호>. 폐기`이다.
def number_of(name: str) -> str:
    return re.search(r"\d+", name)[0]


# 요구사항 절이 폐기됐으면 True이다. 제목이 `요구사항 <번호>. 폐기`이거나 기대 결과 칸이 `폐기`로 시작한다.
def dropped(name: str, body: str) -> bool:
    return name.endswith(". 폐기") or any(v.startswith("폐기") for v in values(body, "기대 결과"))


# 항목 한 줄에 `(요구사항 <번호>)` 표기가 있고 모든 번호가 numbers에 있으면 True이다.
def has_ref(item: str, numbers: set[str]) -> bool:
    refs = [n for group in REQ_REF.findall(item) for n in re.split(r", ?", group)]
    return bool(refs) and all(n in numbers for n in refs)


# 새 양식(변경 범위 절이 없는 명세 문서)이면 True이다. 변경 범위 절이 있는 기존 양식은 번호 표기를 요구하지 않는다.
def numbered(plan: Document) -> bool:
    return "변경 범위" not in titles("\n".join(plan.raw))


# 새 양식 명세 문서의 요구사항 번호 집합을 반환한다. 문서가 없거나 기존 양식이거나 요구사항을 읽지 못하면 None이다.
def requirement_numbers(plan: Document | None) -> set[str] | None:
    if plan is None or not numbered(plan):
        return None
    try:
        return {number_of(name) for name, _ in requirements(plan)}
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
    return set(re.findall(r"^(?:요구사항 )?(\d+)\. ", body, re.M)) or None


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
            require(has_ref(item, numbers), R_REQ_REF.format(title=title, item=bare(item[:40])))
EMPTY = {"", "기록 없음", "없음", "미확인", "승인 대기"}


R_UNCLOSED = "닫히지 않은 코드 블록 또는 HTML 주석이 있습니다."
R_DUP_TITLE = "제목이 중복돼 문서 절을 확정할 수 없습니다."
R_NO_SECTION = "필수 절 `{title}`을 문서에서 찾지 못했습니다."
R_RAW_BLOCK = "`{title}` 절에는 원문 코드 블록이 1개 있어야 합니다."
R_RAW_OUTSIDE = "`{title}` 절에 원문 코드 블록 밖의 내용이 있습니다."


R_FIELD = "`{name}` 칸의 값이 없거나 중복됐습니다."


R_TIME_FORMAT = "`{name}` 칸의 값이 `YYYY-MM-DD HH:MM:SS` 형식이 아닙니다. 현재 값은 `{value}`입니다."


R_NOT_APPROVED = "`{title}`의 승인 상태가 승인이 아닙니다."
R_APPROVAL_FIELD = "`{title}`의 `{name}` 칸이 비어 있습니다."


REQ_TITLE = r"(?:요구사항 )?\d+\. .+"
R_NO_REQ = "요구사항 목록에 요구사항이 없습니다."
R_REQ_NAME = "요구사항 제목이 `<번호>. <이름>` 형식이 아닙니다."


REQ_REF = re.compile(r"\(요구사항 (\d+(?:, ?\d+)*)\)")
R_REQ_REF = "`{title}`의 항목 `{item}`에 요구사항 목록에 있는 번호의 `(요구사항 <번호>)` 표기가 없습니다."


R_NUM_MISMATCH = "명세 문서 요구사항 번호가 경과 문서 요구사항 목록 번호와 다릅니다."
