"""서버 장기기억·persona 기억·채널 요약·전체 삭제·토큰 로그를 가짜 query와 가짜 Discord로 확인한다.
실제 모델과 GitHub API는 호출하지 않는다.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord


DISCORD_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_ROOT / "bot"))
bot = importlib.import_module("bot")
config_module = importlib.import_module("config")
prompts_module = importlib.import_module("prompts")
memory_module = importlib.import_module("memory")
summary_module = importlib.import_module("summary")
memory_store = importlib.import_module("memory_store")

GUILD = 10
OTHER_GUILD = 20
CHANNEL = 100
OTHER_CHANNEL = 101
UNREGISTERED_CHANNEL = 102
THREAD = 200
FORUM = 300
USER = 7
OTHER_USER = 8
AGENTS = [
    bot.AgentConfig(role=role, name=name, korean_name=korean, token="", tools=[])
    for role, name, korean in (
        ("director", "rio", "리오"), ("researcher", "nico", "니코"), ("worker", "jelly", "젤리"),
        ("reviewer", "ricky", "리키"), ("documenter", "pepper", "페퍼"), ("assistant", "buddy", "버디"),
    )
]
JELLY = next(agent for agent in AGENTS if agent.name == "jelly")
RIO = next(agent for agent in AGENTS if agent.name == "rio")


# 기본값에 덮어쓸 값을 반영한 채팅 설정 객체를 만든다.
def chat_config(**overrides) -> SimpleNamespace:
    values = dict(history_max_lines=500, history_hours=12, line_max_chars=2000,
                  max_turns=4, turn_delay_seconds=0, sdk_max_turns=1)
    values.update(overrides)
    return SimpleNamespace(**values)


# 시험용 전체 설정 객체를 만든다.
def make_config(**overrides) -> SimpleNamespace:
    values = dict(
        chat=chat_config(), agents=AGENTS,
        allowed_guild_ids={GUILD, OTHER_GUILD}, allowed_user_ids={USER, OTHER_USER},
        archive_forum_id=FORUM,
        archive_repository=config_module.ArchiveRepositoryConfig(owner="example", repo="repo", token=""),
        summary=config_module.SummaryConfig(), memory=config_module.MemoryConfig(), chat_topics={}, chat_channels=set(),
        reminders=config_module.RemindersConfig(9, 1, 60, 1, 1),
        chat_model_for=Mock(return_value=("test", "low")),
        is_agent_channel=lambda channel_id: channel_id in {CHANNEL, OTHER_CHANNEL, FORUM},
    )
    values.update(overrides)
    return SimpleNamespace(**values)


# 404 NotFound 예외를 만든다.
def not_found() -> discord.NotFound:
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "missing")


# 403 Forbidden 예외를 만든다.
def forbidden() -> discord.Forbidden:
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "forbidden")


# 500 HTTPException 예외를 만든다.
def http_error() -> discord.HTTPException:
    return discord.HTTPException(SimpleNamespace(status=500, reason="Server Error"), "boom")


# 가짜 Discord 메시지 객체를 만든다.
def make_message(message_id, channel, *, author_id=USER, bot_author=False, content="", attachments=(), created=None):
    return SimpleNamespace(
        id=message_id, channel=channel, guild=channel.guild,
        author=SimpleNamespace(id=author_id, bot=bot_author, display_name=f"user{author_id}"),
        content=content, attachments=list(attachments),
        created_at=created or datetime.now(timezone.utc),
    )


# 실행 객체의 채팅 기록에 줄 하나를 추가한다.
def add_line(runtime, seq, age_hours, speaker, content, message_id=None, channel_id=CHANNEL) -> None:
    runtime.chat_history_for(channel_id).append(
        (seq, time.time() - age_hours * 3600, speaker, content, message_id)
    )
    runtime.chat_seq[channel_id] = max(runtime.chat_seq.get(channel_id, 0), seq)


# 도구 등록 데코레이터를 바꿔 기억 도구 모음을 만든다.
def build_tools(runtime, channel, agent, trigger, requester_id=None):
    # 도구 등록 데코레이터를 대신하는 가짜 함수이다.
    def decorator(name, description, schema):
        return lambda handler: handler

    # 도구 데코레이터를 대체하고 기억 서버를 만든다.
    with patch.object(memory_module, "tool", decorator), \
            patch.object(memory_module, "create_sdk_mcp_server", side_effect=lambda **kwargs: kwargs):
        built = bot.build_memory_server(channel, runtime, agent, trigger, requester_id)
    if built is None:
        return None, []
    _, names, server = built
    return {handler.__name__: handler for handler in server["tools"]}, names


# 도구 결과의 첫 텍스트를 반환한다.
def tool_output(result) -> str:
    return result["content"][0]["text"]


# 고정 출력을 순서대로 내는 가짜 요약 질의를 만든다.
def fake_summary_query(outputs, prompts=None, before=None):
    # 출력 목록을 순서대로 내보내는 반복자를 만든다.
    items = iter(outputs)

    # 프롬프트를 기록하고 다음 고정 출력을 내는 가짜 질의이다.
    async def fake_query(*, prompt, options):
        if prompts is not None:
            prompts.append(prompt)
        if before is not None:
            before()
        item = next(items)
        if isinstance(item, Exception):
            raise item
        yield SimpleNamespace(
            structured_output=item, result="", usage={"input_tokens": 3, "output_tokens": 2},
            total_cost_usd=0.002,
        )

    # 만든 가짜 query 함수를 반환한다.
    return fake_query


class FakeForum:
    # 스레드 목록을 가진 가짜 포럼을 만든다.
    def __init__(self, threads=(), archived=(), fail_archived=False):
        self.id = FORUM
        self.guild = SimpleNamespace(id=GUILD)
        self.threads = list(threads)
        self.archived = list(archived)
        self.fail_archived = fail_archived

    # 보관된 스레드를 차례로 내보낸다. 실패 설정이면 예외를 던진다.
    async def archived_threads(self, limit=None):
        if self.fail_archived:
            raise http_error()
        for thread in self.archived:
            yield thread


class FakeThread:
    # 메시지 목록을 가진 가짜 스레드를 만든다.
    def __init__(self, thread_id, messages=(), fail=False, during=None):
        self.id = thread_id
        self.messages = list(messages)
        self.fail = fail
        self.during = during

    # 메시지를 차례로 내보내고 중간 동작을 실행한다.
    async def history(self, limit=None, oldest_first=False):
        for message in self.messages:
            yield message
        if self.during is not None:
            self.during()
        if self.fail:
            raise http_error()


class FakeSession:
    # 비동기 컨텍스트에 진입하면 자신을 반환한다.
    async def __aenter__(self):
        return self

    # 비동기 컨텍스트를 나가며 예외를 삼키지 않는다.
    async def __aexit__(self, *args):
        return False


class MemoryTestBase(unittest.IsolatedAsyncioTestCase):
    # 임시 저장소, 실행 객체, 가짜 채널을 준비한다.
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.attach_dir = root / "attachments"
        self.attach_dir.mkdir()
        self.db_path = root / "memory.sqlite3"
        for patcher in (
            patch.object(memory_module, "CHAT_STATE_DIR", root / "chat_state"),
            patch.object(memory_module, "ATTACHMENT_ROOT", self.attach_dir),
            patch.object(bot, "is_archive_thread", lambda channel, cfg: getattr(channel, "archive", False)),
            patch.object(memory_module, "is_archive_thread", lambda channel, cfg: getattr(channel, "archive", False)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.missing: set[int] = set()
        self.forbidden: set[int] = set()
        self.channels = {
            CHANNEL: self.make_channel(CHANNEL, GUILD),
            OTHER_CHANNEL: self.make_channel(OTHER_CHANNEL, OTHER_GUILD),
            THREAD: self.make_channel(THREAD, GUILD, archive=True, parent_id=FORUM),
        }
        self.runtime = self.make_runtime()

    # 가짜 채널을 만든다.
    def make_channel(self, channel_id, guild_id, *, archive=False, parent_id=None):
        # 없는 메시지와 권한 없는 메시지에 예외를 던지는 가짜 조회이다.
        async def fetch_message(message_id):
            if message_id in self.missing:
                raise not_found()
            if message_id in self.forbidden:
                raise forbidden()
            return SimpleNamespace(id=message_id)

        # 채널 가짜 객체를 만들어 반환한다.
        return SimpleNamespace(
            id=channel_id, name=f"c{channel_id}", guild=SimpleNamespace(id=guild_id, name="guild"),
            parent_id=parent_id, parent=None, archive=archive,
            send=AsyncMock(return_value=SimpleNamespace(id=9999)),
            typing=Mock(return_value=AsyncMock()), fetch_message=fetch_message,
        )

    # 시험용 설정과 저장소를 가진 실행 객체를 만든다.
    def make_runtime(self, **config_overrides):
        runtime = bot.Runtime(
            config=make_config(**config_overrides), archive_workflow=object(),
            memory_approvals=bot.MemoryApprovalStore(Path(self.tmp.name) / "memory_approval.json"),
        )
        runtime.memory = memory_store.MemoryStore.open(self.db_path)
        self.addCleanup(runtime.memory.close)
        runtime.roster = {agent.name: 1000 + index for index, agent in enumerate(AGENTS)}
        client = self.make_client(runtime)
        runtime.clients = {agent.name: client for agent in AGENTS}
        runtime.ready.set()
        return runtime

    # 실행 객체를 가진 가짜 봇 클라이언트를 만든다.
    def make_client(self, runtime, name="buddy", channels=None):
        lookup = self.channels if channels is None else channels
        return SimpleNamespace(
            runtime=runtime, agent=SimpleNamespace(name=name),
            get_channel=lambda channel_id: lookup.get(channel_id),
            fetch_channel=AsyncMock(side_effect=not_found()),
            _is_memory_handler=lambda: True,
        )

    # 서버의 기억 내용을 정렬해 반환한다.
    def contents(self, runtime=None, guild_id=GUILD, kinds=memory_store.LONG_TERM_KINDS, agent=None):
        rows = (runtime or self.runtime).memory.search(
            guild_id, kinds, [], 100, agent=agent, require_match=False,
        )
        return sorted(row["content"] for row in rows)

    # 캐릭터 기억 내용을 정렬해 반환한다.
    def persona_contents(self, agent="jelly", guild_id=GUILD):
        return sorted(row["content"] for row in self.runtime.memory.list_persona(guild_id, agent))

    # 가짜 질의로 한 턴을 실행한다.
    async def run_turn(self, agent, *, trigger=None, usage=None, response="[[next:stop]]"):
        # 채널과 클라이언트를 가짜 객체로 만든다.
        channel = self.channels[CHANNEL]
        client = SimpleNamespace(
            runtime=self.runtime, _refresh_request_prompt=Mock(), get_channel=Mock(return_value=channel),
            _react_target_label=Mock(return_value="없음"), _react_target_author=Mock(return_value="사용자"),
            agent=agent, user=SimpleNamespace(id=1), _request_prompt="SYSTEM",
        )
        prompts: list[str] = []
        options = Mock()

        # 프롬프트를 기록하고 고정 응답을 내는 가짜 질의이다.
        async def fake_query(*, prompt, options):
            prompts.append(prompt)
            yield SimpleNamespace(result=response, subtype="success", num_turns=1,
                                  usage=usage, total_cost_usd=0.01)

        # 봇 의존성을 대체하고 턴을 실행한다.
        with (
            patch.object(bot, "query", fake_query),
            patch.object(bot, "ResultMessage", SimpleNamespace),
            patch.object(bot, "ClaudeAgentOptions", options),
            patch.object(bot, "read_hooks", return_value={}),
            patch.object(bot, "build_server_channels_server", return_value=None),
            patch.object(bot, "ROLE_TOOL_BUILDERS", {}),
        ):
            await bot.AgentBot._handle_chat_turn(
                client, CHANNEL, turn_index=1, turn_limit=1, autonomous=False,
                user_trigger_message_id=trigger,
            )
        return prompts[0], options.call_args.kwargs

    # 가짜 요약 질의로 요약을 한 번 실행한다.
    async def run_summary(self, outputs, prompts=None, before=None):
        with (
            patch.object(summary_module, "query", fake_summary_query(outputs, prompts, before)),
            patch.object(summary_module, "ResultMessage", SimpleNamespace),
            patch.object(summary_module, "ClaudeAgentOptions", Mock()),
        ):
            await bot.run_channel_summary(self.runtime, CHANNEL)

    # 예약된 비동기 작업이 끝나도록 이벤트 루프를 여러 번 양보한다.
    async def settle(self) -> None:
        for _ in range(20):
            await asyncio.sleep(0)


class ConfigAndRestartTests(MemoryTestBase):
    # C7: memory·summary 절이 없는 설정과 재시작 후 유지.
    def test_optional_sections_use_defaults(self) -> None:
        self.assertEqual(config_module.load_optional_section({}, "memory", config_module.MemoryConfig), config_module.MemoryConfig())
        self.assertEqual(config_module.load_optional_section({}, "summary", config_module.SummaryConfig), config_module.SummaryConfig())
        partial = config_module.load_optional_section({"memory": {"prompt_max_items": 2}}, "memory", config_module.MemoryConfig)
        self.assertEqual(partial.prompt_max_items, 2)
        self.assertEqual(partial.prompt_max_chars, config_module.MemoryConfig().prompt_max_chars)
        for raw in ({"memory": {"prompt_max_items": 0}}, {"memory": []}, {"summary": {"model": " "}}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                config_module.load_optional_section(raw, next(iter(raw)), config_module.MemoryConfig if "memory" in raw else config_module.SummaryConfig)
        self.assertEqual(config_module.memory_settings(SimpleNamespace()), config_module.MemoryConfig())
        self.assertEqual(config_module.summary_settings(SimpleNamespace()), config_module.SummaryConfig())

    # 재시작 뒤에도 기억이 남는지 확인한다.
    def test_memories_survive_restart(self) -> None:
        self.runtime.memory.add(GUILD, memory_store.KIND_EXPLICIT, "재시작 후에도 남는 기억", sources=[(1, CHANNEL)])
        self.runtime.memory.close()
        restarted = self.make_runtime()
        self.assertEqual(self.contents(restarted), ["재시작 후에도 남는 기억"])

    # 불러온 순번이 가장 큰 값을 따르는지 확인한다.
    def test_loaded_seq_uses_largest_value(self) -> None:
        memory_module.CHAT_STATE_DIR.mkdir(parents=True, exist_ok=True)
        bot.chat_state_path(CHANNEL).write_text(json.dumps({
            "seq": 1, "history": [[5, time.time(), "user7", "본문", 50]],
        }), encoding="utf-8")
        runtime = self.make_runtime()
        bot.load_chat_state(CHANNEL, runtime)
        self.assertEqual(runtime.chat_seq[CHANNEL], 5)


class GuildIsolationTests(MemoryTestBase):
    # C1: 서버 단위 조회 분리. C4: scope·다른 캐릭터·다른 서버 비노출.
    async def test_long_term_memory_is_separated_by_guild(self) -> None:
        self.runtime.memory.add(GUILD, memory_store.KIND_EXPLICIT, "떡볶이 좋아함", sources=[(1, CHANNEL)])
        self.runtime.memory.add(OTHER_GUILD, memory_store.KIND_EXPLICIT, "떡볶이 싫어함", sources=[(2, OTHER_CHANNEL)])
        self.assertIn("떡볶이 좋아함", bot.render_prompt_memories(self.runtime, GUILD, "떡볶이"))
        self.assertNotIn("싫어함", bot.render_prompt_memories(self.runtime, GUILD, "떡볶이"))
        tools, _ = build_tools(self.runtime, self.channels[OTHER_CHANNEL], RIO, None)
        result = tool_output(await tools["memory_search"]({"query": "떡볶이"}))
        self.assertIn("싫어함", result)
        self.assertNotIn("좋아함", result)

    # 캐릭터 기억이 캐릭터와 서버별로 분리되는지 확인한다.
    async def test_persona_memory_is_per_character_and_guild(self) -> None:
        store = self.runtime.memory
        store.add(GUILD, memory_store.KIND_PERSONA_EVENT, "젤리가 매운 라면을 먹었다", agent="jelly", sources=[(3, CHANNEL)])
        store.add(GUILD, memory_store.KIND_PERSONA_SETTING, "젤리는 사투리를 쓴다", agent="jelly", sources=[(4, CHANNEL)])
        self.assertIn("매운 라면", bot.render_prompt_persona_events(self.runtime, GUILD, "jelly", "라면"))
        self.assertEqual(bot.render_prompt_persona_events(self.runtime, GUILD, "rio", "라면"), "(없음)")
        self.assertEqual(bot.render_prompt_persona_events(self.runtime, OTHER_GUILD, "jelly", "라면"), "(없음)")
        self.assertIn("사투리", bot.render_persona_settings_block(self.runtime, GUILD, "jelly"))
        self.assertEqual(bot.render_persona_settings_block(self.runtime, GUILD, "rio"), "")
        self.assertEqual(bot.render_persona_settings_block(self.runtime, OTHER_GUILD, "jelly"), "")
        self.assertNotIn("라면", bot.render_prompt_memories(self.runtime, GUILD, "라면"))
        rio_tools, _ = build_tools(self.runtime, self.channels[CHANNEL], RIO, None)
        self.assertIn("없습니다", tool_output(await rio_tools["persona_memory_list"]({})))
        jelly_tools, _ = build_tools(self.runtime, self.channels[CHANNEL], JELLY, None)
        listed = tool_output(await jelly_tools["persona_memory_list"]({}))
        self.assertIn("매운 라면", listed)
        self.assertNotIn("라면", tool_output(await jelly_tools["memory_search"]({"query": "라면"})))


class ArchiveTests(MemoryTestBase):
    # C2: archive 수집·수정·동기화.
    def test_capture_targets_allowed_authors_in_archive_threads(self) -> None:
        thread = self.channels[THREAD]
        attachment = SimpleNamespace(filename="plan.png")
        cases = [
            (make_message(1, thread, content="허용 사용자 글", attachments=[attachment]), True),
            (make_message(2, thread, author_id=1002, bot_author=True, content="캐릭터 글"), True),
            (make_message(3, thread, author_id=55, content="허용 밖 사용자"), False),
            (make_message(4, thread, author_id=56, bot_author=True, content="다른 봇"), False),
            (make_message(5, self.channels[CHANNEL], content="일반 채널"), False),
        ]
        for message, expected in cases:
            with self.subTest(message=message.content):
                self.assertEqual(bot.capture_archive_message(self.runtime, message), expected)
        self.assertEqual(self.contents(), ["캐릭터 글", "허용 사용자 글\n[첨부: plan.png]"])

    # 메시지 수정이 아카이브 기억에 반영되는지 확인한다.
    async def test_edit_updates_archive_memory(self) -> None:
        thread = self.channels[THREAD]
        bot.capture_archive_message(self.runtime, make_message(1, thread, content="처음 글"))
        client = self.make_client(self.runtime)
        payload = SimpleNamespace(message=make_message(1, thread, content="고친 글"))
        await bot.AgentBot.on_raw_message_edit(client, payload)
        self.assertEqual(self.contents(), ["고친 글"])

    # 최근 삭제된 메시지를 기억하지 않는지 확인한다.
    def test_recently_deleted_message_is_not_captured(self) -> None:
        self.runtime.record_deleted({1})
        self.assertFalse(bot.capture_archive_message(self.runtime, make_message(1, self.channels[THREAD], content="글")))

    # 가짜 포럼으로 아카이브 동기화를 실행한다.
    async def sync(self, forum) -> None:
        client = self.make_client(self.runtime, channels={**self.channels, FORUM: forum})
        with patch.object(bot.discord, "ForumChannel", FakeForum):
            await memory_module.sync_archive_forum(client)

    # 동기화가 추가와 삭제를 처리하면서 동기화 중 생긴 변경을 보존하는지 확인한다.
    async def test_sync_adds_and_removes_and_preserves_live_changes(self) -> None:
        # 채널과 저장소를 준비하고 기존 기억을 넣는다.
        thread = self.channels[THREAD]
        store = self.runtime.memory
        store.upsert_archive(GUILD, 999, FORUM, THREAD, "user7", "사라진 글")

        # 동기화 중 새 메시지는 보존하고, 동기화 중 삭제된 메시지는 다시 넣지 않는다.
        def during_sync():
            # 동기화 중 새 메시지는 보존하고, 동기화 중 삭제된 메시지는 다시 넣지 않는다.
            bot.capture_archive_message(self.runtime, make_message(777, thread, content="동기화 중 새 글"))
            bot.handle_deleted_messages(self.runtime, {502})

        # 활성 글과 보관 글을 동기화하고 결과를 확인한다.
        active = FakeThread(THREAD, [make_message(501, thread, content="활성 글"),
                                     make_message(502, thread, content="삭제될 글")], during=during_sync)
        archived = FakeThread(201, [make_message(601, thread, content="보관 글")])
        await self.sync(FakeForum([active], [archived]))
        self.assertEqual(self.contents(), ["동기화 중 새 글", "보관 글", "활성 글"])

    # 동기화 오류가 나면 삭제를 건너뛰는지 확인한다.
    async def test_sync_error_skips_deletion(self) -> None:
        thread = self.channels[THREAD]
        self.runtime.memory.upsert_archive(GUILD, 999, FORUM, THREAD, "user7", "남아야 할 글")
        failing = FakeThread(THREAD, [make_message(501, thread, content="활성 글")], fail=True)
        await self.sync(FakeForum([failing], []))
        self.assertEqual(self.contents(), ["남아야 할 글"])
        await self.sync(FakeForum([FakeThread(THREAD, [make_message(501, thread, content="활성 글")])],
                                  fail_archived=True))
        self.assertEqual(self.contents(), ["남아야 할 글", "활성 글"])

    # 시작 시 동기화가 한 번만 실행되는지 확인한다.
    async def test_startup_runs_once(self) -> None:
        client = SimpleNamespace(
            user=SimpleNamespace(id=1), agent=SimpleNamespace(name="buddy"), runtime=self.runtime,
            get_channel=Mock(return_value=FakeForum()), _is_memory_handler=lambda: True,
        )
        with patch.object(bot.discord, "ForumChannel", FakeForum), \
                patch.object(bot, "run_memory_startup", AsyncMock()) as startup:
            await bot.AgentBot.on_ready(client)
            await bot.AgentBot.on_ready(client)
            await self.settle()
        startup.assert_called_once()


class DeleteTests(MemoryTestBase):
    # C3: 요청·대상·분할 조각·스레드·채널 미확인·요약 대상 줄 삭제, 시작 시 NotFound 삭제.
    def setUp(self) -> None:
        super().setUp()
        with patch.object(bot, "save_chat_state"):
            self.runtime.append_chat(CHANNEL, "user7", "나는 매운 음식을 좋아해", 40)
            self.runtime.append_chat(CHANNEL, "user7", "방금 말 기억해", 50)
            self.runtime.append_chat(CHANNEL, "jelly", "긴 답변", 60, message_ids=[60, 61, 62])

    # 기억 저장 도구를 호출하고 결과 문장을 반환한다.
    async def save(self, target, trigger=50, scope="guild"):
        tools, _ = build_tools(self.runtime, self.channels[CHANNEL], JELLY, trigger)
        return tool_output(await tools["memory_save"]({
            "content": "사용자는 매운 음식을 좋아한다", "target_message_id": str(target), "scope": scope,
        }))

    # 메시지 삭제 이벤트를 처리한다.
    async def delete(self, message_id, channel_id=CHANNEL, channels=None):
        client = self.make_client(self.runtime, channels=channels)
        await bot.AgentBot.on_raw_message_delete(
            client, SimpleNamespace(channel_id=channel_id, message_id=message_id),
        )

    # 요청 메시지나 대상 메시지를 지우면 기억이 사라지는지 확인한다.
    async def test_request_or_target_deletion_removes_memory(self) -> None:
        self.assertIn("기억했습니다", await self.save(40, trigger=50))
        await self.delete(50)
        self.assertEqual(self.contents(), [])
        with patch.object(bot, "save_chat_state"):
            self.runtime.append_chat(CHANNEL, "user7", "다시 기억해", 55)
        self.assertIn("기억했습니다", await self.save(40, trigger=55))
        await self.delete(40)
        self.assertEqual(self.contents(), [])
        self.assertNotIn("기억했습니다", await self.save(40, trigger=55))
        self.assertEqual(self.contents(), [])

    # 나뉜 메시지 조각을 지워도 기억이 사라지는지 확인한다.
    async def test_split_chunk_deletion_removes_memory(self) -> None:
        await self.save(60)
        self.runtime.chat_message_groups.clear()
        await self.delete(62)
        self.assertEqual(self.contents(), [])

    # 등록되지 않은 채널에서 지워도 기억이 사라지는지 확인한다.
    async def test_unregistered_channel_deletion_still_removes_memory(self) -> None:
        await self.save(40)
        await self.delete(40, channel_id=UNREGISTERED_CHANNEL, channels={})
        self.assertEqual(self.contents(), [])

    # 스레드를 지우면 그 스레드의 기억이 사라지는지 확인한다.
    async def test_thread_deletion_removes_memories(self) -> None:
        self.runtime.memory.upsert_archive(GUILD, 501, FORUM, THREAD, "user7", "스레드 글")
        self.runtime.memory.add(GUILD, memory_store.KIND_EXPLICIT, "스레드 요청", channel_id=THREAD, sources=[(502, THREAD)])
        await bot.AgentBot.on_raw_thread_delete(self.make_client(self.runtime), SimpleNamespace(thread_id=THREAD))
        self.assertEqual(self.contents(), [])

    # 요약 대상 줄이 메시지 삭제로 지워지는지 확인한다.
    async def test_summary_target_line_deleted_by_remove_message(self) -> None:
        add_line(self.runtime, 90, 3, "user7", "오래된 요약 대상 줄", 90)
        self.runtime.chat_summaries[CHANNEL] = "기존 요약"
        await self.delete(90)
        self.assertNotIn(90, [entry[4] for entry in self.runtime.chat_history_for(CHANNEL)])
        self.assertEqual(self.runtime.chat_summaries[CHANNEL], "기존 요약")

    # 시작 점검이 NotFound인 원본의 기억만 지우는지 확인한다.
    async def test_startup_check_deletes_only_not_found(self) -> None:
        store = self.runtime.memory
        store.add(GUILD, memory_store.KIND_EXPLICIT, "원본 없음", sources=[(70, CHANNEL)])
        store.add(GUILD, memory_store.KIND_PERSONA_EVENT, "권한 오류", agent="jelly", sources=[(71, CHANNEL)])
        store.add(GUILD, memory_store.KIND_EXPLICIT, "채널 없음", sources=[(72, 12345)])
        self.missing = {70}
        self.forbidden = {71}
        await memory_module.verify_memory_sources(self.make_client(self.runtime))
        self.assertEqual(self.contents(), [])
        self.assertEqual(self.persona_contents(), ["권한 오류"])


class PersonaExtractionTests(MemoryTestBase):
    # C4: 요약 호출의 가짜 persona_events 출력 저장, 재시도·포기, 잘못된 항목 거부, 저장 직전 재확인.
    def setUp(self) -> None:
        super().setUp()
        add_line(self.runtime, 1, 3, "user7", "젤리야 떡볶이 먹으러 가자", 11)
        add_line(self.runtime, 2, 3, "jelly", "캬~ 떡볶이 좋슴다", 12)
        add_line(self.runtime, 3, 0.1, "user7", "최근 대화", 15)

    # 요약 출력 dict를 만든다.
    def output(self, *events):
        return {"summary": "떡볶이 약속", "persona_events": list(events)}

    # 캐릭터 사건 dict를 만든다.
    def event(self, agent="jelly", source=("12",), content="사용자와 떡볶이를 먹었다", kind="event"):
        return {"agent": agent, "kind": kind, "content": content, "source_message_ids": list(source)}

    # 올바른 캐릭터 사건은 저장하고 잘못된 사건은 거부하는지 확인한다.
    async def test_valid_events_saved_and_invalid_rejected(self) -> None:
        await self.run_summary([self.output(
            self.event(),
            self.event(kind="setting", content="떡볶이는 매운 맛을 고른다"),
            self.event(agent="stranger", content="로스터 밖"),
            self.event(source=("15",), content="묶음 밖"),
            self.event(source=(), content="출처 없음"),
            self.event(source=("abc",), content="잘못된 출처"),
        )])
        self.assertEqual(self.persona_contents(), ["떡볶이는 매운 맛을 고른다", "사용자와 떡볶이를 먹었다"])

    # 실패 뒤 재시도로 성공하는지 확인한다.
    async def test_retry_then_success(self) -> None:
        await self.run_summary([RuntimeError("1"), {"summary": ""}, self.output(self.event())])
        self.assertEqual(self.persona_contents(), ["사용자와 떡볶이를 먹었다"])
        self.assertEqual(self.runtime.chat_summaries[CHANNEL], "떡볶이 약속")

    # 최대 시도 횟수 뒤에 포기하는지 확인한다.
    async def test_abandon_after_max_attempts(self) -> None:
        self.runtime.chat_summaries[CHANNEL] = "이전 요약"
        with self.assertLogs("agent_team", logging.WARNING) as captured:
            await self.run_summary([RuntimeError("1"), RuntimeError("2"), RuntimeError("3")])
        self.assertTrue(any("summary_not_updated" in line for line in captured.output))
        self.assertEqual(self.persona_contents(), [])
        self.assertEqual(self.runtime.chat_summaries[CHANNEL], "이전 요약")
        self.assertEqual([entry[4] for entry in self.runtime.chat_history_for(CHANNEL)], [15])

    # 저장 전에 원본이 지워지면 저장하지 않는지 확인한다.
    async def test_source_deleted_before_save_is_not_stored(self) -> None:
        # 질의 도중 원본 메시지를 삭제 처리한다.
        def delete_during_call():
            bot.handle_deleted_messages(self.runtime, {12})
            with patch.object(bot, "save_chat_state"):
                self.runtime.remove_message(CHANNEL, 12)

        # 요약 중 원본이 삭제되면 저장하지 않는지 확인한다.
        await self.run_summary([self.output(self.event())], before=delete_during_call)
        self.assertEqual(self.persona_contents(), [])


class AttachmentTests(MemoryTestBase):
    # C5: 단건·bulk·미등록 채널 삭제, 저장 직후 삭제 경합, 시작 시 정리.
    def touch(self, message_id, age_hours=0.0) -> Path:
        path = self.attach_dir / f"{message_id}_1_file.png"
        path.write_bytes(b"x")
        if age_hours:
            old = time.time() - age_hours * 3600
            os.utime(path, (old, old))
        return path

    # 단일 삭제, 일괄 삭제, 미등록 채널 삭제가 첨부 파일을 지우는지 확인한다.
    async def test_single_bulk_and_unregistered_deletes_remove_files(self) -> None:
        single, bulk_a, bulk_b, unregistered = (self.touch(i) for i in (1, 2, 3, 4))
        client = self.make_client(self.runtime)
        await bot.AgentBot.on_raw_message_delete(client, SimpleNamespace(channel_id=CHANNEL, message_id=1))
        await bot.AgentBot.on_raw_bulk_message_delete(
            client, SimpleNamespace(channel_id=CHANNEL, message_ids={2, 3}),
        )
        await bot.AgentBot.on_raw_message_delete(
            self.make_client(self.runtime, channels={}),
            SimpleNamespace(channel_id=UNREGISTERED_CHANNEL, message_id=4),
        )
        for path in (single, bulk_a, bulk_b, unregistered):
            self.assertFalse(path.exists())

    # 삭제 뒤에 저장된 첨부 파일이 제거되는지 확인한다.
    def test_saved_after_delete_is_removed(self) -> None:
        self.runtime.record_deleted({5})
        path = self.touch(5)
        self.assertFalse(bot.register_saved_attachments(self.runtime, 5, CHANNEL))
        self.assertFalse(path.exists())
        kept = self.touch(6)
        self.assertTrue(bot.register_saved_attachments(self.runtime, 6, CHANNEL))
        self.assertTrue(kept.exists())
        self.assertIn((6, CHANNEL), self.runtime.memory.attachment_sources())

    # 시작 시 만료, 누락, 차단된 첨부 파일을 정리하는지 확인한다.
    async def test_startup_cleanup(self) -> None:
        expired = self.touch(7, age_hours=13)
        missing = self.touch(8)
        blocked = self.touch(9)
        for message_id in (7, 8, 9):
            self.runtime.memory.add_attachment_source(message_id, CHANNEL)
        self.missing = {8}
        self.forbidden = {9}
        await memory_module.verify_memory_sources(self.make_client(self.runtime))
        self.assertFalse(expired.exists())
        self.assertFalse(missing.exists())
        self.assertTrue(blocked.exists())
        self.assertEqual(self.runtime.memory.attachment_sources(), [(9, CHANNEL)])


class ToolAndPromptTests(MemoryTestBase):
    # C6: 6역할 도구, 사용자 턴 한정 memory_save, 주입 한도, 자리표시자 오염 방지.
    async def test_all_roles_get_memory_tools(self) -> None:
        self.runtime.memory.add(GUILD, memory_store.KIND_EXPLICIT, "공용 기억 떡볶이", sources=[(1, CHANNEL)])
        for agent in AGENTS:
            with self.subTest(role=agent.role):
                _, options = await self.run_turn(agent)
                self.assertIn("mcp__memory__memory_search", options["allowed_tools"])
                self.assertIn("mcp__memory__persona_memory_list", options["allowed_tools"])
                self.assertFalse([name for name in options["allowed_tools"] if "upload" in name])
                self.assertIn("memory", options["mcp_servers"])
                tools, _ = build_tools(self.runtime, self.channels[CHANNEL], agent, None)
                self.assertIn("공용 기억", tool_output(await tools["memory_search"]({"query": "떡볶이"})))
                self.assertIn("없습니다", tool_output(await tools["persona_memory_list"]({})))

    # 서버나 저장소가 없으면 기억 도구가 없는지 확인한다.
    def test_no_guild_or_store_means_no_tools(self) -> None:
        channel = SimpleNamespace(id=CHANNEL, guild=SimpleNamespace(name="guild"))
        self.assertIsNone(bot.build_memory_server(channel, self.runtime, JELLY))
        self.assertIsNone(bot.build_memory_server(self.channels[CHANNEL], SimpleNamespace(memory=None), JELLY))

    # 기억 저장이 사용자 턴과 알려진 메시지 id에서만 되는지 확인한다.
    async def test_memory_save_only_in_user_turn_and_known_ids(self) -> None:
        with patch.object(bot, "save_chat_state"):
            self.runtime.append_chat(CHANNEL, "user7", "기억해 줘", 50)
        auto_tools, _ = build_tools(self.runtime, self.channels[CHANNEL], JELLY, None)
        self.assertIn("사용자 메시지", tool_output(await auto_tools["memory_save"]({"content": "x", "scope": "guild"})))
        tools, _ = build_tools(self.runtime, self.channels[CHANNEL], JELLY, 50)
        self.assertIn("메시지 ID가 아닙니다", tool_output(await tools["memory_save"]({
            "content": "x", "scope": "guild", "target_message_id": "424242",
        })))
        self.assertIn("scope", tool_output(await tools["memory_save"]({"content": "x", "scope": "stranger"})))
        self.assertEqual(self.contents(), [])
        await tools["memory_save"]({"content": "공용", "scope": "guild"})
        await tools["memory_save"]({"content": "자기 설정", "scope": "self"})
        await tools["memory_save"]({"content": "리오 설정", "scope": "rio"})
        self.assertEqual(self.contents(), ["공용"])
        self.assertEqual(self.persona_contents("jelly"), ["자기 설정"])
        self.assertEqual(self.persona_contents("rio"), ["리오 설정"])

    # 프롬프트에 넣는 기억의 양 제한을 확인한다.
    def test_injection_limits(self) -> None:
        for index in range(10):
            self.runtime.memory.add(GUILD, memory_store.KIND_EXPLICIT, f"떡볶이 {index} " + "가" * 500,
                                    sources=[(index + 1, CHANNEL)])
        settings = config_module.MemoryConfig()
        text = bot.render_prompt_memories(self.runtime, GUILD, "떡볶이")
        self.assertLessEqual(text.count("- ("), settings.prompt_max_items)
        self.assertLessEqual(len(text.replace("\n", "")), settings.prompt_max_chars)
        for index in range(5):
            self.runtime.memory.add(GUILD, memory_store.KIND_PERSONA_SETTING, "설" * 400, agent="jelly",
                                    sources=[(100 + index, CHANNEL)])
        block = bot.render_persona_settings_block(self.runtime, GUILD, "jelly")
        self.assertLessEqual(block.count("설"), settings.persona_setting_max_chars)

    # 프롬프트의 기억 줄에 작성자가 표시되는지 확인한다.
    def test_prompt_memory_line_shows_author(self) -> None:
        self.runtime.memory.upsert_archive(GUILD, 701, FORUM, THREAD, "jelly", "떡볶이 작성자 있는 글")
        self.runtime.memory.upsert_archive(GUILD, 702, FORUM, THREAD, "", "떡볶이 작성자 없는 글")
        text = bot.render_prompt_memories(self.runtime, GUILD, "떡볶이")
        lines = {line.split(") ", 1)[1]: line for line in text.splitlines() if line.startswith("- (")}
        self.assertIn("작성 jelly)", lines["떡볶이 작성자 있는 글"])
        self.assertIn("작성 미상)", lines["떡볶이 작성자 없는 글"])

    # 치환된 값 안의 자리표시자를 다시 치환하지 않는지 확인한다.
    async def test_placeholders_are_not_reexpanded(self) -> None:
        self.assertEqual(
            bot.fill_template("A $memory B $history", {"$memory": "$history", "$history": "H"}),
            "A $history B H",
        )
        self.runtime.memory.add(GUILD, memory_store.KIND_EXPLICIT, "떡볶이 $turn_index $history",
                                sources=[(1, CHANNEL)])
        self.runtime.memory.add(GUILD, memory_store.KIND_PERSONA_SETTING, "젤리 설정 $self_name",
                                agent="jelly", sources=[(2, CHANNEL)])
        with patch.object(bot, "save_chat_state"):
            self.runtime.append_chat(CHANNEL, "user7", "떡볶이 이야기", 50)
        self.runtime.chat_summaries[CHANNEL] = "요약 $memory"
        prompt, options = await self.run_turn(JELLY)
        self.assertIn("떡볶이 $turn_index $history", prompt)
        self.assertIn("이전 대화 요약: 요약 $memory", prompt)
        self.assertEqual(prompt.count("[user7] (메시지 ID 50) 떡볶이 이야기"), 1)
        self.assertIn("캐릭터 기억: (없음)", prompt)
        append = options["system_prompt"]["append"]
        self.assertTrue(append.startswith("SYSTEM"))
        self.assertIn("젤리 설정 $self_name", append)

    # 모든 역할의 실행 프롬프트에 기억 블록이 있는지 확인한다.
    def test_runtime_prompt_has_memory_block_for_every_role(self) -> None:
        project_root = DISCORD_ROOT.parent
        for agent in AGENTS:
            with self.subTest(role=agent.role):
                prompt = bot.build_request_system_prompt(
                    root=project_root, role=agent.role, self_name=agent.name, self_korean_name=agent.korean_name,
                    agents=[agent], archive_repository=None,
                    tools_config=config_module.ToolsConfig(channel_history_max=1, server_channels_max=1),
                    reminders_config=config_module.RemindersConfig(9, 1, 60, 1, 1),
                )
                self.assertIn(prompts_module.MEMORY_BLOCK, prompt)
                self.assertNotIn("$memory_block", prompt)


class UploadTests(MemoryTestBase):
    # C8: GitHub API를 가짜로 대체한 `승인`·`취소` 메시지 처리, PR 생성, 삭제 갱신 표시, archive 충돌.
    def setUp(self) -> None:
        # 기억 한 건과 GitHub 가짜 호출을 준비한다.
        super().setUp()
        self.memory_id = self.runtime.memory.add(
            GUILD, memory_store.KIND_PERSONA_EVENT, "젤리가 떡볶이를 먹었다", agent="jelly", sources=[(80, CHANNEL)],
        )
        self.github = {
            "github_get_file": AsyncMock(return_value=(None, None)),
            "github_branch_sha": AsyncMock(return_value="base-sha"),
            "github_create_branch": AsyncMock(),
            "github_put_file": AsyncMock(),
            "github_create_pr": AsyncMock(return_value="https://example.com/pr/1"),
        }
        for name, mock in self.github.items():
            patcher = patch.object(memory_module, name, mock)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(bot.aiohttp, "ClientSession", FakeSession)
        patcher.start()
        self.addCleanup(patcher.stop)

        # 승인 상태와 에이전트 가짜 객체를 준비한다.
        self.approvals = self.runtime.memory_approvals
        self.jelly = SimpleNamespace(agent=JELLY, runtime=self.runtime)
        self.rio = SimpleNamespace(agent=RIO, runtime=self.runtime)
        self.pepper = SimpleNamespace(
            agent=next(agent for agent in AGENTS if agent.role == "documenter"), runtime=self.runtime,
        )

    # 캐릭터 기억 목록 도구를 호출하고 결과 문장을 반환한다.
    async def list_memories(self, trigger=1, requester=USER, channel_id=CHANNEL) -> str:
        tools, _ = build_tools(self.runtime, self.channels[channel_id], JELLY, trigger, requester)
        return tool_output(await tools["persona_memory_list"]({}))

    # 메시지를 보내 기억 승인 처리를 실행한다.
    async def send(self, message_id, text, *, client=None, author=USER, channel_id=CHANNEL) -> bool:
        message = make_message(message_id, self.channels[channel_id], author_id=author, content=text)
        return await bot.AgentBot._handle_memory_approval(client or self.jelly, message)

    # PR 생성 호출 횟수를 반환한다.
    def pr_count(self) -> int:
        return self.github["github_create_pr"].await_count

    # 저장소 작업 승인 대기가 있는 archive thread를 만든다.
    def stage_archive_pending(self):
        workflow_module = importlib.import_module("archive_workflow")
        store = workflow_module.ArchiveWorkflowStore(Path(self.tmp.name) / "archive_workflow.json")
        pending = workflow_module.Pending("issue", "pending-1", THREAD, USER, {})
        store.thread(THREAD).pending = pending
        self.runtime.archive_workflow = store
        return pending

    # 사용자 턴이 아니면 업로드 제안을 하지 않는지 확인한다.
    async def test_u1_no_offer_outside_user_turn(self) -> None:
        output = await self.list_memories(trigger=None)
        self.assertIn("사용자 메시지에 답하는 턴이 아니라", output)
        self.assertIsNone(self.approvals.active(CHANNEL))
        await self.list_memories(trigger=1, requester=None)
        self.assertIsNone(self.approvals.active(CHANNEL))
        self.assertFalse(await self.send(1, "승인"))
        self.assertEqual(self.pr_count(), 0)

    # 다른 메시지나 다른 사용자의 승인은 아무것도 하지 않는지 확인한다.
    async def test_u2_other_message_or_user_does_nothing(self) -> None:
        output = await self.list_memories()
        self.assertIn("`승인`", output)
        self.assertFalse(await self.send(1, "좋아요 올려 주세요"))
        self.assertFalse(await self.send(2, "승인", author=OTHER_USER))
        self.assertFalse(await self.send(3, "취소", author=OTHER_USER))
        self.assertEqual(self.pr_count(), 0)
        self.channels[CHANNEL].send.assert_not_awaited()
        self.assertIsNotNone(self.approvals.active(CHANNEL))
        self.assertFalse(self.runtime.memory.get(self.memory_id)["uploaded"])

    # 요청자의 승인이 PR을 하나 만드는지 확인한다.
    async def test_u3_requester_approval_creates_one_pr(self) -> None:
        await self.list_memories()
        self.assertTrue(await self.send(1, "승인"))
        self.assertEqual(self.pr_count(), 1)
        branch = self.github["github_create_branch"].call_args.args[2]
        self.assertTrue(branch.startswith("memory/worker-"))
        put_args = self.github["github_put_file"].call_args.args
        self.assertEqual(put_args[2], ".claude/agents/worker/MEMORY.md")
        self.assertIn("젤리가 떡볶이를 먹었다", put_args[3])
        self.assertEqual(self.github["github_create_pr"].call_args.args[3], bot.BASE_BRANCH)
        self.assertTrue(self.runtime.memory.get(self.memory_id)["uploaded"])
        self.channels[CHANNEL].send.assert_awaited_once_with(
            bot.MEMORY_APPROVAL_DONE.format(url="https://example.com/pr/1")
        )
        self.assertEqual(self.approvals.approvals[CHANNEL].status, "done")

    # 기억 명령이 정확히 일치하는 메시지 내용만 인정하는지 확인한다.
    async def test_memory_commands_require_exact_message_content(self) -> None:
        await self.list_memories()
        approval = self.approvals.active(CHANNEL)
        for command in ("승인", "취소"):
            for text in (f"<@999> {command}", f" {command}", f"{command} ", f"{command}\n"):
                with self.subTest(text=text):
                    self.assertFalse(await self.send(1, text))
                    self.assertIs(self.approvals.active(CHANNEL), approval)
                    self.assertEqual(approval.status, "pending")
                    self.assertEqual(self.pr_count(), 0)
        self.assertEqual(self.runtime.memory_approval_handled, set())
        self.channels[CHANNEL].send.assert_not_awaited()
        self.assertFalse(self.runtime.memory.get(self.memory_id)["uploaded"])

    # 승인을 반복해도 중복 처리가 없는지 확인한다.
    async def test_u4_repeated_approval_has_no_duplicate(self) -> None:
        await self.list_memories()
        self.assertTrue(await self.send(1, "승인"))
        self.assertTrue(await self.send(1, "승인", client=self.rio))
        self.assertFalse(await self.send(2, "승인"))
        self.assertEqual(self.pr_count(), 1)

    # 취소가 제안을 지우는지 확인한다.
    async def test_u5_cancel_removes_offer(self) -> None:
        await self.list_memories()
        self.assertTrue(await self.send(1, "취소"))
        self.channels[CHANNEL].send.assert_awaited_once_with(bot.MEMORY_APPROVAL_CANCELLED)
        self.assertIsNone(self.approvals.active(CHANNEL))
        self.assertNotIn(CHANNEL, self.approvals.approvals)
        self.assertFalse(await self.send(2, "승인"))
        self.assertEqual(self.pr_count(), 0)

    # 삭제가 동기화 표시를 남기고 다음 승인에 반영되는지 확인한다.
    async def test_u6_delete_marks_sync_and_next_approval_applies(self) -> None:
        # 목록을 만들고 승인한 뒤 삭제 반영 표시를 확인한다.
        await self.list_memories()
        await self.send(1, "승인")
        self.assertEqual(self.pr_count(), 1)
        bot.handle_deleted_messages(self.runtime, {80})
        await self.settle()
        self.assertEqual(self.pr_count(), 1)
        self.assertEqual(self.approvals.sync_needed, {"jelly"})
        self.assertIsNone(self.runtime.memory.get(self.memory_id))
        saved = json.loads(self.approvals.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["sync_needed"], ["jelly"])

        # 다음 승인에 삭제 반영이 적용되는지 확인한다.
        output = await self.list_memories(trigger=2)
        self.assertIn("원본 삭제 반영 포함", output)
        self.assertTrue(self.approvals.active(CHANNEL).sync)
        self.assertTrue(await self.send(3, "승인"))
        self.assertEqual(self.pr_count(), 2)
        self.assertNotIn("떡볶이", self.github["github_put_file"].call_args.args[3])
        self.assertEqual(self.approvals.sync_needed, set())

    # 다른 봇은 승인 메시지를 소비만 하는지 확인한다.
    async def test_u7_other_bot_only_consumes(self) -> None:
        await self.list_memories()
        self.assertTrue(await self.send(1, "승인", client=self.rio))
        self.assertEqual(self.pr_count(), 0)
        self.channels[CHANNEL].send.assert_not_awaited()
        self.assertEqual(self.approvals.active(CHANNEL).status, "pending")
        self.assertTrue(await self.send(1, "승인"))
        self.assertEqual(self.pr_count(), 1)

    # 다시 불러올 때 실행 중 상태가 실패로 바뀌는지 확인한다.
    def test_u8_reload_turns_executing_into_failed(self) -> None:
        path = Path(self.tmp.name) / "reload.json"
        store = bot.MemoryApprovalStore(path)
        store.offer(CHANNEL, "jelly", USER, [self.memory_id])
        store.mark_sync_needed({"rio"})
        store.approvals[CHANNEL].status = "executing"
        store.save()
        loaded = bot.MemoryApprovalStore.load(path)
        self.assertEqual(loaded.approvals[CHANNEL].status, "failed")
        self.assertEqual(loaded.approvals[CHANNEL].memory_ids, [self.memory_id])
        self.assertEqual(loaded.sync_needed, {"rio"})
        self.assertIsNone(loaded.active(CHANNEL))
        self.assertFalse(path.with_suffix(".tmp").exists())

    # 아카이브 충돌 상태에서 승인하면 아무것도 실행하지 않는지 확인한다.
    async def test_u9_archive_conflict_approval_runs_nothing(self) -> None:
        # 저장소 기억 승인을 준비하고 충돌 응답을 확인한다.
        await self.list_memories(channel_id=THREAD)
        archive_pending = self.stage_archive_pending()
        self.assertIsNotNone(self.approvals.active(THREAD))
        message = make_message(1, self.channels[THREAD], content="승인")
        self.assertTrue(await bot.AgentBot._handle_approval_conflict(self.jelly, message))
        self.assertTrue(await bot.AgentBot._handle_approval_conflict(self.pepper, message))
        self.assertTrue(await bot.AgentBot._handle_approval_conflict(self.jelly, message))
        self.channels[THREAD].send.assert_awaited_once_with(bot.APPROVAL_CONFLICT_APPROVE)
        self.assertEqual(self.pr_count(), 0)
        self.assertEqual(archive_pending.status, "pending")
        self.assertEqual(self.approvals.active(THREAD).status, "pending")

        # 같은 요청자의 저장소 작업 대기가 있으면 새 업로드 대기를 만들지 않는다.
        self.approvals.cancel(THREAD)
        output = await self.list_memories(trigger=2, channel_id=THREAD)
        self.assertIn("저장소 작업 승인 대기가 있어", output)
        self.assertIsNone(self.approvals.active(THREAD))

    # 아카이브 충돌 상태에서 취소하면 둘 다 취소되는지 확인한다.
    async def test_u10_archive_conflict_cancel_cancels_both(self) -> None:
        await self.list_memories(channel_id=THREAD)
        archive_pending = self.stage_archive_pending()
        message = make_message(1, self.channels[THREAD], content="취소")
        self.assertTrue(await bot.AgentBot._handle_approval_conflict(self.pepper, message))
        self.assertTrue(await bot.AgentBot._handle_approval_conflict(self.jelly, message))
        self.channels[THREAD].send.assert_awaited_once_with(bot.APPROVAL_CONFLICT_CANCELLED)
        self.assertEqual(archive_pending.status, "cancelled")
        self.assertIsNone(self.approvals.active(THREAD))
        self.assertEqual(self.pr_count(), 0)

    # 아카이브 명령이 기억 전용 승인을 통과시키는지 확인한다.
    async def test_archive_command_passes_memory_only_approval(self) -> None:
        await self.list_memories(channel_id=THREAD)
        self.runtime.archive_workflow = importlib.import_module("archive_workflow").ArchiveWorkflowStore(
            Path(self.tmp.name) / "archive_workflow.json"
        )
        message = make_message(1, self.channels[THREAD], content="승인")
        self.assertFalse(await bot.AgentBot._handle_approval_conflict(self.pepper, message))
        self.assertFalse(await bot.AgentBot._handle_archive_command(self.pepper, message))
        self.assertTrue(await bot.AgentBot._handle_memory_approval(self.pepper, message))
        self.assertEqual(self.pr_count(), 0)
        self.assertTrue(await bot.AgentBot._handle_memory_approval(self.jelly, message))
        self.assertEqual(self.pr_count(), 1)

    # 기억 전용 아카이브 스레드가 정확하지 않은 명령을 거부하는지 확인한다.
    async def test_memory_only_archive_thread_rejects_non_exact_commands(self) -> None:
        await self.list_memories(channel_id=THREAD)
        self.runtime.archive_workflow = importlib.import_module("archive_workflow").ArchiveWorkflowStore(
            Path(self.tmp.name) / "archive_workflow.json"
        )
        for command in ("승인", "취소"):
            for text in (f"<@999> {command}", f" {command}", f"{command} ", f"{command}\n"):
                with self.subTest(text=text):
                    message = make_message(1, self.channels[THREAD], content=text)
                    self.assertFalse(await bot.AgentBot._handle_archive_command(self.pepper, message))
                    self.assertFalse(await bot.AgentBot._handle_memory_approval(self.jelly, message))
                    self.assertEqual(self.approvals.active(THREAD).status, "pending")
        self.channels[THREAD].send.assert_not_awaited()
        self.assertEqual(self.pr_count(), 0)

    # 충돌 상태의 명령이 정확히 일치하는 내용만 인정하는지 확인한다.
    async def test_conflicting_commands_require_exact_message_content(self) -> None:
        await self.list_memories(channel_id=THREAD)
        archive_pending = self.stage_archive_pending()
        approval = self.approvals.active(THREAD)
        for command in ("승인", "취소"):
            for text in (f"<@999> {command}", f" {command}", f"{command} ", f"{command}\n"):
                with self.subTest(text=text):
                    message = make_message(1, self.channels[THREAD], content=text)
                    self.assertFalse(await bot.AgentBot._handle_approval_conflict(self.pepper, message))
                    self.assertFalse(await bot.AgentBot._handle_approval_conflict(self.jelly, message))
                    self.assertFalse(await bot.AgentBot._handle_archive_command(self.pepper, message))
                    self.assertFalse(await bot.AgentBot._handle_memory_approval(self.jelly, message))
                    self.assertEqual(archive_pending.status, "pending")
                    self.assertIs(self.approvals.active(THREAD), approval)
                    self.assertEqual(approval.status, "pending")
        self.assertEqual(self.runtime.memory_approval_handled, set())
        self.channels[THREAD].send.assert_not_awaited()
        self.assertEqual(self.pr_count(), 0)

    # 아카이브 단독 취소가 명령 정규화를 유지하는지 확인한다.
    async def test_archive_only_cancel_keeps_command_normalization(self) -> None:
        for text in ("<@999> 취소", " 취소", "취소 ", "취소\n"):
            with self.subTest(text=text):
                archive_pending = self.stage_archive_pending()
                message = make_message(1, self.channels[THREAD], content=text)
                self.assertFalse(await bot.AgentBot._handle_approval_conflict(self.pepper, message))
                self.assertTrue(await bot.AgentBot._handle_archive_command(self.pepper, message))
                self.assertEqual(archive_pending.status, "cancelled")
                self.assertIsNone(self.approvals.active(THREAD))
        self.assertEqual(self.pr_count(), 0)

    # 아카이브 단독 승인이 명령 정규화를 유지하는지 확인한다.
    async def test_archive_only_approval_keeps_command_normalization(self) -> None:
        with patch.object(bot, "github_apply_change_set", AsyncMock(return_value="commit-sha")) as apply:
            for text in ("<@999> 승인", " 승인", "승인 ", "승인\n"):
                with self.subTest(text=text):
                    archive_pending = self.stage_archive_pending()
                    archive_pending.kind = "change"
                    archive_pending.data = {
                        "branch": "work", "source_commit_sha": "base-sha",
                        "commit_message": "변경 적용", "operations": [],
                    }
                    message = make_message(1, self.channels[THREAD], content=text)
                    self.assertFalse(await bot.AgentBot._handle_approval_conflict(self.pepper, message))
                    self.assertTrue(await bot.AgentBot._handle_archive_command(self.pepper, message))
                    apply.assert_awaited_once()
                    self.assertEqual(archive_pending.status, "applied")
                    self.assertIsNone(self.approvals.active(THREAD))
                    apply.reset_mock()
        self.assertEqual(self.pr_count(), 0)

    # PR 생성이 실패하면 실패로 표시하는지 확인한다.
    async def test_u11_failure_marks_failed(self) -> None:
        self.github["github_create_pr"].side_effect = bot.aiohttp.ClientError("network down")
        await self.list_memories()
        self.assertTrue(await self.send(1, "승인"))
        self.assertEqual(self.approvals.approvals[CHANNEL].status, "failed")
        self.assertIsNone(self.approvals.active(CHANNEL))
        self.assertFalse(self.runtime.memory.get(self.memory_id)["uploaded"])
        self.assertIn("network down", self.channels[CHANNEL].send.await_args.args[0])
        saved = json.loads(self.approvals.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["approvals"][str(CHANNEL)]["status"], "failed")


class SummaryTests(MemoryTestBase):
    # C11: 대상 선정, 공백 없음, 실패 재시도·포기, 도구 줄 제외, 재시작 후 요약 유지.
    def test_target_selection(self) -> None:
        runtime = self.make_runtime(chat=chat_config(history_max_lines=4))
        add_line(runtime, 1, 3, "user7", "오래된 줄 1", 1)
        add_line(runtime, 2, 3, "user7", "오래된 줄 2", 2)
        for seq in range(3, 7):
            add_line(runtime, seq, 0.1, "user7", f"최근 줄 {seq}", seq)
        self.assertEqual(runtime.summary_target_count(CHANNEL), 2)
        add_line(runtime, 7, 0.1, "user7", "넘친 줄", 7)
        self.assertEqual(runtime.summary_target_count(CHANNEL), 3)

    # 요약 예약 조건과 동시에 한 작업만 도는지 확인한다.
    async def test_schedule_conditions_and_single_task(self) -> None:
        # 오래된 대화 줄과 차단 이벤트를 준비한다.
        add_line(self.runtime, 1, 3, "user7", "오래된 줄", 1)
        gate = asyncio.Event()

        # 신호가 올 때까지 기다리는 가짜 요약 작업이다.
        async def blocked(runtime, channel_id):
            await gate.wait()

        # 조건에 따라 요약이 하나만 예약되는지 확인한다.
        with patch.object(bot, "run_channel_summary", blocked):
            self.assertFalse(self.runtime.maybe_schedule_summary(CHANNEL))
            self.runtime.summary_due_since[CHANNEL] = time.time() - 61 * 60
            self.assertTrue(self.runtime.maybe_schedule_summary(CHANNEL))
            self.assertFalse(self.runtime.maybe_schedule_summary(CHANNEL))
            gate.set()
            await self.runtime.summary_tasks[CHANNEL]
        runtime = self.make_runtime()
        for seq in range(1, 31):
            add_line(runtime, seq, 3, "user7", f"줄 {seq}", seq)
        with patch.object(bot, "run_channel_summary", AsyncMock()) as run:
            self.assertTrue(runtime.maybe_schedule_summary(CHANNEL))
            await runtime.summary_tasks[CHANNEL]
        run.assert_awaited_once()

    # 요약 대상이 성공 전까지 남고 도구 줄이 제외되는지 확인한다.
    async def test_targets_stay_until_success_and_tool_lines_excluded(self) -> None:
        add_line(self.runtime, 1, 3, "user7", "오래된 사용자 줄", 11)
        add_line(self.runtime, 2, 3, "jelly", bot.EVIDENCE_HEADER + "\n- 사용 WebSearch: https://old.example")
        add_line(self.runtime, 3, 0.1, "jelly", bot.EVIDENCE_HEADER + "\n- 사용 WebSearch: https://new.example")
        add_line(self.runtime, 4, 0.1, "user7", "최근 줄", 15)
        rendered = self.runtime.render_chat_history(CHANNEL)
        self.assertIn("오래된 사용자 줄", rendered)
        self.assertNotIn("old.example", rendered)
        self.assertIn("new.example", rendered)
        prompts: list[str] = []
        await self.run_summary([{"summary": "요" * 3000, "persona_events": []}], prompts)
        self.assertIn("[user:user7] (메시지 ID 11) 오래된 사용자 줄", prompts[0])
        self.assertNotIn("WebSearch", prompts[0])
        self.assertEqual(len(self.runtime.chat_summaries[CHANNEL]), config_module.SummaryConfig().max_chars)
        self.assertEqual([entry[0] for entry in self.runtime.chat_history_for(CHANNEL)], [3, 4])
        prompt, _ = await self.run_turn(JELLY)
        self.assertIn("이전 대화 요약: 요요", prompt)
        self.assertNotIn("오래된 사용자 줄", prompt)

    # 요약 시도 전에는 history_hours가 지난 줄도 지우지 않는지 확인한다.
    async def test_history_hours_waits_for_summary_attempts(self) -> None:
        # 장기 정지 뒤 재시작처럼 history_hours보다 오래된 줄도 요약 시도 전에는 지우지 않는다.
        add_line(self.runtime, 1, 13, "user7", "정지 전 줄", 11)
        add_line(self.runtime, 2, 0.1, "user7", "최근 줄", 15)
        self.assertFalse(self.runtime.prune_chat_history(CHANNEL))
        self.assertIn("정지 전 줄", self.runtime.render_chat_history(CHANNEL))
        self.assertEqual(self.runtime.summary_target_count(CHANNEL), 1)
        prompts: list[str] = []
        attempts = config_module.SummaryConfig().max_attempts
        with patch.object(self.runtime, "drop_history_entries", Mock(return_value=0)):
            await self.run_summary([RuntimeError("fail")] * attempts, prompts)
        self.assertEqual(len(prompts), attempts)
        self.assertIn("정지 전 줄", prompts[0])
        # 시도를 마친 줄은 남아 있어도 history_hours 상한으로 지운다.
        self.assertTrue(self.runtime.prune_chat_history(CHANNEL))
        self.assertEqual([entry[0] for entry in self.runtime.chat_history_for(CHANNEL)], [2])

    # 요약이 재시작 뒤에도 남는지 확인한다.
    async def test_summary_survives_restart(self) -> None:
        self.runtime.chat_summaries[CHANNEL] = "재시작 요약"
        self.runtime.append_chat(CHANNEL, "user7", "본문", 50)
        restarted = self.make_runtime()
        bot.load_chat_state(CHANNEL, restarted)
        self.assertEqual(restarted.chat_summaries[CHANNEL], "재시작 요약")
        self.assertEqual(list(restarted.chat_history_for(CHANNEL)), list(self.runtime.chat_history_for(CHANNEL)))


class WipeTests(MemoryTestBase):
    # C12: 전체 삭제의 확인·취소·만료·다른 사용자 무시, 대상 삭제, 장기기억 유지.
    def setUp(self) -> None:
        super().setUp()
        self.runtime.append_chat(CHANNEL, "user7", "지울 대화", 50)
        self.runtime.chat_summaries[CHANNEL] = "지울 요약"
        self.runtime.image_captions[50] = "지울 설명"
        self.runtime.memory.add(GUILD, memory_store.KIND_EXPLICIT, "남을 장기기억", sources=[(1, CHANNEL)])
        self.runtime.memory.add(GUILD, memory_store.KIND_PERSONA_EVENT, "남을 캐릭터 기억", agent="jelly",
                                sources=[(2, CHANNEL)])
        self.handler = SimpleNamespace(agent=SimpleNamespace(name="buddy"), runtime=self.runtime)
        self.other_bot = SimpleNamespace(agent=SimpleNamespace(name="rio"), runtime=self.runtime)
        self.channel = self.channels[CHANNEL]

    # 채팅 지우기 명령 메시지를 보내 처리 결과를 반환한다.
    async def command(self, message_id, text, *, client=None, author=USER, delay=0):
        message = make_message(
            message_id, self.channel, author_id=author, content=text,
            created=datetime.now(timezone.utc) + timedelta(seconds=delay),
        )
        return await bot.AgentBot._handle_chat_wipe_command(client or self.handler, message)

    # 채팅 기록과 요약이 그대로 남았는지 확인한다.
    def assert_kept(self) -> None:
        self.assertEqual(len(self.runtime.chat_history_for(CHANNEL)), 1)
        self.assertEqual(self.runtime.chat_summaries[CHANNEL], "지울 요약")

    # 확인하면 채널 기록을 지우되 기억은 남기는지 확인한다.
    async def test_confirm_wipes_channel_but_keeps_memories(self) -> None:
        self.assertTrue(await self.command(1, bot.CHAT_WIPE_COMMAND))
        self.assertTrue(await self.command(1, bot.CHAT_WIPE_COMMAND, client=self.other_bot))
        self.assertEqual(self.channel.send.await_count, 1)
        self.assertFalse(await self.command(2, bot.CHAT_WIPE_CONFIRM, author=OTHER_USER))
        self.assert_kept()
        self.assertTrue(await self.command(3, bot.CHAT_WIPE_CONFIRM))
        self.assertTrue(await self.command(3, bot.CHAT_WIPE_CONFIRM, client=self.other_bot))
        self.assertEqual(list(self.runtime.chat_history_for(CHANNEL)), [])
        self.assertNotIn(CHANNEL, self.runtime.chat_summaries)
        self.assertNotIn(50, self.runtime.image_captions)
        self.assertFalse(bot.chat_state_path(CHANNEL).exists())
        self.assertEqual(self.contents(), ["남을 장기기억"])
        self.assertEqual(self.persona_contents(), ["남을 캐릭터 기억"])
        self.channel.send.assert_awaited_with(bot.CHAT_WIPE_DONE)

    # 취소와 만료 시 기록이 남는지 확인한다.
    async def test_cancel_and_expiry(self) -> None:
        await self.command(1, bot.CHAT_WIPE_COMMAND)
        self.assertTrue(await self.command(2, bot.CHAT_WIPE_CANCEL))
        self.assertFalse(await self.command(3, bot.CHAT_WIPE_CONFIRM))
        self.assert_kept()
        await self.command(4, bot.CHAT_WIPE_COMMAND)
        self.assertFalse(await self.command(5, bot.CHAT_WIPE_CONFIRM, delay=bot.CHAT_WIPE_TIMEOUT_SECONDS + 1))
        self.assertNotIn(CHANNEL, self.runtime.chat_wipe_requests)
        self.assert_kept()

    # 봇 메시지를 명령으로 처리하지 않는지 확인한다.
    async def test_bot_messages_are_not_commands(self) -> None:
        message = make_message(1, self.channel, author_id=1002, bot_author=True, content=bot.CHAT_WIPE_COMMAND)
        self.assertFalse(await bot.AgentBot._handle_chat_wipe_command(self.handler, message))


class TokenLogTests(MemoryTestBase):
    # C13: 채팅 턴과 요약 호출의 사용량 로그 필드.
    async def test_chat_turn_logs_usage(self) -> None:
        with self.assertLogs("agent_team", logging.INFO) as captured:
            await self.run_turn(JELLY, usage={"input_tokens": 10, "output_tokens": 5})
        line = next(line for line in captured.output if "token usage" in line)
        self.assertIn("kind=chat", line)
        self.assertIn("agent=jelly", line)
        self.assertIn(f"channel={CHANNEL}", line)
        self.assertIn('"input_tokens": 10', line)
        self.assertIn("total_cost_usd=0.01", line)

    # 요약 호출이 토큰 사용량을 로그에 남기는지 확인한다.
    async def test_summary_call_logs_usage(self) -> None:
        add_line(self.runtime, 1, 3, "user7", "오래된 줄", 11)
        with self.assertLogs("agent_team", logging.INFO) as captured:
            await self.run_summary([{"summary": "요약", "persona_events": []}])
        line = next(line for line in captured.output if "token usage" in line)
        self.assertIn("kind=summary", line)
        self.assertIn("model=claude-haiku-4-5", line)
        self.assertIn('"output_tokens": 2', line)
        self.assertIn("total_cost_usd=0.002", line)


if __name__ == "__main__":
    unittest.main()
