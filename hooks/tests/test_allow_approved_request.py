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

    # 작업 폴더를 만들고 경과 문서의 실행 승인 상태를 쓴다. 프로젝트 폴더 경로를 반환한다.
    def project(self, approval="승인", task="T1"):
        proj = self.root / "proj"
        folder = proj / "log" / "task" / task
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "status.md").write_text(f"# t\n\n## 실행 승인 범위\n\n- 사용자 승인 상태: {approval}\n", encoding="utf-8")
        (folder / "spec.md").write_text("old\n", encoding="utf-8")
        return proj

    # 서브에이전트 호출 입력을 만든다. runner가 claude면 agent_id와 agent 기록, codex면 부모 thread가 있는 rollout을 쓴다.
    def child(self, runner, first, proj, role=None, tool="Edit", parent_user="안 B로 해"):
        extra = {"agent_type": role} if role else {}
        if runner == "claude":
            main, agent = self.root / "m.jsonl", self.root / "agent.jsonl"
            main.write_text(claude_line("assistant", "계획") + "\n" + claude_line("user", parent_user) + "\n", encoding="utf-8")
            agent.write_text(claude_line("user", first) + "\n", encoding="utf-8")
            extra.update(agent_id="a1", transcript_path=str(main), agent_transcript_path=str(agent))
        else:
            (self.root / "rollout-1-P.jsonl").write_text(
                codex_meta() + "\n" + codex_msg("user", parent_user) + "\n", encoding="utf-8")
            rollout = self.root / "rollout-1-C.jsonl"
            rollout.write_text(codex_meta(source={"subagent": {"thread_spawn": {"parent_thread_id": "P"}}}) + "\n"
                               + (codex_msg("user", first) + "\n" if first else ""), encoding="utf-8")
            extra.update(transcript_path=str(rollout))
        return {"tool_name": tool, "tool_input": {"file_path": "a.py"}, "cwd": str(proj), **extra}

    FIRST = "작업 종류: 구현\n명세 문서: log/task/T1/spec.md"

    # 서브에이전트는 메인 마지막 메시지와 무관하게 실행 승인 기록이 있으면 allow, 승인 대기이면 deny이다.
    def test_permission_request_subagent_by_execution_approval(self):
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                proj = self.project("승인 대기")
                self.assertEqual(hook.decide(self.child(runner, self.FIRST, proj), runner), "deny")
                proj = self.project("승인")
                self.assertEqual(hook.decide(self.child(runner, self.FIRST, proj), runner), "allow")

    # 메인 세션은 마지막 메시지가 승인이면 Edit도 allow이다(기존 동작).
    def test_permission_request_main_edit_unchanged(self):
        self.assertEqual(self.claude("승인", "print(1)", tool="Edit"), "allow")

    # 역할이나 runner와 무관하게 실행 승인 기록이 있으면 allow이고, 승인 대기 또는 작업 문서 누락이면 deny이다.
    def test_permission_request_any_role(self):
        prompts = {"worker": self.FIRST, "documenter": "작업 종류: 문서 작성\n명세 문서: log/task/T1/spec.md",
                   "reviewer": "작업 종류: 반증\n반증 문서: log/task/T1/refute.md",
                   "researcher": "작업 종류: 조사\n조사 문서: log/task/T1/research.md", None: self.FIRST}
        for runner in ("claude", "codex"):
            for role, first in prompts.items():
                with self.subTest(runner=runner, role=role):
                    proj = self.project("승인")
                    self.assertEqual(hook.decide(self.child(runner, first, proj, role), runner), "allow")
                    missing = self.child(runner, first.replace("T1", "T9"), proj, role)
                    self.assertEqual(hook.decide(missing, runner), "deny")
                    proj = self.project("승인 대기")
                    self.assertEqual(hook.decide(self.child(runner, first, proj, role), runner), "deny")

    # Codex 첫 메시지가 비어 있으면 rollout 첫 줄의 task_name으로 작업을 찾는다.
    def test_permission_request_codex_child_task_name(self):
        proj = self.project("승인")
        data = self.child("codex", "", proj)
        spawn = {"subagent": {"thread_spawn": {"parent_thread_id": "P"}}}
        rollout = Path(data["transcript_path"])
        rollout.write_text(codex_meta(agent_path="/root/implement__T1", source=spawn) + "\n", encoding="utf-8")
        self.assertEqual(hook.decide(data, "codex"), "deny")  # task_name 형식(날짜_시각_이름)이 아니면 작업을 찾지 못한다.
        self.project("승인", task="20261103-063721-x")
        rollout.write_text(codex_meta(agent_path="/root/implement__20261103_063721_x", source=spawn) + "\n", encoding="utf-8")
        self.assertEqual(hook.decide(data, "codex"), "allow")

    # 서브에이전트의 질문 도구는 출력이 없고, 판정 중 예외는 main()이 출력 없이 끝낸다.
    def test_permission_request_subagent_question_and_error(self):
        proj = self.project("승인")
        for runner in ("claude", "codex"):
            self.assertIsNone(hook.decide(self.child(runner, self.FIRST, proj, tool="AskUserQuestion"), runner))
        data = self.child("claude", self.FIRST, proj)
        out = io.StringIO()
        with patch.object(sys, "argv", ["allow_approved_request.py", "--runner", "claude"]), \
             patch.object(sys, "stdin", io.StringIO(json.dumps(data))), patch.object(sys, "stdout", out), \
             patch.object(hook, "executed_approved", side_effect=RuntimeError("x")):
            self.assertEqual(hook.main(), 0)
        self.assertEqual(out.getvalue(), "")

    # 승인 전 메인 세션은 PreToolUse 검사를 통과한 작업 문서 apply_patch만 allow이고 코드 파일이 섞이면 deny이다.
    def test_permission_request_task_doc_before_approval(self):
        proj = self.project()
        rollout = self.root / "rollout-1-M.jsonl"
        rollout.write_text(codex_meta() + "\n" + codex_msg("user", "안 B로 해") + "\n", encoding="utf-8")
        (proj / "hooks").mkdir(exist_ok=True)
        (proj / "hooks" / "a.py").write_text("old\n", encoding="utf-8")

        def patch_of(*files):
            text = "*** Begin Patch\n" + "".join(f"*** Update File: {f}\n@@\n-old\n+new\n" for f in files) + "*** End Patch"
            return hook.decide({"tool_name": "apply_patch", "tool_input": {"command": text}, "cwd": str(proj),
                                "transcript_path": str(rollout)}, "codex")
        import check_tool_use
        import sub_scope
        with patch.object(check_tool_use, "kickoff_missing", return_value=False), \
             patch.object(sub_scope, "skill_called", return_value=True):
            self.assertEqual(patch_of("log/task/T1/spec.md"), "allow")
            self.assertEqual(patch_of("hooks/a.py"), "deny")
            self.assertEqual(patch_of("log/task/T1/spec.md", "hooks/a.py"), "deny")


if __name__ == "__main__":
    unittest.main()
