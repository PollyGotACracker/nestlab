"""하네스 코드가 쓰는 저장소 경로 상수를 한 곳에 정의한다.

호출자: hooks와 tools의 비시험 모듈(tools는 tools/common/sub_repo_paths.py로 불러온다)
입력: 없음
반환: 저장소 루트 기준 폴더 및 파일 Path와 상대 경로 문자열 상수
한계: 폴더 구조가 바뀌면 이 파일만 수정한다. 사용자 홈의 Claude와 Codex 설정 폴더 이름도 여기에 둔다.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 저장소 안 폴더와 파일이다.
SHARED_DIR = ROOT / "shared"
TEMPLATE_DIR = SHARED_DIR / "templates" / "workflow"
SKILL_DIR = SHARED_DIR / "skills"
SETTINGS_DIR = SHARED_DIR / "settings"
AGENT_DIR = ROOT / ".claude" / "agents"
TOOLS_DIR = ROOT / "tools"
LOG_DIR = ROOT / "log"
INCIDENT_DIR = LOG_DIR / "incident"
LOCAL_SETTINGS = SETTINGS_DIR / "local.json"
WIKI_SKILL = SKILL_DIR / "wiki" / "SKILL.md"
CREATE_TASK_DOCS = TOOLS_DIR / "create_task_docs.py"
RUN_WORKFLOW = TOOLS_DIR / "run_workflow.py"
MANAGE_WORKTREE = TOOLS_DIR / "manage_task_worktree.py"

# 저장소 루트 기준 상대 경로 문자열이다. 정규식과 안내 문구, glob에 쓴다.
SHARED_NAME = SHARED_DIR.name
TASK_REL = "log/task"
INCIDENT_REL = "log/incident"
SKILL_REL = "shared/skills"
SKILL_GLOB = f"{SKILL_REL}/*/SKILL.md"
TEMPLATE_GLOB = "shared/templates/workflow/*.md"
AGENT_REL = ".claude/agents"

# 사용자 홈의 Claude Code, Codex 설정 폴더 이름이다.
CLAUDE_HOME_NAME = ".claude"
CODEX_HOME_NAME = ".codex"
