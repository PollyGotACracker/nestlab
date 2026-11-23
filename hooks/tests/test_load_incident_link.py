"""load_incident_link.py 미전달 사건 문서 링크 주입을 확인한다."""
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import check_invalid_response
import load_incident_link


class IncidentLink(unittest.TestCase):
    # 임시 저장소를 사건 문서 루트로 정하고 사건 문서 두 개를 만든다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        patcher = unittest.mock.patch.object(check_invalid_response, "INCIDENT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        folder = self.root / ".agent-logs" / "incident"
        folder.mkdir(parents=True)
        (folder / "20261112-abcdef12.md").write_text("# 사건\n", encoding="utf-8")
        (folder / "20261112-99999999.md").write_text("# 다른 세션\n", encoding="utf-8")
        self.log = self.root / "session.jsonl"
        self.log.write_text("", encoding="utf-8")

    # 입력의 미전달 안내 문장 목록을 하나로 이어 반환한다.
    def notices(self, session="abcdef123456"):
        data = {"cwd": str(self.root), "transcript_path": str(self.log), "session_id": session}
        return "\n".join(load_incident_link.undelivered_incidents(data))

    # 사건 문서 링크는 세션 기록에 같은 안내가 없을 때만 주입하고, 다른 세션의 사건 문서는 주입하지 않는다.
    def test_undelivered_incident_injected_once(self):
        message = self.notices()
        self.assertIn("사건 문서 링크: [20261112-abcdef12](.agent-logs/incident/20261112-abcdef12.md)", message)
        self.assertIn("해당 문제를 분석하는 중입니다", message)
        self.assertNotIn("99999999", message)
        with self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "attachment", "attachment": {"type": "hook_additional_context", "content": [message]}},
                               ensure_ascii=False) + "\n")
        self.assertEqual(self.notices(), "")

    # 세션 ID가 사건 문서에 없으면 주입하지 않는다.
    def test_other_session_gets_nothing(self):
        self.assertEqual(self.notices(session="00000000zzzz"), "")


if __name__ == "__main__":
    unittest.main()
