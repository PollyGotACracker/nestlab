"""Discord 프롬프트가 정본 파일에서 구성되는지 확인한다."""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


DISCORD_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DISCORD_ROOT.parent
sys.path.insert(0, str(DISCORD_ROOT / "bot"))
bot = importlib.import_module("bot")
config_module = importlib.import_module("config")
prompts_module = importlib.import_module("prompts")


# .claude/agents의 역할 전체로 명단을 만든다.
def roster_agents() -> list:
    return [
        bot.AgentConfig(role=role, name=name, korean_name=name, token="", tools=[])
        for role, name in bot.discover_agents(PROJECT_ROOT).items()
    ]


# 지식 저장소 없이 한 역할의 채팅 시스템 프롬프트를 만든다.
def build_prompt(agent, agents: list) -> str:
    return bot.build_request_system_prompt(
        root=PROJECT_ROOT,
        role=agent.role,
        self_name=agent.name,
        self_korean_name=agent.korean_name,
        agents=agents,
        archive_repository=None,
        tools_config=config_module.ToolsConfig(channel_history_max=1, server_channels_max=1),
        reminders_config=config_module.RemindersConfig(
            timezone_offset_hours=9,
            max_days=1,
            check_seconds=60,
            repeat_grace_minutes=1,
            late_minutes=1,
        ),
    )


class BotPromptPathTests(unittest.TestCase):
    # 시스템 프롬프트에 역할 폴더의 MAIN.md가 들어가고 SUB.md는 들어가지 않는지 확인한다.
    def test_system_prompt_includes_main_guideline(self) -> None:
        agents = roster_agents()
        for agent in agents:
            with self.subTest(role=agent.role):
                prompt = build_prompt(agent, agents)
                role_dir = PROJECT_ROOT / ".claude" / "agents" / agent.role
                main_body = (role_dir / "MAIN.md").read_text(encoding="utf-8").strip()
                self.assertIn(main_body, prompt)
                if (role_dir / "SUB.md").is_file():
                    self.assertNotIn((role_dir / "SUB.md").read_text(encoding="utf-8").strip(), prompt)
                self.assertIn("# Global rules", prompt)
                self.assertIn("# Persona", prompt)
                self.assertIn("# Discord chat runtime", prompt)

    # 시스템 프롬프트에 공통 원칙이 들어가는지 확인한다.
    def test_system_prompt_includes_global_principles(self) -> None:
        agents = roster_agents()
        for agent in agents:
            with self.subTest(role=agent.role):
                prompt = build_prompt(agent, agents)
                self.assertFalse(
                    any(line.startswith("@AGENTS.") for line in prompt.splitlines())
                )

    # 명단에 성향 문서의 정체성이 들어가는지 확인한다.
    def test_roster_includes_soul_identity(self) -> None:
        agents = roster_agents()
        reviewer = next(agent for agent in agents if agent.role == "reviewer")
        prompt = build_prompt(reviewer, agents)
        identity = prompts_module.read_soul_identity(PROJECT_ROOT / ".claude" / "agents" / "worker" / "SOUL.md")
        for item in identity:
            with self.subTest(item=item):
                self.assertIn(item, prompt)

    # 프롬프트 서명이 명단의 성향 문서를 감시하는지 확인한다.
    def test_signature_includes_roster_personas(self) -> None:
        worker_soul = PROJECT_ROOT / ".claude" / "agents" / "worker" / "SOUL.md"
        stat = worker_soul.stat()
        entry = (str(worker_soul), stat.st_mtime_ns, stat.st_size)
        self.assertNotIn(entry, bot.prompt_source_signature(PROJECT_ROOT, "reviewer"))
        signature = bot.prompt_source_signature(PROJECT_ROOT, "reviewer", ("reviewer", "worker"))
        self.assertIn(entry, signature)

    # documenter 프롬프트가 정해진 원본 파일을 읽는지 확인한다.
    def test_documenter_prompt_reads_canonical_files(self) -> None:
        # 프롬프트 원본 파일 경로와 서명을 구한다.
        runtime_path = DISCORD_ROOT / "prompts" / "RUNTIME.md"
        archive_path = DISCORD_ROOT / "prompts" / "ARCHIVE.md"
        signature = bot.prompt_source_signature(PROJECT_ROOT, "documenter")

        # 원본 파일마다 서명에 상태가 들어 있는지 확인한다.
        for path in (runtime_path, archive_path):
            with self.subTest(path=path):
                stat = path.stat()
                self.assertIn((str(path), stat.st_mtime_ns, stat.st_size), signature)

        # documenter 프롬프트를 만들어 원본 내용이 들어 있는지 확인한다.
        repository = config_module.ArchiveRepositoryConfig(owner="example", repo="archive", token="")
        agent = bot.AgentConfig(
            role="documenter", name="documenter", korean_name="기록자", token="", tools=[]
        )
        prompt = bot.build_request_system_prompt(
            root=PROJECT_ROOT,
            role="documenter",
            self_name=agent.name,
            self_korean_name=agent.korean_name,
            agents=[agent],
            archive_repository=repository,
            tools_config=config_module.ToolsConfig(channel_history_max=1, server_channels_max=1),
            reminders_config=config_module.RemindersConfig(
                timezone_offset_hours=9,
                max_days=1,
                check_seconds=60,
                repeat_grace_minutes=1,
                late_minutes=1,
            ),
        )
        runtime_source = runtime_path.read_text(encoding="utf-8").strip()
        archive_source = archive_path.read_text(encoding="utf-8").strip()
        self.assertIn(runtime_source.splitlines()[0], prompt)
        self.assertIn(
            archive_source.replace("$repo_name", "example/archive"),
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
