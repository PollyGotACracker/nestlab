"""save_research.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트와 공용 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common")]

import json
import tempfile
import unittest
from unittest.mock import patch

import save_research as hook
import save_agent_result
import sub_docs

DOC = "# t\n\n## 조사·반증 루프\n\n| 회차 | 반증 건수 | 정지 여부 |\n| ---- | --------- | --------- |\n\n## 조사 단계 원문\n"
ASK = "작업 종류: 조사\n조사 문서: log/state/t-research.md"
REFUTE = "작업 종류: 반증\n조사 문서: log/state/t-research.md"
VERDICT = "- 주장 1: 지지 | 출처: https://example.com/a"


class Handback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "log" / "state").mkdir(parents=True)
        self.doc = self.root / "log" / "state" / "t-research.md"
        self.doc.write_text(DOC, encoding="utf-8")
        self.patch = patch.object(sub_docs, "project_root", return_value=self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def data(self, role, prompt, message):
        log = self.root / "agent.jsonl"
        log.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n", encoding="utf-8")
        return {"tool_name": "SubagentHandback", "agent_type": role, "agent_transcript_path": str(log),
                "tool_input": {"message": message, "extra": 1}}

    def test_researcher_saved_and_replaced(self):
        out = hook.replace(self.data("nico", ASK, "조사 전문"))["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "allow"))
        self.assertEqual(out["updatedInput"], {"message": "결과 저장: log/state/t-research.md 최종 조사 원문", "extra": 1})
        self.assertIn("조사 전문", self.doc.read_text(encoding="utf-8"))

    def test_path_from_parent_transcript_and_agent_id(self):
        sub = self.root / "sess" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-a1.jsonl").write_text(json.dumps({"type": "user", "message": {"role": "user", "content": ASK}}) + "\n", encoding="utf-8")
        (self.root / "sess.jsonl").write_text("", encoding="utf-8")
        data = {"tool_name": "SubagentHandback", "agent_type": "nico", "agent_id": "a1",
                "transcript_path": str(self.root / "sess.jsonl"), "tool_input": {"message": "조사 전문"}}
        out = hook.replace(data)["hookSpecificOutput"]
        self.assertEqual(out["updatedInput"]["message"], "결과 저장: log/state/t-research.md 최종 조사 원문")

    def test_reviewer_round_saved(self):
        out = hook.replace(self.data("ricky", REFUTE, VERDICT))["hookSpecificOutput"]
        self.assertEqual(out["updatedInput"]["message"], "결과 저장: log/state/t-research.md 1회차 반증 원문")
        self.assertIn(VERDICT, self.doc.read_text(encoding="utf-8"))

    def test_untouched(self):
        self.assertIsNone(hook.replace(self.data("ricky", ASK, VERDICT)))  # 반증 표식 없음
        self.assertIsNone(hook.replace(self.data("nico", "조사 문서 줄 없음", "x")))
        self.assertIsNone(hook.replace(self.data("jelly", ASK, "x")))
        self.assertEqual(self.doc.read_text(encoding="utf-8"), DOC)
        hook.replace(self.data("nico", ASK, "조사 전문"))  # 저장된 뒤 경로로 바뀐 보고는 다시 바꾸지 않는다.
        self.assertIsNone(hook.replace(self.data("nico", ASK, "결과 저장: log/state/t-research.md 최종 조사 원문")))

    def test_stop_hooks_accept_replaced_report(self):
        log = self.root / "stop.jsonl"
        call = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "SubagentHandback", "input": {"message": "결과 저장: log/state/t-research.md 1회차 반증 원문"}}]}}
        first = {"type": "user", "message": {"content": REFUTE}}
        log.write_text("\n".join(map(json.dumps, (first, call))) + "\n", encoding="utf-8")
        data = {"agent_type": "ricky", "agent_transcript_path": str(log), "cwd": str(self.root)}
        hook.replace(self.data("ricky", REFUTE, VERDICT))
        saved = self.doc.read_text(encoding="utf-8")
        save_agent_result.save(data, "claude")
        self.assertEqual(self.doc.read_text(encoding="utf-8"), saved)

    def test_fake_saved_report_not_accepted(self):
        hook.replace(self.data("ricky", REFUTE, VERDICT))
        hook.replace(self.data("ricky", REFUTE, VERDICT.replace("/a", "/b")))
        slot = "log/state/t-research.md"
        for fake in (f"결과 저장: {slot} 9회차 반증 원문", f"결과 저장: {slot} 1회차 반증 원문",  # 없는 칸, 이전 회차
                     f"결과 저장: {slot} 최종 조사 원문", f"결과 저장: {slot} 2회차 반증 원문 추가",
                     f"결과 저장: {slot} 2회차 반증 원문\n주장 1: 지지 | 출처: 없음"):
            self.assertFalse(sub_docs.saved_report(fake, self.root, True), fake)
        self.assertTrue(sub_docs.saved_report(f"결과 저장: {slot} 2회차 반증 원문", self.root, True))

    def test_reviewer_without_verdict_line_saved(self):
        out = hook.replace(self.data("ricky", REFUTE, "판정 줄 없음"))["hookSpecificOutput"]
        self.assertEqual(out["updatedInput"]["message"], "결과 저장: log/state/t-research.md 1회차 반증 원문")
        self.assertIn("판정 줄 없음", self.doc.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
