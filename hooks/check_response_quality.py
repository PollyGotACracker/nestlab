"""Stop hook: 응답의 예고, 책임 회피 표현, 한도를 이유로 한 작업 불가, 출처 없는 주장과 링크 없는 제안을 검사하고 응답을 다시 쓰게 한다."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_workflow_response"))

from check_invalid_response import MAX_BLOCKS, admissions, context, hit_count, incident_link, incident_path, is_child, plain, record_hits, transcript_of
from check_workflow_response import stop_output
from sub_approval import stage_prefix
from sub_claim import REASON_SOURCE, unsourced_claims
from sub_completion import STOP_REQUEST
from sub_docs import project_root
from sub_form import APPROVAL_BLOCK, body_of, completed_response, form_of
from sub_gate import REQUEST_LINE
from sub_role import director_main, project_session
from sub_session import read_session, turn


# 응답 미흡으로 판정할 표현(책임 회피, 다짐, 확인하지 않은 내용)을 찾는다. 검색 결과 요약의 [미조사] 줄과 사후 모델 호출 실측 줄은 제외한다. 서브에이전트 응답은 검사하지 않는다.
def shift_hits(data: dict, runner: str) -> list[str]:
    if data.get("agent_id") or is_child(data):
        return []
    message = data.get("last_assistant_message") or ""
    text = chr(10).join(line for line in plain(message).splitlines() if not EXEMPT_LINE.search(line))
    if not SHIFT.search(text):
        return []
    user = context(transcript_of(data, runner), message, data.get("cwd"))[0]
    return admissions(text, user, SHIFT)


# block 사유를 반환한다. 통과하면 None이다. record가 거짓이면 사건 기록과 분석 실행 없이 판정만 한다.
def check(data: dict, runner: str = "claude", record: bool = True) -> str | None:
    return inspect(data, runner, record)[0]


# (block 사유, 사용자 경고)를 반환한다. 경고는 책임 회피 감지 때만 있고 없으면 빈 문자열이다.
def inspect(data: dict, runner: str = "claude", record: bool = True) -> tuple[str | None, str]:
    data = {**data, "transcript_path": transcript_of(data, runner)}
    # 서브에이전트 응답은 통과한다.
    if not project_session(data, runner):
        return None, ""
    message = data.get("last_assistant_message") or ""
    if limit_hit(message) and not data.get("stop_hook_active"):
        return REASON_LIMIT, ""
    message = completed_response(data, runner, message)
    data = {**data, "last_assistant_message": message}
    body = body_of(message)

    # director 메인 응답만 검사하고, 구현 승인 요청 줄은 작업 흐름 검사가 다룬다.
    if not director_main(data) or APPROVAL_BLOCK.search(body) or REQUEST_LINE.search(body):
        return None, ""

    # 백그라운드 작업이 남은 응답은 짧은 예고와 출처 판정을 건너뛰고 다짐과 책임 회피 표현만 판정한다.
    user = turn(read_session(transcript_of(data, runner)))[0]
    background = bool(data.get("background_tasks")) and form_of(body) != "approval-complete" and stage_prefix(user) != "완료"
    if not background:
        if STOP_REQUEST.search(user):
            return None, ""
        # 검토 제목, 추가 제안, 에이전트 제안이 든 응답은 웹 문서 링크가 없으면 한 번 block한다.
        if PROPOSAL.search(message) and not WEB_LINK.search(message) and not data.get("stop_hook_active"):
            return REASON_LINK, ""
    return response_reason(data, runner, message, body, record, background)


# 응답 미흡 표현과 출처 없는 주장의 (block 사유, 사용자 경고)를 반환한다. 통과하면 (None, "")이다.
# background이면 대기 안내 같은 짧은 예고와 출처 판정은 건너뛰고 다짐과 책임 회피 표현만 판정한다.
def response_reason(data: dict, runner: str, message: str, body: str, record: bool,
                    background: bool = False) -> tuple[str | None, str]:
    # 응답 미흡 표현은 사건 문서에 기록하고 재작성하게 한다. 같은 사용자 메시지의 block이 한도에 닿으면 기록만 한다.
    hits = shift_hits(data, runner)
    if hits:
        user = context(transcript_of(data, runner), message, data.get("cwd"))[0]
        if record:
            path = record_hits(data, runner, hits)
            count = hit_count(path, user)
        else:
            path = incident_path(data, datetime.now())
            count = hit_count(path, user) + 1
        notice = f"사건 감지({', '.join(hits)}): {incident_link(path, data.get('cwd'))}"
        return (None if count > MAX_BLOCKS or data.get("stop_hook_active") else REASON_EMPTY), notice

    # 예고로 끝난 응답과 출처 없는 주장은 턴당 한 번만 block한다.
    if data.get("stop_hook_active") or background:
        return None, ""
    if ANNOUNCE.search(body):
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
    output = stop_output(reason, notice, runner)
    if output:
        json.dump(output, sys.stdout, ensure_ascii=False)
    return 0


# 예고: 미래형 약속, 당위형 종결과 미실행 선언
ANNOUNCE = re.compile(
    r"겠습니다|하겠어요|할게요|드릴게요|예정입니다|해야 합니다|해야 하는 상황입니다"
    r"|(?:아직|따로|별도로)[^.\n]{0,20}(?:변경|수정|실행|적용)하지 않았")
# 위치 이동을 뜻하는 `앞으로 <이동 동사>`는 다짐이 아니므로 책임 회피에서 뺀다.
MOVE = r"(?!\s*(?:옮|당기|당겨|두|둔|배치|이동))"
# 응답 미흡: 후속 작업을 사용자에게 넘기거나 해결을 포기하는 표현, 다짐 문장, 확인하지 않은 내용을 담은 문장
SHIFT = re.compile(
    r"하실 (?:일은 없|필요는 없|필요 없)|추가로 [^.\n]*?은 없"
    r"|해결됐다고 할 수 없|멈추겠|멈출게요|그만두겠|그만하겠|중단하겠|여기서 마치|이미 충분"
    r"|앞으로" + MOVE + r"(?:는)?(?=[^.\n]*?(?:겠습니다|게요))|이제부터|다음(?: \S+)?부터는? [^.\n]*?(?:겠습니다|게요)"
    r"|(?:이후 처리|이후로는|다음 세션|재발하지 않|반복되지 않)[^.\n]*?(?:겠습니다|게요)"
    r"|미확인(?!\s*(?:표기|표현|단어|문자열))|확인하지 않|확인되지 않")
# 한도 표현과 작업 불가 표현이다. 한 줄에 둘이 함께 있으면 한도를 이유로 한 작업 불가 응답으로 판정한다.
LIMIT_WORD = re.compile(r"한도|사용량 제한|rate limit|usage limit", re.I)
LIMIT_REFUSE = re.compile(r"(?:할|될|있을|드릴) 수 없|불가능|불가합니다|못 합니다|못합니다|어렵습니다")
REASON_LIMIT = "한도를 이유로 작업 불가를 알리는 응답은 표시할 수 없습니다. 한도 언급 없이 가능한 작업을 계속하고 결과를 작성하십시오."


# 한 줄에 한도 표현과 작업 불가 표현이 함께 있으면 True이다. 사건 문서에는 기록하지 않는다.
def limit_hit(message: str) -> bool:
    return any(LIMIT_WORD.search(line) and LIMIT_REFUSE.search(line) for line in plain(message).splitlines())


# 응답 미흡 판정에서 제외하는 줄이다. 검색 결과 요약의 [미조사] 줄과 사후 모델 호출 실측 줄이다.
EXEMPT_LINE = re.compile(r"\[미조사\]|^\s*[-*]?\s*사후 모델 호출 실측:")
# 검토 제목, 추가 제안, 에이전트 제안 표현과 웹 문서 링크이다.
PROPOSAL = re.compile(r"(?m)^#{1,6}\s*검토 \d+|추가 제안|에이전트 제안")
WEB_LINK = re.compile(r"https?://\S")
REASON_LINK = "검토나 제안을 제시한 응답에 웹 문서 링크 출처가 없습니다. 출처가 필요한 제안과 판단마다 해당 추론과 근거 문서의 http 링크만 작성하십시오."
REASON_EMPTY = "직전 응답이 예고나 해명으로 끝났습니다."


if __name__ == "__main__":
    sys.exit(main())
