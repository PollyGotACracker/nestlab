"""Discord 봇의 경로 및 상수와 설정 class를 정의하고 설정을 로딩한다."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

import discord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("agent_team")

# 채팅은 매 턴 채널 기록 전체로 새로 시작하므로 SDK 세션 기록 파일(~/.claude/projects)이 필요 없다.
# SDK가 실행하는 Claude Code는 이 환경 변수를 물려받아 세션 기록을 쓰지 않는다.
os.environ.setdefault("CLAUDE_CODE_SKIP_PROMPT_HISTORY", "1")

HERE = Path(__file__).resolve().parent
AGENT_ROOT = HERE.parent
TURN_PROMPT_PATH = HERE.parent / "prompts" / "TURN.md"
# 지침 파일 이름. 공통 지침은 저장소 루트, 역할 지침은 .claude/agents/<role>/, archive 지침은 Nodebase 저장소 루트에 있다.
COMMON_RULES_FILE = "AGENTS.global.md"
ROLE_RULES_FILE = "AGENTS.md"
ROLE_MAIN_FILE = "MAIN.md"
ARCHIVE_RULES_FILE = "AGENTS.md"
DISCORD_LIMIT = 2000
CHAT_STATE_DIR = HERE / "chat_state"
# Claude API가 받는 effort 값.
CHAT_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# archive 채널(포럼)의 기본 모델. 채널별 override가 없을 때만 chat.model 대신 쓴다.
ARCHIVE_DEFAULT_MODEL = "claude-opus-5-5"
# Discord 투표 한도(공식 문서 기준).
POLL_QUESTION_MAX = 300
POLL_ANSWER_MAX = 55
POLL_ANSWERS_MIN = 2
POLL_ANSWERS_MAX = 10
POLL_HOURS_MAX = 32 * 24
THREAD_NAME_MAX = 100
WEEKDAY_NAMES = "월화수목금토일"


# claude CLI 서브프로세스의 stderr 한 줄을 로그로 남긴다. 실패 원인을 나중에 찾을 수 있게 한다.
def log_sdk_stderr(line: str) -> None:
    log.warning("claude cli stderr: %s", line.rstrip())


# 모델 호출 1회의 ResultMessage 사용량과 비용을 로그 한 줄로 남긴다.
def log_token_usage(kind: str, result_message: Any, **context: Any) -> None:
    # 토큰 사용량과 비용을 로그에 남긴다.
    usage = getattr(result_message, "usage", None)
    usage_text = json.dumps(usage, ensure_ascii=False, sort_keys=True) if isinstance(usage, dict) else "none"
    context_text = " ".join(f"{key}={value}" for key, value in context.items())
    log.info(
        "token usage kind=%s %s usage=%s total_cost_usd=%s",
        kind, context_text, usage_text, getattr(result_message, "total_cost_usd", None),
    )


# 설정 파일에 정의된 에이전트 한 명(역할, 내부 이름, 표시 이름, 토큰, 허용 도구)을 나타낸다.
@dataclass
class AgentConfig:
    role: str
    name: str
    korean_name: str
    token: str
    tools: list[str]


# 에이전트 채널 동작에 필요한 설정값을 담는다. sdk_max_turns만 생략할 수 있다.
@dataclass(frozen=True)
class ChatConfig:
    model: str
    effort: str
    # 멘션 없는 사용자 메시지의 첫 화자를 고르는 모델. 설정 키를 생략하면 claude-haiku-5-5이다.
    router_model: str
    # Claude Agent SDK가 한 캐릭터의 응답 1회를 만드는 동안 허용하는 내부 도구 호출 턴 수.
    # max_turns(대화가 몇 번 오가는지)와는 다른 값이다.
    # 설정 키를 생략하면 None으로 SDK 턴 상한을 두지 않는다.
    sdk_max_turns: int | None
    # 대화 기록과 첨부 파일을 보관하는 시간.
    history_hours: int
    history_max_lines: int
    line_max_chars: int
    # 채팅 처리에 실패했을 때 채널에 보내는 안내. -# 은 Discord에서 작은 회색 글씨(subtext)로 표시된다.
    failure_notice: str
    scheduler_interval_seconds: int
    turn_delay_seconds: int
    max_turns: int


# 첨부 처리 설정값을 담는다.
@dataclass(frozen=True)
class AttachmentsConfig:
    text_max_kb: int
    text_preview_chars: int
    image_captions_max: int


# 링크 미리보기 설정값을 담는다.
@dataclass(frozen=True)
class LinksConfig:
    preview_retries: int
    preview_retry_seconds: int
    description_max_chars: int


# 알림 설정값을 담는다.
@dataclass(frozen=True)
class RemindersConfig:
    # 고정 오프셋을 쓴다. 시간대 데이터(tzdata)가 없는 환경에서도 동작한다.
    timezone_offset_hours: int
    max_days: int
    check_seconds: int
    # 봇이 꺼져 있던 동안 지난 반복 알림 회차는 이 시간 안에 켜졌을 때만 보낸다.
    repeat_grace_minutes: int
    # 한 번짜리 알림이 예정 시각보다 이만큼 넘게 늦으면 늦은 알림으로 표시한다.
    late_minutes: int

    # 알림 설정의 시간대.
    @property
    def tz(self) -> timezone:
        return timezone(timedelta(hours=self.timezone_offset_hours))

    # 프롬프트와 알림 문구에 쓰는 시간대 이름.
    @property
    def tz_label(self) -> str:
        return "한국 시간" if self.timezone_offset_hours == 9 else f"UTC{self.timezone_offset_hours:+d}"

    # 알림 예약 최대 기간(분).
    @property
    def max_minutes(self) -> int:
        return self.max_days * 24 * 60


# 도구가 한 번에 읽는 양을 정하는 설정값을 담는다.
@dataclass(frozen=True)
class ToolsConfig:
    channel_history_max: int
    server_channels_max: int


# config.json의 항목 이름과 설정 클래스. chat.sdk_max_turns와 chat.router_model 외의 키는 필수다.
SETTINGS_SECTIONS = {
    "chat": ChatConfig,
    "attachments": AttachmentsConfig,
    "links": LinksConfig,
    "reminders": RemindersConfig,
    "tools": ToolsConfig,
}


# 설정 항목들을 읽는다. 빠진 키는 모두 모아 한 번에 알린다.
def load_settings_sections(raw: dict[str, Any]) -> dict[str, Any]:
    # 섹션별로 항목 값을 읽는다. 없는 항목은 기본값을 쓰거나 누락으로 모은다.
    missing: list[str] = []
    values: dict[str, dict[str, Any]] = {}
    for section, cls in SETTINGS_SECTIONS.items():
        section_raw = raw.get(section)
        if not isinstance(section_raw, dict):
            section_raw = {}
        values[section] = {}
        for item in fields(cls):
            if item.name not in section_raw:
                if section == "chat" and item.name == "sdk_max_turns":
                    values[section][item.name] = None
                    continue
                if section == "chat" and item.name == "router_model":
                    values[section][item.name] = "claude-haiku-5-5"
                    continue
                missing.append(f"{section}.{item.name}")
                continue
            value = section_raw[item.name]
            values[section][item.name] = str(value) if item.type == "str" else int(value)

    # 누락된 키가 있으면 한꺼번에 알린다.
    if missing:
        raise ValueError("config에 없는 키: " + ", ".join(missing))

    # 섹션 객체를 만들고 effort 값을 확인한다.
    settings = {section: cls(**values[section]) for section, cls in SETTINGS_SECTIONS.items()}
    chat: ChatConfig = settings["chat"]
    if chat.effort not in CHAT_EFFORTS:
        raise ValueError(f"chat.effort는 {', '.join(CHAT_EFFORTS)} 중 하나여야 합니다.")

    # 항목마다 값의 범위를 확인한다.
    for section in settings:
        for item in fields(SETTINGS_SECTIONS[section]):
            value = getattr(settings[section], item.name)
            key = f"{section}.{item.name}"
            if item.type == "str":
                if not value.strip():
                    raise ValueError(f"{key}가 비어 있습니다.")
            elif key == "chat.sdk_max_turns" and value is None:
                continue
            elif key == "chat.turn_delay_seconds":
                if value < 0:
                    raise ValueError(f"{key}는 0 이상이어야 합니다.")
            elif key == "reminders.timezone_offset_hours":
                if not -12 <= value <= 14:
                    raise ValueError(f"{key}는 -12에서 14 사이여야 합니다.")
            elif value < 1:
                raise ValueError(f"{key}는 1 이상이어야 합니다.")
    return settings


# 채널 요약 설정값을 담는다. config의 summary 절은 선택이며 빠진 키는 기본값을 쓴다.
@dataclass(frozen=True)
class SummaryConfig:
    # 이 시간 안의 대화는 원문으로 보내고, 더 오래된 줄은 요약 대상이 된다.
    raw_hours: int = 2
    model: str = "claude-haiku-4-5"
    max_chars: int = 2000
    batch_min_lines: int = 30
    batch_max_lines: int = 150
    batch_max_chars: int = 20000
    flush_minutes: int = 60
    max_attempts: int = 3


# 장기기억 설정값을 담는다. config의 memory 절은 선택이며 빠진 키는 기본값을 쓴다.
@dataclass(frozen=True)
class MemoryConfig:
    prompt_max_items: int = 5
    prompt_max_chars: int = 1500
    search_max_results: int = 10
    persona_prompt_max_items: int = 5
    persona_prompt_max_chars: int = 1000
    persona_setting_max_chars: int = 1000


# 선택 설정 절 하나를 읽는다. 절이나 키가 없으면 기본값을 쓰고, 있는 값은 형식을 검사한다.
def load_optional_section(raw: dict[str, Any], section: str, cls: type) -> Any:
    # 섹션이 없으면 기본값 객체를 반환한다.
    section_raw = raw.get(section)
    if section_raw is None:
        return cls()
    if not isinstance(section_raw, dict):
        raise ValueError(f"{section}는 객체여야 합니다.")

    # 있는 항목만 읽어 값을 확인한다.
    values: dict[str, Any] = {}
    for item in fields(cls):
        if item.name not in section_raw:
            continue
        key = f"{section}.{item.name}"
        value = section_raw[item.name]
        if item.type == "str":
            value = str(value or "").strip()
            if not value:
                raise ValueError(f"{key}가 비어 있습니다.")
        else:
            value = int(value)
            if value < 1:
                raise ValueError(f"{key}는 1 이상이어야 합니다.")
        values[item.name] = value
    return cls(**values)


# 설정 객체에 summary 및 memory 절이 없으면(테스트의 간이 설정 포함) 기본값을 돌려준다.
def summary_settings(config: Any) -> SummaryConfig:
    return getattr(config, "summary", None) or SummaryConfig()


# 설정의 memory 절을 반환한다. 없으면 기본값을 쓴다.
def memory_settings(config: Any) -> MemoryConfig:
    return getattr(config, "memory", None) or MemoryConfig()


# 지식 저장소(GitHub)에 접근하는 데 필요한 설정값을 담는다.
@dataclass(frozen=True)
class ArchiveRepositoryConfig:
    # 모델에게 노출하지 않는 GitHub 저장소 식별자와 쓰기 권한 token이다.
    owner: str
    repo: str
    token: str


# config.json에 적을 수 있는 최상위 키. 목록 밖 키는 시작할 때 거부한다.
CONFIG_TOP_LEVEL_KEYS = frozenset({
    "agents",
    "allowed_guild_ids",
    "allowed_user_ids",
    "archive_forum_id",
    "archive_repository",
    "attachments",
    "chat",
    "chat_channels",
    "links",
    "memory",
    "reminders",
    "request_channels",
    "summary",
    "tools",
})


# config.json 전체 설정을 담고, 채널/에이전트 조회 기능을 제공한다.
@dataclass
class Config:
    # 작업 프롬프트로 답하는 요청 채널과 채팅 프롬프트로 답하는 채팅 채널. 두 목록은 겹치지 않는다.
    request_channels: set[int]
    chat_channels: set[int]
    chat: ChatConfig
    attachments: AttachmentsConfig
    links: LinksConfig
    reminders: RemindersConfig
    tools: ToolsConfig
    allowed_guild_ids: set[int]
    allowed_user_ids: set[int]
    agents: list[AgentConfig]
    # documenter가 읽고 Python 승인 경계가 외부 변경에 사용하는 저장소다.
    archive_repository: ArchiveRepositoryConfig | None = None
    # 저장소 도구를 허용할 Thread의 부모 ForumChannel ID다.
    archive_forum_id: int | None = None
    # 답만 하는 채널별로 chat.model, chat.effort 대신 쓸 값. 적은 항목만 들어 있다.
    channel_models: dict[int, dict[str, str]] = field(default_factory=dict)
    # 채팅 채널별 하루 자율 채팅 횟수.
    chat_per_day: dict[int, int] = field(default_factory=dict)
    # 자율 채팅 주제가 있는 채널. 이 채널의 자율 채팅만 웹 검색을 쓴다.
    chat_topics: dict[int, str] = field(default_factory=dict)
    # 선택 설정 절. config.json에 없으면 기본값을 쓴다.
    summary: SummaryConfig = field(default_factory=SummaryConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    # config.json 파일을 읽고 검증해 Config 인스턴스를 만든다.
    @classmethod
    def load(cls, path: Path) -> "Config":
        # 설정 파일을 읽고 알 수 없는 키를 막는다.
        raw = json.loads(path.read_text(encoding="utf-8"))
        unknown = sorted(key for key in raw if key not in CONFIG_TOP_LEVEL_KEYS)
        if unknown:
            raise ValueError(f"알 수 없는 설정 키: {', '.join(unknown)}. 채팅 채널은 chat_channels에 적는다.")
        request_items = raw.get("request_channels", [])
        request_channels: set[int] = set()
        channel_models: dict[int, dict[str, str]] = {}
        for item in request_items:
            if not isinstance(item, dict):
                request_channels.add(int(item))
                continue
            if not item.get("id"):
                raise ValueError("request_channels의 항목에 id가 없습니다.")
            channel_id = int(item["id"])
            override: dict[str, str] = {}
            for key in ("model", "effort"):
                if key in item:
                    value = str(item[key] or "").strip()
                    if not value:
                        raise ValueError(f"request_channels의 {channel_id} 항목에 {key}가 비어 있습니다.")
                    override[key] = value
            if "effort" in override and override["effort"] not in CHAT_EFFORTS:
                raise ValueError(
                    f"request_channels의 {channel_id} 항목의 effort는 {', '.join(CHAT_EFFORTS)} 중 하나여야 합니다."
                )
            if override:
                channel_models[channel_id] = override
            request_channels.add(channel_id)
        # chat_channels 항목은 채널 ID 또는 {"id", "per_day"(선택), "topic"(선택)} 객체다.
        # per_day를 생략하면 0이고, 같은 ID가 여러 번 나오면 적힌 값을 합친다.
        chat_channels: set[int] = set()
        chat_per_day: dict[int, int] = {}
        chat_topics: dict[int, str] = {}
        for item in raw.get("chat_channels", []):
            if isinstance(item, dict) and not item.get("id"):
                raise ValueError("chat_channels의 항목에 id가 없습니다.")
            raw_id = item["id"] if isinstance(item, dict) else item
            if type(raw_id) not in (str, int):
                raise ValueError("chat_channels의 채널 ID는 문자열 또는 정수여야 합니다.")
            channel_id = int(raw_id)
            if not isinstance(item, dict):
                chat_channels.add(channel_id)
                chat_per_day.setdefault(channel_id, 0)
                continue
            if "per_day" in item:
                per_day = int(item["per_day"])
                if per_day < 0:
                    raise ValueError(f"chat_channels의 {channel_id} 항목의 per_day는 0 이상이어야 합니다.")
                chat_per_day[channel_id] = per_day
            else:
                chat_per_day.setdefault(channel_id, 0)
            if "topic" in item:
                topic = str(item["topic"] or "").strip()
                if not topic:
                    raise ValueError(f"chat_channels의 {channel_id} 항목에 topic이 비어 있습니다.")
                chat_topics[channel_id] = topic
            chat_channels.add(channel_id)
        if not request_channels and not chat_channels:
            raise ValueError("request_channels나 chat_channels에 Discord 채널을 하나 이상 등록하세요.")
        overlap = request_channels & chat_channels
        if overlap:
            raise ValueError(
                "request_channels와 chat_channels에 같은 채널이 있습니다: "
                + ", ".join(str(v) for v in sorted(overlap))
            )

        # 더 이상 쓰지 않는 chat 설정을 막고 설정 절을 읽는다.
        chat_raw = raw.get("chat", {})
        if "auto_conversations_per_day" in chat_raw:
            raise ValueError(
                "chat.auto_conversations_per_day는 더 이상 쓰지 않습니다. "
                "chat_channels의 각 항목에 per_day로 적으세요."
            )
        settings = load_settings_sections(raw)

        # 보관 저장소 설정을 읽는다.
        archive_repo_raw = raw.get("archive_repository")
        archive_repository = (
            ArchiveRepositoryConfig(
                owner=archive_repo_raw["owner"],
                repo=archive_repo_raw["repo"],
                token=archive_repo_raw["token"],
            )
            if archive_repo_raw
            else None
        )
        archive_forum_id = int(raw["archive_forum_id"]) if raw.get("archive_forum_id") else None
        if archive_forum_id is not None:
            if archive_repository is None:
                raise ValueError("archive_forum_id에는 archive_repository 설정이 필요합니다.")
            if archive_forum_id not in request_channels:
                raise ValueError("archive_forum_id는 request_channels에도 등록해야 합니다.")
            if archive_forum_id in chat_channels:
                raise ValueError("archive_forum_id는 chat_channels에 등록할 수 없습니다.")

        # 에이전트 목록을 만들고 documenter 필수 조건을 확인한다.
        agents = [AgentConfig(**item) for item in raw["agents"]]
        if archive_forum_id is not None and not any(agent.role == "documenter" for agent in agents):
            raise ValueError("archive_forum_id에는 documenter 역할 에이전트가 필요합니다.")

        # 읽은 값으로 설정 객체를 반환한다.
        return cls(
            request_channels=request_channels,
            chat_channels=chat_channels,
            **settings,
            allowed_guild_ids={int(v) for v in raw.get("allowed_guild_ids", [])},
            allowed_user_ids={int(v) for v in raw.get("allowed_user_ids", [])},
            agents=agents,
            archive_repository=archive_repository,
            archive_forum_id=archive_forum_id,
            chat_per_day=chat_per_day,
            chat_topics=chat_topics,
            channel_models=channel_models,
            summary=load_optional_section(raw, "summary", SummaryConfig),
            memory=load_optional_section(raw, "memory", MemoryConfig),
        )

    # 채널이 에이전트 채널로 등록됐는지 확인한다. 두 목록 중 어디에 있어도 된다.
    def is_agent_channel(self, channel_id: int) -> bool:
        return channel_id in self.request_channels or channel_id in self.chat_channels

    # 채널에서 쓸 (모델, effort)를 반환한다. 스레드는 부모 채널(포럼 등)의 값을 따른다.
    # archive_forum_id 채널은 override가 없으면 기본 모델 대신 ARCHIVE_DEFAULT_MODEL을 쓴다.
    def chat_model_for(self, channel: Any) -> tuple[str, str]:
        # 스레드이면 부모 채널을 기준으로 채널별 모델과 effort를 고른다.
        channel_id = channel.id
        if isinstance(channel, discord.Thread) and channel.parent_id is not None:
            channel_id = channel.parent_id
        override = self.channel_models.get(channel_id, {})
        default_model = ARCHIVE_DEFAULT_MODEL if channel_id == self.archive_forum_id else self.chat.model
        return override.get("model", default_model), override.get("effort", self.chat.effort)


# 문자열을 MCP 도구 결과 형식으로 감싼다.
def tool_text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}
