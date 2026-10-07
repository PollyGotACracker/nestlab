#!/usr/bin/env python3
"""에이전트 지침·성향 원본 Markdown을 Claude Code나 Codex hook context에 넣는다.
지침 원본은 이 스크립트가 있는 hooks 폴더의 상위 폴더에서 읽는다.

hook 입력 JSON을 stdin으로 받고 hookSpecificOutput JSON을 stdout으로 출력한다.
결정한 에이전트 이름이나 역할의 SOUL.md를 넣는다.

에이전트 결정 순서
- SubagentStart: 입력의 agent_type
- SessionStart
  1. 환경변수 BRAINSTACK_AGENT
  2. 입력의 agent_type
  3. 입력 cwd 폴더 자체의 .claude/settings.json agent 값(상위 폴더 탐색 없음)
  4. --default-role

--include-role: 역할 지침 AGENTS.md도 SOUL.md 앞에 함께 넣는다.
--default-role: 위 1~3에서 에이전트가 정해지지 않을 때 쓸 에이전트 이름이나 역할이다.

공용 모듈(common/): sub_agents
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

# 공용 모듈 폴더를 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "common"))

from sub_agents import discover_agents, read_folder_agent

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


# frontmatter를 뺀 본문을 반환한다.
def strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text.strip()
    parts = text.split("---", 2)
    return parts[2].lstrip().strip() if len(parts) == 3 else text.strip()


# 스크립트 위치를 기준으로 에이전트 구성 원본을 찾는다.
def find_bundle_root() -> Path:
    root = Path(__file__).resolve().parent.parent
    if not (root / "AGENTS.md").is_file():
        raise FileNotFoundError(f"원본의 AGENTS.md가 없습니다: {root}")
    if not (root / ".claude" / "agents").is_dir():
        raise FileNotFoundError(f"에이전트 원본 폴더가 없습니다: {root}")
    return root


# hook 입력과 실행 환경에서 에이전트 이름이나 역할을 정한다.
# 정해지지 않으면 None이다.
def resolve_agent(payload: dict, event: str, default_role: str | None) -> str | None:
    if event == "SubagentStart":
        return payload.get("agent_type") or None
    cwd = Path(payload.get("cwd") or os.getcwd())
    return (
        os.environ.get("BRAINSTACK_AGENT")
        or payload.get("agent_type")
        or read_folder_agent(cwd)
        or default_role
    )


# 요청한 에이전트의 문서를 읽어 hook 출력 JSON을 쓴다.
# 에이전트가 정해지지 않았거나 알 수 없으면 아무것도 출력하지 않는다.
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-role", action="store_true")
    parser.add_argument("--default-role")
    args = parser.parse_args()

    payload = json.load(sys.stdin)
    event = payload.get("hook_event_name") or "SessionStart"
    requested = resolve_agent(payload, event, args.default_role)
    if not requested:
        return 0

    root = find_bundle_root()
    role_to_name = discover_agents(root)
    name_to_role = {name: role for role, name in role_to_name.items()}

    role = name_to_role.get(requested, requested)
    if role not in role_to_name:
        return 0

    role_dir = root / ".claude" / "agents" / role
    role_file = role_dir / "AGENTS.md"
    soul_file = role_dir / "SOUL.md"
    if not role_file.is_file() or not soul_file.is_file():
        raise FileNotFoundError(f"에이전트 문서가 없습니다: {role_dir}")

    core_file = root / "AGENTS.md"
    if not core_file.is_file():
        raise FileNotFoundError(f"공통 지침 문서가 없습니다: {core_file}")

    sections: list[str] = []
    if args.include_role:
        sections.append("# Role\n\n" + strip_frontmatter(role_file.read_text(encoding="utf-8")))
    sections.append("# Persona\n\n" + soul_file.read_text(encoding="utf-8").strip())

    json.dump({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": "\n\n---\n\n".join(sections),
        }
    }, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"load_agent.py: {exc}", file=sys.stderr)
        raise SystemExit(1)
