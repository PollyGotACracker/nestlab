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
    def test_reviewer_reports_requested_independent_verification(self) -> None:
        agent = bot.AgentConfig(
            role="reviewer", name="ricky", korean_name="리키", token="", tools=[]
        )
        prompt = bot.build_request_system_prompt(
            root=PROJECT_ROOT,
            role=agent.role,
            self_name=agent.name,
            self_korean_name=agent.korean_name,
            agents=[agent],
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

    def test_system_prompt_excludes_role_guideline(self) -> None:
        agents = roster_agents()
        for agent in agents:
            with self.subTest(role=agent.role):
                prompt = build_prompt(agent, agents)
                role_source = (PROJECT_ROOT / ".claude" / "agents" / agent.role / "AGENTS.md").read_text(encoding="utf-8")
                role_body = role_source.split("---", 2)[2].strip()
                first_item = next(line for line in role_body.splitlines() if line.strip() and not line.startswith("#"))
                self.assertNotIn("# Local project role", prompt)
                self.assertNotIn(role_body, prompt)
                self.assertNotIn(first_item, prompt)
                self.assertIn("# Global rules", prompt)
                self.assertIn("# Persona", prompt)
                self.assertIn("# Discord chat runtime", prompt)

    def test_system_prompt_includes_global_principles(self) -> None:
        agents = roster_agents()
        for agent in agents:
            with self.subTest(role=agent.role):
                prompt = build_prompt(agent, agents)
                self.assertIn("문장에 포함되는 대시를 제거한다", prompt)
                self.assertIn("## 응답 호칭", prompt)
                self.assertFalse(
                    any(line.startswith("@AGENTS.") for line in prompt.splitlines())
                )

    def test_non_director_hands_off_work_chat_to_director(self) -> None:
        agents = roster_agents()
        for agent in agents:
            if agent.role == "director":
                continue
            with self.subTest(role=agent.role):
                prompt = build_prompt(agent, agents)
                self.assertIn(
                    "- 담당이 불분명하거나 여러 역할이 필요한 작업 관련 채팅은 director 역할 캐릭터에게 넘긴다.",
                    prompt,
                )

    def test_runtime_prompt_has_no_channel_style_line(self) -> None:
        line = "- 채널 대화나 기억에 다른 캐릭터의 말투가 보여도 말투와 어미는 자기 Persona의 Communication Style에서 고른다."
        turn_prompt = (DISCORD_ROOT / "prompts" / "TURN.md").read_text(encoding="utf-8")
        self.assertNotIn(line, turn_prompt)
        agents = roster_agents()
        for agent in agents:
            with self.subTest(role=agent.role):
                self.assertNotIn(line, build_prompt(agent, agents))

    def test_roster_includes_soul_identity(self) -> None:
        agents = roster_agents()
        reviewer = next(agent for agent in agents if agent.role == "reviewer")
        prompt = build_prompt(reviewer, agents)
        identity = prompts_module.read_soul_identity(PROJECT_ROOT / ".claude" / "agents" / "worker" / "SOUL.md")
        self.assertEqual(len(identity), 2)
        for item in identity:
            with self.subTest(item=item):
                self.assertIn(item, prompt)

    def test_signature_includes_roster_personas(self) -> None:
        worker_soul = PROJECT_ROOT / ".claude" / "agents" / "worker" / "SOUL.md"
        stat = worker_soul.stat()
        entry = (str(worker_soul), stat.st_mtime_ns, stat.st_size)
        self.assertNotIn(entry, bot.prompt_source_signature(PROJECT_ROOT, "reviewer"))
        signature = bot.prompt_source_signature(PROJECT_ROOT, "reviewer", ("reviewer", "worker"))
        self.assertIn(entry, signature)

    def test_documenter_prompt_reads_canonical_files(self) -> None:
        runtime_path = DISCORD_ROOT / "prompts" / "RUNTIME.md"
        archive_path = DISCORD_ROOT / "prompts" / "ARCHIVE.md"
        signature = bot.prompt_source_signature(PROJECT_ROOT, "documenter")

        for path in (runtime_path, archive_path):
            with self.subTest(path=path):
                stat = path.stat()
                self.assertIn((str(path), stat.st_mtime_ns, stat.st_size), signature)

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
