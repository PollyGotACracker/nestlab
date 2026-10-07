"""전역 파일의 settings 원본 반영 여부를 항목별로 확인한다. 파일은 바꾸지 않는다."""
from __future__ import annotations

import tomllib
from pathlib import Path

from sub_codex import agents_link_state, features_hooks
from sub_merge import current_block, get_path, group_label, load_json, load_sources, read_or_empty, read_text
from sub_paths import (BASHRC, CLAUDE_MD, CLAUDE_SETTINGS, CODEX_AGENTS, CODEX_CONFIG, CODEX_HOOKS, CODEX_OVERRIDE,
                   CODEX_RULES, COMMON, RECORD)


# 전역 파일의 원본 항목을 (파일 이름, 항목, 반영 여부) 목록으로 만든다.
def check_items(src: dict) -> list[tuple[str, str, bool]]:
    items: list[tuple[str, str, bool]] = []

    def add(label: str, path: Path, name: str, ok: bool) -> None:
        items.append((f"[{label}] {path}", name, ok))

    def check_hooks(label: str, path: Path, data: dict, source: dict) -> None:
        for event, groups in source.items():
            array = get_path(data, ["hooks", event]) or []
            for group in groups:
                add(label, path, group_label(event, group), group in array)

    claude = src["claude"]
    try:
        data = load_json(CLAUDE_SETTINGS)
    except ValueError as exc:
        add("Claude 설정", CLAUDE_SETTINGS, f"파싱 실패: {exc}", False)
    else:
        for key, value in claude.items():
            if key in ("env", "permissions", "hooks", "statusLine"):
                continue
            add("Claude 설정", CLAUDE_SETTINGS, f"{key} = {value}", data.get(key) == value)
        for key, value in claude.get("env", {}).items():
            add("Claude 설정", CLAUDE_SETTINGS, f"env.{key} = {value}", get_path(data, ["env", key]) == value)
        for kind, rules in claude.get("permissions", {}).items():
            array = get_path(data, ["permissions", kind]) or []
            for rule in rules:
                add("Claude 설정", CLAUDE_SETTINGS, f"permissions.{kind}: {rule}", rule in array)
        check_hooks("Claude 설정", CLAUDE_SETTINGS, data, claude.get("hooks", {}))
        if "statusLine" in claude:
            add("Claude 설정", CLAUDE_SETTINGS, "statusLine", data.get("statusLine") == claude["statusLine"])

    try:
        data = load_json(CODEX_HOOKS)
    except ValueError as exc:
        add("Codex hooks", CODEX_HOOKS, f"파싱 실패: {exc}", False)
    else:
        check_hooks("Codex hooks", CODEX_HOOKS, data, src["codex_hooks"]["hooks"])

    try:
        ok = CODEX_CONFIG.is_file() and features_hooks(read_text(CODEX_CONFIG)) is True
    except tomllib.TOMLDecodeError:
        ok = False
    add("Codex 설정", CODEX_CONFIG, "[features] hooks = true", ok)

    for key, label, path in (("codex_rules", "Codex 실행 규칙", CODEX_RULES), ("bashrc", "Git Bash", BASHRC)):
        add(label, path, "brainstack 마커 블록", current_block(read_or_empty(path)) == src[key])

    line = src["claude_md"]
    add("Claude 전역 지침", CLAUDE_MD, line, line in (l.strip() for l in read_or_empty(CLAUDE_MD).splitlines()))

    state = agents_link_state()
    add("Codex 전역 지침", CODEX_AGENTS, f"{COMMON} 연결 ({state or '미연결'})", state is not None)
    add("Codex 전역 지침", CODEX_OVERRIDE, "AGENTS.override.md 없음", not CODEX_OVERRIDE.exists())
    return items


# 항목별 반영 여부를 출력한다.
# 모두 반영됐으면 0, 아니면 1을 반환한다.
def check() -> int:
    items = check_items(load_sources())
    current = None
    for header, name, ok in items:
        if header != current:
            print(header)
            current = header
        print(f"  {'반영됨' if ok else '미반영'}  {name}")
    missing = sum(1 for _, _, ok in items if not ok)
    print(f"\n반영됨: {len(items) - missing}건")
    print(f"미반영: {missing}건")
    print(f"설치 기록: {RECORD if RECORD.is_file() else '없음'}")
    return 0 if missing == 0 else 1
