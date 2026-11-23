"""질문 도구 답과 작업 단계, 승인 시각을 경과 문서와 명세 문서에 기록한다.

호출자: hooks/check_workflow_response.py, hooks/check_context_size.py
입력: hook 입력 dict, runner, 작업 문서 경로
반환: 기록한 문서 본문 또는 경로 목록이며 기록 대상이 없으면 변경하지 않는다.
한계: 세션 기록에 확정 답이 있을 때만 기록한다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from check_invalid_response import transcript_of
from sub_approval import APPROVED, approval_record_times, stage_of
from sub_docs import project_root, section, sibling
from sub_judgment import confirmed
from sub_paths import TASK_REL
from sub_display import find_confirmed, norm, session_confirmed
from sub_question import RECOMMENDED, REIMPLEMENT, REQUIREMENT, description_of, split_tag, tasks_of
from sub_session import question_answers, read_session, turn
from sub_spec import apply as apply_spec
from sub_write import PATCH_FILE, read, strings
from sub_form import splice




# 파일 본문에 edit(기존 본문 -> 새 본문)을 적용해 쓴다. 줄바꿈 형식과 인코딩은 원본을 유지하고 바뀐 내용이 없으면 쓰지 않는다.
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


# Codex exec 입력 안 `tools.apply_patch(...)` 호출의 패치 본문 목록이다. 큰따옴표, 작은따옴표, 템플릿 리터럴 인자를 읽고 디코드에 실패한 호출은 건너뛴다.
def exec_patches(source: str) -> list[str]:
    out = []
    escapes = {"n": "\n", "t": "\t", "r": "\r"}
    for m in APPLY_CALL.finditer(source):
        pos = m.end()
        while pos < len(source) and source[pos].isspace():
            pos += 1
        quote = source[pos] if pos < len(source) else ""
        if quote == '"':
            try:
                value = json.JSONDecoder().raw_decode(source, pos)[0]
            except ValueError:
                continue
            out.append(value) if isinstance(value, str) else None
        elif quote in ("'", "`"):
            chars, i = [], pos + 1
            while i < len(source) and source[i] != quote:
                if source[i] == "\\" and i + 1 < len(source):
                    i += 1
                    chars.append(escapes.get(source[i], source[i]))
                else:
                    chars.append(source[i])
                i += 1
            if i < len(source):
                out.append("".join(chars))
    return out


# 쓰기 도구 호출 한 건이 쓴 파일 경로 목록이다. 쓰기 도구가 아니면 빈 목록이다.
def call_paths(name: str, args) -> list[str]:
    if name not in WRITE_TOOLS:
        return []
    patches = ([t for s in strings(args) for t in exec_patches(s)] if name == "exec"
               else list(strings(args)) if name == "apply_patch" else None)
    return ([a or b for s in patches for a, b in PATCH_FILE.findall(s)] if patches is not None
            else [str((args or {}).get("file_path") or "")])


# 이번 도구 호출이 쓴 작업 문서와 같은 작업의 경과 문서 경로를 반환한다. 작업 문서를 쓰지 않은 호출이면 None이다.
def call_status(data: dict) -> Path | None:
    root = project_root(data.get("cwd"))
    last = None
    for p in call_paths(data.get("tool_name") or "", data.get("tool_input")):
        m = STATUS_PATH.search(p.replace("\\", "/"))
        last = m.group(0) if m else last
    path = sibling(root / last, "state") if last else None
    return path if path and path.resolve().is_relative_to(root.resolve()) and path.is_file() else None


# 이번 호출이 쓴 작업과 색인의 현재 진행 작업의 경과 문서 목록이다.
def target_statuses(data: dict) -> list[Path]:
    from sub_task_index import selected_status
    return list(dict.fromkeys(p for p in (call_status(data), selected_status(project_root(data.get("cwd")))) if p))


# 검토 필요 항목의 구분 키이다. `검토 <번호>.` 항목은 번호로, 그 밖의 항목은 이름 전체로 구분한다.
def review_key(name: str) -> str:
    m = REVIEW_KEY.match(name)
    return m[1] if m else name


# 검토 필요 절 본문에 (키, 항목 본문) 항목들을 넣는다. 같은 키는 바꾸고 새 키는 끝에 덧붙이며 양식 자리표시 항목은 지운다.
def put_reviews(body: str, blocks: dict[str, str]) -> str:
    starts = list(re.finditer(r"(?m)^- (.+)$", body))
    items = {review_key(m.group(1).strip()): body[m.start():(starts[i + 1].start() if i + 1 < len(starts) else len(body))].strip("\n")
             for i, m in enumerate(starts) if not re.search(r"<[^<>\n]+>", m.group(1))}
    for name, block in blocks.items():
        # 시각을 뺀 내용이 같으면 기존 항목(시각 포함)을 둔다.
        if not (name in items and TIME_LINE.sub("", items[name]) == TIME_LINE.sub("", block)):
            items[name] = block
    head = body[:starts[0].start()] if starts else body.rstrip("\n") + "\n\n"
    return head + "\n".join(items.values()) + ("\n\n" if body.endswith("\n\n") else "\n")


# 경과 문서의 단일 단계를 반환하며 완료 검사에 실패하면 구현을 반환한다.
def work_stage(path: Path, text: str) -> str:
    from sub_approval import stage_of, stage_completion_reason
    stage = stage_of(text)
    if stage:
        return "구현" if stage == "완료" and stage_completion_reason(path, text) else stage
    return "구현" if REQUEST_APPROVED(text) else "계획"


# 답이 선택지 라벨과 정확히 같을 때만 고른 라벨 목록을 반환한다. 여러 개를 고르면 ", "로 이은 라벨이다.
# 자유 입력, Codex CLI `user_note:` 메모가 든 답은 수정 입력이므로 빈 목록이다.
def picked_labels(question: dict, answer: str) -> list[str]:
    labels = {RECOMMENDED.sub("", " ".join(str(o["label"]).split())): o for o in question.get("options") or []
              if isinstance(o, dict) and o.get("label")}
    answer = RECOMMENDED.sub("", answer.strip())
    parts = [answer] if answer in labels else [RECOMMENDED.sub("", p.strip()) for p in answer.split(", ")]
    return parts if parts and all(p in labels for p in parts) else []


# 이번 턴 질문 도구 답을 경과 문서에 기록하고 작업 단계를 정규화한다. answers가 있으면 그 답을 쓴다.
# 질문 문장 끝 작업 식별자의 경과 문서에 기록하고, 식별자가 없으면 색인의 현재 진행 작업에 기록한다.
def record_answers(data: dict, runner: str, answers: list | None = None) -> None:
    # 이번 턴 질문 답을 읽고 식별자 꼬리를 뗀 질문과 대상 경과 문서로 묶는다.
    entries = read_session(transcript_of(data, runner))
    if answers is None:
        answers = question_answers(turn(entries)[2])
    groups: dict[Path, list] = {}
    for q, answer in answers:
        text, tag = split_tag(q.get("question") or "")
        target = (tasks_of(data, runner, tag) or [None])[0]
        if target is not None:
            groups.setdefault(target, []).append(({**q, "question": text}, answer))
    if not answers and (path := call_status(data)) is not None:
        groups.setdefault(path, [])
    confirmed = session_confirmed(entries)
    for path, items in groups.items():
        record_to(path, items, confirmed)


# 현재 세션의 작업 문서에 실제 사용자 기록 시각을 반영하며 같은 입력의 재처리는 파일을 바꾸지 않는다.
def record_times(data: dict, runner: str) -> str:
    notices = []
    for path in sorted(target_statuses(data)):
        rewrite(path, lambda text: approval_record_times(data, path, text))
        probe = read(path).replace("기록 없음", "<자동 시각>")
        missing = ["사용자 승인 시각"] if re.search(r"(?m)^\s*- 사용자 승인 시각: 기록 없음\s*$",
                                                 approval_record_times(data, path, probe)) else []
        if missing:
            notices.append(f"{path.as_posix()}: {', '.join(missing)}은 대응 사용자 이벤트의 timestamp를 확인할 수 없어 기록 없음으로 남겼습니다.")
    return "\n".join(notices)


# 재구현 요청 절 본문에 항목 하나를 추가한다. 양식 자리표시 항목과 `없음` 항목은 지우고 같은 항목은 다시 추가하지 않는다.
def put_reimplement(body: str, item: str) -> str:
    starts = list(re.finditer(r"(?m)^- (.+)$", body))
    items = [body[m.start():(starts[i + 1].start() if i + 1 < len(starts) else len(body))].strip("\n")
             for i, m in enumerate(starts) if not re.search(r"<[^<>\n]+>", m.group(1)) and m.group(1).strip() != "없음"]
    # 근거 시각이 달라도 수정 대상, 요구사항 번호와 기대 결과가 같으면 같은 항목이다.
    key = lambda text: re.sub(r" \(근거: 사용자 [^)]*\)", "", text)
    if key(item) not in map(key, items):
        items.append(item)
    head = body[:starts[0].start()] if starts else body.rstrip("\n") + "\n\n"
    return head + "\n".join(items) + ("\n\n" if body.endswith("\n\n") else "\n")


# 확정 질문의 선택지 답을 path 경과 문서 검토 필요 절에 한 줄로 기록한다. 확정 답은 직전 확정안(info-confirmed 응답)의 줄 원문을
# 같은 작업 폴더 명세 문서 요구사항 절의 사용자 확인 시각, 문제, 목표, 기대 결과, 한계, 잠재 위험 칸에 기록하고, 폐기 답은 그 절을 폐기 절로 바꾼다.
# 재구현 질문의 승인 답은 명세 문서 재구현 요청 절에 기록한다. 직접 입력 답과 양식 밖 질문은 기록하지 않는다.
def record_to(path: Path, answers: list, confirmed: list | None = None) -> None:
    now = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    reviews, ops, names, requests = {}, [], {}, []
    flat = lambda text: " ".join(str(text).split())
    spec = path.with_name("spec.md")
    line = lambda number, name, label: f"- 검토 {number}. {name}: {label} (사용자 검토 시각: {now})"
    for q, answer in answers:
        # 선택지 답이 아니면 기록하지 않는다.
        question = flat(q.get("question") or "")
        picked = picked_labels(q, answer)
        if not picked:
            continue
        rev, redo = REQUIREMENT.match(question), REIMPLEMENT.match(question)

        # 폐기 답은 검토 필요 절에 `폐기`를 기록하고 명세 절을 폐기 절로 바꾼다.
        if rev and picked == ["폐기"]:
            reviews[f"검토 {rev[1]}"] = line(rev[1], rev[2].strip(), "폐기")
            ops.append(("drop", int(rev[1]), ""))
        elif rev and picked == ["확정"]:
            # 확정안과 확정 설명이 같을 때만 확정안 원문을 명세 칸에 기록한다.
            number, name = int(rev[1]), rev[2].strip()
            found = find_confirmed(confirmed or [], number, name, rev[3].strip())
            if found and norm(found["fields"]["기대 결과"]) == norm(description_of(q, "확정")):
                cell = found["fields"]
                reviews[f"검토 {number}"] = line(number, name, "확정")
                names[number] = name
                ops.append(("confirm", number, name, cell["목표"], cell["기대 결과"], cell["한계"], cell["잠재 위험"], now,
                            cell["문제"]))
        elif redo and picked == ["승인"]:
            # 수정 대상, 수정 위치, 요구사항 번호, 기대 결과와 사용자 확인 시각을 한 항목으로 만든다.
            requests.append(f"- {redo[2].strip()}: {redo[3].strip()} (요구사항 {redo[1]}) (근거: 사용자 {now})\n"
                            f"  - 기대 결과: {description_of(q, '승인')}")

    # 명세 문서가 있으면 확정안과 재구현 요청을 기록한다.
    spec_changed = False
    if spec.is_file() and (ops or requests):
        try:
            before = read(spec)
            if ops:
                rewrite(spec, lambda text: splice(text, "요구사항 목록", lambda b: apply_spec(b, ops, names)))
            spec_changed = read(spec) != before
            for item in requests:
                rewrite(spec, lambda text: splice(text, "재구현 요청", lambda b: put_reimplement(b, item)))
        except (OSError, ValueError):
            pass

    # 검토 필요 한 줄과 구현 승인 및 작업 단계를 갱신한다. 요구사항 목록이 바뀌었으면 구현 승인을 승인 대기로 되돌린다.
    def edit(text: str) -> str:
        if spec_changed:
            text = splice(text, "구현 승인 범위", lambda body: re.sub(
                r"(?m)^(\s*-\s*(?:사용자 )?승인 상태:\s*)승인\s*$", r"\1승인 대기", body))
        if reviews:
            text = splice(text, "검토 필요", lambda body: put_reviews(body, reviews))
        # 단계가 있는 문서는 단일 항목으로 갱신하고 상태만 있는 기존 문서는 보존한다.
        from sub_approval import normalize_stage, stage_of
        return normalize_stage(text, work_stage(path, text)) if stage_of(text) else text
    rewrite(path, edit)
UNRESOLVED = "중단 판정 미해결"


REVIEW_KEY = re.compile(r"(검토 \d+)(?=[. ])")
STATUS_PATH = re.compile(rf"{re.escape(TASK_REL)}/[\w.\-]+/(?:status|spec|research|refute)\.md")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "apply_patch", "exec")
TIME_LINE = re.compile(r"\(사용자 검토 시각: [^)]*\)")
APPLY_CALL = re.compile(r"tools\.apply_patch\(")


REQUEST_APPROVED = lambda text: any(APPROVED.match(l) for l in section(text, "구현 승인 범위").splitlines())
