"""장기기억 프롬프트·도구·삭제·시작 동기화를 처리하고 대화 상태를 저장한다."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
import discord
from archive_workflow import BASE_BRANCH
from memory_store import (
    KIND_ARCHIVE,
    KIND_EXPLICIT,
    KIND_PERSONA_EVENT,
    KIND_PERSONA_SETTING,
    LONG_TERM_KINDS,
    search_terms,
)
from claude_agent_sdk import create_sdk_mcp_server, tool

from config import CHAT_STATE_DIR, DISCORD_LIMIT, AgentConfig, log, memory_settings, tool_text
from attachments import ATTACHMENT_ROOT, prune_old_attachments
from archive_tools import (
    github_branch_sha,
    github_create_branch,
    github_create_pr,
    github_get_file,
    github_put_file,
)
from tools import parse_message_id
from prompts import is_archive_thread, is_evidence_entry

if TYPE_CHECKING:
    from bot import Runtime

MEMORY_TOOL_NAMES = [
    "mcp__memory__memory_search",
    "mcp__memory__memory_save",
    "mcp__memory__persona_memory_list",
]
MEMORY_KIND_LABELS = {
    KIND_ARCHIVE: "archive",
    KIND_EXPLICIT: "기억 요청",
    KIND_PERSONA_EVENT: "겪은 일",
    KIND_PERSONA_SETTING: "설정",
}
# 자동 첨부 검색어를 뽑을 최근 대화 줄 수.
MEMORY_QUERY_LINES = 3
MEMORY_FILE_NAME = "MEMORY.md"


# 자동 첨부 기억을 고를 검색 문장. 도구·출처 기록을 뺀 최근 대화 몇 줄을 쓴다.
def memory_query_text(runtime: Any, channel_id: int) -> str:
    # 대화 기록에서 증거 항목을 빼고 최근 줄을 이어 검색어를 만든다.
    history = getattr(runtime, "chat_histories", {}).get(channel_id) or ()
    lines = [entry[3] for entry in history if not is_evidence_entry(entry)]
    return "\n".join(lines[-MEMORY_QUERY_LINES:])


# 기억 목록을 글자 한도 안의 프롬프트 목록으로 만든다. 없으면 (없음)이다.
def render_memory_items(rows: list[dict[str, Any]], max_chars: int) -> str:
    # 기억마다 한 줄 문장을 만들고 글자 수 한도 안에서 이어 붙인다.
    lines: list[str] = []
    used = 0
    # 기억마다 한 줄로 만들고 글자 수 한도 안에서 담는다.
    for row in rows:
        content = " ".join(row["content"].split())
        author = (row.get("author") or "").strip() or "미상"
        line = f"- ({MEMORY_KIND_LABELS.get(row['kind'], row['kind'])}, 기억 ID {row['id']}, 작성 {author}) {content}"
        room = max_chars - used
        if room <= 0:
            break
        lines.append(line[:room])
        used += len(lines[-1])
    return ("\n" + "\n".join(lines)) if lines else "(없음)"


# 턴 프롬프트의 관련 장기기억: 최근 대화 낱말이 맞는 서버 장기기억 상위 항목.
def render_prompt_memories(runtime: Any, guild_id: int | None, query_text: str) -> str:
    # 기억 저장소를 검색해 프롬프트에 넣을 기억 문장을 만든다.
    store = getattr(runtime, "memory", None)
    if store is None or guild_id is None:
        return "(없음)"
    settings = memory_settings(runtime.config)
    rows = store.search(guild_id, LONG_TERM_KINDS, search_terms(query_text), settings.prompt_max_items)
    return render_memory_items(rows, settings.prompt_max_chars)


# 턴 프롬프트의 캐릭터 기억: 이 서버에서 이 캐릭터가 겪은 일. 낱말이 맞는 순, 같으면 최신 순이다.
def render_prompt_persona_events(runtime: Any, guild_id: int | None, agent: str, query_text: str) -> str:
    # 런타임에서 기억 저장소를 꺼낸다.
    store = getattr(runtime, "memory", None)
    # 저장소나 서버 정보가 없으면 없음 표시를 반환한다.
    if store is None or guild_id is None:
        return "(없음)"

    # 검색어와 일치하는 캐릭터 사건을 한도만큼 찾아 렌더링한다.
    settings = memory_settings(runtime.config)
    rows = store.search(
        guild_id, (KIND_PERSONA_EVENT,), search_terms(query_text), settings.persona_prompt_max_items,
        agent=agent, require_match=False,
    )
    return render_memory_items(rows, settings.persona_prompt_max_chars)


# 시스템 프롬프트 끝에 붙일 이 캐릭터의 설정. 최신 순으로 persona_setting_max_chars까지 넣는다.
def render_persona_settings_block(runtime: Any, guild_id: int | None, agent: str) -> str:
    # 런타임에서 기억 저장소를 꺼낸다.
    store = getattr(runtime, "memory", None)
    # 저장소나 서버 정보가 없으면 빈 문자열을 반환한다.
    if store is None or guild_id is None:
        return ""

    # 캐릭터 설정 기억을 찾아 설정 블록으로 렌더링한다.
    rows = store.list_persona(guild_id, agent, (KIND_PERSONA_SETTING,))
    if not rows:
        return ""
    items = render_memory_items(rows, memory_settings(runtime.config).persona_setting_max_chars)
    return f"\n\n---\n\n# Character settings\n\n사용자가 이 서버에서 너에게 정해 준 설정이다. 최신 설정이 위에 있다.{items}\n"


# 기억 저장 대상인 archive 메시지인지 판정한다. 허용 서버의 archive 스레드에서 허용 사용자나 캐릭터 봇이 쓴 글이다.
def is_archive_memory_message(runtime: Any, message: Any) -> bool:
    # 아카이브 스레드의 메시지만 대상으로 한다.
    cfg = runtime.config
    if not is_archive_thread(message.channel, cfg):
        return False

    # 허용된 서버인지 확인한다.
    guild = getattr(message, "guild", None)
    if guild is None or (cfg.allowed_guild_ids and guild.id not in cfg.allowed_guild_ids):
        return False

    # 캐릭터이거나 허용된 사용자의 메시지만 기억 대상으로 인정한다.
    author = message.author
    if author.id in set(runtime.roster.values()):
        return True
    return not author.bot and (not cfg.allowed_user_ids or author.id in cfg.allowed_user_ids)


# archive 메시지의 저장 형식: 본문과 첨부 파일명.
def archive_memory_text(message: Any) -> str:
    # 메시지 본문과 첨부 파일명을 이어 archive 기억 문장을 만든다.
    parts = [message.content.strip()] if message.content.strip() else []
    parts += [f"[첨부: {attachment.filename}]" for attachment in message.attachments]
    return "\n".join(parts)


# archive 메시지 작성자를 에이전트 이름이나 표시 이름으로 나타낸다.
def archive_author_label(runtime: Any, message: Any) -> str:
    name = runtime.bot_id_to_name().get(message.author.id)
    return name or message.author.display_name


# archive 메시지 하나를 메시지 ID 기준으로 기억에 추가하거나 갱신한다. 이미 삭제된 메시지는 넣지 않는다.
def capture_archive_message(runtime: Any, message: Any) -> bool:
    # 저장소가 없거나 최근 삭제된 메시지는 건너뛴다.
    store = runtime.memory
    if store is None or message.id in runtime.recent_deleted_ids:
        return False
    if not is_archive_memory_message(runtime, message):
        return False

    # 기억할 본문을 만들고 비었으면 건너뛴다.
    text = archive_memory_text(message)
    if not text:
        return False

    # 아카이브 기억에 저장하거나 갱신한다.
    store.upsert_archive(
        message.guild.id, message.id, message.channel.parent_id, message.channel.id,
        archive_author_label(runtime, message), text,
    )
    return True


# 메시지 ID로 저장한 첨부 파일({message_id}_{attachment_id}_{name})을 지운다.
def delete_attachment_files(message_ids: set[int]) -> int:
    # 첨부 폴더가 없으면 지울 것이 없다.
    if not ATTACHMENT_ROOT.is_dir():
        return 0

    # 메시지 id에 해당하는 첨부 파일을 지운다.
    removed = 0
    for message_id in message_ids:
        for path in ATTACHMENT_ROOT.glob(f"{message_id}_*"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                log.warning("failed to delete attachment %s", path)
    return removed


# 메시지 삭제를 기억·첨부에 반영한다. 채널 등록 여부와 관계없이 먼저 실행한다.
# 저장소에 올린 persona 기억이 지워지면 캐릭터별 MEMORY.md 갱신 필요 표시만 남긴다.
def handle_deleted_messages(runtime: Any, message_ids: set[int]) -> list[dict[str, Any]]:
    # 삭제된 메시지의 묶음을 넓히고 삭제 기록을 남긴다.
    expanded = runtime.expand_message_group(set(message_ids))
    runtime.record_deleted(expanded)

    # 첨부 파일과 출처를 지운다.
    delete_attachment_files(expanded)

    # 저장소가 있으면 연결된 기억을 지우고 기억 파일 갱신 대상으로 표시한다.
    store = runtime.memory
    if store is None:
        return []
    store.remove_attachment_sources(expanded)
    deleted = store.delete_by_messages(expanded)
    mark_memory_file_updates(runtime, deleted)
    if deleted:
        log.info("memory delete messages=%s memories=%s", sorted(expanded), [row["id"] for row in deleted])
    return deleted


# 스레드 삭제를 기억·첨부에 반영한다.
def handle_deleted_channel(runtime: Any, channel_id: int) -> list[dict[str, Any]]:
    # 저장소가 없으면 지울 것이 없다.
    store = runtime.memory
    if store is None:
        return []

    # 채널의 첨부 파일과 출처를 지운다.
    message_ids = {message_id for message_id, _ in store.attachment_sources(channel_id)}
    delete_attachment_files(message_ids)
    store.remove_attachment_sources(message_ids)

    # 채널의 기억을 지우고 기억 파일 갱신 대상으로 표시한다.
    deleted = store.delete_by_channel(channel_id)
    mark_memory_file_updates(runtime, deleted)
    if deleted:
        log.info("memory delete channel=%s memories=%s", channel_id, [row["id"] for row in deleted])
    return deleted


# 저장한 첨부를 원본 메시지에 연결한다. 저장 직후 이미 삭제된 메시지면 파일을 바로 지운다.
def register_saved_attachments(runtime: Any, message_id: int, channel_id: int) -> bool:
    # 삭제된 메시지의 첨부는 지우고 아니면 첨부 출처를 등록한다.
    if message_id in runtime.recent_deleted_ids:
        delete_attachment_files({message_id})
        return False
    if runtime.memory is not None:
        runtime.memory.add_attachment_source(message_id, channel_id)
    return True


# 채널 ID로 서버 ID를 찾는다. 실행 중인 봇이 그 채널을 볼 수 없으면 None이다.
def channel_guild_id(runtime: Any, channel_id: int) -> int | None:
    # 클라이언트마다 채널을 찾아 서버 ID를 돌려준다.
    for client in runtime.clients.values():
        channel = client.get_channel(channel_id)
        guild = getattr(channel, "guild", None)
        if guild is not None:
            return guild.id
    return None


# 요약 호출이 낸 persona 항목을 검증해 저장한다.
# 로스터 밖 캐릭터, 요약 묶음 밖 출처, 출처 없는 항목은 버리고, 저장 직전에 원본 삭제를 다시 확인한다.
def save_persona_events(
    runtime: Any, channel_id: int, entries: list[tuple], events: list[Any]
) -> int:
    # 저장소나 사건이 없으면 저장하지 않는다.
    store = getattr(runtime, "memory", None)
    if store is None or not events:
        return 0

    # 채널의 서버 id를 구한다.
    guild_id = channel_guild_id(runtime, channel_id)
    if guild_id is None:
        log.warning("persona events skipped: guild unknown channel=%s", channel_id)
        return 0

    # 명단, 이번 배치와 현재 기록의 메시지 id, 글자 수 한도를 구한다.
    roster = {agent.name for agent in runtime.config.agents}
    batch_ids = {entry[4] for entry in entries if entry[4] is not None}
    present_ids = {entry[4] for entry in runtime.chat_history_for(channel_id) if entry[4] is not None}
    max_chars = memory_settings(runtime.config).persona_setting_max_chars
    kinds = {"event": KIND_PERSONA_EVENT, "setting": KIND_PERSONA_SETTING}

    # 사건마다 형식과 출처 메시지를 확인하고 저장한다.
    saved = 0
    for item in events:
        if not isinstance(item, dict):
            continue
        agent = str(item.get("agent", "")).strip().removeprefix("agent:")
        kind = kinds.get(str(item.get("kind", "")).strip())
        content = " ".join(str(item.get("content", "")).split())[:max_chars]
        raw_ids = item.get("source_message_ids")
        source_ids = {parse_message_id(value) for value in raw_ids} if isinstance(raw_ids, list) else set()
        if agent not in roster or kind is None or not content:
            continue
        if not source_ids or None in source_ids or not source_ids <= batch_ids:
            continue
        expanded = runtime.expand_message_group(source_ids)
        if expanded & runtime.recent_deleted_ids or not source_ids <= present_ids:
            continue
        store.add(
            guild_id, kind, content, agent=agent, author="summary", channel_id=channel_id,
            sources=[(message_id, channel_id) for message_id in expanded],
        )
        saved += 1

    # 저장 건수를 로그로 남기고 반환한다.
    if saved:
        log.info("persona events saved channel=%s count=%s", channel_id, saved)
    return saved


# MEMORY.md 내용을 저장소에 올린 기억 목록으로 다시 만든다.
def render_memory_file(korean_name: str, rows: list[dict[str, Any]]) -> str:
    # 기억 종류별 절로 캐릭터 기억 파일 내용을 만든다.
    sections = [(KIND_PERSONA_SETTING, "설정"), (KIND_PERSONA_EVENT, "겪은 일")]
    lines = [f"# {korean_name} 기억", "", "Discord에서 사용자가 승인해 올린 캐릭터 기억이다."]
    for kind, title in sections:
        items = [" ".join(row["content"].split()) for row in rows if row["kind"] == kind]
        lines += ["", f"## {title}", ""]
        lines += [f"- {item}" for item in items] or ["- (없음)"]
    return "\n".join(lines) + "\n"


# 한 캐릭터의 MEMORY.md를 새 브랜치에 커밋하고 master로 가는 PR을 만든다. 내용이 같으면 None이다.
# add_ids는 이번에 올릴 기억이며 PR 생성에 성공한 뒤에만 uploaded로 표시한다.
async def publish_persona_memory_file(
    runtime: Any, agent_name: str, add_ids: list[int], reason: str
) -> str | None:
    # 저장소 설정과 대상 캐릭터를 확인한다.
    cfg = runtime.config.archive_repository
    store = runtime.memory
    agent = next((item for item in runtime.config.agents if item.name == agent_name), None)
    if cfg is None or store is None or agent is None:
        raise ValueError("저장소 설정이나 캐릭터를 찾을 수 없습니다.")

    # 올린 기억과 이번에 올릴 기억을 합쳐 기억 파일 내용을 만든다.
    rows = store.uploaded_persona(agent_name)
    known = {row["id"] for row in rows}
    rows += [row for row in (store.get(memory_id) for memory_id in add_ids) if row and row["id"] not in known]
    rows.sort(key=lambda row: (row["created_at"], row["id"]))
    content = render_memory_file(agent.korean_name, rows)
    path = f".claude/agents/{agent.role}/{MEMORY_FILE_NAME}"

    # 기존 파일 내용이 같으면 올릴 것이 없다.
    async with aiohttp.ClientSession() as session:
        current, _ = await github_get_file(session, cfg, path, BASE_BRANCH)
        if current == content:
            store.mark_uploaded(add_ids)
            return None

        # 기본 브랜치에서 새 브랜치를 만든다.
        base_sha = await github_branch_sha(session, cfg, BASE_BRANCH)
        if base_sha is None:
            raise ValueError(f"기본 브랜치 {BASE_BRANCH}가 없습니다.")
        branch = f"memory/{agent.role}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        await github_create_branch(session, cfg, branch, base_sha)

        # 새 브랜치에 파일을 저장하고 PR을 만든다.
        _, sha = await github_get_file(session, cfg, path, branch)
        await github_put_file(session, cfg, path, content, f"{agent.name}: {reason}", sha, branch)
        url = await github_create_pr(
            session, cfg, branch, BASE_BRANCH, f"{agent.korean_name} 기억 {reason}",
            f"Discord persona 기억 반영: {reason}\n\n병합은 사용자가 확인한 뒤 진행합니다.",
        )

    # 올린 기억으로 표시하고 PR URL을 반환한다.
    store.mark_uploaded(add_ids)
    return url


# 저장소에 올린 persona 기억이 삭제되면 캐릭터별 갱신 필요 표시만 저장한다.
# 저장소 MEMORY.md는 그 캐릭터의 다음 `승인` 때 함께 반영한다.
def mark_memory_file_updates(runtime: Any, deleted: list[dict[str, Any]]) -> None:
    # 삭제된 기억 중 업로드된 것의 에이전트를 찾아 동기화 필요로 표시한다.
    agents = {row["agent"] for row in deleted if row.get("uploaded") and row.get("agent")}
    approvals = getattr(runtime, "memory_approvals", None)
    if not agents or approvals is None or runtime.config.archive_repository is None:
        return
    approvals.mark_sync_needed(agents)
    log.info("memory file sync needed agents=%s", sorted(agents))


# 같은 요청자의 저장소 작업 승인 대기(archive pending)를 반환한다. 없으면 None이다.
def archive_pending_for(runtime: Any, channel_id: int, requester_id: int | None) -> Any:
    # 작업 흐름 저장소와 요청자가 있는지 확인한다.
    threads = getattr(getattr(runtime, "archive_workflow", None), "threads", None)
    if not threads or requester_id is None:
        return None

    # 요청자의 대기 중인 항목이면 반환한다.
    workflow = threads.get(channel_id)
    pending = getattr(workflow, "pending", None)
    if pending is None or pending.status != "pending" or pending.requester_id != requester_id:
        return None
    return pending


# 모든 역할이 쓰는 장기기억·persona 기억 도구를 만든다. 서버 ID나 저장소가 없으면 붙이지 않는다.
# memory_save와 업로드 대기는 사용자 메시지로 시작한 대화의 턴(user_trigger_message_id)에서만 만든다.
# requester_id는 업로드 대기를 승인·취소할 수 있는 사용자다.
def build_memory_server(
    channel: Any,
    runtime: Any,
    agent: AgentConfig,
    user_trigger_message_id: int | None = None,
    requester_id: int | None = None,
) -> tuple[str, list[str], Any] | None:
    # 저장소나 서버 정보가 없으면 서버를 만들지 않는다.
    store = getattr(runtime, "memory", None)
    guild_id = getattr(getattr(channel, "guild", None), "id", None)
    if store is None or guild_id is None:
        return None
    settings = memory_settings(runtime.config)
    agent_names = {item.name for item in runtime.config.agents}

    # 서버 장기기억을 검색어로 찾는다.
    @tool(
        "memory_search",
        f"이 서버의 장기기억(archive 포럼 글, 기억 요청)을 낱말로 찾는다. 최대 {settings.search_max_results}개다.",
        {"query": str},
    )
    async def memory_search(args: dict[str, Any]) -> dict[str, Any]:
        # 검색어로 장기기억을 찾는다.
        terms = search_terms(str(args.get("query", "")))
        rows = store.search(guild_id, LONG_TERM_KINDS, terms, settings.search_max_results)
        if not rows:

            # 찾은 기억이 없으면 없음을 알린다.
            return tool_text("찾은 장기기억이 없습니다.")
        return tool_text("\n".join(
            f"- 기억 ID {row['id']} ({MEMORY_KIND_LABELS[row['kind']]}, 작성 {row['author'] or '미상'}) {row['content']}"
            for row in rows
        ))

    # 사용자 요청을 서버 장기기억이나 캐릭터 설정으로 저장한다.
    @tool(
        "memory_save",
        "사용자가 기억하라고 한 내용을 저장한다. scope는 guild(서버 공용), self(자기 설정), "
        "또는 설정을 받을 캐릭터의 내부 ID다. target_message_id는 기억할 내용이 담긴 메시지 ID이고, "
        "비우면 이번 사용자 메시지다.",
        {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "target_message_id": {"type": "string"},
                "scope": {"type": "string"},
            },
            "required": ["content", "scope"],
        },
    )
    async def memory_save(args: dict[str, Any]) -> dict[str, Any]:
        # 사용자 턴이 아니면 저장하지 않는다.
        if user_trigger_message_id is None:
            return tool_text("memory_save는 사용자 메시지에 답하는 턴에서만 쓸 수 있습니다.")

        # 내용과 범위를 읽어 종류와 대상 캐릭터, 글자 수 한도를 정한다.
        content = " ".join(str(args.get("content", "")).split())
        scope = str(args.get("scope", "")).strip()
        if scope == "guild":
            kind, target_agent, max_chars = KIND_EXPLICIT, None, DISCORD_LIMIT
        elif scope == "self" or scope in agent_names:
            kind = KIND_PERSONA_SETTING
            target_agent = agent.name if scope == "self" else scope
            max_chars = settings.persona_setting_max_chars
        else:
            return tool_text("scope는 guild, self, 캐릭터 내부 ID 중 하나여야 합니다.")

        # 내용 길이를 확인한다.
        if not content or len(content) > max_chars:
            return tool_text(f"content는 1~{max_chars}자여야 합니다.")

        # 대상 메시지가 이 채널 대화의 메시지인지 확인한다.
        raw_target = args.get("target_message_id")
        target_id = parse_message_id(raw_target) if raw_target not in (None, "") else user_trigger_message_id
        history = runtime.chat_history_for(channel.id)
        known_ids = runtime.expand_message_group(
            {user_trigger_message_id} | {entry[4] for entry in history if entry[4] is not None}
        )
        if target_id is None or target_id not in known_ids:
            return tool_text("target_message_id가 이 채널 대화의 메시지 ID가 아닙니다.")

        # 출처 메시지가 삭제되지 않았는지 확인한다.
        sources = runtime.expand_message_group({user_trigger_message_id, target_id})
        if sources & runtime.recent_deleted_ids:
            return tool_text("원본 메시지가 이미 삭제되어 저장하지 않았습니다.")

        # 작성자를 구해 기억을 저장하고 결과를 알린다.
        target_entry = next((entry for entry in history if entry[4] == target_id), None)
        author = runtime.chat_display_name(target_entry[2]) if target_entry else ""
        memory_id = store.add(
            guild_id, kind, content, agent=target_agent, author=author, channel_id=channel.id,
            thread_id=channel.id if isinstance(channel, discord.Thread) else None,
            sources=[(message_id, channel.id) for message_id in sources],
        )
        log.info("memory save agent=%s kind=%s memory=%s scope=%s", agent.name, kind, memory_id, scope)
        return tool_text(f"기억했습니다. 기억 ID {memory_id}, 범위 {scope}.")

    # 이 서버의 캐릭터 기억을 보여 주고, 조건이 맞으면 채널당 1건의 업로드 승인 대기를 만든다.
    # 업로드는 요청자의 `승인` 메시지를 받은 Python이 실행하고 이 도구는 GitHub를 호출하지 않는다.
    @tool("persona_memory_list", "이 서버에서 자기 캐릭터가 가진 기억(겪은 일, 설정)을 보여 준다.", {})
    async def persona_memory_list(args: dict[str, Any]) -> dict[str, Any]:
        # 캐릭터 기억 목록과 업로드 대기 상태를 구한다.
        rows = store.list_persona(guild_id, agent.name)
        approvals = getattr(runtime, "memory_approvals", None)
        sync = approvals is not None and agent.name in approvals.sync_needed

        # 기억도 동기화 대기도 없으면 없음을 알린다.
        if not rows and not sync:
            return tool_text("이 서버에 저장된 캐릭터 기억이 없습니다.")

        # 저장소에 올리지 않은 기억과 목록 줄을 만든다.
        pending = [row["id"] for row in rows if not row["uploaded"]]
        lines = [
            f"- 기억 ID {row['id']} ({MEMORY_KIND_LABELS[row['kind']]}"
            f"{', 저장소 반영됨' if row['uploaded'] else ''}) {row['content']}"
            for row in rows
        ]

        # 상태에 맞는 안내 문장을 정하고, 올릴 수 있으면 업로드 대기를 만든다.
        if not pending and not sync:
            footer = "모든 기억이 저장소에 반영되어 있습니다."
        elif runtime.config.archive_repository is None or approvals is None:
            footer = "저장소 설정이 없어 올릴 수 없습니다."
        elif user_trigger_message_id is None or not requester_id:
            footer = "사용자 메시지에 답하는 턴이 아니라 업로드 대기를 만들지 않았습니다."
        elif archive_pending_for(runtime, channel.id, requester_id) is not None:
            footer = "같은 요청자의 저장소 작업 승인 대기가 있어 업로드 대기를 만들지 않았습니다."
        else:
            approvals.offer(channel.id, agent.name, int(requester_id), pending)
            footer = (
                f"저장소에 아직 올리지 않은 기억 {len(pending)}개"
                f"{', 원본 삭제 반영 포함' if sync else ''}. "
                "요청자가 `승인`이라고만 보내면 Python이 PR을 만들고, `취소`라고 보내면 대기를 지웁니다."
            )

        # 목록과 안내 문장을 합쳐 반환한다.
        return tool_text("\n".join([*lines, footer]))

    # 도구를 묶어 memory 서버를 반환한다.
    return "memory", list(MEMORY_TOOL_NAMES), create_sdk_mcp_server(
        name="memory", version="1.0.0",
        tools=[memory_search, memory_save, persona_memory_list],
    )


# 원본 메시지가 있는지 확인한다. 없으면 False, 확인할 수 없으면 None이다.
async def message_exists(client: Any, channel_id: int | None, message_id: int) -> bool | None:
    # 채널이 없으면 확인할 수 없다.
    if channel_id is None:
        return None

    # 메시지를 조회한다. 없으면 False, 확인 실패면 None이다.
    try:
        channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        await channel.fetch_message(message_id)
    except discord.NotFound:
        return False
    except discord.HTTPException as exc:
        log.warning("memory source check failed channel=%s message=%s: %s", channel_id, message_id, exc)
        return None
    return True


# 시작 시 archive 포럼 전체(보관 스레드 포함)를 기억과 맞춘다.
# 오류가 없을 때만 시작 이전 기억 중 찾지 못한 메시지를 지운다. 동기화 중 삭제된 메시지는 다시 넣지 않는다.
async def sync_archive_forum(client: Any) -> None:
    # 서버 포럼 설정을 확인한다.
    runtime = client.runtime
    cfg = runtime.config
    store = runtime.memory
    if store is None or cfg.archive_forum_id is None:
        return
    forum = client.get_channel(cfg.archive_forum_id)
    if not isinstance(forum, discord.ForumChannel):
        return

    # 동기화 전 아카이브 메시지 id를 기록한다.
    before_ids = store.archive_message_ids()
    seen: set[int] = set()
    errors = False

    # 열린 스레드와 보관된 스레드 목록을 모은다.
    threads = {thread.id: thread for thread in forum.threads}
    try:
        async for thread in forum.archived_threads(limit=None):
            threads[thread.id] = thread
    except discord.HTTPException as exc:
        errors = True
        log.warning("archive sync thread list failed: %s", exc)

    # 스레드마다 기억 대상 메시지를 읽어 저장소에 반영한다.
    for thread in threads.values():
        records: list[tuple[int, str, str]] = []
        try:
            async for message in thread.history(limit=None, oldest_first=True):
                if not is_archive_memory_message(runtime, message):
                    continue
                text = archive_memory_text(message)
                if text:
                    records.append((message.id, archive_author_label(runtime, message), text))
        except discord.HTTPException as exc:
            errors = True
            log.warning("archive sync thread failed thread=%s: %s", thread.id, exc)
            continue
        store.sync_archive_thread(forum.guild.id, forum.id, thread.id, records, runtime.recent_deleted_ids)
        seen |= {message_id for message_id, _, _ in records}

    # 오류 없이 끝났으면 사라진 메시지의 기억을 지운다.
    removed: list[dict[str, Any]] = []
    if not errors:
        removed = store.delete_archive_messages(before_ids - seen)

    # 동기화 결과를 로그로 남긴다.
    log.info(
        "archive sync done threads=%s messages=%s removed=%s errors=%s",
        len(threads), len(seen), len(removed), errors,
    )


# 시작 시 explicit·persona 기억과 첨부의 원본을 확인한다. NotFound면 지우고 그 밖의 오류는 보존한다.
async def verify_memory_sources(client: Any) -> None:
    # 오래된 첨부를 정리하고 출처 메시지가 사라진 기억을 찾아 지운다.
    runtime = client.runtime
    store = runtime.memory
    if ATTACHMENT_ROOT.is_dir():
        prune_old_attachments(ATTACHMENT_ROOT, datetime.now().timestamp(), runtime.config.chat.history_hours * 3600)
    if store is None:
        return
    missing: set[int] = set()
    for message_id, channel_id in store.check_sources():
        if await message_exists(client, channel_id, message_id) is False:
            missing.add(message_id)
    if missing:
        handle_deleted_messages(runtime, missing)
    for message_id, channel_id in store.attachment_sources():
        # TTL로 이미 지워진 첨부는 원본 확인 없이 연결 기록만 지운다.
        if not ATTACHMENT_ROOT.is_dir() or not any(ATTACHMENT_ROOT.glob(f"{message_id}_*")):
            store.remove_attachment_sources([message_id])
            continue
        if await message_exists(client, channel_id, message_id) is False:
            delete_attachment_files({message_id})
            store.remove_attachment_sources([message_id])
    log.info("memory source check done missing=%s", len(missing))


# 처리 봇이 시작할 때 한 번 실행하는 기억 정리 작업.
async def run_memory_startup(client: Any) -> None:
    # 준비를 기다린 뒤 시작 단계를 차례로 실행하고 실패는 로그에 남긴다.
    await client.runtime.ready.wait()
    for step in (sync_archive_forum, verify_memory_sources):
        try:
            await step(client)
        except Exception:
            log.exception("memory startup step failed step=%s", step.__name__)


# 채널 대화 상태 파일 경로.
def chat_state_path(channel_id: int) -> Path:
    return CHAT_STATE_DIR / f"{channel_id}.json"


# 채널의 채팅 기록을 파일에 원자적으로 저장한다.
# 쓰는 도중 프로세스가 죽어도 임시 파일만 깨지고 기존 파일은 그대로 남는다.
def save_chat_state(channel_id: int, runtime: "Runtime") -> None:
    # 저장 폴더를 만들고 저장할 데이터를 구성한다.
    CHAT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "user_wait": getattr(runtime, "chat_user_wait", {}).get(channel_id),
        "seq": runtime.chat_seq.get(channel_id, 0),
        "summary": getattr(runtime, "chat_summaries", {}).get(channel_id),
        "history": [list(entry) for entry in runtime.chat_history_for(channel_id)],
        "message_groups": {
            str(message_id): sorted(group)
            for message_id, group in runtime.chat_message_groups.items()
            if any(item[4] == message_id for item in runtime.chat_history_for(channel_id))
        },
    }

    # 임시 파일에 쓰고 권한을 좁힌다.
    target = chat_state_path(channel_id)
    tmp_path = target.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass

    # 임시 파일로 원본을 교체한다.
    os.replace(tmp_path, target)


# 저장된 채팅 기록을 불러온다. 파일이 없거나 손상됐으면 그 채널만 건너뛴다.
# 이전 형식 파일의 sessions 항목은 더 쓰지 않으므로 읽지 않는다.
def load_chat_state(channel_id: int, runtime: "Runtime") -> None:
    # 채팅 상태 파일을 읽고 손상되면 건너뛴다.
    path = chat_state_path(channel_id)
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("채팅 상태 파일이 손상되어 건너뜁니다: %s", path)
        return
    runtime.chat_seq[channel_id] = data.get("seq", 0)
    summary = data.get("summary")
    if isinstance(summary, str) and summary.strip() and hasattr(runtime, "chat_summaries"):
        runtime.chat_summaries[channel_id] = summary
    wait = data.get("user_wait")
    if isinstance(wait, dict) and wait.get("reason") == "user_decision":
        runtime.chat_user_wait[channel_id] = wait
    history = runtime.chat_history_for(channel_id)
    for entry in data.get("history", []):
        # 메시지 ID가 없던 이전 형식(4개 항목)도 읽는다.
        seq, timestamp, speaker, content, *rest = entry
        message_id = rest[0] if rest else None
        history.append((seq, timestamp, speaker, content[:runtime.config.chat.line_max_chars], message_id))
    # 파일의 seq가 기록보다 작게 저장된 경우에도 새 줄의 순번이 겹치지 않게 한다.
    runtime.chat_seq[channel_id] = max([runtime.chat_seq[channel_id], *(entry[0] for entry in history)])
    for message_id, group in data.get("message_groups", {}).items():
        runtime.chat_message_groups[int(message_id)] = {int(value) for value in group}
