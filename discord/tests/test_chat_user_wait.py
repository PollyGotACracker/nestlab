"""사용자 판단 대기의 후속 차단·재개·저장과 응답 API 호환을 확인한다."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


DISCORD_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_ROOT / "bot"))
bot = importlib.import_module("bot")
prompts_module = importlib.import_module("prompts")
memory_module = importlib.import_module("memory")
chat_loop_module = importlib.import_module("chat_loop")


def make_runtime() -> bot.Runtime:
    config = SimpleNamespace(
        chat=SimpleNamespace(history_max_lines=20, history_hours=24, line_max_chars=10000,
                             max_turns=4, turn_delay_seconds=0, sdk_max_turns=1),
        agents=[], chat_topics={}, chat_channels=set(), chat_model_for=Mock(return_value=("test", "low")),
    )
    runtime = bot.Runtime(config=config, archive_workflow=object())
    runtime.ready.set()
    runtime.clients = {
        "jelly": SimpleNamespace(_handle_chat_turn=AsyncMock(return_value="rio")),
        "rio": SimpleNamespace(_handle_chat_turn=AsyncMock(return_value="stop")),
    }
    return runtime


class WaitParserTests(unittest.TestCase):
    def test_wait_overrides_next_and_keeps_three_value_api(self) -> None:
        for next_name in ("stop", "rio"):
            response = f"선택해 주세요.\n[[wait:user]]\n[[next:{next_name}]]"
            self.assertEqual(bot.parse_chat_controls(response), ("선택해 주세요.", "stop", None, True))
            self.assertEqual(prompts_module.parse_chat_result(response), ("선택해 주세요.", "stop", None))
        self.assertEqual(bot.parse_chat_controls("[[react:👍]]\n[[WAIT:USER]]\n[[NEXT:RIO]]"),
                         ("", "stop", "👍", True))

    def test_normal_stop_has_no_wait(self) -> None:
        self.assertEqual(bot.parse_chat_controls("채팅 종료\n[[next:stop]]"),
                         ("채팅 종료", "stop", None, False))

    def test_wait_must_be_single_control_line_before_next(self) -> None:
        self.assertEqual(bot.parse_chat_controls("[[wait:user]]"), ("", "stop", None, True))
        for response in ("본문 [[wait:user]]\n[[next:stop]]",
                         "[[wait:user]]\n[[wait:user]]\n[[next:stop]]",
                         "[[wait:agent]]\n[[next:stop]]",
                         "[[wait:user]]\n[[react:👍]]\n[[next:stop]]",
                         "[[next:stop]]\n[[wait:user]]", "[[wait:user]]\n본문\n[[next:stop]]"):
            with self.subTest(response=response), self.assertRaises(ValueError):
                bot.parse_chat_controls(response)


class WaitStorageTests(unittest.TestCase):
    def test_save_restore_wait_and_message_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(memory_module, "CHAT_STATE_DIR", Path(directory)):
            runtime = make_runtime()
            runtime.chat_user_wait[123] = {"reason": "user_decision", "agent": "jelly"}
            runtime.append_chat(123, "jelly", "선택해 주세요.", 456, message_ids=[456, 457])
            restored = make_runtime()
            bot.load_chat_state(123, restored)
            self.assertEqual(restored.chat_user_wait, runtime.chat_user_wait)
            self.assertEqual(list(restored.chat_history_for(123)), list(runtime.chat_history_for(123)))
            self.assertEqual(restored.chat_message_groups[456], {456, 457})

    def test_old_format_loads_without_wait(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(memory_module, "CHAT_STATE_DIR", Path(directory)):
            bot.chat_state_path(123).write_text(json.dumps({
                "seq": 1, "history": [[1, 0, "user", "옛 기록"]], "sessions": {"jelly": "unused"},
            }), encoding="utf-8")
            runtime = make_runtime()
            bot.load_chat_state(123, runtime)
            self.assertEqual(runtime.chat_user_wait, {})
            self.assertEqual(list(runtime.chat_history_for(123)), [(1, 0, "user", "옛 기록", None)])


class WaitConversationTests(unittest.IsolatedAsyncioTestCase):
    async def run_conversation(self, runtime, **kwargs) -> None:
        await bot.run_chat_conversation(runtime, 123, starter_name="jelly", seed="test", **kwargs)

    async def test_nonuser_entries_do_not_run_turn_or_clear_wait(self) -> None:
        cases = [dict(autonomous=True),
                 dict(autonomous=False, single_turn=True, trigger_speaker="사용자 리액션", trigger_text="👍"),
                 dict(autonomous=False, trigger_speaker="투표 결과", trigger_text="1위 결과"),
                 dict(autonomous=False, user_message=True, trigger_message_id=999)]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                runtime = make_runtime()
                runtime.chat_user_wait[123] = {"reason": "user_decision", "agent": "jelly"}
                with patch.object(chat_loop_module, "save_chat_state") as save, patch.object(bot, "save_chat_state", save):
                    await self.run_conversation(runtime, **kwargs)
                runtime.clients["jelly"]._handle_chat_turn.assert_not_awaited()
                runtime.clients["rio"]._handle_chat_turn.assert_not_awaited()
                self.assertIn(123, runtime.chat_user_wait)
                self.assertEqual(list(runtime.chat_history_for(123)), [])
                save.assert_not_called()
                self.assertFalse(runtime.chat_lock_for(123).locked())

    async def test_invalid_or_bot_pending_cannot_resume(self) -> None:
        for author_id in (None, True, 0, "7", 22):
            with self.subTest(author_id=author_id):
                runtime = make_runtime()
                runtime.roster["rio"] = 22
                runtime.chat_user_wait[123] = {"reason": "user_decision"}
                runtime.add_pending(123, 999, author_id)
                with patch.object(chat_loop_module, "save_chat_state"), patch.object(bot, "save_chat_state"):
                    await self.run_conversation(runtime, autonomous=False, user_message=True, trigger_message_id=999)
                self.assertIn(123, runtime.chat_user_wait)
                runtime.clients["jelly"]._handle_chat_turn.assert_not_awaited()

    async def test_actual_user_resumes_judgment_without_archive_approval(self) -> None:
        runtime = make_runtime()
        runtime.chat_user_wait[123] = {"reason": "user_decision", "agent": "jelly"}
        workflow = runtime.archive_workflow
        runtime.add_pending(123, 999, 7)
        with patch.object(chat_loop_module, "save_chat_state"), patch.object(bot, "save_chat_state"):
            await self.run_conversation(runtime, autonomous=False, user_message=True,
                                        trigger_message_id=999, trigger_speaker="사용자", trigger_text="어떤 차이인가요?")
        self.assertNotIn(123, runtime.chat_user_wait)
        self.assertEqual(runtime.chat_pending[123], 0)
        runtime.clients["jelly"]._handle_chat_turn.assert_awaited_once()
        self.assertIs(runtime.archive_workflow, workflow)

    async def test_wait_state_blocks_followup_even_when_turn_returns_next(self) -> None:
        runtime = make_runtime()

        async def wait_with_conflicting_next(*args, **kwargs):
            runtime.chat_user_wait[123] = {"reason": "user_decision"}
            return "rio"

        runtime.clients["jelly"]._handle_chat_turn.side_effect = wait_with_conflicting_next
        await self.run_conversation(runtime, autonomous=False)
        runtime.clients["rio"]._handle_chat_turn.assert_not_awaited()

    async def test_plain_stop_does_not_block_later_autonomous_chat(self) -> None:
        runtime = make_runtime()
        runtime.clients["jelly"]._handle_chat_turn.return_value = "stop"
        await self.run_conversation(runtime, autonomous=False)
        await self.run_conversation(runtime, autonomous=True)
        self.assertEqual(runtime.clients["jelly"]._handle_chat_turn.await_count, 2)
        self.assertEqual(runtime.chat_user_wait, {})


class WaitDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def deliver(self, response, runtime, *, fail=False):
        channel = SimpleNamespace(
            id=123, name="test", guild=SimpleNamespace(name="test"),
            typing=Mock(return_value=AsyncMock()),
            send=AsyncMock(return_value=SimpleNamespace(id=456)),
        )
        if fail:
            channel.send.side_effect = RuntimeError("전송 실패")
        client = SimpleNamespace(
            runtime=runtime, _refresh_request_prompt=Mock(), get_channel=Mock(return_value=channel),
            _react_target_label=Mock(return_value="없음"),
            agent=SimpleNamespace(name="jelly", role="worker", tools=[]),
            user=SimpleNamespace(id=1), _request_prompt="test",
        )

        async def fake_query(*, prompt, options):
            yield SimpleNamespace(result=response, subtype="success", num_turns=1)

        with (patch.object(bot, "query", fake_query),
              patch.object(bot, "ResultMessage", SimpleNamespace),
              patch.object(bot, "ClaudeAgentOptions", Mock()),
              patch.object(bot, "read_hooks", return_value={}),
              patch.object(bot, "build_server_channels_server", return_value=None),
              patch.object(bot, "ROLE_TOOL_BUILDERS", {})):
            next_name = await bot.AgentBot._handle_chat_turn(
                client, 123, turn_index=1, turn_limit=4, autonomous=False,
            )
        return next_name, channel

    async def test_wait_marker_is_hidden_and_conflicting_next_is_stop(self) -> None:
        runtime = make_runtime()
        with tempfile.TemporaryDirectory() as directory, patch.object(memory_module, "CHAT_STATE_DIR", Path(directory)):
            next_name, channel = await self.deliver("선택해 주세요.\n[[wait:user]]\n[[next:rio]]", runtime)
            saved = json.loads(bot.chat_state_path(123).read_text(encoding="utf-8"))
        self.assertEqual(next_name, "stop")
        channel.send.assert_awaited_once_with("선택해 주세요.")
        self.assertEqual(saved["user_wait"]["reason"], "user_decision")
        self.assertNotIn("[[", list(runtime.chat_history_for(123))[0][3])

    async def test_send_failure_still_persists_wait_and_blocks_followup(self) -> None:
        runtime = make_runtime()
        with tempfile.TemporaryDirectory() as directory, patch.object(memory_module, "CHAT_STATE_DIR", Path(directory)):
            with self.assertRaisesRegex(RuntimeError, "전송 실패"):
                await self.deliver("선택해 주세요.\n[[wait:user]]\n[[next:rio]]", runtime, fail=True)
            restored = make_runtime()
            bot.load_chat_state(123, restored)
            await bot.run_chat_conversation(restored, 123, starter_name="rio", seed="poll", autonomous=False)
        self.assertIn(123, restored.chat_user_wait)
        restored.clients["rio"]._handle_chat_turn.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
