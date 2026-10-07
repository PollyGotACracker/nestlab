"""check_refute_verdict.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트, 공용 모듈, check_tool_use 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import json
import os
import subprocess
import sys
import unittest
import tempfile
import io
from pathlib import Path
from unittest.mock import patch

import check_refute_verdict as hook

REFUTE = "작업 종류: 반증\n주장: 확인할 주장\n출처: hooks/a.py:12"


def run_utf8(hook, data, *args, cwd=None):
    env = {**os.environ, "PYTHONIOENCODING": "", "PYTHONUTF8": "0"}
    return subprocess.run([sys.executable, "-B", hook.__file__, *args], env=env, capture_output=True, check=True, cwd=cwd,
                          input=json.dumps(data, ensure_ascii=False).encode("utf-8")).stdout.decode("utf-8")


def stop(msg, role="ricky", key="agent_type", prompt=REFUTE):
    return hook.check({key: role, "tool_input": {"prompt": prompt}, "last_assistant_message": msg})


class RefuteVerdict(unittest.TestCase):
    def test_unsourced_support_blocked(self):
        self.assertIsNotNone(stop("주장 1: 지지 | 출처: 없음"))
        self.assertIsNotNone(stop("주장 1: 반증 | 출처: 확인함"))
        self.assertIsNotNone(stop("결론만"))
        self.assertIsNone(stop("주장 1: 미확인 | 출처: 없음"))

    def test_url_and_file_line_evidence(self):
        self.assertIsNone(stop("- 주장 1: 지지 | 출처: https://a.b/c"))
        self.assertIsNone(stop("**주장 2: 반증 | 출처: hooks/a.py:12**"))

    def test_one_bad_line_blocks(self):
        self.assertIsNotNone(stop("주장 1: 지지 | 출처: https://a.b\n주장 2: 지지 | 출처: 없음"))

    def test_only_explicit_reviewer_refutation_checked(self):
        for role in ("pepper", "documenter", "researcher", "nico", "jelly", "worker"):
            self.assertIsNone(stop("결론만", role=role))
        for role in ("reviewer", "ricky"):
            for key in ("agent_type", "agent_role"):
                self.assertIsNotNone(stop("결론만", role=role, key=key))
                self.assertIsNone(stop("검수 PASS", role=role, key=key, prompt="입력 문서: log/state/a-input.md"))
                self.assertIsNone(stop("검수 PASS", role=role, key=key, prompt="표식 없음"))
                self.assertIsNone(stop("검수 PASS", role=role, key=key, prompt=REFUTE + "\n작업 종류: 반증"))

    def test_block_ignores_stop_hook_active(self):
        d = {"agent_type": "ricky", "tool_input": {"prompt": REFUTE}, "stop_hook_active": True, "last_assistant_message": "주장 1: 반증 | 출처: 없음"}
        self.assertIsNotNone(hook.check(d))

    def test_mode_from_claude_and_codex_request_records(self):
        requests = (
            {"type": "user", "message": {"content": REFUTE}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": REFUTE}]}},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "child.jsonl"
            for row in requests:
                path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
                data = {"agent_type": "ricky", "agent_transcript_path": str(path),
                        "last_assistant_message": "결론만"}
                self.assertIsNotNone(hook.check(data))
                data["last_assistant_message"] = "주장 1: 반증 | 출처: hooks/a.py:12"
                self.assertIsNone(hook.check(data))
            path.write_text(json.dumps({"type": "user", "message": {"content": "입력 문서: log/state/a-input.md"}}), encoding="utf-8")
            self.assertIsNone(hook.check({"agent_type": "reviewer", "agent_transcript_path": str(path),
                                          "last_assistant_message": "검수 PASS"}))

    def test_mode_from_codex_session_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-example-s1.jsonl"
            rows = [{"type": "session_meta", "payload": {"parent_thread_id": "parent"}},
                    {"type": "response_item", "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": REFUTE}]}}]
            path.write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
            with patch("sub_session.CODEX_SESSIONS", Path(tmp)):
                self.assertIsNotNone(hook.check({"agent_role": "reviewer", "session_id": "s1",
                                                 "last_assistant_message": "결론만"}))

    def test_root_and_subdirectory_with_explicit_mode(self):
        root = Path(hook.__file__).resolve().parents[1]
        for cwd in (root, root / "hooks"):
            for key in ("agent_type", "agent_role"):
                data = {key: "reviewer", "tool_input": {"message": REFUTE}, "last_assistant_message": "결론만"}
                with self.subTest(cwd=cwd, key=key):
                    self.assertEqual(json.loads(run_utf8(hook, data, cwd=cwd))["decision"], "block")
                    data["tool_input"]["message"] = "입력 문서: log/state/a-input.md"
                    self.assertEqual(run_utf8(hook, data, cwd=cwd), "")

    def test_missing_request_mode_fails_open(self):
        self.assertIsNone(hook.check({"agent_type": "ricky", "last_assistant_message": "결론만"}))

    def test_stringio_main_preserves_protocol(self):
        output = io.StringIO()
        data = {"agent_role": "reviewer", "tool_input": {"message": REFUTE}, "last_assistant_message": "결론만"}
        with patch.object(sys, "stdin", io.StringIO(json.dumps(data))), patch.object(sys, "stdout", output):
            self.assertEqual(hook.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["decision"], "block")

    def test_utf8_stdin_stdout(self):
        d = {"agent_type": "ricky", "tool_input": {"prompt": REFUTE}, "last_assistant_message": "주장 1: 지지 | 출처: https://a.b"}
        self.assertEqual(run_utf8(hook, d), "")
        out = json.loads(run_utf8(hook, {**d, "last_assistant_message": "결론만"}))
        self.assertIn("판정 줄이 없습니다", out["reason"])


if __name__ == "__main__":
    unittest.main()
