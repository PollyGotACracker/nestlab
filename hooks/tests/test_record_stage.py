"""record_stage.py 머리줄 판정과 대상 작업 판정을 확인한다. (요구사항 21, 24)"""
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import check_invalid_response
import record_stage

TASK = "20261110-005442-director-misstep-hooks"
OTHER = "20261111-010101-other"
STATUS = "# 작업\n\n- 작업 ID: {task}\n- 작업 상태: 미개시\n- 작업 단계: 착수\n\n## 요구사항\n"


class RecordStage(unittest.TestCase):
    # 임시 저장소에 경과 문서 둘을 만들고, 세션 기록은 두 번째 작업 지정을 뺀 TASK의 문서 생성기 호출 하나로 구성한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        patcher = unittest.mock.patch.object(check_invalid_response, "INCIDENT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        for task in (TASK, OTHER):
            (self.root / "log" / "task" / task).mkdir(parents=True)
            (self.root / "log" / "task" / task / "status.md").write_text(STATUS.format(task=task), encoding="utf-8")
        call = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {
            "command": f"nestlab-task-docs --task {TASK} --title 작업 --stage kickoff", "description": "문서 생성"}}]}}
        self.log = self.root / "session.jsonl"
        self.log.write_text(json.dumps(call, ensure_ascii=False) + "\n", encoding="utf-8")

    # 작업 단계 값이다.
    def stage(self, task=TASK):
        text = (self.root / "log" / "task" / task / "status.md").read_text(encoding="utf-8")
        return text.split("- 작업 단계: ")[1].splitlines()[0]

    # 입력을 판정한다.
    def run_hook(self, prompt, session="abcdef123456"):
        return record_stage.decide({"prompt": prompt, "cwd": str(self.root), "transcript_path": str(self.log), "session_id": session})

    # 작업ID 없는 단계 키워드는 세션 기록의 마지막 작업 지정을 대상으로 기록하고 조사 단계에서 빠진 작업 문서를 만든다.
    def test_keyword_uses_last_designation(self):
        message = self.run_hook("[research]\n본문")
        self.assertEqual(self.stage(), "조사")
        self.assertIn("조사", message)
        for name in ("research.md", "refute.md", "spec.md"):
            self.assertTrue((self.root / "log" / "task" / TASK / name).is_file(), name)
        self.assertEqual(self.stage(OTHER), "착수")

    # 머리줄의 작업ID는 세션 기록보다 우선하고, 기존 문서 본문은 보존한다.
    def test_task_id_in_head_line(self):
        self.run_hook(f"[implement {OTHER}]\n본문")
        self.assertEqual(self.stage(OTHER), "구현")
        self.assertEqual(self.stage(), "착수")
        self.assertIn("## 요구사항", (self.root / "log" / "task" / OTHER / "status.md").read_text(encoding="utf-8"))
        self.run_hook(f"[kickoff {OTHER}]")
        self.assertEqual(self.stage(OTHER), "착수")

    # 작업ID 없는 [kickoff]는 새 작업으로 보고 이전 작업 문서를 바꾸지 않는다.
    def test_kickoff_without_id_keeps_previous(self):
        before = (self.root / "log" / "task" / TASK / "status.md").read_text(encoding="utf-8")
        self.run_hook("[research]")
        message = self.run_hook("[kickoff]")
        self.assertEqual(self.stage(), "조사")
        self.assertIn("새 작업", message)
        self.assertNotEqual(before, (self.root / "log" / "task" / TASK / "status.md").read_text(encoding="utf-8"))

    # 머리줄 형식이 아니거나 경과 문서가 없는 작업ID이면 기록하지 않고 사유를 주입한다. 대괄호가 없으면 출력이 없다.
    def test_invalid_head_line_not_recorded(self):
        message = self.run_hook("[research] 그리고 이거 해")
        self.assertEqual(self.stage(), "착수")
        self.assertEqual(message, record_stage.REASON_FORMAT)
        message = self.run_hook("[implement 20261112-000000-none]")
        self.assertIn("경과 문서가 없어", message)
        self.assertFalse((self.root / "log" / "task" / "20261112-000000-none").exists())
        self.assertIsNone(self.run_hook("대괄호 없는 첫 줄\n[research]"))


class IncidentLink(RecordStage):
    """미전달 사건 문서가 있는 세션의 다음 사용자 메시지에 사건 문서 링크를 주입한다. (요구사항 17)"""

    # 사건 문서 링크는 세션 기록에 같은 안내가 없을 때만 주입하고, 다른 세션의 사건 문서는 주입하지 않는다.
    def test_undelivered_incident_injected_once(self):
        folder = self.root / "log" / "incident"
        folder.mkdir(parents=True)
        (folder / "20261112-abcdef12.md").write_text("# 사건\n", encoding="utf-8")
        (folder / "20261112-99999999.md").write_text("# 다른 세션\n", encoding="utf-8")
        message = self.run_hook("머리줄 없는 메시지")
        self.assertIn("사건 문서 링크: [20261112-abcdef12](log/incident/20261112-abcdef12.md)", message)
        self.assertNotIn("99999999", message)
        with self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "attachment", "attachment": {"type": "hook_additional_context", "content": [message]}},
                               ensure_ascii=False) + "\n")
        self.assertIsNone(self.run_hook("머리줄 없는 메시지"))


if __name__ == "__main__":
    unittest.main()
