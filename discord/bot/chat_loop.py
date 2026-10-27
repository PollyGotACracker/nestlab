"""채팅 대화와 채팅 스케줄러를 실행한다."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from config import log
from prompts import daily_chat_minutes
from memory import save_chat_state

if TYPE_CHECKING:
    from bot import Runtime


# 시작 화자부터 최대 턴수까지 채팅을 이어가며 각 턴을 순서대로 진행시킨다.
async def run_chat_conversation(
    runtime: Runtime,
    channel_id: int,
    *,
    starter_name: str,
    seed: str | int,
    trigger_speaker: str | None = None,
    trigger_text: str | None = None,
    autonomous: bool,
    single_turn: bool = False,
    user_message: bool = False,
    trigger_message_id: int | None = None,
) -> None:
    # 대화 식별자와 잠금을 만들고 잠금 대기를 기록한다.
    conversation_id = uuid.uuid4().hex
    lock = runtime.chat_lock_for(channel_id)
    task_id = id(asyncio.current_task())
    log.info(
        "chat lock wait conversation=%s channel=%s task=%s trigger=%s message=%s author=%s pending=%s",
        conversation_id, channel_id, task_id, "user_message" if user_message else ("autonomous" if autonomous else "event"),
        trigger_message_id, trigger_speaker, runtime.chat_pending.get(channel_id, 0),
    )
    try:
        await lock.acquire()
    except asyncio.CancelledError:
        log.info(
            "chat lock cancel conversation=%s channel=%s task=%s message=%s",
            conversation_id, channel_id, task_id, trigger_message_id,
        )
        raise
    try:
        waits = getattr(runtime, "chat_user_wait", {})
        # 실제 on_message가 등록한 사용자 입력만 판단 대기를 해제한다.
        pending_item = getattr(runtime, "chat_pending_items", {}).get(channel_id, {}).get(trigger_message_id)
        author_id = pending_item.get("author_id") if pending_item else None
        actual_user = (user_message and not autonomous and not single_turn
                       and trigger_message_id is not None and pending_item is not None
                       and pending_item.get("reason") == "user_message"
                       and type(author_id) is int and author_id > 0
                       and author_id not in set(getattr(runtime, "roster", {}).values()))
        if channel_id in waits:
            if not actual_user:
                return
            waits.pop(channel_id)
            save_chat_state(channel_id, runtime)
        log.info(
            "chat lock acquire conversation=%s channel=%s task=%s pending=%s",
            conversation_id, channel_id, task_id, runtime.chat_pending.get(channel_id, 0),
        )
        # on_message가 올려 둔 대기 수를 락을 잡은 시점에 내린다.
        if user_message and trigger_message_id is not None:
            if hasattr(runtime, "consume_pending"):
                runtime.consume_pending(channel_id, trigger_message_id, "lock_acquired")
            else:
                runtime.chat_pending[channel_id] = max(0, runtime.chat_pending.get(channel_id, 0) - 1)
        if trigger_speaker is not None and trigger_text is not None:
            runtime.append_chat(channel_id, trigger_speaker, trigger_text, trigger_message_id)

        # 사용자 메시지로 시작한 대화의 턴만 memory_save와 persona 업로드를 쓸 수 있다.
        user_trigger_message_id = (
            trigger_message_id
            if user_message and not autonomous and not single_turn and trigger_message_id is not None
            else None
        )
        # single_turn이면 시작 화자 한 번만 말하고 다른 화자에게 넘기지 않는다.
        if single_turn:
            turn_limit = 1
        else:
            chat_config = runtime.config.chat
            turn_limit = chat_config.max_turns
        current = starter_name

        # 발언 순서마다 발언자 클라이언트를 찾는다.
        for turn_index in range(1, turn_limit + 1):
            client = runtime.clients.get(current)
            if client is None:
                log.warning(
                    "chat end reason=missing_client conversation=%s channel=%s speaker=%s",
                    conversation_id, channel_id, current,
                )
                return

            # 다음 발언 전에 잠시 기다려 사용자가 읽고 끼어들 틈을 둔다.
            if turn_index > 1:
                for _ in range(runtime.config.chat.turn_delay_seconds):
                    if runtime.chat_pending.get(channel_id, 0) > 0:
                        log.info(
                            "chat end reason=pending_before_turn conversation=%s channel=%s pending_messages=%s",
                            conversation_id, channel_id, sorted(getattr(runtime, "chat_pending_items", {}).get(channel_id, {})),
                        )
                        return
                    await asyncio.sleep(1)
                if runtime.chat_pending.get(channel_id, 0) > 0:
                    log.info(
                        "chat end reason=pending_before_turn conversation=%s channel=%s pending_messages=%s",
                        conversation_id, channel_id, sorted(getattr(runtime, "chat_pending_items", {}).get(channel_id, {})),
                    )
                    return

            # 발언자의 한 턴을 실행한다.
            next_name = await client._handle_chat_turn(
                channel_id,
                turn_index=turn_index,
                turn_limit=turn_limit,
                autonomous=autonomous,
                reaction_trigger=single_turn,
                user_trigger_message_id=user_trigger_message_id,
            )
            if channel_id in getattr(runtime, "chat_user_wait", {}):
                return
            log.info(
                "chat turn conversation=%s channel=%s turn=%d/%d speaker=%s next=%s",
                conversation_id, channel_id, turn_index, turn_limit, current, next_name,
            )

            # 새 사용자 메시지가 락을 기다리면 이 대화를 끝내고 넘겨준다.
            if runtime.chat_pending.get(channel_id, 0) > 0:
                turn_reason = getattr(runtime, "chat_turn_reasons", {}).pop((channel_id, task_id), None)
                reason = "pending_discard" if turn_reason == "pending_discard" else "pending_after_turn"
                log.info(
                    "chat end reason=%s conversation=%s channel=%s pending_messages=%s",
                    reason, conversation_id, channel_id,
                    sorted(getattr(runtime, "chat_pending_items", {}).get(channel_id, {})),
                )
                return
            if turn_index >= turn_limit:
                getattr(runtime, "chat_turn_reasons", {}).pop((channel_id, task_id), None)
                log.info("chat end reason=turn_limit conversation=%s channel=%s", conversation_id, channel_id)
                return

            # 다음 발언자가 없거나 잘못됐는지 판정한다.
            invalid_next = (
                next_name is None
                or next_name == "stop"
                or next_name == current
                or next_name not in runtime.clients
            )
            if not invalid_next:
                getattr(runtime, "chat_turn_reasons", {}).pop((channel_id, task_id), None)
                current = next_name
                continue
            if next_name == "stop":
                reason = getattr(runtime, "chat_turn_reasons", {}).pop((channel_id, task_id), "model_stop")
            elif next_name is None:
                reason = getattr(runtime, "chat_turn_reasons", {}).pop((channel_id, task_id), "invalid_control")
            else:
                reason = "invalid_next"
            log.info(
                "chat end reason=%s conversation=%s channel=%s turn=%s next=%s",
                reason, conversation_id, channel_id, turn_index, next_name,
            )
            return
    finally:
        lock.release()


# chat_channels의 채널별로 정해진 하루치 랜덤 시각이 되면 자율 채팅을 시작한다.
async def run_chat_scheduler(runtime: Runtime) -> None:
    # 런타임 준비를 기다린다.
    await runtime.ready.wait()

    # 하루 기준 상태를 갱신하며 주기마다 반복한다.
    while True:
        now = datetime.now()
        day_key = now.date().isoformat()
        current_minute = now.hour * 60 + now.minute
        runtime.chat_auto_fired = {
            key for key in runtime.chat_auto_fired if key[1] == day_key
        }
        # 새 발언이 없는 채널도 요약 대기 시간(summary.flush_minutes)이 지나면 요약한다.
        for channel_id in list(runtime.chat_histories):
            runtime.maybe_schedule_summary(channel_id)

        # 채널마다 하루 대화 시각표를 만든다.
        for channel_id in sorted(runtime.config.chat_channels):
            sample_client = next(iter(runtime.clients.values()), None)
            if sample_client is not None and isinstance(
                sample_client.get_channel(channel_id), discord.ForumChannel
            ):
                continue
            slots = daily_chat_minutes(
                channel_id, now.date(), runtime.config.chat_per_day.get(channel_id, 0)
            )
            lock = runtime.chat_lock_for(channel_id)

            # 지난 시각표와 이미 실행한 시각표를 건너뛴다.
            for slot_index, slot_minute in enumerate(slots):
                key = (channel_id, day_key, slot_index)
                if key in runtime.chat_auto_fired:
                    continue
                if current_minute < slot_minute:
                    continue
                if current_minute > slot_minute:
                    runtime.chat_auto_fired.add(key)
                    continue

                # 이번 시각표를 실행한 것으로 기록한다.
                runtime.chat_auto_fired.add(key)
                if lock.locked():
                    continue

                # 첫 발언자를 고르고 클라이언트를 확인한다.
                starter_name = runtime.pick_chat_starter(
                    f"auto:{channel_id}:{day_key}:{slot_index}"
                )
                client = runtime.clients.get(starter_name)
                if client is None:
                    continue

                # 자율 대화를 실행한다.
                try:
                    await run_chat_conversation(
                        runtime,
                        channel_id,
                        starter_name=starter_name,
                        seed=f"auto:{channel_id}:{day_key}:{slot_index}",
                        autonomous=True,
                    )
                except Exception:
                    log.exception("auto chat start failed channel=%s", channel_id)

        # 다음 확인까지 기다린다.
        await asyncio.sleep(runtime.config.chat.scheduler_interval_seconds)
