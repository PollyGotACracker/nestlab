"""Stop hook: 메인 에이전트 응답에서 실수·위반 인정 표현을 찾아 사건 문서 초안에 기록한다.

사용: python record_incident.py --runner <claude|codex>
- 검사 대상은 hook 입력의 last_assistant_message이다. 코드 블록, 인라인 코드, 따옴표 인용 안은 검사하지 않는다.
- 일치하면 세션별 사건 문서 `log/incident/<YYYYMMDD>-<session 앞 8자>.md`에 항목을 추가한다.
  파일이 없으면 사건 양식의 머리 항목으로 만들고 조치 상태는 `미착수`로 둔다.
- 하위 에이전트 응답(Claude agent_id, Codex 하위 rollout)은 검사하지 않는다.
- 전체 세션 ID는 머리에, 작업 근거와 응답 위치는 원문 행 번호만 남긴다.
- 세션 경로를 사용하여 내부적으로 원문을 조회하고, 사용자 메시지와 응답은 경로 제거 뒤 코드 블록에 원문대로 기록한다.
- 기록 뒤 {"decision": "block", "reason": ...}를 출력해 근거를 확인한 원인과 예방 행동 작성 뒤 응답을 다시 쓰게 한다.
  stop_hook_active이면 기록만 하고 출력하지 않는다(턴당 1회 차단).
- 사건 문서에 `## 원인과 처리` 절이 있으면 감지 기록을 그 앞에 넣는다.
- 오류는 통과한다(fail-open).

공용 모듈(common/): sub_docs, sub_role, sub_session
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))

from sub_docs import project_root, task_id
from sub_role import first_line, resolve_role
from sub_session import _claude_entry, _codex_entry, norm, session_path, turn
from save_agent_result import fence_for

# 표현 목록 기반 감지이다. 놓치는 표현이 쌓이면 해당 유형에 추가한다.
ADMIT_TYPES = {
    "직접 인정": (
        r"죄송|(?<![가-힣])(?:제|저의)(?: [가-힣]+)? (?:실수|잘못|착오)"
        r"|(?<![가-힣])제가 (?:실수|잘못|착오|틀렸)|(?:실수|잘못)했"
    ),
    "오해·오판": (
        r"잘못 (?:이해|해석|읽|봤|판단|말씀|맞췄|잡았)|제가 엉뚱"
        r"|단정(?:했|한 것)|정(?:한|하신) (?:게|것이|것은|건|거) (?:아니|아님)"
    ),
    "누락·생략": (
        r"놓(?:쳤|치고)|빠뜨|빼먹|누락(?:했|입니다|시켰)|건너뛰었"
        r"|(?<![가-힣])제가 생략|안 했(?:습니다|어요)"
    ),
    "규칙 위반": (
        r"위반(?:했|이었)|어(?:겼|긴)|부적절했|(?:절차|허락) 없이"
        r"|임의로|(?<![가-힣])제 판단으로|다르게 하시려면"
        r"|(?:절차|Skill|스킬|지침|규칙|양식|승인|기록|지시)(?:을|를|도|은|는)?"
        r"\s[^.\n]{0,20}?(?:적용하|따르|지키|받|작성하|읽|확인하)지 않았"
    ),
    "검증 부재": (
        r"확인(?:하지 않고| 안 하고)|(?:확인|근거|검토) 없이(?![^.\n]{0,8}?[도면]\s)"
    ),
    "책임 전가": (
        r"떠넘겼|하실 (?:일은 없|필요는 없)|해결됐다고 할 수 없|원하면|원하시면|계속해야|멈추겠습니다|멈출게요"
    ),
    "결과 손상": (
        r"망가뜨|철회|낭비(?:했|한 것|시켰)"
    ),
    "반복 지적": (
        r"(?:이미|반복) 지적|제가 또"
    )
}
# 다른 단어의 일부로 나오는 패턴(실제 실수의 '제 실수', 시행착오의 '착오')만 앞 글자 검사를 둔다.
ADMIT = re.compile("|".join(ADMIT_TYPES.values()))
ANALYSIS = "\n## 원인과 처리"
INLINE_CODE = re.compile(r"`+[^`\n]*`+|\"[^\"\n]*\"|“[^”\n]*”|'[^'\n]*'|‘[^’\n]*’")
FENCE = re.compile(r"^\s*(```|~~~)")
TASK_REF = re.compile(r"log/state/[\w.\-]+\.md")
PATH = re.compile(
    r"(?<![\w:/\\])(?:/?[A-Za-z]:[\\/]|\\\\|~[\\/]|\.{1,2}[\\/]|/)[^\s`<>\"'\[\](){}*,;]+"
    r"|(?<![\w:/\\])(?:[\w.@~-]+[\\/])+[^\s`<>\"'\[\](){}*,;]+"
    r"|(?<![\w.])\b[\w.-]+\.(?:md|py|jsonl?|ya?ml|toml|txt|csv|log|js|tsx?|jsx|html|css)\b(?::\d+)?")


def without_paths(text: str) -> str:
    """경로 링크의 설명은 보존한다. 잘린 링크와 인용된 공백 포함 경로도 제거한다."""
    parts = re.split(r"(https?://[^\s`<>\"'\[\]()]+)", text, flags=re.I)
    if len(parts) > 1:
        return "".join(part if i % 2 else without_paths(part) for i, part in enumerate(parts))
    text = re.sub(r"\[([^\]\n]*)\]\(([^)\n]*\)|[^\s)\n]+)",
                  lambda m: without_paths(m[1]) if PATH.search(m[2]) else m[0], text)
    text = re.sub(r"(`+|[\"'])([^\n]*?)\1",
                  lambda m: "" if PATH.match(m[2]) else m[1] + PATH.sub("", m[2]) + m[1], text)
    return PATH.sub("", text)


def plain(text: str) -> str:
    out, fence = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fence = not fence
        elif not fence:
            out.append(INLINE_CODE.sub("", line))
    return "\n".join(out)


def is_child(data: dict) -> bool:
    if data.get("agent_id"):
        return True
    source = first_line(data.get("transcript_path")).get("source")
    return isinstance(source, dict) and bool(source.get("subagent"))


def context(path: str, message: str, cwd: str | Path | None = None) -> tuple[str, str, str, int | None]:
    """현재 턴의 응답 위치와 가장 최근 명시된 작업 참조를 실제 원문에서 확인한다."""
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
    user, _, current = turn(entries)
    assistants = [i for i in range(len(entries) - len(current), len(entries))
                  if entries[i]["role"] == "assistant" and entries[i]["text"].strip()]
    index = assistants[-1] if assistants else None
    located = index is not None and norm(entries[index]["text"]) == norm(message)
    response_line = lines[index] if located else None
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
        return user, "미확인", evidence + " · 모호하거나 문서 없음", response_line
    return user, "미확인", "미확인", response_line


REASON = """직전 응답에서 실수·위반 인정 표현({hits})을 감지해 사건 문서 {path}에 기록했습니다.
추측으로 쓰지 말고 아래 순서대로 근거를 확인한 뒤 사건 문서의 `원인과 처리`, `예방 행동` 절을 채우고 직전 응답 내용을 다시 작성하십시오.
1. 원인 확인: 이 세션 기록, 관련 지침·Skill·hook·설정 파일을 열어 실수를 허용한 공백이나 결함을 `파일:행`으로 특정하십시오.
2. 외부 근거: 해결 수단이 도구 기능(hook 이벤트, 설정 키, CLI 옵션 등)에 의존하면 공식 문서를 웹 검색으로 확인하고 출처 URL을 남기십시오.
3. 예방 행동: 준수 기준, 해결 수단(지침|Skill|hook|도구|설정|절차), 바꿀 `파일:행`과 후보 문구를 적으십시오. 확인하지 못한 항목은 미확인으로 표시하십시오.
   이미 있는 규칙을 어긴 경우 같은 규칙을 다시 쓰는 문구는 예방 행동이 아닙니다. 그 규칙이 지켜지지 않은 원인(다른 규칙과의 충돌, 적용 조건의 모호함, 검사 부재 등)을 찾고, 그 원인을 바꾸는 수단을 제시하십시오.
4. 다시 작성한 응답에는 원인, 예방 행동, 출처를 요약하고 사건 문서 경로를 적으십시오.
5. 오탐이면 근거와 함께 오탐이라고 적고 사건 문서에도 표시하십시오.
6. 수정 적용은 하지 말고 제안으로 남기십시오."""


def record(data: dict, runner: str = "claude") -> str | None:
    """감지하면 기록하고 차단 사유를 반환한다. stop_hook_active이면 기록만 한다."""
    transcript = data.get("transcript_path") or (
        session_path(data, runner) if data.get("session_id") else "")
    data = {**data, "transcript_path": transcript}
    message = data.get("last_assistant_message") or ""
    text = plain(message)
    hits = sorted(set(ADMIT.findall(text)))
    if not hits or is_child(data):
        return None
    now = datetime.now()
    session_id = data.get("session_id") or "unknown"
    sid = session_id[:8]
    directory = project_root(data.get("cwd")) / "log" / "incident"
    path = directory / f"{now:%Y%m%d}-{sid}.md"
    role = resolve_role(data) or "미확인"
    user, task, evidence, response_line = context(transcript, data.get("last_assistant_message") or "", data.get("cwd"))
    if not path.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# 자동 감지 {now:%Y-%m-%d} {sid}\n\n"
            f"- 사건 ID: {now:%Y%m%d}-{sid}\n"
            f"- 세션 ID: {session_id}\n"
            f"- 관련 작업 ID: {task}\n"
            f"- 발생·확인 시점: {now:%Y-%m-%d %H:%M}\n"
            f"- 관련 담당: {role}\n"
            f"- 조치 상태: 미착수\n\n"
            f"## 감지 기록\n\n", encoding="utf-8")
    excerpt = without_paths(message)
    request = without_paths(user)
    request_fence, excerpt_fence = fence_for(request), fence_for(excerpt)
    entry = (f"- {now:%Y-%m-%d %H:%M}\n"
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
    # 분석 절이 생긴 뒤에도 감지 기록은 감지 기록 절 안에 둔다.
    head, mark, tail = path.read_text(encoding="utf-8").partition(ANALYSIS)
    if mark:
        path.write_text(head + entry + mark + tail, encoding="utf-8")
    else:
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)
    if data.get("stop_hook_active"):
        return None
    return REASON.format(hits=", ".join(hits), path=f"log/incident/{path.name}")


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", choices=("claude", "codex"), required=True)
    args = parser.parse_args()
    try:
        reason = record(json.load(sys.stdin), args.runner)
    except Exception:
        return 0
    if reason:
        json.dump({"decision": "block", "reason": reason}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
