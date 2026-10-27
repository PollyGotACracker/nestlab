"""load_agent.py 모드별 지침 주입 테스트"""
import json
import subprocess
import sys
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
    # SubagentStart는 SUB.md만, SessionStart는 MAIN.md만 주입하는지 확인한다.
    def test_mode_doc_only(self):
        sub = (ROLES / "worker" / "SUB.md").read_text(encoding="utf-8").strip()
        main = (ROLES / "worker" / "MAIN.md").read_text(encoding="utf-8").strip()
        for flags in ((), ("--include-role",)):
            self.assertIn(sub, run("SubagentStart", "jelly", *flags))
            self.assertNotIn(main, run("SubagentStart", "jelly", *flags))
            self.assertIn(main, run("SessionStart", "jelly", *flags))
            self.assertNotIn(sub, run("SessionStart", "jelly", *flags))


if __name__ == "__main__":
    unittest.main()
