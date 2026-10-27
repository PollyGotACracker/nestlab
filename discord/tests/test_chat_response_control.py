"""응답 제어 계약과 Discord 전송 전 형식 오류 처리를 확인한다."""

from __future__ import annotations

import importlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


DISCORD_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_ROOT / "bot"))
bot = importlib.import_module("bot")
config_module = importlib.import_module("config")
prompts_module = importlib.import_module("prompts")


class ChatResponseControlTests(unittest.TestCase):
    # 올바른 제어 표식 응답이 파싱되는지 확인한다.
    def test_valid_responses(self) -> None:
        cases = [
            ("안녕하세요.\n[[next:rio]]", ("안녕하세요.", "rio", None)),
            ("[[react:👍]]\n[[next:stop]]", ("", "stop", "👍")),
            ("좋습니다.\n[[react:👍]]\n[[next:jelly]]", ("좋습니다.", "jelly", "👍")),
            ("첫 줄\n둘째 줄\n[[next:stop]]", ("첫 줄\n둘째 줄", "stop", None)),
            ("  본문\r\n[[REACT: 👍 ]]\r\n[[NEXT:RIO]]\r\n", ("본문", "rio", "👍")),
            ("[[next:stop]]", ("", "stop", None)),
            ("[[react:<:custom:123>]]\n[[next:rio]]", ("", "rio", "<:custom:123>")),
            # 마지막 next 줄이 빠진 응답은 전체를 본문으로 보고 stop으로 끝낸다.
            ("", ("", "stop", None)),
            ("본문", ("본문", "stop", None)),
        ]
        for response, expected in cases:
            with self.subTest(response=response):
                self.assertEqual(prompts_module.parse_chat_result(response), expected)

    # 다음 화자 없이 대기와 반응 표식만 있는 응답이 파싱되는지 확인한다.
    def test_wait_and_react_without_next(self) -> None:
        cases = [
            ("본문\n[[wait:user]]", ("본문", "stop", None, True)),
            ("본문\n[[react:👍]]", ("본문", "stop", "👍", False)),
            ("본문\n[[react:👍]]\n[[wait:user]]", ("본문", "stop", "👍", True)),
            ("[[react:👍]]", ("", "stop", "👍", False)),
        ]
        for response, expected in cases:
            with self.subTest(response=response):
                self.assertEqual(prompts_module.parse_chat_controls(response), expected)

    # 잘못된 제어 표식 응답이 거부되는지 확인한다.
    def test_invalid_responses(self) -> None:
        cases = [
            "[[next:rio]]\n[[next:stop]]",
            "[[next:rio]]\n본문",
            "본문 [[next:stop]]",
            "[[next:stop]]\n[[react:👍]]",
            "[[react:👍]]\n[[react:🎉]]\n[[next:stop]]",
            "[[react:👍]]\n본문\n[[next:stop]]",
            "본문 [[react:👍]]\n[[next:stop]]",
            "[[react:👍]]\n\n[[next:stop]]",
            "[[next:]]", "[[next:two names]]", "[[next:stop]",
            "[[react:]]\n[[next:stop]]",
            "[[react:   ]]\n[[next:stop]]",
            "[[react:👍]\n[[next:stop]]",
            "[[NEXT:rio]]\n본문\n[[next:stop]]",
            "[[ react:👍]]\n[[next:stop]]",
        ]
        for response in cases:
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    prompts_module.parse_chat_result(response)


class ChatResponseDeliveryTests(unittest.IsolatedAsyncioTestCase):
    # 가짜 채널과 질의로 한 턴을 실행하고 결과를 반환한다.
    async def run_turn(
        self, response: str, *, reaction_trigger: bool = True, chat_config=None,
        turn_index: int = 1, turn_limit: int = 1, autonomous: bool = False,
    ):
        # 채널과 런타임을 가짜 객체로 만든다.
        channel = SimpleNamespace(
            id=123, name="test", guild=SimpleNamespace(name="test"),
            typing=Mock(return_value=AsyncMock()),
            send=AsyncMock(return_value=SimpleNamespace(id=456)),
            get_partial_message=Mock(return_value=SimpleNamespace(add_reaction=AsyncMock())),
        )
        runtime = SimpleNamespace(
            ready=SimpleNamespace(wait=AsyncMock()),
            render_chat_history=Mock(return_value=""),
            chat_last_message={123: SimpleNamespace(id=789, author=SimpleNamespace(id=2))},
            chat_pending={}, append_chat=Mock(), chat_last_user_id={},
            config=SimpleNamespace(chat_model_for=Mock(return_value=("test", "low")),
                                   chat=chat_config if chat_config is not None else SimpleNamespace(sdk_max_turns=1),
                                   chat_topics={}, chat_channels=set()),
        )
        client = SimpleNamespace(
            runtime=runtime, _refresh_request_prompt=Mock(), get_channel=Mock(return_value=channel),
            _react_target_label=Mock(return_value="사용자 메시지"),
            _react_target_author=Mock(return_value="사용자"),
            agent=SimpleNamespace(name="jelly", role="worker", tools=[]),
            user=SimpleNamespace(id=1), _request_prompt="test",
        )
        prompts = []
        options_factory = Mock()

        # 프롬프트를 기록하고 고정 응답을 내는 가짜 질의이다.
        async def fake_query(*, prompt, options):
            prompts.append(prompt)
            yield SimpleNamespace(result=response, subtype="success", num_turns=1)

        # 봇 의존성을 대체하고 턴을 실행한다.
        with (
            patch.object(bot, "query", fake_query),
            patch.object(bot, "ResultMessage", SimpleNamespace),
            patch.object(bot, "ClaudeAgentOptions", options_factory),
            patch.object(bot, "read_hooks", return_value={}),
            patch.object(bot, "build_server_channels_server", return_value=None),
            patch.object(bot, "ROLE_TOOL_BUILDERS", {}),
        ):
            next_name = await bot.AgentBot._handle_chat_turn(
                client, 123, turn_index=turn_index, turn_limit=turn_limit,
                autonomous=autonomous, reaction_trigger=reaction_trigger,
            )
        runtime.sdk_options = options_factory.call_args.kwargs
        return next_name, channel, runtime, prompts[0]

    # 설정에서 읽은 SDK 턴 제한이 질의 옵션에 전달되는지 확인한다.
    async def test_loaded_sdk_turn_limit_reaches_options(self) -> None:
        for value in (None, 4):
            with self.subTest(value=value):
                raw = json.loads((DISCORD_ROOT / "config.example.json").read_text(encoding="utf-8"))
                if value is None:
                    del raw["chat"]["sdk_max_turns"]
                else:
                    raw["chat"]["sdk_max_turns"] = value
                chat_config = config_module.load_settings_sections(raw)["chat"]
                _, _, runtime, _ = await self.run_turn("[[next:stop]]", chat_config=chat_config)
                self.assertIn("max_turns", runtime.sdk_options)
                self.assertEqual(runtime.sdk_options["max_turns"], value)

    # 잘못된 제어 표식이 든 응답이 Discord로 가지 않는지 확인한다.
    async def test_invalid_control_never_reaches_discord(self) -> None:
        for response in (
            "본문\n[[next:rio]]\n[[next:stop]]",
            "[[react:👍]]\n본문\n[[next:stop]]",
        ):
            with self.subTest(response=response):
                next_name, channel, runtime, _ = await self.run_turn(response)
                self.assertIsNone(next_name)
                channel.send.assert_not_awaited()
                channel.get_partial_message.assert_not_called()
                runtime.append_chat.assert_not_called()

    # 반응만 있는 응답과 프롬프트 규약을 확인한다.
    async def test_reaction_only_and_prompt_contract(self) -> None:
        next_name, channel, runtime, prompt = await self.run_turn("[[react:👍]]\n[[next:stop]]")
        self.assertEqual(next_name, "stop")
        channel.send.assert_not_awaited()
        channel.get_partial_message.return_value.add_reaction.assert_awaited_once_with("👍")
        runtime.append_chat.assert_called_once()

    # 본문만 전송되고 제어 표식은 전송되지 않는지 확인한다.
    async def test_only_body_is_sent(self) -> None:
        next_name, channel, runtime, _ = await self.run_turn("본문\n[[react:👍]]\n[[next:rio]]")
        self.assertEqual(next_name, "rio")
        channel.send.assert_awaited_once_with("본문")
        channel.get_partial_message.return_value.add_reaction.assert_awaited_once_with("👍")
        self.assertNotIn("[[", runtime.append_chat.call_args.args[2])

    # 다음 화자 지정이 없는 본문은 전송하고 대화를 멈추는지 확인한다.
    async def test_body_without_next_is_sent_and_stops(self) -> None:
        next_name, channel, runtime, _ = await self.run_turn("본문", reaction_trigger=False)
        self.assertEqual(next_name, "stop")
        channel.send.assert_awaited_once_with("본문")
        runtime.append_chat.assert_called_once()

    # 본문 없는 넘김은 전송하지 않는지 확인한다.
    async def test_handoff_without_body_is_not_sent(self) -> None:
        next_name, channel, runtime, _ = await self.run_turn(
            "[[next:jelly]]", reaction_trigger=False, turn_index=1, turn_limit=6,
        )
        self.assertEqual(next_name, "jelly")
        channel.send.assert_not_awaited()
        channel.get_partial_message.assert_not_called()
        runtime.append_chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
