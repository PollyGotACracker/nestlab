#!/usr/bin/env python3
"""작업 문서(경과, 명세, 조사, 반증 문서)의 단계 선행 조건을 판정하는 공용 모듈이다.

호출자:
- hooks/check_tool_use.py
- hooks/check_tool_use/sub_judgment.py
- hooks/save_handback_report.py, hooks/save_subagent_result.py, tools/run_workflow.py
입력: 작업 문서 본문 또는 저장소 루트, 작업 ID, 단계 이름
반환:
- 검사 함수(validate, check_plan, research_items, check_complete 등)는 통과하면 None을 반환하고
  미달이면 사유 문자열을 담은 ValueError를 던진다.
- transition_reason, call_reason은 차단 사유 문자열 또는 통과 시 None을 반환한다.
한계: 문서의 구조와 칸 형식만 판정하고 자연어 내용의 정확성은 판정하지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from sub_docs import FENCE_LINE, approval_targets, legacy, measure_pending, review_pending, section, sibling, task_doc, titles
from sub_document import Document, R_REQ_REF, approval, bare, check_numbers, check_refs, dropped, field, filled, has_ref, integrated, number_of, numbered, require, requirements, timestamp, values


# 명세 문서가 구현 승인 전 필수 칸을 갖췄는지 확인하고 요구사항 이름 목록을 반환한다.
# state와 every는 기존 호출 호환용이며 판정에 사용하지 않는다.
def check_plan(plan: Document, state: Document | None = None, every: bool = True) -> list[str]:
    from sub_docs import constraint_reason
    reason = constraint_reason("\n".join(plan.raw), "input")
    require(reason is None, reason or "")
    # 요구사항마다 필수 칸, 검증 유형, 한계와 Test Case 표를 확인한다.
    items = requirements(plan)
    unified = integrated("\n".join(plan.raw))
    for name, body in items:
        # 폐기한 요구사항 절은 필수 칸을 검사하지 않는다.
        if dropped(name, body):
            continue
        # 통합 구조 항목은 hook이 기록한 사용자 확인 시각, 문제와 기대 결과 칸, director가 쓴 잠재 위험 칸을 함께 요구한다.
        if unified:
            for key in ("사용자 확인 시각", "문제", "목표", "기대 결과", "잠재 위험", "검증"):
                entries = values(body, key)
                require(len(entries) == 1 and filled(entries[0]), R_ITEM_FIELD.format(name=bare(name), key=key))
            timestamp("사용자 확인 시각", values(body, "사용자 확인 시각")[0])
            require(bool(re.search(r"\((?:정적|실제 동작)\)", values(body, "검증")[0])), R_REVIEW_TYPE.format(name=bare(name)))
            require(not case_gaps(name, body), "; ".join(case_gaps(name, body)))
            continue
        for key in ("목표", "결과", "검증", "기대 결과"):
            entries = values(body, key)
            require(bool(entries) and all(filled(v) for v in entries), R_ITEM_FIELD.format(name=bare(name), key=key))
        require(all(re.search(r"\((?:정적|실제 동작)\)", v) for v in values(body, "검증")), R_REVIEW_TYPE.format(name=bare(name)))
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
    if not limits or not all(v == "없음" or filled(v) and "출처:" in v and "확인 방법:" in v for v in limits):
        gaps.append(R_GAP_LIMIT.format(name=bare(name)))

    # 차단 동작이 없다고 명시한 요구사항만 허용 행으로 검증한다.
    rows = CASE_ROW.findall(body)
    kinds = {kind for kind, *_ in rows}
    required = {"허용"} if re.search(r"(?m)^\s*- 차단 동작:[ \t]*없음[ \t]*$", body) else {"차단", "허용"}
    if not required <= kinds:
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
    if plan is not None:
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
    left = measure_pending(state_text, ("사전", "사후") if final else ("사전",), targets)
    require(not left, R_MEASURE_LEFT.format(items=", ".join(left[:3])))


# 실제 중단 유형이나 미해결 중단 판정이 있으면 True이다. 구현 승인 가정 검사의 안내 줄은 제외한다.
def unresolved(state: Document) -> bool:
    body = state.section("중단 사유")
    return any(filled(value) for value in values(body, "중단 유형")) or any(
        "중단 판정 미해결" in line and "구현 승인을 기록하면 차단됩니다" not in line for line in body.splitlines())

# 작업 문서를 읽어 단계 또는 배정 종류의 선행 조건을 확인한다. 미달이면 사유를 담은 ValueError를 던진다.
def validate(root: Path, task: str, stage: str) -> None:
    # 작업 ID와 단계 이름을 확인한다.
    require(bool(TASK_ID.fullmatch(task)), R_TASK_ID)
    require(stage in STAGE_OF, R_STAGE.format(stage=stage))

    # 경과, 명세, 조사, 반증 문서를 읽고 작업 ID가 일치하는지 확인한다. 경과 문서 외에는 있을 때만 읽는다.
    documents = []
    for kind in ("state", "input", "research", "refute"):
        path = task_doc(root, task, kind)
        require(path.resolve().is_relative_to(root.resolve()), R_OUTSIDE)
        if kind != "state" and not path.is_file():
            require(kind != "input" or STAGE_OF[stage] == "계획", R_NO_SPEC)
            documents.append(None)
            continue
        document = Document(path.read_text(encoding="utf-8-sig"))
        require(field(document.header(), "작업 ID") == task, R_ID_MISMATCH.format(name=path.name))
        documents.append(document)
    state, plan, research, refute = documents

    # 경과 문서의 작업 단계가 요청 단계에 맞는지와 미해결 중단 판정을 확인한다.
    current = next((value.split(":", 1)[1].strip() for value in state.header().splitlines()
                    if value.startswith("- 작업 단계:")), "")
    require(current == STAGE_OF[stage], R_STAGE_MISMATCH.format(current=current or "미지정", stage=stage))
    require(not unresolved(state), R_UNRESOLVED_STOP)

    # 계획 단계는 계획 종료 조건을, 조사와 반증 배정은 조사 계획과 조사 결과를 확인한다.
    if stage == "plan":
        check_plan_exit(state)
        return
    if stage in ("research", "refute"):
        require(research is not None, R_NO_RESEARCH)
        items = research_items(research)
        require(bool(items), R_NO_ITEMS)
        if stage == "refute":
            require(refute is not None or "조사 및 반증 루프" in [t for _, _, t in research.headings], R_NO_REFUTE)
            check_result_sections(research, items)
        return

    # 원인 분석, 문서 구조와 한계, 명세 계획, 검토 필요와 실측 필요 항목을 확인한다.
    check_causes(state)
    from sub_docs import structure_reason, constraint_reason, mask_results
    # 결과 저장 hook만 쓰는 결과 블록은 배정 단계에서 고칠 수 없으므로 출처와 값 검사에서 제외한다.
    for kind, document in zip(("state", "input", "research", "refute"), documents):
        if document is not None:
            reason = structure_reason("\n".join(document.raw), kind) or constraint_reason(mask_results("\n".join(document.raw)), kind)
            require(reason is None, reason or "")
    check_numbers(state, plan)
    # 재구현과 재검증은 기존 요구사항의 결함 처리이므로 아직 요구사항이 없는 새 확정 문제로 막지 않는다.
    names = check_plan(plan, state, every=stage not in ("reimplement", "reverify"))
    check_review_left(state, stage == "complete", plan=plan)

    # 재구현과 재검증 단계는 요청 절의 대상과 위치를 확인한다.
    if stage in ("reimplement", "reverify"):
        title = "재구현 요청" if stage == "reimplement" else "재검증 요청"
        body = plan.section(title)
        key = "기대 결과"
        keys = ("판정 기준", "커맨드") if stage == "reverify" else (key,)
        for k in keys:
            require(filled(body) and bool(values(body, k)) and all(filled(v) for v in values(body, k)), R_REIMPLEMENT_TARGET.format(title=title, key=k))
        require(bool(re.search(r"^- .+: .+", body, re.M)), R_REIMPLEMENT_POSITION.format(title=title))
        # 재검증 요청 항목은 판정 기준과 커맨드 칸을 모두 채워야 한다.
        for item in (re.split(r"(?m)^- ", body)[1:] if stage == "reverify" else []):
            if re.match(r"[^\n]+: .+", item):
                for k in keys:
                    found = values(item, k)
                    require(bool(found) and all(filled(value) for value in found), R_REIMPLEMENT_TARGET.format(title=title, key=k))
        if stage == "reimplement":
            for item in re.split(r"(?m)^- ", body)[1:]:
                if re.match(r"[^\n]+: .+", item):
                    expected = values(item, key)
                    require(bool(expected) and all(filled(value) for value in expected),
                            R_REIMPLEMENT_TARGET.format(title=title, key=key))

    # 완료 단계는 최종 검증 기록을 확인한다.
    if stage == "complete":
        check_complete(state, plan, names)


# 계획 종료 조건으로 경과 문서의 프로젝트 환경, 기존 흐름 코드 블록, 조사 계획을 확인한다.
def check_plan_exit(state: Document) -> None:
    environment = state.section("프로젝트 환경")
    require(filled(environment) and "출처:" in environment, R_PLAN_EXIT_ENV)

    # 기존 흐름 절 안에 비어 있지 않은 코드 블록이 있는지 확인한다.
    start, end = state.bounds("기존 흐름")
    require(any(start <= first < last < end and filled(body) for first, last, body in state.blocks), R_PLAN_EXIT_FLOW)
    research_items(state)

# 경과 문서 원인 분석 항목마다 원인, 근거 내용, 출처, 한계가 채워졌는지 확인한다. 미달이면 ValueError를 던진다.
def check_causes(state: Document) -> None:
    reason = "경과 문서 `원인 분석` 절: 원인, 근거 내용, 출처 또는 한계가 없습니다. log-status 양식을 따르십시오."
    try:
        body = state.section("원인 분석")
    except ValueError:
        raise ValueError(reason) from None
    items = re.split(r"(?m)^- ", body)[1:]
    require(bool(items), reason)
    for item in items:
        head = item.splitlines()[0]
        require(bool(re.fullmatch(r"[^:\n]+: \S.*", head)) and filled(head) and not UNKNOWN_CAUSE.search(head)
                and "근거 출처" not in item, reason)
        for key in ("근거 내용", "출처", "한계"):
            try:
                value = field(item, key)
            except ValueError:
                raise ValueError(reason) from None
            require(value == "없음" if key == "한계" and not filled(value) else filled(value), reason)
            require(key != "한계" or value == "없음" or "출처:" in value, reason)


# 경과 문서 쓰기가 구현 승인, 구현 단계 또는 완료로 바뀌면 선행 조건을 확인해 사유를 반환한다. 해당하지 않으면 None이다.
def transition_reason(path: Path, new: str | None, old: str, plan: str) -> str | None:
    # 새 문서 내용이 없으면 전환 검사를 건너뛴다.
    if new is None:
        return None

    # 이번 쓰기로 새로 승인되거나 완료되는 전환을 찾는다.
    from sub_approval import stage_of
    approved = lambda text: re.search(r"^\s*- (?:사용자 )?승인 상태:\s*승인\s*$", section(text, "구현 승인 범위"), re.M)
    execute = (approved(new) and not approved(old)) or (stage_of(new) == "구현" and stage_of(old) != "구현")
    complete = stage_of(new) == "완료" and stage_of(old) != "완료"
    if not (execute or complete):
        return None

    # 구현 전환은 원인 분석을, 완료 전환은 명세와 검증 기록을 확인한다.
    try:
        state_doc = Document(new)
        if execute:
            check_causes(state_doc)
        if complete:
            plan_doc = Document(plan) if plan.strip() else None
            require(plan_doc is not None, R_NO_SPEC)
            check_numbers(state_doc, plan_doc)
            require(not unresolved(state_doc), R_UNRESOLVED_STOP)
            names = check_plan(plan_doc, state_doc)
            check_review_left(state_doc, True, plan=plan_doc)
            check_complete(state_doc, plan_doc, names)
    except OSError as error:
        return R_IO.format(error=error)
    except ValueError as error:
        return str(error)
    return None


# 구현 승인 요청 줄이다. 에이전트는 이 한 줄만 쓰고 hook이 생성한 승인 요청 본문을 사용자에게 표시한다.
REQUEST_LINE = re.compile(r"(?m)^[ \t]*구현 승인 요청:[ \t]*`?(\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*)`?[ \t]*$")
R_REQUEST_NO_DOC = "구현 승인 요청: 작업 {task}의 경과 문서와 명세 문서가 없습니다. 작업 문서 생성이 먼저입니다."
R_REQUEST_BLANK = "구현 승인 요청: 명세 문서 요구사항의 빈 칸부터 채우십시오. 빈 칸: {items}"
R_REQUEST_PENDING = ("구현 승인 요청: 경과 문서의 검토 필요 또는 사전 모델 호출 실측 필요에 끝나지 않은 항목({items})이 있습니다. "
                     "검토 필요 항목은 확정 답을, 모델 호출 실측 필요 항목은 상태 `모델 호출 실측 완료`를 기록하십시오.")
R_REQUEST_PLAN = "구현 승인 요청: 명세 문서를 log-spec 양식대로 채우십시오. 사유: {reason}"
R_REQUEST_TRANSITION = "구현 승인 요청: 경과 문서에 구현 승인을 기록하면 차단됩니다. 사유: {reason}"
# Codex에서 hook 안내를 Hook feedback으로 표시한 뒤 모델이 작성할 확인 줄이다.
ACK_LINE = "hook 안내를 사용자에게 표시했습니다."


# Codex Stop block reason에 담아 사용자에게 표시할 안내와 확인 줄 지시를 반환한다.
def shown_reason(notice: str) -> str:
    return f"{notice}\n\n위 안내는 사용자에게 표시되었습니다. 응답에는 `{ACK_LINE}` 한 줄만 작성하십시오."


# 명세 문서의 폐기되지 않은 요구사항 번호 목록이다.
def live_numbers(plan: str) -> list[str]:
    from sub_spec import blocks
    body = section(plan.replace("\r\n", "\n"), "요구사항 목록")
    return [str(n) for n, block in blocks(body) if n is not None and not block.split("\n", 1)[0].strip().endswith(". 폐기")]


# 경과 문서 구현 승인 범위 절의 승인 상태를 승인으로, 승인 시각을 stamp로, 연결 대상을 요구사항 번호로 바꾼 본문을 반환한다.
def approved_status(text: str, stamp: str, numbers: list[str]) -> str:
    head = re.search(r"(?m)^#{2,3} 구현 승인 범위[ \t]*$", text)
    if not head:
        return text
    tail = re.search(r"(?m)^#{1,3} ", text[head.end():])
    end = head.end() + tail.start() if tail else len(text)
    body = re.sub(r"(?m)^(\s*-\s*(?:사용자 )?승인 상태:).*$", r"\1 승인", text[head.end():end], count=1)
    body = re.sub(r"(?m)^(\s*-\s*(?:사용자 )?승인 시각:).*$", lambda m: f"{m[1]} {stamp}", body, count=1)
    if numbers:
        body = re.sub(r"(?m)^(\s*-\s*연결 대상:).*$", lambda m: f"{m[1]} 요구사항 {', '.join(numbers)}", body, count=1)
    return text[:head.end()] + body + text[end:]


# 작업의 구현 승인 요청을 검사해 (차단 사유, 사용자에게 표시할 승인 요청 본문)을 반환한다. 통과하면 사유가 None이다.
def approval_request(root: Path, task: str) -> tuple[str | None, str]:
    from sub_docs import strip_comments
    from sub_task_docs import approval_text
    from sub_write import read
    status = task_doc(root, task, "state")
    spec = sibling(status, "input")
    if not status.is_file() or not spec.is_file():
        return R_REQUEST_NO_DOC.format(task=task), ""
    text, plan = read(status), read(spec)

    # 끝나지 않은 검토와 사전 실측이 있으면 요청할 수 없다.
    try:
        targets = measurement_targets(text, Document(plan))
    except ValueError:
        targets = None
    pending = review_pending(text) + measure_pending(text, targets=targets)
    if pending:
        return R_REQUEST_PENDING.format(items=", ".join(pending[:3])), ""

    # 생성 출력에 자리표시자가 남으면 명세 빈 칸부터 채우게 한다.
    output = approval_text(status)
    blanks = [line.strip() for line in strip_comments(output).splitlines() if re.search(r"<[^<>\n]+>", line)]
    if blanks:
        return R_REQUEST_BLANK.format(items=" / ".join(blanks[:3])), ""

    # 명세 양식과 구현 승인 전환 조건을 확인한다.
    try:
        state_doc, plan_doc = Document(text), Document(plan)
        check_numbers(state_doc, plan_doc)
        check_plan(plan_doc, state_doc)
    except ValueError as error:
        return R_REQUEST_PLAN.format(reason=error), ""
    reason = transition_reason(status, approved_status(text, "", live_numbers(plan)), text, plan)
    if reason:
        return R_REQUEST_TRANSITION.format(reason=reason), ""
    return None, output


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


# 작업 단계 이름과 경과 문서의 작업 단계 값이다.
STAGES = ("plan", "implement", "complete")
STAGE_DOCS = {"plan": "계획", "implement": "구현", "complete": "완료"}
# 서브에이전트 호출 검사에만 쓰는 단계이다. 단계 실행기는 실행하지 않는다.
CALL_STAGES = {"research": "계획", "refute": "계획", "reimplement": "구현", "verify": "구현", "reverify": "구현"}
STAGE_OF = {**STAGE_DOCS, **CALL_STAGES}
R_STAGE_MISMATCH = "작업 단계 [{current}]에서는 {stage}를 실행할 수 없습니다. 작업 단계에 맞는 배정을 요청하십시오."
R_NO_RESEARCH = "조사 문서가 없습니다. nestlab-task-docs로 계획 단계 문서를 생성하십시오."
TASK_ID = re.compile(r"\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*")
# 원인 분석 항목의 원인 칸에 쓸 수 없는 미특정 표현이다.
UNKNOWN_CAUSE = re.compile(r"미확인|확인 필요|불명")
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


R_GAP_LIMIT = "명세 문서 요구사항 `{name}`: 한계 칸에 출처와 확인 방법이 없습니다. log-spec 양식을 따르십시오."
R_GAP_KINDS = "명세 문서 요구사항 `{name}`: Test Case 표에 차단 또는 허용 행이 없습니다. log-spec 양식을 따르십시오."
R_GAP_FIELDS = "명세 문서 요구사항 `{name}`: Test Case 표의 입력, 커맨드 또는 기대 결과 칸이 비어 있습니다. log-spec 양식을 따르십시오."


R_ITEM_FORMAT = "조사 계획의 조사 항목 이름이 `조사 <번호>. <이름>` 형식이 아닙니다."
R_NO_EXCLUDE = "조사 계획에 제외 범위가 없습니다."
R_EXCLUDE_SOURCE = "조사 계획의 제외 조건에 URL 또는 `파일:행` 형식의 출처가 없습니다."
SOURCE_FORMAT = re.compile(r"https?://\S+|[^\s:]*[./\\][^\s:]*:\d+")
SOURCE_VALUE = re.compile(r"출처:\s*([^)\n]*)")
R_SOURCE_FORMAT = "조사 문서의 출처가 URL 또는 `파일:행` 형식이 아닙니다. 출처 `{value}`를 고치십시오."


PROBLEM_REF = re.compile(r"\(문제 (\d+(?:, ?\d+)*)\)")
R_PROBLEM_REF = "조사 계획 항목 `{item}`: 문제 번호 표기가 log-research 양식과 다릅니다."


R_RESULT_EMPTY = "조사 결과: 결과 본문이 비어 있습니다. log-research 양식의 결과 블록을 채우십시오."
R_RESULT_ITEM = "조사 결과 `{item}`: 결과 블록이 없습니다. log-research 양식의 결과 블록 제목을 쓰십시오."
R_RESULT_LABEL = "조사 결과 `{item}`: `{label}` 칸이 없습니다. log-research 양식을 따르십시오."
R_RESULT_FIT = "조사 결과 `{item}`: 충족 여부 칸이 없습니다. log-research 양식을 따르십시오."


R_NO_VERDICT = "반증 판정과 출처가 없습니다."
R_VERDICT_SOURCE = "반증 판정의 출처가 URL 또는 `파일:행` 형식이 아닙니다."
R_VERDICT_FORMAT = "반증 결과 `{line}`: 판정 줄 형식이 log-refute 양식과 다릅니다."
CLAIM_START = re.compile(r"^[\s\-*]*주장\s*\d+\s*[:：]\s*(?:지지|반증|미확인)")




R_REVIEW_NOT_STARTED = "검증을 시작하지 않은 작업은 완료로 쓸 수 없습니다."
R_REVIEW_MISSING = "`{name}`의 검증 결과가 없습니다."
R_REVIEW_NOT_PASS = "`{name}`의 최종 검증 결과가 PASS가 아닙니다."
R_REMAINING = "검증 결과의 남은 문제가 없음으로 기록돼 있지 않습니다."
R_PENDING = "명세 문서 `{title}` 절: 처리하지 않은 요청이 있습니다. log-spec 양식을 따르십시오."


R_TASK_ID = "작업 ID 형식이 올바르지 않습니다."
R_STAGE = "지원하지 않는 단계 `{stage}`입니다."
R_OUTSIDE = "문서 경로가 저장소 밖을 가리킵니다."
R_ID_MISMATCH = "`{name}`의 작업 ID가 요청한 작업 ID와 다릅니다."
R_UNRESOLVED_STOP = "중단 사유에 미해결 중단 판정이 있어 다음 단계로 진행할 수 없습니다."
R_NO_ITEMS = "조사할 조사 항목이 없습니다."
R_REVIEW_LEFT = "상태 문서의 검토 필요에 검토가 끝나지 않은 항목이 있습니다."
R_MEASURE_LEFT = "상태 문서의 모델 호출 실측 필요에 실측이 끝나지 않은 항목({items})이 있습니다."


R_REIMPLEMENT_TARGET = "명세 문서 `{title}`: `{key}` 칸이 비어 있습니다. log-spec 양식을 따르십시오."
R_REIMPLEMENT_POSITION = "명세 문서 `{title}`: 수정 위치 항목이 없습니다. log-spec 양식을 따르십시오."

R_NO_REFUTE = "반증 문서가 없습니다."
R_NO_SPEC = "명세 문서가 없습니다."


R_PLAN_EXIT_ENV = "경과 문서 `프로젝트 환경` 절: 출처가 없습니다. log-status 양식을 따르십시오."
R_PLAN_EXIT_FLOW = "경과 문서 `기존 흐름` 절: 채운 코드 블록이 없습니다. log-status 양식을 따르십시오."


R_IO = "작업 문서를 읽지 못했습니다({error})."


R_KIND = "입력의 task 값이 없거나 지원하지 않는 값입니다."
