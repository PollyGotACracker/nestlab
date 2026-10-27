"""skill 원본 폴더, 링크 및 중복 확인 폴더, Hermes 설정 경로이다.

SOURCE는 이 모듈 폴더의 두 단계 위 저장소 폴더에 있는 공용 skill 원본이다.
HOME은 실행 환경의 사용자 폴더이다.
"""
from __future__ import annotations

from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[2] / "shared" / "skills").resolve()
HOME = Path.home()

# 링크를 만드는 폴더. (표시 이름, skill 폴더, 사용하는 도구, 설치 판정용 도구 설정 폴더)
LINK_DIRS = [
    ("Claude Code", HOME / ".claude" / "skills", "Claude Code", [HOME / ".claude"]),
    (".agents", HOME / ".agents" / "skills", "Codex, Cursor, OpenClaw",
     [HOME / ".codex", HOME / ".cursor", HOME / ".openclaw", HOME / ".hermes"]),
]
# 링크를 만들지 않고 중복만 확인하는 폴더.
DUPLICATE_DIRS = [
    (".codex", HOME / ".codex" / "skills"),
    (".cursor", HOME / ".cursor" / "skills"),
]
HERMES_CONFIG = HOME / ".hermes" / "config.yaml"
HERMES_LINE = "skills:\n  external_dirs:\n    - ~/.agents/skills"
