"""대화 품질 복구 계약의 상태 추적·삭제·근거 보존을 확인한다."""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from claude_agent_sdk import AssistantMessage, ServerToolResultBlock, ServerToolUseBlock


DISCORD_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_ROOT / "bot"))
bot = importlib.import_module("bot")
prompts_module = importlib.import_module("prompts")


def make_runtime() -> bot.Runtime:
    config = SimpleNamespace(
        chat=SimpleNamespace(history_max_lines=20, history_hours=24, line_max_chars=10000),
        agents=[],
    )
    return bot.Runtime(config=config)


class HandoffAndEvidenceTests(unittest.TestCase):
    def test_handoff_explanation_is_suppressed_but_content_is_kept(self) -> None:
        self.assertEqual(bot.suppress_handoff_body("제 담당이 아니라 리오에게 넘기겠습니다.", "rio"), "")
        self.assertEqual(bot.suppress_handoff_body("확인 결과는 이렇습니다.", "rio"), "확인 결과는 이렇습니다.")
        self.assertEqual(bot.suppress_handoff_body("제가 답하겠습니다.", "stop"), "제가 답하겠습니다.")
        self.assertEqual(
            bot.suppress_handoff_body("확인 결과는 정상입니다. 제 담당이 아니라 리오에게 넘기겠습니다.", "rio"),
            "확인 결과는 정상입니다.",
        )

    def test_reported_handoff_phrases_are_suppressed(self) -> None:
        cases = [
            "리오가 판단할 몫이라고 리키가 넘겼으니 제가 답할 내용은 없습니다.",
            "제가 낄 자리는 아니네요. 리오님 담당이라고 했으니 넘기겠습니다.",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(bot.suppress_handoff_body(text, "rio"), "")

    def test_tool_urls_are_kept_with_actual_use_and_result(self) -> None:
        uses: dict[str, tuple[str, list[str]]] = {}
        records: list[str] = []
        messages = [
            AssistantMessage(
                content=[ServerToolUseBlock(id="1", name="web_fetch", input={"url": "https://docs.example.com/a"})],
                model="test",
            ),
            AssistantMessage(
                content=[ServerToolResultBlock(tool_use_id="1", content={"url": "https://docs.example.com/a", "text": "ok"})],
                model="test",
            ),
        ]
        for message in messages:
            bot.collect_tool_evidence(message, uses, records)
        rendered = bot.render_tool_evidence(records)
        self.assertIn("사용 web_fetch", rendered)
        self.assertIn("확인 web_fetch", rendered)
        self.assertIn("https://docs.example.com/a", rendered)

    def test_failed_server_tool_result_is_unverified(self) -> None:
        uses = {"1": ("web_fetch", ["https://docs.example.com/missing"])}
        records: list[str] = []
        failed = AssistantMessage(
            content=[ServerToolResultBlock(
                tool_use_id="1",
                content={"type": "web_fetch_error", "url": "https://docs.example.com/missing", "error": "404"},
            )],
            model="test",
        )
        bot.collect_tool_evidence(failed, uses, records)
        rendered = bot.render_tool_evidence(records)
        self.assertIn("실패·미확인 web_fetch", rendered)
        self.assertNotIn("확인 web_fetch", rendered.replace("실패·미확인 web_fetch", ""))

    def test_successful_fetch_confirms_input_url_when_result_omits_it(self) -> None:
        uses = {"1": ("web_fetch", ["https://docs.example.com/a"])}
        records: list[str] = []
        success = AssistantMessage(
            content=[ServerToolResultBlock(tool_use_id="1", content={"type": "web_fetch_result", "text": "ok"})],
            model="test",
        )
        bot.collect_tool_evidence(success, uses, records)
        self.assertIn("확인 web_fetch: https://docs.example.com/a", bot.render_tool_evidence(records))

    def test_reply_target_is_fallback_when_router_fails(self) -> None:
        client = SimpleNamespace(
            runtime=SimpleNamespace(
                bot_id_to_name=Mock(return_value={22: "rio"}),
                config=SimpleNamespace(agents=[]),
                pick_chat_starter=Mock(return_value="jelly"),
                route_chat_target=AsyncMock(return_value=None),
            )
        )
        message = SimpleNamespace(
            mentions=[], content="확인해 줘", channel=SimpleNamespace(id=1),
            reference=SimpleNamespace(resolved=SimpleNamespace(author=SimpleNamespace(id=22))),
            id=100,
        )
        self.assertEqual(asyncio.run(bot.AgentBot._chat_target_name(client, message)), "rio")

    def test_last_replier_is_fallback_before_random_pick(self) -> None:
        history = [(1, 0.0, "폴리", "a", 1), (2, 0.0, "rio", "b", 2), (3, 0.0, "jelly", "c", 3),
                   (4, 0.0, "폴리", "d", 4), (5, 0.0, "pepper", "e", 5), (6, 0.0, "rio", "f", 6)]
        runtime = SimpleNamespace(
            bot_id_to_name=Mock(return_value={}),
            config=SimpleNamespace(agents=[SimpleNamespace(name=n, korean_name=n) for n in ("rio", "jelly", "pepper")]),
            pick_chat_starter=Mock(return_value="jelly"),
            route_chat_target=AsyncMock(return_value=None),
            chat_history_for=Mock(return_value=history),
        )
        message = SimpleNamespace(mentions=[], content="확인", channel=SimpleNamespace(id=1), reference=None, id=9)
        client = SimpleNamespace(runtime=runtime)
        self.assertEqual(asyncio.run(bot.AgentBot._chat_target_name(client, message)), "pepper")
        runtime.chat_history_for.return_value = []
        self.assertEqual(asyncio.run(bot.AgentBot._chat_target_name(client, message)), "jelly")

    def test_mention_wins_and_router_result_is_used(self) -> None:
        route = AsyncMock(return_value="pepper")
        client = SimpleNamespace(
            runtime=SimpleNamespace(bot_id_to_name=Mock(return_value={22: "rio"}), route_chat_target=route)
        )
        mentioned = SimpleNamespace(mentions=[SimpleNamespace(id=22)], content="x", channel=SimpleNamespace(id=1), id=1)
        plain = SimpleNamespace(mentions=[], content="x", channel=SimpleNamespace(id=1), id=2)
        self.assertEqual(asyncio.run(bot.AgentBot._chat_target_name(client, mentioned)), "rio")
        route.assert_not_awaited()
        self.assertEqual(asyncio.run(bot.AgentBot._chat_target_name(client, plain)), "pepper")

    def test_router_model_is_called_once_per_message(self) -> None:
        runtime = make_runtime()
        runtime.config.agents = [SimpleNamespace(name="rio", korean_name="리오")]
        runtime.config.chat.router_model = "m"
        calls = Mock(return_value="rio")

        async def fake(channel_id: int, text: str) -> str:
            calls()
            return "rio"

        runtime._request_chat_router = fake

        async def run() -> list:
            return await asyncio.gather(*(runtime.route_chat_target(1, 5, "hi") for _ in range(6)))

        self.assertEqual(asyncio.run(run()), ["rio"] * 6)
        self.assertEqual(calls.call_count, 1)

    def test_router_answer_picks_first_roster_id(self) -> None:
        runtime = make_runtime()
        runtime.config.agents = [
            SimpleNamespace(name="assistant", korean_name="버디"),
            SimpleNamespace(name="researcher", korean_name="니코"),
        ]
        runtime.config.chat.router_model = "m"
        runtime.render_chat_history = lambda channel_id: ""

        def fake_query(answer: str):
            async def gen(**kwargs):
                yield bot.ResultMessage(
                    subtype="success", duration_ms=0, duration_api_ms=0, is_error=False,
                    num_turns=1, session_id="s", result=answer,
                )
            return gen

        for answer, expected in (("`researcher` (assistant 아님)", "researcher"), ("assistant입니다", "assistant"),
                                 ("assistants", None), ("모름", None), ("니코", "researcher"),
                                 ("버디입니다", "assistant")):
            with patch.object(bot, "query", fake_query(answer)), patch.object(bot, "log_token_usage"):
                self.assertEqual(asyncio.run(runtime._request_chat_router(1, "hi")), expected)

    def test_router_failure_returns_none(self) -> None:
        runtime = make_runtime()

        async def boom(channel_id: int, text: str) -> str:
            raise RuntimeError("x")

        runtime._request_chat_router = boom
        self.assertIsNone(asyncio.run(runtime.route_chat_target(1, 6, "hi")))

    def _digest_runtime(self):
        runtime = make_runtime()
        runtime.config.agents = [
            SimpleNamespace(name="jelly", korean_name="젤리"),
            SimpleNamespace(name="rio", korean_name="리오"),
        ]
        with patch.object(bot, "save_chat_state"):
            runtime.append_chat(1, "user", "질문", 10)
            runtime.append_chat(1, "jelly", "했슴다 캬~", 11)
            runtime.append_chat(1, "rio", "자기 말", 12)
        return runtime

    def test_other_speaker_text_becomes_digest_and_is_shared(self) -> None:
        runtime = self._digest_runtime()
        calls = Mock()

        async def fake(channel_id: int, items: list) -> dict:
            calls()
            return {seq: "요지 문장" for seq, _, _ in items}

        runtime._request_chat_digest = fake

        async def run() -> list:
            with patch.object(bot, "save_chat_state"):
                return await asyncio.gather(
                    runtime.render_speaker_history(1, "rio"), runtime.render_speaker_history(1, "rio"),
                )

        first, second = asyncio.run(run())
        self.assertEqual(first, second)
        self.assertNotIn("했슴다", first)
        self.assertIn("(요지) 요지 문장", first)
        self.assertNotIn("자기 말", first)
        self.assertIn("[리오(나)]", first)
        self.assertIn("[젤리]", first)
        self.assertIn("질문", first)
        self.assertEqual(calls.call_count, 1)

    def test_digest_failure_falls_back_to_original(self) -> None:
        runtime = self._digest_runtime()

        async def boom(channel_id: int, items: list) -> dict:
            raise RuntimeError("x")

        runtime._request_chat_digest = boom
        with patch.object(bot, "save_chat_state"):
            text = asyncio.run(runtime.render_speaker_history(1, "rio"))
        self.assertIn("했슴다", text)

    def test_tone_guide_section_is_read_verbatim(self) -> None:
        soul = DISCORD_ROOT.parent / ".claude" / "agents" / "researcher" / "SOUL.md"
        tone = prompts_module.read_tone_guide(soul)
        self.assertTrue(tone)
        self.assertIn(tone, soul.read_text(encoding="utf-8"))
        self.assertNotIn("## ", tone)


class RuntimeRecoveryTests(unittest.TestCase):
    def test_duplicate_pending_and_deleted_message_cleanup(self) -> None:
        runtime = make_runtime()
        self.assertTrue(runtime.claim_user_message(100))
        self.assertFalse(runtime.claim_user_message(100))
        with patch.object(bot, "save_chat_state"):
            runtime.append_chat(1, "user", "삭제할 본문", 100)
            runtime.image_captions[100] = "삭제할 이미지 설명"
            runtime.chat_last_message[1] = SimpleNamespace(id=100)
            runtime.chat_last_user_message[1] = SimpleNamespace(id=100)
            runtime.chat_last_user_id[1] = 7
            runtime.add_pending(1, 100, 7)
            self.assertEqual(runtime.remove_message(1, 100), 1)
        self.assertNotIn("삭제할 본문", runtime.render_chat_history(1))
        self.assertNotIn(100, runtime.image_captions)
        self.assertNotIn(1, runtime.chat_last_message)
        self.assertNotIn(1, runtime.chat_last_user_message)
        self.assertEqual(runtime.chat_pending[1], 0)

    def test_split_message_group_is_removed_by_any_chunk(self) -> None:
        runtime = make_runtime()
        with patch.object(bot, "save_chat_state"):
            runtime.append_chat(1, "jelly", "긴 본문", 200, message_ids=[200, 201, 202])
            self.assertEqual(runtime.remove_message(1, 201), 1)
        self.assertEqual(runtime.render_chat_history(1), "(아직 대화 없음)")


class ConversationTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_lock_waiter_does_not_leave_pending(self) -> None:
        runtime = make_runtime()
        runtime.clients = {"jelly": SimpleNamespace(_handle_chat_turn=AsyncMock(return_value="stop"))}
        runtime.config.chat.max_turns = 2
        runtime.config.chat.turn_delay_seconds = 0
        lock = runtime.chat_lock_for(1)
        await lock.acquire()
        runtime.add_pending(1, 300, 7)
        task = asyncio.create_task(bot.run_chat_conversation(
            runtime, 1, starter_name="jelly", seed=300, autonomous=False,
            user_message=True, trigger_message_id=300,
        ))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        runtime.consume_pending(1, 300, "handler_cancelled")
        lock.release()
        self.assertEqual(runtime.chat_pending[1], 0)

    async def test_end_reason_distinguishes_model_stop(self) -> None:
        runtime = make_runtime()
        runtime.config.chat.max_turns = 2
        runtime.config.chat.turn_delay_seconds = 0
        runtime.clients = {"jelly": SimpleNamespace(_handle_chat_turn=AsyncMock(return_value="stop"))}
        with self.assertLogs("agent_team", logging.INFO) as captured:
            await bot.run_chat_conversation(runtime, 1, starter_name="jelly", seed="x", autonomous=False)
        self.assertTrue(any("reason=model_stop" in line and "conversation=" in line for line in captured.output))


class PromptLocationTests(unittest.TestCase):
    def test_turn_prompt_is_watched_and_contains_recheck_contract(self) -> None:
        prompt = (DISCORD_ROOT / "prompts" / "TURN.md").read_text(encoding="utf-8")
        with patch.object(prompts_module, "canonical_role_dir", return_value=DISCORD_ROOT):
            with patch.object(Path, "stat", autospec=True) as stat:
                stat.return_value = SimpleNamespace(st_mtime_ns=1, st_size=1)
                signature = bot.prompt_source_signature(DISCORD_ROOT, "worker")
        self.assertIn(str(DISCORD_ROOT / "prompts" / "TURN.md"), [item[0] for item in signature])


if __name__ == "__main__":
    unittest.main()
