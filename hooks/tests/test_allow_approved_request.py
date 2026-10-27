"""allow_approved_request.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트와 공용 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import io
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

    # 사용자 마지막 메시지가 user인 Claude 기록과 도구 입력으로 판정한다.
    def claude(self, user, command="python a.py", tool="Bash", **extra):
        path = self.root / "t.jsonl"
        kickoff = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "1", "name": "Skill", "input": {"skill": "nestlab-kickoff"}}]}})
        path.write_text(kickoff + "\n" + claude_line("assistant", "계획") + "\n" + claude_line("user", user) + "\n",
                        encoding="utf-8")
        return hook.decide({"tool_name": tool, "tool_input": {"command": command, "description": "명령 실행"},
                            "transcript_path": str(path), **extra}, "claude")

    # 승인 턴 Bash는 allow, 승인 전 Bash는 deny를 돌려준다.
    def test_permission_request_approval_turn(self):
        self.assertEqual(self.claude("승인"), "allow")
        self.assertEqual(self.claude("승인", "git commit -m x"), "allow")
        self.assertEqual(self.claude("안 B로 해"), "deny")
        self.assertEqual(self.claude("안 B로 해", "git commit -m x"), "deny")

    # 질문 도구는 승인 여부와 관계없이 출력 없이 둔다.
    def test_permission_request_question_tools(self):
        for tool in ("AskUserQuestion", "ExitPlanMode", "request_user_input"):
            self.assertIsNone(self.claude("승인", tool=tool), tool)
            self.assertIsNone(self.claude("안 B로 해", tool=tool), tool)

    # 허용 목록 명령은 승인 여부와 관계없이 allow이고 하위 명령이 여럿이면 승인 전 deny이다.
    def test_permission_request_allow_list(self):
        unit = "python -m unittest discover -s hooks/tests"
        self.assertEqual(self.claude("안 B로 해", unit), "allow")
        self.assertEqual(self.claude("안 B로 해", unit + " && python a.py"), "deny")
        self.assertEqual(self.claude("안 B로 해", unit, permission_mode="dontAsk"), "allow")

    # 승인 전에도 PreToolUse 검사를 통과한 읽기 셸 명령은 allow이고 쓰기 명령과 셸 외 도구는 deny이다.
    def test_permission_request_read_before_approval(self):
        for command in ('python -I -X utf8 -c "print(1)"', "sed -n '/^## 경로 목록/,$p' AGENTS.md"):
            self.assertEqual(self.claude("안 B로 해", command), "allow", command)
        for command in ("cp a.txt b.txt", "git commit -m x", "python -c \"open('a','w')\""):
            self.assertEqual(self.claude("안 B로 해", command), "deny", command)
        self.assertEqual(self.claude("안 B로 해", "print(1)", tool="Edit"), "deny")

    # main()이 deny는 decision.behavior와 message로, 질문 도구는 빈 출력으로 내보낸다.
    def test_permission_request_output(self):
        path = self.root / "t.jsonl"
        path.write_text(claude_line("assistant", "계획") + "\n" + claude_line("user", "다시 봐") + "\n", encoding="utf-8")

        def run(tool):
            out = io.StringIO()
            data = {"tool_name": tool, "tool_input": {"command": "git commit -m x"}, "transcript_path": str(path)}
            with patch.object(sys, "argv", ["allow_approved_request.py", "--runner", "claude"]), \
                 patch.object(sys, "stdin", io.StringIO(json.dumps(data))), patch.object(sys, "stdout", out):
                hook.main()
            return out.getvalue()
        decision = json.loads(run("Bash"))["hookSpecificOutput"]["decision"]
        self.assertEqual(decision["behavior"], "deny")
        self.assertTrue(decision["message"].endswith("다른 방식으로 작업하십시오."))
        self.assertEqual(run("AskUserQuestion"), "")

    # 승인 뒤 하네스 항목은 건너뛰어 승인이 유지되고, 실제 사용자 발화가 오면 승인이 끝난다.
    def test_permission_request_survives_harness(self):
        path = self.root / "h.jsonl"
        lines = [claude_line("assistant", "계획"), claude_line("user", "승인"),
                 claude_line("user", "Skill /x is already loaded"),
                 claude_line("user", "Another Claude session sent a message:\n보고")]
        data = {"tool_name": "Bash", "tool_input": {"command": "python a.py"}, "transcript_path": str(path)}
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(hook.decide(data, "claude"), "allow")
        path.write_text("\n".join(lines + [claude_line("user", "다시 봐")]) + "\n", encoding="utf-8")
        self.assertEqual(hook.decide(data, "claude"), "deny")

    # Codex 하위 thread는 부모 rollout의 사용자 마지막 메시지로 판정한다.
    def test_permission_request_codex_child_uses_parent(self):
        (self.root / "rollout-1-P.jsonl").write_text(
            codex_meta() + "\n" + codex_msg("user", "승인") + "\n", encoding="utf-8")
        child = self.root / "rollout-1-C.jsonl"
        child.write_text(codex_meta(agent_role="jelly", source={"subagent": {"thread_spawn": {"parent_thread_id": "P"}}})
                         + "\n", encoding="utf-8")
        data = {"tool_name": "Bash", "tool_input": {"command": "python a.py"}, "transcript_path": str(child)}
        self.assertEqual(hook.decide(data, "codex"), "allow")


if __name__ == "__main__":
    unittest.main()
