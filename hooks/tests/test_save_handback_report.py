"""save_handback_report.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트와 공용 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common")]

import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import save_handback_report as hook
import save_subagent_result
import sub_docs

DOC = "# t\n\n## 조사 단계 원문\n"
LOOP = "# t\n\n## 조사·반증 루프\n\n| 회차 | 반증 건수 | 정지 여부 |\n| ---- | --------- | --------- |\n\n## 회차 기록\n"
ASK = "작업 종류: 조사\n조사 문서: log/task/t/research.md"
REFUTE = "작업 종류: 반증\n반증 문서: log/task/t/refute.md"
RESEARCH_PATH = "log/task/t/research.md"
REFUTE_PATH = "log/task/t/refute.md"
VERDICT = "- 주장 1: 지지 | 출처: https://example.com/a"


class Handback(unittest.TestCase):
    # 임시 저장소와 조사 문서를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "log" / "task" / "t").mkdir(parents=True)
        self.doc = self.root / RESEARCH_PATH
        self.doc.write_text(DOC, encoding="utf-8")
        self.refute = self.root / REFUTE_PATH
        self.refute.write_text(LOOP, encoding="utf-8")
        self.patch = patch.object(sub_docs, "project_root", return_value=self.root)
        self.patch.start()

    # patch와 임시 저장소를 정리한다.
    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    # 서브에이전트 기록과 SubagentHandback 입력을 만드는 보조 함수이다.
    def data(self, role, prompt, message):
        log = self.root / "agent.jsonl"
        log.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n", encoding="utf-8")
        return {"tool_name": "SubagentHandback", "agent_type": role, "agent_transcript_path": str(log),
                "tool_input": {"message": message, "extra": 1}}

    # researcher 보고가 조사 문서에 저장되고 안내 문장으로 바뀌는지 확인한다.
    def test_researcher_saved_and_replaced(self):
        out = hook.replace(self.data("nico", ASK, "조사 전문"))["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "allow"))
        self.assertEqual(out["updatedInput"], {"message": f"조사 결과를 {RESEARCH_PATH}의 `최종 조사 원문` 칸에 저장했습니다.", "extra": 1})
        self.assertIn("조사 전문", self.doc.read_text(encoding="utf-8"))

    # 부모 기록 폴더와 agent id로 하위 기록을 찾는지 확인한다.
    def test_path_from_parent_transcript_and_agent_id(self):
        sub = self.root / "sess" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-a1.jsonl").write_text(json.dumps({"type": "user", "message": {"role": "user", "content": ASK}}) + "\n", encoding="utf-8")
        (self.root / "sess.jsonl").write_text("", encoding="utf-8")
        data = {"tool_name": "SubagentHandback", "agent_type": "nico", "agent_id": "a1",
                "transcript_path": str(self.root / "sess.jsonl"), "tool_input": {"message": "조사 전문"}}
        out = hook.replace(data)["hookSpecificOutput"]
        self.assertEqual(out["updatedInput"]["message"], f"조사 결과를 {RESEARCH_PATH}의 `최종 조사 원문` 칸에 저장했습니다.")

    # reviewer 반증 보고가 회차 칸에 저장되는지 확인한다.
    def test_reviewer_round_saved(self):
        out = hook.replace(self.data("ricky", REFUTE, VERDICT))["hookSpecificOutput"]
        self.assertEqual(out["updatedInput"]["message"], f"조사 결과를 {REFUTE_PATH}의 `1회차 반증 원문` 칸에 저장했습니다.")
        self.assertIn(VERDICT, self.refute.read_text(encoding="utf-8"))
        self.assertEqual(self.doc.read_text(encoding="utf-8"), DOC)

    # 대상이 아닌 보고는 저장하지 않는지 확인한다.
    def test_untouched(self):
        self.assertIsNone(hook.replace(self.data("ricky", ASK, VERDICT)))  # 반증 표식 없음
        self.assertIsNone(hook.replace(self.data("nico", "조사 문서 줄 없음", "x")))
        self.assertIsNone(hook.replace(self.data("jelly", ASK, "x")))
        self.assertEqual(self.doc.read_text(encoding="utf-8"), DOC)
        hook.replace(self.data("nico", ASK, "조사 전문"))  # 저장된 뒤 경로로 바뀐 보고는 다시 바꾸지 않는다.
        self.assertIsNone(hook.replace(self.data("nico", ASK, f"조사 결과를 {RESEARCH_PATH}의 `최종 조사 원문` 칸에 저장했습니다.")))

    # 안내 문장으로 바뀐 보고를 종료 hook이 다시 저장하지 않는지 확인한다.
    def test_stop_hooks_accept_replaced_report(self):
        log = self.root / "stop.jsonl"
        call = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "SubagentHandback", "input": {"message": f"조사 결과를 {REFUTE_PATH}의 `1회차 반증 원문` 칸에 저장했습니다."}}]}}
        first = {"type": "user", "message": {"content": REFUTE}}
        log.write_text("\n".join(map(json.dumps, (first, call))) + "\n", encoding="utf-8")
        data = {"agent_type": "ricky", "agent_transcript_path": str(log), "cwd": str(self.root)}
        hook.replace(self.data("ricky", REFUTE, VERDICT))
        saved = self.refute.read_text(encoding="utf-8")
        save_subagent_result.save(data, "claude")
        self.assertEqual(self.refute.read_text(encoding="utf-8"), saved)

    # 저장된 것처럼 꾸민 보고 문장을 인정하지 않는지 확인한다.
    def test_fake_saved_report_not_accepted(self):
        hook.replace(self.data("ricky", REFUTE, VERDICT))
        hook.replace(self.data("ricky", REFUTE, VERDICT.replace("/a", "/b")))
        slot = REFUTE_PATH
        for fake in (f"조사 결과를 {slot}의 `9회차 반증 원문` 칸에 저장했습니다.", f"조사 결과를 {slot}의 `1회차 반증 원문` 칸에 저장했습니다.",  # 없는 칸, 이전 회차
                     f"조사 결과를 {slot}의 `최종 조사 원문` 칸에 저장했습니다.", f"조사 결과를 {slot}의 `2회차 반증 원문` 칸에 저장했습니다. 추가",
                     f"조사 결과를 {slot}의 `2회차 반증 원문` 칸에 저장했습니다.\n주장 1: 지지 | 출처: 없음"):
            self.assertFalse(sub_docs.saved_report(fake, self.root, True), fake)
        self.assertTrue(sub_docs.saved_report(f"조사 결과를 {slot}의 `2회차 반증 원문` 칸에 저장했습니다.", self.root, True))

    # 판정 줄이 없는 반증 보고가 deny되는지 확인한다.
    def test_reviewer_without_verdict_line_denied(self):
        out = hook.replace(self.data("ricky", REFUTE, "판정 줄 없음"))["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertEqual(self.refute.read_text(encoding="utf-8"), LOOP)

    # 경로가 든 보고도 경로 검사 없이 통과하는지 확인한다.
    def test_report_with_path_allowed(self):
        out = hook.replace(self.data("nico", ASK, "근거는 `chat_state/a.json`과 a.jsonl에 있다"))["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "allow")


if __name__ == "__main__":
    unittest.main()
