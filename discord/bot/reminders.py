"""알림 도구를 구성하고 알림 스케줄러를 실행한다."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import discord
from claude_agent_sdk import create_sdk_mcp_server, tool

from config import HERE, WEEKDAY_NAMES, AgentConfig, RemindersConfig, log, tool_text

if TYPE_CHECKING:
    from bot import Runtime


# 알림 앞머리 문구가 붙어도 Discord 메시지 한도(2000자) 안에 들어가게 한다.
REMINDER_TEXT_MAX = 1500
REMINDERS_PATH = HERE / "reminders.json"
REMINDER_TOOL_NAMES = [
    "mcp__reminder__reminder_set",
    "mcp__reminder__reminder_repeat",
    "mcp__reminder__reminder_list",
    "mcp__reminder__reminder_cancel",
]


# 알림 설정 시간대의 현재 시각.
def reminder_now(reminders: RemindersConfig) -> datetime:
    return datetime.now(reminders.tz)


# 알림 목록을 파일에 원자적으로 저장한다.
def save_reminders(runtime: "Runtime") -> None:
    # 저장할 데이터를 임시 파일에 쓰고 권한을 좁힌다.
    data = {"next_id": runtime.reminder_next_id, "reminders": runtime.reminders}
    tmp_path = REMINDERS_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass

    # 임시 파일로 원본을 교체한다.
    os.replace(tmp_path, REMINDERS_PATH)


# 저장된 알림 목록을 불러온다. 파일이 없거나 손상됐으면 빈 목록으로 시작한다.
def load_reminders(runtime: "Runtime") -> None:
    # 알림 파일이 없으면 건너뛴다.
    if not REMINDERS_PATH.is_file():
        return

    # 파일을 읽는다. 손상됐으면 로그를 남기고 건너뛴다.
    try:
        data = json.loads(REMINDERS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("알림 파일이 손상되어 건너뜁니다: %s", REMINDERS_PATH)
        return

    # 알림 목록과 다음 번호를 복원한다.
    runtime.reminders = list(data.get("reminders", []))
    runtime.reminder_next_id = int(data.get("next_id", 1))


# '매일', '평일', '주말', '월,수,금' 같은 요일 표현을 요일 번호(월=0) 목록으로 바꾼다. 해석할 수 없으면 None이다.
def parse_reminder_days(raw: str) -> list[int] | None:
    # 매일, 평일, 주말 같은 이름은 요일 번호 목록으로 바꾼다.
    raw = raw.strip()
    if raw == "매일":
        return list(range(7))
    if raw == "평일":
        return list(range(5))
    if raw == "주말":
        return [5, 6]

    # 요일 이름 목록을 읽어 번호로 바꾼다. 잘못된 이름이 있으면 None이다.
    days: set[int] = set()
    for part in re.split(r"[,\s]+", raw):
        if not part:
            continue
        name = part.removesuffix("요일")
        if len(name) != 1 or name not in WEEKDAY_NAMES:
            return None
        days.add(WEEKDAY_NAMES.index(name))
    return sorted(days) or None


# 'HH:MM' 형식의 시각을 검사하고 두 자리로 맞춘다. 잘못된 형식이면 None이다.
def parse_reminder_time(raw: str) -> str | None:
    # HH:MM 형식과 범위를 검사해 정규화한 시각을 돌려준다.
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw.strip())
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


# 반복 요일 목록을 매일 및 매주 문구로 바꾼다.
def format_reminder_days(days: list[int]) -> str:
    # 요일 목록을 매일 또는 요일 이름 문구로 만든다.
    if days == list(range(7)):
        return "매일"
    return "매주 " + ",".join(WEEKDAY_NAMES[day] for day in days)


# 알림 하나를 목록 표시 문구로 바꾼다.
def format_reminder(reminder: dict[str, Any]) -> str:
    # 알림 종류에 맞는 시점 문구를 만들어 한 줄로 돌려준다.
    if reminder["kind"] == "repeat":
        when = f"반복 {format_reminder_days(reminder['days'])} {reminder['time']}"
    else:
        at = datetime.fromisoformat(reminder["at"])
        when = f"한 번 {at:%Y-%m-%d} ({WEEKDAY_NAMES[at.weekday()]}) {at:%H:%M}"
    return f"#{reminder['id']} {when} {reminder['text']}"


# assistant 역할 전용 알림 도구를 만든다. 알림은 reminders.json에 저장되어 재시작해도 유지된다.
def build_reminder_server(
    channel: discord.abc.Messageable, runtime: "Runtime", agent: AgentConfig
) -> tuple[str, list[str], Any] | None:
    # 알림 설정을 꺼낸다.
    settings = runtime.config.reminders

    # 새 알림에 ID와 채널을 붙여 저장한다.
    def add_reminder(reminder: dict[str, Any]) -> dict[str, Any]:
        # 알림에 ID를 붙여 목록에 추가하고 저장한다.
        reminder = {"id": runtime.reminder_next_id, "channel_id": channel.id, "agent": agent.name, **reminder}
        runtime.reminder_next_id += 1
        runtime.reminders.append(reminder)
        save_reminders(runtime)
        return reminder

    # 알림 내용 길이를 검사하고 오류 문구를 반환한다.
    def check_text(text: str) -> str | None:
        # 알림 내용 길이를 검사해 오류 문구를 돌려준다.
        if not text or len(text) > REMINDER_TEXT_MAX:
            return f"알림 내용은 1~{REMINDER_TEXT_MAX}자여야 합니다."
        return None

    # 몇 분 뒤 한 번 울릴 알림을 예약한다.
    @tool(
        "reminder_set",
        f"지정한 분 뒤에 채널로 알림을 한 번 보낸다. minutes는 1~{settings.max_minutes}이다.",
        {"minutes": int, "text": str},
    )
    async def reminder_set(args: dict[str, Any]) -> dict[str, Any]:
        # 분과 문구를 확인한다.
        minutes = args.get("minutes")
        text = str(args.get("text", "")).strip()
        if not isinstance(minutes, int) or not 1 <= minutes <= settings.max_minutes:
            return tool_text(f"minutes는 1~{settings.max_minutes}이어야 합니다.")
        error = check_text(text)
        if error:
            return tool_text(error)

        # 예약 시각을 계산해 일회 알림을 등록한다.
        at = (reminder_now(settings) + timedelta(minutes=minutes)).replace(second=0, microsecond=0)
        reminder = add_reminder({"kind": "once", "at": at.isoformat(), "text": text})
        return tool_text(f"알림을 예약했습니다: {format_reminder(reminder)} ({settings.tz_label})")

    # 요일과 시각으로 반복 알림을 예약한다.
    @tool(
        "reminder_repeat",
        f"정해진 요일과 시각({settings.tz_label})마다 채널로 알림을 보낸다. "
        "days는 '매일', '평일', '주말' 또는 '월,수,금'처럼 쉼표로 구분한 요일이고, time은 'HH:MM'이다.",
        {"days": str, "time": str, "text": str},
    )
    async def reminder_repeat(args: dict[str, Any]) -> dict[str, Any]:
        # 요일, 시각, 문구를 확인한다.
        days = parse_reminder_days(str(args.get("days", "")))
        if days is None:
            return tool_text("days는 '매일', '평일', '주말' 또는 '월,수,금' 형식이어야 합니다.")
        time_text = parse_reminder_time(str(args.get("time", "")))
        if time_text is None:
            return tool_text("time은 00:00~23:59 사이의 'HH:MM' 형식이어야 합니다.")
        text = str(args.get("text", "")).strip()
        error = check_text(text)
        if error:
            return tool_text(error)

        # 반복 알림을 등록한다.
        reminder = add_reminder(
            {"kind": "repeat", "days": days, "time": time_text, "text": text, "last_fired": None}
        )
        return tool_text(f"반복 알림을 예약했습니다: {format_reminder(reminder)} ({settings.tz_label})")

    # 현재 채널의 예약 알림을 보여 준다.
    @tool("reminder_list", "현재 시각과 현재 채널의 알림 목록을 보여 준다.", {})
    async def reminder_list(args: dict[str, Any]) -> dict[str, Any]:
        # 현재 시각과 채널의 알림 목록을 문장으로 만든다.
        now = reminder_now(settings)
        lines = [f"현재 시각: {now:%Y-%m-%d} ({WEEKDAY_NAMES[now.weekday()]}) {now:%H:%M} ({settings.tz_label})"]
        items = [format_reminder(r) for r in runtime.reminders if r["channel_id"] == channel.id]
        lines += items or ["예약된 알림이 없습니다."]
        return tool_text("\n".join(lines))

    # 현재 채널의 알림 하나를 ID로 취소한다.
    @tool("reminder_cancel", "현재 채널의 알림을 번호로 취소한다.", {"id": int})
    async def reminder_cancel(args: dict[str, Any]) -> dict[str, Any]:
        # ID에 맞는 채널의 알림을 찾아 취소한다.
        reminder_id = args.get("id")
        for reminder in runtime.reminders:
            if reminder["id"] == reminder_id and reminder["channel_id"] == channel.id:
                runtime.reminders.remove(reminder)
                save_reminders(runtime)
                return tool_text(f"알림을 취소했습니다: {format_reminder(reminder)}")
        return tool_text(f"현재 채널에 #{reminder_id} 알림이 없습니다.")

    # 도구를 묶어 reminder 서버를 반환한다.
    return "reminder", list(REMINDER_TOOL_NAMES), create_sdk_mcp_server(
        name="reminder",
        version="1.0.0",
        tools=[reminder_set, reminder_repeat, reminder_list, reminder_cancel],
    )


# 알림 하나를 등록한 봇 계정으로 채널에 보낸다. 허용 사용자를 멘션해 푸시 알림이 가게 한다.
async def send_reminder(runtime: Runtime, reminder: dict[str, Any], late_at: datetime | None) -> None:
    # 알림을 보낼 에이전트 클라이언트를 구한다.
    client = runtime.clients.get(reminder["agent"])
    if client is None:
        log.warning("reminder agent missing id=%s agent=%s", reminder["id"], reminder["agent"])
        return

    # 채널을 구한다. 구하지 못하면 로그를 남기고 건너뛴다.
    channel_id = reminder["channel_id"]
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except discord.HTTPException as exc:
            log.warning("reminder channel fetch failed id=%s channel=%s: %s", reminder["id"], channel_id, exc)
            return

    # 허용된 사용자를 멘션하는 접두어를 만든다.
    mention = " ".join(f"<@{user_id}>" for user_id in sorted(runtime.config.allowed_user_ids))
    prefix = f"{mention} " if mention else ""

    # 늦은 알림인지에 따라 본문을 만든다.
    text = reminder["text"]
    if late_at is not None:
        body = f"⏰ 늦은 알림 (예정 {late_at:%m-%d %H:%M}): {text}"
    else:
        body = f"⏰ 알림: {text}"

    # 전송하고 채팅 기록에 남긴다.
    try:
        sent = await channel.send(prefix + body)
    except discord.HTTPException as exc:
        log.warning("reminder send failed id=%s agent=%s: %s", reminder["id"], reminder["agent"], exc)
        return
    runtime.append_chat(channel_id, reminder["agent"], f"(알림) {text}", sent.id)


# 지금 울릴 알림을 골라 보낸다. 중복 발송을 막으려고 보내기 전에 상태를 먼저 저장한다.
async def fire_due_reminders(runtime: Runtime, now: datetime) -> None:
    # 시각이 된 알림을 골라 보낼 목록을 만든다.
    to_send: list[tuple[dict[str, Any], datetime | None]] = []
    for reminder in list(runtime.reminders):
        if reminder["kind"] == "once":
            at = datetime.fromisoformat(reminder["at"])
            if at > now:
                continue
            runtime.reminders.remove(reminder)
            late = now - at > timedelta(minutes=runtime.config.reminders.late_minutes)
            to_send.append((reminder, at if late else None))
            continue
        if now.weekday() not in reminder["days"]:
            continue
        hour, minute = map(int, reminder["time"].split(":"))
        slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # 봇이 꺼져 있어 유예 시간이 지난 회차는 건너뛰고 다음 회차를 기다린다.
        if not slot <= now < slot + timedelta(minutes=runtime.config.reminders.repeat_grace_minutes):
            continue
        if reminder.get("last_fired") == slot.isoformat():
            continue
        reminder["last_fired"] = slot.isoformat()
        to_send.append((reminder, None))

    # 보낼 알림이 없으면 끝내고 있으면 저장 후 전송한다.
    if not to_send:
        return
    save_reminders(runtime)
    for reminder, late_at in to_send:
        await send_reminder(runtime, reminder, late_at)


# 알림 파일을 주기적으로 확인해 시각이 된 알림을 보낸다.
async def run_reminder_scheduler(runtime: Runtime) -> None:
    # 런타임 준비를 기다린다.
    await runtime.ready.wait()

    # 주기마다 시각이 된 알림을 확인한다.
    while True:
        try:
            await fire_due_reminders(runtime, reminder_now(runtime.config.reminders))
        except Exception:
            log.exception("reminder check failed")
        await asyncio.sleep(runtime.config.reminders.check_seconds)
