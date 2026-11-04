"""작업 문서 주석 삭제와 잠금 비교의 주석 무시를 확인한다."""
import re
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import sub_docs
import sub_scope


class StripComments(unittest.TestCase):
    def test_multiline_comment_removed(self):
        text = "# 제목\n\n<!--\n양식 설명\n-->\n\n- 항목 <!-- 한 줄 -->\n"
        self.assertEqual(sub_docs.strip_comments(text), "# 제목\n\n- 항목\n")

    def test_code_block_kept(self):
        text = "# 조사\n\n```\n<!-- 원문 -->\n```\n"
        self.assertEqual(sub_docs.strip_comments(text), text)

    def test_inline_code_kept(self):
        text = "- `<!--` 표시를 설명한다.\n- 다음 줄 <!-- 지움 -->\n"
        self.assertEqual(sub_docs.strip_comments(text), "- `<!--` 표시를 설명한다.\n- 다음 줄\n")

    def test_outside_log_task_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doc.md"
            path.write_text("a <!-- b -->\n", encoding="utf-8")
            sub_docs.strip_task_comments(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "a <!-- b -->\n")

    def test_task_doc_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log" / "task" / "t1" / "status.md"
            path.parent.mkdir(parents=True)
            path.write_text("a <!-- b -->\n", encoding="utf-8")
            sub_docs.strip_task_comments(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "a\n")


class LockedIgnoresComments(unittest.TestCase):
    def setUp(self):
        self.old = sub_docs.template_text("nestlab-log-state")
        self.path = Path("unused.md")

    def test_comment_only_difference_allowed(self):
        new = sub_docs.strip_comments(self.old)
        self.assertFalse(sub_scope.state_locked({}, self.path, new, self.old))

    def test_review_item_written_blocked(self):
        old = sub_docs.template_text("nestlab-log-spec")
        new = re.sub(r"(?m)^(#+ 검토 필요.*)$", r"\1\n\n- 직접 쓴 항목", sub_docs.strip_comments(old), count=1)
        self.assertTrue(sub_scope.spec_locked({}, self.path, new, old))


if __name__ == "__main__":
    unittest.main()
