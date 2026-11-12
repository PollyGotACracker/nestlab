"""load_agent.py 모드별 지침 주입 테스트"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1]
ROLES = HOOKS.parent / ".claude" / "agents"


# load_agent.py를 실행해 additionalContext를 반환한다.
def run(event, role="jelly", *flags):
    payload = {"hook_event_name": event, "agent_type": role, "cwd": str(HOOKS)}
    out = subprocess.run([sys.executable, "-B", str(HOOKS / "load_agent.py"), *flags], input=json.dumps(payload),
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


class Mode(unittest.TestCase):
    def test_merged_main_roles_use_current_agents(self):
        for role in ("director", "assistant"):
            original = (ROLES / role / "AGENTS.md").read_text(encoding="utf-8")
            body = original.split("---", 2)[2].strip() if original.startswith("---") else original.strip()
            self.assertIn(body, run("SessionStart", role))

    def test_external_director_context_preserves_path_privacy(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = {"hook_event_name": "SessionStart", "agent_type": "director", "cwd": folder}
            output = subprocess.run([sys.executable, "-B", str(HOOKS / "load_agent.py")], input=json.dumps(payload),
                                    cwd=folder, capture_output=True, text=True, encoding="utf-8", check=True)
            context = json.loads(output.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertNotIn("# 작업 문서 도구", context)
            self.assertNotIn(f"Python 실행 파일: `{Path(sys.executable).resolve()}`", context)
            self.assertNotIn(f"작업 문서 생성기: `{(HOOKS.parent / 'tools' / 'create_task_docs.py').resolve()}`", context)
            self.assertNotIn("# 작업 문서 도구", run("SessionStart", "worker"))

    # SubagentStart는 SUB.md만, SessionStart는 MAIN.md만 주입하는지 확인한다.
    def test_mode_doc_only(self):
        sub = (ROLES / "worker" / "SUB.md").read_text(encoding="utf-8").strip()
        main = (ROLES / "worker" / "MAIN.md").read_text(encoding="utf-8").strip()
        for flags in ((), ("--include-role",)):
            self.assertIn(sub, run("SubagentStart", "jelly", *flags))
            self.assertNotIn(main, run("SubagentStart", "jelly", *flags))
            self.assertIn(main, run("SessionStart", "jelly", *flags))
            self.assertNotIn(sub, run("SessionStart", "jelly", *flags))

    # 이름 선언 줄은 주입 내용의 앞 2,000바이트 안에 있다.
    def test_name_in_preview(self):
        for role in ("director", "assistant", "worker"):
            soul = (ROLES / role / "SOUL.md").read_text(encoding="utf-8").splitlines()
            name = next(line for line in soul if line.startswith("넌 "))
            for event in ("SessionStart", "SubagentStart"):
                head = run(event, role).encode("utf-8")[:2000].decode("utf-8", "ignore")
                self.assertIn(name, head)


# 역할 이름과 Codex 에이전트 이름의 대응은 .codex/agents의 toml 파일 이름이다.
CODEX = HOOKS.parent / ".codex" / "agents"
# runner별 도구 이름 차이이다. 주입 본문을 비교하기 전에 Claude 이름을 Codex 이름으로 바꾼다.
TOOL_NAMES = {"AskUserQuestion": "request_user_input"}


# 도구 이름 차이를 Codex 이름으로 통일한다.
def same_tools(text):
    for claude_name, codex_name in TOOL_NAMES.items():
        text = text.replace(claude_name, codex_name)
    return text


class RunnerParity(unittest.TestCase):
    """Claude와 Codex가 같은 역할 설명과 주입 본문을 쓰는지 비교한다. (요구사항 41)"""

    # Codex 에이전트 toml의 description이 Claude 역할 정의의 description 원문과 같다.
    def test_description_matches(self):
        import re
        import tomllib
        for role_dir in sorted(ROLES.iterdir()):
            text = (role_dir / "AGENTS.md").read_text(encoding="utf-8")
            name = re.search(r"(?m)^name: (\S+)$", text)[1]
            claude = re.search(r'(?m)^description: "(.*)"$', text)[1]
            codex = tomllib.loads((CODEX / f"{name}.toml").read_text(encoding="utf-8"))["description"]
            self.assertEqual(codex, claude, role_dir.name)

    # 두 runner의 주입 본문이 같다. Claude는 역할 정의 본문을 직접 받고 hook은 성향과 작업 지침을 넣으며, Codex는 hook이 역할 본문까지 넣는다.
    def test_injected_body_matches(self):
        import re
        for role_dir in sorted(ROLES.iterdir()):
            role = role_dir.name
            name = re.search(r"(?m)^name: (\S+)$", (role_dir / "AGENTS.md").read_text(encoding="utf-8"))[1]
            body = (role_dir / "AGENTS.md").read_text(encoding="utf-8").split("---", 2)[2].strip()
            for event in ("SessionStart", "SubagentStart"):
                with self.subTest(role=role, event=event):
                    claude = run(event, role)
                    codex = run(event, name, "--include-role")
                    self.assertIn(same_tools(body), same_tools(codex))
                    for part in claude.split("\n\n---\n\n"):
                        self.assertIn(same_tools(part), same_tools(codex))


if __name__ == "__main__":
    unittest.main()
