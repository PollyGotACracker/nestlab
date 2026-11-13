#!/usr/bin/env python3
"""작업 문서(경과, 명세, 조사, 반증 문서)의 단계 선행 조건을 판정하는 공용 모듈이다.

호출자:
- hooks/check_tool_use.py
- hooks/check_tool_use/sub_judgment.py
- hooks/save_handback_report.py, hooks/save_subagent_result.py, tools/run_workflow.py
입력: 작업 문서 본문 또는 저장소 루트, 작업 ID, 단계 이름
반환:
- 검사 함수(validate, check_plan, check_research, check_complete 등)는 통과하면 None을 반환하고
  미달이면 사유 문자열을 담은 ValueError를 던진다.
- transition_reason, call_reason은 차단 사유 문자열 또는 통과 시 None을 반환한다.
한계: 문서의 구조와 칸 형식만 판정하고 자연어 내용의 정확성은 판정하지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from sub_docs import FENCE_LINE, approval_targets, legacy, measure_pending, problem_entries, review_done_numbers, review_entries, review_pending, section, sibling, task_doc, titles
from sub_document import Document, R_REQ_REF, approval, bare, check_numbers, check_refs, dropped, field, filled, has_ref, number_of, numbered, require, requirements, timestamp, values


# 명세 문서가 구현 승인 전 필수 칸을 갖췄는지 확인하고 요구사항 이름 목록을 반환한다.
def check_plan(plan: Document, state: Document | None = None) -> list[str]:
    # 요구사항마다 필수 칸, 검증 유형, 한계와 Test Case 표를 확인한다.
    items = requirements(plan)
    for name, body in items:
        # 폐기한 요구사항 절은 필수 칸을 검사하지 않는다.
        if dropped(name, body):
            continue
        for key in ("목표", "결과", "검증", "기대 결과"):
            entries = values(body, key)
            require(bool(entries) and all(filled(v) for v in entries), R_ITEM_FIELD.format(name=bare(name), key=key))
        require(all(re.search(r"\((?:정적|실제 동작)\)", v) for v in values(body, "검증")), R_REVIEW_TYPE.format(name=bare(name)))
        require(not case_gaps(name, body), "; ".join(case_gaps(name, body)))

    # 단계가 있는 작업은 요구사항마다 확정되고 폐기되지 않은 문제에 연결돼야 하고, 확정 문제마다 연결 요구사항이 1개 이상 있어야 한다.
    state_text = "\n".join(state.raw) if state is not None else ""
    if state is not None and re.search(r"(?m)^- 작업 단계: ", state_text):
        live = {n for n, (confirmed, gone) in problem_entries(state_text).items() if confirmed and not gone}
        linked = set()
        for name, body in items:
            if dropped(name, body):
                continue
            line = re.search(r"(?m)^- 문제:\s*(.+)$", body)
            refs = {int(n) for n in re.findall(r"\d+", line[1])} if line else set()
            require(bool(refs) and refs <= live, R_PROBLEM_LINK.format(name=bare(name)))
            linked |= refs
        missing = sorted(live - linked)
        require(not missing, R_PROBLEM_MISSING.format(numbers=", ".join(map(str, missing))))

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
        for key in ("배정 범위", "조회 대상", "변경 대상", "생성 대상"):
            require(bool(plan.section(key).strip()), R_SECTION_EMPTY.format(title=key))
        target = plan.section("문서 갱신 대상")

        # 항목마다 요구사항 목록에 있는 `(요구사항 <번호>)` 표기가 있는지 확인한다.
        numbers = {number_of(name) for name, _ in items}
        for key in ("배정 범위", "변경 대상", "생성 대상", "문서 갱신 대상", "삭제 목록", "통합 목록", "재구현 요청", "재검증 요청"):
            if key in names:
                check_refs(plan.section(key), numbers, key)

        # 배정 범위는 담당 역할로 시작하고, 삭제와 통합 항목은 근거(`파일:행`)를 가진다.
        roles = [m[1] for m in re.finditer(r"(?m)^- ([^:\n]+):", plan.section("배정 범위"))]
        require(bool(roles) and all(r in ASSIGN_ROLES for r in roles), R_ASSIGN_ROLE.format(roles=", ".join(roles) or "없음"))
        for key in ("삭제 목록", "통합 목록"):
            for line in plan.section(key).splitlines() if key in names else []:
                item = line[2:].strip() if line.startswith("- ") else ""
                require(not item or item.rstrip(".") in ("없음", "기록 없음") or bool(re.search(r"\S:\d+", item)),
                        R_LIST_SOURCE.format(title=key, item=bare(item[:40])))
    require(bool(re.search(r"\S:\d+", target)) or ("검색어와 일치 없음" in target and "검색어:" in target),
            R_DOC_TARGET)

    # 검증 계획의 실행 위치, 실제 검증, 검증 커맨드를 확인한다.
    checks = plan.section("검증 계획")
    require(filled(field(checks, "실행 위치")), R_CHECK_PLACE)
    actual = field(checks, "실제 검증")
    require(filled(actual) and "출처:" in actual, R_CHECK_ACTUAL)
    require(bool(re.search(r"^- 검증 커맨드\s*\n(?:  - `[^`<>\n]+`[^\n]*\n?)+", checks, re.M)),
            R_CHECK_CMDS)
    return [number_of(name) if "변경 범위" not in names else name.split(". ", 1)[1] for name, body in items
            if not dropped(name, body)]

# 요구사항 하나의 한계 칸과 Test Case 표 누락 사유 목록을 반환한다.
def case_gaps(name: str, body: str) -> list[str]:
    # 빠진 항목 목록을 시작한다.
    gaps = []

    # 한계에 출처와 확인 방법이 있는지 확인한다.
    limits = values(body, "한계")
    if not limits or not all(filled(v) and "출처:" in v and "확인 방법:" in v for v in limits):
        gaps.append(R_GAP_LIMIT.format(name=bare(name)))

    # Test Case 표에 차단 행과 허용 행이 있는지 확인한다.
    rows = CASE_ROW.findall(body)
    kinds = {kind for kind, *_ in rows}
    if kinds != {"차단", "허용"}:
        gaps.append(R_GAP_KINDS.format(name=bare(name)))

    # Test Case 행마다 입력, 커맨드, 기대 결과가 채워졌는지 확인한다.
    if any(not all(filled(v.strip().strip('`')) for v in row[1:]) for row in rows):
        gaps.append(R_GAP_FIELDS.format(name=bare(name)))
    return gaps


# 줄의 `출처:` 값 중 URL 또는 `파일:행` 형식이 아닌 첫 값을 반환한다. 모두 형식에 맞으면 None이다.
def bad_source(line: str) -> str | None:
    # 조사 문서 양식의 `<`로 시작하는 자리표시자 값은 제외한다.
    return next((v.strip() for v in SOURCE_VALUE.findall(line)
                 if not v.strip().startswith("<") and not SOURCE_FORMAT.search(v)), None)


# 조사 문서의 조사 계획을 검사하고 조사 항목 이름 목록을 반환한다.
def research_items(research: Document) -> list[str]:
    # 조사 항목 제목의 형식을 확인한다.
    body = research.section("조사 계획")
    start, end = research.bounds("조사 계획")
    items = [name for i, level, name in research.headings if start <= i < end and name.startswith("조사 ")]
    require(all(re.fullmatch(r"조사 \d+\. .+", name) for name in items), R_ITEM_FORMAT)

    # 항목마다 `(문제 <번호>)` 표기를 요구한다. 조사 문서에 `문제 <번호> 조사 필요 판정` 제목이 있으면 그 번호여야 한다.
    judged = {m[1] for _, _, t in research.headings if (m := re.fullmatch(r"문제 (\d+) 조사 필요 판정", t))}
    for name in items:
        refs = [n for group in PROBLEM_REF.findall(name) for n in re.split(r", ?", group)]
        require(bool(refs) and all(n in judged for n in refs if judged), R_PROBLEM_REF.format(item=bare(name[:40])))

    # 제외 범위마다 출처가 있는지 확인한다.
    excluded = values(body, "제외 범위")
    require(bool(excluded), R_NO_EXCLUDE)
    for exclusion in excluded:
        require(exclusion.rstrip(".") == "없음" or (filled(exclusion) and bool(SOURCE_VALUE.search(exclusion))
                                                    and bad_source(exclusion) is None), R_EXCLUDE_SOURCE)
    return items

# 조사 원문이 조사 항목마다 제안, 후보, 출처, 충족 여부를 담았는지 확인한다.
def check_research_result(message: str, items: list[str]) -> None:
    # 결과가 비었는지 확인하고 확인할 항목이 없으면 끝낸다.
    require(filled(message), R_RESULT_EMPTY)
    if not items:
        return

    # 실제 제목으로 구간을 나누며 코드·인용·주장 안의 조사 번호는 항목 경계로 쓰지 않는다.
    rounds = []
    for segment in message.split("\n\n---\n\n"):
        blocks, number, lines, fence = {}, None, [], ""
        for line in segment.splitlines():
            marker = FENCE_LINE.match(line)
            if marker and not fence:
                fence = marker[1]
            elif marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
            heading = re.fullmatch(r"(?:#{1,6} )?조사\s*(\d+)\. .+", line) if not fence and not marker else None
            if heading:
                if number is not None:
                    blocks.setdefault(number, []).append("\n".join(lines))
                number, lines = heading[1], []
            # 예시 안의 후보·출처도 실제 조사 근거로 대신할 수 없다.
            elif number is not None and not fence and not marker and not line.lstrip().startswith(">"):
                lines.append(line)
        if number is not None:
            blocks.setdefault(number, []).append("\n".join(lines))
        rounds.append(blocks)

    # 같은 회차 보완은 결합하고 새 회차가 있으면 그 항목의 마지막 회차만 검사한다.
    for item in items:
        number = re.match(r"조사\s*(\d+)\.", item)[1]
        segments = [blocks[number] for blocks in rounds if number in blocks]
        require(bool(segments), R_RESULT_ITEM.format(item=bare(item)))
        latest = "\n".join(segments[-1])
        for label in ("제안", "후보", "출처"):
            require(label in latest, R_RESULT_LABEL.format(item=bare(item), label=label))
        require("충족" in latest or "결론 불가" in latest, R_RESULT_FIT.format(item=bare(item)))

# 조사 문서에 항목마다 결과를 확인한다. 항목별 결과 절(`조사 <번호>. <이름> 결과`)이 있는 문서는 결과 저장 hook이 칸을 검사해 넣었으므로 절 존재만 보고,
# 기존 양식의 최종 조사 원문 코드 블록은 원문 형식을 검사한다.
def check_result_sections(research: Document, items: list[str]) -> None:
    titles_ = [name for _, _, name in research.headings]
    if "조사 결과" not in titles_:
        check_research_result(research.raw_block("최종 조사 원문: 조사 단계"), items)
        return
    for item in items:
        number = item.split(". ", 1)[0].split()[1]
        require(any(re.fullmatch(rf"조사 {number}\. .+ 결과", name) for name in titles_), R_RESULT_ITEM.format(item=bare(item)))


# 반증 원문에 판정 줄이 있고 출처가 URL 또는 `파일:행`인지 확인한다.
def check_refutation(message: str) -> None:
    # 판정 상수를 불러온다.
    from sub_call import VERDICT

    # 판정 줄을 선별하고, `주장 <번호>:`로 시작하지만 판정 형식이 아닌 줄은 거부한다.
    lines = message.splitlines()
    for line in lines:
        require(VERDICT.match(line) or not CLAIM_START.match(line), R_VERDICT_FORMAT.format(line=bare(line.strip()[:80])))
    verdicts = [VERDICT.match(line) for line in lines]
    verdicts = [m for m in verdicts if m]
    require(bool(verdicts), R_NO_VERDICT)

    # 미확인이 아닌 판정의 출처 형식을 확인한다.
    require(all(m[2] == "미확인" or SOURCE_FORMAT.search(m[3]) for m in verdicts),
            R_VERDICT_SOURCE)


# 조사 항목이 폐기 문제에만 연결됐거나 명세 문서 작업 기준 자료에 `- 조사 <번호>.` 결정 항목이 있으면 True이다.
def review_exempt(state: Document, plan: Document | None, item: str) -> bool:
    number = item.split(". ", 1)[0].split()[1]
    if plan is not None and re.search(r"^- 조사 " + number + r"\. ", section("\n".join(plan.raw), "작업 기준 자료"), re.M):
        return True
    refs = [n for group in PROBLEM_REF.findall(item) for n in re.split(r", ?", group)]
    gone = set(re.findall(r"(?m)^- 문제 (\d+)\. 폐기:", section("\n".join(state.raw), "문제 정의")))
    return bool(refs) and all(n in gone for n in refs)


# 조사 원문, 반증 회차, 사용자 확인과 검토 항목 기록이 갖춰졌는지 확인한다. reviewed가 False면 사용자 확인 전 검사만 한다.
def check_research(state: Document, research: Document, items: list[str], reviewed: bool = True,
                   refute: Document | None = None, plan: Document | None = None) -> None:
    # 확인할 항목이 없으면 끝낸다.
    if not items:
        return

    # 최종 조사 원문과 반증 회차 표가 있는지 확인한다. 반증 문서가 없으면 조사 문서에서 읽는다.
    # 사용자 검토가 끝난 항목은 원문 형식을 다시 검사하지 않는다.
    refute = refute or research
    done = review_done_numbers("\n".join(state.raw)) if reviewed else set()
    pending = [item for item in items if item.split(". ", 1)[0].split()[1] not in done
               and not (reviewed and review_exempt(state, plan, item))]
    check_result_sections(research, pending)
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
        title = f"{number}회차 반증 결과"
        raw = refute.section(title) if title in [name for _, _, name in refute.headings] else refute.raw_block(f"{number}회차 반증 원문")
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

    # 조사 항목마다 경과 문서 검토 필요 절의 확정 답 한 줄이 있는지 확인한다. 같은 번호 항목을 찾고, 없으면 첫 줄에 항목 이름이 든 항목을 찾는다.
    # 폐기 문제에 연결됐거나 작업 기준 자료에 결정이 기록된 항목은 검토 기록을 요구하지 않는다.
    review = state.section("검토 필요")
    entries = [(line, ok) for line, ok in review_entries("\n".join(state.raw)) if line.startswith("- 검토 ")]
    for item in items:
        if review_exempt(state, plan, item):
            continue
        number, name = item.split(". ", 1)
        base = PROBLEM_REF.sub("", name).strip()
        matches = ([e for e in entries if e[0].startswith("- 검토 " + number.split()[1] + ".")]
                   or [e for e in entries if base in e[0]])
        require(len(matches) == 1, R_REVIEW_ITEM.format(item=bare(item)))
        require(matches[0][1], R_REVIEW_UNDONE.format(item=bare(item)))

    # 반증이 남은 채 2회에 도달했으면 검증 미완 보고가 있는지 확인한다.
    if len(rows) >= 2 and int(rows[-1][1]):
        require("검증 미완" in review, R_UNVERIFIED)

# 요구사항마다 최종 검증 PASS 기록이 있고 남은 문제와 재구현 요청이 없는지 확인한다.
def check_complete(state: Document, plan: Document, names: list[str]) -> None:
    require(not unresolved(state), R_UNRESOLVED_STOP)
    # 검증을 시작했는지 확인하고 검증 결과 항목을 나눈다.
    review = state.section("검증 결과")
    require("검증 미개시" not in review and legacy("검증 미개시") not in review, R_REVIEW_NOT_STARTED)
    entries = re.split(r"(?m)^- (?=\d{4}-\d{2}-\d{2} )", review)[1:]

    # 새 양식 명세 문서면 검증 항목마다 요구사항 목록에 있는 번호 표기를 요구하고, 요구사항을 번호로 대조한다.
    by_number = numbered(plan)
    if by_number:
        for entry in entries:
            require(has_ref(entry.splitlines()[0], set(names)), R_REQ_REF.format(title="검증 결과", item=bare(entry.splitlines()[0][:40])))

    # 요구사항마다 마지막 검증 항목이 PASS인지 확인한다.
    for name in names:
        label = r".+ \(요구사항 (?:\d+, ?)*" + name + r"(?:, ?\d+)*\)" if by_number else re.escape(name)
        matches = [entry for entry in entries if re.match(
            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}: " + label
            + r" \((정적|실제 동작), (?:검증|" + legacy("검증") + r"|재검증 [12]/2|" + legacy("재검증") + r" [12]/2)\)\s*$",
            entry.splitlines()[0])]
        shown = f"요구사항 {name}" if by_number else name
        require(bool(matches), R_REVIEW_MISSING.format(name=bare(shown)))
        result = matches[-1]
        require(bool(re.search(r"^  - PASS: \S", result, re.M)) and
                not re.search(r"^  - (?:FAIL|미측정):", result, re.M), R_REVIEW_NOT_PASS.format(name=bare(shown)))

    # 남은 문제가 없고 처리하지 않은 재구현과 재검증 요청이 없는지 확인한다.
    require(bool(entries) and field(entries[-1], "남은 문제") in ("없음", "없음."), R_REMAINING)
    for title in ("재구현 요청", "재검증 요청"):
        # 주석은 section이 이미 빼므로, 하위 절 제목 줄을 뺀 남은 줄이 모두 빈 표기인지만 본다.
        lines = [line.strip().rstrip(".") for line in plan.section(title).splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
        require(all(line in ("없음", "- 없음", "기록 없음", "- 기록 없음") for line in lines),
                R_PENDING.format(title=title))


# 경과 문서의 검토 필요 항목과 실측 필요 항목이 끝났는지 확인한다. 완료 단계(final)는 사후 실측도 요구한다.
# 관리 단계는 현재 명세 요구사항을, 기존 작업은 기록된 승인 연결 대상을 실측 범위로 삼는다.
def measurement_targets(state_text: str, plan: Document | None = None) -> set[str] | None:
    if plan is not None and re.search(r"(?m)^- 작업 단계: (착수|조사|구현|진행|완료)\s*$", state_text):
        return {number_of(name) for name, body in requirements(plan) if not dropped(name, body)}
    return approval_targets(state_text)


# 검토 필요와 연결된 실측 필요 항목의 완료 상태를 확인한다.
def check_review_left(state: Document, final: bool, research: Document | None = None,
                      plan: Document | None = None) -> None:
    # 경과 문서 검토 필요 절에 확정 답이 없는 항목이 없는지 확인한다. 양식 주석 안의 형식 예시는 읽지 않는다.
    require(not review_pending("\n".join(state.raw)), R_REVIEW_LEFT)

    # 실측 필요 항목이 시점에 맞게 실측 완료인지 확인한다.
    # 구현 승인 단계에서는 승인 대상 요구사항과 연결되지 않은 사전 실측 항목을 뺀다.
    state_text = "\n".join(state.raw)
    targets = measurement_targets(state_text, plan)
    managed = bool(re.search(r"(?m)^- 작업 단계: (착수|조사|구현|진행|완료)\s*$", state_text))
    left = measure_pending(state_text, ("사전", "사후") if final else ("사전",), targets if managed or not final else None)
    require(not left, R_MEASURE_LEFT.format(items=", ".join(left[:3])))


# 실제 중단 유형이나 미해결 중단 판정이 있으면 True이다. 구현 승인 가정 검사의 안내 줄은 제외한다.
def unresolved(state: Document) -> bool:
    body = state.section("중단 사유")
    return any(filled(value) for value in values(body, "중단 유형")) or any(
        "중단 판정 미해결" in line and "구현 승인을 기록하면 차단됩니다" not in line for line in body.splitlines())

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
        managed_state = bool(documents and re.search(r"(?m)^- 작업 단계: (착수|조사|구현|진행|완료)\s*$", documents[0].header()))
        if kind == "research" and not path.is_file() and (stage == "kickoff" or managed_state):
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

    current = next((value.split(":", 1)[1].strip() for value in state.header().splitlines()
                    if value.startswith("- 작업 단계:")), "")
    managed = current in ("착수", "조사", "구현", "진행", "완료")
    if managed:
        permitted = {"kickoff": ("착수",), "research": ("조사",), "refute": ("조사",),
                     "implement": ("구현", "진행"), "verify": ("구현", "진행"),
                     "reimplement": ("구현", "진행"), "reverify": ("구현", "진행"), "complete": ("완료",)}
        require(current in permitted.get(stage, ()), f"작업 단계 [{current}]에서는 {stage}를 실행할 수 없습니다.")

    # 미해결 중단 판정과 기존 문서의 상태 기록을 확인한다.
    require(not unresolved(state), R_UNRESOLVED_STOP)
    status = current if managed else field(state.header(), "작업 상태")
    if not managed:
        require(status in ("미개시", "구현", "진행") or (stage == "complete" and status == "완료"), R_BAD_STATUS)

    # 착수 단계는 착수 종료 조건만 확인한다.
    if stage == "kickoff":
        check_kickoff(state if managed else research)
        return

    # 조사 승인과 조사 계획을 확인한다. 조사와 반증 단계는 여기서 끝난다.
    if not managed:
        approval(state, "조사 승인 범위")
    items = research_items(research) if research is not None else []
    if stage in ("research", "refute"):
        require(bool(items), R_NO_ITEMS)
        if stage == "refute":
            require(refute is not None or "조사 및 반증 루프" in [t for _, _, t in research.headings], R_NO_REFUTE)
            check_result_sections(research, items)
        return

    # 구현 승인, 입력 문서 계획, 조사 기록, 검토 필요와 실측 필요 항목을 확인한다.
    if not managed:
        approval(state, "구현 승인 범위")
        require(status in ("구현", "진행", "완료"), R_EXEC_STATUS)
    check_numbers(state, plan)
    names = check_plan(plan, state)
    if not managed:
        check_research(state, research, items, refute=refute, plan=plan)
    check_review_left(state, stage == "complete", None if managed else research, plan=plan)

    # 재구현과 재검증 단계는 요청 절의 대상과 위치를 확인한다.
    if stage in ("reimplement", "reverify"):
        title = "재구현 요청" if stage == "reimplement" else "재검증 요청"
        body = plan.section(title)
        key = "기대 결과" if stage == "reimplement" else "필요한 근거"
        require(filled(body) and bool(values(body, key)) and all(filled(v) for v in values(body, key)), R_REIMPLEMENT_TARGET.format(title=title, key=key))
        require(bool(re.search(r"^- .+: .+", body, re.M)), R_REIMPLEMENT_POSITION.format(title=title))
        if stage == "reimplement":
            for item in re.split(r"(?m)^- ", body)[1:]:
                if re.match(r"[^\n]+: .+", item):
                    expected = values(item, key)
                    require(bool(expected) and all(filled(value) for value in expected),
                            R_REIMPLEMENT_TARGET.format(title=title, key=key))

    # 완료 단계는 최종 검증 기록을 확인한다.
    if stage == "complete":
        check_complete(state, plan, names)


# 착수 종료 조건으로 프로젝트 환경, 기존 흐름 코드 블록, 조사 계획을 확인한다. 요구사항 목록은 계획 단계에서 확정하므로 요구하지 않는다.
def check_kickoff(research: Document) -> None:
    environment = research.section("프로젝트 환경")
    require(filled(environment) and "출처:" in environment, R_KICKOFF_ENV)

    # 기존 흐름 절 안에 비어 있지 않은 코드 블록이 있는지 확인한다.
    start, end = research.bounds("기존 흐름")
    require(any(start <= first < last < end and filled(body) for first, last, body in research.blocks), R_KICKOFF_FLOW)
    research_items(research)

# 상태 문서 새 내용이 조사 승인, 구현 승인, 완료로 바뀌면 선행 조건을 확인해 사유를 반환한다. 해당하지 않으면 None이다.
def transition_reason(path: Path, new: str | None, old: str, plan: str) -> str | None:
    # 새 문서 내용이 없으면 전환 검사를 건너뛴다.
    if new is None:
        return None

    # 이번 쓰기로 새로 승인되거나 완료되는 전이를 찾는다.
    approved = lambda text, title: re.search(r"^\s*- (?:사용자 )?승인 상태:\s*승인\s*$", section(text, title), re.M)
    kickoff = approved(new, "조사 승인 범위") and not approved(old, "조사 승인 범위")
    execute = approved(new, "구현 승인 범위") and not approved(old, "구현 승인 범위")
    from sub_approval import stage_of
    finished = lambda text: stage_of(text) == "완료" or (not stage_of(text) and bool(re.search(r"^- 작업 상태: 완료\s*$", text, re.M)))
    complete = finished(new) and not finished(old)
    if not (kickoff or execute or complete):
        return None

    # 전이에 맞는 선행 조건을 확인하고 미달 사유를 반환한다.
    try:
        state_doc, plan_doc = Document(new), Document(plan) if plan.strip() else None
        managed = bool(re.search(r"(?m)^- 작업 단계: (착수|조사|구현|진행|완료)\s*$", state_doc.header()))
        if managed and not complete:
            return None
        research_path = sibling(path, "research")
        research = Document(research_path.read_text(encoding="utf-8-sig")) if research_path.is_file() else None
        refute = sibling(path, "refute")
        refute_doc = Document(refute.read_text(encoding="utf-8-sig")) if refute.is_file() else None
        if kickoff:
            if not managed:
                check_kickoff(research)
        if execute or complete:
            if not managed:
                require(research is not None, "조사 문서가 없습니다. 조사 단계에서 생성하십시오.")
            require(plan_doc is not None, R_NO_SPEC)
            check_numbers(state_doc, plan_doc)
            require(not unresolved(state_doc), R_UNRESOLVED_STOP)
            names = check_plan(plan_doc, state_doc)
            if not managed:
                check_research(state_doc, research, research_items(research), refute=refute_doc, plan=plan_doc)
            check_review_left(state_doc, complete, None if managed else research, plan=plan_doc)
            if complete:
                check_complete(state_doc, plan_doc, names)
    except OSError as error:
        return R_IO.format(error=error)
    except ValueError as error:
        return str(error)
    return None


# 서브에이전트 호출이 단계 선행 조건을 채웠는지 확인해 사유를 반환한다. 대상 호출이 아니면 None이다.
def call_reason(data: dict) -> str | None:
    # 호출 판정에 쓰는 모듈을 불러온다.
    from sub_call import call_info, task_stage, ROLES
    from sub_docs import project_root, task_id

    # 역할 에이전트 호출이고 입력에 작업 ID가 있을 때만 검사한다.
    target, prompt = call_info(data)
    if target not in ROLES:
        return None
    task = task_id(prompt)
    if not task:
        return None

    # task 값을 단계 이름으로 읽어 선행 조건을 확인한다.
    kind = task_stage(prompt)
    try:
        require(bool(kind), R_KIND)
        validate(project_root(data.get("cwd")), task, kind)
    except OSError as error:
        return R_IO.format(error=error)
    except ValueError as error:
        return str(error)
    return None


STAGES = ("kickoff", "research", "refute", "implement", "reimplement", "complete")
# 서브에이전트 호출 검사에만 쓰는 단계이다. 단계 실행기는 실행하지 않는다.
CALL_STAGES = ("verify", "reverify")
TASK_ID = re.compile(r"\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*")
CASE_ROW = re.compile(r"^\|\s*(차단|허용)\s*\|([^|\n]*)\|([^|\n]*)\|([^|\n]*)\|\s*$", re.M)


ASSIGN_ROLES = ("worker", "documenter", "reviewer", "researcher", "director")
R_ASSIGN_ROLE = "배정 범위의 담당 `{roles}`은 `- <역할>: <범위>` 형식의 worker, documenter, reviewer, researcher, director여야 합니다."
R_LIST_SOURCE = "`{title}` 항목 `{item}`에 `파일:행` 근거가 없습니다."
R_ITEM_FIELD = "`{name}`의 `{key}` 칸이 비어 있습니다."
R_REVIEW_TYPE = "`{name}`의 검증에 (정적) 또는 (실제 동작) 유형이 없습니다."
R_PLACEHOLDER = "`{title}`에 채우지 않은 자리표시자가 있습니다."
R_SECTION_EMPTY = "`{title}` 절이 비어 있습니다."
R_SCOPE_KEY = "변경 범위에 `{key}` 항목이 없습니다."
R_DOC_TARGET = "문서 갱신 대상에 `파일:행` 또는 검색어와 일치 없음 기록이 없습니다."
R_CHECK_PLACE = "검증 계획에 실행 위치가 없습니다."
R_CHECK_ACTUAL = "검증 계획의 실제 검증에 확인 수단 출처가 없습니다."
R_CHECK_CMDS = "검증 계획에 검증 커맨드 목록이 없습니다."


R_PROBLEM_LINK = "요구사항 `{name}`의 `- 문제:` 줄이 없거나 확정되지 않았거나 폐기된 문제를 가리킵니다. 확정된 문제 번호에 연결하십시오."
R_PROBLEM_MISSING = "확정된 문제 {numbers}에 연결된 요구사항이 없습니다. 요구사항의 `- 문제:` 줄로 연결하거나 문제 폐기 질문으로 정리하십시오."
R_GAP_LIMIT = "요구사항 `{name}`에 한계, 출처와 확인 방법이 없습니다."
R_GAP_KINDS = "요구사항 `{name}`의 Test Case 표에 차단 행과 허용 행이 1행 이상 있어야 합니다."
R_GAP_FIELDS = "요구사항 `{name}`의 Test Case 행마다 입력, 커맨드와 기대 결과가 있어야 합니다."


R_ITEM_FORMAT = "조사 계획의 조사 항목 이름이 `조사 <번호>. <이름>` 형식이 아닙니다."
R_NO_EXCLUDE = "조사 계획에 제외 범위가 없습니다."
R_EXCLUDE_SOURCE = "조사 계획의 제외 조건에 URL 또는 `파일:행` 형식의 출처가 없습니다."
SOURCE_FORMAT = re.compile(r"https?://\S+|[^\s:]*[./\\][^\s:]*:\d+")
SOURCE_VALUE = re.compile(r"출처:\s*([^)\n]*)")
R_SOURCE_FORMAT = "조사 문서의 출처가 URL 또는 `파일:행` 형식이 아닙니다. 출처 `{value}`를 고치십시오."


PROBLEM_REF = re.compile(r"\(문제 (\d+(?:, ?\d+)*)\)")
R_PROBLEM_REF = "조사 계획의 항목 `{item}`에 `(문제 <번호>)` 표기가 없거나 `문제 <번호> 조사 필요 판정`에 없는 번호입니다."


R_RESULT_EMPTY = "조사 원문이 비어 있습니다."
R_RESULT_ITEM = "조사 원문에 `{item}` 항목이 없습니다."
R_RESULT_LABEL = "조사 원문의 `{item}`에 `{label}` 항목이 없습니다."
R_RESULT_FIT = "조사 원문의 `{item}`에 후보 충족 여부가 없습니다."


R_NO_VERDICT = "반증 판정과 출처가 없습니다."
R_VERDICT_SOURCE = "반증 판정의 출처가 URL 또는 `파일:행` 형식이 아닙니다."
R_VERDICT_FORMAT = "반증 판정 줄이 `주장 <번호>: <지지 | 반증 | 미확인> | 출처: <URL 또는 파일:행>` 형식이 아닙니다. 줄: `{line}`"
CLAIM_START = re.compile(r"^[\s\-*]*주장\s*\d+\s*[:：]\s*(?:지지|반증|미확인)")


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
R_UNVERIFIED = "반증 2회에 도달했으므로 검증 미완을 사용자 보고로 기록해야 합니다."


R_REVIEW_NOT_STARTED = "검증을 시작하지 않은 작업은 완료로 쓸 수 없습니다."
R_REVIEW_MISSING = "`{name}`의 검증 결과가 없습니다."
R_REVIEW_NOT_PASS = "`{name}`의 최종 검증 결과가 PASS가 아닙니다."
R_REMAINING = "검증 결과의 남은 문제가 없음으로 기록돼 있지 않습니다."
R_PENDING = "`{title}`에 처리하지 않은 항목이 있습니다."


R_TASK_ID = "작업 ID 형식이 올바르지 않습니다."
R_STAGE = "지원하지 않는 단계 `{stage}`입니다."
R_OUTSIDE = "문서 경로가 저장소 밖을 가리킵니다."
R_ID_MISMATCH = "`{name}`의 작업 ID가 요청한 작업 ID와 다릅니다."
R_UNRESOLVED_STOP = "중단 사유에 미해결 중단 판정이 있어 다음 단계로 진행할 수 없습니다."
R_BAD_STATUS = "작업 상태가 중단이거나 이 단계에서 쓸 수 없는 값입니다."
R_NO_ITEMS = "조사할 조사 항목이 없습니다."
R_EXEC_STATUS = "구현 승인 뒤 작업 상태는 구현이어야 합니다."
R_REVIEW_LEFT = "상태 문서의 검토 필요에 검토가 끝나지 않은 항목이 있습니다."
R_MEASURE_LEFT = "상태 문서의 모델 호출 실측 필요에 실측이 끝나지 않은 항목({items})이 있습니다."


R_REIMPLEMENT_TARGET = "`{title}`에 대상과 `{key}` 칸이 있어야 합니다."
R_REIMPLEMENT_POSITION = "`{title}`에 대상 위치가 없습니다."

R_NO_REFUTE = "반증 문서가 없습니다."
R_NO_SPEC = "명세 문서가 없습니다."
SPECLESS_STAGES = ("kickoff", "research", "refute")


R_KICKOFF_ENV = "착수 기록의 프로젝트 환경에 확인 내용과 출처가 없습니다."
R_KICKOFF_FLOW = "착수 기록의 `기존 흐름` 절에 코드 블록이 없습니다."


R_IO = "작업 문서를 읽지 못했습니다({error})."


R_KIND = "입력의 task 값이 없거나 지원하지 않는 값입니다."
