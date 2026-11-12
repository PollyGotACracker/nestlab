"""SubagentStop hook: researcher 최종 결과를 조사 문서의 조사 결과 절에, reviewer 반증 결과를 반증 문서의 회차 결과 절에 쓴다.
보고는 결과 양식의 블록과 칸을 모두 채웠을 때만 코드 블록 없이 절 본문으로 삽입한다.
조사 결과의 주장 목록은 반증 문서의 주장 목록 절에 쓴다.

호출자: SubagentStop hook 설정(설정 원본), hooks/save_handback_report.py와 tools/run_workflow.py(write_result)
반환:
- stdout에 `{"decision": "block", "reason": ...}` JSON을 출력하거나 아무것도 출력하지 않는다.
- write_result는 차단 사유 문자열 또는 저장했거나 건너뛴 경우 None이다.
입력: SubagentStop hook 입력 JSON을 stdin으로 받는다(agent_id, transcript_path, session_id).
한계: Codex 하위 thread의 기록 위치는 rollout 파일명에 의존한다. 칸 검사는 빈 값과 자리표시자만 판정하고 내용의 정확성은 판정하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 공용 모듈 폴더를 불러온다. sub_form은 호출 시점에 불러온다.
HOOKS = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS / "common"))
sys.path.insert(0, str(HOOKS / "check_tool_use"))
sys.path.insert(0, str(HOOKS / "check_workflow_response"))

from sub_call import VERDICT, refute_mode
from sub_docs import doc, loop_count, saved_report, sibling, strip_task_comments, task_id, template_text
from sub_role import codex_rollout, director_assignment, first_prompt, main_session, parent_thread, resolve_role
from sub_session import agent_transcript, handback_report
from sub_task_docs import STAGE_LINE, document_stage_reason

MAX_ROUNDS = 2
CLAIM_LINE = re.compile(r"^\s*[-*]\s*주장\s*\d+\s*[:：].+$", re.M)
REQUESTED = re.compile(r"^item: (research \d+)\s*$", re.M)
# 결과 블록 제목 줄이다. 조사는 `### 조사 <번호>. <항목명> 결과`, 반증은 `### <n>회차 반증 결과`이다.
RESEARCH_BLOCK = re.compile(r"(?m)^### 조사 (\d+)\. .+ 결과[ \t]*$")
REFUTE_BLOCK = re.compile(r"(?m)^### .*회차 반증 결과[ \t]*$")
RESULT_SECTION = {"research": "조사 결과", "refute": "회차 결과"}


# 서브에이전트 기록 경로를 반환한다. 찾지 못하면 빈 문자열이다.
def transcript(data: dict, runner: str) -> str:
    path = agent_transcript(data)
    if path:
        return path
    return codex_rollout(data.get("agent_id")) if runner == "codex" else ""


# 하위 요청, Codex 부모 요청 순으로 작업 ID를 찾는다.
def find_task(data: dict, runner: str, path: str, prompt: str) -> str | None:
    # 하위 요청에 작업 문서 경로가 있으면 그 작업 ID를 쓴다.
    tid = task_id(prompt)
    if tid:
        return tid

    # 요청에 없으면 Codex는 부모 요청에서 찾는다.
    if runner == "codex":
        return task_id(first_prompt(codex_rollout(parent_thread(path))))
    return None


# 본문 안의 가장 긴 backtick 연속보다 1개 긴 펜스이다. 최소 3개이다.
def fence_for(body: str) -> str:
    longest = max((len(run) for run in re.findall(r"`{3,}", body)), default=2)
    return "`" * max(3, longest + 1)


# 코드 블록 밖에서 title 제목(수준 level)의 절 범위를 (시작 줄, 끝 줄)로 반환한다. 끝은 같거나 높은 수준의 다음 제목 줄이다. 절이 없으면 None이다.
def section_bounds(lines: list[str], title: str, level: int = 2) -> tuple[int, int] | None:
    start = next((i for i, line in enumerate(lines) if line.strip() == "#" * level + " " + title), None)
    if start is None:
        return None
    end, fence = len(lines), ""
    for i in range(start + 1, len(lines)):
        run = re.match(r"`{3,}|~{3,}", lines[i])
        if run and not fence:
            fence = run.group()
        elif run and lines[i].startswith(fence) and not lines[i].strip().strip(fence[0]):
            fence = ""
        elif not fence and re.match(rf"#{{1,{level}}} ", lines[i]):
            end = i
            break
    return start, end


# slot 이름의 결과 절 본문이다. 절이 없으면 빈 문자열이다. `<n>회차 반증 결과`는 3수준, 나머지는 2수준 제목이다.
def saved_body(text: str, slot: str) -> str:
    lines = text.splitlines()
    found = section_bounds(lines, slot, 3 if slot.endswith("반증 결과") else 2)
    return "\n".join(lines[found[0] + 1:found[1]]).strip() if found else ""


# 결과 블록의 제목 줄을 뺀 본문이다. 저장한 보고와 제출한 보고를 제목 번호와 무관하게 비교할 때 쓴다.
def without_heading(text: str) -> str:
    return re.sub(r"(?m)^### .*결과[ \t]*\n?", "", text).strip()


# 결과 양식(조사 문서 `조사 결과`, 반증 문서 `회차 결과` 절)에서 보고 형식으로 쓰는 부분이다.
def result_template(kind: str) -> str:
    text = template_text("research" if kind == "research" else "refute")
    start = re.search(rf"(?m)^## {RESULT_SECTION[kind]}[ \t]*$", text)
    return text[start.start():] if start else ""


# 보고가 결과 양식의 모든 블록과 칸을 채웠는지 확인해 문제 목록을 반환한다. wanted는 결과가 필요한 조사 번호이다.
def result_gaps(kind: str, message: str, wanted: list[str]) -> list[str]:
    # sub_form은 사건 기록 모듈을 거쳐 이 모듈을 불러오므로 호출 시점에 불러온다.
    from sub_form import block_gaps
    gaps = block_gaps(result_template(kind), message)
    if kind == "research":
        # 결론 불가 보고는 후보 안 블록이 없어도 된다.
        if "결론 불가" in message:
            gaps = [g for g in gaps if not g.startswith("`안 ")]
        found = set(RESEARCH_BLOCK.findall(message))
        gaps += [f"조사 {n} 결과 블록이 없습니다" for n in wanted if n not in found]
        # 주장 목록 절은 줄이 하나 이상 있어야 한다.
        for part in re.finditer(r"(?ms)^#### 주장 목록[ \t]*\n(.*?)(?=^#{1,4} |\Z)", message):
            if not part[1].strip():
                gaps.append("주장 목록 칸이 비었습니다")
    else:
        for part in re.finditer(r"(?ms)^#### 주장 판정[ \t]*\n(.*?)(?=^#{1,4} |\Z)", message):
            if not part[1].strip():
                gaps.append("주장 판정 칸이 비었습니다")
    return gaps


# 칸 검사 결과 문제가 있으면 재제출을 요구하는 ValueError를 던진다.
def require_filled(gaps: list[str]) -> None:
    if gaps:
        raise ValueError("결과 양식의 칸을 채워 다시 제출하십시오. 누락: " + "; ".join(gaps[:8]))


# 결과 절 안의 양식 자리표시자(`<`가 든 `###` 제목) 블록을 지운다.
def drop_placeholder_blocks(text: str, kind: str) -> str:
    lines = text.splitlines()
    found = section_bounds(lines, RESULT_SECTION[kind])
    if not found:
        return text
    out, skip = lines[:found[0] + 1], False
    for line in lines[found[0] + 1:found[1]]:
        if line.startswith("### "):
            skip = "<" in line
        if not skip:
            out.append(line)
    out += lines[found[1]:]
    return "\n".join(out) + "\n"


# 결과 절 끝에 (조사 번호, 블록) 목록을 덧붙인다. 같은 번호의 기존 조사 결과 블록은 새 블록으로 바꾼다. 절이 없으면 문서 끝에 절을 만든다.
def insert_result(text: str, kind: str, blocks: list[tuple[str, str]]) -> str:
    text = drop_placeholder_blocks(text, kind)
    title = RESULT_SECTION[kind]
    lines = text.splitlines()
    found = section_bounds(lines, title)
    if not found:
        return text.rstrip("\n") + f"\n\n## {title}\n" + "".join(f"\n{block.strip()}\n" for _, block in blocks)

    # 절 본문을 이어 붙이며 같은 번호의 기존 블록은 지운다.
    body = "\n".join(lines[found[0] + 1:found[1]]).strip("\n")
    for key, block in blocks:
        if key:
            body = re.sub(rf"(?ms)^### 조사 {key}\. .+ 결과[ \t]*\n.*?(?=^### |\Z)", "", body).strip("\n")
        body = (body + "\n\n" if body else "") + block.strip("\n")
    tail = "\n".join(lines[found[1]:])
    return "\n".join(lines[:found[0] + 1]) + "\n\n" + body + "\n" + ("\n" + tail + "\n" if tail else "")


# 보고에서 조사 결과 블록을 (조사 번호, 블록 본문) 목록으로 나눈다. 첫 블록 앞의 글은 버린다.
def research_blocks(message: str) -> list[tuple[str, str]]:
    marks = list(RESEARCH_BLOCK.finditer(message))
    return [(m[1], message[m.start():(marks[i + 1].start() if i + 1 < len(marks) else len(message))].strip("\n"))
            for i, m in enumerate(marks)]


# 반증 보고의 첫 결과 블록 제목을 이번 회차 번호로 바꾼 본문이다. 블록 앞의 글은 버린다.
def refute_block(message: str, n: int) -> str:
    found = REFUTE_BLOCK.search(message)
    return f"### {n}회차 반증 결과" + message[found.end():].rstrip("\n") if found else message


# 반증 문서 양식의 `<회차>` 표 행을 지운다.
def drop_placeholder_row(text: str) -> str:
    return re.sub(r"(?m)^\|\s*<회차>[^\n]*\n?", "", text)


# `조사 및 반증 루프` 표에 이번 회차 행(회차, 반증 건수, 정지 여부)을 추가한다. 표 절이 없으면 그대로 반환한다.
def add_loop_row(text: str, message: str) -> str:
    # 회차 번호와 반증 건수로 행을 만든다. 회차는 루프 절 안의 행만 센다.
    n = loop_count(text) + 1
    refuted = sum(1 for line in message.splitlines() if (m := VERDICT.match(line)) and m.group(2) == "반증")
    stop = "정지(반증 0건)" if refuted == 0 else f"정지({MAX_ROUNDS}회 도달)" if n >= MAX_ROUNDS else "계속"
    row = f"| {n} | {refuted} | {stop} |"

    # 루프 절의 끝 위치를 찾는다.
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip() == "## 조사 및 반증 루프"), None)
    if start is None:
        return text
    end = start + 1
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1

    # 표의 마지막 줄 뒤에 행을 넣는다.
    last = max((i for i in range(start, end) if lines[i].strip().startswith("|")), default=end - 1)
    lines.insert(last + 1, row)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


# 반증 문서의 `주장 목록` 절 본문을 조사 결과의 주장 줄로 바꾼다. 주장 줄이 있고 반증 문서가 없으면 양식으로 만든다.
def write_claims(refute_doc: Path, message: str) -> None:
    claims = [re.sub(r"^\s*[*-]\s*", "- ", line).rstrip() for line in CLAIM_LINE.findall(message)]
    if not claims:
        return

    # 반증 문서가 없으면 양식에 작업 ID를 채워 만든다.
    if not refute_doc.is_file():
        template = template_text("refute")
        if not template:
            return
        refute_doc.write_text(template.replace("<작업-id>", refute_doc.parent.name) + "\n", encoding="utf-8")
    text = refute_doc.read_text(encoding="utf-8")

    # 기존 주장 줄은 남기고 새 주장 번호는 기존 최대 번호 뒤로 이어 붙인다. 양식 자리표시 줄은 지운다.
    number = lambda line: int(re.search(r"주장\s*(\d+)", line)[1])
    found = re.search(r"(^## 주장 목록[^\n]*\n)(.*?)(?=^## |\Z)", text, re.M | re.S)
    kept = {number(l): l.rstrip() for l in (found.group(2) if found else "").splitlines() if re.match(r"^- 주장 \d+\s*[:：]", l)}
    # 주장 저장을 재시도해도 같은 주장의 번호와 본문을 보존한다.
    existing = {re.sub(r"주장\s*\d+", "주장", line, count=1) for line in kept.values()}
    claims = [line for line in claims if re.sub(r"주장\s*\d+", "주장", line, count=1) not in existing]
    if not claims:
        return
    shift = max(kept) - min(number(c) for c in claims) + 1 if kept else 0
    added = {number(c) + shift: re.sub(r"주장\s*\d+", f"주장 {number(c) + shift}", c, count=1) for c in claims}
    merged = {**kept, **added}
    body = "\n".join(merged[k] for k in sorted(merged))
    replaced = re.sub(r"(^## 주장 목록[^\n]*\n)(.*?)(?=^## |\Z)", lambda m: f"{m.group(1)}\n{body}\n\n", text, count=1, flags=re.M | re.S)
    if replaced != text:
        refute_doc.write_text(replaced, encoding="utf-8")
    strip_task_comments(refute_doc)


# target 문서에 결과를 쓴다. 반증이면 target은 반증 문서이고, 조사이면 조사 문서이며 주장 목록을 반증 문서에 쓴다.
# 양식 칸이 비었거나 판정 형식이 어긋나면 문서를 바꾸지 않고 재제출 사유를 반환한다. 이미 기록한 반증 보고면 쓰지 않는다.
def write_result(target: Path, prompt: str, message: str, refute: bool) -> str | None:
    text = target.read_text(encoding="utf-8")
    status = sibling(target, "state")
    if status.is_file():
        match = STAGE_LINE.search(status.read_text(encoding="utf-8-sig"))
        if match and (reason := document_stage_reason("refute" if refute else "research", match[1].strip())):
            return reason

    # 보고가 결과 양식의 블록과 칸을 채웠는지, 반증 판정 형식을 지켰는지 확인한다.
    from sub_document import Document
    from sub_gate import check_refutation, research_items
    try:
        if refute:
            require_filled(result_gaps("refute", message, []))
            check_refutation(message)
            # 주장 목록 번호 중 앞 회차와 이번 보고 어디에도 판정이 없는 번호를 찾는다.
            listed = re.search(r"^## 주장 목록[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
            claims = {int(n) for n in re.findall(r"^- 주장 (\d+)\s*[:：]", listed.group(1) if listed else "", re.M)}
            judged = {int(m.group(1)) for line in (text + "\n" + message).splitlines() if (m := VERDICT.match(line))}
            if missing := sorted(claims - judged):
                raise ValueError(f"주장 {', '.join(map(str, missing))} 판정이 없습니다. 해당 주장만 판정해 보고하십시오.")
        else:
            document = Document(text)
            items = research_items(document) if "조사 계획" in [t for _, _, t in document.headings] else []
            # 재조사 요청의 `item: research <번호>` 항목만 검사한다.
            asked = {n for line in REQUESTED.findall(prompt) for n in re.findall(r"research\s*(\d+)", line)}
            numbers = [i.split(". ", 1)[0].split()[-1] for i in items]
            require_filled(result_gaps("research", message, [n for n in numbers if not asked or n in asked]))
    except ValueError as error:
        return str(error)

    # 반증은 회차 결과 블록과 루프 행을, 조사는 항목별 결과 블록을 갱신한다.
    if refute:
        block = refute_block(message, loop_count(text) + 1)
        if without_heading(block) in text:  # 같은 보고로 다시 멈춘 경우는 이미 기록했다.
            return None
        text = add_loop_row(insert_result(drop_placeholder_row(text), "refute", [("", block)]), message)
    else:
        text = insert_result(text, "research", research_blocks(message))
    target.write_text(text, encoding="utf-8")
    strip_task_comments(target)
    if not refute:
        write_claims(sibling(target, "refute"), message)
    return None


# block 사유를 반환한다. 저장했거나 건너뛰면 None이다.
def save(data: dict, runner: str) -> str | None:
    # researcher 및 reviewer만 대상으로 한다.
    role = resolve_role(data)
    if role not in ("researcher", "reviewer"):
        return
    # 메인 세션은 자기 기록의 첫 요청을, 하위 실행은 director가 배정한 기록을 대상으로 한다.
    main = main_session(data) and not agent_transcript(data)
    path = (data.get("transcript_path") or "") if main else transcript(data, runner)
    if not main and not director_assignment(data, path):
        return

    # SubagentHandback으로 넘긴 보고가 최종 결과이다. 뒤따르는 후속 응답으로 덮어쓰지 않는다.
    report = handback_report(path)
    message = report or (data.get("last_assistant_message") or "").strip()

    # 반증 여부를 가리고 저장하지 않을 경우를 건너뛴다.
    prompt = first_prompt(path) or "\n".join(str(v) for v in (data.get("tool_input") or {}).values())
    refute = role == "reviewer" and refute_mode(prompt)
    if role == "reviewer" and not refute:
        return
    if not message:
        return "결과 보고가 비어 있습니다. 조사 또는 반증 결과를 제출하십시오."
    # Claude는 Handback 없이 끝난 응답을 하네스가 되돌려 다시 보고하게 한다. 그 응답을 저장하면 회차가 둘이 된다.
    if refute and runner == "claude" and not report and not main:
        return
    if saved_report(message, data.get("cwd"), refute):  # 이미 저장해 경로로 바뀐 보고는 덮어쓰지 않는다.
        return

    # 조사 결과는 조사 문서에, 반증 결과는 반증 문서에 쓴다.
    tid = find_task(data, runner, path, prompt)
    target = doc(tid, "refute" if refute else "research", data.get("cwd")) if tid else None
    if not target:
        return "작업 ID를 찾지 못해 결과를 저장하지 못했습니다. 배정 프롬프트의 input 줄에 작업 문서 경로가 있는지 확인하고 보고를 다시 제출하십시오."
    if not target.is_file():
        return f"결과를 저장할 문서가 없습니다: {target}"
    return write_result(target, prompt, message, refute)


# stdin의 hook 입력으로 결과를 저장하고 필요하면 block JSON을 출력한다. 항상 0을 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞추고 인자를 읽는다.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    runner = parser.parse_args().runner

    # 저장 오류는 stderr에 남기고 사유와 함께 종료를 보류한다.
    try:
        reason = save(json.load(sys.stdin), runner)
        if reason:
            print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    except Exception as error:
        print(f"결과 원문 저장 오류: {error!r}", file=sys.stderr)
        print(json.dumps({"decision": "block", "reason": f"결과 원문 저장 중 {type(error).__name__} 오류가 발생했습니다. 보고를 다시 제출하십시오."}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
