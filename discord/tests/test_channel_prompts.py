"""채널 종류별 시스템·턴 프롬프트 분리와 변형 표식, 캐릭터 기억 주입을 확인한다."""

from __future__ import annotations

import importlib
import random
import re
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord


DISCORD_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DISCORD_ROOT.parent
sys.path.insert(0, str(DISCORD_ROOT / "bot"))
bot = importlib.import_module("bot")
config_module = importlib.import_module("config")
prompts_module = importlib.import_module("prompts")

TOOLS_CONFIG = config_module.ToolsConfig(channel_history_max=1, server_channels_max=1)
REMINDERS_CONFIG = config_module.RemindersConfig(
    timezone_offset_hours=9, max_days=1, check_seconds=60, repeat_grace_minutes=1, late_minutes=1,
)


def roster_agents() -> list:
    return [
        bot.AgentConfig(role=role, name=name, korean_name=f"{name}-ko", token="", tools=[])
        for role, name in bot.discover_agents(PROJECT_ROOT).items()
    ]


def build_chat(agent, agents, root=PROJECT_ROOT) -> str:
    return bot.build_chat_system_prompt(
        root, agent.role, agent.name, agent.korean_name, agents, TOOLS_CONFIG, REMINDERS_CONFIG,
    )


def build_request(agent, agents, root=PROJECT_ROOT) -> str:
    return bot.build_request_system_prompt(
        root, agent.role, agent.name, agent.korean_name, agents, None, TOOLS_CONFIG, REMINDERS_CONFIG,
    )


def fake_thread(thread_id: int, parent_id: int) -> discord.Thread:
    thread = discord.Thread.__new__(discord.Thread)
    thread.id = thread_id
    thread.parent_id = parent_id
    return thread


class RenderVariantTests(unittest.TestCase):
    def test_keeps_matching_and_removes_other_variants(self) -> None:
        template = "앞\n  {{request:요청 줄}}\n  {{chat:채팅 줄}}\n뒤"
        self.assertEqual(prompts_module.render_variant(template, "request"), "앞\n  요청 줄\n뒤")
        self.assertEqual(prompts_module.render_variant(template, "chat"), "앞\n  채팅 줄\n뒤")

    def test_multiline_variant(self) -> None:
        template = "{{chat:첫 줄\n  둘째 줄}}\n끝"
        self.assertEqual(prompts_module.render_variant(template, "chat"), "첫 줄\n  둘째 줄\n끝")
        self.assertEqual(prompts_module.render_variant(template, "request"), "끝")

    def test_broken_markers_raise(self) -> None:
        for template in (
            "{{chat:닫히지 않음\n다음 줄",
            "앞 {{chat:줄 머리가 아님}}",
            "{{other:알 수 없는 종류}}",
            "{{chat:안에 {{request:중첩}}}}",
            "닫는 표식만}}",
        ):
            with self.subTest(template=template), self.assertRaises(ValueError):
                prompts_module.render_variant(template, "chat")
        with self.assertRaises(ValueError):
            prompts_module.render_variant("본문", "other")

    def test_templates_render_for_both_kinds(self) -> None:
        for path in (prompts_module.RUNTIME_PROMPT_PATH, config_module.TURN_PROMPT_PATH):
            text = path.read_text(encoding="utf-8")
            for kind in prompts_module.PROMPT_KINDS:
                with self.subTest(path=path.name, kind=kind):
                    rendered = prompts_module.render_variant(text, kind)
                    self.assertNotIn("{{", rendered)
                    self.assertNotIn("}}", rendered)


class ChannelKindTests(unittest.TestCase):
    def test_thread_uses_parent_channel(self) -> None:
        cfg = SimpleNamespace(chat_channels={10})
        self.assertTrue(prompts_module.is_chat_channel(SimpleNamespace(id=10), cfg))
        self.assertFalse(prompts_module.is_chat_channel(SimpleNamespace(id=11), cfg))
        self.assertTrue(prompts_module.is_chat_channel(fake_thread(99, 10), cfg))
        self.assertFalse(prompts_module.is_chat_channel(fake_thread(10, 11), cfg))

    def test_daily_chat_seed(self) -> None:
        day = date(2026, 10, 2)
        expected = sorted(random.Random(f"{day.isoformat()}:42:chat").sample(range(24 * 60), 3))
        self.assertEqual(prompts_module.daily_chat_minutes(42, day, 3), expected)
        self.assertEqual(prompts_module.daily_chat_minutes(42, day, 0), [])


class ChatSystemPromptTests(unittest.TestCase):
    def test_chat_prompt_for_every_role(self) -> None:
        agents = roster_agents()
        common = prompts_module.STYLE_SECTION.sub("", prompts_module.read_common_rules(PROJECT_ROOT), count=1).strip()
        chat_text = prompts_module.CHAT_PROMPT_PATH.read_text(encoding="utf-8")
        for agent in agents:
            with self.subTest(role=agent.role):
                prompt = build_chat(agent, agents)
                soul = (PROJECT_ROOT / ".claude" / "agents" / agent.role / "SOUL.md").read_text(
                    encoding="utf-8"
                ).strip()
                self.assertTrue(prompt.startswith(f"# Global rules\n\n{common}\n\n---\n\n# Persona\n\n{soul}"))
                self.assertIn("# Discord chat channel", prompt)
                self.assertIn(chat_text.split("\n", 3)[2], prompt)
                self.assertIn("## Response control", prompt)
                self.assertIn(prompts_module.CHAT_MEMORY_BLOCK, prompt)
                tail = prompt.split(soul, 1)[1]
                for banned in ("$", "{{", "}}"):
                    self.assertNotIn(banned, tail)
                for other in agents:
                    if other.name != agent.name:
                        self.assertIn(f"- {other.korean_name} (내부 ID: {other.name}, 종: ", tail)
                self.assertNotIn("역할: ", tail)

    def test_chat_and_request_prompts_keep_all_common_headings(self) -> None:
        agents = roster_agents()
        common = prompts_module.read_common_rules(PROJECT_ROOT)
        headings = re.findall(r"^#{2,3} .+$", common, re.M)
        for agent in agents:
            with self.subTest(role=agent.role):
                for prompt in (build_chat(agent, agents), build_request(agent, agents)):
                    for heading in headings:
                        if heading == "### 문장 표현" and "# Discord chat channel" in prompt:
                            continue  # 채팅은 이 절을 턴 프롬프트 끝에 둔다.
                        self.assertIn(heading, prompt)


class CharacterMemoryTests(unittest.TestCase):
    def make_root(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        common = config_module.COMMON_RULES_FILE
        shutil.copy(PROJECT_ROOT / common, root / common)
        role_dir = root / ".claude" / "agents" / "worker"
        role_dir.mkdir(parents=True)
        for name in (config_module.ROLE_RULES_FILE, "SOUL.md"):
            shutil.copy(PROJECT_ROOT / ".claude" / "agents" / "worker" / name, role_dir / name)
        return root

    def test_signature_watches_chat_and_memory_files(self) -> None:
        root = self.make_root()
        memory_path = root / ".claude" / "agents" / "worker" / "MEMORY.md"
        signature = bot.prompt_source_signature(root, "worker")
        chat_stat = prompts_module.CHAT_PROMPT_PATH.stat()
        self.assertIn(
            (str(prompts_module.CHAT_PROMPT_PATH), chat_stat.st_mtime_ns, chat_stat.st_size), signature,
        )
        self.assertIn((str(memory_path), 0, -1), signature)
        memory_path.write_text("- 젤리는 매운 떡볶이를 좋아한다.\n", encoding="utf-8")
        stat = memory_path.stat()
        changed = bot.prompt_source_signature(root, "worker")
        self.assertIn((str(memory_path), stat.st_mtime_ns, stat.st_size), changed)
        self.assertNotEqual(signature, changed)

    def test_memory_file_is_injected_after_persona(self) -> None:
        root = self.make_root()
        agent = bot.AgentConfig(role="worker", name="jelly", korean_name="젤리", token="", tools=[])
        without = {"request": build_request(agent, [agent], root), "chat": build_chat(agent, [agent], root)}
        for prompt in without.values():
            self.assertNotIn("# Character memory", prompt)
        (root / ".claude" / "agents" / "worker" / "MEMORY.md").write_text(
            "- 젤리는 매운 떡볶이를 좋아한다.\n", encoding="utf-8"
        )
        soul = (root / ".claude" / "agents" / "worker" / "SOUL.md").read_text(encoding="utf-8").strip()
        block = "# Character memory\n\n- 젤리는 매운 떡볶이를 좋아한다.\n\n---\n\n"
        for kind, prompt in (
            ("request", build_request(agent, [agent], root)), ("chat", build_chat(agent, [agent], root)),
        ):
            with self.subTest(kind=kind):
                self.assertIn(f"# Persona\n\n{soul}\n\n---\n\n{block}", prompt)
                self.assertEqual(prompt.replace(block, "", 1), without[kind])


class TurnPromptTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, *, chat: bool):
        channel = SimpleNamespace(
            id=123, name="test", guild=SimpleNamespace(name="test"),
            typing=Mock(return_value=AsyncMock()),
            send=AsyncMock(return_value=SimpleNamespace(id=456)),
        )
        runtime = SimpleNamespace(
            ready=SimpleNamespace(wait=AsyncMock()), render_chat_history=Mock(return_value=""),
            render_speaker_history=AsyncMock(return_value=""),
            chat_last_message={}, chat_last_user_id={}, chat_pending={}, append_chat=Mock(),
            config=SimpleNamespace(
                chat_model_for=Mock(return_value=("test", "low")), chat=SimpleNamespace(sdk_max_turns=1),
                chat_topics={}, chat_channels={123} if chat else set(),
            ),
        )
        client = SimpleNamespace(
            runtime=runtime, get_channel=Mock(return_value=channel),
            _refresh_request_prompt=Mock(), _refresh_chat_prompt=Mock(),
            _request_prompt="REQUEST", _chat_prompt="CHAT",
            _react_target_label=Mock(return_value="없음"),
            agent=SimpleNamespace(name="jelly", korean_name="젤리", role="worker", tools=[]), user=SimpleNamespace(id=1),
        )
        prompts: list[str] = []
        options = Mock()

        async def fake_query(*, prompt, options):
            prompts.append(prompt)
            yield SimpleNamespace(result="[[next:stop]]", subtype="success", num_turns=1)

        with (
            patch.object(bot, "query", fake_query),
            patch.object(bot, "ResultMessage", SimpleNamespace),
            patch.object(bot, "ClaudeAgentOptions", options),
            patch.object(bot, "read_hooks", return_value={}),
            patch.object(bot, "build_server_channels_server", return_value=None),
            patch.object(bot, "ROLE_TOOL_BUILDERS", {}),
        ):
            await bot.AgentBot._handle_chat_turn(
                client, 123, turn_index=1, turn_limit=2, autonomous=False,
            )
        return client, prompts[0], options.call_args.kwargs["system_prompt"]

    async def test_request_turn_keeps_work_prompt(self) -> None:
        client, prompt, system_prompt = await self.run_turn(chat=False)
        client._refresh_request_prompt.assert_called_once()
        client._refresh_chat_prompt.assert_not_called()
        self.assertEqual(system_prompt, {"type": "preset", "preset": "claude_code", "append": "REQUEST"})
        self.assertNotIn("발언 화자:", prompt)

    async def test_chat_turn_ends_with_speaker_tone_guide(self) -> None:
        _, prompt, _ = await self.run_turn(chat=True)
        tone = bot.read_tone_guide(bot.canonical_role_dir(bot.RULES_ROOT, "worker") / "SOUL.md")
        style = bot.read_style_rules(bot.RULES_ROOT)
        self.assertTrue(tone)
        self.assertIn(f"젤리의 Tone Guide:\n{tone}\n{style}\n", prompt)

    def test_style_rules_move_from_chat_system_prompt(self) -> None:
        style = bot.read_style_rules(bot.RULES_ROOT)
        agents = roster_agents()
        self.assertNotIn(style, build_chat(agents[0], agents))
        self.assertIn(style, build_request(agents[0], agents))

    async def test_chat_turn_drops_work_sentences(self) -> None:
        client, prompt, system_prompt = await self.run_turn(chat=True)
        client._refresh_chat_prompt.assert_called_once()
        client._refresh_request_prompt.assert_not_called()
        self.assertIsInstance(system_prompt, str)
        self.assertEqual(system_prompt, "CHAT")


if __name__ == "__main__":
    unittest.main()
