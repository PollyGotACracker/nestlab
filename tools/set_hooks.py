#!/usr/bin/env python3
"""bashrc 블록 설치, 설정 원본 자리표시 채우기, 전역 설정 병합을 한다.

호출자: 사용자(`python tools/set_hooks.py install`)
입력: 명령줄 인자 install 하나
반환: 설치 결과 줄을 stdout에 출력하고, 실패하면 stderr에 사유를 쓰고 종료 코드 1을 반환한다.
한계: 설치한 설정이 Claude Code와 Codex에서 실제로 동작하는지는 설치 후 세션에서 확인해야 한다.
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

# 저장소 모듈 import 오류 메시지도 UTF-8로 나오도록 먼저 설정한다.
for stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "shared" / "settings"
BASHRC = Path.home() / ".bashrc"
# 예제 파일에서 만들 실제 설정 파일이다.
FILLED = [("claude/settings.example.json", "claude/settings.json"),
          ("codex/config.example.toml", "codex/config.toml")]
BEGIN, END = "# >>> nestlab >>>", "# <<< nestlab <<<"
PREFIX_RE = re.compile(r"^prefix_rule[(]", re.M)
BLOCK_RE = re.compile(re.escape(BEGIN) + ".*?" + re.escape(END), re.S)


# 예제 텍스트의 자리표시를 실제 경로로 바꾼다.
def fill(text: str) -> str:
    return text.replace("<NESTLAB>", ROOT.as_posix()).replace("<PYTHON>", Path(sys.executable).as_posix())


# 텍스트의 nestlab 블록을 교체하고, 없으면 끝에 추가한다. 블록은 마커를 포함해야 한다.
def put_block(text: str, block: str) -> str:
    if BLOCK_RE.search(text):
        return BLOCK_RE.sub(lambda _: block, text, count=1)
    return text + ("\n" if text and not text.endswith("\n") else "") + ("\n" if text else "") + block + "\n"


# 본문을 마커로 감싼다.
def wrap(body: str) -> str:
    return f"{BEGIN}\n{body.strip(chr(10))}\n{END}"


# src의 dict는 재귀 병합, list는 없는 항목만 추가, 그 밖의 값은 덮어쓴다.
def merge_json(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            merge_json(dst[key], value)
        elif isinstance(value, list) and isinstance(dst.get(key), list):
            dst[key] += [v for v in value if v not in dst[key]]
        else:
            dst[key] = value


# old와 값이 같은 항목을 dst에서 지운다. hook 그룹은 값 동일성으로만 비교하므로 저장소 경로가 바뀌면 이전 경로 항목이 남는다.
def prune_json(dst: dict, old: dict) -> None:
    for key, value in old.items():
        if key not in dst:
            continue
        if isinstance(value, dict) and isinstance(dst[key], dict):
            prune_json(dst[key], value)
            if not dst[key]:
                del dst[key]
        elif isinstance(value, list) and isinstance(dst[key], list):
            dst[key] = [v for v in dst[key] if v not in value]
            if not dst[key]:
                del dst[key]
        elif dst[key] == value:
            del dst[key]


# config.toml 최상위 키를 예제 값으로 맞추고 전역 approvals_reviewer 줄을 지운다.
# 예제 [features]의 키가 없으면 추가하고, [windows] 키를 덮어쓰며, hook 표는 마커 블록으로 교체한다.
def merge_config(old: str, example: str) -> str:
    # 첫 표 앞의 최상위 키 영역을 나누고 approvals_reviewer 줄을 지운다.
    m = re.search(r"^\[", old, re.M)
    cut = m.start() if m else len(old)
    head, rest = old[:cut], old[cut:]
    head = re.sub(r"^approvals_reviewer\s*=.*\n?", "", head, flags=re.M)

    # 예제의 최상위 키를 같은 이름 줄에 덮어쓰고 없으면 앞에 추가한다.
    for key, value in tomllib.loads(example).items():
        if isinstance(value, dict):
            continue
        line = f"{key} = {json.dumps(value, ensure_ascii=False)}\n"
        head, n = re.subn(rf"^{key}\s*=.*\n?", lambda _: line, head, count=1, flags=re.M)
        if not n:
            head = line + head
    text = head + rest

    # 예제 [features]의 키 중 사용자 설정에 없는 키를 추가하고 hook 표를 마커 블록으로 교체한다.
    have = tomllib.loads(text).get("features", {})
    lines = "".join(f"{key} = {json.dumps(value)}\n"
                    for key, value in tomllib.loads(example).get("features", {}).items() if key not in have)
    if lines:
        m = re.search(r"^\[features\][^\n]*\n", text, re.M)
        if m:
            text = text[:m.end()] + lines + text[m.end():]
        else:
            text = text.rstrip("\n") + "\n\n[features]\n" + lines
    # 예제 [windows]의 키를 사용자 설정 [windows] 표의 같은 키 줄에 덮어쓰고, 표나 키가 없으면 추가한다.
    for key, value in tomllib.loads(example).get("windows", {}).items():
        line = f"{key} = {json.dumps(value, ensure_ascii=False)}\n"
        m = re.search(r"^\[windows\][^\n]*\n", text, re.M)
        if not m:
            text = text.rstrip("\n") + "\n\n[windows]\n" + line
            continue
        nxt = re.compile(r"^\[", re.M).search(text, m.end())
        end = nxt.start() if nxt else len(text)
        body, n = re.subn(rf"^{key}\s*=.*\n?", lambda _: line, text[m.end():end], count=1, flags=re.M)
        text = text[:m.end()] + (body if n else line + body) + text[end:]
    return put_block(text, wrap(example[example.index("[[hooks."):]))


# 파일을 줄바꿈 LF로 읽는다. 파일이 없으면 빈 문자열이다.
def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else ""


# 상위 폴더를 만들고 파일을 LF로 쓴다.
def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# hook 설정에서 nestlab hook 명령 개수를 센다.
def count_hooks(hooks: dict) -> int:
    return sum("nestlab/hooks/" in h.get("command", "")
               for groups in hooks.values() if isinstance(groups, list) for g in groups for h in g.get("hooks", []))


# 쓴 대상 파일을 다시 읽어 결과 줄을 만든다.
def report(bashrc: Path, claude: Path, config: Path, rules: Path, home: Path) -> list[str]:
    # 홈 기준 경로 표기 함수이다.
    def name(p: Path) -> str:
        return "~/" + p.relative_to(home).as_posix() if p.is_relative_to(home) else p.as_posix()

    # bashrc와 Claude 설정 결과 줄을 만든다.
    lines = ["설치 완료",
             f"- {name(bashrc)}: nestlab 블록" if BLOCK_RE.search(read(bashrc)) else f"- {name(bashrc)}: nestlab 블록 없음"]
    if claude.parent.is_dir():
        lines.append(f"- {name(claude)}: nestlab hook 명령 {count_hooks(json.loads(read(claude)).get('hooks', {}))}개")
    else:
        lines.append(f"- {name(claude.parent)}: 건너뜀 (설정 폴더 없음)")

    # Codex 설정과 규칙 결과 줄을 만든다.
    if config.parent.is_dir():
        cfg = tomllib.loads(read(config))
        text = read(rules)
        m = BLOCK_RE.search(text)
        inside = len(PREFIX_RE.findall(m.group())) if m else 0
        outside = len(PREFIX_RE.findall(text)) - inside
        lines += [f"- {name(config)}: nestlab hook 명령 {count_hooks(cfg.get('hooks', {}))}개, "
                  f"sandbox_mode = {json.dumps(cfg.get('sandbox_mode'))}, approval_policy = {json.dumps(cfg.get('approval_policy'))}",
                  f"- {name(rules)}: prefix_rule {inside}개" + (f" (블록 밖 {outside}개)" if outside else "")]
    else:
        lines.append(f"- {name(config.parent)}: 건너뜀 (설정 폴더 없음)")
    return lines


# bashrc 블록, 설정 원본, 전역 설정을 설치하고 결과 줄 목록을 반환한다.
def install(settings: Path = SETTINGS, bashrc: Path = BASHRC, home: Path | None = None) -> list[str]:
    # bashrc 블록을 넣고 예제 파일에서 실제 설정 파일을 만든다.
    home = home or Path.home()
    write(bashrc, put_block(read(bashrc), read(settings / "bashrc.sh").strip("\n")))
    old = json.loads(read(settings / "claude/settings.json") or "{}")  # 지난 설치 내용, 덮어쓰기 전에 읽는다.
    for src, dst in FILLED:
        write(settings / dst, fill(read(settings / src)))

    # 이전 설치 항목을 빼고 Claude 전역 설정에 병합한다.
    claude = home / ".claude" / "settings.json"
    if claude.parent.is_dir():
        data = json.loads(read(claude) or "{}")
        prune_json(data, old)
        merge_json(data, json.loads(read(settings / "claude/settings.json")))
        write(claude, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    # Codex 설정과 규칙을 병합한다.
    codex = home / ".codex"
    rules = codex / "rules" / "default.rules"
    if codex.is_dir():
        write(codex / "config.toml", merge_config(read(codex / "config.toml"), read(settings / "codex/config.toml")))
        write(rules, put_block(read(rules), wrap(read(settings / "codex/rules/default.rules"))))
    return report(bashrc, claude, codex / "config.toml", rules, home)


# 인자를 확인해 설치하고 결과를 출력한다. 실패하면 사유를 stderr에 쓰고 1을 반환한다.
def main() -> int:
    # install 인자가 아니면 사용법을 알린다.
    if sys.argv[1:] != ["install"]:
        print("사용법: python tools/set_hooks.py install", file=sys.stderr)
        return 2

    # 설치하고 결과 줄을 출력한다. 실패하면 사유를 stderr에 쓴다.
    try:
        print(*install(), sep="\n")
    except (OSError, ValueError) as e:
        print(f"설치 실패: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
