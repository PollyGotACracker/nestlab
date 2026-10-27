"""director·history·server_channels 도구와 역할별 도구 목록을 구성한다."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import discord
from claude_agent_sdk import create_sdk_mcp_server, tool

from config import (
    DISCORD_LIMIT,
    POLL_ANSWERS_MAX,
    POLL_ANSWERS_MIN,
    POLL_ANSWER_MAX,
    POLL_HOURS_MAX,
    POLL_QUESTION_MAX,
    THREAD_NAME_MAX,
    AgentConfig,
    ArchiveRepositoryConfig,
    log,
    tool_text,
)
from reminders import REMINDER_TOOL_NAMES, build_reminder_server
from attachments import split_message

if TYPE_CHECKING:
    from bot import Runtime

FORUM_TAGS_MIN = 1
FORUM_TAGS_MAX = 5


# 도구 인자로 받은 메시지 ID를 정수로 바꾼다. Discord ID는 JavaScript 정수 범위를 넘어 문자열로 받는다.
def parse_message_id(value: Any) -> int | None:
    # 값을 양의 정수 메시지 ID로 바꾼다.
    try:
        message_id = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return message_id if message_id > 0 else None


# director 역할 전용 도구(투표, 스레드, 고정)를 만든다. discord.py 2.4 미만이면 Poll이 없어 투표만 뺀다.
def build_director_server(
    channel: discord.abc.Messageable, runtime: "Runtime", agent: AgentConfig
) -> tuple[str, list[str], Any] | None:
    # 도구 목록을 시작한다.
    tools = []

    # 현재 채널에 투표를 올린다.
    @tool(
        "poll_create",
        "채널에 투표를 올린다.",
        {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "answers": {"type": "array", "items": {"type": "string"}},
                "hours": {"type": "integer"},
                "multiple": {"type": "boolean"},
            },
            "required": ["question", "answers", "hours"],
        },
    )
    async def poll_create(args: dict[str, Any]) -> dict[str, Any]:
        # 투표 질문과 선택지를 검사하고 투표를 만든다.
        question = str(args.get("question", "")).strip()
        answers = [str(a).strip() for a in args.get("answers", []) if str(a).strip()]
        hours = args.get("hours")
        if not question or len(question) > POLL_QUESTION_MAX:
            return tool_text(f"질문은 1~{POLL_QUESTION_MAX}자여야 합니다.")
        if not POLL_ANSWERS_MIN <= len(answers) <= POLL_ANSWERS_MAX:
            return tool_text(f"선택지는 {POLL_ANSWERS_MIN}~{POLL_ANSWERS_MAX}개여야 합니다.")
        if any(len(a) > POLL_ANSWER_MAX for a in answers):
            return tool_text(f"선택지 하나는 {POLL_ANSWER_MAX}자 이하여야 합니다.")
        if not isinstance(hours, int) or not 1 <= hours <= POLL_HOURS_MAX:
            return tool_text(f"기간은 1~{POLL_HOURS_MAX}시간이어야 합니다.")
        poll = discord.Poll(
            question=question,
            duration=timedelta(hours=hours),
            multiple=bool(args.get("multiple", False)),
        )
        for answer in answers:
            poll.add_answer(text=answer)
        try:
            sent = await channel.send(poll=poll)
        except discord.HTTPException as exc:
            log.warning("poll create failed agent=%s: %s", agent.name, exc)
            return tool_text("투표를 올리지 못했습니다.")
        # 투표 메시지는 봇 발언과 별개로 올라가므로, 다른 캐릭터도 알 수 있게 대화 기록에 남긴다.
        runtime.append_chat(
            channel.id, agent.name,
            f"(투표) {question} / 선택지: {', '.join(answers)} / {hours}시간", sent.id,
        )
        return tool_text("투표를 올렸습니다.")

    # 도구 이름 목록을 시작하고 투표 도구를 등록한다.
    tool_names = []
    if hasattr(discord, "Poll"):
        tools.append(poll_create)
        tool_names.append("mcp__director__poll_create")

    # 현재 채널에 스레드를 만들고 첫 글을 올린다.
    @tool(
        "thread_create",
        "현재 채널에 공개 스레드를 만든다. message_id를 주면 그 메시지에서 시작한다. "
        "content를 주면 만든 스레드에 첫 글로 올린다.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "message_id": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["name"],
        },
    )
    async def thread_create(args: dict[str, Any]) -> dict[str, Any]:
        # 스레드 이름과 첫 글을 검사하고 스레드를 만든다.
        name = str(args.get("name", "")).strip()
        if not name or len(name) > THREAD_NAME_MAX:
            return tool_text(f"스레드 이름은 1~{THREAD_NAME_MAX}자여야 합니다.")
        content = str(args.get("content") or "").strip()
        if len(content) > DISCORD_LIMIT:
            return tool_text(f"첫 글은 {DISCORD_LIMIT}자 이하여야 합니다.")
        if isinstance(channel, discord.Thread):
            return tool_text("스레드 안에서는 스레드를 만들 수 없습니다.")
        raw_id = args.get("message_id")
        message_id = parse_message_id(raw_id) if raw_id not in (None, "") else None
        if raw_id not in (None, "") and message_id is None:
            return tool_text("message_id가 올바르지 않습니다.")
        try:
            if message_id is not None:
                thread = await channel.get_partial_message(message_id).create_thread(name=name)
            else:
                thread = await channel.create_thread(name=name, type=discord.ChannelType.public_thread)
        except discord.HTTPException as exc:
            log.warning("thread create failed agent=%s: %s", agent.name, exc)
            return tool_text(f"스레드를 만들지 못했습니다: {exc}")
        # 스레드 생성은 봇 발언과 별개라, 다른 캐릭터도 알 수 있게 현재 채널 기록에 남긴다.
        runtime.append_chat(channel.id, agent.name, f"(스레드 생성) {thread.name}")
        if not content:
            return tool_text(f"스레드를 만들었습니다: {thread.name}")
        try:
            sent = await thread.send(content)
        except discord.HTTPException as exc:
            log.warning("thread first message failed agent=%s: %s", agent.name, exc)
            return tool_text(f"스레드를 만들었지만 첫 글을 올리지 못했습니다: {exc}")
        # 스레드에서 이어지는 대화가 첫 글을 알도록 스레드 기록에 남긴다.
        runtime.append_chat(thread.id, agent.name, content, sent.id)
        return tool_text(f"스레드를 만들고 첫 글을 올렸습니다: {thread.name}")

    # 현재 채널의 메시지를 고정한다.
    @tool(
        "message_pin",
        "현재 채널의 메시지를 고정한다.",
        {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    )
    async def message_pin(args: dict[str, Any]) -> dict[str, Any]:
        # 메시지 id를 확인한다.
        message_id = parse_message_id(args.get("message_id"))
        if message_id is None:
            return tool_text("message_id가 올바르지 않습니다.")

        # 메시지를 고정한다. 실패하면 오류 문장을 반환한다.
        try:
            await channel.get_partial_message(message_id).pin()
        except discord.HTTPException as exc:
            log.warning("message pin failed agent=%s: %s", agent.name, exc)
            return tool_text(f"메시지를 고정하지 못했습니다: {exc}")
        return tool_text("메시지를 고정했습니다.")

    # 스레드와 고정 도구를 등록하고 서버를 반환한다.
    tools += [thread_create, message_pin]
    tool_names += ["mcp__director__thread_create", "mcp__director__message_pin"]
    return "director", tool_names, create_sdk_mcp_server(
        name="director", version="1.0.0", tools=tools
    )


# reviewer 역할 전용 채널 기록 조회 도구를 만든다. 봇이 저장한 기록이 아니라 Discord의 실제 메시지를 읽는다.
def build_history_server(
    channel: discord.abc.Messageable, runtime: "Runtime", agent: AgentConfig
) -> tuple[str, list[str], Any] | None:
    # 채널 기록 한도를 꺼낸다.
    history_max = runtime.config.tools.channel_history_max
    line_max_chars = runtime.config.chat.line_max_chars

    # 현재 채널의 최근 메시지를 읽는다.
    @tool(
        "channel_history",
        f"채널의 최근 메시지를 오래된 순으로 읽는다. limit은 1~{history_max}이다.",
        {"limit": int},
    )
    async def channel_history(args: dict[str, Any]) -> dict[str, Any]:
        # 조회 개수를 한도 안으로 정한다.
        try:
            limit = int(args.get("limit", history_max))
        except (TypeError, ValueError):
            limit = history_max
        limit = max(1, min(limit, history_max))

        # 채널 기록을 읽는다.
        id_to_name = runtime.bot_id_to_name()
        try:
            messages = [m async for m in channel.history(limit=limit)]
        except discord.HTTPException as exc:
            log.warning("channel history failed agent=%s: %s", agent.name, exc)
            return tool_text("채널 기록을 읽지 못했습니다.")

        # 오래된 메시지부터 화자와 본문을 한 줄로 만든다.
        lines = []
        for m in reversed(messages):
            name = id_to_name.get(m.author.id)
            speaker = runtime.chat_display_name(name) if name else m.author.display_name
            body = m.content.strip()[:line_max_chars] or "(텍스트 없음)"
            if m.attachments:
                body += f" (첨부 {len(m.attachments)}개)"
            lines.append(f"[{m.created_at:%Y-%m-%d %H:%M}] [{speaker}] {body}")
        return tool_text("\n".join(lines) or "메시지가 없습니다.")

    # 도구를 묶어 history 서버를 반환한다.
    return "history", ["mcp__history__channel_history"], create_sdk_mcp_server(
        name="history", version="1.0.0", tools=[channel_history]
    )


CHANNEL_TYPE_LABELS = {
    "text": "텍스트",
    "news": "공지",
    "forum": "포럼",
    "media": "미디어",
    "voice": "음성",
    "stage_voice": "스테이지",
}


# 모든 역할이 쓰는 서버 채널 목록 조회 도구를 만든다. 이 봇이 볼 권한이 있는 채널만 반환한다.
def build_server_channels_server(
    channel: discord.abc.Messageable, runtime: "Runtime", agent: AgentConfig
) -> tuple[str, list[str], Any] | None:
    # 서버 정보가 없으면 서버를 만들지 않는다.
    guild = getattr(channel, "guild", None)
    if guild is None:
        return None
    current_id = getattr(channel, "parent_id", None) or channel.id
    channels_max = runtime.config.tools.server_channels_max

    # 서버의 카테고리별 채널 목록을 보여 준다.
    @tool(
        "server_channels",
        f"현재 Discord 서버에서 볼 수 있는 채널 목록을 카테고리별로 읽는다. 최대 {channels_max}개다.",
        {},
    )
    async def server_channels(args: dict[str, Any]) -> dict[str, Any]:
        # 서버의 카테고리별 채널을 순회하며 볼 수 있는 채널 줄을 만든다.
        lines = []
        total = 0
        for category, channels in guild.by_category():
            category_name = category.name if category else "카테고리 없음"
            for ch in channels:
                if isinstance(ch, discord.CategoryChannel):
                    continue
                if not ch.permissions_for(guild.me).view_channel:
                    continue
                total += 1
                if len(lines) >= channels_max:
                    continue
                marks = [CHANNEL_TYPE_LABELS.get(ch.type.name, ch.type.name)]
                if runtime.config.is_agent_channel(ch.id):
                    marks.append("에이전트 채널")
                if ch.id == current_id:
                    marks.append("현재 채널")
                line = f"- [{category_name}] #{ch.name} ({', '.join(marks)})"
                tags = getattr(ch, "available_tags", None)
                if tags:
                    line += f" 태그: {', '.join(tag.name for tag in tags)}"
                lines.append(line)

        # 생략한 채널 수를 더해 반환한다.
        if total > len(lines):
            lines.append(f"(외 {total - len(lines)}개 생략)")
        return tool_text(f"서버: {guild.name}\n" + ("\n".join(lines) or "볼 수 있는 채널이 없습니다."))

    # 포럼 채널에 태그를 붙여 새 글을 올린다.
    @tool(
        "forum_post",
        f"에이전트 채널로 등록된 포럼에 태그를 붙여 새 글을 올린다. 태그는 {FORUM_TAGS_MIN}~{FORUM_TAGS_MAX}개다.",
        {
            "type": "object",
            "properties": {
                "forum": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["forum", "title", "content", "tags"],
        },
    )
    async def forum_post(args: dict[str, Any]) -> dict[str, Any]:
        # 포럼 이름, 제목, 내용, 태그를 검사하고 글을 만든다.
        forum_name = str(args.get("forum", "")).strip().lstrip("#")
        title = str(args.get("title", "")).strip()
        content = str(args.get("content", "")).strip()
        raw_tags = args.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = raw_tags.split(",")
        tag_names = list(dict.fromkeys(str(t).strip() for t in raw_tags if str(t).strip()))
        if not title or len(title) > THREAD_NAME_MAX:
            return tool_text(f"제목은 1~{THREAD_NAME_MAX}자여야 합니다.")
        if not content or len(content) > DISCORD_LIMIT:
            return tool_text(f"본문은 1~{DISCORD_LIMIT}자여야 합니다.")
        if not FORUM_TAGS_MIN <= len(tag_names) <= FORUM_TAGS_MAX:
            return tool_text(f"태그는 {FORUM_TAGS_MIN}~{FORUM_TAGS_MAX}개여야 합니다.")
        # archive forum은 전용 시작 도구만 사용해 일반 게시가 저장소 작업 문맥이 되지 않게 한다.
        forum = next(
            (
                ch for ch in guild.channels
                if isinstance(ch, discord.ForumChannel)
                and ch.name == forum_name
                and runtime.config.is_agent_channel(ch.id)
                and ch.id != runtime.config.archive_forum_id
            ),
            None,
        )
        if forum is None:
            return tool_text(
                f"일반 게시가 가능한 에이전트 포럼을 찾지 못했습니다: #{forum_name}"
            )
        # 태그는 이름으로 받고 포럼의 실제 태그와 대소문자 구분 없이 맞춘다.
        by_name = {tag.name.casefold(): tag for tag in forum.available_tags}
        missing = [name for name in tag_names if name.casefold() not in by_name]
        if missing:
            available = ", ".join(tag.name for tag in forum.available_tags) or "없음"
            return tool_text(f"없는 태그: {', '.join(missing)}. 사용할 수 있는 태그: {available}")
        tags = [by_name[name.casefold()] for name in tag_names]
        try:
            created = await forum.create_thread(name=title, content=content, applied_tags=tags)
        except discord.HTTPException as exc:
            log.warning("forum post failed agent=%s: %s", agent.name, exc)
            return tool_text(f"포럼 글을 올리지 못했습니다: {exc}")
        # 글에서 이어지는 대화가 본문을 알고, 현재 대화의 다른 캐릭터도 글이 올라간 것을 알도록 기록한다.
        runtime.append_chat(created.thread.id, agent.name, content, created.message.id)
        tag_text = ", ".join(tag.name for tag in tags)
        runtime.append_chat(channel.id, agent.name, f"(포럼 글 작성) #{forum.name} / {title} / 태그: {tag_text}")
        return tool_text(f"#{forum.name}에 '{title}' 글을 올렸습니다. 태그: {tag_text}")

    # 기본 채널 도구와 documenter 전용 도구를 등록한다.
    channel_tools = [server_channels, forum_post]
    channel_tool_names = ["mcp__channels__server_channels", "mcp__channels__forum_post"]
    if agent.role == "documenter" and runtime.config.archive_forum_id is not None:
        # 저장소 요청의 원문과 첨부를 ID로 고정된 전용 포럼에 옮긴다.
        # 이름으로 찾는 일반 forum_post와 분리해 다른 포럼에서 GitHub 작업이 시작되지 않게 한다.
        @tool(
            "archive_thread_start",
            "현재 요청을 설정된 archive forum의 새 작업 게시글로 옮긴다. 일반 forum_post와 별개다.",
            {"title": str, "content": str},
        )
        async def archive_thread_start(args: dict[str, Any]) -> dict[str, Any]:
            # 제목과 첫 글을 확인한다.
            title = str(args.get("title", "")).strip()
            content = str(args.get("content", "")).strip()
            if not title or len(title) > THREAD_NAME_MAX:
                return tool_text(f"제목은 1~{THREAD_NAME_MAX}자여야 합니다.")
            if not content:
                return tool_text("첫 글 내용이 필요합니다.")

            # 아카이브 포럼 채널을 확인한다.
            forum = guild.get_channel(runtime.config.archive_forum_id)
            if not isinstance(forum, discord.ForumChannel):
                return tool_text("archive_forum_id가 ForumChannel을 가리키지 않습니다.")

            # 원문 사용자 메시지와 작업 자료로 게시글을 만들어 올리고 첨부 파일을 옮긴다.
            try:
                original = runtime.chat_last_user_message.get(channel.id)
                original_text = original.content.strip() if original is not None else ""
                post_text = original_text or content
                if original_text and content != original_text:
                    post_text = f"{original_text}\n\n[작업 자료]\n{content}"
                chunks = split_message(post_text)
                created = await forum.create_thread(name=title, content=chunks[0])
                for chunk in chunks[1:]:
                    await created.thread.send(chunk)
                if original is not None and original.attachments:
                    files = [await attachment.to_file(use_cached=True) for attachment in original.attachments]
                    for start in range(0, len(files), 10):
                        await created.thread.send(files=files[start:start + 10])
            except discord.HTTPException as exc:
                return tool_text(f"archive thread를 만들지 못했습니다: {exc}")

            # 채팅 기록에 남기고 결과를 알린다.
            runtime.append_chat(created.thread.id, agent.name, post_text, created.message.id)
            runtime.append_chat(
                channel.id,
                agent.name,
                f"(저장소 작업 게시글 생성) #{forum.name} / {title}",
            )
            return tool_text(f"#{forum.name}에 저장소 작업 게시글을 만들었습니다: {created.thread.mention}")
        # 저장소 스레드 시작 도구를 등록한다.
        channel_tools.append(archive_thread_start)
        channel_tool_names.append("mcp__channels__archive_thread_start")

    # 도구를 묶어 channels 서버를 반환한다.
    return "channels", channel_tool_names, create_sdk_mcp_server(
        name="channels", version="1.0.0", tools=channel_tools
    )


# 역할별 전용 도구 서버. documenter의 지식 저장소 도구는 설정값이 필요해 도구 연결부에서 따로 붙인다.
ROLE_TOOL_BUILDERS = {
    "director": build_director_server,
    "reviewer": build_history_server,
    "assistant": build_reminder_server,
}


# 역할에 실제로 붙는 전용 도구 이름을 반환한다. 도구 연결부와 같은 조건을 쓴다.
def role_tool_names(role: str, archive_repository: "ArchiveRepositoryConfig | None") -> list[str]:
    # 역할별 도구 이름을 정한다.
    if role == "director":
        names = ["mcp__director__poll_create"] if hasattr(discord, "Poll") else []
        names += ["mcp__director__thread_create", "mcp__director__message_pin"]
    elif role == "reviewer":
        names = ["mcp__history__channel_history"]
    elif role == "assistant":
        names = REMINDER_TOOL_NAMES
    else:
        names = []

    # 서버 이름 접두어를 뗀 도구 이름을 반환한다.
    return [name.rsplit("__", 1)[-1] for name in names]
