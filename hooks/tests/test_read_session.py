"""sub_session.read_session와 sub_role.first_prompt 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트, 공용 모듈, check_tool_use 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import sub_approval
import sub_role
import sub_session


# Claude 기록의 사용자 한 줄이다.
def user(text):
    return json.dumps({"type": "user", "message": {"content": text}}, ensure_ascii=False)


# Claude 기록의 assistant 한 줄이다.
def assistant(text):
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}, ensure_ascii=False)


# 줄 목록을 임시 기록 파일로 저장하고 경로를 반환한다.
def write(test, lines):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    test.addCleanup(os.remove, path)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class ReadSessionTest(unittest.TestCase):
    def test_normal(self):
        path = write(self, [user("a"), assistant("b"), user("c")])
        self.assertEqual([e["role"] for e in sub_session.read_session(path)], ["user", "assistant", "user"])

    def test_last_incomplete_line(self):
        path = write(self, [user("a"), '{"type": "user", "mess'])
        entries = sub_session.read_session(path)
        self.assertEqual([e["role"] for e in entries], ["user", "broken"])
        self.assertEqual(sub_approval.last_user(path)[0], "")

    def test_middle_broken(self):
        path = write(self, [user("a"), "{broken", user("b")])
        users = [e for e in sub_session.read_session(path) if e["role"] == "user"]
        self.assertEqual([e["text"] for e in users], ["a", "b"])

    def test_broken_after_approval(self):
        path = write(self, [user("계획 확인"), assistant("보고"), user("승인"), "{broken"])
        self.assertFalse(sub_approval.approved(sub_approval.last_user(path)[0]))

    def test_broken_before_approval(self):
        path = write(self, [user("a"), "{broken", assistant("보고"), user("승인")])
        self.assertTrue(sub_approval.approved(sub_approval.last_user(path)[0]))

    def test_empty_file(self):
        path = write(self, [])
        self.assertEqual(sub_session.read_session(path), [])

    def test_non_object_line(self):
        path = write(self, [user("a"), "[1, 2]", "123"])
        self.assertEqual([e["role"] for e in sub_session.read_session(path)], ["user", "broken", "broken"])


class FirstPromptTest(unittest.TestCase):
    def test_first_prompt_stops(self):
        path = write(self, [user("첫 요청")] + [user(f"m{i}") for i in range(1999)])
        with patch.object(sub_session, "_claude_entry", wraps=sub_session._claude_entry) as spy:
            self.assertEqual(sub_role.first_prompt(path), "첫 요청")
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(len(sub_session.read_session(path)), 2000)

    def test_first_prompt_cipher(self):
        meta = json.dumps({"type": "session_meta", "payload": {"task_name": "implement__20261121_070700_repo_improvement"}})
        msg = json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user",
                                                               "content": [{"type": "input_text", "text": "gAAAAAabc123="}]}})
        path = write(self, [meta, msg])
        self.assertEqual(sub_role.first_prompt(path),
                         "task: implement\ninput: .agent-logs/task/20261121-070700-repo-improvement/spec.md")


if __name__ == "__main__":
    unittest.main()
