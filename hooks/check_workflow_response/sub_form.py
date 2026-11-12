"""응답 본문의 양식(조사·구현·실측·완료 승인 요청, 검토 선택 요청, 중단 판정 요청)과 안 블록 출처를 검사한다.

호출자: hooks/check_workflow_response.py, hooks/replace_blocked_response.py
입력: 응답 본문 문자열, 저장소 루트
반환: 누락 항목 목록 또는 양식 이름
한계: 정규식 판정이므로 자연어 의미는 판정하지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from check_invalid_response import plain, transcript_of
from sub_claim import PATH, URL
from sub_docs import FENCE_LINE, HEADING, legacy, section, template_text, template_titles, titles
from sub_question import UNCONFIRMED
from sub_session import previous_stop_reason, read_session, turn, user_messages
from sub_document import Document, dropped, filled, number_of, requirements, values
from sub_write import read


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


# 인라인 코드에 쓴 실제 승인 입력은 읽고, 코드 블록과 인용 및 예시 줄은 제외한다.
def requests_approval(message: str) -> bool:
    text = re.sub(r"(?<!`)`([^`\n]+)`(?!`)", r"\1", body_of(message))
    for line in plain(text).splitlines():
        if re.match(r"^\s*(?:[-*]\s*)?(?:e\.g\.|예시\s*[:：]|예\s*[:：])", line):
            continue
        if re.search(r"승인(?:으로|을|이라고|이라고만)?\s*(?:입력(?:을)?\s*)?(?:답|응답|회신|보내|전송|입력)", line) and re.search(
                r"(?:주십|주세요|주세|달라|바랍|하십시오|하시면|하면|보내면|보내시면|입력하면|입력하시면)", line):
            return True
    return False


# 승인 스킬 양식의 필수 절 제목이나 구체적인 본문이 빠진 경우 누락 목록을 반환한다.
def approval_form_gap(body: str, plans: list[str] | None = None) -> list[str]:
    missing = [] if APPROVAL_BLOCK.search(body) else ["구현 승인 요청"]
    for title in template_titles("approval-implement"):
        content = re.sub(r"<!--.*?-->", "", section(body, title), flags=re.S).strip()
        if title != "구현 승인 요청" and (title not in titles(body) or not filled(content)):
            missing.append(title)
    if missing:
        return missing

    concrete = lambda value: filled(value) and not re.search(r"<[^<>\n]+>|미확정|확정 대기", value)
    blocks = lambda title: [part for part in re.split(r"(?m)^- ", section(body, title))[1:] if part.strip()]

    # 기존 값 추출기로 반복 필드를 읽되 빈 값과 자리표시자는 승인 전에 차단한다.
    def fields(block: str, title: str, names: tuple[str, ...]) -> None:
        for name in names:
            found = values(block, name)
            if len(found) != 1 or not concrete(found[0]):
                missing.append(f"{title} > {name}: 구체적인 값 필요")

    related = {}
    for block in blocks("관련 요구사항"):
        line = block.splitlines()[0]
        match = re.fullmatch(r"요구사항 (\d+)\. ([^:\n]+):[ \t]*(.+)", line)
        if not match or not concrete(match[3]):
            missing.append("관련 요구사항 > `요구사항 <번호>. <이름>: <확정된 결과>` 필요")
            continue
        number, name = match[1], match[2].strip()
        if number in related:
            missing.append(f"관련 요구사항 > 요구사항 {number}: 중복 번호")
        related[number] = name
        fields(block, f"관련 요구사항 > 요구사항 {number}", ("이번 변경",))
    if not related:
        missing.append("관련 요구사항 > 번호와 이름이 있는 개별 항목 필요")

    # 명세의 기대 결과가 실제로 확정된 요구만 승인 요청에 연결한다.
    if plans is not None:
        known = {}
        for plan in plans:
            try:
                known.update({number_of(name): (re.sub(r"^(?:요구사항 )?\d+\. ", "", name), content)
                              for name, content in requirements(Document(plan))})
            except ValueError:
                continue
        for number, name in related.items():
            expected = known.get(number)
            if expected is None or expected[0] != name:
                missing.append(f"관련 요구사항 > 요구사항 {number}. {name}: 명세의 번호와 이름 일치 필요")
            elif dropped(expected[0], expected[1]) or not values(expected[1], "기대 결과") or not all(
                    concrete(value) for value in values(expected[1], "기대 결과")):
                missing.append(f"관련 요구사항 > 요구사항 {number}: 명세의 확정된 기대 결과 필요")

    preliminary = section(body, "사전 작업").strip().removeprefix("- ").strip()
    if preliminary != "해당 없음":
        for block in blocks("사전 작업"):
            fields(block, "사전 작업", ("목적", "절차", "발생 가능한 상황"))
        if not blocks("사전 작업"):
            missing.append("사전 작업 > 개별 작업 또는 `해당 없음` 필요")

    # 명세 문서 절에는 명세 문서 링크가 있어야 한다. 변경 대상과 Test Case는 명세 문서의 검사가 담당한다.
    if not re.search(r"\[[^\]\n]+\]\([^)\n]+\)", section(body, "명세 문서")):
        missing.append("명세 문서 > 명세 문서 링크 필요")
    return missing + [g for g in repeat_gaps(body, "approval-implement") if g not in missing]


# 응답 본문 제목이 가리키는 양식 이름이다. 없으면 None이다.
def form_of(body: str) -> str | None:
    return next((name for name, heading in FORM_HEADINGS.items()
                 if re.search(rf"(?m)^#{{1,6}} {heading}\s*$", body)), None)


# 양식의 필수 제목과 `- 항목:` 줄 중 응답 본문에 없거나 값이 빈 것의 목록을 반환한다.
# 절 채움은 코드 블록을 포함한 원문(raw)으로, 항목 줄은 코드 블록을 뺀 본문으로 판정한다.
def form_gap(body: str, form: str, raw: str | None = None) -> list[str]:
    # 양식 주석을 뺀 본문에서 필수 제목과 항목 이름을 구한다.
    template = re.sub(r"<!--.*?-->", "", template_text(form), flags=re.S)
    heading = FORM_HEADINGS[form]
    own = [t for t in template_titles(form) if not re.fullmatch(heading, t)]
    labels = list(dict.fromkeys(re.findall(r"(?m)^\s*- ([^:\n<`|]+):", template)))

    # 제목은 본문이 있어야 하고, 항목은 값이 있는 줄이 하나 이상이어야 한다.
    source = raw or body
    # 구조 변경 절은 변경이 없을 때 `없음`만 쓴다.
    ok = lambda t: filled(section(source, t)) or (t == "구조 변경" and section(source, t).strip().rstrip(".") == "없음")
    missing = [t for t in own if t not in titles(source) or not ok(t)]
    missing += [l for l in labels if not re.search(rf"(?m)^\s*- {re.escape(l)}:\s*\S", body)]
    # 모델 호출 실측 항목명은 동적 라벨이므로 해당 절의 최상위 목록 줄로 확인한다.
    if form == "approval-measure" and not re.search(
            r"(?m)^- [^\s:<>\n][^:<>\n]*:[ \t]+요구사항 [^\s<>.]+\.[ \t]+[^\s<>]",
            re.split(r"(?m)^#{1,6} ", (section(body, "모델 호출 실측 항목") or section(body, "실측 항목")), maxsplit=1)[0]):
        missing.append("모델 호출 실측 항목의 항목명과 요구사항")
    return missing + [g for g in repeat_gaps(body, form) if g not in missing]


# 출처 하나가 http(s) URL이거나 저장소 기준 실재하는 `파일:행`(또는 `파일:시작-끝`)이면 None을 반환한다.
# 아니면 파일 없음, 행 범위 초과, 형식 오류 사유이다. url_only이면 URL만 허용한다.
def source_gap(source: str, root: Path, url_only: bool = False) -> str | None:
    source = source.strip()
    if re.fullmatch(r"https?://\S+", source):
        return None
    if url_only:
        return f"출처 `{source[:40]}`는 URL이어야 합니다"
    found = FILE_SOURCE.match(source)
    if not found:
        return f"출처 `{source[:40]}`는 URL 또는 `파일:행` 형식이어야 합니다"
    path = root / found[1]
    if not path.is_file():
        return f"출처 파일 없음 `{found[1]}`"
    lines = len(read(path).splitlines())
    if int(found[3] or found[2]) > lines or int(found[2]) < 1:
        return f"출처 행 범위 초과 `{found[0]}`(파일 {lines}행)"
    return None


# 후보 보고의 안 블록마다 항목 줄 존재, 줄별 출처 실재, 자리표시자 잔존, 확인하지 않은 내용을 검사해 사유 목록을 반환한다.
# 한계와 위험 줄은 URL 출처만 허용하며 값이 `없음`이면 출처를 요구하지 않는다. 사후 모델 호출 실측 줄은 미확인 검사에서 제외한다.
def choice_gaps(body: str, root: Path) -> list[str]:
    heads = list(CHOICE_BLOCK.finditer(body))
    gaps = []
    for index, head in enumerate(heads):
        text = body[head.start():(heads[index + 1].start() if index + 1 < len(heads) else len(body))]
        # 다음 같은 수준 이하 제목 앞까지만 안 블록으로 본다.
        text = re.split(r"(?m)^#{1,3} (?!안 )", text, maxsplit=1)[0]
        number = head[1]
        if re.search(r"<[^<>\n]+>", head[0]):
            gaps.append(f"안 {number} 제목에 자리표시자가 남아 있습니다")
        for label in CHOICE_LINES:
            line = re.search(rf"(?m)^- {label}:[ \t]*(.*)$", text)
            if not line or not line[1].strip():
                gaps.append(f"안 {number} {label} 줄이 없거나 비었습니다")
                continue
            if re.search(r"<[^<>\n]+>", line[1]) and label != "예시":
                gaps.append(f"안 {number} {label} 줄에 자리표시자가 남아 있습니다")
            if label not in SOURCED_LINES:
                continue
            value = line[1].strip()
            if label == "한계와 위험" and value.split(" (출처")[0].strip() == "없음":
                continue
            tail = SOURCE_TAIL.search(value)
            if not tail:
                gaps.append(f"안 {number} {label} 출처 필요")
                continue
            reason = next((r for s in re.split(r",\s*", tail[1]) if (r := source_gap(s, root, label == "한계와 위험"))), None)
            if reason:
                gaps.append(f"안 {number} {label}: {reason}")
        for line in text.splitlines():
            word = None if line.startswith(MEASURE_LINE) else next((w for w in UNCONFIRMED if w in line), None)
            if word:
                gaps.append(f"안 {number}에 확인하지 않은 내용 `{word}`가 있습니다. 직접 확인한 뒤 다시 작성하십시오")
    return gaps


# 검증된 같은 턴의 Stop 재작성에서 원래 보고와 같은 제목의 보완 절을 결합한다.
def completed_response(data: dict, runner: str, message: str) -> str:
    body = body_of(message)
    kind_of = lambda value: form_of(body_of(value)) or ("approval-implement" if APPROVAL_BLOCK.search(body_of(value)) else None)
    if kind_of(message) or (len(body.strip().splitlines()) == 1 and re.match(r"^(?:위반한 지침:|오탐(?:[:： ]|입니다))", body.strip())):
        return message
    all_entries = read_session(transcript_of(data, runner))
    users = [entry for entry in all_entries if user_messages([entry])]
    turn_id = users[-1].get("turn_id") if users else None
    entries = turn(all_entries)[2]
    final = lambda entry: (entry["role"] == "assistant" and entry["text"].strip()
                           and entry.get("channel") in (None, "final") and entry.get("phase") in (None, "final_answer"))
    last = max((index for index, entry in enumerate(entries) if final(entry)), default=-1)
    current_turn = None
    if last >= 0 and entries[last]["text"] == message:
        current_turn = entries[last].get("turn_id")
        entries = entries[:last] + entries[last + 1:]
    active, form, waiting, active_turn = "", None, False, None

    def supplement(original: str, fragment: str, form: str) -> str:
        allowed = set(template_titles(form))
        headings = [(len(match[1]), match[2]) for line in body_of(fragment).splitlines() if (match := HEADING.match(line))]
        if not headings or any(title not in allowed for _, title in headings):
            return original
        level = min(depth for depth, _ in headings)
        template_levels = {match[2]: len(match[1]) for line in template_text(form).splitlines() if (match := HEADING.match(line))}
        for depth, title in headings:
            if depth != level:
                continue
            content = "\n" + section(fragment, title).strip() + "\n\n"
            if title in titles(original):
                original = splice(original, title, lambda _: content)
            else:
                original = original.rstrip() + "\n\n" + "#" * template_levels[title] + " " + title + "\n" + content
        return original

    for entry in entries:
        if entry.get("stop"):
            stop = entry["stop"]
            waiting = bool(active and all(not expected or stop.get("turn_id") in (None, expected) for expected in (turn_id, active_turn, current_turn)) and any(marker in stop["reason"] for marker in (
                REASON_APPROVAL_FORM, REASON_APPROVAL_PLAN, "양식의 필수 제목이나 항목", "착수 단계에서 같은 작업", "완료 선행 검사")))
        elif final(entry) and (not turn_id or entry.get("turn_id") in (None, turn_id)):
            candidate = kind_of(entry["text"])
            if candidate:
                active, form, waiting, active_turn = entry["text"], candidate, False, entry.get("turn_id")
            elif waiting:
                active, waiting = supplement(active, entry["text"], form), False
    return supplement(active, message, form) if waiting else message


# 실제 승인 서식 Stop의 보완 절은 같은 사용자 턴의 원래 전체 승인안이 있어야 검사할 수 있다.
def approval_original_gap(data: dict, runner: str, message: str) -> str | None:
    body = body_of(message)
    if APPROVAL_BLOCK.search(body) or form_of(body):
        return None
    headings = titles(body)
    allowed = set(template_titles("approval-implement")) - {"구현 승인 요청"}
    if not headings or not set(headings) <= allowed:
        return None
    path = transcript_of(data, runner)
    reason = previous_stop_reason(path)
    if not reason.startswith((REASON_APPROVAL_FORM, REASON_APPROVAL_PLAN)):
        return None
    entries = turn(read_session(path))[2]
    if any(APPROVAL_BLOCK.search(body_of(entry["text"])) for entry in entries
           if entry["role"] == "assistant" and entry.get("phase") in (None, "final_answer") and entry.get("channel") in (None, "final")):
        return None
    return (REASON_APPROVAL_FORM + " 같은 사용자 턴에 결합할 최초 전체 승인 보고가 없습니다. "
            "`승인 요청` 제목과 작업 문서 링크 및 양식의 다섯 절을 갖춘 원래 승인안을 작성한 뒤 해당 절을 보완하십시오.")
FENCE = re.compile(r"^\s*(```|~~~)")
TABLE = re.compile(r"^\s*\|.*\|\s*$", re.M)
LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S", re.M)
DIFF = re.compile(r"^(?:[+-]{3} |@@ )", re.M)
APPROVAL_BLOCK = re.compile(r"^#{1,6} (?:작업 승인 필요|구현 승인 요청)\s*$", re.M)

REASON_APPROVAL_FORM = "승인 요청 서식의 필수 절과 구체적인 본문을 채워 응답을 다시 작성하십시오."
REASON_APPROVAL_PLAN = "승인 요청의 명세 문서 검사에 실패했습니다. 사유: "


# 응답 본문 양식을 가리키는 제목이다. 제목이 있는 응답은 해당 양식의 필수 제목과 항목을 갖춰야 한다.
FORM_HEADINGS = {"request-stop": "중단 판정 요청",
                 "approval-complete": "완료 승인 요청", "request-choice": r"검토 \d+ \(\d+/\d+\)", "approval-research": "조사 승인 요청",
                 "approval-measure": "(?:모델 호출 )?실측 승인 요청"}


CHOICE_LINES = ("적용할 작업", "장점", "단점", "현재 동작", "한계와 위험", "사후 모델 호출 실측", "예시")
SOURCED_LINES = ("장점", "단점", "현재 동작", "한계와 위험")
CHOICE_BLOCK = re.compile(r"(?m)^#{3,6} 안 (\d+)\. .*$")
SOURCE_TAIL = re.compile(r"\(출처: ([^)]*)\)\s*$")
FILE_SOURCE = re.compile(r"^([^\s:]+):(\d+)(?:-(\d+))?$")
MEASURE_LINE = "- 사후 모델 호출 실측:"


# text에서 title로 시작하는 제목 절의 본문(다음 같은 수준 이상 제목 전까지)에 edit을 적용한 text 전체를 반환한다. 절이 없으면 text 그대로이다.
def splice(text: str, title: str, edit) -> str:
    # 제목 줄을 찾아 본문의 시작과 끝 위치를 구한다. 코드 블록 안의 줄은 제목으로 보지 않는다.
    start = end = None
    aliases = (title,) if title == "조사 승인 범위" and title in titles(text) else (title, legacy(title))
    level, fence, pos = 0, "", 0
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        f = FENCE_LINE.match(bare)
        if f and not fence:
            fence = f.group(1)
        elif f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence):
            fence = ""
        m = None if fence or f else HEADING.match(bare)
        if m and start is not None and len(m.group(1)) <= level:
            end = pos
            break
        if m and start is None and m.group(2).startswith(aliases):
            level, start = len(m.group(1)), pos + len(line)
        pos += len(line)
    if start is None:
        return text

    # 본문만 바꾸고 나머지는 그대로 둔다.
    end = len(text) if end is None else end
    return text[:start] + edit(text[start:end]) + text[end:]


PLACEHOLDER = re.compile(r"<[^<>\n]+>")
HEADING_LINE = re.compile(r"^(#{1,6}) (.+?)\s*$")
ITEM_LINE = re.compile(r"^(\s*)(-|\d+\.)\s+(.*)$")
LABEL_TEXT = re.compile(r"^([^:\n<`|]+):[ \t]*(.*)$")
FREE_LABELS = ("예시",)


# 텍스트를 (제목 수준, 제목, 본문 줄 목록) 구간으로 나눈다. 코드 블록 안의 줄은 제목으로 보지 않는다.
def split_sections(text: str) -> list[tuple[int, str, list[str]]]:
    out, fence = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fence = not fence
        found = None if fence else HEADING_LINE.match(line)
        if found:
            out.append((len(found[1]), found[2], []))
        elif out:
            out[-1][2].append(line)
    return out


# 양식 줄을 응답 줄과 대조하는 정규식 본문으로 바꾼다. 자리표시자는 임의의 내용에, 나머지는 문자 그대로 대응한다.
def shape(text: str) -> str:
    return "".join(".+?" if i % 2 else re.escape(part) for i, part in enumerate(re.split(r"(<[^<>\n]+>)", text)))


# 줄이 `- 항목: 값` 형식이면 (들여쓰기 길이, 항목, 값)을 반환한다. 아니면 None이다.
def label_of(line: str) -> tuple[int, str, str] | None:
    item = ITEM_LINE.match(line)
    label = LABEL_TEXT.match(item[3]) if item else None
    return (len(item[1]), label[1].strip(), label[2].strip()) if label else None


# 양식 구간 줄에서 반복 항목(자리표시자가 든 목록 줄과 그 아래 하위 항목 줄)과 반복에 속하지 않는 최상위 항목을 나눈다.
# 반환은 ([(들여쓰기, 목록 기호, 줄 본문, 하위 항목 이름 목록)], 구간 항목 이름 목록)이다.
def template_groups(lines: list[str]) -> tuple[list[tuple[int, str, str, list[str]]], list[str]]:
    groups, labels = [], []
    index = 0
    while index < len(lines):
        line = lines[index]
        item, label = ITEM_LINE.match(line), label_of(line)
        if item and not label and PLACEHOLDER.search(item[3]):
            subs, index = [], index + 1
            while index < len(lines) and (not lines[index].strip() or len(lines[index]) - len(lines[index].lstrip()) > len(item[1])):
                found = label_of(lines[index])
                if found:
                    subs.append(found[1])
                index += 1
            if subs:
                groups.append((len(item[1]), item[2], item[3], subs))
            continue
        if label:
            labels.append(label[1])
        index += 1
    return groups, labels


# 구간 줄에 `- 항목: 값` 줄이 있고 값이 비지 않았으며 자리표시자가 남지 않았는지 확인해 문제 목록을 반환한다.
def label_gaps(lines: list[str], labels: list[str], where: str) -> list[str]:
    found = {}
    for line in lines:
        label = label_of(line)
        if label:
            found.setdefault(label[1], []).append(label[2])
    gaps = []
    for name in labels:
        values_ = found.get(name)
        if not values_ or not any(values_):
            gaps.append(f"{where} > {name}")
        elif name not in FREE_LABELS and any(PLACEHOLDER.search(v) for v in values_):
            gaps.append(f"{where} > {name}: 자리표시자가 남아 있습니다")
    return gaps


# 양식 원본을 읽어 응답의 반복 블록(제목에 번호 자리표시자가 든 구간, 목록 항목)마다 모든 항목 줄을 검사한다.
def repeat_gaps(body: str, form: str) -> list[str]:
    return block_gaps(template_text(form), body, re.compile(FORM_HEADINGS.get(form, r"(?:작업 승인 필요|구현 승인 요청)")))


# 양식 본문(template)과 응답 본문(body)을 구간 단위로 대조한다. top이 있으면 양식의 첫 제목은 그 패턴의 응답 제목과 대응하는 양식 제목이다.
# 번호 자리표시자 제목은 같은 수준에서 같은 형태의 응답 제목마다, 목록 항목은 같은 들여쓰기의 같은 형태 줄마다 검사한다.
def block_gaps(template_body: str, body: str, top: re.Pattern | None = None) -> list[str]:
    template = split_sections(re.sub(r"<!--.*?-->", "", template_body, flags=re.S))
    response = split_sections(body)
    gaps = []
    for position, (level, title, lines) in enumerate(template):
        groups, labels = template_groups(lines)
        repeated = (top is None or position > 0) and PLACEHOLDER.search(title)
        if repeated:
            pattern = re.compile(shape(title) + "$")
            matched = [(t, l) for lv, t, l in response if lv == level and pattern.match(t)]
            if not matched:
                gaps.append(f"`{title}` 블록이 없습니다")
        elif top is not None and position == 0:
            matched = [(t, l) for lv, t, l in response if top.fullmatch(t)]
        elif top is None and position == 0:
            continue
        else:
            matched = [(t, l) for lv, t, l in response if t == title]
            # 양식 전체 대조(top 없음)에서는 응답에 없는 절도 누락이다. top이 있는 양식은 제목 누락 검사가 맡는다.
            if top is None and not matched:
                gaps.append(f"`{title}` 절이 없습니다")
        for name, inner in matched:
            if repeated and PLACEHOLDER.search(name):
                gaps.append(f"{name} 제목에 자리표시자가 남아 있습니다")
            gaps += label_gaps(inner, labels, name)
            for indent, bullet, text, subs in groups:
                pattern = shape(text)
                start = re.compile(rf"^ {{{indent}}}{r'-' if bullet == '-' else r'\d+\.'}\s+{pattern}$")
                for number, line in enumerate(inner):
                    if not start.match(line):
                        continue
                    end = number + 1
                    while end < len(inner) and (not inner[end].strip() or len(inner[end]) - len(inner[end].lstrip()) > indent):
                        end += 1
                    gaps += label_gaps(inner[number + 1:end], subs, f"{name} > {line.strip()[:30]}")
    return gaps


# 양식 줄 중 `(출처: …)` 칸이 있는 항목의 응답 줄이 실재하는 출처를 갖췄는지 검사한다. 안 블록 줄은 choice_gaps가 맡는다.
# 값이 `없음`이면 출처를 요구하지 않고, 양식 칸이 `<URL>`뿐이면 URL만 허용한다.
def source_gaps(body: str, form: str, root: Path) -> list[str]:
    gaps = []
    for line in re.sub(r"<!--.*?-->", "", template_text(form), flags=re.S).splitlines():
        label, tag = label_of(line), re.search(r"\(출처: ([^)]*)\)\s*$", line)
        if not label or not tag or label[1] in SOURCED_LINES:
            continue
        url_only = tag[1].strip() == "<URL>"
        for found in re.finditer(rf"(?m)^\s*[-*]\s+{re.escape(label[1])}:[ \t]*(.*)$", body):
            value = found[1].strip()
            if value.split(" (출처")[0].strip() == "없음":
                continue
            mark = SOURCE_TAIL.search(value)
            if not mark:
                gaps.append(f"{label[1]} 출처 필요")
                continue
            reason = next((r for s in re.split(r",\s*", mark[1]) if (r := source_gap(s, root, url_only))), None)
            if reason:
                gaps.append(f"{label[1]}: {reason}")
    return gaps
