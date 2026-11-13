"""서브에이전트 호출 승인과 승인 키워드를 판정한다.

호출자: hooks/check_tool_use.py
입력: hook 입력 dict, 사용자 마지막 메시지, 하위 요청 본문
반환:
- check_agent_call, check_codex_child, approval_reason은 deny 사유 문자열 또는 통과 시 None
- approved, executed_approved는 bool
- user_message는 사용자 마지막 메시지 문자열
한계: Codex spawn_agent는 PreToolUse가 실행되지 않아 하위 thread의 도구 호출 시점에 승인을 확인한다.
"""
from __future__ import annotations

import re
from pathlib import Path

from sub_call import call_info
from sub_input import LEAK, session_leak
from sub_docs import doc, section, task_id
from sub_role import codex_rollout, first_line, first_prompt, parent_thread, resolve_role
from sub_session import read_session, session_path, turn, user_messages
from sub_task_docs import TASK_ID, create
from sub_write import APPROVAL_RETRY, read

# 사용자 메시지의 마지막이 승인 키워드로 끝날 때만 승인이다.
# 승인 키워드 뒤에 오는 문자는 `.`, `!`, `~`만 허용된다.
# 승인 키워드는 계획 수정을 의도한 프롬프트와 구별되어야 한다.
APPROVAL = re.compile(r"(?:^|\s)\S*승인\s*[.!~]*\s*\Z")
APPROVED = re.compile(r"^\s*-\s*(?:사용자 )?승인 상태:\s*승인\s*$")
IN_SCOPE = ("task: reimplement", "task: reverify")
RESTORED_WORK = ("task: implement",)
# 조사 승인 뒤 조사와 반증은 사용자 대기 없이 이어진다(상태 문서 조사 승인 범위가 승인일 때).
RESEARCH_SCOPE = ("task: research", "task: refute")
NEGATION = ("안", "못")


# (사용자 마지막 메시지, 그 직전 assistant 응답)이다.
def last_user(path: str | None) -> tuple[str, str]:
    user, shown, _ = turn(read_session(path or ""))
    return user, shown


# 사용자 마지막 메시지를 구한다. Codex 하위 thread는 부모 rollout의 메시지이다.
def user_message(data: dict, runner: str) -> str:
    path = data.get("transcript_path") or session_path(data, runner)
    parent = parent_thread(path)
    if parent:
        path = codex_rollout(parent)
    return last_user(path)[0]


# 사용자 메시지의 비어 있지 않은 마지막 줄이 승인 키워드이면 True이다. 부정어가 바로 앞에 오면 False이다.
def approved(user: str) -> bool:
    lines = [line for line in user.splitlines() if line.strip()]
    if not lines:
        return False
    words = lines[-1].split()
    if len(words) >= 2 and words[-2] in NEGATION:
        return False
    return bool(APPROVAL.search(lines[-1]))


# 상태 문서의 승인 절(기본은 구현 승인 범위)에 승인 상태가 승인으로 기록돼 있으면 True이다.
def executed_approved(task: str | None, cwd: str | None = None, title: str = "구현 승인 범위") -> bool:
    body = section(read(doc(task, "state", cwd)), title) if task else ""
    return any(APPROVED.match(line) for line in body.splitlines())


# 기존 단계 및 승인 판정이 인정한 현재 사용자 메시지의 기록 시각만 승인 절에 채운다.
def approval_record_times(data: dict, path, text: str) -> str:
    entries = read_session(data.get("transcript_path") or "")
    messages = [entry for entry in entries if user_messages([entry])]
    if not messages:
        return text
    entry = messages[-1]
    user, stage = entry["text"], stage_of(text)
    prefix = stage_prefix(user)
    if stage:
        title = {"조사": "조사 승인 범위", "구현": "구현 승인 범위"}.get(stage) if prefix == stage else None
    else:
        title = None
        if approved(user):
            # 종전 승인 판정의 승인 대상은 바로 앞 승인 보고 제목으로 구분한다.
            shown = turn(entries)[1]
            title = "조사 승인 범위" if "조사 승인 요청" in shown else "구현 승인 범위" if "구현 승인 요청" in shown or "작업 승인 필요" in shown else None
    if title is None:
        return text
    from check_workflow_response import splice
    stamp = entry.get("timestamp") or "기록 없음"
    return splice(text, title, lambda body: re.sub(r"(?m)^(\s*- 사용자 승인 시각:)[^\n]*$",
                                                  lambda m: m[1] + " " + stamp, body))


# 같은 작업의 명세 문서에 실제 대상이 적힌 대기 재구현이 있는지 확인한다.
def reimplement_pending(task: str | None, cwd: str | None = None) -> bool:
    from sub_document import filled, values
    body = section(read(doc(task, "input", cwd)), "재구현 요청") if task else ""
    for item in re.split(r"(?m)^- ", body)[1:]:
        target = re.match(r"([^\n]+):[ \t]*(.*)", item)
        expected = values(item, "기대 결과")
        if target and expected and all(filled(value) and not re.search(r"<[^<>\n]+>", value)
                                       for value in [target[1], target[2], *expected]):
            return True
    return False


STAGES = ("착수", "조사", "구현", "완료")
# 사용자 메시지 머리줄의 단계 키워드와 경과 문서 작업 단계 값의 대응이다.
KEYWORDS = {"kickoff": "착수", "research": "조사", "implement": "구현", "complete": "완료"}
HEAD = re.compile(r"^\[(kickoff|research|implement|complete)(?: (\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*))?\]")
STAGE_LINE = re.compile(r"(?m)^- 작업 단계:[ \t]*(.*)$")
# log/ 밖 쓰기를 허용하는 단계와 단계별 허용 task 값이다.
WRITE_STAGES = ("구현",)
STAGE_CALLS = {"조사": ("task: research", "task: refute"),
               "구현": ("task: implement", "task: verify", "task: reimplement", "task: reverify")}
REASON_STAGE_CALL = "경과 문서의 작업 단계 [{stage}]에서는 `{kind}` 배정을 할 수 없습니다. 허용: {allowed}."


# 사용자 메시지 첫 줄 시작의 `[키워드]` 또는 `[키워드 작업ID]`를 (작업 단계 값, 작업 ID)로 읽는다.
# 첫 줄이 대괄호로 시작하지 않으면 (None, None), 대괄호로 시작하지만 머리줄 형식이 아니면 ("보류", None)이다.
def head_line(message: str) -> tuple[str | None, str | None]:
    first = (message or "").split("\n", 1)[0].rstrip()
    if not first.startswith("["):
        return None, None
    found = HEAD.match(first)
    return (KEYWORDS[found[1]], found[2]) if found else ("보류", None)


# 사용자 메시지 첫 줄의 단계 접두어이다. 접두어가 없으면 None, 머리줄 형식이 아니면 "보류"이다.
def stage_prefix(message: str) -> str | None:
    return head_line(message)[0]


# 경과 문서의 `작업 단계` 값이다. 줄이 없거나 단계 이름이 아니면 None이다. 기존 기록의 `진행`은 `구현`으로 읽는다.
def stage_of(text: str) -> str | None:
    m = STAGE_LINE.search(text or "")
    value = {"진행": "구현"}.get(m[1].strip(), m[1].strip()) if m else ""
    return value if value in STAGES else None


# 기존 상태 기록을 단계 한 줄로 변환하고 나머지 본문은 보존한다.
def normalize_stage(text: str, stage: str | None = None) -> str:
    legacy = re.search(r"(?m)^- 작업 상태:[ \t]*(.*)$", text)
    previous = {"미개시": "착수", "진행": "구현", "구현": "구현", "완료": "완료", "중단": "구현"}
    stage = stage or stage_of(text) or (previous.get(legacy[1].strip()) if legacy else None)
    if stage not in STAGES:
        return text
    line = f"- 작업 단계: {stage}"
    if STAGE_LINE.search(text):
        text = STAGE_LINE.sub(line, text, count=1)
        return re.sub(r"(?m)^- 작업 상태:.*\n?", "", text)
    if legacy:
        return re.sub(r"(?m)^- 작업 상태:.*$", line, text, count=1)
    header = re.search(r"(?m)^- 작업 ID:.*$", text) or re.search(r"(?m)^# .*$", text)
    if header:
        return text[:header.end()] + "\n" + line + text[header.end():]
    return line + "\n" + text


# 완료 요청의 선행 검사 실패 사유를 반환한다. 통과하면 None이다.
def stage_completion_reason(status: Path, text: str | None = None) -> str | None:
    from sub_docs import sibling
    from sub_gate import transition_reason
    text = read(status) if text is None else text
    return transition_reason(status, normalize_stage(text, "완료"), normalize_stage(text, "구현"), read(sibling(status, "input")))


# status 경과 문서에 단계 접두어(prefix)를 기록하고 현재 단계를 반환한다. 접두어가 없거나 잘못되면 기록된 단계를 유지한다.
# 단계가 조사이면 빠진 research, refute, spec 문서만 생성하며 기존 본문은 보존한다.
def apply_stage(status: Path, prefix: str | None) -> str | None:
    text = read(status)
    if prefix == "완료" and stage_completion_reason(status, text):
        current = stage_of(normalize_stage(text))
        prefix = current if current and current != "완료" else "구현"
    updated = normalize_stage(text, prefix if prefix in STAGES else None)
    if updated != text:
        status.write_text(updated, encoding="utf-8")
    text = updated
    stage = stage_of(text)
    if stage == "조사" and TASK_ID.fullmatch(status.parent.name):
        title = re.search(r"(?m)^# (.+)$", text)
        root = status.parents[3]
        for kind in ("research", "refute", "review"):
            create(root, status.parent.name, title[1] if title else status.parent.name, kind)
    return stage


# 이 세션이 쓴 경과 문서에 사용자 마지막 메시지의 단계 접두어를 기록하고 현재 단계를 반환한다.
# 접두어가 없거나 잘못되면 기록된 단계를 유지한다. 경과 문서가 없으면 None이다.
def sync_stage(data: dict, runner: str = "claude", user: str | None = None) -> str | None:
    from check_workflow_response import written_status
    prefix = stage_prefix(user_message(data, runner) if user is None else user)
    status = written_status({k: v for k, v in data.items() if k != "tool_name"}, runner)
    return None if status is None else apply_stage(status, prefix)


# 요청 첫 줄의 task 값이 단계별 허용 목록 밖이면 사유를 반환한다. 허용이면 None이다.
def stage_call_reason(stage: str, prompt: str) -> str | None:
    kind = next((line.strip() for line in prompt.splitlines() if line.strip()), "")
    allowed = STAGE_CALLS.get(stage, ())
    return None if kind in allowed else REASON_STAGE_CALL.format(stage=stage, kind=kind, allowed=", ".join(allowed) or "없음")


REASON_APPROVAL = (
    "사용자 마지막 메시지에 승인 키워드가 없습니다. 작업 전 사용자 승인을 받으십시오. "
    + APPROVAL_RETRY + " "
    "사용자의 검토 없이 작업을 시도한 경우 `nestlab-log-incident` 스킬과 director 지침의 작업 문서 작성 절차를 따르십시오."
)

# 사용자 승인 키워드나 승인 범위 예외가 없으면 deny 사유를 반환한다.
def approval_reason(user: str, prompt: str, cwd: str | None = None) -> str | None:
    # 사용자 승인이 이미 있으면 사유 없이 통과시킨다.
    if approved(user):
        return None

    # 재구현과 재검증은 구현 승인, 조사와 반증은 조사 승인이 기록돼 있으면 허용한다.
    first = next((line.strip() for line in prompt.splitlines() if line.strip()), "")
    if first in IN_SCOPE and executed_approved(task_id(prompt), cwd):
        return None
    if (first in RESTORED_WORK and executed_approved(task_id(prompt), cwd)
            and reimplement_pending(task_id(prompt), cwd)):
        return None
    if first in RESEARCH_SCOPE and executed_approved(task_id(prompt), cwd, "조사 승인 범위"):
        return None
    return REASON_APPROVAL


# Claude Agent 호출 검사이다.
def check_agent_call(data: dict) -> str | None:
    # director의 호출만 검사한다.
    if data.get("agent_id") or resolve_role(data) != "director":
        return None

    # 승인 여부를 판정한다.
    _, prompt = call_info(data)
    user, _ = last_user(data.get("transcript_path"))

    # 경과 문서에 작업 단계가 있으면 단계별 task 값만 배정한다.
    stage = sync_stage(data, "claude", user)
    if stage:
        return stage_call_reason(stage, prompt)
    return approval_reason(user, prompt, data.get("cwd"))


# Codex 하위 thread의 도구 호출에서 부모 director의 승인을 검사한다.
def check_codex_child(data: dict) -> str | None:
    # 하위 thread 요청에 세션 기록 정보가 섞였는지 확인한다.
    path = data.get("transcript_path")
    leak = session_leak(first_prompt(path), data.get("cwd"), parent_thread(path), data.get("session_id"))
    if leak:
        return LEAK.format(leak=leak)

    # 부모 thread의 사용자 마지막 메시지로 승인을 판정한다.
    parent = codex_rollout(parent_thread(path))
    if not parent or first_line(parent).get("agent_role"):
        return None  # 부모가 메인 thread가 아님
    user, _ = last_user(parent)

    # 부모 세션의 경과 문서에 작업 단계가 있으면 단계별 task 값만 허용한다.
    prompt = first_prompt(path)
    stage = sync_stage({"transcript_path": parent, "cwd": data.get("cwd")}, "codex", user)
    if stage:
        return stage_call_reason(stage, prompt)
    return approval_reason(user, prompt, data.get("cwd"))
