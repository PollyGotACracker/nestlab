"""return_research_path.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트와 공용 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common")]

import io
import json
import tempfile
import unittest
from unittest.mock import patch

import return_research_path as hook
import sub_docs

DOC = "# t\n\n## 조사·반증 루프\n\n| 회차 | 반증 건수 | 정지 여부 |\n| ---- | --------- | --------- |\n| 1 | 1 | 계속 |\n"
ASK = "작업 종류: 조사\n조사 문서: log/state/t-research.md"
REFUTE = "작업 종류: 반증\n조사 문서: log/state/t-research.md"


class Replace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "log" / "state").mkdir(parents=True)
        (root / "log" / "state" / "t-research.md").write_text(DOC, encoding="utf-8")
        self.patch = patch.object(sub_docs, "project_root", return_value=root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def claude(self, target, prompt, resp=None):
        return {"tool_name": "Agent", "tool_input": {"subagent_type": target, "prompt": prompt},
                "tool_response": resp or {"status": "completed", "agentId": "a1", "usage": {"x": 1},
                                          "content": [{"type": "text", "text": "긴 응답 전문"}]}}

    def test_claude_content_replaced_other_fields_kept(self):
        out = hook.replace(self.claude("nico", ASK), "claude")["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUse")
        new = out["updatedToolOutput"]
        self.assertEqual(new["content"], [{"type": "text", "text": "결과 저장: log/state/t-research.md 최종 조사 원문"}])
        self.assertEqual((new["agentId"], new["usage"]), ("a1", {"x": 1}))

    def test_reviewer_slot_is_latest_round(self):
        new = hook.replace(self.claude("ricky", REFUTE), "claude")["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(new["content"][0]["text"], "결과 저장: log/state/t-research.md 1회차 반증 원문")

    def test_codex_block_reason(self):
        data = {**self.claude("researcher", ASK), "tool_name": "spawn_agent",
                "tool_input": {"agent_type": "researcher", "message": ASK}}
        self.assertEqual(hook.replace(data, "codex"),
                         {"decision": "block", "reason": "결과 저장: log/state/t-research.md 최종 조사 원문"})

    def test_untouched_calls(self):
        self.assertIsNone(hook.replace(self.claude("nico", "입력 문서: log/state/t-input.md"), "claude"))
        self.assertIsNone(hook.replace(self.claude("jelly", ASK), "claude"))
        self.assertIsNone(hook.replace(self.claude("nico", ASK, {"status": "async_launched"}), "claude"))
        self.assertIsNone(hook.replace(self.claude("nico", ASK.replace("t-", "none-")), "claude"))
        self.assertIsNone(hook.replace({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "claude"))

    def test_main_outputs_json(self):
        out = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(self.claude("nico", ASK)))), \
                patch.object(sys, "stdout", out), patch.object(sys, "argv", ["x", "--runner", "claude"]):
            self.assertEqual(hook.main(), 0)
        self.assertIn("updatedToolOutput", out.getvalue())


if __name__ == "__main__":
    unittest.main()
