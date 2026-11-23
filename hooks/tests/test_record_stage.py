"""record_stage.py 머리줄 판정, 색인 기반 대상 작업 판정과 계획 단계 지침 주입을 확인한다."""
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
import sub_docs
from sub_task_index import refresh_index, selected_status

TASK = "20261110-005442-director-misstep-hooks"
OTHER = "20261111-010101-other"
STATUS = "# 작업\n\n- 작업 ID: {task}\n- 작업 단계: 계획\n\n## 요구사항\n"
STATUS += "## 원인 분석\n- 조건 누락: 입력 검사의 조건 누락\n  - 근거 내용: 입력에서 재현했다.\n  - 출처: hooks/a.py:1\n  - 한계: 없음\n"


class RecordStage(unittest.TestCase):
    # 임시 저장소에 경과 문서 둘을 만들고 색인의 현재 진행 작업을 TASK로 선택한다.
    def setUp(self):
        structure = unittest.mock.patch.object(sub_docs, "structure_reason", return_value=None)
        structure.start()
        self.addCleanup(structure.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        patcher = unittest.mock.patch.object(check_invalid_response, "INCIDENT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        for task in (OTHER, TASK):
            (self.root / ".agent-logs" / "task" / task).mkdir(parents=True)
            (self.root / ".agent-logs" / "task" / task / "status.md").write_text(STATUS.format(task=task), encoding="utf-8")
            refresh_index(self.root, task, current=True)
        self.log = self.root / "session.jsonl"
        self.log.write_text("", encoding="utf-8")

    # 작업 단계 값이다.
    def stage(self, task=TASK):
        text = (self.root / ".agent-logs" / "task" / task / "status.md").read_text(encoding="utf-8")
        return text.split("- 작업 단계: ")[1].splitlines()[0]

    # 입력을 판정한다.
    def run_hook(self, prompt, session="abcdef123456", role="director"):
        return record_stage.decide({"prompt": prompt, "cwd": str(self.root), "transcript_path": str(self.log), "session_id": session, "agent_type": role})

    # 작업ID 없는 단계 키워드는 색인의 현재 진행 작업에 기록하고 계획 단계에서 빠진 작업 문서를 만든다.
    def test_keyword_uses_index_selection(self):
        message = self.run_hook("[plan]\n본문")
        self.assertEqual(self.stage(), "계획")
        self.assertIn(record_stage.RECORDED.format(task=TASK, stage="계획"), message)
        for name in ("research.md", "refute.md", "spec.md"):
            self.assertTrue((self.root / ".agent-logs" / "task" / TASK / name).is_file(), name)
        self.assertFalse((self.root / ".agent-logs" / "task" / OTHER / "spec.md").exists())
        self.assertEqual(selected_status(self.root).parent.name, TASK)

    # 머리줄의 작업ID는 색인 선택보다 우선하고 그 작업을 현재 진행 작업으로 선택하며, 기존 문서 본문은 보존한다.
    def test_task_id_in_head_line(self):
        self.run_hook(f"[implement {OTHER}]\n본문")
        self.assertEqual(self.stage(OTHER), "구현")
        self.assertEqual(self.stage(), "계획")
        self.assertEqual(selected_status(self.root).parent.name, OTHER)
        self.assertIn("## 요구사항", (self.root / ".agent-logs" / "task" / OTHER / "status.md").read_text(encoding="utf-8"))
        index = (self.root / ".agent-logs/task/index.md").read_text(encoding="utf-8")
        self.assertIn(f"({OTHER}/status.md), 구현", index)
        self.run_hook(f"[plan {OTHER}]")
        self.assertEqual(self.stage(OTHER), "계획")

    # 단계가 없는 문서도 메타데이터 영역에 단계 한 줄을 기록한다.
    def test_missing_stage_is_inserted_in_header(self):
        from sub_approval import normalize_stage
        from sub_document import Document, field
        text = f"# 작업\n\n- 작업 ID: {TASK}\n\n## 작업 경과\n- 보존할 내용\n"
        updated = normalize_stage(text, "계획")
        self.assertEqual(field(Document(updated).header(), "작업 단계"), "계획")
        self.assertIn("## 작업 경과\n- 보존할 내용", updated)
        self.assertEqual(updated.count("- 작업 단계:"), 1)

    # 계획 단계의 완료 요청은 명세 누락으로 실패하며 다음 행동을 안내하고 단계를 유지한다.
    def test_failed_completion_preserves_plan(self):
        message = self.run_hook(f"[complete {TASK}]")
        self.assertIn("계획 단계를 유지합니다", message)
        self.assertNotIn("않았습니다", message)
        self.assertEqual(self.stage(), "계획")
        self.assertIn("- 완료 시각: 없음", (self.root / ".agent-logs" / "task" / TASK / "status.md").read_text(encoding="utf-8"))

    # 색인에 현재 진행 작업이 없으면 작업ID 없는 머리줄과 머리줄 없는 메시지는 문서를 바꾸지 않고 미완료 작업 목록과 작업 선택 질문을 안내한다.
    def test_no_selection_requests_index(self):
        (self.root / ".agent-logs/task/index.md").unlink()
        before = (self.root / ".agent-logs" / "task" / TASK / "status.md").read_text(encoding="utf-8")
        for prompt in ("[implement]", "머리줄 없는 요청"):
            with self.subTest(prompt=prompt):
                message = self.run_hook(prompt)
                self.assertTrue(message.startswith(record_stage.REASON_NO_TARGET), message)
                self.assertIn(f"({TASK}/status.md), 계획", message)
                self.assertIn(f"({OTHER}/status.md), 계획", message)
        self.assertEqual(before, (self.root / ".agent-logs" / "task" / TASK / "status.md").read_text(encoding="utf-8"))

    # `[작업ID]`는 단계를 바꾸지 않고 그 작업을 현재 진행 작업으로 선택하고, 없는 작업ID는 없음을 알린다.
    def test_task_only_head_line_selects_task(self):
        message = self.run_hook(f"[{OTHER}]\n본문")
        self.assertIn(record_stage.SELECTED.format(task=OTHER, stage="계획"), message)
        self.assertEqual(selected_status(self.root).parent.name, OTHER)
        self.assertEqual(self.stage(OTHER), "계획")
        self.assertIn(record_stage.REASON_NO_DOC.format(task="20261112-000000-none"), self.run_hook("[20261112-000000-none]"))
        self.assertEqual(selected_status(self.root).parent.name, OTHER)

    # `[plan new]`는 어떤 작업의 단계도 바꾸지 않고 신규 작업 생성 절차를 안내하며, 다른 키워드의 new는 형식 오류이다.
    def test_plan_new_guides_creation(self):
        message = self.run_hook("[plan new]\n새 기능")
        self.assertIn(record_stage.NEW_TASK, message)
        self.assertEqual(selected_status(self.root).parent.name, TASK)
        self.assertEqual(self.stage(), "계획")
        self.assertTrue(self.run_hook("[implement new]").startswith(record_stage.REASON_FORMAT))

    # 머리줄 뒤의 본문과 줄바꿈 표시가 있어도 실제 경과 문서의 단계를 기록한다.
    def test_head_line_suffix_records_stage(self):
        for suffix in (" 본문", "\\\n본문"):
            with self.subTest(suffix=suffix):
                self.run_hook(f"[implement {OTHER}]" + suffix)
                self.assertEqual(self.stage(OTHER), "구현")
                self.assertEqual(self.stage(), "계획")
                self.run_hook(f"[plan {OTHER}]")

    # 머리줄 형식이 아니거나 경과 문서가 없는 작업ID이면 기록하지 않고 수행할 행동을 주입한다.
    def test_invalid_head_line_not_recorded(self):
        message = self.run_hook("[plan 잘못된id] 그리고 이거 해")
        self.assertEqual(self.stage(), "계획")
        self.assertTrue(message.startswith(record_stage.REASON_FORMAT))
        message = self.run_hook("[implement 20261112-000000-none]")
        self.assertIn(record_stage.REASON_NO_DOC.format(task="20261112-000000-none"), message)
        self.assertFalse((self.root / ".agent-logs" / "task" / "20261112-000000-none").exists())
        self.assertNotIn(record_stage.REASON_FORMAT, self.run_hook("대괄호 없는 첫 줄\n[plan]"))

    # 현재 진행 작업이 계획 단계이면 매 턴 지침과 계획 스킬의 출력 검사 절을 주입하고, 구현 단계이면 주입하지 않는다.
    def test_plan_stage_injects_notice_and_checks(self):
        skill = self.root / "SKILL.md"
        skill.write_text("# 계획\n\n## 출력 검사\n\n- 대상: 응답\n- 금지: `근거 출처` 출처 표기\n\n## 다음 절\n본문\n", encoding="utf-8")
        with unittest.mock.patch.object(record_stage, "PLAN_SKILL", skill):
            message = self.run_hook("머리줄 없는 메시지")
            self.assertIn(record_stage.PLAN_NOTICE, message)
            self.assertIn("## 출력 검사\n\n- 대상: 응답\n- 금지: `근거 출처` 출처 표기", message)
            self.assertNotIn("## 다음 절", message)
            self.run_hook(f"[implement {TASK}]")
            self.assertEqual(self.stage(), "구현")
            self.assertIsNone(self.run_hook("머리줄 없는 메시지"))

    # 구현 단계 기록 실패 사유가 해당 양식 대신 log-status 양식 이름으로 끝나는지 확인한다.
    def test_implement_reason_names_template(self):
        status = self.root / ".agent-logs" / "task" / TASK / "status.md"
        status.write_text(STATUS.format(task=TASK) + "- 한계: 외부 서비스 중단\n", encoding="utf-8")
        reason = record_stage.implement_reason(status)
        self.assertTrue(reason.endswith("log-status 양식을 따르십시오."), reason)
        self.assertNotIn("해당 양식", reason)

    # director 밖 역할의 머리줄은 단계를 기록하지 않고 계획 지침도 주입하지 않는다.
    def test_non_director_skips_stage_and_plan_notice(self):
        skill = self.root / "SKILL.md"
        skill.write_text("# 계획\n\n## 출력 검사\n\n- 대상: 응답\n", encoding="utf-8")
        with unittest.mock.patch.object(record_stage, "PLAN_SKILL", skill):
            self.assertIsNone(self.run_hook(f"[implement {TASK}]\n본문", role="assistant"))
            self.assertEqual(self.stage(), "계획")
            self.assertIsNone(self.run_hook("머리줄 없는 메시지", role="assistant"))
            self.assertIn(record_stage.PLAN_NOTICE, self.run_hook("[plan]\n본문"))


class IncidentLink(RecordStage):
    """미전달 사건 문서가 있는 세션의 다음 사용자 메시지에 사건 문서 링크를 주입한다."""

    # 사건 문서 링크는 세션 기록에 같은 안내가 없을 때만 주입하고, 다른 세션의 사건 문서는 주입하지 않는다.
    def test_undelivered_incident_injected_once(self):
        (self.root / ".agent-logs/task/index.md").unlink()
        folder = self.root / ".agent-logs" / "incident"
        folder.mkdir(parents=True)
        (folder / "20261112-abcdef12.md").write_text("# 사건\n", encoding="utf-8")
        (folder / "20261112-99999999.md").write_text("# 다른 세션\n", encoding="utf-8")
        message = self.run_hook("머리줄 없는 메시지")
        self.assertIn("사건 문서 링크: [20261112-abcdef12](.agent-logs/incident/20261112-abcdef12.md)", message)
        self.assertIn("해당 문제를 분석하는 중입니다", message)
        self.assertNotIn("99999999", message)
        self.assertIn("사건 문서 링크", self.run_hook("[plan]", role="assistant"))
        with self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "attachment", "attachment": {"type": "hook_additional_context", "content": [message]}},
                               ensure_ascii=False) + "\n")
        self.assertNotIn("사건 문서 링크", self.run_hook("머리줄 없는 메시지"))


if __name__ == "__main__":
    unittest.main()
