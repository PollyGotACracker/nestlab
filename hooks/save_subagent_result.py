"""SubagentStop hook: researcher 최종 결과를 조사 문서에, reviewer 반증 결과를 반증 문서의 기존 칸에 원문 그대로 쓴다.
조사 결과의 주장 목록은 반증 문서의 주장 목록 절에 쓴다.

호출자: SubagentStop hook 설정(shared/settings), hooks/save_handback_report.py와 tools/run_workflow.py(write_result)
반환:
- stdout에 `{"decision": "block", "reason": ...}` JSON을 출력하거나 아무것도 출력하지 않는다.
- write_result는 차단 사유 문자열 또는 저장했거나 건너뛴 경우 None이다.
입력: SubagentStop hook 입력 JSON을 stdin으로 받는다(agent_id, transcript_path, session_id).
한계: Codex 하위 thread의 기록 위치는 rollout 파일명에 의존한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))

from sub_call import VERDICT, refute_mode
from sub_docs import doc, loop_count, saved_report, sibling, task_id
from sub_role import codex_rollout, first_prompt, parent_thread, resolve_role
from sub_session import agent_transcript, handback_report

MAX_ROUNDS = 2
CLAIM_LINE = re.compile(r"^\s*[-*]\s*주장\s*\d+\s*[:：].+$", re.M)


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


# 제목 아래 코드 블록 본문이다. 없으면 빈 문자열이다.
def raw_body(text: str, title: str) -> str:
    m = re.search(rf"^#{{2,3}} {re.escape(title)}[^\n]*\n+(`{{3,}})[^\n]*\n(.*?)\n\1[ \t]*$", text, re.M | re.S)
    return m.group(2).strip() if m else ""


# 제목 아래 코드 블록을 펜스까지 통째로 교체한다. 펜스 길이는 본문에 맞춘다.
def replace_block(text: str, title: str, body: str) -> str:
    pattern = re.compile(rf"(^#{{2,3}} {re.escape(title)}[^\n]*\n+)(`{{3,}})[^\n]*\n.*?\n\2[ \t]*$", re.M | re.S)
    fence = fence_for(body)
    return pattern.sub(lambda m: f"{m.group(1)}{fence}\n{body}\n{fence}", text, count=1)


# title 절 끝에 블록을 덧붙인다. 절이 없으면 문서 끝에 절을 만든다.
def insert_blocks(text: str, blocks: str, title: str = "조사 단계 원문") -> str:
    # 절의 시작 줄을 찾는다. 절이 없으면 문서 끝에 만든다.
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.strip() == f"## {title}"), None)
    if start is None:
        return text.rstrip("\n") + f"\n\n## {title}\n" + blocks

    # 코드 블록 밖의 다음 `## ` 제목 앞까지를 절 끝으로 삼는다.
    end, fence = len(lines), 0
    for i in range(start + 1, len(lines)):
        run = re.match(r"`{3,}", lines[i])
        if run and not fence:
            fence = len(run.group())
        elif run and len(run.group()) >= fence and not lines[i].strip().strip("`"):
            fence = 0
        elif not fence and lines[i].startswith("## "):  # 코드 블록 안의 `## ` 줄은 절 경계가 아니다.
            end = i
            break

    # 절 끝에 블록을 끼워 넣는다.
    head = "".join(lines[:end]).rstrip("\n") + "\n"
    tail = "".join(lines[end:])
    return head + blocks + ("\n" + tail if tail else "")


# `회차 기록` 절 끝에 이번 회차의 반증 요청과 반증 원문 블록을 덧붙인다.
def add_round_blocks(text: str, request: str, message: str) -> str:
    n = loop_count(text) + 1
    blocks = "".join(f"\n### {n}회차 반증 {kind}\n\n{fence_for(body)}\n{body}\n{fence_for(body)}\n"
                     for kind, body in (("요청", request.strip()), ("원문", message)))
    return insert_blocks(text, blocks, "회차 기록")


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


# 반증 문서의 `주장 목록` 절 본문을 조사 결과의 주장 줄로 바꾼다. 반증 문서나 절이 없으면 쓰지 않는다.
def write_claims(refute_doc: Path, message: str) -> None:
    claims = [re.sub(r"^\s*[*-]\s*", "- ", line).rstrip() for line in CLAIM_LINE.findall(message)]
    if not claims or not refute_doc.is_file():
        return
    text = refute_doc.read_text(encoding="utf-8")
    body = "\n".join(claims)  # ponytail: 재조사는 목록 전체를 바꾼다. 항목별 병합은 재조사 범위가 겹칠 때 추가한다.
    replaced = re.sub(r"(^## 주장 목록[^\n]*\n)(.*?)(?=^## |\Z)", lambda m: f"{m.group(1)}\n{body}\n\n", text, count=1, flags=re.M | re.S)
    if replaced != text:
        refute_doc.write_text(replaced, encoding="utf-8")


# target 문서에 결과를 쓴다. 반증이면 target은 반증 문서이고, 조사이면 조사 문서이며 주장 목록을 반증 문서에 쓴다.
# 이미 기록한 반증 보고면 쓰지 않는다.
def write_result(target: Path, prompt: str, message: str, refute: bool) -> str | None:
    text = target.read_text(encoding="utf-8")

    # 보고가 조사 원문 또는 반증 판정 형식을 채웠는지 확인한다.
    from sub_workflow import Document, check_research_result, check_refutation, research_items
    try:
        if refute:
            check_refutation(message)
        else:
            document = Document(text)
            items = research_items(document) if "조사 계획" in [t for _, _, t in document.headings] else []
            check_research_result(message, items)
    except ValueError as error:
        return str(error)

    # 반증은 회차 블록과 루프 행을, 조사는 최종 조사 원문 칸을 갱신한다.
    if refute:
        if message in text:  # 같은 보고로 다시 멈춘 경우는 이미 기록했다.
            return None
        text = add_loop_row(add_round_blocks(text, prompt, message), message)
    else:
        # 재조사는 기존 원문을 지우지 않고 가로줄 뒤에 덧붙인다.
        old = raw_body(text, "최종 조사 원문")
        if old.endswith(message.strip()):  # 같은 보고로 다시 멈춘 경우는 이미 기록했다.
            return None
        body = message if old in ("", "기록 없음") or old.startswith("<") else f"{old}\n\n---\n\n{message}"
        replaced = replace_block(text, "최종 조사 원문", body)
        if replaced == text and not re.search(r"^#{2,3} 최종 조사 원문", text, re.M):
            fence = fence_for(message)
            replaced = insert_blocks(text, f"\n### 최종 조사 원문\n\n{fence}\n{message}\n{fence}\n")
        text = replaced
    target.write_text(text, encoding="utf-8")
    if not refute:
        write_claims(sibling(target, "refute"), message)
    return None


# block 사유를 반환한다. 저장했거나 건너뛰면 None이다.
def save(data: dict, runner: str) -> str | None:
    # researcher 및 reviewer만 대상으로 한다.
    role = resolve_role(data)
    if role not in ("researcher", "reviewer"):
        return
    path = transcript(data, runner)

    # SubagentHandback으로 넘긴 보고가 최종 결과이다. 뒤따르는 후속 응답으로 덮어쓰지 않는다.
    report = handback_report(path)
    message = report or (data.get("last_assistant_message") or "").strip()
    if not message:
        return

    # 반증 여부를 가리고 저장하지 않을 경우를 건너뛴다.
    prompt = first_prompt(path) or "\n".join(str(v) for v in (data.get("tool_input") or {}).values())
    refute = role == "reviewer" and refute_mode(prompt)
    # Claude는 Handback 없이 끝난 응답을 하네스가 되돌려 다시 보고하게 한다. 그 응답을 저장하면 회차가 둘이 된다.
    if refute and runner == "claude" and not report:
        return
    if saved_report(message, data.get("cwd"), refute):  # 이미 저장해 경로로 바뀐 보고는 덮어쓰지 않는다.
        return
    if role == "reviewer" and not refute:
        return

    # 조사 결과는 조사 문서에, 반증 결과는 반증 문서에 쓴다.
    tid = find_task(data, runner, path, prompt)
    target = doc(tid, "refute" if refute else "research", data.get("cwd")) if tid else None
    if not target or not target.is_file():
        return
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

    # 저장 오류는 통과시키고, 사유가 있으면 block으로 출력한다.
    try:
        reason = save(json.load(sys.stdin), runner)
        if reason:
            print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
