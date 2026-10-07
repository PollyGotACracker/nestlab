"""역할 지침 폴더에서 에이전트 이름을 찾고 폴더 설정의 기본 에이전트를 읽는 공용 함수이다.

사용: load_agent.py, common/sub_role.py
"""
from __future__ import annotations

import json
from pathlib import Path


# 역할 지침 frontmatter의 name 값을 읽는다.
# 값이 없으면 None이다.
def read_agent_name(agents_file: Path) -> str | None:
    text = agents_file.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) != 3:
        return None
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


# cwd 폴더 자체의 Claude 프로젝트 설정에서 agent 값을 읽는다.
# 상위 폴더는 탐색하지 않는다.
# 파일이 없거나 읽을 수 없거나 값이 없으면 None이다.
def read_folder_agent(cwd: Path) -> str | None:
    settings_file = cwd / ".claude" / "settings.json"
    if not settings_file.is_file():
        return None
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    agent = settings.get("agent") if isinstance(settings, dict) else None
    return agent if isinstance(agent, str) and agent else None
