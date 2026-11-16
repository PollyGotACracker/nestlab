"""명세 문서 요구사항 절의 hook 관리 칸(통합 구조의 사용자 확인 시각, 문제, 기대 결과와 기존 구조의 결과, 기대 결과)을 읽고 쓰는 공용 모듈이다.

호출자:
- hooks/check_workflow_response.py(검토 확정 답을 결과 칸에 기록, 폐기 답을 `### 요구사항 <번호>. 폐기` 절로 기록)
- hooks/check_tool_use/sub_scope.py(문제 정의 확정 답을 사용자 확인 시각과 문제 칸에 기록, 에이전트의 hook 관리 칸 직접 수정 잠금)
- hooks/common/sub_task_docs.py(구현 승인 요청 본문 생성)
입력: 명세 문서의 `요구사항 목록` 절 본문 또는 명세 문서 전문
반환: 칸 값 dict 또는 칸을 바꾼 절 본문
한계: `###` 제목이 코드 블록 안에 있으면 요구사항 절 경계로 오인한다.
기존 구조(`- 칸:` 줄) 절은 기존 구조로 읽고 쓴다. 통합 구조는 `#### 칸` 제목 아래 본문을 칸 값으로 쓰며 hook 관리 칸은 사용자 확인 시각, 문제, 기대 결과이다.
"""
from __future__ import annotations

import re

from sub_docs import template_section

TEMPLATE = "spec"
BLOCK = re.compile(r"(?m)^(?=### )")
NUMBER = re.compile(r"### (?:요구사항 )?(\d+)\. ")
PLACEHOLDER = {"기대 결과": "  - <판정 기준>"}
# 통합 전 기존 구조 명세 문서에 새 절을 만들 때 쓰는 절이다.
LEGACY_BLOCK = ("### <번호>. <요구사항 이름>\n\n- 문제: 문제 <번호>\n- 목표:\n  - <요구사항의 목표>\n"
                "- 결과:\n  - <목표를 달성했을 때 기대되는 결과>\n- 검증:\n  - <검증 내용> (<정적 | 실제 동작>)\n"
                "- 기대 결과:\n  - <판정 기준>\n- 한계:\n  - <설계 자체의 제약 | 없음> (출처: <파일:행 | URL>, 확인 방법: <방법>)\n"
                "- Test Case:\n  - 차단 동작: <있음 | 없음>\n\n")
LOCKED = ("사용자 확인 시각", "문제", "기대 결과", "결과")


# 절 본문을 (번호, 절 본문) 목록으로 나눈다. 번호가 없는 머리말과 양식 자리표시 절의 번호는 None이다.
def blocks(body: str) -> list[tuple[int | None, str]]:
    out = []
    for part in BLOCK.split(body):
        m = NUMBER.match(part)
        if part:
            out.append((int(m[1]) if m else None, part))
    return out


# 절이 칸을 `#### 칸` 제목으로 쓰는 통합 구조이면 True이다.
def heading_form(block: str) -> bool:
    return bool(re.search(r"(?m)^#### \S", block))


# 절에서 칸의 (줄 목록, 본문 시작, 본문 끝)이다. 통합 구조는 `#### name` 제목, 기존 구조는 `- name:` 줄이 칸이다.
# 칸이 없으면 시작과 끝이 -1이다. 본문 앞뒤 빈 줄은 범위에서 뺀다.
def span(block: str, name: str) -> tuple[list[str], int, int]:
    lines = block.split("\n")
    head = next((i for i, l in enumerate(lines) if l.rstrip() == f"#### {name}"), None)
    if head is not None:
        start = head + 1
        end = next((i for i in range(start, len(lines)) if re.match(r"#{1,6} ", lines[i])), len(lines))
        while start < end and not lines[start].strip():
            start += 1
        while end > start and not lines[end - 1].strip():
            end -= 1
        return lines, start, end
    start = next((i for i, l in enumerate(lines) if l.rstrip() == f"- {name}:"), None)
    if start is None:
        return lines, -1, -1
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][:1] == " "):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return lines, start + 1, end


# 칸 본문을 줄 목록으로 바꾼 절을 반환한다. 줄이 없으면 기존 구조 양식 자리표시 줄을 쓴다. 칸이 없으면 절 그대로이다.
def set_lines(block: str, name: str, new: list[str]) -> str:
    lines, start, end = span(block, name)
    if start < 0:
        return block
    return "\n".join(lines[:start] + (new or [PLACEHOLDER.get(name, "")]) + lines[end:])


# 칸 본문을 줄 끝 공백 없이 이은 문자열이다.
def text_of(block: str, name: str) -> str:
    lines, start, end = span(block, name)
    return "\n".join(l.rstrip() for l in lines[start:end]).strip() if start >= 0 else ""


# 명세 양식의 요구사항 절 자리표시 본문이다. legacy이면 기존 구조 절을 쓰고, 양식을 읽지 못하면 기존 구조 절을 쓴다.
def template_block(legacy: bool = False) -> str:
    tpl = "" if legacy else template_section(TEMPLATE, "요구사항 목록")
    if "### " in tpl:
        return tpl[tpl.index("### "):].rstrip("\n") + "\n\n"
    return LEGACY_BLOCK


# 한 절에서 hook만 쓰는 칸의 값을 이은 문자열이다.
def locked_text(block: str) -> str:
    return "\n--\n".join(text_of(block, name) for name in LOCKED)


# 명세 전문 또는 요구사항 목록 절 본문에서 번호별 hook 관리 칸 값이다.
def cells(body: str) -> dict[int, str]:
    return {k: locked_text(t) for k, t in blocks(body) if k is not None}


# 양식 자리표시 절의 hook 관리 칸 값이다. legacy이면 기존 구조 절의 값이다.
def blank_cells(legacy: bool = False) -> str:
    return locked_text(template_block(legacy))


# 요구사항 목록 절 본문에 ops를 순서대로 적용한 본문을 반환한다. names는 새 절 제목에 쓸 번호별 이름이다.
# ops:
# - ("req", 번호, 확정 안 설명, 문제 표기[, 복사할 문제 항목 번호]): 결과 칸을 쓴다.
# - ("problem", 번호, 문제 정의 줄, 답 시각): 통합 구조의 사용자 확인 시각과 문제 칸을 쓴다.
# - ("drop", 번호, 사유): 절을 폐기 절로 바꾼다. 사유는 쓰지 않는다.
def apply(body: str, ops: list[tuple], names: dict[int, str]) -> str:
    parts = blocks(body)
    legacy = any(k is not None or t.startswith("### ") for k, t in parts) and not any(heading_form(t) for _, t in parts)

    # 번호의 절 위치를 반환한다. 없으면 양식 자리표시 절을 지우고 번호 순서 위치에 양식으로 새 절을 만든다.
    def find(n: int) -> int:
        nonlocal parts
        for i, (k, _) in enumerate(parts):
            if k == n:
                return i
        parts = [(k, t) for k, t in parts if not (k is None and t.startswith("### <"))]
        at = next((i for i, (k, _) in enumerate(parts) if k is not None and k > n), len(parts))
        heading = re.sub(r"(?m)^### .*$", lambda _: f"### {n}. {names.get(n, '요구사항')}", template_block(legacy), count=1)
        parts.insert(at, (n, heading))
        return at

    # 통합 구조 절의 제목 이름을 바꾼다.
    rename = lambda block, n, name: re.sub(r"(?m)^### .*$", lambda _: f"### {n}. {name}", block, count=1)
    # 안 설명은 `다.`로 끝나는 문장 단위로 나눠 결과 칸 목록으로 쓴다. 문장 안의 번호 뒤 마침표는 나누지 않는다.
    desc = lambda s: re.sub(r"^-?\s*안 \d+\. [^:]+: ", "", s.strip())
    sentences = lambda s: [p for p in re.split(r"(?<=다\.)\s+", desc(s)) if p.strip()]

    for op in ops:
        n = op[1]
        if op[0] == "req" and not legacy:
            # 통합 구조는 기대 결과 칸만 hook이 쓰고 제목 이름을 확정한 검토 이름으로 바꾼다. 복사 대상이 있으면 문제 칸을 옮긴다.
            source = next((t for k, t in parts if len(op) > 4 and op[4] is not None and k == op[4]), None)
            i = find(n)
            block = rename(parts[i][1], n, names.get(n, "요구사항"))
            if source is not None:
                for name in ("사용자 확인 시각", "문제"):
                    block = set_lines(block, name, text_of(source, name).split("\n"))
            parts[i] = (n, set_lines(block, "기대 결과", [f"- {s}" for s in sentences(op[2])]))
        elif op[0] == "req":
            i = find(n)
            # 결과 칸이 양식 자리표시 값이거나 이전 안 설명 그대로면 새 안 설명으로 채운다.
            # 결과 칸은 hook만 쓸 수 있어 비워 두면 구현 배정이 막힌다.
            old = desc(text_of(parts[i][1], "기대 결과"))
            block = set_lines(parts[i][1], "기대 결과", [f"  - {op[2]}"])
            # 결과 칸은 목록 줄을 이어 비교하므로 이전 안 설명을 문장으로 나눠 쓴 칸도 이전 값으로 본다.
            joined = lambda b: " ".join(re.sub(r"^\s*-\s*", "", l) for l in text_of(b, "결과").splitlines() if l.strip())
            # 자리표시 표기가 남은 결과 칸은 채움 검사를 통과하지 못하므로 새 안 설명으로 다시 쓴다.
            if joined(block) in (joined(template_block(True)), old) or re.search(r"<[^<>\n]+>", joined(block)):
                block = set_lines(block, "결과", [f"  - {s}" for s in sentences(op[2])])
            # 확정한 검토 질문의 문제 번호를 문제 칸에 쓴다.
            if len(op) > 3 and op[3]:
                block = re.sub(r"(?m)^- 문제:.*$", lambda _: f"- 문제: {op[3]}", block, count=1)
            parts[i] = (parts[i][0], block)
        elif op[0] == "problem":
            # 기존 구조는 문제 정의를 경과 문서에 쓰므로 건너뛴다. 새 절의 제목 이름은 문제 원문으로 시작하고 검토 확정 때 검토 이름으로 바뀐다.
            if legacy or op[2].startswith(("추가 합의: ", "교체: ")) and n not in {k for k, _ in parts}:
                continue
            names = {n: op[2].split(". ", 1)[-1], **names}
            i = find(n)
            parts[i] = (n, problem_block(parts[i][1], n, op[2], op[3]))
        elif legacy:
            # 절을 `### 요구사항 <번호>. 폐기`와 기대 결과 `폐기`만 남긴다.
            parts[find(n)] = (n, f"### 요구사항 {n}. 폐기\n\n- 기대 결과:\n  - 폐기\n\n")
        else:
            i = find(n)
            parts[i] = (n, rename(parts[i][1], n, "폐기"))

    # 새 절이 들어간 자리에서 절 사이 빈 줄을 맞추고 이어 붙인다.
    texts = [t if k is None or t.endswith("\n\n") else t.rstrip("\n") + "\n\n" for k, t in parts[:-1]]
    return "".join(texts + [parts[-1][1]] if parts else [])


# 통합 구조 절에 문제 정의 확정 답 한 건을 쓴 절을 반환한다. line은 `- 문제 <번호>. <원문>`, `- 문제 <번호>. 폐기: <사유>`,
# `추가 합의: <내용>`, `교체: <새 원문>` 중 하나이고 stamp는 답 시각이다. 같은 원문의 재확정은 이전 확인 시각을 유지한다.
def problem_block(block: str, n: int, line: str, stamp: str) -> str:
    rename = lambda b, name: re.sub(r"(?m)^### .*$", lambda _: f"### {n}. {name}", b, count=1)
    current = text_of(block, "문제").split("\n")
    first = current[0] if current and not re.search(r"<[^<>\n]+>", current[0]) else ""
    before = text_of(block, "사용자 확인 시각")
    before = before if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", before) else ""
    if line.startswith("추가 합의: "):
        # 원문과 기존 하위 줄은 바꾸지 않고 같은 합의가 없을 때만 하위 줄을 덧붙인다.
        if first and not any(l.startswith(f"- {line} (") for l in current):
            block = set_lines(block, "문제", current + [f"- {line} (사용자 확인 시각: {stamp})"])
        return block
    if line.startswith("교체: "):
        # 번호를 유지한 채 새 원문과 시각으로 바꾸고 이전 원문과 이전 시각은 이전 확정 줄로 남긴다.
        if first and first != line[4:]:
            block = set_lines(block, "문제", [line[4:], *current[1:], f"- 이전 확정: {first} (사용자 확인 시각: {before or '기록 없음'})"])
            block = set_lines(block, "사용자 확인 시각", [stamp])
        return block
    text = line.split(". ", 1)[1]
    if text.startswith("폐기: "):
        block = rename(block, "폐기")
    elif re.search(r"<[^<>\n]+>", block.split("\n", 1)[0]) or block.split("\n", 1)[0].endswith(". 요구사항"):
        # 자리표시 제목이 남은 절은 문제 원문을 제목 이름으로 쓴다.
        block = rename(block, text)
    block = set_lines(block, "사용자 확인 시각", [before if first == text and before else stamp])
    return set_lines(block, "문제", [text] + (current[1:] if first == text else []))
