"""명세 문서의 확정 기록, 번호 검사와 쓰기 잠금을 검증한다."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]
import check_workflow_response as hook
import sub_docs
import sub_question
import sub_scope
from sub_workflow import option_gap


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
        self.state = "# 작업\n- 작업 상태: 진행\n## 실행 승인 범위\n- 사용자 승인 상태: 승인\n"
        self.status.write_text(self.state, encoding="utf-8")
        self.spec = self.status.with_name("spec.md")
        self.plan = sub_docs.template_text("nestlab-log-spec")
        self.report = ("#### 검토 1. 이름\n\n| 안 | 기준 |\n| :-- | :-- |\n| 안 1. 선택 | 충족 |\n\n"
                       "##### 안 1. 선택\n\n- 장점: 내용 (출처: hooks/a.py:1)\n"
                       "- 단점: 내용 (출처: hooks/a.py:2)\n")
        self.plan = hook.splice(self.plan, "검토 보고", lambda _: "\n" + self.report + "\n")
        self.spec.write_text(self.plan, encoding="utf-8")
        self.research = self.status.with_name("research.md")
        self.research.write_text("# 조사\n", encoding="utf-8")

    # 두 runner의 실제 답 출력 형식에서 명세 문서만 확정 기록으로 쓰는지 확인한다.
    def test_runner_answers_stay_in_spec(self):
        question = "요구사항 1. 이름: 결과 문장 (작업 1104-234558)"
        review = "검토 1. 이름: 처리 방법을 정한다. (작업 1104-234558)"
        questions = [{"id": "req", "question": question, "options": [{"label": "확정"}]},
                     {"id": "rev", "question": review, "options": [{"label": "안 1. 선택"}]}]
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.status.write_text(self.state, encoding="utf-8")
                self.spec.write_text(self.plan, encoding="utf-8")
                if runner == "claude":
                    rows = [{"type": "user", "message": {"content": "시작"}},
                            {"type": "assistant", "message": {"content": [
                                {"type": "tool_use", "id": "q", "name": "AskUserQuestion", "input": {"questions": questions}}]}},
                            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "q", "content": "답"}]},
                             "toolUseResult": {"answers": {question: "확정", review: "안 1. 선택"}}}]
                else:
                    rows = [{"type": "response_item", "payload": payload} for payload in (
                        {"type": "message", "role": "user", "content": [{"text": "시작"}]},
                        {"type": "function_call", "name": "request_user_input", "call_id": "q",
                         "arguments": json.dumps({"questions": questions})},
                        {"type": "function_call_output", "call_id": "q", "output": json.dumps({"answers": {
                            "req": {"answers": ["확정"]}, "rev": {"answers": ["안 1. 선택"]}}})})]
                transcript = self.root / "input.jsonl"
                transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
                with patch.object(sub_question, "project_root", return_value=self.root):
                    hook.record_answers({"cwd": str(self.root), "transcript_path": str(transcript)}, runner)
                text = self.spec.read_text(encoding="utf-8")
                self.assertIn("  - 결과 문장", text)
                self.assertIn("- 확정: 안 1. 선택", text)
                self.assertIn("명세 문서 > 검토 > 검토 보고", text)
                self.assertNotIn("작업 1104-234558", text)
                self.assertNotIn("검토 필요", self.status.read_text(encoding="utf-8"))
                self.assertIn("승인 대기", self.status.read_text(encoding="utf-8"))
                self.assertEqual(self.research.read_text(encoding="utf-8"), "# 조사\n")

    # 확정 칸 잠금과 새 위치의 안 블록 출처 검사, 폐기 번호 검사를 확인한다.
    def test_guards_read_spec(self):
        self.assertFalse(sub_scope.spec_locked({}, self.spec, self.plan, self.plan))
        changed = hook.splice(self.plan, "검토 필요", lambda _: "\n- 검토 1. 임의 변경\n  - 상태: 검토 완료\n")
        self.assertTrue(sub_scope.spec_locked({}, self.spec, changed, self.plan))
        self.assertIsNone(option_gap(self.plan, ""))
        self.assertIsNotNone(option_gap(self.plan.replace("- 장점:", "- 설명:"), ""))
        dropped = hook.splice(self.plan, "요구사항 목록", lambda _: "\n### 요구사항 3. 폐기\n")
        self.spec.write_text(dropped, encoding="utf-8")
        self.assertEqual(sub_question.used_numbers(self.status, "요구사항"), ({3}, 4))
        self.assertEqual(sub_docs.decision_path(self.status), self.spec)
        self.spec.write_text("# 기존 명세\n", encoding="utf-8")
        self.assertEqual(sub_docs.decision_path(self.status), self.status)

    # 같은 안 제목의 뒤 블록이 정상이어도 앞 검토의 누락을 검사한다.
    def test_same_option_title_in_different_reviews(self):
        for depth in (3, 4):
            with self.subTest(depth=depth):
                heading = "#" * depth
                first = self.report.replace("#### 검토", f"{heading} 검토").replace("##### 안", f"{heading}# 안")
                second = first.replace("검토 1.", "검토 2.")
                report = "## 검토 보고\n" + first.replace("- 장점:", "- 설명:") + second
                self.assertIn("장점", option_gap(report, "") or "")
                self.assertIsNone(option_gap("## 검토 보고\n" + first + second, ""))


if __name__ == "__main__":
    unittest.main()
