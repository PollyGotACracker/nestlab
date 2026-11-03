"""Stop hook: 응답의 결과물, 출처, 작업 흐름 위반과 책임 회피 표현을 검사하고 응답을 다시 쓰게 한다.

호출자:
- Claude Code와 Codex의 Stop hook 설정(shared/settings)
- 호출 명령은 `python check_workflow_response.py --runner <claude|codex>`
입력: hook 입력 JSON(last_assistant_message, stop_hook_active 등)을 stdin으로 받는다.
반환:
- 위반이면 stdout에 `{"decision": "block", "reason": ..., "systemMessage": ...}` JSON을 출력하고 통과하면 아무것도 출력하지 않는다.
- check()는 block 사유 문자열 또는 None을 반환한다.
- director 메인 세션이 세션 기록에 nestlab-kickoff 호출 없이 응답을 끝내면 한 번 block한다.
한계: 외부 모델을 호출하지 않는 정규식 판정이라 자연어 의미는 판정하지 못한다. 오류는 통과한다(fail-open).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))

from check_invalid_response import (MAX_BLOCKS, NOTICE_INCIDENT, admissions, context, hit_count, incident_link,
                             incident_path, is_child, plain, record_hits, transcript_of)
from sub_claim import PATH, REASON_SOURCE, URL, unsourced_claims
from sub_docs import FENCE_LINE, HEADING, doc, measure_pending, project_root, review_done
from sub_judgment import ASK, DONE, confirmed, open_tasks, research_pending, review_items, status_of
from sub_question import TABLE_OR_HEADING
from sub_role import resolve_role
from sub_scope import kickoff_missing
from sub_session import question_answers, read_session, turn
from sub_workflow import R_NUM_MISMATCH, Document, check_numbers
from sub_write import PATCH_FILE, read, strings

MAX_LEN = 300
FENCE = re.compile(r"^\s*(```|~~~)")
TABLE = re.compile(r"^\s*\|.*\|\s*$", re.M)
LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S", re.M)
DIFF = re.compile(r"^(?:[+-]{3} |@@ )", re.M)
# 예고: 미래형 약속과 미실행 선언
ANNOUNCE = re.compile(
    r"겠습니다|하겠어요|할게요|드릴게요|예정입니다|(?:아직|따로|별도로)[^.\n]{0,20}(?:변경|수정|실행|적용)하지 않았")
# 인정과 자기 평가
ADMIT = re.compile(
    r"맞습니다[!.]?|죄송|제가 잘못|제 잘못|제 실수|해결하는 대신|못 했|못했")
# 위치 이동을 뜻하는 `앞으로 <이동 동사>`는 다짐이 아니므로 책임 회피에서 뺀다.
MOVE = r"(?!\s*(?:옮|당기|당겨|두|둔|배치|이동))"
# 책임 회피: 후속 작업을 사용자에게 넘기거나 해결을 포기하는 표현
SHIFT = re.compile(
    r"하실 (?:일은 없|필요는 없)|추가로 [^.\n]*?은 없"
    r"|해결됐다고 할 수 없|멈추겠습니다|멈출게요"
    r"|앞으로" + MOVE + r"|앞으로는|다음(?: \S+)?부터는? [^.\n]*?(?:하겠습니다|할게요)|이미 충분")
APPROVAL_BLOCK = re.compile(r"^#{1,6} (?:작업 승인 필요|승인 요청)\s*$", re.M)
CHOICE_BLOCK = re.compile(r"^#{1,6} (?:사용자 )?(?:선택|판단)(?: 요청| 필요)\s*$", re.M)
# 둘째 갈래는 임시 경로(log/state)이다. 기존 로그 삭제 뒤 제거한다.
STATE_REF = re.compile(r"log/task/([\w.\-]+)/(?:status|spec|research|refute)\.md|log/state/([\w.\-]+?)(?:-input|-research)?\.md")
QUESTION_END = re.compile(r"[?？]\s*\**\s*$")
UNRESOLVED = "중단 판정 미해결"


# 코드 블록과 인용 줄을 뺀 본문을 반환한다.
def body_of(message: str) -> str:
    out, fence = [], False
    for line in message.splitlines():
        if FENCE.match(line):
            fence = not fence
        elif not fence and not line.lstrip().startswith(">"):
            out.append(line)
    return "\n".join(out)


# 코드 블록, 표, 목록, diff, 경로, 링크 중 하나라도 있으면 True이다.
def has_deliverable(message: str, body: str) -> bool:
    if any(FENCE.match(line) for line in message.splitlines()):
        return True
    return any(p.search(body) for p in (TABLE, LIST, DIFF, PATH, URL))


REASON_PROPOSAL = ("조사 문서의 최종 조사 원문이 비어 있는데 조사 도구 호출 없이 해결안을 제안했습니다.\n"
                   "조사 단계로 돌아가 저장소와 웹을 조사한 뒤 결과를 보고하십시오.")
# 해결안 표지: 추천 문장. 안 비교 표나 heading은 TABLE_OR_HEADING이 판정한다.
PROPOSAL = re.compile(r"추천(?:은|합니다|드립니다| 안)")
RESEARCH_TOOLS = {"Read", "Grep", "Glob", "WebSearch", "WebFetch", "Agent"}


# 이번 턴에 조사 도구를 호출했으면 True이다.
def researched(data: dict, runner: str) -> bool:
    entries = turn(read_session(transcript_of(data, runner)))[2]
    return any(name in RESEARCH_TOOLS for e in entries for name, _ in e["calls"])


REASON_APPROVAL_DOC ="승인 요청에 작업의 경과 문서 경로(log/task/<작업-id>/status.md)를 링크로 작성하십시오."
REASON_APPROVAL_PENDING = ("경과 문서 '{doc}'의 검토 필요 또는 사전 실측 필요에 끝나지 않은 항목이 있어 승인을 요청할 수 없습니다. 해당 항목은 '{items}'입니다.\n"
                           "모든 안의 사용자 검토가 끝난 후 승인 요청을 진행하십시오.")
REASON_CHOICE = "판단 지점 밖의 선택 요청입니다. 사용자 검토 필요 상태인 검토 항목과 연결하십시오."
REASON_QUESTION = ("응답을 질문 문장으로 끝냈습니다. 지침이나 요구로 정해지는 사안이면 근거를 적고 진행하십시오.\n"
                   "사용자 판단이 필요하면 경과 문서 검토 필요 항목을 `사용자 검토 필요`로 두고 질문 도구를 사용해 물으십시오.")

# (작업 흐름 위반 사유, 관련 경과 문서)이다. log 폴더가 없는 저장소는 검사하지 않는다.
def workflow_reason(data: dict, message: str, body: str, runner: str) -> tuple[str | None, list[Path]]:
    # 작업 문서 폴더가 없으면 검사하지 않는다.
    root = project_root(data.get("cwd"))
    if not (root / "log").is_dir():
        return None, []

    # 응답이 가리킨 경과 문서를 찾고, 없으면 진행 중인 작업을 쓴다.
    refs = [doc(a or b, "state", root) for a, b in dict.fromkeys(STATE_REF.findall(message))]
    docs = [p for p in refs if p.is_file()] or open_tasks(root)

    # 선택 요청 블록은 사용자 검토 필요 항목과 연결돼야 한다.
    if CHOICE_BLOCK.search(body):
        needed = [(p, name) for p in docs for name, item in review_items(read(p)).items()
                  if status_of(item) == "사용자 검토 필요" and name in body]
        if not needed:
            return REASON_CHOICE, docs

    # 승인 요청 블록은 director의 상태 문서 경로를 요구하고 참조 문서의 검토 필요 항목을 검사한다.
    if APPROVAL_BLOCK.search(body):
        if not refs and resolve_role(data) == "director":
            return REASON_APPROVAL_DOC, docs
        try:
            ready = review_done()
        except ValueError as error:
            return str(error), docs
        for path in refs:
            text = read(path)
            pending = [name for name, item in review_items(text).items()
                       if status_of(item) not in ready] + measure_pending(text)
            if pending:
                return REASON_APPROVAL_PENDING.format(doc=path.name, items=", ".join(pending[:3])), [path]

            # 같은 작업 폴더의 명세 문서 요구사항 번호가 요구사항 목록 번호와 다르면 막는다. 명세 문서가 없거나 읽을 수 없으면 건너뛴다.
            spec = path.with_name("spec.md")
            if spec.is_file():
                try:
                    state_doc, plan_doc = Document(text), Document(read(spec))
                except ValueError:
                    continue
                try:
                    check_numbers(state_doc, plan_doc)
                except ValueError as error:
                    return str(error), [path]

    # 조사 원문이 빈 작업에서 조사 도구 호출 없이 해결안을 제안한 director 응답은 조사 단계로 되돌린다.
    if (resolve_role(data) == "director" and research_pending(docs) and PROPOSAL.search(body)
            and TABLE_OR_HEADING.search(body) and not researched(data, runner)):
        return REASON_PROPOSAL, docs

    # Claude 응답이 질문 문장으로 끝나면 질문 도구를 쓰게 한다.
    lines = [line for line in body.splitlines() if line.strip()]
    if runner == "claude" and lines and QUESTION_END.search(lines[-1]):
        return REASON_QUESTION, docs
    return None, docs


# text에서 title로 시작하는 제목 절의 본문(다음 같은 수준 이상 제목 전까지)에 edit을 적용한 전문을 반환한다. 절이 없으면 text 그대로이다.
def splice(text: str, title: str, edit) -> str:
    # 제목 줄을 찾아 본문의 시작과 끝 위치를 구한다. 코드 블록 안의 줄은 제목으로 보지 않는다.
    start = end = None
    level, fence, pos = 0, "", 0
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        f = FENCE_LINE.match(bare)
        if f and not fence:
            fence = f.group(1)
        elif f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence):
            fence = ""
        m = None if fence or f else HEADING.match(bare)
        if m and start is not None and len(m.group(1)) <= level:
            end = pos
            break
        if m and start is None and m.group(2).startswith(title):
            level, start = len(m.group(1)), pos + len(line)
        pos += len(line)
    if start is None:
        return text

    # 본문만 바꾸고 나머지는 그대로 둔다.
    end = len(text) if end is None else end
    return text[:start] + edit(text[start:end]) + text[end:]


# 파일 본문에 edit(전문 -> 전문)을 적용해 쓴다. 줄바꿈 형식과 인코딩은 원본을 유지하고 바뀐 내용이 없으면 쓰지 않는다.
def rewrite(path: Path, edit) -> None:
    raw = path.read_bytes().decode("utf-8")
    text = raw.replace("\r\n", "\n")
    new = edit(text)
    if new != text:
        path.write_bytes((new.replace("\n", "\r\n") if "\r\n" in raw else new).encode("utf-8"))


# 같은 위반이 반복되면 경과 문서 중단 사유 절 끝에 미해결 줄을 남긴다. 경과 문서가 하나로 정해질 때만 쓴다.
def record_unresolved(docs: list[Path], reason: str) -> None:
    # 기록할 작업 문서가 하나로 정해지지 않으면 기록하지 않는다.
    if len(docs) != 1:
        return

    # 중단 사유 절 끝에 미해결 줄을 덧붙인다. 같은 줄이 있으면 덧붙이지 않는다.
    line = f"- {UNRESOLVED}: {' '.join(reason.split())}"
    tail = lambda body: body.rstrip("\n") + "\n" + line + ("\n\n" if body.endswith("\n\n") else "\n")
    rewrite(docs[0], lambda text: splice(text, "중단 사유", lambda body: body if line in body.splitlines() else tail(body)))


REQUIREMENT = re.compile(r"^요구사항 (\d+)\. [^:\n]+: .+$")
STATUS_PATH = re.compile(r"log/task/[\w.\-]+/status\.md")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "apply_patch")
TIME_LINE = re.compile(r"^(  - 사용자 검토 시각: ).*$", re.M)


# 세션 기록에서 에이전트가 마지막으로 쓴 경과 문서 경로를 반환한다. 없으면 None이다.
def written_status(data: dict, runner: str) -> Path | None:
    last = None
    for entry in read_session(transcript_of(data, runner)):
        for name, args in entry["calls"]:
            if name not in WRITE_TOOLS:
                continue
            paths = ([a or b for s in strings(args) for a, b in PATCH_FILE.findall(s)] if name == "apply_patch"
                     else [str((args or {}).get("file_path") or "")])
            for p in paths:
                m = STATUS_PATH.search(p.replace("\\", "/"))
                last = m.group(0) if m else last
    path = project_root(data.get("cwd")) / last if last else None
    return path if path and path.is_file() else None


# 요구사항 목록 절 본문에 line들을 번호 순서로 넣는다. 같은 번호는 바꾸고 양식 자리표시 줄은 지운다.
def put_requirements(body: str, lines: list[str]) -> str:
    items = {int(m.group(1)): m.group(0) for m in re.finditer(r"(?m)^(\d+)\. (?!<).*$", body)}
    items.update({int(re.match(r"\d+", l).group(0)): l for l in lines})
    first = re.search(r"(?m)^\d+\. ", body)
    head = body[:first.start()] if first else "\n"
    return head + "\n".join(items[k] for k in sorted(items)) + ("\n\n" if body.endswith("\n\n") else "\n")


# 검토 필요 절 본문에 (이름, 항목 본문) 항목들을 넣는다. 같은 이름은 바꾸고 새 이름은 끝에 덧붙이며 양식 자리표시 항목은 지운다.
def put_reviews(body: str, blocks: dict[str, str]) -> str:
    starts = list(re.finditer(r"(?m)^- (.+)$", body))
    items = {m.group(1).strip(): body[m.start():(starts[i + 1].start() if i + 1 < len(starts) else len(body))].strip("\n")
             for i, m in enumerate(starts) if not m.group(1).startswith("<")}
    for name, block in blocks.items():
        # 시각을 뺀 내용이 같으면 기존 항목(시각 포함)을 둔다.
        if not (name in items and TIME_LINE.sub("", items[name]) == TIME_LINE.sub("", block)):
            items[name] = block
    head = body[:starts[0].start()] if starts else body.rstrip("\n") + "\n\n"
    return head + "\n".join(items.values()) + ("\n\n" if body.endswith("\n\n") else "\n")


# 이번 턴 질문 도구 답을 경과 문서에 기록한다. 요구사항 형식 질문의 확정 답은 요구사항 목록에, 그 밖의 질문은 검토 필요에 쓴다.
def record_answers(data: dict, runner: str) -> None:
    # 이번 턴 질문 답과 기록할 경과 문서를 찾는다.
    answers = question_answers(turn(read_session(transcript_of(data, runner)))[2])
    path = written_status(data, runner) if answers else None
    if path is None:
        return

    # 질문마다 요구사항 줄 또는 검토 필요 항목을 만든다.
    now = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    requirements, reviews = [], {}
    flat = lambda text: " ".join(str(text).split())
    for q, answer in answers:
        question, ok = flat(q.get("question") or ""), confirmed(answer)
        if REQUIREMENT.match(question):
            if ok:
                requirements.append(question[len("요구사항 "):])
            continue
        picked = [o for o in q.get("options") or [] if isinstance(o, dict) and o.get("label") and str(o["label"]) in answer]
        label = ", ".join(flat(o["label"]) for o in picked) or flat(answer)
        basis = " ".join(flat(o.get("description") or "") for o in picked).strip() or "기록 없음"
        reviews[question] = (f"- {question}\n  - 상태: {DONE if ok else ASK}\n  - 사용자 검토 시각: {now}\n"
                             f"  - 대응 근거: {basis}\n  - 대응 방안:\n    - {label}")

    # 두 절만 바꾼다.
    def edit(text: str) -> str:
        if requirements:
            text = splice(text, "요구사항 목록", lambda body: put_requirements(body, requirements))
        if reviews:
            text = splice(text, "검토 필요", lambda body: put_reviews(body, reviews))
        return text
    rewrite(path, edit)


REASON_SHIFT = ("직전 응답에 책임을 회피하는 표현({hits})이 있습니다. 사건 분석은 별도 세션이 진행하므로 사건 문서를 작업하지 마십시오.\n"
                "재작성한 응답의 첫 줄에 아래 문장을 그대로 쓰고, 사용자의 요청에 맞는 내용을 포함하여 재작성하십시오.\n"
                + NOTICE_INCIDENT)
WARNING = "직전 응답은 응답 검사에서 무효 처리됐습니다. 다시 작성합니다."


# 응답 미흡으로 판정할 책임 회피 표현을 찾는다. 서브에이전트 응답은 검사하지 않는다.
def shift_hits(data: dict, runner: str) -> list[str]:
    if data.get("agent_id") or is_child(data):
        return []
    message = data.get("last_assistant_message") or ""
    text = plain(message)
    if not SHIFT.search(text):
        return []
    user = context(transcript_of(data, runner), message, data.get("cwd"))[0]
    return admissions(text, user, SHIFT)


REASON_KICKOFF = "첫 응답 전에 nestlab-kickoff 스킬을 호출하지 않았습니다. nestlab-kickoff 스킬을 먼저 실행한 뒤 응답하십시오."
REASON_EMPTY = ("직전 응답에서 사용자 요청에 대한 결과물 없이 예고나 해명만 작성되었습니다.\n"
                "결과물(코드, 표, 목록, 파일 경로, diff)을 포함하여 다시 작성하십시오.")
# block 사유를 반환한다. 통과하면 None이다. record가 거짓이면 사건 기록과 분석 실행 없이 판정만 한다.
def check(data: dict, runner: str = "claude", record: bool = True) -> str | None:
    return inspect(data, runner, record)[0]


# (block 사유, 사용자 경고)를 반환한다. 경고는 책임 회피 감지 때만 있고 없으면 빈 문자열이다.
def inspect(data: dict, runner: str = "claude", record: bool = True) -> tuple[str | None, str]:
    # 서브에이전트 응답과 백그라운드 작업이 남은 응답은 통과한다.
    if data.get("agent_id") or data.get("background_tasks"):
        return None, ""
    message = data.get("last_assistant_message") or ""
    body = body_of(message)

    # 이번 턴 질문 도구 답을 경과 문서에 기록한다. 기록 오류는 판정에 영향을 주지 않는다.
    if record:
        try:
            record_answers(data, runner)
        except Exception:
            pass

    # director 메인 세션이 nestlab-kickoff 호출 없이 끝낸 응답은 한 번 block한다.
    if not data.get("stop_hook_active") and kickoff_missing({**data, "transcript_path": transcript_of(data, runner)}, runner):
        return REASON_KICKOFF, ""

    # 작업 흐름 위반은 한 번 block하고, 반복되면 미해결로 기록한 뒤 통과한다.
    reason, docs = workflow_reason(data, message, body, runner)
    if reason and data.get("stop_hook_active"):
        record_unresolved(docs, reason)
        return None, ""
    if reason:
        return reason, ""

    # 책임 회피 표현은 사건 문서에 기록하고 재작성하게 한다. 같은 사용자 메시지의 block이 한도에 닿으면 기록만 한다.
    hits = shift_hits(data, runner)
    if hits:
        user = context(transcript_of(data, runner), message, data.get("cwd"))[0]
        if record:
            path = record_hits(data, runner, hits)
            count = hit_count(path, user)
        else:
            path = incident_path(data, datetime.now())
            count = hit_count(path, user) + 1
        notice = f"사건 감지({', '.join(hits)}): {path}"
        link = incident_link(path, data.get("cwd"))
        return (None if count > MAX_BLOCKS else REASON_SHIFT.format(hits=", ".join(hits), link=link)), notice

    # 결과물 없는 미흡 응답과 출처 없는 주장은 턴당 한 번만 block한다.
    if data.get("stop_hook_active"):
        return None, ""
    if (not has_deliverable(message, body) and len(body.strip()) <= MAX_LEN
            and (ANNOUNCE.search(body) or ADMIT.search(body))):
        return REASON_EMPTY, ""
    claims = unsourced_claims(message, body)
    if claims:
        return REASON_SOURCE.format(sentences=" / ".join(claims[:3])), ""
    return None, ""


# stdin의 hook 입력을 검사하고 위반이면 block JSON을 출력한다. 항상 0을 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞추고 인자를 읽는다.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    runner = parser.parse_args().runner

    # 검사 오류는 통과시키고, 위반은 block으로 출력한다.
    try:
        reason, notice = inspect(json.load(sys.stdin), runner)
    except Exception:
        return 0
    if reason:
        output = {"decision": "block", "reason": reason}
        if notice:
            output["systemMessage"] = notice
        elif runner == "codex":
            output["systemMessage"] = WARNING  # Codex는 응답 표시를 바꿀 수 없어 경고로 알린다.
        json.dump(output, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
