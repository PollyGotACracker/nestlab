"""검사 대상·허용 범위 설정, 패턴, 위반 종류 이름이다.

ROOT는 이 저장소 폴더 최상위 경로이다.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 검사 대상 파일의 저장소 기준 glob 패턴.
TARGET_PATTERNS = [
    "AGENTS.md",
    ".claude/agents/*/AGENTS.md",
    "discord/prompts/*.md",
    "schema/*.md",
    "shared/skills/*/SKILL.md",
]

# 검사5: 경로 목록 절에서만 적을 수 있는 경로의 첫 구간.
DIRECT_PATH_ROOTS = {"log", ".claude", "shared", "hooks", "tools", "discord", ".codex"}
PATH_LIST_FILE = "AGENTS.md"
PATH_LIST_SECTION = "경로 목록"

# 런타임에 다른 파일의 절을 합쳐 쓰는 파일과 그 절을 찾을 추가 파일.
# Discord 채팅 프롬프트는 런타임 프롬프트의 `Response control` 절을 붙여 만든다.
SECTION_EXTRA_FILES = {"discord/prompts/CHAT.md": ["discord/prompts/RUNTIME.md"]}
# 절 참조에 파일이 없을 때 현재 파일과 CORE_FILES 다음으로 찾는 양식 파일의 glob 패턴.
SCHEMA_PATTERN = "schema/*.md"

RULE_ID_SCOPE = "discord/prompts/"
SKIP_DIRS = {".git", "node_modules", "__pycache__"}

INLINE_CODE = re.compile(r"`([^`\n]+)`")
# "·"나 ","로 이은 하나 이상의 백틱 제목과 그 뒤의 "절".
SECTION_REF = re.compile(r"`[^`\n]+`(?:\s*[·,]\s*`[^`\n]+`)*\s*절")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
PATH_CHARS = re.compile(r"^[\w.\-/*?{},~]+$")
FILE_EXT = re.compile(r"\.([A-Za-z][A-Za-z0-9]{0,4})$")
# 저장소 파일로 보는 확장자.
# `node.js`, `www.example.com` 같은 토큰은 제외된다.
REPO_EXTS = {
    "md", "py", "toml", "txt", "json", "jsonl", "yaml", "yml", "ps1", "sh",
    "csv", "html", "css", "png", "jpg", "jpeg", "gif", "svg", "zip",
}
# :3, :3-5, :3,5, :3-5,9 같은 행 번호 접미사.
LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")
# 한국어 조사가 붙은 경우(A060를, H001은)도 찾도록 ASCII 단어 경계를 쓴다.
RULE_ID = re.compile(r"(?<![A-Za-z0-9_])[AHT]\d{3}(?![A-Za-z0-9_])")
NEGATIVE = re.compile(r"않는다|하지 마|금지")

KIND_PATH = "검사1 경로 없음"
KIND_SECTION = "검사2 절 제목 없음"
KIND_RULE_ID = "검사3 Rule ID"
KIND_NEGATIVE = "검사4 부정형"
KIND_DIRECT = "검사5 경로 직접 기재"
KIND_CROSS = "검사6 다른 지침 참조"
# 검사6: 다른 지침 파일을 가리키는 표현.
CROSS_REF = re.compile(r"상위 지침|프로젝트 지침")
KINDS = (KIND_PATH, KIND_SECTION, KIND_RULE_ID, KIND_NEGATIVE, KIND_DIRECT, KIND_CROSS)
