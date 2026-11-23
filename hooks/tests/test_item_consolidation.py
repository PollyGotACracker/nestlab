"""확정 질문의 확정 답이 경과 문서 검토 필요 절과 명세 요구사항 목록에만 기록되는지 검증한다."""
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
PLAN = ("# 명세\n\n## 요구사항 목록\n\n### 2. 이름\n\n#### 사용자 확인 시각\n\n<YYYY-MM-DD HH:MM:SS>\n\n#### 문제\n\n문제 문장.\n\n"
        "#### 목표\n\n- <요구사항의 목표>\n\n#### 기대 결과\n\n- <목표를 달성했을 때 기대되는 결과>\n\n"
        "#### 한계\n\n- <한계>\n\n#### 잠재 위험\n\n- <위험>\n\n## 작업 계획\n")
DISPLAY = ("## 확정안 2. 이름\n\n- 문제: 문제 문장.\n- 목표: 처리 방법을 정한다.\n- 기대 결과: 처리한다.\n"
           "- 한계: 없음 (출처: hooks/a.py:1, 확인 방법: 열람)\n- 잠재 위험: 없음 (출처: hooks/a.py:1)\n"
           "- 사후 실측: 없음\n- 사후 실측 사유: 없음\n")


class Consolidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.object(sub_docs, "project_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.status = self.root / ".agent-logs/task/20261104-234558-test/status.md"
        self.status.parent.mkdir(parents=True)
        self.status.write_text(STATE, encoding="utf-8")
        self.spec = self.status.with_name("spec.md")
        self.spec.write_text(PLAN, encoding="utf-8")
        self.research = self.status.with_name("research.md")
        self.research.write_text("# 조사\n", encoding="utf-8")

    # 두 runner의 실제 답 출력 형식에서 확정 답이 경과 문서 한 줄과 명세 요구사항으로만 기록되는지 확인한다.
    def test_runner_answers_stay_in_status_and_spec(self):
        review = "요구사항 2. 이름: 처리 방법을 정한다. (작업 1104-234558)"
        questions = [{"id": "rev", "question": review, "options": [
            {"label": "확정", "description": "처리한다."}, {"label": "폐기"}]}]
        shown = {"type": "assistant", "message": {"content": [{"type": "text", "text": DISPLAY}]}}
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.status.write_text(STATE, encoding="utf-8")
                self.spec.write_text(PLAN, encoding="utf-8")
                if runner == "claude":
                    rows = [{"type": "user", "message": {"content": "시작"}}, shown,
                            {"type": "assistant", "message": {"content": [
                                {"type": "tool_use", "id": "q", "name": "AskUserQuestion", "input": {"questions": questions}}]}},
                            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "q", "content": "답"}]},
                             "toolUseResult": {"answers": {review: "확정"}}}]
                else:
                    rows = [{"type": "response_item", "payload": payload} for payload in (
                        {"type": "message", "role": "user", "content": [{"text": "시작"}]},
                        {"type": "message", "role": "assistant", "content": [{"text": DISPLAY}]},
                        {"type": "function_call", "name": "request_user_input", "call_id": "q",
                         "arguments": json.dumps({"questions": questions})},
                        {"type": "function_call_output", "call_id": "q", "output": json.dumps({"answers": {
                            "rev": {"answers": ["확정"]}}})})]
                transcript = self.root / "input.jsonl"
                transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
                with patch.object(sub_question, "project_root", return_value=self.root):
                    sub_record.record_answers({"cwd": str(self.root), "transcript_path": str(transcript)}, runner)
                status, spec = self.status.read_text(encoding="utf-8"), self.spec.read_text(encoding="utf-8")
                self.assertIn("- 검토 2. 이름: 확정 (사용자 검토 시각: ", status)
                self.assertNotIn("근거 위치", status)
                self.assertIn("### 2. 이름", spec)
                self.assertIn("#### 기대 결과\n\n- 처리한다.\n", spec)
                self.assertIn("#### 목표\n\n- 처리 방법을 정한다.\n", spec)
                self.assertNotIn("작업 1104-234558", status + spec)
                self.assertIn("승인 대기", status)
                self.assertEqual(self.research.read_text(encoding="utf-8"), "# 조사\n")


if __name__ == "__main__":
    unittest.main()
