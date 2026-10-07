"""파일 입출력, settings 원본 로드, JSON·마커 블록 병합의 공용 함수이다.

sync_* 함수는 기존 설치 기록 항목(entry)을 받아 settings 원본과 맞춘다.
entry가 없으면 신규 설치와 같이 병합한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import sub_settings
from sub_paths import ROOT

PLACEHOLDER = "<BRAINSTACK>"
PYTHON = "<PYTHON>"
MARK_BEGIN = "# >>> brainstack >>>"
MARK_END = "# <<< brainstack <<<"


# 줄바꿈을 바꾸지 않고 파일을 읽는다.
def read_text(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


# 줄바꿈을 바꾸지 않고 파일을 쓴다.
def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


# 파일이 없으면 빈 문자열을 반환한다.
def read_or_empty(path: Path) -> str:
    return read_text(path) if path.is_file() else ""


# 파일이 쓰는 줄바꿈을 반환한다.
# 파일이 비어 있으면 LF이다.
def eol_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


# 파일이 없으면 빈 dict를 반환한다.
def load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(read_text(path))
    if not isinstance(data, dict):
        raise ValueError(f"JSON 최상위가 객체가 아닙니다: {path}")
    return data


def dump_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


# 문자열 안의 <BRAINSTACK>을 path_text로, <PYTHON>을 실행 중인 Python 경로로 바꾼다.
def fill(value, path_text: str):
    if isinstance(value, str):
        return value.replace(PLACEHOLDER, path_text).replace(PYTHON, Path(sys.executable).as_posix())
    if isinstance(value, list):
        return [fill(v, path_text) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, path_text) for k, v in value.items()}
    return value


# 마커 블록 원본의 줄바꿈을 LF로 맞추고 앞뒤 빈 줄을 지운다.
def checked_block(block: str) -> str:
    return block.replace("\r\n", "\n").strip("\n")


# settings 원본을 읽고 자리표시를 실제 경로로 바꾼다.
def load_sources() -> dict:
    claude = fill(sub_settings.CLAUDE_SETTINGS, ROOT.as_posix())
    claude["permissions"] = fill(sub_settings.CLAUDE_PERMISSION_RULES, ROOT.as_posix())
    claude["hooks"] = fill(sub_settings.CLAUDE_HOOK_RULES, ROOT.as_posix())
    return {
        "claude": claude,
        "codex_hooks": {"hooks": fill(sub_settings.CODEX_HOOK_RULES, ROOT.as_posix())},
        "claude_md": sub_settings.CLAUDE_IMPORT_LINE.strip().replace(PLACEHOLDER, ROOT.as_posix()),
        "codex_rules": checked_block(sub_settings.CODEX_RULES_BLOCK),
        "bashrc": checked_block(sub_settings.BASHRC_BLOCK),
    }


# ---------------------------------------------------------------- JSON 병합


# 키가 없으면 만들고 만든 경로를 created에 남긴다.
def ensure(container: dict, key: str, factory, created: list, path: list[str]):
    if key not in container:
        container[key] = factory()
        if path not in created:
            created.append(path)
    value = container[key]
    if not isinstance(value, factory):
        raise ValueError(f"{'.'.join(path)} 형식이 {factory.__name__}이 아닙니다.")
    return value


# 키 경로의 값을 반환한다.
# 경로가 없으면 None이다.
def get_path(data, path: list[str]):
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


# install이 만든 컨테이너가 비었으면 지운다.
def prune(data: dict, created: list[list[str]]) -> None:
    for path in sorted(created, key=len, reverse=True):
        parent = get_path(data, path[:-1]) if len(path) > 1 else data
        if isinstance(parent, dict) and parent.get(path[-1]) in ({}, []):
            del parent[path[-1]]


# install이 넣은 키를 지운다.
def drop_value(container, key: str) -> None:
    if isinstance(container, dict):
        container.pop(key, None)


# 단일 값 키를 settings 원본에 맞추고 install이 넣은 키의 기록을 반환한다.
# owned는 기존 설치 기록의 키이다.
# 값이 다르면 덮어쓰고, 원본에서 빠진 키는 지운다.
def sync_values(container: dict, source: dict, owned: dict, label: str, preview: list[str]) -> dict:
    result: dict[str, dict] = {}
    for key in list(source) + [key for key in owned if key not in source]:
        name = label + key
        if key not in source:
            drop_value(container, key)
            preview.append(f"삭제 {name}")
            continue
        value = source[key]
        if container.get(key) == value and key not in owned:
            continue
        if container.get(key) != value:
            action = "교체" if key in container else "추가"
            preview.append(f"{action} {name}: {json.dumps(value, ensure_ascii=False)}")
            container[key] = value
        result[key] = {}
    return result


# hook 그룹 표시 이름을 만든다.
def group_label(event: str, group: dict) -> str:
    hooks = group.get("hooks") or [{}]
    desc = hooks[0].get("statusMessage") or hooks[0].get("command", "")
    matcher = f" [{group['matcher']}]" if "matcher" in group else ""
    return f"hooks.{event}{matcher}: {desc}"


# hooks 이벤트 배열을 settings 원본에 맞추고 install이 소유한 그룹을 반환한다.
# owned는 기존 설치 기록의 그룹이다.
# 원본에서 바뀐 그룹은 같은 위치에서 교체하고 빠진 그룹은 지운다.
# 사용자 그룹과 같은 그룹은 추가하지 않아 중복을 만들지 않는다.
def sync_hooks(data: dict, source: dict, owned: dict, created: list, preview: list[str]) -> dict:
    result: dict[str, list] = {}
    events = list(source) + [event for event in owned if event not in source]
    if not events:
        return result
    hooks = ensure(data, "hooks", dict, created, ["hooks"]) if source else data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    for event in events:
        desired = source.get(event, [])
        mine = owned.get(event, [])
        if desired:
            array = ensure(hooks, event, list, created, ["hooks", event])
        else:
            array = hooks.get(event) if isinstance(hooks.get(event), list) else []
        present = [group for group in mine if group in desired and group in array]
        slots: list[int] = []
        for group in (g for g in mine if g not in desired):
            index = next((i for i in range(len(array) - 1, -1, -1) if array[i] == group and i not in slots), None)
            if index is not None:
                slots.append(index)
        fresh = [group for group in desired if group not in array]
        for number, group in enumerate(fresh):
            if number < len(slots):
                array[slots[number]] = group
                preview.append(f"교체 {group_label(event, group)}")
            else:
                array.append(group)
                preview.append(f"추가 {group_label(event, group)}")
            preview.append("      " + json.dumps(group, ensure_ascii=False))
        for index in sorted(slots[len(fresh):], reverse=True):
            preview.append(f"삭제 {group_label(event, array[index])}")
            del array[index]
        kept = present + fresh
        if kept:
            result[event] = kept
    return result


# 기록된 hook 그룹을 배열 뒤쪽부터 찾아 지운다.
def remove_hooks(data: dict, added: dict) -> None:
    for event, groups in added.items():
        array = get_path(data, ["hooks", event])
        if not isinstance(array, list):
            continue
        for group in groups:
            if group in array:
                del array[len(array) - 1 - array[::-1].index(group)]


# ---------------------------------------------------------------- 마커 블록


# 마커 블록의 시작·끝 위치를 반환한다.
# 블록이 없으면 None이다.
def find_block(text: str) -> tuple[int, int] | None:
    start = text.find(MARK_BEGIN)
    if start < 0:
        return None
    end = text.find(MARK_END, start)
    if end < 0:
        return None
    return start, end + len(MARK_END)


# 파일 안의 마커 블록을 LF 기준 문자열로 반환한다.
def current_block(text: str) -> str | None:
    span = find_block(text)
    return text[span[0]:span[1]].replace("\r\n", "\n") if span else None


# 마커 블록을 추가하거나 교체한 새 내용과 기록을 반환한다.
# 블록이 이미 같으면 None이다.
def put_block(text: str, block: str) -> tuple[str, dict] | None:
    existing = current_block(text)
    if existing == block:
        return None
    eol = eol_of(text)
    body = block.replace("\n", eol)
    span = find_block(text)
    if span:
        return text[:span[0]] + body + text[span[1]:], {"prefix": "", "suffix": ""}
    prefix = "" if not text else (eol if text.endswith("\n") else eol + eol)
    return text + prefix + body + eol, {"prefix": prefix, "suffix": eol}


# 마커 블록과 install이 블록 앞뒤에 넣은 줄바꿈을 지운다.
def drop_block(text: str, record: dict) -> str:
    span = find_block(text)
    if not span:
        return text
    start, end = span
    prefix, suffix = record["prefix"], record["suffix"]
    if prefix and text[:start].endswith(prefix):
        start -= len(prefix)
    if suffix and text[end:].startswith(suffix):
        end += len(suffix)
    return text[:start] + text[end:]


# 마커 블록을 settings 원본 내용으로 덮어쓰거나 추가한다.
# (새 내용 또는 None, 새 기록 항목 또는 None, 미리보기)를 반환한다.
# 기존 설치 기록이 있으면 그 기록 항목을 그대로 쓴다.
def sync_block(path: Path, text: str, block: str, entry: dict | None) -> tuple[str | None, dict | None, list[str]]:
    result = put_block(text, block)
    if result is None:
        return None, None, []
    new, record = result
    action = "교체" if find_block(text) else "추가"
    if entry is None:
        record["created"] = not path.exists()
    else:
        record = entry
    body = ["      " + line for line in block.split("\n")]
    return new, record, [f"{action} 마커 블록", *body]
