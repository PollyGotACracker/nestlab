"""역할 지침 폴더에서 에이전트 이름을 찾고 폴더 설정의 기본 에이전트를 읽는 공용 함수이다.

호출자:
- hooks/load_agent.py
- hooks/common/sub_role.py
입력: 역할 지침 파일 경로, 저장소 경로, cwd 경로
반환:
- read_agent_name, read_folder_agent는 이름 문자열 또는 None
- discover_agents는 {역할: 에이전트 이름} dict
- 차단 사유 문자열은 없음
한계: read_folder_agent는 cwd 폴더만 보고 상위 폴더는 탐색하지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path


# 역할 지침 frontmatter의 name 값을 읽는다. 값이 없으면 None이다.
def read_agent_name(agents_file: Path) -> str | None:
    # frontmatter 구간을 나눈다.
    text = agents_file.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) != 3:
        return None

    # name 줄의 값을 반환한다.
    for line in parts[1].splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip() or None
    return None


# 역할 지침 폴더마다 {역할: 에이전트 이름}을 만든다.
def discover_agents(root: Path) -> dict[str, str]:
    agents: dict[str, str] = {}
    for role_dir in sorted((root / ".claude" / "agents").iterdir()):
        agents_file = role_dir / "AGENTS.md"
        if not role_dir.is_dir() or not agents_file.is_file():
            continue
        agents[role_dir.name] = read_agent_name(agents_file) or role_dir.name
    return agents


# cwd 폴더 자체의 Claude 프로젝트 설정에서 agent 값을 읽는다. 상위 폴더는 탐색하지 않고, 파일이 없거나 값이 없으면 None이다.
def read_folder_agent(cwd: Path) -> str | None:
    # 설정 파일을 읽는다.
    settings_file = cwd / ".claude" / "settings.json"
    if not settings_file.is_file():
        return None
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    # agent 값이 비지 않은 문자열일 때만 반환한다.
    agent = settings.get("agent") if isinstance(settings, dict) else None
    return agent if isinstance(agent, str) and agent else None
