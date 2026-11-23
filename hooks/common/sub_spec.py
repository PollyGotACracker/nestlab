"""명세 문서 요구사항 절의 hook 관리 칸을 읽고 쓰는 공용 모듈이다.

호출자:
- hooks/check_workflow_response.py(확정 질문의 확정 답을 사용자 확인 시각, 문제, 목표, 기대 결과, 한계, 잠재 위험 칸에 기록)
- hooks/check_tool_use/sub_scope.py(에이전트의 hook 관리 칸 직접 수정 잠금)
- hooks/common/sub_task_docs.py(구현 승인 요청 본문 생성)
입력: 명세 문서의 `요구사항 목록` 절 본문 또는 명세 문서 전문
반환: 칸 값 dict 또는 칸을 바꾼 절 본문
한계: `###` 제목이 코드 블록 안에 있으면 요구사항 절 경계로 오인한다.
기존 구조(`- 칸:` 줄) 절은 기존 구조로 읽고 쓴다. 통합 구조는 `#### 칸` 제목 아래 본문을 칸 값으로 쓴다.
사용자 확인 시각, 기대 결과, 결과 칸은 항상 hook만 쓰고, 문제, 목표, 한계, 잠재 위험 칸은 사용자 확인 시각이 기록된 뒤부터 hook만 쓴다.
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
LOCKED = ("사용자 확인 시각", "기대 결과", "결과")
# 사용자 확인 시각이 기록된 절에서 추가로 잠기는 칸이다.
CONFIRMED_LOCKED = ("문제", "목표", "한계", "잠재 위험")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


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


# 한 절에서 hook만 쓰는 칸의 값을 이은 문자열이다. 사용자 확인 시각이 기록된 절은 확정으로 잠기는 칸을 더한다.
def locked_text(block: str) -> str:
    if STAMP.fullmatch(text_of(block, "사용자 확인 시각")):
        return "\n--\n".join(text_of(block, name) for name in LOCKED + CONFIRMED_LOCKED)
    return "\n--\n".join(text_of(block, name) for name in LOCKED)


# 명세 전문 또는 요구사항 목록 절 본문에서 번호별 hook 관리 칸 값이다.
def cells(body: str) -> dict[int, str]:
    return {k: locked_text(t) for k, t in blocks(body) if k is not None}


# 양식 자리표시 절의 hook 관리 칸 값이다. legacy이면 기존 구조 절의 값이다.
def blank_cells(legacy: bool = False) -> str:
    return locked_text(template_block(legacy))


# 안 설명 앞의 목록 표식과 `안 <번호>. <이름>: ` 접두를 뗀 문자열이다.
def desc(text: str) -> str:
    return re.sub(r"^\s*(?:[-*]\s+)?(?:안 \d+\. [^:]+: )?", "", text.strip())


QUOTE_PAIRS = {"'": "'", '"': '"', "‘": "’", "“": "”"}


# 안 설명을 `다.`로 끝나는 문장 단위로 나눈 목록이다. 따옴표 안과 문장 안의 번호 뒤 마침표는 나누지 않으며, 닫히지 않은 따옴표 뒤는 나누지 않는다.
def sentences(text: str) -> list[str]:
    body, parts, start, closer = desc(text), [], 0, ""
    for i, ch in enumerate(body):
        if closer:
            closer = "" if ch == closer else closer
        elif ch in QUOTE_PAIRS:
            closer = QUOTE_PAIRS[ch]
        elif ch == "." and body[max(i - 1, 0)] == "다" and body[i + 1:i + 2].isspace():
            parts.append(body[start:i + 1])
            start = i + 1
    parts.append(body[start:])
    return [p.strip() for p in parts if p.strip()]


# 값 한 줄을 목록 줄로 바꾼다. 이미 목록 표식이 있으면 그대로 둔다.
def bullet(value: str) -> str:
    value = value.strip()
    return value if value.startswith("- ") else f"- {value}"


# 요구사항 목록 절 본문에 ops를 순서대로 적용한 본문을 반환한다. names는 새 절 제목에 쓸 번호별 이름이다.
# ops:
# - ("confirm", 번호, 이름, 목표, 확정 기대 결과, 한계, 잠재 위험, 확인 시각[, 문제]): 확정안을 사용자 확인 시각, 목표, 기대 결과, 한계, 잠재 위험 칸에 쓰고, 문제가 있으면 문제 칸에도 쓴다.
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

    for op in ops:
        n = op[1]
        if op[0] == "confirm" and not legacy:
            # 같은 확정안이 이미 기록돼 있으면 확인 시각을 유지하고 다시 쓰지 않는다.
            _, _, name, goal, expected, limit, risk, stamp, *problem = op
            i = find(n)
            block = rename(parts[i][1], n, name)
            cells_ = {"목표": [bullet(goal)], "기대 결과": [f"- {s}" for s in sentences(expected)],
                      "한계": [bullet(limit)], "잠재 위험": [bullet(risk)]}
            # 문제 줄이 있으면 문제 칸에 한 문장으로 기록한다.
            if problem and problem[0]:
                cells_["문제"] = [problem[0].strip()]
            same = all(text_of(block, c) == "\n".join(v) for c, v in cells_.items()) and STAMP.fullmatch(text_of(block, "사용자 확인 시각"))
            for cell, lines in cells_.items():
                block = set_lines(block, cell, lines)
            parts[i] = (n, block if same else set_lines(block, "사용자 확인 시각", [stamp]))
        elif op[0] == "confirm":
            # 기존 구조는 기대 결과 칸과 결과 칸에 확정 기대 결과를 쓴다.
            i = find(n)
            block = set_lines(parts[i][1], "기대 결과", [f"  - {op[4]}"])
            parts[i] = (n, set_lines(block, "결과", [f"  - {s}" for s in sentences(op[4])]))
        elif legacy:
            # 절을 `### 요구사항 <번호>. 폐기`와 기대 결과 `폐기`만 남긴다.
            parts[find(n)] = (n, f"### 요구사항 {n}. 폐기\n\n- 기대 결과:\n  - 폐기\n\n")
        else:
            i = find(n)
            parts[i] = (n, rename(parts[i][1], n, "폐기"))

    # 새 절이 들어간 자리에서 절 사이 빈 줄을 맞추고 이어 붙인다.
    texts = [t if k is None or t.endswith("\n\n") else t.rstrip("\n") + "\n\n" for k, t in parts[:-1]]
    return "".join(texts + [parts[-1][1]] if parts else [])
