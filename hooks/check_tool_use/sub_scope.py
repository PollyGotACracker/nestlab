"""역할별 쓰기 범위와 작업 문서 쓰기를 검사한다.

호출자: hooks/check_tool_use.py(check_write)
입력: hook 입력 dict, 역할 이름, 쓰기 대상 경로 목록, 메인 세션 여부
반환: check_write, check_doc은 deny 사유 문자열 또는 통과 시 None
한계: Bash로 실행한 스크립트가 직접 여는 파일은 판정하지 못한다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sub_approval import APPROVED, approved, last_user
from sub_docs import (FENCE_LINE, HEADING, decision_path, doc_kind, measure_pending, missing_titles, project_root, record_leak, review_done, section, sibling, strip_comments, task_doc,
                      task_id, template_section, template_text, titles)
from sub_path import command_paths, in_incident, in_temp
from sub_role import first_prompt, main_session
from sub_session import _claude_entry, _codex_entry, agent_transcript, question_answers, read_session, turn, user_messages
from sub_spec import blank_cells, cells
from sub_judgment import DONE, check_judgment, check_raw, check_stage, early_items, review_items, status_of, unanswered
from sub_write import APPROVAL_RETRY, RETRY, added_lines, bash_command, bash_write_reason, is_write_bash, read, strings
from sub_workflow import R_SOURCE_FORMAT, bad_source, bare, filled, option_gap, transition_reason

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


REASON_NO_APPROVAL = "사용자 마지막 메시지에 '승인'이 없어 승인 상태를 승인으로 쓸 수 없습니다.\n" + APPROVAL_RETRY
REASON_UNRESOLVED = "중단 사유의 `중단 판정 미해결` 줄은 지우거나 바꿀 수 없습니다. " + RETRY
REASON_LOCKED = ("경과 문서의 작업 상태 줄, 요구사항 목록과 검토 필요 절은 hook만 바꿀 수 있습니다. "
                 "작업 상태는 hook이 계산해 쓰고, 요구사항과 검토 항목은 질문 도구로 확정받으십시오.")
LOCKED = ("요구사항 목록", "검토 필요")
STATE_LINE = re.compile(r"^- 작업 상태:.*$", re.M)


# 문서의 `- 작업 상태:` 첫 줄이다. 없으면 빈 문자열이다.
def state_line(text: str) -> str:
    m = STATE_LINE.search(text)
    return m.group(0).strip() if m else ""


# 경과 문서 쓰기 후 전문에서 작업 상태 줄, 요구사항 목록과 검토 필요 절이 기존 문서와 다르면 True이다.
# 새 문서는 이 줄과 두 절이 경과 문서 양식 원문과 같아야 한다. 전문을 구할 수 없는 apply_patch는 True이다.
def state_locked(data: dict, path: Path, new: str | None, old: str) -> bool:
    if new is None:
        return True

    # 기존 문서는 기존 본문과, 새 문서는 양식 원문과 작업 상태 줄 및 절 본문을 주석 없이 비교한다.
    new, old = strip_comments(new), strip_comments(old)
    if state_line(new) != state_line(old or strip_comments(template_text(SKILL_OF["state"]))):
        return True
    return any(section(new, t).strip() != strip_comments(section(old, t) if old else template_section(SKILL_OF["state"], t)).strip()
               for t in LOCKED)
REASON_SPEC_LOCKED = ("명세 문서 요구사항 절의 기대 결과와 검토 필요 칸은 hook만 바꿀 수 있습니다. "
                      "질문 도구로 `요구사항 <번호>. <이름>: <확인 가능한 결과 한 문장>` 문장을 확정받으십시오.")


# 명세 문서 쓰기 후 전문에서 요구사항 절의 기대 결과 칸이 기존 문서와 다르면 True이다.
# 기존에 없던 절과 새 문서는 양식 자리표시 값과 같아야 한다. 전문을 구할 수 없는 apply_patch는 True이다.
def spec_locked(data: dict, path: Path, new: str | None, old: str) -> bool:
    if new is None:
        return True

    # 번호별 기대 결과 칸 값을 비교한다. 지워진 절은 바뀐 것으로 본다.
    got, was, blank = cells(section(new, "요구사항 목록")), cells(section(old, "요구사항 목록")), blank_cells()
    if any(got.get(n) != (was.get(n) or blank) for n in got.keys() | was.keys()):
        return True
    if "검토" in titles(new):
        before = section(old, "검토 필요") if old else template_section(SKILL_OF["input"], "검토 필요")
        return strip_comments(section(new, "검토 필요")).strip() != strip_comments(before).strip()
    return False


REASON_PLAN_LOCKED = ("실행 승인 뒤에는 명세 문서의 `재작업 요청`, `재검증 요청` 밖 절과 조사 문서의 `검토 보고` 절을 고칠 수 없습니다. "
                      "계획이나 검토 근거를 바꾸려면 질문 도구로 요구사항 변경을 확정받아 승인을 다시 받으십시오.")
OPEN_TITLES = ("재작업 요청", "재검증 요청")
EXEC_DONE = re.compile(r"^\s*- (?:사용자 )?승인 상태:\s*승인\s*$", re.M)


# 명세 문서 전문에서 `재작업 요청`, `재검증 요청` 절 본문을 뺀 텍스트이다.
def without_open(text: str) -> str:
    out, level, fence = [], None, ""
    for line in text.splitlines():
        # 코드 블록 안의 줄은 제목으로 보지 않는다.
        f = FENCE_LINE.match(line)
        if f and not fence:
            fence = f.group(1)
        elif f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence):
            fence = ""
        m = None if fence or f else HEADING.match(line)
        if m and level is not None and len(m.group(1)) <= level:
            level = None
        if m and level is None and m.group(2).startswith(OPEN_TITLES):
            level = len(m.group(1))
        elif level is None:
            out.append(line)
    return "\n".join(out)


# 같은 작업의 경과 문서 실행 승인이 승인이고 문서 계획 절이 바뀌는 쓰기면 True이다. kind는 명세 문서 `input` 또는 조사 문서 `research`이다.
def plan_locked(path: Path, kind: str, new: str | None, old: str) -> bool:
    status = sibling(path, "state")
    if not status.is_file() or not EXEC_DONE.search(section(read(status), "실행 승인 범위")):
        return False
    if new is None:
        return True
    if kind == "input":
        return without_open(new) != without_open(old)
    return section(new, "검토 보고") != section(old, "검토 보고")


REASON_SPEC_EARLY = "{items}이(가) 끝나지 않아 명세 문서를 쓸 수 없습니다. 모든 검토를 끝낸 뒤 쓰십시오."
REASON_UNCONFIRMED = ("기존 요구 구분 `director 해석 (검토 {number})`의 검토 완료 근거가 없어 쓸 수 없습니다. "
                      "새 해석은 `문제 정의 확인: <보고한 문제 정의와 범위> (작업 MMDD-HHMMSS)` 질문으로 확인받고 "
                      "실제 사용자 확인 시각과 확인 내용을 기록하십시오.")
REASON_USER_QUOTE = "작업 문서에 사용자 메시지 원문({quote}…)이 들어 있습니다. 요약해서 쓰십시오."
INTERPRETED = re.compile(r"^\s*(?:- )?요구 구분:\s*director 해석 \(검토 (\d+)\)", re.M)
QUOTE_MIN = 20


# 추가 줄을 공백 정규화해 세션 사용자 메시지와 QUOTE_MIN자 이상 연속 일치하는 첫 부분을 반환한다. 없으면 None이다.
def user_quote(lines: list[str], data: dict) -> str | None:
    said = " ".join(" ".join(user_messages(read_session(data.get("transcript_path") or ""))).split())
    for line in lines:
        line = " ".join(line.split())
        for i in range(len(line) - QUOTE_MIN + 1):
            if line[i:i + QUOTE_MIN] in said:
                return line[i:i + QUOTE_MIN]
    return None


# 경과 문서에 새로 쓰는 `요구 구분: director 해석 (검토 N)` 줄의 검토 N이 기존 문서에서 검토 완료가 아니면 번호를 반환한다. 아니면 None이다.
def unconfirmed(added: list[str], old: str) -> str | None:
    wanted = [m[1] for m in map(INTERPRETED.match, added) if m]
    if not wanted:
        return None
    done = {m[1] for name, item in review_items(old).items() if (m := re.match(r"검토 (\d+)", name)) and status_of(item) == DONE}
    return next((n for n in wanted if n not in done), None)


PROBLEM_QUESTION = re.compile(r"^문제 정의 확인: .+ \(작업 (\d{4}-\d{6})\)$")
PROBLEM_TIME = re.compile(r"^director 해석 \(사용자 확인 (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\)$")
PROBLEM_YES = re.compile(r"(?:확인|확정|동의|맞습니다|맞아요|맞아|네|예|좋습니다)(?:합니다|했습니다|했어요)?[.!~]*")
REASON_PROBLEM_CONFIRM = ("문제 정의의 `{item}`에 같은 작업의 실제 사용자 확인 답과 확인 내용이 필요합니다. "
                          "`문제 정의 확인: <보고한 문제 정의와 범위> (작업 MMDD-HHMMSS)` 질문으로 확인받고 실제 답 시각과 요약을 기록하십시오.")


# 같은 작업의 마지막 문제 정의 확인 질문에 대한 긍정 답 시각을 KST로 반환한다.
def problem_confirmed(data: dict, path: Path) -> set[str]:
    tag = path.parent.name[4:15]
    calls, confirmed_times = [], set()
    for line in read(Path(data.get("transcript_path") or "")).splitlines():
        try:
            row = json.loads(line)
            entry = (_codex_entry if "payload" in row else _claude_entry)(row)
            if entry is None:
                continue
            for name, args in entry["calls"]:
                if name in ("AskUserQuestion", "request_user_input") and any(
                        (match := PROBLEM_QUESTION.fullmatch(" ".join(str(q.get("question") or "").split()))) and match[1] == tag
                        for q in (args or {}).get("questions", []) if isinstance(q, dict)):
                    confirmed_times.clear()
            if entry["calls"]:
                calls.append(entry)
            for question, answer in question_answers(calls + [entry]) if entry.get("replies") else []:
                match = PROBLEM_QUESTION.fullmatch(" ".join(str(question.get("question") or "").split()))
                if not match or match[1] != tag:
                    continue
                answer = re.sub(r"\s*\(Recommended\)$", "", answer).strip()
                if not PROBLEM_YES.fullmatch(answer):
                    continue
                stamp = datetime.fromisoformat(str(row.get("timestamp") or "").replace("Z", "+00:00"))
                if stamp.tzinfo is not None:
                    stamp = stamp.astimezone(timezone(timedelta(hours=9)))
                confirmed_times.add(stamp.strftime("%Y-%m-%d %H:%M:%S"))
        except (ValueError, TypeError, AttributeError):
            continue
    return confirmed_times


# 새 문제 정의의 확인 요약과 실제 답을 대조한다. 기존 검토 번호 기록은 기존 검사를 유지한다.
def problem_confirmation_gap(data: dict, path: Path, new: str, old: str) -> str | None:
    blocks = lambda text: [block for block in re.split(r"(?m)^- ", section(text, "문제 정의"))[1:] if block.strip()]
    previous = {block.splitlines()[0]: block.strip() for block in blocks(old)}
    confirmed_times = None
    for block in blocks(new):
        name = block.splitlines()[0]
        kind = re.search(r"(?m)^\s*- 요구 구분:\s*(.+)$", block)
        if previous.get(name) == block.strip() or not filled(name.split(":", 1)[0]) or INTERPRETED.search(block):
            continue
        match = PROBLEM_TIME.fullmatch(kind[1].strip()) if kind else None
        if not kind or (not match and not re.fullmatch(r"사용자 지시 \(로그 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\)", kind[1].strip())):
            return REASON_PROBLEM_CONFIRM.format(item=bare(name))
        summary = re.search(r"(?m)^\s*- 확인 내용:[ \t]*(.*)$", block)
        stamp = re.search(r"(?m)^\s*- 사용자 확인 시각:[ \t]*(.*)$", block)
        if confirmed_times is None:
            confirmed_times = problem_confirmed(data, path)
        if (not summary or not filled(summary[1]) or not stamp or stamp[1].strip() not in confirmed_times
                or (kind[1].startswith("director 해석") and (not match or match[1] != stamp[1].strip()))):
            return REASON_PROBLEM_CONFIRM.format(item=bare(name))
    return None
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
    if kind == "input":
        # 모든 검토가 끝나기 전에는 명세 문서를 쓸 수 없다. 경과 문서가 없으면 건너뛴다.
        status = sibling(path, "state")
        try:
            left = early_items(status) if status.is_file() and "검토" not in titles(new or "") else []
        except ValueError as error:
            return str(error)
        if left:
            return REASON_SPEC_EARLY.format(items=", ".join(left[:3]))
        if spec_locked(data, path, new, old):
            return REASON_SPEC_LOCKED
        if plan_locked(path, kind, new, old):
            return REASON_PLAN_LOCKED
        gap = option_gap(new, old) if new is not None else None
        if gap:
            return gap
    if kind == "refute":
        return check_raw(new, old, blank_ok=True)
    if kind == "research":
        # 새로 들어가는 줄의 출처가 URL 또는 파일:행 형식인지 확인한다.
        bad = next((b for b in map(bad_source, added) if b is not None), None)
        if bad is not None:
            return R_SOURCE_FORMAT.format(value=bare(bad[:40]))
        gap = option_gap(new, old) if new is not None else None
        if gap:
            return gap
        return check_raw(new, old) or check_judgment(path, new, old, transcript) or (
            REASON_PLAN_LOCKED if plan_locked(path, kind, new, old) else None)
    if kind == "state":
        plan = read(sibling(path, "input"))
        # 이전 본문의 중단 판정 미해결 줄이 새 본문에 그대로 있어야 한다.
        if new is not None and any(UNRESOLVED in l and l not in section(new, "중단 사유").splitlines()
                                   for l in section(old, "중단 사유").splitlines()):
            return REASON_UNRESOLVED
        if state_locked(data, path, new, old):
            return REASON_LOCKED
        number = unconfirmed(added, read(decision_path(path)))
        if number:
            return REASON_UNCONFIRMED.format(number=number)
        gap = problem_confirmation_gap(data, path, new, old) if new is not None else None
        if gap:
            return gap
        return check_stage(path, new, old, transcript) or transition_reason(path, new, old, plan)
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


# Bash 쓰기 명령의 경로 토큰 중 스킬이 필요한 log/ 문서 경로를 반환한다.
def bash_log_docs(data: dict, root: Path) -> list[Path]:
    cwd = Path(data.get("cwd") or ".")
    paths = (cwd / token for token, _ in command_paths(bash_command(data)))
    return [p for p in paths if rel(p, root).startswith(LOG) and skill_for(rel(p, root))]


WRITE_ROLES = {"worker", "documenter"}
IN_PROGRESS = re.compile(r"^- 작업 상태:\s*진행\s*$", re.M)
EXEC_APPROVED = re.compile(r"^\s*- (?:사용자 )?승인 상태:\s*승인\s*$", re.M)
REASON_LOG_BASH = "이 쓰기 명령은 log/ 폴더 경로를 포함합니다. " + RETRY
REASON_REVIEW_LEFT = "진행 중인 작업의 검토 필요 또는 사전 실측 필요 항목({items})이 끝나지 않아 log/ 밖에 쓸 수 없습니다. " + RETRY


# 작업 상태가 진행이고 실행 승인이 승인인 경과 문서의 (경로, 본문) 목록을 반환한다.
def running_states(root: Path) -> list[tuple[Path, str]]:
    out = []
    for path in sorted(root.glob("log/task/*/status.md")) + sorted(root.glob("log/state/*.md")):
        if path.name.endswith(("-input.md", "-research.md")):
            continue
        text = read(path)
        if IN_PROGRESS.search(text) and EXEC_APPROVED.search(section(text, "실행 승인 범위")):
            out.append((path, text))
    return out


# 진행 중인 작업의 검토 필요 항목 중 허용 값이 아닌 상태의 항목, 답 없는 검토 보고, 미완료 사전 실측 항목 이름 목록을 반환한다. 허용 값을 읽지 못하면 ValueError이다.
def unreviewed_items(root: Path) -> list[str]:
    states = running_states(root)
    if not states:
        return []
    done = review_done()
    return [name for path, text in states
            for name in [n for n, item in review_items(read(decision_path(path))).items() if status_of(item) not in done]
            + unanswered(path, done) + measure_pending(text)]


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
REASON_OUTSIDE_LOG = "사용자 마지막 메시지에 승인 명령이 없어 메인 세션은 log/ 경로만 쓸 수 있습니다. " + APPROVAL_RETRY
REASON_SCRIPT_FILE = "승인 전에는 log/와 OS 임시 폴더에 스크립트 파일을 쓸 수 없습니다. " + APPROVAL_RETRY
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
        if guarded and r.startswith(LOG) and not r.startswith("log/incident/"):
            lines = added_lines(data, path)[0]
            if record_leak("\n".join(lines)):
                return REASON_RECORD
            if doc_kind(r):
                quote = user_quote(lines, data)
                if quote:
                    return REASON_USER_QUOTE.format(quote=quote)
        reason = check_doc(data, path, r, user)
        if reason:
            return reason
    return None
