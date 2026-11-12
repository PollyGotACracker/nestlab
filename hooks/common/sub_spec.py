"""명세 문서 요구사항 절의 결과 칸과 기대 결과 칸을 읽고 쓰는 공용 모듈이다.

호출자:
- hooks/check_workflow_response.py(질문 확정 답을 기대 결과 칸에 기록, 폐기 답을 `### 요구사항 <번호>. 폐기` 절로 기록)
- hooks/check_tool_use/sub_scope.py(에이전트의 결과 칸과 기대 결과 칸 직접 수정 잠금)
입력: 명세 문서의 `요구사항 목록` 절 본문 또는 명세 문서 전문
반환: 칸 값 dict 또는 칸을 바꾼 절 본문
한계: `###` 제목이 코드 블록 안에 있으면 요구사항 절 경계로 오인한다.
"""
from __future__ import annotations

import re

from sub_docs import template_section

TEMPLATE = "spec"
BLOCK = re.compile(r"(?m)^(?=### )")
NUMBER = re.compile(r"### (?:요구사항 )?(\d+)\. ")
PLACEHOLDER = {"기대 결과": "  - <판정 기준>"}


# 절 본문을 (번호, 절 본문) 목록으로 나눈다. 번호가 없는 머리말과 양식 자리표시 절의 번호는 None이다.
def blocks(body: str) -> list[tuple[int | None, str]]:
    out = []
    for part in BLOCK.split(body):
        m = NUMBER.match(part)
        if part:
            out.append((int(m[1]) if m else None, part))
    return out


# 절에서 `- name:` 칸의 (줄 목록, 본문 시작, 본문 끝)이다. 칸이 없으면 시작과 끝이 -1이다.
def span(block: str, name: str) -> tuple[list[str], int, int]:
    lines = block.split("\n")
    start = next((i for i, l in enumerate(lines) if l.rstrip() == f"- {name}:"), None)
    if start is None:
        return lines, -1, -1
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][:1] == " "):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return lines, start + 1, end


# 칸 본문을 `  - ` 항목 단위의 줄 목록으로 반환한다. 들여쓴 하위 줄은 앞 항목에 속한다.
def items(block: str, name: str) -> list[list[str]]:
    lines, start, end = span(block, name)
    out: list[list[str]] = []
    for l in lines[start:end] if start >= 0 else []:
        if re.match(r"  - ", l) or not out:
            out.append([l])
        else:
            out[-1].append(l)
    return out


# 칸 본문을 항목 목록으로 바꾼 절을 반환한다. 항목이 없으면 양식 자리표시 줄을 쓴다. 칸이 없으면 절 그대로이다.
def set_items(block: str, name: str, new: list[list[str]]) -> str:
    lines, start, end = span(block, name)
    if start < 0:
        return block
    body = [l for item in new for l in item] or [PLACEHOLDER[name]]
    return "\n".join(lines[:start] + body + lines[end:])


# 칸 본문을 줄 끝 공백 없이 이은 문자열이다.
def text_of(block: str, name: str) -> str:
    lines, start, end = span(block, name)
    return "\n".join(l.rstrip() for l in lines[start:end]).strip() if start >= 0 else ""


# 명세 양식의 요구사항 절 자리표시 본문이다. 양식을 읽지 못하면 최소 절을 쓴다.
def template_block() -> str:
    tpl = template_section(TEMPLATE, "요구사항 목록")
    if "### " in tpl:
        return tpl[tpl.index("### "):].rstrip("\n") + "\n\n"
    return "### <번호>. <요구사항 이름>\n\n- 결과:\n  - <목표를 달성했을 때 기대되는 결과>\n- 기대 결과:\n  - <판정 기준>\n\n"


# 한 절에서 hook만 쓰는 두 칸(결과, 기대 결과)의 값을 이은 문자열이다.
def locked_text(block: str) -> str:
    return text_of(block, "결과") + "\n--\n" + text_of(block, "기대 결과")


# 명세 전문 또는 요구사항 목록 절 본문에서 번호별 결과 칸과 기대 결과 칸 값이다.
def cells(body: str) -> dict[int, str]:
    return {k: locked_text(t) for k, t in blocks(body) if k is not None}


# 양식 자리표시 절의 결과 칸과 기대 결과 칸 값이다.
def blank_cells() -> str:
    return locked_text(template_block())


# 요구사항 목록 절 본문에 ops를 순서대로 적용한 본문을 반환한다.
# ops: ("req", 번호, 문장), ("drop", 번호, 사유)이다. 사유는 쓰지 않는다. names는 새 절 제목에 쓸 번호별 이름이다.
def apply(body: str, ops: list[tuple], names: dict[int, str]) -> str:
    parts = blocks(body)

    # 번호의 절 위치를 반환한다. 없으면 양식 자리표시 절을 지우고 번호 순서 위치에 양식으로 새 절을 만든다.
    def find(n: int) -> int:
        nonlocal parts
        for i, (k, _) in enumerate(parts):
            if k == n:
                return i
        parts = [(k, t) for k, t in parts if not (k is None and t.startswith("### <"))]
        at = next((i for i, (k, _) in enumerate(parts) if k is not None and k > n), len(parts))
        heading = re.sub(r"(?m)^### .*$", lambda _: f"### {n}. {names.get(n, '요구사항')}", template_block(), count=1)
        parts.insert(at, (n, heading))
        return at

    for op in ops:
        n = op[1]
        if op[0] == "req":
            i = find(n)
            # 결과 칸이 양식 자리표시 값이거나 이전 안 설명 그대로면 새 안 설명으로 채운다.
            # 결과 칸은 hook만 쓸 수 있어 비워 두면 구현 배정이 막힌다.
            desc = lambda s: re.sub(r"^-?\s*안 \d+\. [^:]+: ", "", s.strip())
            old = desc(text_of(parts[i][1], "기대 결과"))
            block = set_items(parts[i][1], "기대 결과", [[f"  - {op[2]}"]])
            if text_of(block, "결과").removeprefix("- ") in (text_of(template_block(), "결과").removeprefix("- "), old):
                # 안 설명은 문장 단위로 나눠 결과 칸 목록으로 쓴다.
                sentences = [s for s in re.split(r"(?<=[.!?])\s+", desc(op[2])) if s.strip()]
                block = set_items(block, "결과", [[f"  - {s}"] for s in sentences])
            # 확정한 검토 질문의 문제 번호를 문제 칸에 쓴다.
            if len(op) > 3 and op[3]:
                block = re.sub(r"(?m)^- 문제:.*$", lambda _: f"- 문제: {op[3]}", block, count=1)
            parts[i] = (parts[i][0], block)
        else:
            # 절을 `### 요구사항 <번호>. 폐기`와 기대 결과 `폐기`만 남긴다.
            parts[find(n)] = (n, f"### 요구사항 {n}. 폐기\n\n- 기대 결과:\n  - 폐기\n\n")

    # 새 절이 들어간 자리에서 절 사이 빈 줄을 맞추고 이어 붙인다.
    texts = [t if k is None or t.endswith("\n\n") else t.rstrip("\n") + "\n\n" for k, t in parts[:-1]]
    return "".join(texts + [parts[-1][1]] if parts else [])
