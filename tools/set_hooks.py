#!/usr/bin/env python3
"""bashrc 블록 설치, 설정 원본 자리표시 채우기, 전역 설정 병합을 한다.

install: 한 번에 아래를 처리한다. 다시 실행해도 결과가 같고 사용자 항목은 보존한다.
         1. shared/settings/bashrc.sh 블록을 ~/.bashrc에 추가하거나 교체한다.
         2. shared/settings/ 의 *.example.* 에서 <NESTLAB>(저장소 경로)과 <PYTHON>(실행한 Python 경로)을 채워
            ignore된 settings.json, config.toml을 만든다.
         3. 덮어쓰기 전의 기존 settings.json 항목(지난 설치분)을 전역 settings.json에서 먼저 뺀다.
            그 뒤 만든 파일과 rules/default.rules를 ~/.claude/settings.json, ~/.codex/config.toml,
            ~/.codex/rules/default.rules에 병합한다. 권한 목록은 shared/settings/ 파일에만 있고 여기서는 옮기기만 한다.
            config.toml은 전역 approvals_reviewer 줄을 지운다.

테스트: tools/tests/test_set_hooks.py
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "shared" / "settings"
BASHRC = Path.home() / ".bashrc"
# 예제 파일에서 만들 실제 설정 파일이다.
FILLED = [("claude/settings.example.json", "claude/settings.json"),
          ("codex/config.example.toml", "codex/config.toml")]
# ponytail: hook 그룹을 값 동일성으로만 비교한다. 저장소 경로가 바뀌면 이전 경로 항목이 남는다.
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


# config.toml 최상위 키를 예제 값으로 맞추고 전역 approvals_reviewer 줄을 지운다.
# [features] hooks가 없으면 추가하고, hook 표는 마커 블록으로 교체한다.
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


def merge_config(old: str, example: str) -> str:
    m = re.search(r"^\[", old, re.M)
    cut = m.start() if m else len(old)
    head, rest = old[:cut], old[cut:]
    head = re.sub(r"^approvals_reviewer\s*=.*\n?", "", head, flags=re.M)
    for key, value in tomllib.loads(example).items():
        if isinstance(value, dict):
            continue
        line = f"{key} = {json.dumps(value, ensure_ascii=False)}\n"
        head, n = re.subn(rf"^{key}\s*=.*\n?", lambda _: line, head, count=1, flags=re.M)
        if not n:
            head = line + head
    text = head + rest
    if "hooks" not in tomllib.loads(text).get("features", {}):
        m = re.search(r"^\[features\][^\n]*\n", text, re.M)
        if m:
            text = text[:m.end()] + "hooks = true\n" + text[m.end():]
        else:
            text = text.rstrip("\n") + "\n\n[features]\nhooks = true\n"
    return put_block(text, wrap(example[example.index("[[hooks."):]))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else ""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def count_hooks(hooks: dict) -> int:
    return sum("nestlab/hooks/" in h.get("command", "")
               for groups in hooks.values() if isinstance(groups, list) for g in groups for h in g["hooks"])


# 쓴 대상 파일을 다시 읽어 결과 줄을 만든다.
def report(bashrc: Path, claude: Path, config: Path, rules: Path, home: Path) -> list[str]:
    def name(p: Path) -> str:
        return "~/" + p.relative_to(home).as_posix() if p.is_relative_to(home) else p.as_posix()
    lines = ["설치 완료",
             f"- {name(bashrc)}: nestlab 블록" if BLOCK_RE.search(read(bashrc)) else f"- {name(bashrc)}: nestlab 블록 없음"]
    if claude.parent.is_dir():
        lines.append(f"- {name(claude)}: nestlab hook 명령 {count_hooks(json.loads(read(claude)).get('hooks', {}))}개")
    else:
        lines.append(f"- {name(claude.parent)}: 건너뜀 (설정 폴더 없음)")
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


def install(settings: Path = SETTINGS, bashrc: Path = BASHRC, home: Path | None = None) -> list[str]:
    home = home or Path.home()
    write(bashrc, put_block(read(bashrc), read(settings / "bashrc.sh").strip("\n")))
    old = json.loads(read(settings / "claude/settings.json") or "{}")  # 지난 설치 내용, 덮어쓰기 전에 읽는다.
    for src, dst in FILLED:
        write(settings / dst, fill(read(settings / src)))
    claude = home / ".claude" / "settings.json"
    if claude.parent.is_dir():
        data = json.loads(read(claude) or "{}")
        prune_json(data, old)
        merge_json(data, json.loads(read(settings / "claude/settings.json")))
        write(claude, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    codex = home / ".codex"
    rules = codex / "rules" / "default.rules"
    if codex.is_dir():
        write(codex / "config.toml", merge_config(read(codex / "config.toml"), read(settings / "codex/config.toml")))
        write(rules, put_block(read(rules), wrap(read(settings / "codex/rules/default.rules"))))
    return report(bashrc, claude, codex / "config.toml", rules, home)


def main() -> int:
    if sys.argv[1:] != ["install"]:
        print("사용법: python tools/set_hooks.py install", file=sys.stderr)
        return 2
    try:
        print(*install(), sep="\n")
    except (OSError, ValueError) as e:
        print(f"설치 실패: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
