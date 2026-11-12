"""검토 확정 답이 경과 문서 검토 필요 절과 명세 요구사항 목록에만 기록되는지 검증한다."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]
import isolate_incidents  # noqa: F401 사건 문서를 저장소에 쓰지 않는다.
import check_workflow_response as hook
import sub_docs
import sub_question
import sub_record

STATE = ("# 작업\n- 작업 상태: 진행\n## 실행 승인 범위\n- 사용자 승인 상태: 승인\n### 검토 필요\n\n"
         "- 검토 <번호>. <이름>: <확정 안 | 폐기> (사용자 검토 시각: <YYYY-MM-DD HH:MM:SS>)\n")
PLAN = "# 명세\n\n## 요구사항 목록\n\n### 1. 하나\n\n- 기대 결과:\n  - 기존 결과\n\n## 작업 계획\n"


class Consolidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.object(sub_docs, "project_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.status = self.root / "log/task/20261104-234558-test/status.md"
        self.status.parent.mkdir(parents=True)
        self.status.write_text(STATE, encoding="utf-8")
        self.spec = self.status.with_name("spec.md")
        self.spec.write_text(PLAN, encoding="utf-8")
        self.research = self.status.with_name("research.md")
        self.research.write_text("# 조사\n", encoding="utf-8")

    # 두 runner의 실제 답 출력 형식에서 검토 답이 경과 문서 한 줄과 명세 요구사항으로만 기록되는지 확인한다.
    def test_runner_answers_stay_in_status_and_spec(self):
        review = "검토 2. 이름: 처리 방법을 정한다. (문제 1) (작업 1104-234558)"
        questions = [{"id": "rev", "question": review, "options": [{"label": "안 1. 선택", "description": "처리한다."}]}]
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.status.write_text(STATE, encoding="utf-8")
                self.spec.write_text(PLAN, encoding="utf-8")
                if runner == "claude":
                    rows = [{"type": "user", "message": {"content": "시작"}},
                            {"type": "assistant", "message": {"content": [
                                {"type": "tool_use", "id": "q", "name": "AskUserQuestion", "input": {"questions": questions}}]}},
                            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "q", "content": "답"}]},
                             "toolUseResult": {"answers": {review: "안 1. 선택"}}}]
                else:
                    rows = [{"type": "response_item", "payload": payload} for payload in (
                        {"type": "message", "role": "user", "content": [{"text": "시작"}]},
                        {"type": "function_call", "name": "request_user_input", "call_id": "q",
                         "arguments": json.dumps({"questions": questions})},
                        {"type": "function_call_output", "call_id": "q", "output": json.dumps({"answers": {
                            "rev": {"answers": ["안 1. 선택"]}}})})]
                transcript = self.root / "input.jsonl"
                transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
                with patch.object(sub_question, "project_root", return_value=self.root):
                    sub_record.record_answers({"cwd": str(self.root), "transcript_path": str(transcript)}, runner)
                status, spec = self.status.read_text(encoding="utf-8"), self.spec.read_text(encoding="utf-8")
                self.assertIn("- 검토 2. 이름: 안 1. 선택 (사용자 검토 시각: ", status)
                self.assertNotIn("근거 위치", status)
                self.assertIn("### 2. 이름", spec)
                self.assertIn("  - 안 1. 선택: 처리한다.", spec)
                self.assertNotIn("작업 1104-234558", status + spec)
                self.assertIn("승인 대기", status)
                self.assertEqual(self.research.read_text(encoding="utf-8"), "# 조사\n")

    # 폐기 답이 기록된 번호는 폐기 번호로 읽고 새 번호를 안내한다.
    def test_used_numbers_read_status(self):
        self.status.write_text(STATE.replace("- 검토 <번호>. <이름>: <확정 안 | 폐기> (사용자 검토 시각: <YYYY-MM-DD HH:MM:SS>)",
                                             "- 검토 3. X: 폐기 (사용자 검토 시각: 2026-11-12 03:00:00)"), encoding="utf-8")
        self.assertEqual(sub_question.used_numbers(self.status), ({3}, 4))


if __name__ == "__main__":
    unittest.main()
