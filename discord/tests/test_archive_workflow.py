"""지식 저장소 승인 상태와 브랜치 경계를 검증한다."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "bot" / "archive_workflow.py"
SPEC = importlib.util.spec_from_file_location("archive_workflow", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
archive_workflow = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = archive_workflow
SPEC.loader.exec_module(archive_workflow)


class BranchNameTests(unittest.TestCase):
    # 사용자 문자열이 섞이지 않은 `<타입>/<번호>`만 허용한다.
    def test_issue_type_and_number_only_make_branch(self) -> None:
        self.assertEqual(archive_workflow.make_branch_name("docs", 123), "docs/123")
        for issue_type, number in (("fix", 1), ("docs", 0), ("docs/#", 1)):
            with self.subTest(issue_type=issue_type, number=number):
                with self.assertRaises(ValueError):
                    archive_workflow.make_branch_name(issue_type, number)

    # ref 경로를 모호하게 만드는 연속·선행·후행 slash를 거부한다.
    def test_slash_edge_cases_are_rejected(self) -> None:
        pattern = archive_workflow.BRANCH_PATTERN
        for branch in ("/docs/1", "docs//1", "docs/1/", "docs/#1"):
            self.assertIsNone(pattern.fullmatch(branch))


class WorkflowStoreTests(unittest.TestCase):
    # 임시 저장 파일과 저장소 객체를 준비한다.
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "workflow.json"
        self.store = archive_workflow.ArchiveWorkflowStore(self.path)

    # 임시 폴더를 정리한다.
    def tearDown(self) -> None:
        self.temporary.cleanup()

    # Issue 승인 없이 commit 승인 단계로 건너뛰지 못한다.
    def test_change_cannot_be_staged_before_issue(self) -> None:
        with self.assertRaisesRegex(ValueError, "Issue"):
            self.store.stage("change", 10, 20, {
                "operations": [{"type": "create", "path": "a.md", "content": "a"}],
                "commit_message": "docs: a", "source_commit_sha": "base",
            })

    # 다른 사용자의 승인과 재시작 뒤 pending 변조를 막는다.
    def test_requester_owns_pending_and_state_survives_reload(self) -> None:
        pending = self.store.stage("issue", 10, 20, {
            "issue_type": "docs", "title": "[DOCS] 제목", "body": "본문",
            "labels": ["docs"], "template_path": ".github/ISSUE_TEMPLATE/docs.md",
            "template_sha": "template",
        })
        with self.assertRaises(PermissionError):
            self.store.pending_for(10, 21)
        loaded = archive_workflow.ArchiveWorkflowStore.load(self.path)
        self.assertEqual(loaded.pending_for(10, 20).pending_id, pending.pending_id)

    # 수정 요청 뒤에는 이전 미리보기가 아니라 새 pending만 승인된다.
    def test_one_active_pending_is_replaced_by_revision(self) -> None:
        first = self.store.stage("issue", 10, 20, {
            "issue_type": "docs", "title": "[DOCS] 이전", "body": "이전", "labels": ["docs"],
            "template_path": ".github/ISSUE_TEMPLATE/docs.md", "template_sha": "one",
        })
        second = self.store.stage("issue", 10, 20, {
            "issue_type": "docs", "title": "[DOCS] 새 변경", "body": "새 변경", "labels": ["docs"],
            "template_path": ".github/ISSUE_TEMPLATE/docs.md", "template_sha": "two",
        })
        self.assertNotEqual(first.pending_id, second.pending_id)
        self.assertEqual(self.store.pending_for(10, 20).pending_id, second.pending_id)

    # 커밋 승인과 PR 승인을 별도 단계로 유지한다.
    def test_pr_requires_verified_change(self) -> None:
        workflow = self.store.thread(10)
        workflow.issue_number = 7
        workflow.issue_type = "docs"
        workflow.branch = "docs/7"
        with self.assertRaisesRegex(ValueError, "커밋"):
            self.store.stage("pr", 10, 20, {"title": "PR", "body": "본문"})

    # 여러 파일 작업과 커밋 메시지를 pending 하나에서 변형 없이 유지한다.
    def test_change_set_keeps_operations_and_message_together(self) -> None:
        workflow = self.store.thread(10)
        workflow.issue_number = 7
        workflow.issue_type = "docs"
        workflow.branch = "docs/7"
        operations = [
            {"type": "create", "path": "a.md", "content": "a", "expected_sha": None},
            {"type": "update", "path": "b.md", "content": "b", "expected_sha": "old"},
            {"type": "delete", "path": "c.md", "expected_sha": "gone"},
        ]
        message = "docs: 지식 문서 정리\n\n본문\n\nResolves: #7\nSee also: None"
        pending = self.store.stage("change", 10, 20, {
            "operations": operations,
            "commit_message": message,
            "source_commit_sha": "base",
        })
        self.assertEqual(pending.data["operations"], operations)
        self.assertEqual(pending.data["commit_message"], message)
        self.assertEqual(pending.data["branch"], "docs/7")

    # 접두사, 제목 길이, Resolves 없이도 메시지를 그대로 보관한다.
    def test_change_message_has_no_fixed_format_constraints(self) -> None:
        workflow = self.store.thread(10)
        workflow.issue_number = 7
        workflow.issue_type = "bug"
        workflow.branch = "bug/7"
        for message in ("fix: 오류 수정", "docs: " + "가" * 41, "docs: 문서 정리\n\n본문"):
            with self.subTest(message=message):
                pending = self.store.stage("change", 10, 20, {
                    "operations": [{"type": "create", "path": "a.md", "content": "a"}],
                    "commit_message": message,
                    "source_commit_sha": "base",
                })
                self.assertEqual(pending.data["commit_message"], message)

    # 형식 검사를 없애도 필수 입력의 누락과 빈 값을 거부한다.
    def test_change_requires_message_operations_and_source_sha(self) -> None:
        workflow = self.store.thread(10)
        workflow.issue_number = 7
        workflow.issue_type = "docs"
        workflow.branch = "docs/7"
        valid = {
            "operations": [{"type": "create", "path": "a.md", "content": "a"}],
            "commit_message": "docs: 문서 정리",
            "source_commit_sha": "base",
        }
        for key, empty in (("commit_message", ""), ("operations", []), ("source_commit_sha", "")):
            for missing in (True, False):
                with self.subTest(key=key, missing=missing):
                    data = dict(valid)
                    if missing:
                        del data[key]
                    else:
                        data[key] = empty
                    with self.assertRaisesRegex(ValueError, "전체 변경 작업"):
                        self.store.stage("change", 10, 20, data)
                    self.assertIsNone(workflow.pending)


if __name__ == "__main__":
    unittest.main()
