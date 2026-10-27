#!/usr/bin/env python3
"""에이전트 지침·성향 원본 Markdown을 Claude Code나 Codex hook context에 넣는다.
지침 원본은 이 스크립트가 있는 hooks 폴더의 상위 폴더에서 읽는다.

호출자: SessionStart, SubagentStart hook 설정(shared/settings)
반환:
- stdout에 `hookSpecificOutput`(`additionalContext`) JSON을 출력한다.
- 에이전트가 정해지지 않으면 출력이 없다.
원본 문서가 없으면 stderr에 오류 문구를 쓰고 종료 코드 1을 반환한다.
한계: 에이전트 이름이나 역할을 알 수 없으면 아무것도 주입하지 않는다.
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

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


# frontmatter를 뺀 본문을 반환한다.
def strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text.strip()
    parts = text.split("---", 2)
    return parts[2].lstrip().strip() if len(parts) == 3 else text.strip()


ERR_CORE_MISSING = "{root}에 원본 AGENTS.md가 없습니다."
ERR_AGENTS_MISSING = "{root}에 에이전트 원본 폴더가 없습니다."

# 스크립트 위치를 기준으로 에이전트 구성 원본을 찾는다.
def find_bundle_root() -> Path:
    root = Path(__file__).resolve().parent.parent
    if not (root / "AGENTS.md").is_file():
        raise FileNotFoundError(ERR_CORE_MISSING.format(root=root))
    if not (root / ".claude" / "agents").is_dir():
        raise FileNotFoundError(ERR_AGENTS_MISSING.format(root=root))
    return root


# hook 입력과 실행 환경에서 에이전트 이름이나 역할을 정한다.
# 정해지지 않으면 None이다.
def resolve_agent(payload: dict, event: str, default_role: str | None) -> str | None:
    # SubagentStart는 입력의 agent_type만 쓴다.
    if event == "SubagentStart":
        return payload.get("agent_type") or None

    # SessionStart는 환경 변수, 입력, 폴더 설정, 기본 역할 순으로 정한다.
    cwd = Path(payload.get("cwd") or os.getcwd())
    return (
        os.environ.get("NESTLAB_AGENT")
        or payload.get("agent_type")
        or read_folder_agent(cwd)
        or default_role
    )


ERR_ROLE_DOCS = "{role_dir}에 에이전트 문서가 없습니다."
ERR_COMMON_DOC = "공통 지침 문서를 {core_file}에서 찾지 못했습니다."

# 요청한 에이전트의 문서를 읽어 hook 출력 JSON을 쓴다.
# 에이전트가 정해지지 않았거나 알 수 없으면 아무것도 출력하지 않는다.
def main() -> int:
    # 인자와 hook 입력을 읽고 요청한 에이전트를 정한다.
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-role", action="store_true")
    parser.add_argument("--default-role")
    args = parser.parse_args()

    # 표준 입력에서 hook 입력을 읽고 요청된 역할을 구한다.
    payload = json.load(sys.stdin)
    event = payload.get("hook_event_name") or "SessionStart"
    requested = resolve_agent(payload, event, args.default_role)
    if not requested:
        return 0

    # 요청한 에이전트를 역할 이름으로 바꾼다. 알 수 없는 역할이면 출력하지 않는다.
    root = find_bundle_root()
    role_to_name = discover_agents(root)
    name_to_role = {name: role for role, name in role_to_name.items()}

    # 요청 이름을 역할로 바꾸고 알 수 없는 역할이면 끝낸다.
    role = name_to_role.get(requested, requested)
    if role not in role_to_name:
        return 0

    # 역할 지침, 성향, 공통 지침 원본이 있는지 확인한다.
    role_dir = root / ".claude" / "agents" / role
    role_file = role_dir / "AGENTS.md"
    soul_file = role_dir / "SOUL.md"
    if not role_file.is_file() or not soul_file.is_file():
        raise FileNotFoundError(ERR_ROLE_DOCS.format(role_dir=role_dir))

    # 공통 지침 파일이 있는지 확인한다.
    core_file = root / "AGENTS.md"
    if not core_file.is_file():
        raise FileNotFoundError(ERR_COMMON_DOC.format(core_file=core_file))

    # 공통 지침(옵션), 모드별 작업 지침, 성향 문서를 합쳐 hook 출력으로 쓴다.
    # SubagentStart는 SUB.md만, SessionStart는 MAIN.md만 넣는다. 해당 파일이 없으면 넣지 않는다.
    sections: list[str] = []
    if args.include_role:
        sections.append("# Role\n\n" + strip_frontmatter(role_file.read_text(encoding="utf-8")))
    mode_file = role_dir / ("SUB.md" if event == "SubagentStart" else "MAIN.md")
    if mode_file.is_file():
        sections.append("# Task\n\n" + mode_file.read_text(encoding="utf-8").strip())
    sections.append("# Persona\n\n" + soul_file.read_text(encoding="utf-8").strip())

    # 역할 지침을 hook 출력으로 내보낸다.
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
