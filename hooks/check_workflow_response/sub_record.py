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
from sub_docs import FENCE_LINE, HEADING, legacy, project_root, review_entries, section, sibling, titles
from sub_judgment import confirmed
from sub_paths import TASK_REL
from sub_question import DROP, RECOMMENDED, REVIEW, split_tag, tasks_of
from sub_scope import PROBLEM_QUESTION, problem_record
from sub_session import question_answers, read_session, turn
from sub_spec import apply as apply_spec, blocks as spec_blocks
from sub_document import filled, values
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


# 세션 기록에서 에이전트가 마지막으로 쓴 경과 문서 경로를 반환한다. PostToolUse 입력이면 지금 호출도 포함한다. 없으면 None이다.
def written_status(data: dict, runner: str) -> Path | None:
    calls = [c for entry in read_session(transcript_of(data, runner)) for c in entry["calls"]]
    if data.get("tool_name"):
        calls.append((data["tool_name"], data.get("tool_input")))
    last = None
    for name, args in calls:
        for p in call_paths(name, args):
            m = STATUS_PATH.search(p.replace("\\", "/"))
            last = m.group(0) if m else last
    path = sibling(project_root(data.get("cwd")) / last, "state") if last else None
    return path if path and path.is_file() else None


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
    return "구현" if REQUEST_APPROVED(text) else "착수"


# 답이 선택지 라벨과 정확히 같을 때만 고른 라벨 목록을 반환한다. 여러 개를 고르면 ", "로 이은 라벨이다.
# 자유 입력, Codex CLI `user_note:` 메모가 든 답은 수정 입력이므로 빈 목록이다.
def picked_labels(question: dict, answer: str) -> list[str]:
    labels = {RECOMMENDED.sub("", " ".join(str(o["label"]).split())): o for o in question.get("options") or []
              if isinstance(o, dict) and o.get("label")}
    answer = RECOMMENDED.sub("", answer.strip())
    parts = [answer] if answer in labels else [RECOMMENDED.sub("", p.strip()) for p in answer.split(", ")]
    return parts if parts and all(p in labels for p in parts) else []


# 이번 턴 질문 도구 답을 경과 문서에 기록하고 작업 단계를 정규화한다. answers가 있으면 그 답을 쓴다.
# 질문 문장 끝 작업 식별자의 경과 문서에 기록하고, 식별자가 없으면 진행 중인 작업이 하나일 때 그 작업, 여럿이면 마지막으로 쓴 경과 문서에 기록한다.
def record_answers(data: dict, runner: str, answers: list | None = None) -> None:
    # 이번 턴 질문 답을 읽고 식별자 꼬리를 뗀 질문과 대상 경과 문서로 묶는다.
    if answers is None:
        answers = question_answers(turn(read_session(transcript_of(data, runner)))[2])
    groups: dict[Path, list] = {}
    for q, answer in answers:
        # 문제 정의 확인 답은 같은 작업의 경과 문서 문제 정의 절에 기록한다. 문서 생성 전 답은 다음 기록 때 세션 기록에서 다시 읽는다.
        if (problem := PROBLEM_QUESTION.fullmatch(" ".join(str(q.get("question") or "").split()))):
            for target in tasks_of(data, runner, problem[6])[:1]:
                if target.is_file():
                    rewrite(target, lambda text, t=target, item=(q, answer): problem_record(data, t, text, [item]))
            continue
        text, tag = split_tag(q.get("question") or "")
        target = (tasks_of(data, runner, tag) or [None])[0]
        if target is not None:
            groups.setdefault(target, []).append(({**q, "question": text}, answer))
    if not answers and (path := written_status(data, runner)) is not None:
        groups.setdefault(path, [])
    for path, items in groups.items():
        record_to(path, items)


# 현재 세션의 작업 문서에 실제 사용자 기록 시각을 반영하며 같은 입력의 재처리는 파일을 바꾸지 않는다.
def record_times(data: dict, runner: str) -> str:
    path = written_status(data, runner)
    if path is None or not path.is_file():
        return ""
    rewrite(path, lambda text: approval_record_times(data, path, problem_record(data, path, text)))
    probe = read(path).replace("기록 없음", "<자동 시각>")
    missing = ["사용자 승인 시각"] if re.search(r"(?m)^\s*- 사용자 승인 시각: 기록 없음\s*$",
                                             approval_record_times(data, path, probe)) else []
    return (f"{path.as_posix()}: {', '.join(missing)}은 대응 사용자 이벤트의 timestamp를 확인할 수 없어 기록 없음으로 남겼습니다."
            if missing else "")


# 형식을 지킨 검토 질문의 선택지 답만 path 경과 문서 검토 필요 절에 한 줄로 기록하고, 확정 안(폐기와 재조사 제외)은 같은 작업 폴더 명세 문서 요구사항 목록 끝에 추가한다.
# 직접 입력 답과 번호 접두가 없는 질문은 기록하지 않는다.
def record_to(path: Path, answers: list) -> None:
    # 질문마다 검토 필요 한 줄과 명세 문서 요구사항 추가 작업을 만든다.
    now = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    reviews, ops, names = {}, [], {}
    flat = lambda text: " ".join(str(text).split())
    known = {m[1]: m[2] for line, _ in review_entries(read(path)) if (m := re.match(r"- 검토 (\d+)\. ([^:\n]+)", line))}
    spec = path.with_name("spec.md")
    listed = [(k, t) for k, t in spec_blocks(section(read(spec), "요구사항 목록")) if k is not None] if spec.is_file() else []
    fresh = max((k for k, _ in listed), default=0) + 1
    line = lambda number, name, label: f"- 검토 {number}. {name}: {label} (사용자 검토 시각: {now})"
    for q, answer in answers:
        # 선택지 답이 아니면 기록하지 않는다.
        question = flat(q.get("question") or "")
        picked = picked_labels(q, answer)
        if not picked:
            continue
        drop, rev = DROP.match(question), REVIEW.match(question)

        # 폐기 답은 확정 안 자리에 `폐기`를 기록하고 재조사 답은 기록하지 않는다.
        if (rev and picked == ["폐기"]) or (drop and confirmed(answer)):
            number = (rev or drop)[1]
            reviews[f"검토 {number}"] = line(number, known.get(number) or (rev[2] if rev else "폐기"), "폐기")
        elif rev and picked != ["재조사"]:
            number, name = rev[1], rev[2]
            label = ", ".join(picked)
            reviews[f"검토 {number}"] = line(number, name, label)

            # 고른 선택지의 설명을 확정 안 라벨과 함께 기대 결과에 쓴다. 같은 이름의 요구사항이 있으면 그 항목을 갱신한다.
            desc = " ".join(flat(o.get("description") or "") for o in q.get("options") or []
                            if isinstance(o, dict) and RECOMMENDED.sub("", flat(o.get("label") or "")) in picked).strip()
            same = next((k for k, t in listed if re.match(rf"### {k}\. {re.escape(name)}\s*$", t.splitlines()[0])), None)
            target = same or fresh
            fresh += 0 if same else 1
            names[target] = name
            ops.append(("req", target, label + (f": {desc}" if desc else ""),
                        "문제 " + ", ".join(re.findall(r"\d+", rev[4]))))

    # 명세 문서가 있으면 요구사항 목록에 추가한다.
    spec_changed = False
    if ops and spec.is_file():
        try:
            before = read(spec)
            rewrite(spec, lambda text: splice(text, "요구사항 목록", lambda b: apply_spec(b, ops, names)))
            spec_changed = read(spec) != before
        except (OSError, ValueError):
            pass

    # 검토 필요 한 줄과 구현 승인 및 작업 단계를 갱신한다. 명세가 바뀌었으면 구현 승인을 승인 대기로 되돌린다.
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
STATUS_PATH = re.compile(rf"{TASK_REL}/[\w.\-]+/(?:status|spec)\.md")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "apply_patch", "exec")
TIME_LINE = re.compile(r"\(사용자 검토 시각: [^)]*\)")
APPLY_CALL = re.compile(r"tools\.apply_patch\(")


REQUEST_APPROVED = lambda text: any(APPROVED.match(l) for l in section(text, "구현 승인 범위").splitlines())
