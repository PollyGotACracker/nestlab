"""Stop hook: 메인 에이전트 응답에서 실수·위반 인정 표현을 찾아 사건 문서 초안에 기록한다.

사용: python record_incident.py --runner <claude|codex>
- 검사 대상은 hook 입력의 last_assistant_message이다. 코드 블록, 인라인 코드, 따옴표 인용 안은 검사하지 않는다.
- 일치하면 세션별 사건 문서 `log/incident/<YYYYMMDD>-<session 앞 8자>.md`에 항목을 추가한다.
  저장 위치는 세션 저장소와 무관하게 이 hook의 원본 저장소이다.
- 하위 에이전트 응답(Claude agent_id, Codex 하위 rollout)은 검사하지 않는다.
- 전체 세션 ID는 머리에, 작업 근거와 응답 위치는 원문 행 번호만 남긴다.
- 세션 경로를 사용하여 내부적으로 원문을 조회하고, 사용자 메시지와 응답은 경로 제거 뒤 코드 블록에 원문대로 기록한다.
- 기록 뒤 {"decision": "block", "reason": ...}를 출력해 근거를 확인한 원인과 예방 행동 작성 뒤 응답을 다시 쓰게 한다.
  stop_hook_active이면 기록만 하고 출력하지 않는다(턴당 1회 차단).
- 감지 기록은 `## 감지 기록` 절 끝(다음 `## ` 절 앞)에 넣는다. 그 절이 없으면 문서 끝에 덧붙인다.
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

# 사건 문서는 세션 저장소와 무관하게 이 hook의 원본 저장소(nestlab)에 저장한다. 테스트가 교체한다.
INCIDENT_ROOT = Path(__file__).resolve().parents[1]

# 표현 목록 기반 감지이다. 놓치는 표현이 쌓이면 해당 유형에 추가한다.
ADMIT_TYPES = {
    "직접 인정": (
        r"죄송|(?<![가-힣])(?:제|저의)(?: [가-힣]+(?<![엔에]))? (?:실수|잘못|착오)"
        r"|(?<![가-힣])제가 (?:실수|잘못|착오|틀렸)|(?:실수|잘못)했|제가 지키지 않"
    ),
    "오해·오판": (
        r"잘못 (?:이해|해석|읽|봤|판단|말씀|맞췄|잡았)|제가 엉뚱"
        r"|단정(?:했|한 것)|정(?:한|하신) (?:게|것이|것은|건|거) (?:아니|아님)"
    ),
    "누락·생략": (
        r"놓(?:쳤|치고)|빠뜨|빼먹|누락(?:했|입니다|시켰)|건너뛰었(?!을 (?:수|가능성|지도))|검토하지 않았"
        r"|(?<![가-힣])제가 생략|안 했(?:습니다|어요)|했어야 했"
    ),
    "규칙 위반": (
        r"위반(?:했|이었)|어(?:겼|긴)|부적절했|(?:절차|허락|근거) 없이"
        r"|임의로|(?<![가-힣])제 판단으로|다르게 하시려면"
        r"|(?:절차|Skill|스킬|지침|규칙|양식|승인|기록|지시)(?:을|를|도|은|는)?"
        r"\s[^.\n]{0,20}?(?:적용하|따르|지키|받|작성하|읽|확인하)지 않았"
    ),
    "검증 부재": (
        r"확인(?:하지 않고| 안 하고)|(?:확인|근거|검토) 없이(?![^.\n]{0,8}?[도면]\s)|묻지 않|확인한 사실이 아니|확인하지 않"
    ),
    "책임 전가": (
        r"떠넘겼|하실 (?:일은 없|필요는 없)|해결됐다고 할 수 없|원하면|원하시면|계속해야|멈추겠습니다|멈출게요"
    ),
    "결과 손상": (
        r"망가뜨|철회|낭비(?:했|한 것|시켰)|거둘게요|거둡니다|거둬들입니다|거두겠습니다|생각하지 않고|손부터 댔"
    ),
    "반복 지적": (
        r"(?:이미|반복) 지적|제가 또|이번 턴(?:에도|도)"
    )
}
# 다른 단어의 일부로 나오는 패턴(실제 실수의 '제 실수', 시행착오의 '착오')만 앞 글자 검사를 둔다.
ADMIT = re.compile("|".join(ADMIT_TYPES.values()))
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
`nestlab-log-incident` Skill을 호출해 그 절차대로 사건 문서의 `원인과 처리`, `예방 행동` 절을 채우고 직전 응답 내용을 다시 작성하십시오."""


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
    directory = INCIDENT_ROOT / "log" / "incident"
    path = directory / f"{now:%Y%m%d}-{sid}.md"
    role = resolve_role(data) or "미확인"
    user, task, evidence, response_line = context(transcript, data.get("last_assistant_message") or "", data.get("cwd"))
    if not path.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# 자동 감지 {now:%Y-%m-%d} {sid}\n\n"
            f"- 사건 ID: {now:%Y%m%d}-{sid}\n"
            f"- 세션 ID: {session_id}\n"
            f"- 실행 위치: {data.get('cwd') or '미확인'}\n"
            f"- 관련 작업 ID: {task}\n"
            f"- 발생·확인 시점: {now:%Y-%m-%d %H:%M:%S}\n"
            f"- 관련 담당: {role}\n"
            f"- 조치 상태: 미착수\n\n"
            f"## 감지 기록\n\n", encoding="utf-8")
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
    if data.get("stop_hook_active"):
        return None
    return REASON.format(hits=", ".join(hits), path=str(path))


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
