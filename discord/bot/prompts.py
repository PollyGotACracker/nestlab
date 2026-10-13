"""에이전트 탐색, 시스템 프롬프트 구성, 채팅 제어 해석, 도구 기록을 담당한다."""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import date
from pathlib import Path
from typing import Any

import discord
from claude_agent_sdk import (
    ServerToolResultBlock,
    ServerToolUseBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from config import (
    DISCORD_LIMIT,
    HERE,
    POLL_ANSWERS_MAX,
    POLL_ANSWERS_MIN,
    POLL_ANSWER_MAX,
    POLL_HOURS_MAX,
    POLL_QUESTION_MAX,
    THREAD_NAME_MAX,
    TURN_PROMPT_PATH,
    AgentConfig,
    ArchiveRepositoryConfig,
    Config,
    RemindersConfig,
    ToolsConfig,
)
from tools import role_tool_names

RUNTIME_PROMPT_PATH = HERE.parent / "prompts" / "RUNTIME.md"
ARCHIVE_PROMPT_PATH = HERE.parent / "prompts" / "ARCHIVE.md"
CHAT_PROMPT_PATH = HERE.parent / "prompts" / "CHAT.md"
# 역할 폴더의 캐릭터 기억 파일. 있으면 시스템 프롬프트의 Persona 뒤에 넣는다.
CHARACTER_MEMORY_FILE = "MEMORY.md"
# 템플릿 줄 머리의 {{request:...}}/{{chat:...}} 변형 표식. 표식 앞 들여쓰기는 결과에 남긴다.
PROMPT_KINDS = ("request", "chat")
VARIANT_START = re.compile(r"^([ \t]*)\{\{([A-Za-z_]+):")
CHAT_NEXT = re.compile(r"\[\[next:([a-z0-9_-]+)\]\]", re.IGNORECASE)
CHAT_WAIT = re.compile(r"\[\[wait:user\]\]", re.IGNORECASE)
CHAT_REACT = re.compile(r"\[\[react:([^\[\]\r\n]+)\]\]", re.IGNORECASE)
CHAT_CONTROL = re.compile(r"\[\[\s*(?:next|react)\b", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://[^\s<>\]\[\)\(\"']+")
HANDOFF_BODY_PATTERN = re.compile(
    r"(?:제\s*(?:담당|역할)이\s*아니|제가\s*(?:직접\s*)?(?:할|처리할)\s*수\s*없|"
    r"(?:담당|역할)[^\n]{0,40}(?:에게|한테)\s*(?:넘기|연결)|"
    r"(?:넘겨|연결해)\s*(?:드리|볼게|보겠)|"
    r"(?:판단|답변|처리)할\s*몫[^\n]{0,60}(?:넘겼|넘기)|"
    r"낄\s*자리[^\n]{0,20}아니|"
    r"[가-힣]+님?\s*(?:담당|역할)이라고)"
)
SENTENCE_PATTERN = re.compile(r".*?(?:[.!?。！？]+(?=\s|$)|$)")
# 에이전트 채널에서 역할마다 맡는 요청. 프롬프트의 역할 목록과 요청 넘기기 기준으로 쓴다.
ROLE_DUTIES = {
    "director": "작업 요청에서 사용자의 현재 질문과 교정 유지, 작업 요청의 주제 이탈과 새로운 정보 없는 반복 조정, 담당이 불분명하거나 여러 역할이 필요한 요청 배정, 투표 올리기, 스레드 만들기, 메시지 고정",
    "researcher": "새 정보 조사, 최신 정보 확인",
    "worker": "코드 조각, 글 초안, 정리, 계산 같은 결과물 작성",
    "reviewer": "발언 검토, 인용 대조(채널 기록), 사실 검증(웹 검색)",
    "documenter": "지식 저장소 조회, 기록, 삭제",
    "assistant": "알림 같은 생활형 요청",
}


# 채널이 등록된 에이전트 채널인지 확인한다. 채널이 스레드면 부모 채널(포럼 채널 등)의
# ID도 함께 확인한다.
def is_registered_agent_channel(channel: Any, cfg: Config) -> bool:
    if cfg.is_agent_channel(channel.id):
        return True
    if isinstance(channel, discord.Thread) and channel.parent_id is not None:
        return cfg.is_agent_channel(channel.parent_id)
    return False


# 채팅 프롬프트로 답할 채널인지 확인한다. 스레드는 부모 채널 ID로 판정한다.
def is_chat_channel(channel: Any, cfg: Config) -> bool:
    channel_id = channel.id
    if isinstance(channel, discord.Thread) and channel.parent_id is not None:
        channel_id = channel.parent_id
    return channel_id in cfg.chat_channels


# GitHub 쓰기 도구를 붙일 수 있는 저장소 전용 Thread인지 부모 포럼 ID로 판정한다.
def is_archive_thread(channel: Any, cfg: Config) -> bool:
    return (
        isinstance(channel, discord.Thread)
        and cfg.archive_forum_id is not None
        and channel.parent_id == cfg.archive_forum_id
    )


# 에이전트 역할에 해당하는 .claude/agents 하위 폴더 경로를 반환한다.
def canonical_role_dir(root: Path, role: str) -> Path:
    return root / ".claude" / "agents" / role


# AGENTS.md의 frontmatter에서 name 값을 읽는다.
def read_agent_name(agents_file: Path) -> str | None:
    text = agents_file.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) != 3:
        return None
    for line in parts[1].splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip() or None
    return None


# .claude/agents에서 에이전트 ID와 호출 이름을 찾는다.
def discover_agents(root: Path) -> dict[str, str]:
    """내부 에이전트 ID(폴더명)와 호출 이름을 .claude/agents에서 읽는다."""
    agents: dict[str, str] = {}
    for role_dir in sorted((root / ".claude" / "agents").iterdir()):
        agents_file = role_dir / "AGENTS.md"
        if not role_dir.is_dir() or not agents_file.is_file():
            continue
        name = read_agent_name(agents_file)
        if name:
            agents[role_dir.name] = name
    return agents


SOUL_IDENTITY_FIELDS = (("Species", "종"), ("MBTI", "MBTI"))


# SOUL.md의 Identity 항목에서 종·MBTI 값을 읽는다.
def read_soul_identity(soul_path: Path) -> list[str]:
    text = soul_path.read_text(encoding="utf-8")
    parts = []
    for key, label in SOUL_IDENTITY_FIELDS:
        match = re.search(rf"^- \*\*{key}:\*\*\s*(.+)$", text, re.MULTILINE)
        if match:
            parts.append(f"{label}: {match.group(1).strip()}")
    return parts


# SOUL.md의 `## Tone Guide` 절 원문을 읽는다. 절이 없으면 빈 문자열이다.
def read_tone_guide(soul_path: Path) -> str:
    text = soul_path.read_text(encoding="utf-8")
    match = re.search(r"^## Tone Guide[^\n]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


# 프롬프트 구성에 쓰이는 파일들의 수정 시각·크기로 서명을 만든다(파일 변경 감지용).
def prompt_source_signature(
    root: Path, role: str, roster_roles: tuple[str, ...] = ()
) -> tuple[tuple[str, int, int], ...]:
    role_dir = canonical_role_dir(root, role)
    paths = [
        root / "AGENTS.md", role_dir / "AGENTS.md", role_dir / "SOUL.md",
        RUNTIME_PROMPT_PATH, TURN_PROMPT_PATH, CHAT_PROMPT_PATH,
    ]
    paths += [canonical_role_dir(root, other) / "SOUL.md" for other in roster_roles if other != role]
    if role == "documenter":
        paths.append(ARCHIVE_PROMPT_PATH)
    signature = [
        (str(path), path.stat().st_mtime_ns, path.stat().st_size)
        for path in paths
    ]
    # 캐릭터 기억 파일은 없을 수 있으므로 없으면 (경로, 0, -1)로 둔다.
    memory_path = role_dir / CHARACTER_MEMORY_FILE
    try:
        memory_stat = memory_path.stat()
        signature.append((str(memory_path), memory_stat.st_mtime_ns, memory_stat.st_size))
    except FileNotFoundError:
        signature.append((str(memory_path), 0, -1))
    return tuple(signature)


# 줄 머리의 {{request:...}}/{{chat:...}} 표식 중 kind에 맞는 내용만 남기고 나머지 표식 줄은 지운다.
# 표식은 여러 줄에 걸칠 수 있고, 닫히지 않거나 줄 머리가 아닌 표식은 ValueError로 거부한다.
def render_variant(template: str, kind: str) -> str:
    if kind not in PROMPT_KINDS:
        raise ValueError(f"알 수 없는 프롬프트 종류입니다: {kind}")
    lines = template.split("\n")
    rendered: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = VARIANT_START.match(line)
        if match is None:
            if "{{" in line or "}}" in line:
                raise ValueError(f"변형 표식이 줄 머리에 있지 않습니다: {index + 1}행")
            rendered.append(line)
            index += 1
            continue
        indent, marker_kind = match.groups()
        if marker_kind not in PROMPT_KINDS:
            raise ValueError(f"알 수 없는 변형 표식입니다: {marker_kind} ({index + 1}행)")
        start = index
        body = [line[match.end():]]
        while not body[-1].endswith("}}"):
            index += 1
            if index >= len(lines):
                raise ValueError(f"닫히지 않은 변형 표식입니다: {start + 1}행")
            body.append(lines[index])
        body[-1] = body[-1][:-2]
        text = "\n".join(body)
        if "{{" in text or "}}" in text:
            raise ValueError(f"변형 표식 안에 다른 표식이 있습니다: {start + 1}행")
        if marker_kind == kind:
            rendered.append(indent + text)
        index += 1
    return "\n".join(rendered)


# RUNTIME.md의 `## Response control` 절부터 끝까지를 kind 변형으로 반환한다.
def response_control_template(kind: str) -> str:
    runtime_template = RUNTIME_PROMPT_PATH.read_text(encoding="utf-8").strip()
    _, marker, rest = runtime_template.partition("## Response control")
    if not marker:
        raise ValueError(f"{RUNTIME_PROMPT_PATH}에 `## Response control` 절이 없습니다.")
    return render_variant(marker + rest, kind)


# 역할 폴더에 MEMORY.md가 있으면 Persona 뒤에 넣을 `# Character memory` 절을 만든다.
def render_character_memory(root: Path, role: str) -> str:
    path = canonical_role_dir(root, role) / CHARACTER_MEMORY_FILE
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    return f"# Character memory\n\n{text}\n\n---\n\n"


# 역할 전용 도구 안내. 도구 연결부(ROLE_TOOL_BUILDERS)와 같은 조건으로 붙인다.
def render_role_tool_block(
    role: str, tools_config: ToolsConfig, reminders_config: RemindersConfig
) -> str:
    role_tool_block = ""
    if role == "director":
        if hasattr(discord, "Poll"):
            role_tool_block = (
                "- poll_create 도구로 채널에 투표를 올릴 수 있다. 사용자의 선택이 필요할 때만 쓴다. "
                f"선택지는 {POLL_ANSWERS_MIN}~{POLL_ANSWERS_MAX}개, 선택지 하나는 {POLL_ANSWER_MAX}자 이하, "
                f"질문은 {POLL_QUESTION_MAX}자 이하, 기간은 1~{POLL_HOURS_MAX}시간이다.\n"
            )
        role_tool_block += (
            f"- thread_create 도구로 현재 채널에 스레드를 만들 수 있다. 이름은 {THREAD_NAME_MAX}자 이하이고, "
            "message_id를 주면 그 메시지에서 스레드를 시작한다. "
            f"스레드에 남길 내용은 content에 {DISCORD_LIMIT}자 이하로 넣으면 첫 글로 올라간다.\n"
            "- message_pin 도구로 현재 채널의 메시지를 고정할 수 있다.\n"
        )
    elif role == "reviewer":
        role_tool_block = (
            f"- channel_history 도구로 채널의 최근 메시지를 최대 {tools_config.channel_history_max}개까지 다시 읽을 수 있다. "
            "인용이 실제 메시지와 맞는지 대조할 때 쓴다.\n"
        )
    elif role == "assistant":
        role_tool_block = (
            f"- reminder_set 도구로 1분에서 {reminders_config.max_minutes}분({reminders_config.max_days}일) "
            "뒤에 한 번 울리는 알림을 예약할 수 있다.\n"
            f"- reminder_repeat 도구로 매일이나 지정한 요일의 정해진 시각({reminders_config.tz_label})에 "
            "울리는 반복 알림을 예약할 수 있다.\n"
            "- 특정 시각에 울릴 한 번짜리 알림은 턴 프롬프트의 현재 시각을 기준으로 분을 계산한다. "
            "현재 시각을 확인하려고 reminder_list를 부르지 않는다.\n"
            "- reminder_list로 현재 채널의 알림 목록을 보고, reminder_cancel에 번호를 넣어 취소한다.\n"
            "- 예약한 알림은 봇이 재시작해도 유지된다.\n"
        )
    return role_tool_block


# 요청 채널용 시스템 프롬프트(공통 규칙 + 페르소나 + 런타임 규칙)를 만든다.
def build_request_system_prompt(
    root: Path,
    role: str,
    self_name: str,
    self_korean_name: str,
    agents: list[AgentConfig],
    archive_repository: ArchiveRepositoryConfig | None,
    tools_config: ToolsConfig,
    reminders_config: RemindersConfig,
) -> str:
    common = read_common_rules(root)
    role_dir = canonical_role_dir(root, role)
    soul = (role_dir / "SOUL.md").read_text(encoding="utf-8").strip()
    character_memory = render_character_memory(root, role)
    runtime_template = render_variant(RUNTIME_PROMPT_PATH.read_text(encoding="utf-8").strip(), "request")
    others = []
    for agent in agents:
        if agent.name == self_name:
            continue
        tools = role_tool_names(agent.role, archive_repository)
        tool_text_part = f", 전용 도구: {', '.join(tools)}" if tools else ""
        identity = read_soul_identity(canonical_role_dir(root, agent.role) / "SOUL.md")
        identity_part = "".join(f", {item}" for item in identity)
        others.append(
            f"- {agent.korean_name} (내부 ID: {agent.name}, 역할: {agent.role}{identity_part}, "
            f"담당: {ROLE_DUTIES.get(agent.role, '없음')}{tool_text_part})"
        )
    roster_block = "\n".join(others) if others else "- (없음)"

    # 역할마다 맡는 요청을 알려, 담당이 아닌 요청은 담당 역할에게 넘기게 한다.
    duty_block = (
        f"- 너의 역할은 {role}이고 담당은 {ROLE_DUTIES.get(role, '없음')}이다.\n"
        "- 담당 목록은 사용자가 할 수 있는 일을 직접 물을 때만 말한다. "
        "로컬 작업 세션의 역할은 물어볼 때만 '로컬 작업에서는'처럼 구분해 말한다.\n"
    )
    if others:
        duty_block += (
            "- 채팅과 대상이 불분명한 발화는 어느 담당이든 Persona로 답한다. "
            "담당이 정해진 작업 요청이 자기 담당이 아니면 직접 처리하지 말고, "
            "[[next:...]]에 담당 역할 캐릭터의 내부 ID를 넣어 넘긴다.\n"
        )
    if role != "director" and any(agent.role == "director" for agent in agents):
        duty_block += "- 담당이 불분명하거나 여러 역할이 필요한 작업 관련 채팅은 director 역할 캐릭터에게 넘긴다.\n"

    role_tool_block = render_role_tool_block(role, tools_config, reminders_config)

    length_exception = (
        "- 작성 요청에 답할 때는 결과물 본문이 필요한 만큼 길어도 된다. 채팅에는 이 예외를 쓰지 않는다.\n"
        if role == "worker"
        else ""
    )

    # 지식 저장소 도구는 documenter 역할에만 붙으므로, 그 역할이 실행 중일 때만 안내한다.
    archive_block = ""
    if archive_repository is not None and any(agent.role == "documenter" for agent in agents):
        repo_name = f"{archive_repository.owner}/{archive_repository.repo}"
        if role == "documenter":
            archive_block = (
                ARCHIVE_PROMPT_PATH
                .read_text(encoding="utf-8")
                .strip()
                .replace("$repo_name", repo_name)
            ) + "\n"
        else:
            archive_block = (
                f"- 지식 저장소는 GitHub {repo_name}이다. 조회·기록·삭제 도구는 documenter 역할 캐릭터만 가지고 있다.\n"
                "- 지식 저장소 연결, 내용, 수정, 삭제에 관한 요청에는 직접 답하지 말고, "
                "[[next:...]]에 documenter 역할 캐릭터의 내부 ID를 넣어 넘긴다.\n"
            )

    runtime_text = fill_template(runtime_template, {
        "$self_name": self_name,
        "$self_korean_name": self_korean_name,
        "$duty_block": duty_block.rstrip(),
        "$role_tool_block": role_tool_block.rstrip(),
        "$archive_block": archive_block.rstrip(),
        "$memory_block": MEMORY_BLOCK,
        "$length_exception": length_exception.rstrip(),
        "$roster_block": roster_block,
    })

    return (
        f"# Global rules\n\n{common}\n\n---\n\n"
        f"# Persona\n\n{soul}\n\n---\n\n"
        f"{character_memory}"
        f"{runtime_text}\n"
    )


# Discord 공통 규칙은 AGENTS.md의 `# 프로젝트 원칙` 앞까지이다. 그 뒤는 로컬 저장소 작업 전용이다.
PROJECT_RULES = re.compile(r"\r?\n---\r?\n\s*# 프로젝트 원칙")


def read_common_rules(root: Path) -> str:
    return PROJECT_RULES.split((root / "AGENTS.md").read_text(encoding="utf-8"), 1)[0].strip()


# 채팅은 `### 문장 표현` 절을 시스템 프롬프트 대신 턴 프롬프트 끝에 둔다.
STYLE_SECTION = re.compile(r"^### 문장 표현[^\n]*\n.*?(?=^#{1,3} |\Z)", re.MULTILINE | re.DOTALL)


def read_style_rules(root: Path) -> str:
    match = STYLE_SECTION.search(read_common_rules(root))
    return match.group(0).strip() if match else ""


# 채팅 채널용 시스템 프롬프트(공통 규칙 + 페르소나 + 채팅 규칙)를 만든다.
# 작업용 담당 넘기기·Archive 절차 없이 이름·내부 ID·SOUL identity만 담은 동료 명단을 쓴다.
def build_chat_system_prompt(
    root: Path,
    role: str,
    self_name: str,
    self_korean_name: str,
    agents: list[AgentConfig],
    tools_config: ToolsConfig,
    reminders_config: RemindersConfig,
) -> str:
    common = STYLE_SECTION.sub("", read_common_rules(root), count=1).strip()
    soul = (canonical_role_dir(root, role) / "SOUL.md").read_text(encoding="utf-8").strip()
    character_memory = render_character_memory(root, role)
    chat_template = CHAT_PROMPT_PATH.read_text(encoding="utf-8").strip()
    others = []
    for agent in agents:
        if agent.name == self_name:
            continue
        identity = read_soul_identity(canonical_role_dir(root, agent.role) / "SOUL.md")
        identity_part = "".join(f", {item}" for item in identity)
        others.append(f"- {agent.korean_name} (내부 ID: {agent.name}{identity_part})")
    roster_block = "\n".join(others) if others else "- (없음)"
    response_control = fill_template(response_control_template("chat"), {"$roster_block": roster_block})

    chat_text = fill_template(chat_template, {
        "$self_name": self_name,
        "$self_korean_name": self_korean_name,
        "$role_tool_block": render_role_tool_block(role, tools_config, reminders_config).rstrip(),
        "$memory_block": CHAT_MEMORY_BLOCK,
        "$response_control": response_control,
    })

    return (
        f"# Global rules\n\n{common}\n\n---\n\n"
        f"# Persona\n\n{soul}\n\n---\n\n"
        f"{character_memory}"
        f"{chat_text}\n"
    )


# 응답 본문에서 [[next:...]], [[react:...]] 제어 줄을 분리해 본문·다음 화자·리액션 이모지를 반환한다.
def parse_chat_controls(result: str) -> tuple[str, str, str | None, bool]:
    lines = result.strip().splitlines()
    next_match = CHAT_NEXT.fullmatch(lines[-1].strip()) if lines else None
    # 마지막 next 줄이 빠진 응답은 next를 stop으로 보고 wait·react 줄은 그대로 분리한다.
    if next_match:
        lines.pop()
    wait_match = CHAT_WAIT.fullmatch(lines[-1].strip()) if lines else None
    if wait_match:
        lines.pop()
    react_match = CHAT_REACT.fullmatch(lines[-1].strip()) if lines else None
    react_emoji = react_match.group(1).strip() if react_match else None
    if react_match:
        if not react_emoji:
            raise ValueError("chat reaction must not be empty")
        lines.pop()
    text = "\n".join(lines).strip()
    if (CHAT_CONTROL.search(text) or re.search(r"\[\[\s*wait\s*:", text, re.IGNORECASE)
            or (react_emoji and (CHAT_CONTROL.search(react_emoji)
                                or re.search(r"\[\[\s*wait\s*:", react_emoji, re.IGNORECASE)))):
        raise ValueError("chat control lines are duplicated, malformed or misplaced")

    next_name = next_match.group(1).lower() if next_match and not wait_match else "stop"
    return text, next_name, react_emoji, bool(wait_match)


# 기존 호출자의 3값 API를 유지한다.
def parse_chat_result(result: str) -> tuple[str, str, str | None]:
    text, next_name, react_emoji, _ = parse_chat_controls(result)
    return text, next_name, react_emoji


# 역할 연결 판단만 담은 본문은 제어값과 중복되므로 Discord 전송 전에 제거한다.
def suppress_handoff_body(text: str, next_name: str) -> str:
    if next_name == "stop" or not text:
        return text
    kept_lines = []
    for line in text.splitlines():
        sentences = [match.group(0) for match in SENTENCE_PATTERN.finditer(line) if match.group(0)]
        kept = "".join(sentence for sentence in sentences if not HANDOFF_BODY_PATTERN.search(sentence))
        if kept.strip():
            kept_lines.append(kept.strip())
    return "\n".join(kept_lines)


# 도구 결과 블록이 오류인지 판정한다.
def tool_result_failed(block: ToolResultBlock | ServerToolResultBlock) -> bool:
    if getattr(block, "is_error", False):
        return True
    content = block.content
    if not isinstance(content, dict):
        return False
    if content.get("is_error") is True or content.get("error"):
        return True
    result_type = str(content.get("type", "")).lower()
    return result_type.endswith("error") or "error" in result_type


# SDK 도구 블록에서 이번 턴에 실제 사용한 도구와 확인 URL을 추출한다.
def collect_tool_evidence(
    message: Any, tool_uses: dict[str, tuple[str, list[str]]], records: list[str]
) -> None:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
            urls = sorted(set(URL_PATTERN.findall(json.dumps(block.input, ensure_ascii=False))))
            tool_uses[block.id] = (block.name, urls)
            records.append(f"사용 {block.name}" + (f": {', '.join(urls)}" if urls else ""))
        elif isinstance(block, (ToolResultBlock, ServerToolResultBlock)):
            tool_name, input_urls = tool_uses.get(block.tool_use_id, ("unknown", []))
            raw = json.dumps(block.content, ensure_ascii=False) if not isinstance(block.content, str) else block.content
            urls = sorted(set(input_urls + URL_PATTERN.findall(raw)))
            if tool_result_failed(block):
                status = "실패·미확인"
            elif tool_name.lower() in {"webfetch", "web_fetch"} and urls:
                status = "확인"
            else:
                status = "결과"
            records.append(f"{status} {tool_name}" + (f": {', '.join(urls)}" if urls else ""))


# 도구·출처 기록을 중복 없이 목록 문구로 만든다.
def render_tool_evidence(records: list[str]) -> str:
    unique = list(dict.fromkeys(records))
    return "\n".join(f"- {record}" for record in unique)


# 턴마다 남기는 도구·출처 기록 줄의 머리말. 요약 입력에서 빼고 원문 창 안의 것만 보낸다.
EVIDENCE_HEADER = "[이번 턴 도구·출처 기록]"


# 채팅 기록 줄이 도구·출처 기록인지 판정한다.
def is_evidence_entry(entry: tuple[int, float, str, str, int | None]) -> bool:
    return entry[3].startswith(EVIDENCE_HEADER)


# 템플릿의 알려진 자리표시자를 긴 이름부터 한 번에 치환한다.
# 치환된 값 안의 $이름은 다시 치환하지 않아 기록·기억 내용이 다른 자리를 오염시키지 않는다.
def fill_template(template: str, values: dict[str, str]) -> str:
    if not values:
        return template
    pattern = re.compile("|".join(re.escape(key) for key in sorted(values, key=len, reverse=True)))
    return pattern.sub(lambda match: values[match.group(0)], template)


# 현재 작업의 채팅 턴 종료 사유를 기록한다.
def set_chat_turn_reason(runtime: Any, channel_id: int, reason: str) -> None:
    reasons = getattr(runtime, "chat_turn_reasons", None)
    if reasons is None:
        reasons = {}
        runtime.chat_turn_reasons = reasons
    reasons[(channel_id, id(asyncio.current_task()))] = reason


# 현재 asyncio 작업 ID를 반환한다. 작업 밖이면 None을 반환한다.
def current_task_id() -> int | None:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return id(task) if task is not None else None


# 본문에서 가장 앞에 나온 캐릭터 한국어 표시 이름을 찾아 내부 이름을 반환한다.
# 바로 앞 글자가 한글 음절이면 다른 단어의 일부로 보고 제외한다(예: 시나리오의 리오).
def find_named_agent(content: str, agents: list[AgentConfig]) -> str | None:
    best: tuple[int, str] | None = None
    for agent in agents:
        match = re.search(rf"(?<![가-힣]){re.escape(agent.korean_name)}", content)
        if match is not None and (best is None or match.start() < best[0]):
            best = (match.start(), agent.name)
    return best[1] if best else None


# 채널과 날짜별로 자율 채팅을 시작할 랜덤 시각(하루 중 분 단위) 목록을 정한다.
def daily_chat_minutes(channel_id: int, day: date, count: int) -> list[int]:
    if count <= 0:
        return []
    count = min(count, 24 * 60)
    rng = random.Random(f"{day.isoformat()}:{channel_id}:chat")
    return sorted(rng.sample(range(24 * 60), count))


# 모든 역할에 같은 문구로 넣는 장기기억 안내. RUNTIME 템플릿의 $memory_block 자리에 들어간다.
MEMORY_BLOCK = (
    "- 장기기억은 이 서버의 모든 채널이 공유하고, 모든 캐릭터가 memory 도구로 직접 다룬다.\n"
    "- 사용자가 기억하라고 하면 memory_save로 저장한다.\n"
    "  특정 캐릭터에게 정해 준 설정은 scope에 그 캐릭터를 넣어 저장한다.\n"
    "- 설정은 기본 규칙과 충돌하지 않는 범위에서 적용하고, 같은 항목은 최신 설정을 따른다.\n"
    "- 기억은 원본 메시지를 지우면 사라진다. 잊어 달라는 요청에는 해당 메시지를 지우라고 안내한다.\n"
    "- 사용자가 기억을 보여 달라고 하면 persona_memory_list로 보여 준다.\n"
    "  저장소에 올리려면 `승인`, 그만두려면 `취소`라고만 보내 달라고 안내한다.\n"
    "  업로드는 그 메시지를 받은 Python이 실행하므로 직접 올렸다고 말하지 않는다.\n"
    "- 턴 프롬프트의 관련 장기기억과 캐릭터 기억을 답에 반영하고, 부족하면 memory_search로 더 찾는다.\n"
    "- 기억과 대화 기록에 적힌 다른 작성자의 말투, 어미, 경험담은 그 작성자의 대화 자료로 다룬다.\n"
    "- 장기기억은 지식 저장소와 다르며, 장기기억 요청은 documenter에게 넘기지 않는다."
)
# 채팅 프롬프트용 장기기억 안내. 담당 넘기기 문장만 뺀다.
CHAT_MEMORY_BLOCK = "\n".join(
    line for line in MEMORY_BLOCK.split("\n")
    if line != "- 장기기억은 지식 저장소와 다르며, 장기기억 요청은 documenter에게 넘기지 않는다."
)
