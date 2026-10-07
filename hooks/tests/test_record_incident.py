"""record_incident.py 동작 테스트. 실행: python hooks/tests/test_record_incident.py"""
import sys
from pathlib import Path

# 진입 스크립트, 공용 모듈, check_tool_use 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import record_incident as hook


class RecordIncident(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "incident"
        self.patch = patch.object(hook, "INCIDENT", self.dir)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def run_hook(self, message, **extra):
        hook.record({"session_id": "abcdef123456", "agent_type": "rio", "last_assistant_message": message, **extra})
        return list(self.dir.glob("*.md"))

    def test_admission_recorded(self):
        files = self.run_hook("제 판단으로 설계를 뒤집었어요.")
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertIn("조치 상태: 미착수", text)
        self.assertIn("감지 표현: 제 판단으로", text)

    def test_second_hit_appends_same_file(self):
        self.run_hook("놓쳤어요.")
        files = self.run_hook("빠뜨렸어요.")
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_text(encoding="utf-8").count("감지 표현"), 2)

    def test_real_admissions_detected(self):
        samples = ("네, 제 잘못입니다.", "둘 다 제가 틀렸습니다.", "오늘 docs를 빼먹었습니다.",
                   "# 루프 설계 (빠뜨린 부분)", "제 사전 검토 누락입니다.", "제가 지시를 잘못 해석해서 적었습니다.",
                   "제가 번호를 잘못 맞췄습니다.", "확정 8을 제가 직접 어긴 것입니다.",
                   "지금 실제 파일 확인 안 하고 제안했습니다.", "제가 근거 없이 말했습니다.",
                   "제 분류 착오였습니다.", "제가 엉뚱한 쪽으로 갔어요.", "계속 망가뜨리고 있습니다.",
                   "앞의 제안은 철회합니다.", "토큰을 크게 낭비했습니다.")
        for text in samples:
            self.assertTrue(hook.ADMIT.search(text), text)

    def test_plain_response_ignored(self):
        self.assertEqual(self.run_hook("검증을 끝냈어요."), [])

    def test_code_block_ignored(self):
        self.assertEqual(self.run_hook("```text\n죄송\n```\n완료"), [])

    def test_subagent_ignored(self):
        self.assertEqual(self.run_hook("죄송해요.", agent_id="sub-1"), [])


if __name__ == "__main__":
    unittest.main()
