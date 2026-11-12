"""역할과 runner별 조정 검사 및 공통 경로 보안 경계를 확인한다."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]
import isolate_incidents  # noqa: F401 사건 문서를 저장소에 쓰지 않는다.
import allow_approved_request
import check_tool_use
import check_workflow_response
import return_research_path
import sub_docs
import sub_role
import sub_scope
import sub_path
import sub_document
import sub_gate

ROLES = ("director", "assistant", "researcher", "worker", "documenter", "reviewer")


class DirectorBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        temporary = patch.object(sub_path, "TEMP", self.root / "os-temp")
        temporary.start()
        self.addCleanup(temporary.stop)

    def event(self, role, runner, child=False, **fields):
        path = self.root / f"{runner}-{role}-{child}.jsonl"
        if runner == "codex":
            payload = {"agent_role": role}
            if child:
                payload["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": "parent"}}}
            content = {"type": "session_meta", "payload": payload}
        else:
            content = {"type": "user", "message": {"content": "검토"}}
        path.write_text(json.dumps(content) + "\n", encoding="utf-8")
        return {"cwd": str(self.root), "agent_type": role, "transcript_path": str(path),
                **({"agent_id": "child"} if runner == "claude" and child else {}), **fields}

    def test_roles_main_and_child_events(self):
        for runner in ("claude", "codex"):
            for role in ROLES:
                for child in (False, True):
                    with self.subTest(runner=runner, role=role, child=child):
                        data = self.event(role, runner, child, tool_name="Write",
                                          tool_input={"file_path": str(self.root / "result.txt"), "content": "결과"})
                        self.assertEqual(sub_role.resolve_role(data), role)
                        self.assertEqual(sub_role.main_session(data), not child)
                        self.assertEqual(sub_role.director_main(data), role == "director" and not child)
                        if role != "director" or child:
                            with patch.object(check_tool_use, "check", return_value=None):
                                self.assertEqual(allow_approved_request.decide(data, runner), "allow")
                            self.assertIsNone(check_workflow_response.check({**data, "last_assistant_message": "수정하겠습니다."}, runner))
                            if role != "researcher":
                                self.assertIsNone(sub_scope.check_write(data, role, [self.root / "result.txt"], not child, runner))
                        for name, inputs in (("Read", {"file_path": str(self.root / ".env")}),
                                             ("Read", {"file_path": str(self.root.parent / "outside-secret.txt")})):
                            blocked = check_tool_use.check({**data, "tool_name": name, "tool_input": inputs}, runner)
                            self.assertIsNotNone(blocked)

    def test_director_dispatch_requires_approval_for_both_runners(self):
        for runner, name in (("claude", "Agent"), ("codex", "spawn_agent")):
            data = self.event("director", runner, tool_name=name,
                              tool_input={"subagent_type": "jelly", "agent_type": "worker", "fork_turns": "none",
                                          "prompt": "task: implement\ninput: log/task/t/spec.md",
                                          "message": "task: implement\ninput: log/task/t/spec.md"})
            self.assertIn("승인", check_tool_use.check(data, runner))

    # 승인된 director 턴의 기본 역할 배정은 호출 순서와 무관하게 두 runner에서 연속 허용된다.
    def test_approved_parallel_dispatch_passes_both_runners(self):
        for runner, name in (("claude", "Agent"), ("codex", "spawn_agent")):
            data = self.event("director", runner, tool_name=name)
            Path(data["transcript_path"]).write_text(
                json.dumps({"type": "user", "message": {"content": "승인"}}) + "\n"
                if runner == "claude" else
                json.dumps({"type": "session_meta", "payload": {"agent_role": "director"}}) + "\n"
                + json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user",
                              "content": [{"type": "input_text", "text": "승인"}]}}) + "\n", encoding="utf-8")
            with patch.object(check_tool_use, "call_reason", return_value=None):
                for agent in ("jelly", "ricky"):
                    prompt = f"task: {'implement' if agent == 'jelly' else 'verify'}\ninput: log/task/t/spec.md"
                    inputs = {"subagent_type": agent, "agent_type": agent, "fork_turns": "none", "prompt": prompt, "message": prompt}
                    with self.subTest(runner=runner, agent=agent):
                        self.assertIsNone(check_tool_use.check({**data, "tool_input": inputs}, runner))

    def test_input_role_precedes_metadata_and_environment(self):
        data = self.event("worker", "codex")
        with patch.dict(os.environ, {"NESTLAB_AGENT": "director"}):
            self.assertEqual(sub_role.resolve_role(data), "worker")
            self.assertEqual(sub_role.resolve_role({**data, "agent_type": "nico"}), "researcher")
            self.assertIsNone(sub_role.resolve_role({**data, "agent_type": "unknown"}))

    def test_specialist_and_unknown_spawns_keep_runner_defaults(self):
        for runner, name in (("claude", "Agent"), ("codex", "spawn_agent")):
            for role in (*ROLES[1:], "unknown"):
                for child in (False, True):
                    with self.subTest(runner=runner, role=role, child=child):
                        data = self.event(role, runner, child, tool_name=name,
                                          tool_input={"agent_type": "worker", "message": "일반 작업"})
                        self.assertIsNone(check_tool_use.check(data, runner))

    def test_parent_identity_uses_only_parent_evidence(self):
        child = self.root / "child.jsonl"
        parent = self.root / "parent.jsonl"
        child.write_text(json.dumps({"payload": {"agent_role": "researcher", "source": {
            "subagent": {"thread_spawn": {"parent_thread_id": "parent"}}}}}) + "\n", encoding="utf-8")
        data = {"transcript_path": str(child), "cwd": str(self.root)}
        with patch.object(sub_role, "codex_rollout", return_value=str(parent)), \
                patch.dict(os.environ, {"NESTLAB_AGENT": "director"}), \
                patch.object(sub_role, "read_folder_agent", return_value="director"):
            for role, expected in (("assistant", False), (None, False), ("director", True)):
                parent.write_text(json.dumps({"payload": {"agent_role": role}}) + "\n", encoding="utf-8")
                self.assertEqual(sub_role.director_assignment(data, str(child)), expected)

    def test_claude_parent_agent_setting_decides_director(self):
        child, parent = self.root / "c.jsonl", self.root / "p.jsonl"
        child.write_text("{}\n", encoding="utf-8")
        data = {"transcript_path": str(parent), "cwd": str(self.root)}
        user = json.dumps({"type": "user", "message": {"content": "x"}})
        for lines, expected in (([user, json.dumps({"type": "agent-setting", "agentSetting": "rio"})], True),
                                ([user, json.dumps({"type": "agent-setting", "agentSetting": "buddy"})], False),
                                ([user], False)):
            parent.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertEqual(sub_role.director_assignment(data, str(child)), expected)

    def test_result_replacement_requires_saved_nonempty_body(self):
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True)
        document = folder / "research.md"
        data = self.event("director", "claude", tool_name="Agent",
                          tool_input={"subagent_type": "nico", "prompt": "task: research\ninput: log/task/t/research.md"},
                          tool_response={"status": "completed", "content": "일반 응답"})
        with patch.object(sub_docs, "project_root", return_value=self.root):
            for body in ("", "### 조사 <번호>. <항목명> 결과\n\n- 결론: <내용>\n"):
                document.write_text(f"# 조사\n\n## 조사 결과\n\n{body}", encoding="utf-8")
                self.assertIsNone(return_research_path.replace(data, "claude"))
            document.write_text("# 조사\n\n## 조사 결과\n\n### 조사 1. 구조 결과\n\n- 결론: 일반 응답\n", encoding="utf-8")
            self.assertIsNotNone(return_research_path.replace(data, "claude"))
            self.assertIsNone(return_research_path.replace({**data, "tool_response": {"status": "running"}}, "claude"))
            self.assertIsNone(return_research_path.replace({**data, "agent_type": "assistant"}, "claude"))
            self.assertIsNone(return_research_path.replace({**data, "tool_response": {"status": "completed", "content": "다른 응답"}}, "claude"))


if __name__ == "__main__":
    unittest.main()
