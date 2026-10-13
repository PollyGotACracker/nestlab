"""최소 턴 없는 대화 진행과 기존 설정 호환성을 확인한다."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock


DISCORD_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_ROOT / "bot"))
bot = importlib.import_module("bot")
config_module = importlib.import_module("config")


class ChatSettingsTests(unittest.TestCase):
    def test_minimum_turn_settings_are_not_required_or_consumed(self) -> None:
        raw = json.loads((DISCORD_ROOT / "config.example.json").read_text(encoding="utf-8"))
        original = config_module.load_settings_sections(raw)
        for name in ("user_min_turns", "auto_min_turns"):
            self.assertNotIn(name, raw["chat"])
            self.assertNotIn(name, {item.name for item in fields(config_module.ChatConfig)})
            raw["chat"][name] = "unused legacy value"
        self.assertEqual(config_module.load_settings_sections(raw), original)

    def test_sdk_turn_limit_can_be_omitted(self) -> None:
        raw = json.loads((DISCORD_ROOT / "config.example.json").read_text(encoding="utf-8"))
        del raw["chat"]["sdk_max_turns"]
        self.assertIsNone(config_module.load_settings_sections(raw)["chat"].sdk_max_turns)

    def test_router_model_defaults_when_omitted(self) -> None:
        raw = json.loads((DISCORD_ROOT / "config.example.json").read_text(encoding="utf-8"))
        del raw["chat"]["router_model"]
        self.assertEqual(config_module.load_settings_sections(raw)["chat"].router_model, "claude-haiku-5-5")

    def test_sdk_turn_limit_preserves_positive_values(self) -> None:
        for value in (1, 4, 12):
            with self.subTest(value=value):
                raw = json.loads((DISCORD_ROOT / "config.example.json").read_text(encoding="utf-8"))
                raw["chat"]["sdk_max_turns"] = value
                self.assertEqual(config_module.load_settings_sections(raw)["chat"].sdk_max_turns, value)

    def test_sdk_turn_limit_rejects_nonpositive_values_and_null(self) -> None:
        for value in (0, -1, None):
            with self.subTest(value=value):
                raw = json.loads((DISCORD_ROOT / "config.example.json").read_text(encoding="utf-8"))
                raw["chat"]["sdk_max_turns"] = value
                with self.assertRaises((ValueError, TypeError)):
                    config_module.load_settings_sections(raw)

    def test_other_settings_remain_required(self) -> None:
        original = json.loads((DISCORD_ROOT / "config.example.json").read_text(encoding="utf-8"))
        for section, cls in config_module.SETTINGS_SECTIONS.items():
            for item in fields(cls):
                if (section, item.name) in (("chat", "sdk_max_turns"), ("chat", "router_model")):
                    continue
                with self.subTest(section=section, key=item.name):
                    raw = json.loads(json.dumps(original))
                    del raw["chat"]["sdk_max_turns"]
                    del raw[section][item.name]
                    with self.assertRaises(ValueError) as raised:
                        config_module.load_settings_sections(raw)
                    self.assertIn(f"{section}.{item.name}", str(raised.exception))
                    self.assertNotIn("chat.sdk_max_turns", str(raised.exception))

    def test_maximum_turns_still_requires_positive_value(self) -> None:
        raw = json.loads((DISCORD_ROOT / "config.example.json").read_text(encoding="utf-8"))
        raw["chat"]["max_turns"] = 0
        with self.assertRaisesRegex(ValueError, "chat.max_turns"):
            config_module.load_settings_sections(raw)


class ChatContinuationTests(unittest.IsolatedAsyncioTestCase):
    def make_runtime(self, next_name):
        return SimpleNamespace(
            chat_lock_for=Mock(return_value=asyncio.Lock()), chat_pending={},
            config=SimpleNamespace(chat=SimpleNamespace(max_turns=4, turn_delay_seconds=0)),
            clients={
                "jelly": SimpleNamespace(_handle_chat_turn=AsyncMock(return_value=next_name)),
                "rio": SimpleNamespace(_handle_chat_turn=AsyncMock(return_value="stop")),
            },
        )

    async def run_conversation(self, runtime, **kwargs):
        await bot.run_chat_conversation(
            runtime, 123, starter_name="jelly", seed="test", **kwargs,
        )

    async def test_stop_and_invalid_next_end_first_turn(self) -> None:
        for autonomous in (False, True):
            for next_name in ("stop", None, "jelly", "unknown"):
                with self.subTest(autonomous=autonomous, next_name=next_name):
                    runtime = self.make_runtime(next_name)
                    await self.run_conversation(runtime, autonomous=autonomous)
                    runtime.clients["jelly"]._handle_chat_turn.assert_awaited_once()
                    runtime.clients["rio"]._handle_chat_turn.assert_not_awaited()

    async def test_valid_followup_continues(self) -> None:
        runtime = self.make_runtime("rio")
        await self.run_conversation(runtime, autonomous=False)
        runtime.clients["rio"]._handle_chat_turn.assert_awaited_once()
        self.assertEqual(runtime.clients["rio"]._handle_chat_turn.call_args.kwargs["turn_index"], 2)

    async def test_single_turn_reaction_never_continues(self) -> None:
        runtime = self.make_runtime("rio")
        await self.run_conversation(runtime, autonomous=False, single_turn=True)
        runtime.clients["rio"]._handle_chat_turn.assert_not_awaited()
        self.assertTrue(runtime.clients["jelly"]._handle_chat_turn.call_args.kwargs["reaction_trigger"])

    async def test_maximum_turns_ends_conversation(self) -> None:
        runtime = self.make_runtime("rio")
        runtime.config.chat.max_turns = 3
        runtime.clients["rio"]._handle_chat_turn.return_value = "jelly"
        await self.run_conversation(runtime, autonomous=False)
        self.assertEqual(runtime.clients["jelly"]._handle_chat_turn.await_count, 2)
        self.assertEqual(runtime.clients["rio"]._handle_chat_turn.await_count, 1)

    async def test_pending_user_input_prevents_followup(self) -> None:
        runtime = self.make_runtime("rio")

        async def receive_user_input(*args, **kwargs):
            runtime.chat_pending[123] = 1
            return "rio"

        runtime.clients["jelly"]._handle_chat_turn.side_effect = receive_user_input
        await self.run_conversation(runtime, autonomous=False)
        runtime.clients["rio"]._handle_chat_turn.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
