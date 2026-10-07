"""install·uninstall 흐름이다.

install은 신규·기존 설치 모두 settings 원본 전체를 전역 파일에 맞춘다.
설치 기록에는 install이 넣은 항목을 남긴다.
uninstall은 기록된 항목을 지운다.
쓰기 중 실패하면 백업으로 install 직전 상태를 복구한다.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from sub_check import check
from sub_claude import sync_claude_import, sync_claude_settings, uninstall_claude_md, uninstall_claude_settings
from sub_codex import (make_agents_link, sync_codex_agents, sync_codex_config, sync_codex_hooks,
                   uninstall_codex_agents, uninstall_codex_config, uninstall_codex_hooks)
from sub_merge import drop_block, dump_json, load_json, load_sources, read_or_empty, read_text, sync_block, write_text
from sub_paths import (BASHRC, CLAUDE_MD, CLAUDE_SETTINGS, CODEX_AGENTS, CODEX_CONFIG, CODEX_HOOKS, CODEX_OVERRIDE,
                   CODEX_RULES, RECORD, RECORD_DIR)


# ---------------------------------------------------------------- 계획


# JSON 설정 파일의 계획을 (새 내용, 새 기록 항목, 미리보기)로 반환한다.
# 내용과 기록이 모두 그대로이면 둘 다 None이다.
def json_plan(path: Path, entry: dict | None, sync) -> tuple[str | None, dict | None, list[str]]:
    data = load_json(path)
    before = copy.deepcopy(data)
    preview: list[str] = []
    record = sync(data, entry, preview)
    new = dump_json(data) if data != before else None
    if new is None and (entry is None or record == entry):
        return None, None, preview
    return new, record, preview


# 전역 파일마다 바꿀 내용을 계산한다.
# 파일은 바꾸지 않는다.
def build_plan(src: dict, existing: dict | None) -> list[dict]:
    old = existing["items"] if existing is not None else {}
    plan: list[dict] = []

    def add(key: str, label: str, path: Path, backup: str, builder) -> None:
        new, record, preview = builder()
        plan.append({
            "key": key, "label": label, "path": path, "backup": backup,
            "new": new, "record": record, "preview": preview,
        })

    add("claude_settings", "Claude 설정", CLAUDE_SETTINGS, "claude-settings.json",
        lambda: json_plan(CLAUDE_SETTINGS, old.get("claude_settings"),
                          lambda data, entry, preview: sync_claude_settings(data, src["claude"], entry, preview)))
    add("codex_hooks", "Codex hooks", CODEX_HOOKS, "codex-hooks.json",
        lambda: json_plan(CODEX_HOOKS, old.get("codex_hooks"),
                          lambda data, entry, preview: sync_codex_hooks(data, src["codex_hooks"], entry, preview)))
    add("codex_config", "Codex 설정", CODEX_CONFIG, "codex-config.toml",
        lambda: sync_codex_config(read_or_empty(CODEX_CONFIG)))
    add("codex_rules", "Codex 실행 규칙", CODEX_RULES, "codex-default.rules",
        lambda: sync_block(CODEX_RULES, read_or_empty(CODEX_RULES), src["codex_rules"], old.get("codex_rules")))
    add("bashrc", "Git Bash", BASHRC, "bashrc",
        lambda: sync_block(BASHRC, read_or_empty(BASHRC), src["bashrc"], old.get("bashrc")))
    add("claude_md", "Claude 전역 지침", CLAUDE_MD, "claude-CLAUDE.md",
        lambda: sync_claude_import(read_or_empty(CLAUDE_MD), src["claude_md"], old.get("claude_md")))
    add("codex_agents", "Codex 전역 지침", CODEX_AGENTS, "codex-AGENTS.md", sync_codex_agents)
    return plan


def pending(plan: list[dict]) -> list[dict]:
    return [item for item in plan if item["new"] is not None or item["record"] is not None]


# 바뀌는 항목만 파일별로 출력한다.
def print_plan(plan: list[dict]) -> None:
    for item in plan:
        print(f"[{item['label']}] {item['path']}")
        lines = item["preview"] if item["new"] is not None or item["record"] is not None else []
        for line in lines or ["변경 없음"]:
            print(f"  {line}")
    print(f"\n설치 기록: {RECORD}")


# 관리 대상 파일 전부를 파일 내용이 바뀌었는지에 따라 출력한다.
def print_result(plan: list[dict]) -> None:
    for item in plan:
        print(f"[{item['label']}] {item['path']}: {'반영' if item['new'] is not None else '변경 없음'}")


# ---------------------------------------------------------------- 적용


# 백업한 파일로 되돌린다.
# 백업이 없으면 install이 만든 파일이므로 지운다.
def restore_file(target: Path, backup: str | None) -> None:
    if target.is_symlink() or target.exists():
        target.unlink()
    if backup:
        shutil.copy2(backup, target)


# 계산한 내용을 전역 파일에 쓰고 설치 기록을 남긴다.
# 하나라도 실패하면 백업으로 전체 원복한다.
def apply_plan(plan: list[dict], existing_record: dict | None = None) -> None:
    changes = pending(plan)
    writes = [item for item in changes if item["new"] is not None]
    backup_dir = RECORD_DIR / "backup" / datetime.now().strftime("%Y%m%d-%H%M%S-%f") if writes else None
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=False)
    backups: dict[str, str | None] = {}
    record_before = RECORD.read_bytes() if RECORD.is_file() else None
    for item in writes:
        path = item["path"]
        if path.exists():
            destination = backup_dir / item["backup"]
            shutil.copy2(path, destination)
            backups[str(path)] = str(destination)
        else:
            backups[str(path)] = None

    try:
        for item in writes:
            if item["key"] == "codex_agents":
                item["record"]["link"] = make_agents_link()
            else:
                write_text(item["path"], item["new"])
        items = {item["key"]: item["record"] for item in changes}
        if existing_record is not None:
            # 이번에 바뀌지 않은 항목은 기존 기록 항목을 쓴다.
            items = {**existing_record["items"], **items}
        record = {
            "version": 1,
            "installed_at": datetime.now().isoformat(timespec="seconds"),
            "backup_dir": str(backup_dir) if backup_dir is not None else None,
            "backups": backups,
            "items": items,
        }
        write_text(RECORD, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    except BaseException:
        print("실패: 백업으로 원복합니다.", file=sys.stderr)
        for path, backup in backups.items():
            try:
                restore_file(Path(path), backup)
            except OSError as exc:
                print(f"  원복 실패 {path}: {exc}", file=sys.stderr)
        if record_before is None:
            RECORD.unlink(missing_ok=True)
        else:
            RECORD.write_bytes(record_before)
        raise

    print_result(plan)
    print(f"\n설치 기록: {RECORD}")
    if backup_dir is not None:
        print(f"백업: {backup_dir}")


def install(dry_run: bool) -> int:
    src = load_sources()
    existing = load_json(RECORD) if RECORD.is_file() else None
    try:
        plan = build_plan(src, existing)
    except ValueError as exc:
        print(f"중단: {exc}", file=sys.stderr)
        return 1
    blocked = CODEX_OVERRIDE.exists()
    if dry_run:
        print_plan(plan)
        if blocked:
            print(f"\n중단 예정: {CODEX_OVERRIDE}가 있습니다.")
        print("\n--dry-run: 파일을 바꾸지 않았습니다.")
        return 1 if blocked else 0
    if blocked:
        print(f"중단: {CODEX_OVERRIDE}가 있어 ~/.codex/AGENTS.md가 적용되지 않습니다.", file=sys.stderr)
        return 1
    if pending(plan):
        apply_plan(plan, existing)
    else:
        print_result(plan)
        print(f"\n설치 기록: {RECORD if existing is not None else '없음 (바꿀 항목이 없어 만들지 않음)'}")
    # 설치 결과는 check 판정으로 확인한다.
    print("\n[check]")
    return check()


# ---------------------------------------------------------------- uninstall


# 마커 블록을 지운다.
# install이 만든 파일이 비면 파일도 지운다.
def uninstall_block(path: Path, record: dict) -> None:
    if not path.is_file():
        return
    text = drop_block(read_text(path), record)
    if record.get("created") and not text.strip():
        path.unlink()
    else:
        write_text(path, text)


UNINSTALLERS = [
    ("claude_settings", CLAUDE_SETTINGS, uninstall_claude_settings),
    ("codex_hooks", CODEX_HOOKS, uninstall_codex_hooks),
    ("codex_config", CODEX_CONFIG, uninstall_codex_config),
    ("codex_rules", CODEX_RULES, lambda record: uninstall_block(CODEX_RULES, record)),
    ("bashrc", BASHRC, lambda record: uninstall_block(BASHRC, record)),
    ("claude_md", CLAUDE_MD, uninstall_claude_md),
    ("codex_agents", CODEX_AGENTS, uninstall_codex_agents),
]


# 설치 기록의 항목만 제거한다.
# 모두 성공하면 설치 기록을 지우고 0을, 하나라도 실패하면 기록을 남기고 1을 반환한다.
def uninstall() -> int:
    if not RECORD.is_file():
        print(f"설치 기록이 없습니다: {RECORD}", file=sys.stderr)
        return 1
    items = json.loads(read_text(RECORD))["items"]
    problems = 0
    for key, path, remove in UNINSTALLERS:
        if key not in items:
            continue
        try:
            remove(items[key])
        except (OSError, ValueError, KeyError) as exc:
            problems += 1
            print(f"[{key}] {path}: 제거 실패 {exc}", file=sys.stderr)
        else:
            print(f"[{key}] {path}: 제거")
    if problems:
        print(f"\n실패 {problems}건. 설치 기록을 남깁니다: {RECORD}", file=sys.stderr)
        return 1
    RECORD.unlink()
    print(f"\n설치 기록 삭제: {RECORD}")
    return 0
