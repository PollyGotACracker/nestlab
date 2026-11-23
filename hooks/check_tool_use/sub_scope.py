"""에이전트별 쓰기 범위와 작업 문서 쓰기를 검사한다.

호출자: hooks/check_tool_use.py(check_write)
입력: hook 입력 dict, 역할 이름, 쓰기 대상 경로 목록, 메인 세션 여부
반환: check_write, check_doc은 deny 사유 문자열 또는 통과 시 None
한계:
- Bash로 실행한 스크립트가 직접 여는 파일은 판정하지 못한다.
"""
from __future__ import annotations

import re
from pathlib import Path

from sub_approval import APPROVED, WRITE_STAGES, approved, approval_record_times, last_user, stage_of, sync_stage
from sub_docs import (FENCE_LINE, HEADING, doc_kind, measure_pending, missing_titles, project_root, record_leak, review_done_numbers, review_pending, section, sibling, strip_comments, task_doc,
                      task_id, template_name, template_section, template_text, titles)
from sub_paths import INCIDENT_REL, SKILL_REL, TASK_INDEX_REL, TASK_REL
from sub_path import REASON_SOURCE_PATH, command_paths, in_incident, in_temp, source_leak
from sub_role import director_main, first_prompt, main_session
from sub_session import agent_transcript, question_answers, read_session, turn, user_messages
from sub_spec import blank_cells, cells, template_block
from sub_judgment import check_judgment, check_raw, check_stage, confirmed_by, early_items
from sub_write import APPROVAL_RETRY, RETRY, SHELLS, added_lines, bash_command, bash_write_reason, is_write_bash, read, strings
from sub_document import Document, bare, filled, integrated
from sub_gate import R_SOURCE_FORMAT, bad_source, measurement_targets, transition_reason
from sub_task_docs import document_stage_reason

READONLY = {"researcher"}
LOG = (".agent-logs/",)
UNRESOLVED = "중단 판정 미해결"
TEMPLATE_OF = {"state": "status", "input": "spec", "research": "research", "refute": "refute"}


# 저장소 루트 기준 상대 경로를 반환한다. 루트 밖이면 빈 문자열이다.
def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return ""


REASON_NO_APPROVAL = "사용자 마지막 메시지에 '승인'이 없어 승인 상태를 승인으로 쓸 수 없습니다.\n" + APPROVAL_RETRY
REASON_UNRESOLVED = "중단 사유의 `중단 판정 미해결` 줄은 `중단 판정`이 든 질문 도구 질문의 확정 선택지 답이 세션 기록에 있어야 지우거나 바꿀 수 있습니다. " + RETRY
REASON_LOCKED = ("status.md의 `작업 단계` 줄과 `검토 필요` 절은 hook만 바꿀 수 있습니다. "
                 "log-status 양식을 따르고 변경은 질문 도구로 확정받으십시오.")
LOCKED = ("검토 필요",)
STATE_LINE = re.compile(r"^- 작업 상태:.*$", re.M)


# 단일 작업 단계 줄을 반환하며 단계가 없는 기존 문서는 상태 줄을 읽는다. 둘 다 없으면 빈 문자열이다.
def state_line(text: str) -> str:
    m = re.search(r"(?m)^- 작업 단계:.*$", text) or STATE_LINE.search(text)
    return m.group(0).strip() if m else ""


# 경과 문서 쓰기 후 작업 단계 줄과 검토 필요 절이 기존 문서와 다르면 True이다.
# 새 문서는 이 줄과 절이 경과 문서 양식 원문과 같아야 한다. 파일 전체 내용을 구할 수 없는 apply_patch는 True이다.
def state_locked(data: dict, path: Path, new: str | None, old: str) -> bool:
    if new is None:
        return True

    # 기존 문서는 기존 본문과, 새 문서는 양식 원문과 보호 줄 및 절 본문을 주석 없이 비교한다.
    new, old = strip_comments(new), strip_comments(old)
    if state_line(new) != state_line(old or strip_comments(template_text(TEMPLATE_OF["state"]))):
        return True
    return any(section(new, t).strip() != strip_comments(section(old, t) if old else template_section(TEMPLATE_OF["state"], t)).strip()
               for t in LOCKED)
REASON_SPEC_LOCKED = ("spec.md 요구사항 절의 `사용자 확인 시각`, `결과`, `기대 결과` 칸과 확정된 절의 `문제`, `목표`, `한계`, `잠재 위험` 칸은 hook만 바꿀 수 있습니다. "
                      "log-spec 양식을 따르고 변경은 질문 도구로 확정받으십시오.")


# 명세 문서 쓰기 후 파일 전체 내용에서 요구사항 절의 결과 칸과 기대 결과 칸이 기존 문서와 다르면 True이다.
# 기존에 없던 절과 새 문서는 양식 자리표시 값과 같아야 한다. 파일 전체 내용을 구할 수 없는 apply_patch는 True이다.
def spec_locked(data: dict, path: Path, new: str | None, old: str) -> bool:
    if new is None:
        return True

    # 번호별 hook 관리 칸 값을 비교한다. 지워진 절은 바뀐 것으로 본다.
    # 통합 구조는 사용자 확인 시각과 기대 결과 칸이고, 사용자 확인 시각이 기록된 절은 문제, 목표, 한계, 잠재 위험 칸도 비교한다.
    got, was, blank = cells(section(new, "요구사항 목록")), cells(section(old, "요구사항 목록")), blank_cells(not integrated(new))
    if any(got.get(n) != (was.get(n) or blank) for n in got.keys() | was.keys()):
        return True
    return False


REASON_PLAN_LOCKED = ("구현 승인 뒤에는 명세 문서의 `재구현 요청`, `재검증 요청` 밖 절을 고칠 수 없습니다. "
                      "계획을 바꾸려면 질문 도구로 요구사항 변경을 확정받아 승인을 다시 받으십시오.")
OPEN_TITLES = ("재구현 요청", "재검증 요청")
EXEC_DONE = re.compile(r"^\s*- (?:사용자 )?승인 상태:\s*승인\s*$", re.M)


# 명세 문서 전문에서 `재구현 요청`, `재검증 요청` 절 본문을 뺀 텍스트이다.
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


# 작업 계획 항목의 `(요구사항 <번호>)` 표기를 쓰는 시점에 구현 배정 검사와 같은 기준으로 확인한다.
# 입력은 쓰기 뒤와 앞의 명세 문서이고, 위반이면 사유를, 아니면 None을 반환한다.
def plan_ref_reason(new: str | None, old: str) -> str | None:
    from sub_document import check_refs, number_of, numbered, requirements
    if new is None or section(new, "작업 계획") == section(old or "", "작업 계획"):
        return None

    # 요구사항이 아직 없는 작성 초기 문서와 기존 양식 문서는 검사하지 않는다.
    try:
        plan = Document(new)
        numbers = {number_of(name) for name, _ in requirements(plan)}
    except ValueError:
        return None
    if not numbered(plan):
        return None

    # 자리표시자가 남은 줄은 작성 중인 양식 줄로 보고 건너뛴다.
    names = {name for _, _, name in plan.headings}
    try:
        for key in ("배정 범위", "변경 대상", "생성 대상", "문서 갱신 대상", "삭제 목록", "통합 목록"):
            if key in names:
                body = "\n".join(line for line in plan.section(key).splitlines() if "<" not in line)
                check_refs(body, numbers, key)
    except ValueError as error:
        return str(error)
    return None


# 같은 작업의 경과 문서 구현 승인이 승인이고 문서 계획 절이 바뀌는 쓰기면 True이다. kind는 명세 문서 `input` 또는 조사 문서 `research`이다.
def plan_locked(path: Path, kind: str, new: str | None, old: str) -> bool:
    status = sibling(path, "state")
    state = read(status)
    stage = stage_of(state)
    if not status.is_file() or (stage != "구현" if stage else not EXEC_DONE.search(section(state, "구현 승인 범위"))):
        return False
    if new is None:
        return True
    if kind == "input":
        return not placeholder_fill(without_open(old), without_open(new))
    return False


# old에서 new로 바뀐 줄이 모두 명세 양식 요구사항 절의 자리표시 줄을 값으로 바꾼 것이면 True이다. 같으면 True이다.
# 구현 단계에 질문으로 확정돼 hook이 추가한 요구사항의 빈 칸을 채우는 쓰기만 허용하고, 확정된 값의 변경은 막는다.
def placeholder_fill(old: str, new: str) -> bool:
    import difflib
    # 통합 구조와 기존 구조(`- 칸:` 줄) 양식의 자리표시 줄을 모두 채움 대상으로 본다.
    marks = {l.strip() for legacy in (False, True) for l in template_block(legacy).splitlines() if "<" in l and not l.startswith("###")}
    a, b = old.splitlines(), new.splitlines()
    return all(tag == "equal" or (tag == "replace" and all(l.strip() in marks for l in a[i1:i2]))
               for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes())


EVIDENCE = re.compile(r"\(근거: (검증|사용자) (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\)")
CONFIRMED_TIME = re.compile(r"사용자 (?:검토|확인) 시각:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
REASON_EVIDENCE_REQUIRED = "명세 문서 `재구현 요청` 절의 새 항목에 `(근거: 검증 시각)` 표시가 없습니다. 사용자 근거 항목은 재구현 질문의 승인 답으로 hook이 기록합니다. log-spec 양식을 따르십시오."
REASON_EVIDENCE_TIME = "재구현 요청의 근거 시각 `{kind} {stamp}`이 경과 문서 검증 결과의 FAIL 시각과 일치하지 않습니다. log-spec 양식을 따르십시오."
REASON_USER_EVIDENCE = ("`(근거: 사용자 시각)` 재구현 요청 항목은 직접 쓸 수 없습니다. "
                        "`재구현 요청 <요구사항 번호>. <수정 대상 경로>: <수정 위치>` 질문의 `승인` 답으로 hook이 기록합니다.")


# 경과 문서 검증 결과 절에서 FAIL 줄이 딸린 판정 시각 집합이다. 시각은 `- YYYY-MM-DD HH:MM:SS` 줄 머리이다.
def fail_times(status_text: str) -> set[str]:
    out, current = set(), None
    for line in section(status_text, "검증 결과").splitlines():
        head = re.match(r"- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        current = head[1] if head else current
        if current and re.match(r"\s+- FAIL\b", line):
            out.add(current)
    return out


# 질문 답 기록 hook이 남긴 확정 답 시각 집합이다. 경과 문서 검토 필요 절의 사용자 검토 시각이다.
# spec_text가 있으면 통합 구조 명세 요구사항 항목의 사용자 확인 시각 칸과 문제 칸의 추가 합의 시각도 포함한다.
def confirmed_times(status_text: str, spec_text: str = "") -> set[str]:
    from sub_document import values
    stamps = {m[1] for m in CONFIRMED_TIME.finditer(section(status_text, "검토 필요"))}
    if integrated(spec_text):
        body = section(spec_text, "요구사항 목록")
        stamps |= {v for v in values(body, "사용자 확인 시각") if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", v)}
        stamps |= {m[1] for m in CONFIRMED_TIME.finditer(body)}
    return stamps


# 명세 문서 재구현 요청 절에 새로 추가하는 항목에 근거 표시가 없거나 근거 시각이 검증 FAIL 시각에 없으면 사유를 반환한다. 사용자 근거 항목은 hook만 기록하므로 직접 쓰면 사유를 반환한다.
# 일반 사용자 메시지 시각은 근거가 아니다. 양식 자리표시 항목과 파일 전체 내용을 구할 수 없는 쓰기는 판정하지 않는다. 맞으면 None이다.
def evidence_gap(data: dict, path: Path, new: str | None, old: str) -> str | None:
    if new is None:
        return None
    known = set(section(old, "재구현 요청").splitlines())
    for item in re.split(r"(?m)^(?=- )", section(new, "재구현 요청")):
        first = item.splitlines()[0] if item.strip() else ""
        if not first.startswith("- ") or first.strip() == "- 없음" or first in known or PLACEHOLDER.search(first):
            continue
        marks = EVIDENCE.findall(item)
        if not marks:
            return REASON_EVIDENCE_REQUIRED
        failed = fail_times(read(sibling(path, "state")))
        for kind, stamp in marks:
            if kind == "사용자":
                return REASON_USER_EVIDENCE
            if stamp not in failed:
                return REASON_EVIDENCE_TIME.format(kind=kind, stamp=stamp)
    return None


REASON_BASIS_CODE = ("명세 문서 `재검증 요청` 절의 `판정 기준` 칸에 구현 위치나 코드 식별자(`{value}`)가 있습니다. "
                     "요구사항 번호와 확인할 동작만 쓰고 위치는 reviewer가 변경 대상 파일의 diff로 찾게 하십시오. log-spec 양식을 따르십시오.")
# 파일:행, 밑줄이 든 이름, 괄호 호출과 백틱 코드이다. 이름은 ASCII 영문, 숫자, 밑줄만 보고 한글 단어는 제외한다.
BASIS_CODE = re.compile(r"[A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+:\d+|[A-Za-z0-9]+_[A-Za-z0-9_]+|[A-Za-z0-9_]+\(|`[^`]+`")


# 명세 문서 재검증 요청 절에 새로 추가한 `판정 기준` 줄에 구현 위치나 코드 식별자가 있으면 해당 부분을, 아니면 None을 반환한다.
# 파일 전체 내용을 구할 수 없는 쓰기는 판정하지 않는다.
def basis_code(new: str | None, old: str) -> str | None:
    if new is None:
        return None
    known = set(section(old, "재검증 요청").splitlines())
    for line in section(new, "재검증 요청").splitlines():
        m = re.match(r"\s*- 판정 기준:(.*)", line)
        if m and line not in known and (hit := BASIS_CODE.search(m[1])):
            return hit[0]
    return None


REASON_SPEC_EARLY ="{items}이(가) 끝나지 않아 명세 문서를 쓸 수 없습니다. 모든 검토를 끝낸 뒤 쓰십시오."
REASON_UNCONFIRMED = ("기존 요구 구분 `director 해석 (검토 {number})`의 검토 완료 근거가 없어 쓸 수 없습니다. "
                      "새 해석은 question-confirmed 양식의 확정 질문으로 확인받으십시오.")
REASON_USER_QUOTE = "작업 문서에 사용자 메시지 원문({quote}…)이 들어 있습니다. 요약해서 쓰십시오."
INTERPRETED = re.compile(r"^\s*(?:- )?요구 구분:\s*director 해석 \(검토 (\d+)\)", re.M)
QUOTE_MIN = 20
TASK_ID_TEXT = re.compile(r"(?<![\w-])\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*(?![a-z0-9-])")
REASON_OTHER_TASK = "작업 문서에 다른 작업 ID({task})를 쓸 수 없습니다. 이 작업의 내용으로 요약해 쓰십시오."


# 추가 줄을 공백 정규화해 세션 사용자 메시지와 QUOTE_MIN자 이상 연속 일치하는 첫 부분을 반환한다. 없으면 None이다.
# 사용자 메시지가 이전 응답 본문을 인용한 부분은 사용자가 입력한 문장이 아니므로 제외한다.
def user_quote(lines: list[str], data: dict) -> str | None:
    entries = read_session(data.get("transcript_path") or "")
    said = " ".join(" ".join(user_messages(entries)).split())
    shown = " ".join(" ".join(e["text"] for e in entries if e["role"] == "assistant").split())
    for line in lines:
        if re.match(r"\s*- (?:작업 ID|근거 출처|출처):", line):
            continue
        # 작업 ID는 사용자 원문 인용으로 보지 않는다.
        line = " ".join(TASK_ID_TEXT.sub(" ", line).split())
        for i in range(len(line) - QUOTE_MIN + 1):
            piece = line[i:i + QUOTE_MIN]
            if piece in said and piece.strip() not in shown:
                return piece
    return None


# 경과 문서에 새로 쓰는 `요구 구분: director 해석 (검토 N)` 줄의 검토 N이 기존 문서에서 검토 완료가 아니면 번호를 반환한다. 아니면 None이다.
def unconfirmed(added: list[str], old: str) -> str | None:
    wanted = [m[1] for m in map(INTERPRETED.match, added) if m]
    if not wanted:
        return None
    done = review_done_numbers(old)
    return next((n for n in wanted if n not in done), None)


PLACEHOLDER = re.compile(r"<[^<>\n]+>")


REASON_MISSING_TITLES = "{name}: 빠진 절 `{titles}`. {template} 양식을 따르십시오."

# 작업 문서 한 개 쓰기 검사이다.
def check_doc(data: dict, path: Path, r: str, user: str) -> str | None:
    # 작업 문서가 아니면 검사하지 않는다.
    kind = doc_kind(r)
    if kind is None:
        return None
    added, new = added_lines(data, path)
    old = read(path)

    # 승인 줄은 사용자 승인 키워드가 있을 때만 쓸 수 있다.
    managed = stage_of(old if kind == "state" else read(sibling(path, "state")))
    if not managed and any(APPROVED.match(line) for line in added) and not approved(user):
        return REASON_NO_APPROVAL

    # 템플릿 필수 절이 빠지면 차단한다.
    if new is not None:
        from sub_docs import structure_reason
        reason = structure_reason(new, TEMPLATE_OF[kind], path.name)
        if reason:
            return reason
        lost = missing_titles(new, TEMPLATE_OF[kind], old)
        if lost:
            return REASON_MISSING_TITLES.format(name=path.name, titles=", ".join(lost[:3]), template=template_name(TEMPLATE_OF[kind]))

    # 문서 종류별 검사를 한다.
    transcript = data.get("transcript_path") or ""
    if kind == "input":
        # 모든 검토가 끝나기 전에는 요구사항 목록과 작업 계획 절을 바꿀 수 없다.
        status = sibling(path, "state")
        try:
            changed = new is not None and any(section(new, t) != section(old, t) for t in ("요구사항 목록", "작업 계획"))
            left = early_items(status) if status.is_file() and changed else []
        except ValueError as error:
            return str(error)
        if left:
            return REASON_SPEC_EARLY.format(items=", ".join(left[:3]))
        if spec_locked(data, path, new, old):
            return REASON_SPEC_LOCKED
        if plan_locked(path, kind, new, old):
            return REASON_PLAN_LOCKED
        reason = plan_ref_reason(new, old) or evidence_gap(data, path, new, old)
        if not reason and (code := basis_code(new, old)):
            reason = REASON_BASIS_CODE.format(value=code)
        if reason:
            return reason
    if kind == "refute":
        return check_raw(new, old, blank_ok=True)
    if kind == "research":
        # 새로 들어가는 줄의 출처가 URL 또는 파일:행 형식인지 확인한다.
        bad = next((b for b in map(bad_source, added) if b is not None), None)
        if bad is not None:
            return R_SOURCE_FORMAT.format(value=bare(bad[:40]))
        return check_raw(new, old) or check_judgment(path, new, old, transcript) or (
            REASON_PLAN_LOCKED if plan_locked(path, kind, new, old) else None)
    if kind == "state":
        plan = read(sibling(path, "input"))
        # 이전 본문의 중단 판정 미해결 줄이 새 본문에 그대로 있어야 한다. `중단 판정` 질문의 확정 답이 있으면 줄을 지울 수 있다.
        if new is not None and any(UNRESOLVED in l and l not in section(new, "중단 사유").splitlines()
                                   for l in section(old, "중단 사유").splitlines()) and not confirmed_by(transcript, "중단 판정"):
            return REASON_UNRESOLVED
        if state_locked(data, path, new, old):
            return REASON_LOCKED
        number = unconfirmed(added, read(path))
        if number:
            return REASON_UNCONFIRMED.format(number=number)
        computed = approval_record_times(data, path, new) if new is not None else new
        return check_stage(path, computed, old, transcript) or transition_reason(path, computed, old, plan)
    return None


# 쓰기 대상 경로가 요구하는 스킬 이름을 반환한다. 대상이 아니면 None이다.
def skill_for(r: str) -> str | None:
    if r.startswith(INCIDENT_REL + "/") and r.endswith(".md"):
        return "nestlab-log-incident"
    name = r.rsplit("/", 1)[-1]
    if (name in ("AGENTS.md", "AGENTS.global.md", "CLAUDE.md") or (r.startswith(SKILL_REL + "/") and name == "SKILL.md")
            or (r.startswith(".claude/agents/") and name in ("MAIN.md", "SUB.md"))):
        return "nestlab-implement-doc"
    return None


READ_CMD = re.compile(r"\b(?:Get-Content|cat|type|sed|head|less|more)\b", re.I)


# 도구 입력이 읽기 커맨드로 `<skill>/SKILL.md`를 읽는 호출이면 True이다.
def reads_skill(args, skill: str) -> bool:
    text = "\n".join(strings(args))
    return bool(READ_CMD.search(text) and re.search(re.escape(skill) + r"[\\/]+SKILL\.md", text, re.I))


# 해당 스킬 호출이 있는지 판정한다. whole이 거짓이면 현재 턴만, 참이면 세션 전체 기록을 본다.
# Codex는 읽기 커맨드로 `<skill>/SKILL.md`를 읽은 호출이다.
def skill_called(data: dict, skill: str, runner: str = "claude", whole: bool = False) -> bool:
    # 대화 기록의 호출 범위를 읽는다.
    entries = read_session(agent_transcript(data) or data.get("transcript_path") or "")

    # Claude는 스킬 도구 호출을, Codex는 읽기 커맨드 호출을 찾는다.
    for e in (entries if whole else turn(entries)[2]):
        for name, args in e["calls"]:
            if name == "Skill":
                called = args.get("skill") or ""
                if called == skill or called.endswith(":" + skill):
                    return True
            elif runner == "codex" and name != "apply_patch" and reads_skill(args, skill):
                return True
    return False


RULE_DOCS = (("hooks/", "rules/harness-rules.md"), ("tools/", "rules/tool-rules.md"), ("shared/tools/", "rules/tool-rules.md"))


# 쓰기 대상이 hooks/, tools/ 또는 shared/tools/ 아래 .py 파일이면 먼저 읽어야 하는 상세 규칙 문서 경로를 반환한다. 대상이 아니면 None이다.
def rules_for(r: str) -> str | None:
    return next((doc for prefix, doc in RULE_DOCS if r.startswith(prefix) and r.endswith(".py")), None)


# 세션 기록에 상세 규칙 문서를 읽은 호출이 있으면 True이다. Read 도구 호출이거나 읽기 커맨드가 문서 경로를 포함한 호출을 읽은 것으로 본다.
def rules_read(data: dict, doc: str) -> bool:
    entries = read_session(agent_transcript(data) or data.get("transcript_path") or "")
    for e in entries:
        for name, args in e["calls"]:
            text = "\n".join(strings(args)).replace("\\", "/")
            if doc in text and (name == "Read" or (name in SHELLS and READ_CMD.search(text))):
                return True
    return False


# Bash 쓰기 커맨드의 경로 토큰 중 작업 문서와 사건 문서 경로를 반환한다.
def bash_log_docs(data: dict, root: Path) -> list[Path]:
    cwd = Path(data.get("cwd") or ".")
    paths = (cwd / token for token, _ in command_paths(bash_command(data)))
    return [p for p in paths if rel(p, root).startswith(LOG) and (doc_kind(rel(p, root)) or skill_for(rel(p, root)))]


WRITE_ROLES = {"worker", "documenter"}
IN_PROGRESS = re.compile(r"^- 작업 상태:\s*(?:구현|진행)\s*$", re.M)
EXEC_APPROVED = re.compile(r"^\s*- (?:사용자 )?승인 상태:\s*승인\s*$", re.M)
REASON_LOG_BASH = "이 쓰기 커맨드는 .agent-logs/ 폴더 경로를 포함합니다. " + RETRY
REASON_REVIEW_LEFT = "진행 중인 작업의 검토 필요 또는 사전 모델 호출 실측 필요 항목({items})이 끝나지 않아 .agent-logs/ 밖에서 쓸 수 없습니다. " + RETRY


# 구현 단계의 경과 문서를 반환한다. 단계가 없는 기존 문서는 진행 상태와 구현 승인을 확인한다.
def running_states(root: Path) -> list[tuple[Path, str]]:
    out = []
    for path in sorted(root.glob(f"{TASK_REL}/*/status.md")):
        text = read(path)
        stage = stage_of(text)
        if stage == "구현" or (not stage and IN_PROGRESS.search(text) and EXEC_APPROVED.search(section(text, "구현 승인 범위"))):
            out.append((path, text))
    return out


# 진행 중인 작업의 경과 문서 검토 필요 절에서 확정 답이 없는 항목과 미완료 사전 모델 호출 실측 항목 이름 목록을 반환한다.
def unreviewed_items(root: Path) -> list[str]:
    states = running_states(root)
    if not states:
        return []
    return [name for path, text in states
            for name in review_pending(text) + measure_pending(text, targets=measurement_targets(text,
                Document(read(sibling(path, "input"))) if stage_of(text) and sibling(path, "input").is_file() else None))]


# 구현 승인 뒤의 worker나 documenter 세션이면 True이다. 첫 요청의 명세 문서로 작업을 찾아 구현 승인 기록을 본다.
def write_bash_free(data: dict, role: str | None) -> bool:
    if role not in WRITE_ROLES or main_session(data):
        return False
    root = project_root(data.get("cwd"))
    task = task_id(first_prompt(agent_transcript(data) or data.get("transcript_path")))
    if not task:
        return False
    return bool(EXEC_APPROVED.search(section(read(task_doc(root, task, "state")), "구현 승인 범위")))


# 쓰기 Bash 커맨드의 경로 토큰이 .agent-logs/ 아래면 사유를 반환한다. 쓰기 커맨드가 아니거나 .agent-logs/ 경로가 없으면 None이다.
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
REASON_STAGE_LOCKED = "경과 문서의 `작업 단계` 줄은 사용자 메시지 첫 줄의 머리줄(`[plan]`, `[implement]`, `[complete]`, `[키워드 작업ID]`)로 hook만 기록합니다. 해당 줄을 변경하지 마십시오."
REASON_OUTSIDE_LOG = ("사용자 마지막 메시지에 승인 키워드가 없어 메인 세션은 .agent-logs/ 경로만 쓸 수 있습니다. " + APPROVAL_RETRY)
REASON_OUTSIDE_STAGE = ("경과 문서의 작업 단계가 [{stage}]이므로 메인 세션은 .agent-logs/ 경로만 쓸 수 있습니다. "
                        "사용자가 첫 줄에 `[implement]`를 쓴 메시지를 보내면 작업 단계가 [구현]으로 기록됩니다.")
REASON_LOG_PATH = (".agent-logs/ 아래에는 .agent-logs/task/<작업-id>/의 status.md, research.md, refute.md, spec.md와 .agent-logs/incident/<사건-id>.md만 쓸 수 있습니다. "
                   "대상 경로: {path}")
REASON_SCRIPT_FILE = "승인 전에는 .agent-logs/에 스크립트 파일을 쓸 수 없습니다. 파일을 쓰는 대신 명령 출력으로 즉시 처리하십시오."
REASON_NO_SKILL = "{skill} 스킬을 이번 턴에 호출하지 않았습니다. " + RETRY
REASON_NO_RULES = "{path}를 쓰기 전에 {doc}를 읽지 않았습니다. 문서를 읽고 대상 항목의 목적과 고정 구조를 확인한 뒤 같은 쓰기를 다시 실행하십시오."
SCRIPT_EXT = (".py", ".sh", ".ps1", ".js", ".bat", ".cmd")


REASON_RECORD = "작업 문서에 사건 문서 경로와 세션 기록 정보를 쓸 수 없습니다. " + RETRY
REASON_STAMP = "{name}의 `생성 시각`, `완료 시각` 줄은 hook만 기록합니다. log-status 양식을 따르십시오."

# 에이전트별 쓰기 범위를 검사한다. 위반이면 deny 사유를, 통과면 None을 반환한다.
def check_write(data: dict, role: str | None, paths: list[Path], main: bool = False, runner: str = "claude") -> str | None:
    # 읽기 전용 에이전트는 모든 쓰기를 차단한다.
    if role in READONLY:
        return REASON_READONLY.format(role=role)
    root = project_root(data.get("cwd"))
    user, _ = last_user(data.get("transcript_path"))

    # 메인 세션은 에이전트와 무관하게 .agent-logs/ 밖 쓰기에 사용자 승인이 필요하다.
    guarded = director_main(data)
    stage = sync_stage(data, runner, user) if guarded else None
    ok = stage in WRITE_STAGES if stage else approved(user)
    if guarded:
        # 승인 전에는 .agent-logs/에 스크립트 파일을 쓸 수 없다.
        if not ok and any(p.suffix.lower() in SCRIPT_EXT and rel(p, root).startswith(LOG)
                                      for p in paths):
            return REASON_SCRIPT_FILE
        outside = not paths or any(not rel(p, root).startswith(LOG) and not in_temp(str(p)) and not in_incident(str(p))
                                   for p in paths)
        if outside and not ok:
            return REASON_OUTSIDE_STAGE.format(stage=stage) if stage else REASON_OUTSIDE_LOG

        # 승인 뒤에도 진행 중인 작업에 허용 값이 아닌 검토 필요 항목이 있으면 director의 쓰기를 차단한다.
        if outside and role == "director":
            try:
                left = unreviewed_items(root)
            except ValueError as error:
                return str(error)
            if left:
                return REASON_REVIEW_LEFT.format(items=", ".join(left[:3]))

    # 대상마다 기록 정보와 작업 문서 규칙을 검사한다.
    for path in paths:
        r = rel(path, root)
        kind = doc_kind(r)

        # hooks/와 tools/의 .py 파일은 상세 규칙 문서를 읽은 뒤에만 쓸 수 있다.
        doc = rules_for(r)
        if doc and not rules_read(data, doc):
            return REASON_NO_RULES.format(path=r, doc=doc)
        if kind:
            from sub_docs import structure_reason, constraint_reason, mask_results
            new = added_lines(data, path)[1]
            if new is not None:
                # 생성 시각과 완료 시각은 hook이 기록한다. 새 문서는 양식의 초기값만 쓸 수 있다.
                if kind == "state":
                    stamps = lambda text: re.findall(r"(?m)^- (?:생성|완료) 시각:.*$", text)
                    initial = stamps(strip_comments(template_text(TEMPLATE_OF[kind])))
                    if stamps(new) != stamps(read(path)) and (path.is_file() or stamps(new) not in ([], initial)):
                        return REASON_STAMP.format(name=path.name)
                # 기존 문서는 이전에 있던 양식 제목만, 새 문서는 양식의 모든 제목을 요구한다.
                # 결과 저장 hook만 쓰는 결과 블록은 작성자가 고칠 수 없으므로 출처와 값 검사에서 제외한다.
                reason = (structure_reason(new, TEMPLATE_OF[kind], path.name)
                          or constraint_reason(mask_results(new), TEMPLATE_OF[kind], path.name))
                lost = [] if reason else missing_titles(new, TEMPLATE_OF[kind], read(path))
                if lost:
                    reason = REASON_MISSING_TITLES.format(name=path.name, titles=", ".join(lost[:3]), template=template_name(TEMPLATE_OF[kind]))
                if reason:
                    return reason

        # 작업 색인은 현재 진행 작업 절의 작업 선택만 쓸 수 있고, 다른 절은 hook이 갱신한다.
        if r == TASK_INDEX_REL:
            from sub_task_index import index_edit_reason
            reason = index_edit_reason(root, read(path) if path.is_file() else None, added_lines(data, path)[1])
            if reason:
                return reason
            continue

        # .agent-logs/ 아래에는 작업 문서 네 종류와 사건 문서만 쓸 수 있다.
        if r.startswith(LOG) and not kind and not re.fullmatch(INCIDENT_REL + r"/[\w.\-]+\.md", r):
            return REASON_LOG_PATH.format(path=r)

        # 작업 문서에는 다른 작업 ID를 새로 쓸 수 없다.
        if r.startswith(TASK_REL + "/") and kind:
            own = r.split("/")[2]
            other = next((t for line in added_lines(data, path)[0] for t in TASK_ID_TEXT.findall(line) if t != own), None)
            if other:
                return REASON_OTHER_TASK.format(task=other)

            # 모든 역할의 작업 문서에 출처로 쓸 수 없는 경로와 식별자를 추가할 수 없다.
            found = source_leak("\n".join(added_lines(data, path)[0]), data, root)
            if found:
                return REASON_SOURCE_PATH.format(found=found)
        if guarded and r.startswith(TASK_REL + "/") and kind and kind != "state":
            target_stage = stage_of(read(sibling(path, "state")))
            reason = document_stage_reason(TEMPLATE_OF[kind], target_stage) if target_stage else None
            if reason:
                return reason
            if kind == "input" and target_stage == "구현":
                new = added_lines(data, path)[1]
                if not path.is_file() or new is None or not placeholder_fill(without_open(read(path)), without_open(new)):
                    return REASON_PLAN_LOCKED
        # 작업 단계 줄은 hook만 기록하고, 착수 단계에서는 명세 문서를 쓸 수 없다.
        if guarded and doc_kind(r) == "state":
            new = added_lines(data, path)[1]
            if new is not None and stage_of(new) != stage_of(read(path)):
                return REASON_STAGE_LOCKED
        if guarded and r.startswith(LOG) and not r.startswith(INCIDENT_REL + "/"):
            lines = added_lines(data, path)[0]
            if record_leak("\n".join(lines)):
                return REASON_RECORD
            if doc_kind(r):
                quote = user_quote(lines, data)
                if quote:
                    return REASON_USER_QUOTE.format(quote=quote)
        reason = check_doc(data, path, r, user) if guarded else None
        if reason:
            return reason
    return None
