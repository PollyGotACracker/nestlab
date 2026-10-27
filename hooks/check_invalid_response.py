"""Stop hook: 메인 에이전트 응답에서 실수 및 위반을 인정하는 표현을 찾아 사건 문서 초안에 기록한다.

호출자: Stop hook 설정(shared/settings)
반환: 
- 감지하면 stdout에 `{"decision": "block", "reason": REASON_INCIDENT, "systemMessage": ...}` JSON을 출력하고, 그 밖에는 출력이 없다.
- record()는 block 사유 문자열 또는 None을 반환한다.
입력: hook 입력 JSON을 stdin으로 받는다(last_assistant_message, transcript_path, session_id).
한계: 표현 목록 기반 감지라 목록 밖 표현은 놓치고, 목록 안 표현은 문맥을 오판할 수 있다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))

from sub_docs import project_root, task_id
from sub_role import first_line, resolve_role
from sub_session import _claude_entry, _codex_entry, norm, session_path, turn
from save_subagent_result import fence_for

# 사건 문서는 세션 저장소와 무관하게 이 hook의 원본 저장소(nestlab)에 저장한다. 테스트가 교체한다.
INCIDENT_ROOT = Path(__file__).resolve().parents[1]

# 표현 목록 기반 감지이다. 놓치는 표현이 쌓이면 해당 유형에 추가한다.
ADMIT_TYPES = {
    "직접 인정": (
        r"죄송|(?<![가-힣])(?:제|저의)(?: [가-힣]+(?<![엔에]))? (?:실수|잘못|착오)"
        r"|(?<![가-힣])제가 (?:실수|잘못|착오|틀렸)|(?:실수|잘못)했|제가 지키지 않|어긴|어겼|떠넘겼"
        r"|(?:지적|불만|말씀)[^.\n]{0,10}?맞습니다"
    ),
    "절차 및 검증 누락": (
        r"잘못 (?:이해|해석|읽|봤|판단|말씀|맞췄|잡았)|제가 엉뚱|잘못 (?:판정|판단)|판단(?:은|이) 틀|(?:잘못된|잘못) 판단"
        r"|단정(?:했|한 것)|정(?:한|하신) (?:게|것이|것은|건|거) (?:아니|아님)|신 적이 없"
        r"|위반(?:했|이었)|어(?:겼|긴)|부적절했|(?:절차|허락|근거|비교|확인|검토) 없이"
        r"|임의로|(?<![가-힣])제 판단으로|다르게 하시려면"
        r"|(?:절차|Skill|스킬|지침|규칙|양식|승인|기록|지시)들?(?:을|를|도|은|는)?"
        r"\s[^.\n]{0,20}?(?:묻|입증되|입증하|적용하|따르|지키|받|작성하|읽|검토하)지 않"
        r"|확인(?:하지 않고| 안 하고)"
        r"|확인한 사실이 아니|다만 이것이|사실과 다|(?:근거|이유|출처)가 없었|근거 없는"
        r"|놓(?:쳤|치고)|빠뜨|빼먹|누락(?:했|입니다|시켰)|건너뛰었(?!을 (?:수|가능성|지도))"
        r"|(?<![가-힣])제가 생략|안 했(?:습니다|어요)|했어야 했|지 않은 채|야 했(?:는데|습니다|어요)"
    ),
    "작업 번복": (
        r"망가뜨|철회|낭비(?:했|한 것|시켰)|거둘게요|거둡니다|거둬들입니다|거두겠습니다|생각하지 않고|손부터 댔|정정(?:합니다|하겠습니다|할게요)"
    ),
    "반복 지적": (
        r"(?:이미|반복) 지적|제가 또|이번 턴(?:에도|도)|같은 실수|만 반복했"
    )
}
# 다른 단어의 일부로 나오는 패턴(실제 실수의 '제 실수', 시행착오의 '착오')만 앞 글자 검사를 둔다.
ADMIT = re.compile("|".join(ADMIT_TYPES.values()))
INLINE_CODE = re.compile(r"`+[^`\n]*`+|\"[^\"\n]*\"|“[^”\n]*”|'[^'\n]*'|‘[^’\n]*’")
FENCE = re.compile(r"^\s*(```|~~~)")
TASK_REF = re.compile(r"log/task/[\w.\-]+/(?:status|spec|research|refute)\.md|log/state/[\w.\-]+\.md")  # 둘째 갈래는 임시 경로이다.
PATH = re.compile(
    r"(?<![\w:/\\])(?:/?[A-Za-z]:[\\/]|\\\\|~[\\/]|\.{1,2}[\\/]|/)[^\s`<>\"'\[\](){}*,;]+"
    r"|(?<![\w:/\\])(?:[\w.@~-]+[\\/])+[^\s`<>\"'\[\](){}*,;]+"
    r"|(?<![\w.])\b[\w.-]+\.(?:md|py|jsonl?|ya?ml|toml|txt|csv|log|js|tsx?|jsx|html|css)\b(?::\d+)?")
# 오탐으로 확인된 문맥이다(사건 20261024-13c433b9).
META = re.compile(r"감지 표현|감지된|오탐|사건 판정")  # 사건 판정과 오탐 분석 문장
DENIAL = re.compile(r"아니(?:에요|예요|다|ㅂ니다|합니다)|아닙니다|아닌(?:\s|$)")  # 표현 뒤의 부정 서술
PASSIVE = re.compile(r"철회(?:된|되었|돼|됐)")  # 다른 주체의 결정을 전한 수동 서술
# 이미 기록된 위반의 인용(표 행), 지침이 요구하는 미적용 보고, 고친 결과 보고는 새 인정이 아니다.
# 1인칭 인정 표현(죄송, 제 실수, 제가)이 든 줄은 이 제외를 받지 않는다.
REPORT = re.compile(r"(?:범위 밖|승인 (?:범위|전)|권한)[^.\n]*적용하지 않"
                    r"|(?:이미|모두|전부) (?:고쳤|수정했|반영했|바로잡았)")
FIRST_PERSON = re.compile(r"죄송|제 (?:실수|잘못)|제가")
# 인용과 설명 문장에도 흔한 표현이다. 1인칭 문맥이나 직전 사용자 지적이 있을 때만 감지한다.
CONDITIONAL = {"어긴", "지 않은 채", "확인 없이", "근거 없는"}
SPEAKER = re.compile(r"죄송|제 (?:실수|잘못|판단)|제가|저는|저의|저도")
POINTED = re.compile(r"지적|잘못|틀렸|틀린|어겼|어기|위반|않았|안 지|맞지 않|불만|왜 ")
# 같은 문장 뒤쪽이 자기 행동을 과거형으로 인정하는 서술이면 문맥 없이도 감지한다. `못했습니다` 같은 부정은 뺀다.
SELF_PAST = re.compile(r"(?:것|사건|문장)(?:이 원인)?입니다(?!만)|(?<!못)(?:냈|했|썼)었?습니다")
# 도구와 코드가 주어인 문장은 코드 동작 설명이므로 자기 행동 인정으로 보지 않는다.
CODE_SUBJECT = re.compile(r"(?:도구|hook|훅|코드|함수|스크립트|모듈|정규식)(?:는|은)\s")


# 경로 링크의 설명은 보존한다. 잘린 링크와 인용된 공백 포함 경로도 제거한다.
def without_paths(text: str) -> str:
    # URL은 그대로 두고 URL 사이 조각만 재귀로 처리한다.
    parts = re.split(r"(https?://[^\s`<>\"'\[\]()]+)", text, flags=re.I)
    if len(parts) > 1:
        return "".join(part if i % 2 else without_paths(part) for i, part in enumerate(parts))

    # 링크 설명은 보존하고 인용된 경로와 일반 경로는 지운다.
    text = re.sub(r"\[([^\]\n]*)\]\(([^)\n]*\)|[^\s)\n]+)",
                  lambda m: without_paths(m[1]) if PATH.search(m[2]) else m[0], text)
    text = re.sub(r"(`+|[\"'])([^\n]*?)\1",
                  lambda m: "" if PATH.match(m[2]) else m[1] + PATH.sub("", m[2]) + m[1], text)
    return PATH.sub("", text)


# 코드 블록과 인라인 코드, 따옴표 인용을 뺀 본문을 반환한다.
def plain(text: str) -> str:
    out, fence = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fence = not fence
        elif not fence:
            out.append(INLINE_CODE.sub("", line))
    return "\n".join(out)


# 줄마다 인정 표현을 찾아 (줄, 표현)을 낸다. 제목 줄, 오탐 분석 줄, 표 행, 미적용 및 수정 결과 보고, 사용자 문장 인용, 뒤따르는 부정 서술, 수동 서술은 뺀다.
def scan(text: str, user: str = "", pattern: re.Pattern = ADMIT):
    # (줄, 일치 표현) 쌍을 차례로 낸다.
    for line in text.splitlines():
        # 제목, 표 행, 오탐 분석 줄과 결과 보고 줄은 건너뛴다.
        if line.lstrip().startswith(("#", "|")) or META.search(line):
            continue
        if REPORT.search(line) and not FIRST_PERSON.search(line):
            continue

        # 일치한 표현 중 사용자 인용, 부정 서술, 수동 서술은 뺀다.
        for m in pattern.finditer(line):
            window = line[max(0, m.start() - 6):m.end() + 6].strip()
            if user and (window in user or m.group(0) in user and len(m.group(0)) >= 4):
                continue
            if DENIAL.search(re.split(r"[.!?。]", line[m.end():], maxsplit=1)[0]):
                continue
            if PASSIVE.match(line, m.start()):
                continue
            if m.group(0) in CONDITIONAL and not (
                    SPEAKER.search(line) or POINTED.search(user)
                    or (SELF_PAST.search(re.split(r"[.!?。]", line[m.end():], maxsplit=1)[0])
                        and not CODE_SUBJECT.search(line[:m.start()]))):
                continue
            yield line, m.group(0)


# 인정 표현 목록을 정렬해 반환한다.
def admissions(text: str, user: str = "", pattern: re.Pattern = ADMIT) -> list[str]:
    return sorted({hit for _, hit in scan(text, user, pattern)})


# 서브에이전트의 hook 입력이면 True이다.
def is_child(data: dict) -> bool:
    if data.get("agent_id"):
        return True
    source = first_line(data.get("transcript_path")).get("source")
    return isinstance(source, dict) and bool(source.get("subagent"))


# 현재 턴의 응답 위치와 가장 최근 명시된 작업 참조를 실제 원문에서 확인한다.
def context(path: str, message: str, cwd: str | Path | None = None) -> tuple[str, str, str, int | None]:
    # 대화 기록 파일을 읽어 항목과 원문 행 번호를 모은다.
    entries, lines = [], []
    try:
        with open(path, encoding="utf-8") as f:
            for number, line in enumerate(f, 1):
                try:
                    data = json.loads(line)
                    entry = (_codex_entry if "payload" in data else _claude_entry)(data)
                except (ValueError, AttributeError, TypeError):
                    continue
                if entry and (entry["text"].strip() or entry["calls"]):
                    entries.append(entry)
                    lines.append(number)
    except OSError:
        pass

    # 현재 턴의 마지막 assistant 응답이 hook이 받은 응답과 같은지 확인해 응답 위치를 구한다.
    user, _, current = turn(entries)
    assistants = [i for i in range(len(entries) - len(current), len(entries))
                  if entries[i]["role"] == "assistant" and entries[i]["text"].strip()]
    index = assistants[-1] if assistants else None
    located = index is not None and norm(entries[index]["text"]) == norm(message)
    response_line = lines[index] if located else None

    # 가장 최근에 명시된 작업 문서 참조를 뒤에서부터 찾는다.
    # 응답 뒤의 참조나 다른 턴의 같은 문구를 이번 응답의 근거로 쓰지 않는다.
    considered = entries[:index + 1] if located else entries
    for i in range(len(considered) - 1, -1, -1):
        entry = considered[i]
        refs = set(TASK_REF.findall(entry["text"] + "\n" +
                                    json.dumps(entry["calls"], ensure_ascii=False)))
        if not refs:
            continue
        tasks = {task_id(f"입력 문서: {ref}") for ref in refs}
        evidence = f"{lines[i]}"
        if len(tasks) == 1 and all((project_root(cwd) / ref).is_file() for ref in refs):
            return user, next(iter(tasks)) or "미확인", evidence, response_line
        return user, "미확인", evidence + ", 모호하거나 문서 없음", response_line
    return user, "미확인", "미확인", response_line


NOTICE_INCIDENT = "직전 응답에 문제가 있어 사건 문서에 기록했습니다: {link}"
REASON_INCIDENT = ("직전 응답에서 문제 표현({hits})을 감지했습니다. 사건 분석은 별도 세션이 진행하므로 사건 문서를 작업하지 마십시오.\n"
                   "위반한 지침이 무엇인지 확인한 후, 첫 줄에 아래 문장을 넣고, 위반 행동을 일으킨 판단 과정 요약과 기존 작업을 위한 내용으로 응답하십시오.\n"
                   + NOTICE_INCIDENT)
MAX_BLOCKS = 8  # Claude Code 연속 continuation 한도 기본값. Codex는 한도가 없어 이 값이 유일한 반복 한도이다.
WINDOWS_FLAGS = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


# 사건 분석 세션을 hook 종료 뒤에도 살아남는 분리 프로세스로 띄운다. 실행 실패는 무시한다.
def launch_analysis(path: Path, runner: str) -> None:
    command = [sys.executable, str(INCIDENT_ROOT / "tools" / "run_workflow.py"),
               "--incident", str(path), "--runner", runner]
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}

    # Windows는 분리 플래그를, 그 밖은 새 세션을 쓴다. 작업 객체가 분리를 막으면 해당 플래그만 뺀다.
    try:
        if os.name == "nt":
            try:
                subprocess.Popen(command, creationflags=WINDOWS_FLAGS | BREAKAWAY, **quiet)
            except OSError:
                subprocess.Popen(command, creationflags=WINDOWS_FLAGS, **quiet)
        else:
            subprocess.Popen(command, start_new_session=True, **quiet)
    except OSError:
        pass


# 대화 기록 경로를 반환한다. 입력에 없으면 session_id로 찾는다.
def transcript_of(data: dict, runner: str) -> str:
    return data.get("transcript_path") or (session_path(data, runner) if data.get("session_id") else "")


# 이 세션의 사건 문서 경로를 반환한다. 파일은 만들지 않는다.
def incident_path(data: dict, now: datetime) -> Path:
    sid = (data.get("session_id") or "unknown")[:8]
    directory = INCIDENT_ROOT / "log" / "incident"
    path = directory / f"{now:%Y%m%d}-{sid}.md"
    if not path.is_file():
        pattern = f"{now:%Y%m%d}-[0-9][0-9][0-9][0-9]-{sid}.md"
        path = next((p for p in sorted(directory.glob(pattern)) if p.is_file()),
                    directory / f"{now:%Y%m%d-%H%M}-{sid}.md")
    return path


# 사건 문서의 `[사건-id](경로)` 링크를 반환한다. cwd가 저장소 안이면 저장소 기준 상대 경로, 아니면 절대 경로이다.
def incident_link(path: Path, cwd: str | Path | None) -> str:
    target = str(path)
    if cwd:
        try:
            Path(cwd).resolve().relative_to(INCIDENT_ROOT.resolve())
            target = path.resolve().relative_to(INCIDENT_ROOT.resolve()).as_posix()
        except ValueError:
            pass
    return f"[{path.stem}]({target})"


# 사건 문서에 기록된 감지 항목 중 같은 사용자 메시지의 수를 반환한다. 문서가 없으면 0이다.
def hit_count(path: Path, user: str) -> int:
    if not path.is_file():
        return 0
    request = without_paths(user)
    fence = fence_for(request)
    block = f"  - 사용자 메시지:\n\n    {fence}\n    " + request.replace("\n", "\n    ") + f"\n    {fence}\n"
    return path.read_text(encoding="utf-8").count(block)


# 감지 표현을 사건 문서에 기록하고 그 경로를 반환한다. 같은 사용자 메시지의 첫 감지에서만 분석 세션을 띄운다.
def record_hits(data: dict, runner: str, hits: list[str]) -> Path:
    # 대화 기록에서 사용자 메시지, 작업 근거, 응답 위치를 찾는다.
    transcript = transcript_of(data, runner)
    message = data.get("last_assistant_message") or ""
    user, task, evidence, response_line = context(transcript, message, data.get("cwd"))

    # 사건 문서 경로를 정하고 없으면 머리 정보와 함께 만든다.
    now = datetime.now()
    session_id = data.get("session_id") or "unknown"
    path = incident_path(data, now)
    role = resolve_role(data) or "미확인"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# {runner}: {path.stem}\n\n"
            f"- 사건 ID: {path.stem}\n"
            f"- runner: {runner}\n"
            f"- 세션 ID: {session_id}\n"
            f"- 관련 작업 ID: {task}\n"
            f"- 실행 위치: {data.get('cwd') or '미확인'}\n"
            f"- 발생 및 확인 시점: {now:%Y-%m-%d %H:%M:%S}\n"
            f"- 관련 담당: {role}\n"
            f"- 조치 상태: 미착수\n\n"
            f"## 감지 기록\n\n", encoding="utf-8")

    # 사용자 메시지와 응답 발췌를 경로 제거 뒤 코드 블록으로 감지 항목을 만든다.
    excerpt = without_paths(message)
    request = without_paths(user)
    request_fence, excerpt_fence = fence_for(request), fence_for(excerpt)
    entry = (f"- {now:%Y-%m-%d %H:%M:%S}\n"
             f"  - 감지 표현: {', '.join(hits)}\n"
             f"  - 관련 작업 ID: {task}\n"
             f"  - 근거 작업 위치: {evidence}\n"
             f"  - runner: {runner}\n"
             f"  - 응답 위치: "
             + (str(response_line) if response_line else "미확인") + "\n"
             f"  - 사용자 메시지:\n\n"
             f"    {request_fence}\n    " + request.replace("\n", "\n    ") + "\n"
             f"    {request_fence}\n"
             f"  - 응답 발췌:\n\n"
             f"    {excerpt_fence}\n    " + excerpt.replace("\n", "\n    ") + "\n"
             f"    {excerpt_fence}\n")

    # 다른 절이 생긴 뒤에도 감지 기록은 `## 감지 기록` 절 끝(다음 `## ` 절 앞)에 둔다.
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^## 감지 기록[^\n]*\n(?:.*?\n)??(?=^## |\Z)", text, re.M | re.S)
    if m:
        head = text[:m.end()].rstrip("\n") + "\n"
        tail = text[m.end():]
        path.write_text(head + entry + ("\n" + tail if tail else ""), encoding="utf-8")
    else:
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)

    # 같은 사용자 메시지의 재감지에서는 기록만 하고 분석 세션을 띄우지 않는다.
    if hit_count(path, user) == 1:
        launch_analysis(path, runner)
    return path


# 감지하면 기록하고 (재작성 지시 사유, 사용자 경고)를 반환한다. 감지가 없으면 None이고, block 한도를 넘으면 사유가 None이다.
def detect(data: dict, runner: str = "claude") -> tuple[str | None, str] | None:
    # 대화 기록 경로를 정하고 서브에이전트 응답은 건너뛴다.
    data = {**data, "transcript_path": transcript_of(data, runner)}
    message = data.get("last_assistant_message") or ""
    if is_child(data):
        return None

    # 인정 표현이 있는지 확인한다.
    text = plain(message)
    if not ADMIT.search(text):
        return None
    user = context(data["transcript_path"], message, data.get("cwd"))[0]
    hits = admissions(text, user)
    if not hits:
        return None

    # 기록하고 분석 세션을 띄운 뒤, block 한도 안이면 재작성 지시를 반환한다.
    path = record_hits(data, runner, hits)
    link = incident_link(path, data.get("cwd"))
    notice = NOTICE_INCIDENT.format(link=link)
    if hit_count(path, user) > MAX_BLOCKS:
        return None, notice
    return REASON_INCIDENT.format(hits=", ".join(hits), link=link), notice


# 감지하면 기록하고 재작성 지시 사유를 반환한다. block 한도를 넘으면 기록만 하고 None이다.
def record(data: dict, runner: str = "claude") -> str | None:
    result = detect(data, runner)
    return result[0] if result else None


# stdin의 hook 입력으로 사건을 기록하고 감지하면 block JSON을 출력한다. 항상 0을 반환한다.
def main() -> int:
    # 입출력 인코딩을 UTF-8로 맞추고 인자를 읽는다.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    args = parser.parse_args()

    # 기록 오류는 통과시키고, 감지하면 block으로 출력한다.
    try:
        reason, notice = detect(json.load(sys.stdin), args.runner) or (None, "")
    except Exception:
        return 0
    if reason:
        json.dump({"decision": "block", "reason": reason, "systemMessage": notice}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
