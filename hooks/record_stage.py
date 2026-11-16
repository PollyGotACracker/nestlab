#!/usr/bin/env python3
"""UserPromptSubmit hook: 미전달 사건 문서 링크와 계획 단계 지침을 주입하고, 머리줄의 단계 키워드를 대상 작업의 경과 문서에 기록한다.

호출자: Claude Code와 Codex의 UserPromptSubmit hook 설정(설정 원본)
입력: hook 입력 JSON(prompt, transcript_path, cwd)을 stdin으로 받는다.
반환:
- 이 세션의 사건 문서 중 세션 기록에 링크가 아직 없는 것이 있으면 그 링크를 `additionalContext`로 stdout에 출력한다.
- 머리줄이 있으면 기록 결과나 수행할 행동을 `additionalContext`에 더한다.
  `[plan new]`는 신규 작업 생성 절차를, `[작업ID]`는 단계 변경 없이 그 작업을 현재 진행 작업으로 선택한 결과를 더한다.
  작업 문서가 없는 작업ID는 해당 작업이 없음을 알리게 한다.
- 머리줄 없이 `승인`을 보낸 메시지가 hook이 표시한 구현 승인 요청 바로 뒤이면 그 작업의 구현 승인과 구현 단계를 기록한다.
- 현재 진행 작업이 없으면 미완료 작업 목록과 작업 선택 질문 안내를 더한다.
- 색인의 현재 진행 작업이 계획 단계이면 계획 단계 지침과 계획 스킬의 `## 출력 검사` 절 원문을 더한다.
- 출력할 내용이 없으면 아무것도 출력하지 않고, 어느 경우에도 사용자 메시지를 차단하지 않는다.
한계:
- 첫 줄 시작의 `[plan new]`, `[작업ID]`, `[plan|implement|complete]`, `[키워드 작업ID]`를 머리줄로 인정한다.
- 작업ID 없는 키워드는 작업 색인(.agent-logs/task/index.md)의 현재 진행 작업을 대상으로 한다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "check_tool_use"))

import check_invalid_response
from sub_approval import NEW_TASK as NEW_TASK_ID, apply_stage, head_line, stage_completion_reason
from sub_docs import project_root
from sub_paths import INCIDENT_REL, SKILL_DIR, TASK_REL
from sub_role import director_main
from sub_task_index import selected_status, refresh_index
from sub_session import read_session
from sub_write import strings

TASK = r"\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*"
DESIGNATION = re.compile(rf"--task[ =]+({TASK})|{re.escape(TASK_REL)}/({TASK})/")
REASON_FORMAT = ("머리줄: 사용자에게 첫 줄 머리줄 형식이 `[plan new]`, `[작업ID]`, `[plan]`, `[implement]`, `[complete]` "
                 "또는 `[키워드 작업ID]`임을 알리십시오.")
REASON_NO_DOC = "작업 {task}: 작업 문서가 없습니다. 사용자에게 해당 작업이 없음을 알리십시오."
REASON_NO_TARGET = ("작업 색인: 현재 진행 작업이 없습니다. 프로젝트 작업 요청이면 아래 미완료 작업 중 요청과 관련된 작업을 "
                    "question-task 양식의 선택지로 제시하고, 기존 작업으로 진행할지 신규 작업을 착수할지 질문하십시오.")
NEW_TASK = ("신규 작업: director 지침의 계획 단계 절차대로 KST 시각과 기준 커밋을 확인하고 "
            "`nestlab-task-docs --task <YYYYMMDD-HHMMSS-name> --title \"<작업명>\" --stage plan`으로 작업 문서를 생성하십시오.")
RECORDED = "작업 {task}의 작업 단계를 `{stage}`로 기록했습니다."
SELECTED = "작업 {task}를 현재 진행 작업으로 선택했습니다. 작업 단계는 `{stage}`로 유지합니다."
RECORDED_APPROVAL = "작업 {task}의 구현 승인과 작업 단계 `구현`을 기록했습니다. 명세 요구사항대로 구현을 배정하십시오."
NOTICE_AGENT = ("사건 문서 링크: {link}. 직전 응답의 실수 인정 표현으로 기록된 사건이며 분석 세션이 분석합니다.\n"
                "감지 기록의 응답 발췌를 읽고, 현재 작업을 계속하며 다음 응답부터 같은 행동을 바로잡으십시오.")
PLAN_NOTICE = ("열지 않은 코드와 확인하지 않은 외부 사실은 추측하지 않는다. 답하기 전에 관련 파일을 읽고, "
               "설계 판단에 필요한 외부 사실은 자료 유형에 맞는 원문(공식 문서, 공개 구현, 이슈)과 반대 자료 본문을 직접 열어 확인한다.\n"
               "병렬로 실행할 수 있는 독립 작업은 서브에이전트에 나눠 동시에 실행한다.\n"
               "몇 번의 도구 호출로 끝나는 확인은 서브에이전트를 기다리지 않고 직접 처리한다.")
# 계획 스킬 경로
PLAN_SKILL = SKILL_DIR / "nestlab-plan" / "SKILL.md"


# 이 세션의 사건 문서 중 세션 기록에 링크가 아직 없는 문서의 안내 문장 목록이다.
def undelivered_incidents(data: dict) -> list[str]:
    sid = (data.get("session_id") or "unknown")[:8]
    folder = check_invalid_response.INCIDENT_ROOT / INCIDENT_REL
    try:
        sent = Path(data.get("transcript_path") or "").read_text(encoding="utf-8")
    except (OSError, ValueError):
        sent = ""
    notices = []
    for path in sorted(folder.glob(f"*-{sid}.md")):
        notice = NOTICE_AGENT.format(link=check_invalid_response.incident_link(path, data.get("cwd")))
        # 세션 기록의 줄바꿈은 이스케이프되므로 첫 줄로 전달 여부를 판정한다.
        if notice.splitlines()[0] not in sent:
            notices.append(notice)
    return notices


# 계획 스킬의 `## 출력 검사` 절 원문을 반환한다. 스킬이나 절이 없으면 빈 문자열이다.
def plan_checks() -> str:
    text = PLAN_SKILL.read_text(encoding="utf-8-sig") if PLAN_SKILL.is_file() else ""
    found = re.search(r"(?ms)^## 출력 검사[ \t]*\n.*?(?=^## |\Z)", text)
    return found[0].strip() if found else ""


# 색인의 현재 진행 작업이 계획 단계이면 계획 단계 지침과 출력 검사 절을 반환한다. 아니면 None이다.
def plan_context(data: dict) -> str | None:
    from sub_approval import stage_of
    status = selected_status(project_root(data.get("cwd")))
    if status is None or stage_of(status.read_text(encoding="utf-8-sig")) != "계획":
        return None
    return "\n\n".join(part for part in (PLAN_NOTICE, plan_checks()) if part)


# 미전달 사건 문서 링크, 머리줄 단계 기록 결과, 계획 단계 지침을 이은 additionalContext 문장을 반환한다. 모두 없으면 None이다.
# 단계 기록과 계획 지침은 director 메인 세션에만 적용하고, 사건 문서 링크는 모든 역할에 주입한다.
def decide(data: dict) -> str | None:
    parts = undelivered_incidents(data)
    lead = director_main(data)
    # 사용자 메시지마다 작업 폴더의 경과 문서로 색인 목록을 다시 구성한다.
    root = project_root(data.get("cwd"))
    if lead and (root / TASK_REL).is_dir():
        try:
            refresh_index(root)
        except ValueError:
            pass
    staged = (decide_stage(data) or record_approval(data) or unselected(data)) if lead else None
    plan = plan_context(data) if lead else None
    return "\n".join(parts + [part for part in (staged, plan) if part]) or None


# 직전 응답의 마지막 assistant 텍스트에 hook이 표시한 구현 승인 요청 줄이 있고 사용자 메시지가 승인이면,
# 그 작업 경과 문서에 구현 승인과 구현 단계를 기록하고 결과 문장을 반환한다. 해당하지 않으면 None이다.
def record_approval(data: dict) -> str | None:
    from datetime import datetime, timedelta, timezone
    from sub_approval import approved
    from sub_gate import ACK_LINE, REQUEST_LINE, approval_request, approved_status, live_numbers
    from sub_write import read
    if not approved(data.get("prompt") or ""):
        return None
    # Codex는 요청 줄 뒤에 hook 안내 확인 줄을 쓰므로 확인 줄은 건너뛴다.
    shown = next((e["text"] for e in reversed(read_session(data.get("transcript_path") or ""))
                  if e["role"] == "assistant" and e["text"].strip() and e["text"].strip() != ACK_LINE), "")
    request = REQUEST_LINE.search(shown)
    if not request:
        return None

    # 요청 뒤 작업 문서가 바뀌어 검사를 통과하지 못하면 승인을 기록하지 않는다.
    root, task = project_root(data.get("cwd")), request[1]
    reason, _ = approval_request(root, task)
    if reason:
        return f"작업 {task}: 구현 승인을 기록하지 못했습니다. {reason}"
    status = root / TASK_REL / task / "status.md"
    stamp = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
    status.write_text(approved_status(read(status), stamp, live_numbers(read(status.with_name("spec.md")))), encoding="utf-8")
    recorded = apply_stage(status, "구현")
    refresh_index(root, task, current=True)
    if recorded != "구현":
        return f"작업 {task}: 구현 승인을 기록했지만 구현 단계로 기록하지 못했습니다. {implement_reason(status)}"
    return RECORDED_APPROVAL.format(task=task)


# 현재 진행 작업이 없을 때의 작업 선택 안내와 색인의 미완료 작업 줄 목록을 반환한다.
def no_target(root: Path) -> str:
    from sub_task_index import PENDING, ITEM, index_text, split_index
    try:
        lines = [line for line in split_index(index_text(root))[1][PENDING] if ITEM.match(line)]
    except ValueError:
        lines = []
    return "\n".join([REASON_NO_TARGET, "미완료 작업 목록:", *(lines or ["- 없음"])])


# 머리줄이 없는 메시지에서 현재 진행 작업이 없으면 작업 선택 안내를 반환한다. 현재 진행 작업이 있으면 None이다.
def unselected(data: dict) -> str | None:
    root = project_root(data.get("cwd"))
    if head_line(data.get("prompt") or "")[0] is not None or not (root / TASK_REL).is_dir() or selected_status(root):
        return None
    return no_target(root)


# 경과 문서가 구현 단계 기록 조건을 채우지 못한 첫 위반 위치와 양식 이름을 반환한다. 위반이 없으면 빈 문자열이다.
def implement_reason(status: Path) -> str:
    from sub_document import Document
    from sub_docs import constraint_reason, structure_reason
    from sub_gate import check_causes
    text = status.read_text(encoding="utf-8-sig")
    try:
        check_causes(Document(text))
    except ValueError as error:
        return str(error)
    return structure_reason(text, "state") or constraint_reason(text, "state") or ""


# 머리줄을 판정해 경과 문서에 작업 단계를 기록하고 결과 문장을 반환한다. 머리줄이 없으면 None이다.
def decide_stage(data: dict) -> str | None:
    stage, task = head_line(data.get("prompt") or "")
    if stage is None:
        return None
    if stage == "보류":
        return REASON_FORMAT

    if task == NEW_TASK_ID:
        return NEW_TASK

    # 대상 작업은 머리줄 작업ID, 색인의 현재 진행 작업 순으로 정한다. 머리줄에 지정한 작업은 현재 진행 작업으로 선택한다.
    root = project_root(data.get("cwd"))
    named = task is not None
    selected = selected_status(root)
    task = task or (selected.parent.name if selected else None)
    if task is None:
        return no_target(root)
    status = root / TASK_REL / task / "status.md"
    if not status.is_file():
        return REASON_NO_DOC.format(task=task)
    recorded = apply_stage(status, stage)
    refresh_index(root, task, current=True if named else None)
    if not stage:
        return SELECTED.format(task=task, stage=recorded)
    if stage == "구현" and recorded != "구현":
        return f"작업 {task}: 구현 단계로 기록하지 못했습니다. {implement_reason(status)}"
    if stage == "완료" and recorded != "완료":
        return f"작업 {task}: {recorded} 단계를 유지합니다. 다음 완료 조건을 채운 뒤 `[complete]`를 다시 요청하십시오. {stage_completion_reason(status)}"
    return RECORDED.format(task=task, stage=recorded)


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        context = decide(json.load(sys.stdin))
    except Exception:
        return 0
    if context:
        json.dump({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}},
                  sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
