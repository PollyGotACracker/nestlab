"""서브에이전트 호출 입력을 문서 경로와 식별자로 제한한다.

호출자: 
- hooks/check_tool_use.py
- hooks/check_tool_use/sub_approval.py(LEAK, session_leak)
입력: hook 입력 dict, 하위 요청 본문, runner 이름
반환: 
- check_input, session_leak은 deny 사유 문자열(session_leak은 해당 줄) 또는 통과 시 None
- format_reason은 배정 형식 위반 사유 문자열 또는 통과 시 None
- is_format, is_main은 bool
"""
from __future__ import annotations

import re

import sub_docs
from sub_paths import TASK_REL
from sub_call import AGENT_ROLES, CIPHER, DOCUMENTER, RESEARCHER, REVIEWER, ROLES, STAGE_FILE, STAGE_NAMES, WORKER_REVIEWER, call_info, is_followup
from sub_role import resolve_role
from sub_write import read

DOC_LINE = re.compile(rf"^input: ({TASK_REL}/[\w.\-]+/(?:status|spec|research|refute)\.md)\s*$", re.M)
DOCUMENT = re.compile(rf"^input: {TASK_REL}/[\w.\-]+/(research|refute|spec)\.md$")
ITEM_LINE = re.compile(r"^item: research \d+$")
# 구현과 재구현 배정을 요구사항 단위로 나눠 병렬 배정하는 줄이다.
REQ_LINE = re.compile(r"^item: requirement \d+(?:, \d+)*$")
TASK_KEY = re.compile(r"^task: (\S+)$")
# 대상 역할별로 허용하는 task 값이다.
ROLE_STAGES = ({"researcher": ("research",), "reviewer": ("refute", "verify", "reverify"),
                "worker": ("implement", "reimplement"), "documenter": ("implement", "reimplement")})


# 호출한 쪽이 메인 세션이면 True이다. Codex는 항상 True, Claude는 agent_id가 없을 때 True이다.
def is_main(data: dict, target: str, runner: str) -> bool:
    if runner == "codex":
        return True
    return not data.get("agent_id")


# prompt의 `task:`, `input:` 두 줄과 선택 item 줄(재조사 `item: research <번호>`, 구현과 재구현 `item: requirement <번호>`)의 위반 사유를 반환한다.
# 형식에 맞으면 None이다.
def format_reason(prompt: str, target: str = "") -> str | None:
    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
    if len(lines) < 2 or not (m := TASK_KEY.match(lines[0])):
        return REASON_UNSUPPORTED
    stage = m[1]
    if stage not in STAGE_NAMES:
        return REASON_UNSUPPORTED_TASK.format(task=stage)
    role = AGENT_ROLES.get(target, target)
    if role in ROLE_STAGES and stage not in ROLE_STAGES[role]:
        return REASON_STAGE_ROLE.format(role=role, task=stage)
    doc = DOCUMENT.match(lines[1])
    if not doc or doc[1] != STAGE_FILE.get(stage, "spec"):
        return REASON_DOCUMENT.format(task=stage, file=STAGE_FILE.get(stage, "spec"))
    rest = lines[2:]
    line = ITEM_LINE if stage == "research" else REQ_LINE if stage in ("implement", "reimplement") else None
    if rest and not (line and all(line.match(ln) for ln in rest)):
        return REASON_UNSUPPORTED
    return None


# prompt가 새 배정 형식이면 True이다.
def is_format(prompt: str, target: str = "") -> bool:
    return format_reason(prompt, target) is None


LEAK = "서브에이전트 입력과 전달되는 문서에서 사건 문서 경로와 세션 기록 정보(경로 및 세션 id)를 제거하십시오. 해당 부분은 {leak}입니다."

# text와 text가 가리키는 작업 문서(조사 단계 원문 절 제외)에서 기록 정보 형식이나 주어진 세션 id(전체 및 앞 8자)가 든 첫 줄이다.
def session_leak(text: str, cwd: str | None, *session_ids: str | None) -> str | None:
    # 검사할 세션 id와 본문(요청과 가리킨 문서)을 구한다.
    root = sub_docs.project_root(cwd)
    ids = {i for s in session_ids if s for i in (s, s[:8]) if len(i) >= 8}

    # 가리킨 문서는 조사 단계 원문 절과 반증 회차 기록 절을 빼고, 본문 줄마다 기록 정보와 세션 id를 찾는다.
    docs = [read(root / rel) for rel in DOC_LINE.findall(text)]
    for body in [text] + [d.replace(sub_docs.section(d, "조사 단계 원문"), "").replace(sub_docs.section(d, "회차 기록"), "")
                          for d in docs]:
        leak = sub_docs.record_leak(body)
        if leak:
            return leak
        for line in body.splitlines():
            if any(i in line for i in ids):
                return line.strip()[:120]
    return None


REASON_RESEARCHER_CALL = "researcher는 다른 에이전트를 호출할 수 없습니다."
REASON_UNSUPPORTED = ("지원하지 않는 형식입니다. 배정 요청은 소문자 두 줄 `task: <research | refute | implement | verify | reimplement | reverify>`와 "
                      f"`input: {TASK_REL}/<작업-id>/<문서>.md`만 허용합니다. 재조사는 `item: research <번호>`, "
                      "구현과 재구현은 `item: requirement <번호>[, <번호>]` 줄 하나를 더할 수 있습니다.")
REASON_UNSUPPORTED_TASK = "지원하지 않는 task 값 `{task}`입니다. research, refute, implement, verify, reimplement, reverify만 허용합니다."
REASON_STAGE_ROLE = "{role}에는 `task: {task}`를 배정할 수 없습니다."
REASON_DOCUMENT = f"`task: {{task}}`의 input은 `{TASK_REL}/<작업-id>/{{file}}.md`여야 합니다."
REASON_NO_SPAWN = "같은 세션 기록에서 이 task_name의 spawn_agent 호출을 찾지 못했습니다. 새 spawn_agent로 배정하십시오."
REASON_CIPHER = ("Codex spawn_agent의 task_name은 `<단계>__<작업 ID의 -를 _로 바꾼 값>` 형식이어야 합니다. "
                 "단계: research, refute, implement, verify, reimplement, reverify.")

# 위반 사유를 반환한다. 통과면 None이다.
def check_input(data: dict, runner: str) -> str | None:
    # 같은 세션 기록에서 담당을 찾지 못한 follow-up은 새 배정을 요구한다.
    target, prompt = call_info(data)
    if not target and is_followup(data.get("tool_name")):
        return REASON_NO_SPAWN
    if target not in ROLES:
        return None

    # Codex 호출의 본문이 task_name으로도 복원되지 않는 암호문이면 차단한다.
    if runner == "codex" and CIPHER.match(prompt.strip()):
        return REASON_CIPHER

    # 요청 본문과 가리킨 문서에 세션 기록 정보가 섞였는지 확인한다. 대체 요청이 가리키는 문서도 포함한다.
    text = "\n".join([v for v in (data.get("tool_input") or {}).values() if isinstance(v, str)] + [prompt])
    leak = session_leak(text, data.get("cwd"), data.get("session_id"))
    if leak:
        return LEAK.format(leak=leak)

    # researcher가 서브에이전트를 부르면 차단한다.
    if data.get("agent_id") and resolve_role(data) == "researcher":
        return REASON_RESEARCHER_CALL

    # 메인 세션이 아닌 호출은 reviewer 외에는 검사하지 않고, 나머지는 배정 형식을 확인한다.
    if target not in REVIEWER and not is_main(data, target, runner):
        return None
    return format_reason(prompt, target)
