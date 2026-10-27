"""역할별 쓰기 범위와 작업 문서 쓰기를 검사한다.

호출자: hooks/check_tool_use.py(check_write)
입력: hook 입력 dict, 역할 이름, 쓰기 대상 경로 목록, 메인 세션 여부
반환: check_write, check_doc은 deny 사유 문자열 또는 통과 시 None
한계: Bash로 실행한 스크립트가 직접 여는 파일은 판정하지 못한다.
"""
from __future__ import annotations

import re
from pathlib import Path

from sub_approval import APPROVED, approved, last_user
from sub_docs import doc_kind, missing_titles, project_root, record_leak, review_done, section, sibling, task_doc, task_id
from sub_path import command_paths, in_incident, in_temp
from sub_role import first_prompt, main_session, resolve_role
from sub_session import agent_transcript, read_session, turn
from sub_plan import check_plan
from sub_judgment import (check_decision, check_item_names, check_judgment, check_raw, check_review_done, check_stage,
                          review_items, status_of)
from sub_write import RETRY, added_lines, bash_command, bash_write_reason, is_write_bash, read, strings
from sub_workflow import R_SOURCE_FORMAT, bad_source, transition_reason

READONLY = {"researcher", "reviewer"}
LOG = ("log/",)
UNRESOLVED = "중단 판정 미해결"
SKILL_OF = {"state": "nestlab-log-state", "input": "nestlab-log-spec",
            "research": "nestlab-log-research", "refute": "nestlab-log-refute"}


# 저장소 루트 기준 상대 경로를 반환한다. 루트 밖이면 빈 문자열이다.
def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return ""


REASON_NO_APPROVAL = "사용자 마지막 메시지에 '승인'이 없어 승인 상태를 승인으로 쓸 수 없습니다.\n승인 요청 지침과 스킬을 확인하십시오. " + RETRY
REASON_UNRESOLVED = "결정 기록의 `중단 판정 미해결` 줄은 지우거나 바꿀 수 없습니다. " + RETRY
REASON_MISSING_TITLES = "작업 문서에 {skill} 템플릿의 필수 절({titles})이 없습니다. " + RETRY

# 작업 문서 한 개 쓰기 검사이다.
def check_doc(data: dict, path: Path, r: str, user: str) -> str | None:
    # 작업 문서가 아니면 검사하지 않는다.
    kind = doc_kind(r)
    if kind is None:
        return None
    added, new = added_lines(data, path)
    old = read(path)

    # 승인 줄은 사용자 승인 명령이 있을 때만 쓸 수 있다.
    if any(APPROVED.match(line) for line in added) and not approved(user):
        return REASON_NO_APPROVAL

    # 템플릿 필수 절이 빠지면 막는다.
    if new is not None:
        lost = missing_titles(new, SKILL_OF[kind], old)
        if lost:
            return REASON_MISSING_TITLES.format(titles=", ".join(lost[:5]), skill=SKILL_OF[kind])

    # 문서 종류별 검사를 한다.
    transcript = data.get("transcript_path") or ""
    if kind == "refute":
        return check_raw(new, old, blank_ok=True)
    if kind == "research":
        # 새로 들어가는 줄의 출처가 URL 또는 파일:행 형식인지 확인한다.
        bad = next((b for b in map(bad_source, added) if b is not None), None)
        if bad is not None:
            return R_SOURCE_FORMAT.format(value=bad[:40])
        return check_raw(new, old) or check_judgment(path, new, old, transcript)
    if kind == "state":
        plan = read(sibling(path, "input"))
        # 이전 본문의 중단 판정 미해결 줄이 새 본문에 그대로 있어야 한다.
        if new is not None and any(UNRESOLVED in l and l not in new.splitlines() for l in old.splitlines()):
            return REASON_UNRESOLVED
        return (check_plan(new, old, plan) or check_decision(path, new, old)
                or check_review_done(new, old, transcript) or check_stage(path, new, old, transcript) or check_item_names(new, old)
                or transition_reason(path, new, old, plan))
    return None


# 쓰기 대상 경로가 요구하는 스킬 이름을 반환한다. 대상이 아니면 None이다.
def skill_for(r: str) -> str | None:
    kind = doc_kind(r)
    if kind is not None:
        return SKILL_OF[kind]
    if r.startswith("log/incident/") and r.endswith(".md"):
        return "nestlab-log-incident"
    name = r.rsplit("/", 1)[-1]
    if (name in ("AGENTS.md", "AGENTS.global.md", "CLAUDE.md") or (r.startswith("shared/skills/") and name == "SKILL.md")
            or (r.startswith(".claude/agents/") and name in ("MAIN.md", "SUB.md"))):
        return "nestlab-rule"
    return None


READ_CMD = re.compile(r"\b(?:Get-Content|cat|type|sed|head|less|more)\b", re.I)


# 도구 입력이 읽기 명령으로 `<skill>/SKILL.md`를 읽는 호출이면 True이다.
def reads_skill(args, skill: str) -> bool:
    text = "\n".join(strings(args))
    return bool(READ_CMD.search(text) and re.search(re.escape(skill) + r"[\\/]+SKILL\.md", text, re.I))


# 해당 스킬 호출이 있는지 판정한다. whole이 거짓이면 현재 턴만, 참이면 세션 전체 기록을 본다.
# Codex는 읽기 명령으로 `<skill>/SKILL.md`를 읽은 호출이다.
def skill_called(data: dict, skill: str, runner: str = "claude", whole: bool = False) -> bool:
    # 대화 기록의 호출 범위를 읽는다.
    entries = read_session(agent_transcript(data) or data.get("transcript_path") or "")

    # Claude는 스킬 도구 호출을, Codex는 읽기 명령 호출을 찾는다.
    for e in (entries if whole else turn(entries)[2]):
        for name, args in e["calls"]:
            if name == "Skill":
                called = args.get("skill") or ""
                if called == skill or called.endswith(":" + skill):
                    return True
            elif runner == "codex" and name != "apply_patch" and reads_skill(args, skill):
                return True
    return False


# director 메인 세션이 세션 기록에 nestlab-kickoff 호출 없이 도구를 부르는 중이면 True이다.
def kickoff_missing(data: dict, runner: str = "claude") -> bool:
    if not main_session(data) or resolve_role(data) != "director":
        return False
    return not skill_called(data, "nestlab-kickoff", runner, whole=True)


# Bash 쓰기 명령의 경로 토큰 중 스킬이 필요한 log/ 문서 경로를 반환한다.
def bash_log_docs(data: dict, root: Path) -> list[Path]:
    cwd = Path(data.get("cwd") or ".")
    paths = (cwd / token for token, _ in command_paths(bash_command(data)))
    return [p for p in paths if rel(p, root).startswith(LOG) and skill_for(rel(p, root))]


WRITE_ROLES = {"worker", "documenter"}
IN_PROGRESS = re.compile(r"^- 작업 상태:\s*진행\s*$", re.M)
EXEC_APPROVED = re.compile(r"^\s*- (?:사용자 )?승인 상태:\s*승인\s*$", re.M)
REASON_LOG_BASH = "이 쓰기 명령은 log/ 폴더 경로를 포함합니다. " + RETRY
REASON_REVIEW_LEFT = "진행 중인 작업의 검토 필요 항목({items})이 끝나지 않아 log/ 밖에 쓸 수 없습니다. " + RETRY


# 작업 상태가 진행이고 실행 승인이 승인인 작업 문서 본문 목록을 반환한다.
def running_states(root: Path) -> list[str]:
    out = []
    for path in sorted(root.glob("log/task/*/status.md")) + sorted(root.glob("log/state/*.md")):
        if path.name.endswith(("-input.md", "-research.md")):
            continue
        text = read(path)
        if IN_PROGRESS.search(text) and EXEC_APPROVED.search(section(text, "실행 승인 범위")):
            out.append(text)
    return out


# 진행 중인 작업의 검토 필요 항목 중 허용 값이 아닌 상태의 항목 이름 목록을 반환한다. 허용 값을 읽지 못하면 ValueError이다.
def unreviewed_items(root: Path) -> list[str]:
    states = running_states(root)
    if not states:
        return []
    done = review_done()
    return [name for text in states for name, item in review_items(text).items() if status_of(item) not in done]


# 실행 승인 뒤의 worker나 documenter 세션이면 True이다. 첫 요청의 명세 문서로 작업을 찾아 실행 승인 기록을 본다.
def write_bash_free(data: dict, role: str | None) -> bool:
    if role not in WRITE_ROLES or main_session(data):
        return False
    root = project_root(data.get("cwd"))
    task = task_id(first_prompt(agent_transcript(data) or data.get("transcript_path")))
    if not task:
        return False
    return bool(EXEC_APPROVED.search(section(read(task_doc(root, task, "state")), "실행 승인 범위")))


# 쓰기 Bash 명령의 경로 토큰이 log/ 아래면 사유를 반환한다. 쓰기 명령이 아니거나 log/ 경로가 없으면 None이다.
def log_bash_reason(data: dict) -> str | None:
    command = bash_command(data)
    if not (is_write_bash(command) or bash_write_reason(command)):
        return None
    root = project_root(data.get("cwd"))
    cwd = Path(data.get("cwd") or ".")
    if any(rel(cwd / token, root).startswith(LOG) for token, _ in command_paths(command)):
        return REASON_LOG_BASH
    return None


REASON_READONLY = "{role}은 읽기 전용입니다. " + RETRY
REASON_OUTSIDE_LOG = "사용자 마지막 메시지에 승인 명령이 없어 메인 세션은 log/ 경로만 쓸 수 있습니다. " + RETRY
REASON_SCRIPT_FILE = "승인 전에는 log/와 OS 임시 폴더에 스크립트 파일을 쓸 수 없습니다. " + RETRY
REASON_NO_SKILL = "{skill} 스킬을 이번 턴에 호출하지 않았습니다. " + RETRY
SCRIPT_EXT = (".py", ".sh", ".ps1", ".js", ".bat", ".cmd")
VERDICT_ITEM = re.compile(r"^- \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:")


# 경과 문서 쓰기 후 `검증 결과` 절에 새로 생기는 `- YYYY-MM-DD HH:MM:SS:` 항목 줄 목록을 반환한다.
def new_verdict_items(data: dict, path: Path) -> list[str]:
    _, new = added_lines(data, path)
    if new is None:
        return []
    before = section(read(path), "검증 결과").splitlines()
    return [l for l in section(new, "검증 결과").splitlines() if VERDICT_ITEM.match(l) and l not in before]


REASON_RECORD = "작업 문서에 사건 문서 경로와 세션 기록 정보를 쓸 수 없습니다. " + RETRY

# 역할별 쓰기 범위를 검사한다. 위반이면 deny 사유를, 통과면 None을 반환한다.
def check_write(data: dict, role: str | None, paths: list[Path], main: bool = False, runner: str = "claude") -> str | None:
    # 읽기 전용 역할은 모든 쓰기를 막는다.
    if role in READONLY:
        return REASON_READONLY.format(role=role)
    root = project_root(data.get("cwd"))
    user, _ = last_user(data.get("transcript_path"))

    # 메인 세션은 역할과 무관하게 log/ 밖 쓰기에 사용자 승인이 필요하다.
    guarded = main
    if guarded:
        # 승인 전에는 log/와 OS 임시 폴더에 스크립트 파일을 쓸 수 없다.
        if not approved(user) and any(p.suffix.lower() in SCRIPT_EXT and (rel(p, root).startswith(LOG) or in_temp(str(p)))
                                      for p in paths):
            return REASON_SCRIPT_FILE
        outside = not paths or any(not rel(p, root).startswith(LOG) and not in_temp(str(p)) and not in_incident(str(p))
                                   for p in paths)
        if outside and not approved(user):
            return REASON_OUTSIDE_LOG

        # 승인 뒤에도 진행 중인 작업에 허용 값이 아닌 검토 필요 항목이 있으면 director의 쓰기를 막는다.
        if outside and role == "director":
            try:
                left = unreviewed_items(root)
            except ValueError as error:
                return str(error)
            if left:
                return REASON_REVIEW_LEFT.format(items=", ".join(left[:3]))

    # Bash 쓰기 명령이 가리키는 작업 문서도 스킬 호출을 확인한다.
    if data.get("tool_name") == "Bash":
        for path in bash_log_docs(data, root):
            skill = skill_for(rel(path, root))
            if not skill_called(data, skill, runner):
                return REASON_NO_SKILL.format(skill=skill)

    # 대상마다 기록 정보와 작업 문서 규칙을 검사한다.
    for path in paths:
        r = rel(path, root)
        skill = skill_for(r)
        if skill and not skill_called(data, skill, runner):
            return REASON_NO_SKILL.format(skill=skill)
        # 경과 문서 검증 결과에 새 항목을 쓰면 판정 스킬 호출도 요구한다.
        if doc_kind(r) == "state" and new_verdict_items(data, path) and not skill_called(data, "nestlab-verdict", runner):
            return REASON_NO_SKILL.format(skill="nestlab-verdict")
        if guarded and r.startswith(LOG) and not r.startswith("log/incident/") and record_leak("\n".join(added_lines(data, path)[0])):
            return REASON_RECORD
        reason = check_doc(data, path, r, user)
        if reason:
            return reason
    return None
