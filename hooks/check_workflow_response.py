"""Stop hook: 응답의 결과물, 출처, 작업 흐름 위반과 책임 회피 표현을 검사하고 응답을 다시 쓰게 한다.

호출자:
- Claude Code와 Codex의 Stop hook 설정(shared/settings)
- 호출 명령은 `python check_workflow_response.py --runner <claude|codex>`
입력: hook 입력 JSON(last_assistant_message, stop_hook_active 등)을 stdin으로 받는다.
반환:
- 위반이면 stdout에 `{"decision": "block", "reason": ..., "systemMessage": ...}` JSON을 출력하고 통과하면 아무것도 출력하지 않는다.
- check()는 block 사유 문자열 또는 None을 반환한다.
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

from check_invalid_response import (MAX_BLOCKS, admissions, context, hit_count, incident_path, is_child, plain,
                             record_hits, transcript_of)
from sub_docs import doc, legacy, project_root, review_done
from sub_judgment import open_tasks, review_items, status_of
from sub_role import resolve_role
from sub_write import read

MAX_LEN = 300
FENCE = re.compile(r"^\s*(```|~~~)")
TABLE = re.compile(r"^\s*\|.*\|\s*$", re.M)
LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S", re.M)
DIFF = re.compile(r"^(?:[+-]{3} |@@ )", re.M)
PATH = re.compile(
    r"(?:[\w.@~-]+[\\/])+[\w.@-]+|\b[\w.-]+\.(?:md|py|jsonl?|ya?ml|toml|txt|csv|log|js|tsx?|html|css)\b")
URL = re.compile(r"https?://\S+")
INLINE = re.compile(r"`[^`\n]+`")
SOURCE = re.compile(PATH.pattern + "|" + URL.pattern + "|" + INLINE.pattern + r"|\[추론\]")
# 예고: 미래형 약속과 미실행 선언
ANNOUNCE = re.compile(
    r"겠습니다|하겠어요|할게요|드릴게요|예정입니다|(?:아직|따로|별도로)[^.\n]{0,20}(?:변경|수정|실행|적용)하지 않았")
# 인정과 자기 평가
ADMIT = re.compile(
    r"맞습니다[!.]?|죄송|제가 잘못|제 잘못|제 실수|해결하는 대신|못 했|못했")
# 책임 회피: 후속 작업을 사용자에게 넘기거나 해결을 포기하는 표현
SHIFT = re.compile(
    r"하실 (?:일은 없|필요는 없)|추가로 [^.\n]*?은 없"
    r"|해결됐다고 할 수 없|멈추겠습니다|멈출게요"
    r"|앞으로|앞으로는|(?:앞으로|앞으로는|다음) [^.\n]*?(?:에는|부터|부터는)|이미 충분")
CLAIM = re.compile(r"호출됐|호출되었|확인했|측정했|동작합니다|동작했|통과했")
NEGATION = re.compile(r"하지 않|않았|못 |못했|없")
# 불가 주장: 부정형 능력 주장
INACCESSIBLE = re.compile(r"(?:볼|읽을|열|접근할|확인할|찾을|조회할|할) 수 없|불가능")
QUOTED = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”|'[^'\n]*'|‘[^’\n]*’")
APPROVAL_BLOCK = re.compile(r"^#{1,6} 작업 승인 필요\s*$", re.M)
CHOICE_BLOCK = re.compile(r"^#{1,6} (?:사용자 )?(?:선택|판단)(?: 요청| 필요)\s*$", re.M)
# 둘째 갈래는 임시 경로(log/state)이다. 기존 로그 삭제 뒤 제거한다.
STATE_REF = re.compile(r"log/task/([\w.\-]+)/(?:status|spec|research|refute)\.md|log/state/([\w.\-]+?)(?:-input|-research)?\.md")
QUESTION_END = re.compile(r"[?？]\s*\**\s*$")
UNRESOLVED = "중단 판정 미해결"
RECORD = "결정 기록"


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


# 출처 없이 측정이나 확인을 주장한 긍정형 문장과 출처 없는 불가 주장 문장 목록을 반환한다.
def unsourced_claims(message: str, body: str) -> list[str]:
    split = lambda text: [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text)]
    sentences = split(body)

    # 불가 주장은 응답 다른 곳의 출처로 대신할 수 없어 같은 문단(빈 줄로 나뉜 줄 묶음) 안에 출처가 있어야 한다.
    # 따옴표로 인용한 예시는 주장이 아니므로 인용 구간을 뺀 문장으로 판정한다.
    blind = [s for paragraph in re.split(r"\n\s*\n", body) if not SOURCE.search(paragraph)
             for s in split(paragraph) if INACCESSIBLE.search(QUOTED.sub("", s))]

    # 긍정형 주장의 출처 판정 단위는 응답 전체이다. 출처는 코드 블록과 인용을 뺀 본문 앞의 원문에서도 찾는다.
    if SOURCE.search(message):
        return blind
    return blind + [s for s in sentences if CLAIM.search(s) and not NEGATION.search(s)]


REASON_APPROVAL_DOC = "승인 요청에 작업의 경과 문서 경로(log/task/<작업-id>/status.md)를 링크로 작성하십시오."
REASON_APPROVAL_PENDING = ("경과 문서 '{doc}'의 검토 필요에 사용자 검토가 끝나지 않은 항목이 있어 승인을 요청할 수 없습니다. 해당 항목은 '{items}'입니다.\n"
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
            pending = [name for name, item in review_items(read(path)).items()
                       if status_of(item) not in ready]
            if pending:
                return REASON_APPROVAL_PENDING.format(doc=path.name, items=", ".join(pending[:3])), [path]

    # Claude 응답이 질문 문장으로 끝나면 질문 도구를 쓰게 한다.
    lines = [line for line in body.splitlines() if line.strip()]
    if runner == "claude" and lines and QUESTION_END.search(lines[-1]):
        return REASON_QUESTION, docs
    return None, docs


# 같은 위반이 반복되면 경과 문서 결정 기록 끝에 미해결을 남긴다. 경과 문서가 하나로 정해질 때만 쓴다.
def record_unresolved(docs: list[Path], reason: str) -> None:
    # 기록할 작업 문서가 하나로 정해지지 않으면 기록하지 않는다.
    if len(docs) != 1:
        return

    # 기록할 결정 항목을 만들고 결정 기록 절 끝을 찾는다. 기존 log/state 문서는 기존 절 이름으로 찾는다.
    path = docs[0]
    text = read(path)
    entry = (f"\n### {UNRESOLVED}\n\n- 시각: {datetime.now():%Y-%m-%d %H:%M:%S}\n- 담당: hook\n"
             f"- 결정: {UNRESOLVED}\n- 근거: {reason}\n")
    titles = "|".join(map(re.escape, (RECORD, legacy(RECORD))))
    m = re.search(rf"^## (?:{titles})[^\n]*\n.*?(?=^#{{1,2}} |\Z)", text, re.M | re.S)

    # 결정 기록 절 끝에 항목을 덧붙인다.
    if m:
        head = text[:m.end()].rstrip("\n") + "\n"
        path.write_text(head + entry + ("\n" + text[m.end():] if text[m.end():] else ""), encoding="utf-8")


REASON_SHIFT = ("직전 응답에 책임을 회피하는 표현({hits})이 있습니다. 사건 분석은 별도 세션이 진행하므로 사건 문서를 작업하지 마십시오.\n"
                "재작성한 응답의 첫 줄에 아래 문장을 그대로 쓰고, 사용자의 요청에 맞는 내용을 포함하여 재작성하십시오.\n")
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


REASON_EMPTY = ("직전 응답에서 사용자 요청에 대한 결과물 없이 예고나 해명만 작성되었습니다.\n"
                "결과물(코드, 표, 목록, 파일 경로, diff)을 포함하여 다시 작성하십시오.")
REASON_SOURCE = ("측정이나 확인을 주장한 문장 '{sentences}'에 근거가 없습니다.\n"
                 "근거의 출처(파일:행, 파일 경로, 링크, 명령 결과 인용)를 포함하여 다시 작성하십시오.")

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
        return (None if count > MAX_BLOCKS else REASON_SHIFT.format(hits=", ".join(hits))), notice

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
