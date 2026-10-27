"""allow_approved_request.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트와 공용 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import json
import tempfile
import unittest
from unittest.mock import patch

import allow_approved_request as hook
import sub_role


# Claude 대화 기록 한 줄을 JSON 문자열로 만든다.
def claude_line(role: str, text: str) -> str:
    content = text if role == "user" else [{"type": "text", "text": text}]
    return json.dumps({"type": role, "message": {"content": content}}, ensure_ascii=False)


# Codex rollout의 session_meta 줄을 JSON 문자열로 만든다.
def codex_meta(**payload) -> str:
    return json.dumps({"type": "session_meta", "payload": payload}, ensure_ascii=False)


# Codex rollout의 메시지 줄을 JSON 문자열로 만든다.
def codex_msg(role: str, text: str) -> str:
    return json.dumps({"type": "response_item", "payload": {"type": "message", "role": role,
                                                            "content": [{"type": "input_text", "text": text}]}},
                      ensure_ascii=False)


class AllowApprovedRequest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        p = patch.object(sub_role, "CODEX_SESSIONS", self.root)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    # 사용자 마지막 메시지가 user인 Claude 기록과 Bash 입력으로 판정한다.
    def claude(self, user, command, **extra):
        path = self.root / "t.jsonl"
        path.write_text(claude_line("assistant", "계획") + "\n" + claude_line("user", user) + "\n", encoding="utf-8")
        return hook.decide({"tool_name": "Bash", "tool_input": {"command": command},
                            "transcript_path": str(path), **extra}, "claude")

    # 승인 턴에는 스크립트 실행을 허용하고 ask 목록 명령은 허용하지 않는다.
    def test_approved_turn(self):
        self.assertTrue(self.claude("승인", "python a.py"))
        self.assertFalse(self.claude("승인", "git add a.md"))
        self.assertFalse(self.claude("승인", "python a.py && git add a.md"))

    # 승인 턴에도 위험 패턴이 든 인라인 코드는 허용하지 않고, git -C는 읽기만 허용하며 git -c는 허용하지 않는다.
    def test_approved_turn_risky_inline_and_git_options(self):
        self.assertFalse(self.claude("승인", 'python -c "import subprocess"'))
        self.assertFalse(self.claude("승인", "python -c \"open('C:/Users/a/.claude/settings.json')\""))
        self.assertFalse(self.claude("승인", "node -e \"require('child_process')\""))
        self.assertTrue(self.claude("승인", 'python -c "print(1)"'))
        self.assertTrue(self.claude("승인", "git -C hooks log -1"))
        self.assertFalse(self.claude("승인", "git -C hooks push"))
        self.assertFalse(self.claude("승인", "git -c a.b=c log -1"))

    # dontAsk 세션은 허용 목록과 승인 턴 명령 모두 허용하지 않고 default는 기존 판정과 같다.
    def test_dont_ask(self):
        unit = "python -m unittest discover -s hooks/tests"
        self.assertFalse(self.claude("안 B로 해", unit, permission_mode="dontAsk"))
        self.assertFalse(self.claude("승인", "python a.py", permission_mode="dontAsk"))
        self.assertTrue(self.claude("안 B로 해", unit, permission_mode="default"))

    # 비승인 턴에는 허용 목록만 허용한다.
    def test_unapproved_turn(self):
        self.assertFalse(self.claude("안 B로 해", "python a.py"))
        self.assertTrue(self.claude("안 B로 해", "python -m unittest discover -s hooks/tests"))
        self.assertFalse(self.claude("안 B로 해", "python -m unittest x && python a.py"))

    # 승인 뒤 하네스 항목은 건너뛰어 승인이 유지되고, 실제 사용자 발화가 오면 승인이 끝난다.
    def test_approval_survives_harness(self):
        path = self.root / "h.jsonl"
        lines = [claude_line("assistant", "계획"), claude_line("user", "승인"),
                 claude_line("user", "Skill /x is already loaded"),
                 claude_line("user", "Another Claude session sent a message:\n보고")]
        data = {"tool_name": "Bash", "tool_input": {"command": "python a.py"}, "transcript_path": str(path)}
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertTrue(hook.decide(data, "claude"))
        path.write_text("\n".join(lines + [claude_line("user", "다시 봐")]) + "\n", encoding="utf-8")
        self.assertFalse(hook.decide(data, "claude"))

    # Codex 하위 thread는 부모 rollout의 사용자 마지막 메시지로 판정한다.
    def test_codex_child_uses_parent(self):
        (self.root / "rollout-1-P.jsonl").write_text(
            codex_meta() + "\n" + codex_msg("user", "승인") + "\n", encoding="utf-8")
        child = self.root / "rollout-1-C.jsonl"
        child.write_text(codex_meta(agent_role="jelly", source={"subagent": {"thread_spawn": {"parent_thread_id": "P"}}})
                         + "\n", encoding="utf-8")
        data = {"tool_name": "Bash", "tool_input": {"command": "python a.py"}, "transcript_path": str(child)}
        self.assertTrue(hook.decide(data, "codex"))


if __name__ == "__main__":
    unittest.main()
