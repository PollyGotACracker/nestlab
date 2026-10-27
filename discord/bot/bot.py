"""에이전트 팀의 Discord 봇들을 구성하고 실행한다.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
from collections import OrderedDict, deque
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any

import aiohttp
import discord
from archive_workflow import BASE_BRANCH, ArchiveWorkflowStore, make_branch_name, parse_frontmatter
from memory_store import MemoryStore
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query, tool

from config import (
    AGENT_ROOT,
    CHAT_STATE_DIR,
    COMMON_RULES_FILE,
    HERE,
    ROLE_MAIN_FILE,
    ROLE_RULES_FILE,
    TURN_PROMPT_PATH,
    WEEKDAY_NAMES,
    AgentConfig,
    Config,
    log,
    log_sdk_stderr,
    log_token_usage,
    summary_settings,
)
from reminders import load_reminders, reminder_now, run_reminder_scheduler
from attachments import (
    LINK_PATTERN,
    build_document_trigger_text,
    build_image_trigger_text,
    caption_images,
    fetch_link_previews,
    read_hooks,
    save_attachments,
    split_message,
)
from archive_tools import (
    ARCHIVE_TOOL_NAMES,
    WEB_TOOL_NAMES,
    build_archive_server,
    github_apply_change_set,
    github_branch_sha,
    github_create_branch,
    github_create_issue,
    github_create_pr,
    github_error_text,
    github_find_open_pr,
    github_get_file,
)
from tools import ROLE_TOOL_BUILDERS, build_server_channels_server
from prompts import (
    CHAT_PROMPT_PATH,
    EVIDENCE_HEADER,
    EVIDENCE_HEADERS,
    build_chat_system_prompt,
    build_request_system_prompt,
    canonical_role_dir,
    collect_tool_evidence,
    current_task_id,
    discover_agents,
    fill_template,
    find_named_agent,
    is_archive_thread,
    is_chat_channel,
    is_evidence_entry,
    is_registered_agent_channel,
    parse_chat_controls,
    read_style_rules,
    read_tone_guide,
    prompt_source_signature,
    render_tool_evidence,
    render_variant,
    set_chat_turn_reason,
    suppress_handoff_body,
)
from memory_approval import MemoryApprovalStore
from memory import (
    archive_pending_for,
    build_memory_server,
    capture_archive_message,
    chat_state_path,
    handle_deleted_channel,
    handle_deleted_messages,
    load_chat_state,
    memory_query_text,
    publish_persona_memory_file,
    register_saved_attachments,
    render_persona_settings_block,
    render_prompt_memories,
    render_prompt_persona_events,
    run_memory_startup,
    save_chat_state,
)
from summary import run_channel_summary
from chat_loop import run_chat_conversation, run_chat_scheduler


# 공통 규칙(AGENTS.global.md)과 에이전트 파일(.claude/agents)은 저장소 루트의 원본을 읽는다.
RULES_ROOT = AGENT_ROOT.parent
CONFIG_PATH = AGENT_ROOT / "config.json"
MENTION = re.compile(r"<@!?(\d+)>")
ARCHIVE_STATE_PATH = HERE / "archive_workflow.json"
# 서버 장기기억과 persona 기억 저장 파일.
MEMORY_DB_PATH = HERE / "memory.sqlite3"
# 캐릭터 기억 업로드 승인 대기와 저장소 갱신 필요 표시 저장 파일.
MEMORY_APPROVAL_PATH = HERE / "memory_approval.json"
MEMORY_APPROVAL_COMMANDS = {"승인", "취소"}
# 삭제 이벤트로 받은 메시지 ID를 기억해 두는 개수. 저장 직후 및 동기화 중 삭제 경합 확인에 쓴다.
RECENT_DELETED_MAX = 4096
CHAT_WIPE_TIMEOUT_SECONDS = 5 * 60


# 여러 Discord 봇 인스턴스가 공유하는 실행 상태(로스터, 대화 기록, 잠금 등)를 담는다.
@dataclass
class Runtime:
    config: Config
    roster: dict[str, int] = field(default_factory=dict)
    clients: dict[str, "AgentBot"] = field(default_factory=dict)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    chat_histories: dict[int, deque[tuple[int, float, str, str, int | None]]] = field(default_factory=dict)
    chat_seq: dict[int, int] = field(default_factory=dict)
    chat_locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    chat_auto_fired: set[tuple[int, str, int]] = field(default_factory=set)
    chat_last_message: dict[int, discord.Message] = field(default_factory=dict)
    # 역할 전달 중 봇 메시지가 추가돼도 저장소 작업의 최초 사용자 요청과 첨부를 유지한다.
    chat_last_user_message: dict[int, discord.Message] = field(default_factory=dict)
    # 이미지 메시지 ID별 공용 캡션. 오래된 항목부터 버려 메모리를 제한한다.
    image_captions: OrderedDict[int, str] = field(default_factory=OrderedDict)
    # 분할 전송된 한 발언의 모든 Discord 메시지 ID를 첫 메시지 ID에 연결한다.
    chat_message_groups: dict[int, set[int]] = field(default_factory=dict)
    # 예약된 알림 목록. 바뀔 때마다 reminders.json에 저장한다.
    reminders: list[dict[str, Any]] = field(default_factory=list)
    reminder_next_id: int = 1
    # 채널별로 락을 기다리는 사용자 메시지 수. 0보다 크면 진행 중인 채팅을 멈춘다.
    chat_pending: dict[int, int] = field(default_factory=dict)
    # 사용자 판단 대기는 새 메시지 대기 수와 별개이며 재시작 뒤에도 유지한다.
    chat_user_wait: dict[int, dict[str, Any]] = field(default_factory=dict)
    # pending을 counter가 아니라 실제 Discord 메시지 단위로 식별한다.
    chat_pending_items: dict[int, dict[int, dict[str, Any]]] = field(default_factory=dict)
    chat_seen_messages: set[int] = field(default_factory=set)
    chat_seen_order: deque[int] = field(default_factory=lambda: deque(maxlen=2048))
    chat_turn_reasons: dict[tuple[int, int], str] = field(default_factory=dict)
    # 지식 저장소 pending은 외부 변경 승인과 재시작 사이의 동일성을 보장한다.
    archive_workflow: ArchiveWorkflowStore = field(
        default_factory=lambda: ArchiveWorkflowStore.load(ARCHIVE_STATE_PATH)
    )
    # 도구가 Stage 소유자를 기록할 수 있도록 채널의 마지막 사용자 ID를 보관한다.
    chat_last_user_id: dict[int, int] = field(default_factory=dict)
    # 채널별 이전 대화 요약문. chat_state 파일의 summary 항목으로 저장한다.
    chat_summaries: dict[int, str] = field(default_factory=dict)
    # 채널별 요약 백그라운드 태스크(채널당 1개)와 요약 대상이 처음 관측된 시각.
    summary_tasks: dict[int, asyncio.Task] = field(default_factory=dict)
    summary_due_since: dict[int, float] = field(default_factory=dict)
    # 채널별로 요약 시도(최대 summary.max_attempts회)를 마친 줄의 seq. 이 줄만 history_hours로 지울 수 있다.
    summary_attempted: dict[int, set[int]] = field(default_factory=dict)
    # 채널 전체 삭제의 확인 대기와 이미 처리한 명령 메시지 ID.
    chat_wipe_requests: dict[int, dict[str, Any]] = field(default_factory=dict)
    chat_wipe_handled: set[int] = field(default_factory=set)
    # 장기기억 저장소. main이 파일을 열어 연결하며, 없으면 기억 기능을 쓰지 않는다.
    memory: MemoryStore | None = None
    # 최근 삭제 이벤트로 받은 메시지 ID.
    recent_deleted_ids: set[int] = field(default_factory=set)
    recent_deleted_order: deque[int] = field(default_factory=lambda: deque(maxlen=RECENT_DELETED_MAX))
    # 채널별 기억 업로드 승인 대기와 캐릭터별 저장소 갱신 필요 표시. 재시작 뒤에도 유지한다.
    memory_approvals: MemoryApprovalStore = field(
        default_factory=lambda: MemoryApprovalStore.load(MEMORY_APPROVAL_PATH)
    )
    # 모든 봇이 같은 승인 및 취소 메시지를 받으므로, 처리한 메시지 ID를 남겨 나머지 봇이 소비만 하게 한다.
    memory_approval_handled: set[int] = field(default_factory=set)
    # 시작 동기화 및 원본 확인은 프로세스당 한 번만 실행한다.
    memory_startup_started: bool = False
    # 사용자 메시지 ID별 첫 화자 선택 태스크. 모든 봇이 한 번의 모델 호출을 공유한다.
    chat_router_tasks: dict[int, asyncio.Task] = field(default_factory=dict)
    # 다른 캐릭터 발언의 요지 저장소((채널, seq) -> 요지)와 생성 태스크. 봇들이 한 번의 모델 호출을 공유한다.
    chat_digests: dict[tuple[int, int], str] = field(default_factory=dict)
    chat_digest_tasks: dict[tuple[int, tuple[int, ...]], asyncio.Task] = field(default_factory=dict)

    # 삭제된 메시지 ID를 최근 삭제 목록에 넣는다.
    def record_deleted(self, message_ids: set[int]) -> None:
        # 삭제된 메시지 ID를 최근 삭제 목록에 기록하고 오래된 ID를 밀어낸다.
        for message_id in message_ids:
            if message_id in self.recent_deleted_ids:
                continue
            if len(self.recent_deleted_order) == self.recent_deleted_order.maxlen:
                self.recent_deleted_ids.discard(self.recent_deleted_order[0])
            self.recent_deleted_order.append(message_id)
            self.recent_deleted_ids.add(message_id)

    # 분할 전송된 발언의 다른 조각 ID까지 포함한 메시지 ID 묶음을 돌려준다.
    def expand_message_group(self, message_ids: set[int]) -> set[int]:
        # 메시지가 속한 묶음의 ID를 모두 모은다.
        expanded = set(message_ids)
        for primary, group in self.chat_message_groups.items():
            if primary in expanded or expanded & group:
                expanded |= group
                expanded.add(primary)
        return expanded

    # Discord 사용자 ID로 내부 에이전트 이름을 찾는 역방향 맵을 만든다.
    def bot_id_to_name(self) -> dict[int, str]:
        return {bot_id: name for name, bot_id in self.roster.items()}

    # 채널별 채팅 기록 큐를 가져오거나 없으면 새로 만든다.
    # history_max_lines를 넘친 줄은 바로 버리지 않고 요약 성공(또는 포기) 때 지운다.
    def chat_history_for(self, channel_id: int) -> deque[tuple[int, float, str, str, int | None]]:
        # 채널의 대화 기록을 돌려주고 없으면 새로 만든다.
        history = self.chat_histories.get(channel_id)
        if history is None:
            history = deque()
            self.chat_histories[channel_id] = history
        return history

    # chat.history_hours보다 오래되고 요약 시도를 마친 기록 항목을 지운다. 지운 항목이 있으면 True다.
    # 요약 시도 전 줄은 남겨 요약 대상으로 넘긴다. history_hours는 요약이 계속 실패할 때의 상한이다.
    def prune_chat_history(self, channel_id: int) -> bool:
        # 채널의 대화 기록과 요약 시도 목록을 꺼낸다.
        history = self.chat_history_for(channel_id)
        attempted = self.summary_attempted.get(channel_id, set())

        # 보관 시간이 지나고 요약을 시도한 줄부터 앞에서 지운다.
        cutoff = datetime.now().timestamp() - self.config.chat.history_hours * 3600
        pruned = False
        while history and history[0][1] < cutoff and history[0][0] in attempted:
            attempted.discard(history.popleft()[0])
            pruned = True
        return pruned

    # 채팅 기록에 발언 한 줄을 순번, 시각, Discord 메시지 ID와 함께 추가하고 오래된 항목을 지운다.
    def append_chat(
        self, channel_id: int, speaker: str, content: str, message_id: int | None = None,
        message_ids: list[int] | None = None,
    ) -> None:
        # 빈 내용은 기록하지 않는다.
        content = content.strip()
        if not content:
            return

        # 줄 길이 한도로 자르고 순번을 매긴다.
        content = content[:self.config.chat.line_max_chars]
        seq = self.chat_seq.get(channel_id, 0) + 1
        self.chat_seq[channel_id] = seq
        now = datetime.now().timestamp()

        # 기록에 추가하고, 나뉜 메시지 묶음을 보관한다.
        self.chat_history_for(channel_id).append((seq, now, speaker, content, message_id))
        if message_id is not None and message_ids:
            self.chat_message_groups[message_id] = set(message_ids)

        # 오래된 줄을 정리하고 상태를 저장하며 요약 작업을 예약한다.
        self.prune_chat_history(channel_id)
        save_chat_state(channel_id, self)
        self.maybe_schedule_summary(channel_id)

    # 요약 대상 줄 수를 센다. raw_hours보다 오래된 줄과 history_max_lines를 넘친 줄이 앞에서부터 대상이다.
    def summary_target_count(self, channel_id: int, now: float | None = None) -> int:
        # 요약 기준 시각과 초과 줄 수를 구한다.
        history = self.chat_history_for(channel_id)
        now = datetime.now().timestamp() if now is None else now
        cutoff = now - summary_settings(self.config).raw_hours * 3600
        overflow = len(history) - self.config.chat.history_max_lines

        # 한도를 넘었거나 기준 시각보다 오래된 앞쪽 줄을 센다.
        count = 0
        for index, entry in enumerate(history):
            if index >= overflow and entry[1] >= cutoff:
                break
            count += 1
        return count

    # 한 번의 요약 호출에 넣을 앞쪽 대상 줄을 고른다. 도구 및 출처 기록 줄은 한도 계산 없이 함께 지울 범위에만 넣는다.
    def summary_batch(self, channel_id: int) -> list[tuple[int, float, str, str, int | None]]:
        # 요약 설정과 대상 줄을 구한다.
        settings = summary_settings(self.config)
        targets = list(self.chat_history_for(channel_id))[:self.summary_target_count(channel_id)]

        # 줄 수와 글자 수 한도 안에서 배치에 담는다. 근거 항목은 한도와 무관하게 담는다.
        batch: list[tuple[int, float, str, str, int | None]] = []
        lines = 0
        chars = 0
        for entry in targets:
            if is_evidence_entry(entry):
                batch.append(entry)
                continue
            if lines >= settings.batch_max_lines or (lines and chars + len(entry[3]) > settings.batch_max_chars):
                break
            batch.append(entry)
            lines += 1
            chars += len(entry[3])
        return batch

    # 요약 대상이 실행 조건(줄 수 또는 대기 시간)을 채우면 채널당 하나의 백그라운드 요약 태스크를 띄운다.
    def maybe_schedule_summary(self, channel_id: int, now: float | None = None) -> bool:
        # 이미 도는 요약 작업이 있으면 예약하지 않는다.
        task = self.summary_tasks.get(channel_id)
        if task is not None and not task.done():
            return False

        # 요약 대상 줄 수를 구하고 없으면 대기 시각을 지운다.
        now = datetime.now().timestamp() if now is None else now
        count = self.summary_target_count(channel_id, now)
        if count == 0:
            self.summary_due_since.pop(channel_id, None)
            return False

        # 배치 최소 줄 수에 모자라고 대기 시간이 남았으면 예약하지 않는다.
        settings = summary_settings(self.config)
        since = self.summary_due_since.setdefault(channel_id, now)
        if count < settings.batch_min_lines and now - since < settings.flush_minutes * 60:
            return False

        # 이벤트 루프가 있으면 요약 작업을 만든다.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self.summary_tasks[channel_id] = loop.create_task(run_channel_summary(self, channel_id))
        return True

    # 요약에 반영했거나 요약을 포기한 줄을 기록에서 지운다. 그사이 삭제 및 추가된 줄은 seq로 구분한다.
    def drop_history_entries(self, channel_id: int, seqs: set[int]) -> int:
        # 지울 줄과 남길 줄을 나눠 기록을 갱신한다.
        history = self.chat_history_for(channel_id)
        kept = [entry for entry in history if entry[0] not in seqs]
        removed = [entry for entry in history if entry[0] in seqs]
        history.clear()
        history.extend(kept)

        # 요약 시도 표시와 메시지 묶음을 정리하고 저장한다.
        self.summary_attempted.get(channel_id, set()).difference_update(seqs)
        for entry in removed:
            if entry[4] is not None:
                self.chat_message_groups.pop(entry[4], None)
        save_chat_state(channel_id, self)
        return len(removed)

    # 채널 전체 삭제: 기록, 요약문, chat_state 파일, 이미지 캡션 캐시를 지운다. 장기기억과 persona 기억은 유지한다.
    def wipe_channel(self, channel_id: int) -> int:
        # 채널 기록에 속한 메시지 id와 묶음 id를 모아 이미지 설명을 지운다.
        history = self.chat_history_for(channel_id)
        message_ids = {entry[4] for entry in history if entry[4] is not None}
        for primary in list(message_ids):
            message_ids |= self.chat_message_groups.pop(primary, set())
        for message_id in message_ids:
            self.image_captions.pop(message_id, None)

        # 기록, 요약, 대기, 순번, 요약 작업 상태를 비운다.
        removed = len(history)
        history.clear()
        self.chat_summaries.pop(channel_id, None)
        self.chat_user_wait.pop(channel_id, None)
        self.chat_seq.pop(channel_id, None)
        self.summary_due_since.pop(channel_id, None)
        self.summary_attempted.pop(channel_id, None)
        task = self.summary_tasks.pop(channel_id, None)
        if task is not None and not task.done():
            task.cancel()

        # 저장된 채팅 상태 파일을 지운다.
        try:
            chat_state_path(channel_id).unlink(missing_ok=True)
        except OSError:
            log.warning("failed to delete chat state channel=%s", channel_id)
        return removed

    # 처음 받은 사용자 메시지만 처리 대상으로 표시한다.
    def claim_user_message(self, message_id: int) -> bool:
        # 처음 보는 메시지 ID이면 기록하고 True를 돌려준다.
        if message_id in self.chat_seen_messages:
            return False
        if len(self.chat_seen_order) == self.chat_seen_order.maxlen:
            self.chat_seen_messages.discard(self.chat_seen_order[0])
        self.chat_seen_order.append(message_id)
        self.chat_seen_messages.add(message_id)
        return True

    # 처리 대기 중인 사용자 메시지를 채널 대기 목록에 추가한다.
    def add_pending(self, channel_id: int, message_id: int, author_id: int) -> None:
        # 대기 항목을 등록하고 대기 수를 갱신한다.
        items = self.chat_pending_items.setdefault(channel_id, {})
        before = len(items)
        items[message_id] = {
            "message_id": message_id, "author_id": author_id,
            "task_id": current_task_id(), "reason": "user_message",
        }
        self.chat_pending[channel_id] = len(items)

        # 변경을 로그로 남긴다.
        log.info(
            "chat pending change channel=%s message=%s author=%s task=%s reason=create before=%s after=%s",
            channel_id, message_id, author_id, items[message_id]["task_id"], before, len(items),
        )

    # 처리를 마친 메시지를 채널 대기 목록에서 뺀다.
    def consume_pending(self, channel_id: int, message_id: int, reason: str) -> None:
        # 대기 항목을 꺼내 대기 수를 갱신한다.
        items = self.chat_pending_items.setdefault(channel_id, {})
        before = len(items)
        item = items.pop(message_id, None)
        self.chat_pending[channel_id] = len(items)

        # 변경을 로그로 남긴다.
        log.info(
            "chat pending change channel=%s message=%s author=%s task=%s reason=%s before=%s after=%s",
            channel_id, message_id, item.get("author_id") if item else None,
            item.get("task_id") if item else None, reason, before, len(items),
        )

    # 삭제된 메시지와 같은 묶음의 기록을 지우고 지운 줄 수를 반환한다.
    def remove_message(self, channel_id: int, message_id: int) -> int:
        # 삭제된 메시지가 속한 묶음의 대표 id를 모은다.
        history = self.chat_history_for(channel_id)
        removed_primary = {
            primary for primary, group in self.chat_message_groups.items() if message_id in group
        }
        removed_primary.add(message_id)

        # 기록에서 해당 줄을 빼고 이미지 설명을 지운다.
        kept = [entry for entry in history if entry[4] not in removed_primary]
        removed = len(history) - len(kept)
        history.clear()
        history.extend(kept)
        for primary in removed_primary:
            group = self.chat_message_groups.pop(primary, set())
            for grouped_id in group:
                self.image_captions.pop(grouped_id, None)
            self.image_captions.pop(primary, None)
        self.image_captions.pop(message_id, None)

        # 마지막 메시지 참조가 삭제 대상이면 지운다.
        if self.chat_last_message.get(channel_id) is not None and self.chat_last_message[channel_id].id in removed_primary:
            self.chat_last_message.pop(channel_id, None)
        if self.chat_last_user_message.get(channel_id) is not None and self.chat_last_user_message[channel_id].id in removed_primary:
            self.chat_last_user_message.pop(channel_id, None)
            self.chat_last_user_id.pop(channel_id, None)

        # 대기 항목을 소비하고 상태를 저장한다.
        self.consume_pending(channel_id, message_id, "message_deleted")
        save_chat_state(channel_id, self)
        return removed

    # 내부 에이전트 이름에 해당하는 한국어 표시 이름을 찾는다.
    def chat_display_name(self, speaker: str) -> str:
        # 화자 이름에 맞는 한국어 이름을 돌려준다.
        for agent in self.config.agents:
            if agent.name == speaker:
                return agent.korean_name
        return speaker

    # 채팅 기록 한 줄을 프롬프트용 텍스트로 만든다. 메시지 ID가 있으면 함께 적는다.
    # me가 발언자와 같으면 이름 뒤에 (나)를 붙인다.
    def render_chat_entry(self, speaker: str, content: str, message_id: int | None, me: str | None = None) -> str:
        # 메시지 ID와 화자 표시를 붙여 대화 항목을 한 줄로 만든다.
        id_part = f" (메시지 ID {message_id})" if message_id else ""
        self_part = "(나)" if me and speaker == me else ""
        return f"[{self.chat_display_name(speaker)}{self_part}]{id_part} {content}"

    # 채널의 전체 채팅 기록을 프롬프트에 넣을 텍스트로 만든다.
    # 새 발언 없이 시작하는 자율 채팅도 오래된 기록을 보지 않도록 먼저 정리한다.
    # digests에 seq가 있으면 그 발언은 원문 대신 `(요지) 문장`으로 쓴다.
    def render_chat_history(self, channel_id: int, digests: dict[int, str | None] | None = None, me: str | None = None) -> str:
        # 오래된 기록을 정리하고 채널 대화 기록을 프롬프트용 문자열로 만든다.
        if self.prune_chat_history(channel_id):
            save_chat_state(channel_id, self)
        history = self.chat_history_for(channel_id)
        if not history:
            return "(아직 대화 없음)"
        digests = digests or {}
        # 도구 및 출처 기록 줄은 원문 창(summary.raw_hours) 안의 것만 보낸다.
        raw_cutoff = datetime.now().timestamp() - summary_settings(self.config).raw_hours * 3600
        return "\n".join(
            self.render_chat_entry(speaker, f"(요지) {digests[seq]}" if digests.get(seq) else content, message_id, me)
            for seq, timestamp, speaker, content, message_id in history
            if not (content.startswith(EVIDENCE_HEADERS) and timestamp < raw_cutoff)
        ) or "(아직 대화 없음)"

    # 발언 묶음을 라우터 모델로 어미와 말버릇이 없는 한 문장 요지로 옮긴다. {seq: 요지}를 돌려준다.
    async def _request_chat_digest(self, channel_id: int, items: list[tuple[int, str, str]]) -> dict[int, str]:
        # 정규화 지시와 질의 옵션을 만든다.
        options = ClaudeAgentOptions(
            system_prompt=(
                "각 발언의 모든 문장을 표준어로 옮긴다. 감탄사와 말버릇은 뺀다. 그 밖의 낱말과 내용은 원문 그대로 쓴다. 높임 수준은 원문과 같게 쓴다. 음슴체 어미는 해요체로 옮긴다.\n"
                "입력은 `번호: [화자] 발언` 줄이다. 입력과 같은 개수의 `번호: 문장` 줄만 출력한다."
            ),
            tools=[], allowed_tools=[], cwd=str(AGENT_ROOT), permission_mode="default",
            setting_sources=[], strict_mcp_config=True, max_turns=1,
            model=self.config.chat.router_model, stderr=log_sdk_stderr,
        )

        # 발언 목록을 질의 문장으로 만들고 결과를 받는다.
        prompt = "\n".join(f"{seq}: [{name}] {' '.join(content.split())}" for seq, name, content in items)
        answer = ""
        async for sdk_message in query(prompt=prompt, options=options):
            if isinstance(sdk_message, ResultMessage):
                log_token_usage("digest", sdk_message, channel=channel_id, model=self.config.chat.router_model)
                answer = (getattr(sdk_message, "result", "") or "").strip()

        # 응답에서 번호별 문장을 읽고 빠진 번호가 있으면 실패로 처리한다.
        parsed = {int(m.group(1)): m.group(2).strip() for m in re.finditer(r"^\s*(\d+):\s*(.+)$", answer, re.M)}
        if any(not parsed.get(seq) for seq, _, _ in items):
            raise ValueError("digest lines missing")
        return {seq: parsed[seq] for seq, _, _ in items}

    # 화자 기준 대화 기록을 만든다. 사용자 발언은 원문이고, 화자 자신을 포함한 캐릭터 발언은 요지다.
    # 요지는 발언별로 저장하고 아직 없는 발언 묶음만 한 번 만들어 6개 봇이 공유한다. 실패하면 원문이다.
    async def render_speaker_history(self, channel_id: int, speaker: str) -> str:
        # 다른 캐릭터의 발언만 모은다. 없으면 일반 기록 렌더링을 반환한다.
        names = {agent.name for agent in self.config.agents}
        history = self.chat_history_for(channel_id)
        others = [
            (seq, who, content) for seq, _, who, content, _ in history
            if who in names
        ]
        if not others:
            return self.render_chat_history(channel_id, me=speaker)

        # 아직 요약하지 않은 발언을 찾는다.
        missing = [
            (seq, self.chat_display_name(who), content) for seq, who, content in others
            if (channel_id, seq) not in self.chat_digests
        ]

        # 요약 요청을 한 번만 보내고 결과를 캐시에 넣는다. 실패하면 원문 기록을 반환한다.
        if missing:
            key = (channel_id, tuple(seq for seq, _, _ in missing))
            task = self.chat_digest_tasks.get(key)
            if task is None:
                task = asyncio.ensure_future(self._request_chat_digest(channel_id, missing))
                self.chat_digest_tasks[key] = task
                while len(self.chat_digest_tasks) > 64:
                    self.chat_digest_tasks.pop(next(iter(self.chat_digest_tasks)))
            try:
                for seq, text in (await asyncio.shield(task)).items():
                    self.chat_digests[(channel_id, seq)] = text
                while len(self.chat_digests) > 1024:
                    self.chat_digests.pop(next(iter(self.chat_digests)))
            except Exception:
                log.exception("chat digest failed channel=%s speaker=%s", channel_id, speaker)
                return self.render_chat_history(channel_id, me=speaker)

        # 요약한 발언으로 기록을 렌더링한다.
        digests = {seq: self.chat_digests.get((channel_id, seq)) for seq, _, _ in others}
        return self.render_chat_history(channel_id, digests, speaker)

    # 채널별로 채팅 진행을 직렬화할 락을 가져오거나 없으면 새로 만든다.
    def chat_lock_for(self, channel_id: int) -> asyncio.Lock:
        # 채널의 대화 잠금을 돌려주고 없으면 새로 만든다.
        lock = self.chat_locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self.chat_locks[channel_id] = lock
        return lock

    # 라우터 모델로 사용자 메시지가 말을 건 캐릭터의 내부 ID를 고른다. 실패하거나 명단 밖 값이면 None이다.
    async def _request_chat_router(self, channel_id: int, text: str) -> str | None:
        # 에이전트 목록으로 라우터 옵션을 만들고 모델 응답에서 대상 이름을 고른다.
        names = {agent.name for agent in self.config.agents}
        roster = "\n".join(f"{agent.name}: {agent.korean_name}" for agent in self.config.agents)
        options = ClaudeAgentOptions(
            system_prompt=(
                "채팅 채널에서 사용자의 새 메시지가 누구에게 하는 말인지 고른다.\n"
                "사용자가 이름, 호칭, 멘션으로 부른 캐릭터를 고른다. 문장에 이름이 화제로만 나온 캐릭터는 부른 캐릭터로 보지 않는다.\n"
                "부른 캐릭터가 없고 새 메시지가 특정 캐릭터의 발언을 묻거나 지적하면, 그 발언을 처음 한 캐릭터를 고른다.\n"
                "부른 캐릭터가 없으면 최근 대화에서 사용자 메시지에 마지막으로 답한 캐릭터를 고른다.\n"
                "캐릭터의 내부 ID 하나만 출력한다.\n"
                f"캐릭터 명단(내부 ID: 한국어 이름):\n{roster}"
            ),
            tools=[], allowed_tools=[], cwd=str(AGENT_ROOT), permission_mode="default",
            setting_sources=[], strict_mcp_config=True, max_turns=1,
            model=self.config.chat.router_model, stderr=log_sdk_stderr,
        )
        history = self.render_chat_history(channel_id)[-4000:]
        prompt = f"최근 대화:\n{history}\n\n새 사용자 메시지:\n{text}"
        answer = ""
        async for sdk_message in query(prompt=prompt, options=options):
            if isinstance(sdk_message, ResultMessage):
                log_token_usage(
                    "router", sdk_message, channel=channel_id, model=self.config.chat.router_model,
                )
                answer = (getattr(sdk_message, "result", "") or "").strip()
        # 한글이 ID에 바로 붙어도 찾도록 ASCII 단어 문자만 경계로 본다. 한국어 표시 이름도 내부 ID로 바꾼다.
        by_label = {agent.name: agent.name for agent in self.config.agents}
        by_label.update({agent.korean_name: agent.name for agent in self.config.agents})
        found = re.search(r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, by_label)) + r")(?![A-Za-z0-9_])", answer)
        return by_label[found.group(1)] if found else None

    # 메시지 ID별로 한 번만 라우터를 호출하고 결과를 공유한다. 실패는 None이다.
    async def route_chat_target(self, channel_id: int, message_id: int, text: str) -> str | None:
        # 같은 메시지의 라우팅 요청이 이미 있으면 재사용한다.
        task = self.chat_router_tasks.get(message_id)
        if task is None:
            task = asyncio.ensure_future(self._request_chat_router(channel_id, text))
            self.chat_router_tasks[message_id] = task
            while len(self.chat_router_tasks) > 64:
                self.chat_router_tasks.pop(next(iter(self.chat_router_tasks)))

        # 라우팅 결과를 기다린다. 실패하면 None을 반환한다.
        try:
            return await asyncio.shield(task)
        except Exception:
            log.exception("chat router failed channel=%s message=%s", channel_id, message_id)
            return None

    # 시드값을 기준으로 채팅을 시작할 캐릭터를 무작위로 고른다.
    def pick_chat_starter(self, seed: str | int) -> str:
        # 에이전트 이름 중에서 시드에 따라 시작할 하나를 고른다.
        names = [agent.name for agent in self.config.agents]
        if not names:
            raise RuntimeError("채팅을 시작할 에이전트가 없습니다.")
        rng = random.Random(f"starter:{seed}")
        return rng.choice(names)


MEMORY_APPROVAL_CANCELLED = "기억 업로드 대기를 취소했습니다."
MEMORY_APPROVAL_DONE = "기억 반영 PR을 만들었습니다. 병합은 사용자가 합니다: {url}"
MEMORY_APPROVAL_SAME = "저장소 MEMORY.md가 이미 같은 내용입니다."
MEMORY_APPROVAL_EMPTY = "새로 올릴 기억이 없습니다."
APPROVAL_CONFLICT_APPROVE = (
    "승인 대기가 두 건(저장소 작업, 기억 업로드)이라 실행하지 않았습니다. "
    "`취소`로 모두 취소한 뒤 하나씩 다시 요청해 주세요."
)
APPROVAL_CONFLICT_CANCELLED = "저장소 작업 대기와 기억 업로드 대기를 모두 취소했습니다."
# 채널 전체 삭제 명령. 같은 사용자가 시간 안에 확인하면 모델 호출 없이 봇이 처리한다.
CHAT_WIPE_COMMAND = "기억 전체 삭제"
CHAT_WIPE_CONFIRM = "확인"
CHAT_WIPE_CANCEL = "취소"
CHAT_WIPE_PROMPT = (
    "이 채널의 대화 기록과 이전 대화 요약을 모두 삭제합니다. 서버 장기기억과 캐릭터 기억은 유지됩니다.\n"
    f"진행하려면 5분 안에 `{CHAT_WIPE_CONFIRM}`, 그만두려면 `{CHAT_WIPE_CANCEL}`을 입력하세요."
)
CHAT_WIPE_CANCELLED = "기억 전체 삭제를 취소했습니다."
CHAT_WIPE_DONE = "이 채널의 대화 기록과 이전 대화 요약을 삭제했습니다."

# 에이전트 한 명에 대응하는 Discord 클라이언트다. 등록된 에이전트 채널 메시지를 처리한다.
class AgentBot(discord.Client):
    # Discord intents를 설정하고 에이전트별 프롬프트 캐시를 초기화한다.
    def __init__(self, agent: AgentConfig, runtime: Runtime) -> None:
        # Discord 클라이언트를 만든다.
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        # 에이전트, 실행 객체, 프롬프트 캐시를 초기화한다.
        self.agent = agent
        self.runtime = runtime
        self._request_prompt: str | None = None
        self._request_prompt_signature: tuple[tuple[str, int, int], ...] | None = None
        self._chat_prompt: str | None = None
        self._chat_prompt_signature: tuple[tuple[str, int, int], ...] | None = None

    # 시스템 프롬프트 원본 파일들의 현재 서명을 만든다.
    def _prompt_signature(self) -> tuple[tuple[str, int, int], ...]:
        # 이 에이전트의 프롬프트 원본 서명을 계산한다.
        return prompt_source_signature(
            RULES_ROOT, self.agent.role, tuple(agent.role for agent in self.runtime.config.agents)
        )

    # 요청 채널 규칙 파일이 바뀌었으면 작업 시스템 프롬프트를 다시 만든다.
    def _refresh_request_prompt(self) -> None:
        # 프롬프트 소스가 그대로면 캐시를 유지한다.
        signature = self._prompt_signature()
        if self._request_prompt is not None and signature == self._request_prompt_signature:
            return

        # 요청용 시스템 프롬프트를 다시 만든다.
        self._request_prompt = build_request_system_prompt(
            RULES_ROOT,
            self.agent.role,
            self.agent.name,
            self.agent.korean_name,
            self.runtime.config.agents,
            self.runtime.config.archive_repository,
            self.runtime.config.tools,
            self.runtime.config.reminders,
        )
        self._request_prompt_signature = signature

    # 채팅 채널 규칙 파일이 바뀌었으면 채팅 시스템 프롬프트를 다시 만든다.
    def _refresh_chat_prompt(self) -> None:
        # 프롬프트 소스가 그대로면 캐시를 유지한다.
        signature = self._prompt_signature()
        if self._chat_prompt is not None and signature == self._chat_prompt_signature:
            return

        # 채팅용 시스템 프롬프트를 다시 만든다.
        self._chat_prompt = build_chat_system_prompt(
            RULES_ROOT,
            self.agent.role,
            self.agent.name,
            self.agent.korean_name,
            self.runtime.config.agents,
            self.runtime.config.tools,
            self.runtime.config.reminders,
        )
        self._chat_prompt_signature = signature

    # 서버, 채널, 사용자 제한 조건에 따라 이 메시지를 처리할지 판단한다.
    def _allowed(self, message: discord.Message) -> bool:
        # 허용된 서버인지 확인한다.
        cfg = self.runtime.config
        if cfg.allowed_guild_ids:
            if message.guild is None or message.guild.id not in cfg.allowed_guild_ids:
                return False

        # 등록된 채널인지 확인한다.
        if not is_registered_agent_channel(message.channel, cfg):
            return False

        # 허용된 사용자인지 확인한다.
        if not message.author.bot and cfg.allowed_user_ids:
            if message.author.id not in cfg.allowed_user_ids:
                return False
        return True

    # 투표 결과 메시지를 대화 기록에 남기고 director 캐릭터가 결과에 반응하게 한다.
    # 모든 봇이 같은 메시지를 받으므로 director 봇 하나만 처리한다.
    async def _handle_poll_result(self, message: discord.Message) -> None:
        # director가 아니거나 허용 범위 밖 채널이면 처리하지 않는다.
        if self.agent.role != "director":
            return
        cfg = self.runtime.config
        if cfg.allowed_guild_ids and (message.guild is None or message.guild.id not in cfg.allowed_guild_ids):
            return
        if not is_registered_agent_channel(message.channel, cfg):
            return
        await self.runtime.ready.wait()

        # 투표 결과 필드에서 질문과 1위를 정리해 기록 문장을 만든다.
        fields = {field.name: field.value for embed in message.embeds for field in embed.fields}
        question = fields.get("poll_question_text") or "(질문 없음)"
        winner = fields.get("victor_answer_text")
        winner_part = f"1위: {winner} ({fields.get('victor_answer_votes', '0')}표)" if winner else "1위 없음"
        text = f"질문: {question} / {winner_part} / 전체 {fields.get('total_votes', '0')}표"
        # 기록의 메시지 ID는 결과 메시지가 아니라 원래 투표 메시지를 가리키게 한다.
        poll_message_id = message.reference.message_id if message.reference else None
        try:
            await run_chat_conversation(
                self.runtime,
                message.channel.id,
                starter_name=self.agent.name,
                seed=message.id,
                trigger_speaker="투표 결과",
                trigger_text=text,
                autonomous=False,
                trigger_message_id=poll_message_id or message.id,
            )
        except Exception:
            log.exception("poll result handling failed agent=%s channel=%s", self.agent.name, message.channel.id)

    # 답장 메시지면 답장 대상(작성자, 메시지 ID, 본문 앞부분)을 설명하는 줄을 만든다. 답장이 아니면 None이다.
    async def _reply_target_label(self, message: discord.Message) -> str | None:
        # 답장 참조가 없으면 None을 반환한다.
        reference = message.reference
        if reference is None or reference.message_id is None:
            return None

        # 참조된 메시지를 구하고, 구하지 못하면 원문 없음 표시를 반환한다.
        target = reference.resolved
        if not isinstance(target, discord.Message):
            try:
                target = await message.channel.fetch_message(reference.message_id)
            except discord.HTTPException:
                return f"[답장 대상] 메시지 ID {reference.message_id} (원문을 찾을 수 없음)"

        # 작성자와 본문 일부로 답장 대상 문장을 만든다.
        author_bot = self.runtime.bot_id_to_name().get(target.author.id)
        author = self.runtime.chat_display_name(author_bot) if author_bot else target.author.display_name
        snippet = target.content.strip() or "(텍스트 없음)"
        return f"[답장 대상] {author}의 메시지 (메시지 ID {target.id}) 「{snippet}」"

    # 멘션된 봇을 우선하고, 없으면 라우터 모델이 대화 맥락으로 고른다.
    # 라우터가 실패하면 본문 이름, 답장 대상, 무작위 순서로 고른다.
    async def _chat_target_name(self, message: discord.Message) -> str:
        # 멘션을 우선하고 없으면 라우터로 대화 대상 이름을 정한다.
        bot_names = self.runtime.bot_id_to_name()
        for mentioned in message.mentions:
            target = bot_names.get(mentioned.id)
            if target is not None:
                return target
        routed = await self.runtime.route_chat_target(message.channel.id, message.id, message.content)
        if routed is not None:
            return routed
        named = find_named_agent(message.content, self.runtime.config.agents)
        if named is not None:
            return named
        reference = message.reference
        resolved = getattr(reference, "resolved", None)
        author = getattr(resolved, "author", None)
        if author is not None:
            replied = bot_names.get(author.id)
            if replied is not None:
                return replied
        # 마지막 사용자 메시지 다음에 처음 발언한 캐릭터가 이어서 답한다.
        agent_names = {agent.name for agent in self.runtime.config.agents}
        last_replier = None
        for _, _, who, _, _ in reversed(self.runtime.chat_history_for(message.channel.id)):
            if who not in agent_names:
                break
            last_replier = who
        if last_replier is not None:
            return last_replier
        return self.runtime.pick_chat_starter(message.id)

    # 모든 봇이 같은 이벤트를 받으므로 기억 및 기록 반영은 이름이 가장 앞선 처리 봇 하나만 한다.
    def _is_memory_handler(self) -> bool:
        return self.agent.name == min(self.runtime.clients, default=self.agent.name)

    # 메시지 하나가 삭제되면 기억 및 첨부와 채팅 기록에서 지운다.
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        # 삭제된 메시지의 기억과 첨부를 지우고 등록 채널이면 대화 기록에서도 뺀다.
        if not self._is_memory_handler():
            return
        # 채널 확인보다 먼저 기억과 첨부를 지워 미등록 채널의 삭제도 반영한다.
        handle_deleted_messages(self.runtime, {payload.message_id})
        channel = self.get_channel(payload.channel_id)
        if channel is None or not is_registered_agent_channel(channel, self.runtime.config):
            return
        removed = self.runtime.remove_message(payload.channel_id, payload.message_id)
        log.info(
            "chat message delete channel=%s message=%s removed_history=%s",
            payload.channel_id, payload.message_id, removed,
        )

    # 여러 메시지가 한꺼번에 삭제되면 기억 및 첨부와 채팅 기록에서 지운다.
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        # 기억 처리 담당 클라이언트만 처리한다.
        if not self._is_memory_handler():
            return
        handle_deleted_messages(self.runtime, set(payload.message_ids))

        # 등록된 채널이면 기록에서 해당 메시지를 지우고 로그로 남긴다.
        channel = self.get_channel(payload.channel_id)
        if channel is None or not is_registered_agent_channel(channel, self.runtime.config):
            return
        removed = sum(self.runtime.remove_message(payload.channel_id, message_id) for message_id in payload.message_ids)
        log.info(
            "chat bulk message delete channel=%s messages=%s removed_history=%s",
            payload.channel_id, sorted(payload.message_ids), removed,
        )

    # 스레드가 삭제되면 그 안의 메시지를 원본으로 둔 기억과 첨부를 지운다.
    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        # 삭제된 스레드의 기억을 정리한다.
        if not self._is_memory_handler():
            return
        handle_deleted_channel(self.runtime, payload.thread_id)

    # archive 메시지가 수정되면 저장한 기억 내용을 갱신한다.
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        # 수정된 메시지를 archive 기억에 반영한다.
        if not self._is_memory_handler():
            return
        message = getattr(payload, "message", None)
        if message is not None:
            capture_archive_message(self.runtime, message)

    # 봇 로그인이 끝나면 로스터에 등록하고, 모든 봇이 준비되면 ready 이벤트를 켠다.
    async def on_ready(self) -> None:
        # archive 포럼 채널을 확인하고 봇 ID를 명단에 등록한다.
        assert self.user is not None
        archive_forum_id = self.runtime.config.archive_forum_id
        if archive_forum_id is not None and not isinstance(
            self.get_channel(archive_forum_id), discord.ForumChannel
        ):
            raise RuntimeError("archive_forum_id가 이 봇이 볼 수 있는 ForumChannel을 가리키지 않습니다.")
        self.runtime.roster[self.agent.name] = self.user.id
        log.info("%s connected id=%s", self.agent.name, self.user.id)
        if len(self.runtime.roster) == len(self.runtime.config.agents):
            self.runtime.ready.set()
        # archive 동기화와 원본 확인은 처리 봇이 모든 봇 준비 뒤 백그라운드에서 한 번만 실행한다.
        if self._is_memory_handler() and not self.runtime.memory_startup_started:
            self.runtime.memory_startup_started = True
            asyncio.create_task(run_memory_startup(self))

    # 에이전트 채널에서 사용자가 메시지에 리액션을 달면, 그 리액션을 대화 맥락으로 받아 캐릭터가 이어간다.
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        # 자기 반응, 서버 밖 반응, 허용하지 않은 사용자의 반응은 처리하지 않는다.
        if self.user is None or payload.user_id == self.user.id:
            return
        if payload.guild_id is None:
            return
        cfg = self.runtime.config
        if cfg.allowed_guild_ids and payload.guild_id not in cfg.allowed_guild_ids:
            return
        if cfg.allowed_user_ids and payload.user_id not in cfg.allowed_user_ids:
            return

        # 반응이 달린 채널이 등록 채널인지 확인한다.
        channel = self.get_channel(payload.channel_id)
        if channel is None:
            channel = await self.fetch_channel(payload.channel_id)
        if not is_registered_agent_channel(channel, cfg):
            return

        # 런타임 준비를 기다린다.
        await self.runtime.ready.wait()

        # 다른 봇이 단 리액션은 무시한다. 봇끼리 리액션으로 대화를 연쇄 촉발하지 않게 한다.
        if payload.user_id in set(self.runtime.roster.values()):
            return

        # 반응이 달린 메시지를 가져온다.
        try:
            reacted_message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return

        # 반응에 답할 에이전트를 고르고 이 에이전트가 아니면 끝낸다.
        bot_names = self.runtime.bot_id_to_name()
        author_bot = bot_names.get(reacted_message.author.id)
        target_name = author_bot
        if target_name is None:
            target_name = self.runtime.pick_chat_starter(f"react:{payload.message_id}")
        if target_name != self.agent.name:
            return

        # 반응 알림 문장을 만들어 대화를 시작한다.
        reactor_name = payload.member.display_name if payload.member else str(payload.user_id)
        author_label = (
            self.runtime.chat_display_name(author_bot)
            if author_bot
            else reacted_message.author.display_name
        )
        snippet = reacted_message.content.strip() or "(텍스트 없음)"
        trigger_text = (
            f"({reactor_name}님이 {author_label}의 메시지 「{snippet}」에 {payload.emoji} 리액션을 달았다)"
        )
        caption = self.runtime.image_captions.get(payload.message_id)
        if caption:
            trigger_text = f"{trigger_text}\n[이미지 설명] {caption}"

        # 사람 메시지에 단 리액션은 공용 기록에만 남기고 응답하지 않는다.
        if author_bot is None:
            async with self.runtime.chat_lock_for(payload.channel_id):
                self.runtime.append_chat(payload.channel_id, reactor_name, trigger_text)
            return

        # 봇 메시지에 단 리액션은 작성자 봇만 한 번 응답한다.
        try:
            await run_chat_conversation(
                self.runtime,
                payload.channel_id,
                starter_name=self.agent.name,
                seed=f"react:{payload.message_id}:{payload.emoji}",
                trigger_speaker=reactor_name,
                trigger_text=trigger_text,
                autonomous=False,
                single_turn=True,
            )
        except Exception:
            log.exception(
                "chat reaction handling failed agent=%s channel=%s", self.agent.name, payload.channel_id
            )

    # 명시적인 승인과 취소는 모델을 거치지 않고 요청자와 pending을 대조한 뒤 처리한다.
    # 외부 API 호출 전에 executing으로 저장해 같은 메시지가 중복 실행되는 것을 막는다.
    async def _handle_archive_command(self, message: discord.Message) -> bool:
        # archive 스레드의 승인이나 취소 명령을 확인하고 대기 중인 요청을 처리한다.
        if self.agent.role != "documenter" or not is_archive_thread(message.channel, self.runtime.config):
            return False
        command = MENTION.sub("", message.content).strip()
        if command not in {"승인", "취소"}:
            return False
        # 같은 요청자의 기억 대기가 있으면 비정확 원문의 archive 실행을 막는다.
        # 저장소 작업 대기 없이 기억 대기만 있으면 기억 승인 처리에 넘긴다.
        approval = self.runtime.memory_approvals.active(message.channel.id)
        if (
            approval is not None
            and approval.requester_id == message.author.id
            and (
                message.content not in MEMORY_APPROVAL_COMMANDS
                or archive_pending_for(self.runtime, message.channel.id, message.author.id) is None
            )
        ):
            return False
        store = self.runtime.archive_workflow
        try:
            pending = store.pending_for(message.channel.id, message.author.id)
            if command == "취소":
                store.cancel(message.channel.id, message.author.id)
                await message.channel.send("pending을 취소했습니다.")
                return True
            pending.status = "executing"
            store.save()
            cfg = self.runtime.config.archive_repository
            assert cfg is not None
            workflow = store.thread(message.channel.id)
            async with aiohttp.ClientSession() as session:
                if pending.kind == "issue":
                    issue_type = str(pending.data["issue_type"])
                    expected_path = f".github/ISSUE_TEMPLATE/{issue_type}.md"
                    template, sha = await github_get_file(session, cfg, expected_path, BASE_BRANCH)
                    if template is None or sha != pending.data.get("template_sha"):
                        raise ValueError("Issue 템플릿이 Stage 이후 변경되어 적용을 중단했습니다.")
                    template_meta, _ = parse_frontmatter(template)
                    title_prefix = template_meta.get("title", "")
                    template_labels = [
                        value.strip()
                        for value in template_meta.get("labels", "").split(",")
                        if value.strip()
                    ]
                    if title_prefix and not str(pending.data["title"]).startswith(title_prefix):
                        raise ValueError("Issue 제목이 원격 템플릿의 접두사와 다릅니다.")
                    if sorted(template_labels) != sorted(str(value) for value in pending.data.get("labels", [])):
                        raise ValueError("Issue label이 원격 템플릿과 다릅니다.")
                    number, url = await github_create_issue(
                        session, cfg, str(pending.data["title"]), str(pending.data["body"]),
                        [str(value) for value in pending.data.get("labels", [])],
                    )
                    branch = make_branch_name(issue_type, number)
                    workflow.issue_number, workflow.issue_url = number, url
                    workflow.issue_type, workflow.branch = issue_type, branch
                    store.save()
                    base_sha = await github_branch_sha(session, cfg, BASE_BRANCH)
                    if base_sha is None:
                        raise ValueError(f"기본 브랜치 {BASE_BRANCH}가 없습니다.")
                    if await github_branch_sha(session, cfg, branch) is not None:
                        raise ValueError(f"작업 브랜치 {branch}가 이미 있습니다.")
                    await github_create_branch(session, cfg, branch, base_sha)
                    result = f"Issue #{number}와 작업 브랜치 {branch}를 만들었습니다. {url}"
                elif pending.kind == "change":
                    commit_sha = await github_apply_change_set(
                        session, cfg, str(pending.data["branch"]), str(pending.data["source_commit_sha"]),
                        str(pending.data["commit_message"]), list(pending.data["operations"]),
                    )
                    workflow.change_applied = True
                    result = f"승인된 변경 세트를 커밋 하나로 적용하고 검증했습니다: {commit_sha}"
                else:
                    template, sha = await github_get_file(
                        session, cfg, ".github/PULL_REQUEST_TEMPLATE.md", BASE_BRANCH
                    )
                    if template is None or sha != pending.data.get("template_sha"):
                        raise ValueError("PR 템플릿이 Stage 이후 변경되어 적용을 중단했습니다.")
                    existing = await github_find_open_pr(session, cfg, str(pending.data["branch"]), BASE_BRANCH)
                    url = existing or await github_create_pr(
                        session, cfg, str(pending.data["branch"]), BASE_BRANCH,
                        str(pending.data["title"]), str(pending.data["body"]),
                    )
                    result = f"PR을 확인했습니다: {url}"
            pending.status = "applied"
            store.save()
            await message.channel.send(result)
        except (ValueError, PermissionError, aiohttp.ClientResponseError) as exc:
            if 'pending' in locals() and pending.status == "executing":
                # 외부 응답이 불명확할 수 있으므로 자동 재호출하지 않는 실패 상태로 남긴다.
                pending.status = "failed"
                store.save()
            error_text = (
                github_error_text(exc)
                if isinstance(exc, aiohttp.ClientResponseError)
                else str(exc)
            )
            await message.channel.send(error_text)
        return True

    # archive thread에서 같은 요청자의 저장소 작업 대기와 기억 업로드 대기가 함께 있으면
    # `승인`은 둘 다 실행하지 않고 안내하며, `취소`는 둘 다 취소한다. 안내와 처리는 documenter가 한다.
    async def _handle_approval_conflict(self, message: discord.Message) -> bool:
        # 아카이브 스레드의 사람 메시지가 승인 또는 취소 명령인지 확인한다.
        runtime = self.runtime
        if message.author.bot or not is_archive_thread(message.channel, runtime.config):
            return False
        command = message.content
        if command not in MEMORY_APPROVAL_COMMANDS:
            return False

        # 이미 처리한 메시지는 건너뛴다.
        if message.id in runtime.memory_approval_handled:
            return True

        # 기억 승인과 아카이브 대기가 같은 요청자에게 동시에 있을 때만 충돌로 처리한다.
        channel_id = message.channel.id
        approval = runtime.memory_approvals.active(channel_id)
        if approval is None or approval.requester_id != message.author.id:
            return False
        if archive_pending_for(runtime, channel_id, message.author.id) is None:
            return False

        # documenter만 충돌 안내를 보낸다. 승인은 안내만 하고 취소는 둘 다 취소한다.
        if self.agent.role != "documenter":
            return True
        runtime.memory_approval_handled.add(message.id)
        if command == "승인":
            await message.channel.send(APPROVAL_CONFLICT_APPROVE)
            return True
        runtime.archive_workflow.cancel(channel_id, message.author.id)
        runtime.memory_approvals.cancel(channel_id)
        await message.channel.send(APPROVAL_CONFLICT_CANCELLED)
        return True

    # 기억 업로드 대기의 요청자가 보낸 `승인` 및 `취소`를 모델 호출 없이 처리한다.
    # 대기 건의 캐릭터 봇만 실행 및 안내하고, 다른 봇은 같은 메시지를 소비만 한다.
    # 외부 API 호출 전에 executing으로 저장해 같은 대기가 중복 실행되는 것을 막는다.
    async def _handle_memory_approval(self, message: discord.Message) -> bool:
        # 기억 승인 명령인지 확인하고 대기 중인 승인을 처리한다.
        runtime = self.runtime
        if message.author.bot:
            return False
        command = message.content
        if command not in MEMORY_APPROVAL_COMMANDS:
            return False
        if message.id in runtime.memory_approval_handled:
            return True
        channel_id = message.channel.id
        store = runtime.memory_approvals
        approval = store.active(channel_id)
        if approval is None or approval.requester_id != message.author.id:
            return False
        if approval.agent != self.agent.name:
            return True
        runtime.memory_approval_handled.add(message.id)
        if command == "취소":
            store.cancel(channel_id)
            await message.channel.send(MEMORY_APPROVAL_CANCELLED)
            return True
        approval.status = "executing"
        store.save()
        try:
            memory = runtime.memory
            ids = [
                memory_id for memory_id in approval.memory_ids
                if memory is not None and (row := memory.get(memory_id)) and not row["uploaded"]
            ]
            sync = approval.sync or approval.agent in store.sync_needed
            if not ids and not sync:
                result = MEMORY_APPROVAL_EMPTY
            else:
                url = await publish_persona_memory_file(
                    runtime, approval.agent, ids, "업로드" if ids else "원본 삭제 반영"
                )
                store.sync_needed.discard(approval.agent)
                result = MEMORY_APPROVAL_SAME if url is None else MEMORY_APPROVAL_DONE.format(url=url)
            approval.status = "done"
            store.save()
        except (ValueError, aiohttp.ClientError) as exc:
            # 외부 응답이 불명확할 수 있으므로 자동 재호출하지 않는 실패 상태로 남긴다.
            approval.status = "failed"
            store.save()
            result = (
                github_error_text(exc)
                if isinstance(exc, aiohttp.ClientResponseError)
                else f"기억을 올리지 못했습니다: {exc}"
            )
        await message.channel.send(result)
        return True

    # 채널 전체 삭제 명령과 그 확인 및 취소를 모델 호출 없이 처리한다. 처리한 메시지는 채팅 기록에 넣지 않는다.
    # 모든 봇이 같은 판정으로 메시지를 소비하고, 실제 처리와 안내는 처리 봇(min(runtime.clients)) 하나만 한다.
    async def _handle_chat_wipe_command(self, message: discord.Message) -> bool:
        # 봇 메시지와 미등록 채널을 건너뛴다.
        runtime = self.runtime
        if message.author.bot or not is_registered_agent_channel(message.channel, runtime.config):
            return False

        # 멘션을 뗀 명령과 처리 담당 여부를 구한다.
        command = MENTION.sub("", message.content).strip()
        channel_id = message.channel.id
        handler = self.agent.name == min(runtime.clients, default=self.agent.name)

        # 지우기 명령이면 담당 봇이 확인 요청을 보낸다.
        if command == CHAT_WIPE_COMMAND:
            if handler:
                runtime.chat_wipe_requests[channel_id] = {
                    "user_id": message.author.id,
                    "expires_at": message.created_at.timestamp() + CHAT_WIPE_TIMEOUT_SECONDS,
                }
                await message.channel.send(CHAT_WIPE_PROMPT)
            return True

        # 이미 처리한 메시지와 요청자가 다른 확인 명령은 건너뛴다.
        if message.id in runtime.chat_wipe_handled:
            return True
        request = runtime.chat_wipe_requests.get(channel_id)
        if (
            request is None
            or command not in {CHAT_WIPE_CONFIRM, CHAT_WIPE_CANCEL}
            or request["user_id"] != message.author.id
        ):
            return False

        # 확인 시간이 지났으면 요청을 지운다.
        if message.created_at.timestamp() > request["expires_at"]:
            if handler and runtime.chat_wipe_requests.get(channel_id) is request:
                runtime.chat_wipe_requests.pop(channel_id, None)
            return False

        # 담당 봇이 아니면 소비만 한다.
        if not handler:
            return True

        # 요청을 소비하고 취소면 취소 안내를 보낸다.
        runtime.chat_wipe_handled.add(message.id)
        runtime.chat_wipe_requests.pop(channel_id, None)
        if command == CHAT_WIPE_CANCEL:
            await message.channel.send(CHAT_WIPE_CANCELLED)
            return True

        # 채널 기록을 지우고 완료 안내를 보낸다.
        async with runtime.chat_lock_for(channel_id):
            removed = runtime.wipe_channel(channel_id)
        log.info("chat wipe channel=%s user=%s removed_history=%s", channel_id, message.author.id, removed)
        await message.channel.send(CHAT_WIPE_DONE)
        return True

    # 에이전트 채널에서 들어온 메시지를 처리한다.
    async def on_message(self, message: discord.Message) -> None:
        # 봇 사용자 정보가 있는지 확인한다.
        assert self.user is not None

        # 투표 결과 메시지는 작성자가 봇일 수 있어 봇 및 자기 메시지 필터보다 먼저 처리한다.
        if message.type == getattr(discord.MessageType, "poll_result", None):
            await self._handle_poll_result(message)
            return
        # 처리 봇 자신의 글도 archive 기억이 되도록 자기 메시지 필터보다 먼저 기록한다.
        if self._is_memory_handler():
            capture_archive_message(self.runtime, message)
        if message.author.id == self.user.id:
            return
        if not self._allowed(message):
            return

        # 명령 메시지를 해당 처리기로 넘기고 처리되면 끝낸다.
        if await self._handle_approval_conflict(message):
            return
        if await self._handle_archive_command(message):
            return
        if await self._handle_memory_approval(message):
            return
        if await self._handle_chat_wipe_command(message):
            return

        # 등록 채널의 사용자 메시지는 대화 응답 대상인지 판정한다.
        if is_registered_agent_channel(message.channel, self.runtime.config):
            if message.author.bot:
                return
            self.runtime.chat_last_message[message.channel.id] = message
            self.runtime.chat_last_user_message[message.channel.id] = message
            self.runtime.chat_last_user_id[message.channel.id] = message.author.id
            await self.runtime.ready.wait()
            if await self._chat_target_name(message) != self.agent.name:
                return
            if not self.runtime.claim_user_message(message.id):
                log.info("chat duplicate callback ignored channel=%s message=%s", message.channel.id, message.id)
                return
            text = MENTION.sub("", message.content).strip()
            image_paths, documents = await save_attachments(message, AGENT_ROOT, self.runtime.config)
            if image_paths or any(doc.path is not None for doc in documents):
                register_saved_attachments(self.runtime, message.id, message.channel.id)
            if image_paths:
                async with message.channel.typing():
                    caption = await caption_images(image_paths, self.runtime.config.chat.model)
                if caption:
                    captions = self.runtime.image_captions
                    captions[message.id] = caption
                    while len(captions) > self.runtime.config.attachments.image_captions_max:
                        captions.popitem(last=False)
                text = build_image_trigger_text(text, caption, image_paths)
            if documents:
                document_text = build_document_trigger_text(documents, self.runtime.config.attachments.text_preview_chars)
                text = f"{text}\n{document_text}" if text else document_text
            if LINK_PATTERN.search(message.content):
                async with message.channel.typing():
                    preview = await fetch_link_previews(message, self.runtime.config.links)
                if preview:
                    text = f"{text}\n{preview}" if text else preview
            reply_label = await self._reply_target_label(message)
            if reply_label:
                text = f"{reply_label}\n{text}" if text else reply_label
            self.runtime.add_pending(message.channel.id, message.id, message.author.id)
            try:
                await run_chat_conversation(
                    self.runtime,
                    message.channel.id,
                    starter_name=self.agent.name,
                    seed=message.id,
                    trigger_speaker=message.author.display_name,
                    trigger_text=text or "(텍스트 없는 메시지)",
                    autonomous=False,
                    user_message=True,
                    trigger_message_id=message.id,
                )
            except asyncio.CancelledError:
                self.runtime.consume_pending(message.channel.id, message.id, "handler_cancelled")
                raise
            except Exception:
                log.exception("chat handling failed agent=%s channel=%s", self.agent.name, message.channel.id)
                # 대화 흐름을 깨지 않도록 작은 회색 안내 한 줄만 보낸다. 자세한 내용은 로그에 있다.
                try:
                    await message.channel.send(self.runtime.config.chat.failure_notice)
                except discord.HTTPException:
                    pass
            finally:
                if message.id in self.runtime.chat_pending_items.get(message.channel.id, {}):
                    self.runtime.consume_pending(message.channel.id, message.id, "handler_finished")
            return

    # 리액션 대상 메시지의 작성자 표시 이름을 반환한다.
    def _react_target_author(self, message: discord.Message) -> str:
        # 반응 대상 작성자의 표시 이름을 돌려준다.
        author_bot = self.runtime.bot_id_to_name().get(message.author.id)
        if author_bot is not None:
            return self.runtime.chat_display_name(author_bot)
        return message.author.display_name

    # 프롬프트에 넣을 리액션 대상 설명(작성자, 구분, 본문 앞부분)을 만든다.
    def _react_target_label(self, message: discord.Message | None) -> str:
        # 메시지가 없으면 없음 표시를 반환한다.
        if message is None:
            return "없음"

        # 작성자 종류(자기 자신, 캐릭터, 사용자)를 가른다.
        assert self.user is not None
        if message.author.id == self.user.id:
            kind = "네 메시지, 리액션 불가"
        elif message.author.id in set(self.runtime.roster.values()):
            kind = "캐릭터 메시지"
        else:
            kind = "사용자 메시지"

        # 본문 일부와 종류로 대상 문장을 만든다.
        snippet = message.content.strip() or "(텍스트 없음)"
        return f"{self._react_target_author(message)}의 메시지 「{snippet}」 ({kind})"

    # 한 캐릭터의 채팅 발언 한 턴을 만들어 채널에 보내고, 다음 화자 이름을 반환한다.
    async def _handle_chat_turn(
        self,
        channel_id: int,
        *,
        turn_index: int,
        turn_limit: int,
        autonomous: bool,
        reaction_trigger: bool = False,
        user_trigger_message_id: int | None = None,
    ) -> str | None:
        # 런타임 준비를 기다리고 채널을 가져온다.
        await self.runtime.ready.wait()
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        # chat_channels는 채팅 프롬프트, 그 외 에이전트 채널은 작업 프롬프트로 답한다.
        is_chat = is_chat_channel(channel, self.runtime.config)
        if is_chat:
            self._refresh_chat_prompt()
        else:
            self._refresh_request_prompt()

        # 채팅 채널과 작업 채널에 맞는 대화 기록을 만든다.
        if is_chat:
            history = await self.runtime.render_speaker_history(channel_id, self.agent.name)
        else:
            history = self.runtime.render_chat_history(channel_id)

        # 턴 종류에 맞는 지시 문장을 고른다.
        if autonomous and turn_index == 1:
            turn_instruction = (
                "사용자 입력 없이 네가 먼저 가볍게 화제를 꺼내라. "
                "거창한 주제나 업무 지시가 아니라 평범한 채팅으로 시작한다."
            )
        elif reaction_trigger:
            turn_instruction = (
                "이번 턴은 리액션이 트리거다. "
                "할 말이 있으면 캐릭터의 말로 짧게 반응한다. "
                "다른 사람 메시지에 이모지로만 반응하려면 [[react:<이모지>]] 줄과 [[next:stop]] 줄만 출력한다. "
                "반응할 것이 없으면 [[next:stop]] 한 줄만 출력한다."
            )
        else:
            turn_instruction = ""
        # 자율 채팅은 주제가 있는 채널에서만 웹 검색을 쓰고, 화제를 그 주제 범위에 맞춘다.
        auto_topic = self.runtime.config.chat_topics.get(channel_id) if autonomous else None
        if autonomous and auto_topic is None:
            turn_instruction += (
                "\n이 채팅은 사용자 없이 이어지고 웹 검색 도구가 없다. "
                "화제는 일상, 취향, 이 채널에 오간 대화처럼 지금 아는 것만으로 말할 수 있는 것에서 고른다."
            )
        elif auto_topic is not None:
            if turn_index == 1:
                turn_instruction += f"\n웹 검색으로 이 채널의 주제({auto_topic})에 관한 최근 소식을 하나 찾아 가볍게 꺼낸다."
            turn_instruction += f"\n이 채널은 다음 주제를 이야기하는 곳이라 화제는 이 범위 안에서 고른다: {auto_topic}"

        # 채널 정보와 반응 대상 정보를 정리한다.
        continuation_rule = ""
        react_target = self.runtime.chat_last_message.get(channel_id)
        react_target_label = self._react_target_label(react_target)
        if isinstance(channel, discord.Thread) and channel.parent is not None:
            channel_label = f"#{channel.parent.name} 채널의 스레드 '{channel.name}'"
        else:
            channel_label = f"#{channel.name}"
        # 알림 시각 계산에 현재 시각이 필요한 assistant에만 넣는다. 도구 호출 한 번을 줄여 턴 한도를 지킨다.
        now_line = ""
        if self.agent.role == "assistant":
            reminders = self.runtime.config.reminders
            now = reminder_now(reminders)
            now_line = f"현재 시각: {now:%Y-%m-%d} ({WEEKDAY_NAMES[now.weekday()]}) {now:%H:%M} ({reminders.tz_label})\n"
        summary = getattr(self.runtime, "chat_summaries", {}).get(channel_id) or "(없음)"
        guild_id = getattr(channel.guild, "id", None)
        memory_query = memory_query_text(self.runtime, channel_id)
        speaker_tone = ""
        style_rules = ""
        if is_chat:
            style_rules = read_style_rules(RULES_ROOT)
            tone = read_tone_guide(canonical_role_dir(RULES_ROOT, self.agent.role) / "SOUL.md")
            name = self.agent.korean_name
            speaker_tone = f"{name}의 Tone Guide:\n{tone}"
        turn_template = render_variant(
            TURN_PROMPT_PATH.read_text(encoding="utf-8").strip(), "chat" if is_chat else "request"
        )
        prompt = fill_template(turn_template, {
            "$channel_label": channel_label, "$guild_name": channel.guild.name,
            "$now_line": now_line.rstrip(), "$summary": summary, "$history": history,
            "$memory": render_prompt_memories(self.runtime, guild_id, memory_query),
            "$persona_memory": render_prompt_persona_events(
                self.runtime, guild_id, self.agent.name, memory_query
            ),
            "$react_target_label": react_target_label, "$turn_limit": str(turn_limit),
            "$turn_index": str(turn_index), "$turn_instruction": turn_instruction,
            "$continuation_rule": continuation_rule, "$speaker_tone": speaker_tone,
            "$style_rules": style_rules,
        })

        # 이번 턴에 쓸 도구와 MCP 서버를 정한다.
        base_tools = [
            tool for tool in self.agent.tools
            if not (autonomous and auto_topic is None and tool in WEB_TOOL_NAMES)
        ]
        mcp_servers: dict[str, Any] = {}
        if (
            self.agent.role == "documenter"
            and self.runtime.config.archive_repository is not None
            and is_archive_thread(channel, self.runtime.config)
        ):
            base_tools = [*base_tools, *ARCHIVE_TOOL_NAMES]
            mcp_servers = {
                "archive": build_archive_server(
                    self.runtime.config.archive_repository,
                    self.runtime.archive_workflow,
                    channel.id,
                    self.runtime.chat_last_user_id.get(channel.id, 0),
                )
            }
        builders = [build_server_channels_server]
        role_builder = ROLE_TOOL_BUILDERS.get(self.agent.role)
        if role_builder is not None:
            builders.append(role_builder)
        for builder in builders:
            built = builder(channel, self.runtime, self.agent)
            if built is None:
                continue
            server_name, tool_names, server = built
            base_tools = [*base_tools, *tool_names]
            mcp_servers[server_name] = server
        # 장기기억 도구는 모든 역할에 붙고, 저장 및 업로드 대기 허용 여부는 이번 대화의 사용자 메시지로 정한다.
        built = build_memory_server(
            channel, self.runtime, self.agent, user_trigger_message_id,
            self.runtime.chat_last_user_id.get(channel.id),
        )
        if built is not None:
            server_name, tool_names, server = built
            base_tools = [*base_tools, *tool_names]
            mcp_servers[server_name] = server

        # 모델과 시스템 프롬프트를 정한다.
        model, effort = self.runtime.config.chat_model_for(channel)
        persona_settings = render_persona_settings_block(self.runtime, guild_id, self.agent.name)
        # 채팅 채널은 Claude Code preset 없이 채팅 프롬프트 문자열만 보낸다.
        system_prompt: str | dict[str, str] = (
            self._chat_prompt + persona_settings
            if is_chat
            else {
                "type": "preset",
                "preset": "claude_code",
                "append": self._request_prompt + persona_settings,
            }
        )
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            tools=base_tools,
            allowed_tools=base_tools,
            mcp_servers=mcp_servers,
            cwd=str(AGENT_ROOT),
            permission_mode="default",
            hooks=read_hooks(self.agent.name),
            setting_sources=[],
            strict_mcp_config=True,
            max_turns=self.runtime.config.chat.sdk_max_turns,
            model=model,
            effort=effort,
            stderr=log_sdk_stderr,
            max_buffer_size=10 * 1024 * 1024,
        )

        # 응답을 요청하고 결과와 도구 사용 기록을 모은다.
        result = ""
        result_subtype: str | None = None
        result_turns: int | None = None
        tool_uses: dict[str, tuple[str, list[str]]] = {}
        evidence_records: list[str] = []
        async with channel.typing():
            async for sdk_message in query(prompt=prompt, options=options):
                collect_tool_evidence(sdk_message, tool_uses, evidence_records)
                if isinstance(sdk_message, ResultMessage):
                    result = getattr(sdk_message, "result", "") or ""
                    result_subtype = getattr(sdk_message, "subtype", None)
                    result_turns = getattr(sdk_message, "num_turns", None)
                    log_token_usage(
                        "chat", sdk_message, agent=self.agent.name, channel=channel_id, model=model,
                    )

        # 생성하는 동안 사용자 메시지가 새로 왔으면 그 메시지를 못 본 응답이므로 보내지 않는다.
        if self.runtime.chat_pending.get(channel_id, 0) > 0:
            set_chat_turn_reason(self.runtime, channel_id, "pending_discard")
            pending_ids = sorted(self.runtime.chat_pending_items.get(channel_id, {}))
            log.info(
                "chat turn discarded reason=pending_discard agent=%s channel=%s pending_messages=%s",
                self.agent.name, channel_id, pending_ids,
            )
            return "stop"

        # 응답에서 제어 줄을 분리한다.
        try:
            text, next_name, react_emoji, user_wait = parse_chat_controls(result)
        except ValueError as exc:
            set_chat_turn_reason(self.runtime, channel_id, "invalid_control")
            log.warning(
                "chat response control invalid agent=%s channel=%s subtype=%s turns=%s: %s",
                self.agent.name, channel_id, result_subtype, result_turns, exc,
            )
            return None

        # 전송 실패 시에도 판단 대기를 보존하여 다른 진입 경로의 종속 진행을 막는다.
        if user_wait:
            self.runtime.chat_user_wait[channel_id] = {
                "reason": "user_decision", "agent": self.agent.name,
            }
            save_chat_state(channel_id, self.runtime)
        text = suppress_handoff_body(text, next_name)
        set_chat_turn_reason(self.runtime, channel_id, "model_stop" if next_name == "stop" else "continue")

        # 반응 요청이 있으면 대상 메시지에 반응을 단다.
        react_record: str | None = None
        if react_emoji:
            last_message = self.runtime.chat_last_message.get(channel_id)
            if last_message is None:
                react_status = "대상 없음"
                log.warning("reaction skipped: no target agent=%s channel=%s", self.agent.name, channel_id)
            elif last_message.author.id == self.user.id:
                react_status = "자기 메시지라 건너뜀"
                log.warning("reaction skipped: own message agent=%s channel=%s", self.agent.name, channel_id)
            else:
                # 저장된 Message는 다른 봇 클라이언트가 만든 객체일 수 있다.
                # 그 객체로 요청하면 그 봇의 토큰이 쓰이므로, 이 봇의 채널에서 부분 메시지를 만든다.
                target = channel.get_partial_message(last_message.id)
                try:
                    await target.add_reaction(react_emoji)
                    react_status = "성공"
                except (discord.HTTPException, TypeError):
                    react_status = "실패"
                    log.warning(
                        "failed to add reaction %r agent=%s channel=%s",
                        react_emoji, self.agent.name, channel_id,
                    )
            target_author = self._react_target_author(last_message) if last_message else "없음"
            react_record = f"({target_author} 메시지에 {react_emoji} 리액션: {react_status})"

        # 본문도 리액션도 없으면 반응하지 않은 것으로 보고 채널과 기록에 아무것도 남기지 않는다.
        if not text and not react_emoji:
            log.warning(
                "chat turn empty agent=%s channel=%s subtype=%s turns=%s raw=%r",
                self.agent.name, channel_id, result_subtype, result_turns, result[:200],
            )
            if evidence_records:
                evidence = f"{EVIDENCE_HEADER}\n" + render_tool_evidence(evidence_records)
                self.runtime.append_chat(channel_id, self.agent.name, evidence)
            return next_name

        # 응답 본문을 나눠 전송한다.
        if text:
            sent = None
            first_id = None
            sent_ids: list[int] = []
            for chunk in split_message(text):
                sent = await channel.send(chunk)
                first_id = first_id or sent.id
                sent_ids.append(sent.id)
            if sent is not None:
                self.runtime.chat_last_message[channel_id] = sent
            record_parts = [text]
            if react_record:
                record_parts.append(react_record)
            record = "\n".join(record_parts)
            self.runtime.append_chat(
                channel_id, self.agent.name, record, first_id, message_ids=sent_ids,
            )
        else:
            record = react_record or ""
            self.runtime.append_chat(channel_id, self.agent.name, record)
        if evidence_records:
            # 긴 작업 답변이 line_max_chars에 잘려도 실제 도구 및 URL 기록은 별도 항목으로 보존한다.
            evidence = f"{EVIDENCE_HEADER}\n" + render_tool_evidence(evidence_records)
            self.runtime.append_chat(channel_id, self.agent.name, evidence)
        return next_name


# 설정과 에이전트 파일을 검증하고, 모든 Discord 봇과 채팅 스케줄러를 함께 실행한다.
async def main() -> None:
    # 설정 파일이 없으면 안내하며 종료한다.
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"{CONFIG_PATH}가 없습니다. config.example.json을 config.json으로 복사해 값을 채우세요."
        )

    # 설정 파일을 읽는다.
    config = Config.load(CONFIG_PATH)

    # 공통 지침과 채팅 프롬프트 파일이 있는지 확인한다.
    if not (RULES_ROOT / COMMON_RULES_FILE).is_file():
        raise SystemExit(f"{RULES_ROOT / COMMON_RULES_FILE}가 없습니다.")
    if not CHAT_PROMPT_PATH.is_file():
        raise SystemExit(f"{CHAT_PROMPT_PATH}가 없습니다.")

    # 지침 폴더에서 에이전트 목록을 찾는다.
    known_agents = discover_agents(RULES_ROOT)
    if not known_agents:
        raise SystemExit(f"{RULES_ROOT / '.claude' / 'agents'}에 에이전트 폴더가 없습니다.")

    # 역할마다 지침과 SOUL 파일이 있는지 확인한다.
    for role in known_agents:
        role_dir = canonical_role_dir(RULES_ROOT, role)
        for filename in (ROLE_RULES_FILE, ROLE_MAIN_FILE, "SOUL.md"):
            if not (role_dir / filename).is_file():
                raise SystemExit(f"{role_dir / filename}가 없습니다.")

    # 설정한 에이전트의 역할과 이름이 유효한지 확인한다.
    if not config.agents:
        raise SystemExit("agents에 실행할 에이전트를 하나 이상 등록하세요.")
    seen_roles: set[str] = set()
    seen_names: set[str] = set()
    for agent in config.agents:
        expected_name = known_agents.get(agent.role)
        if expected_name is None:
            raise SystemExit(f"알 수 없는 내부 에이전트 ID입니다: {agent.role}")
        if agent.name != expected_name:
            raise SystemExit(
                f"{agent.role} 호출 이름은 {expected_name} 이어야 합니다."
            )
        if agent.role in seen_roles or agent.name in seen_names:
            raise SystemExit(f"중복 등록된 에이전트입니다: {agent.role}")
        seen_roles.add(agent.role)
        seen_names.add(agent.name)

    # 런타임을 만들고 기억 저장소를 연다.
    runtime = Runtime(config=config)
    # 기억 저장소는 채팅 기록을 불러오기 전에 연다.
    runtime.memory = MemoryStore.open(MEMORY_DB_PATH)
    try:
        os.chmod(MEMORY_DB_PATH, 0o600)
    except OSError:
        pass
    # 스레드(포럼 게시글) ID는 설정에 없으므로, 등록 채널과 함께 저장 파일 목록에서 찾아 불러온다.
    state_ids = config.request_channels | config.chat_channels
    if CHAT_STATE_DIR.is_dir():
        state_ids |= {int(path.stem) for path in CHAT_STATE_DIR.glob("*.json") if path.stem.isdigit()}
    for channel_id in sorted(state_ids):
        load_chat_state(channel_id, runtime)
    load_reminders(runtime)
    bots = [AgentBot(agent, runtime) for agent in config.agents]
    runtime.clients = {bot.agent.name: bot for bot in bots}
    await asyncio.gather(
        run_chat_scheduler(runtime),
        run_reminder_scheduler(runtime),
        *(bot.start(bot.agent.token) for bot in bots),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
