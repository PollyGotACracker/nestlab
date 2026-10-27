"""단계 실행기의 CLI 결과와 실패 처리를 확인한다."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_workflow as runner


class Workflow(unittest.TestCase):
    # 임시 저장소와 역할 지침을 준비한다.
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        folder = self.root / ".claude/agents/researcher"
        folder.mkdir(parents=True)
        (folder / "AGENTS.md").write_text("역할 지침", encoding="utf-8")

    # Claude 최종 결과를 반환하고 --bare를 쓰지 않는지 확인한다.
    def test_claude_final_and_no_bare(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"result": "결과"}))
        with patch.object(runner.subprocess, "run", return_value=result) as call:
            self.assertEqual(runner.invoke(self.root, "claude", "research", "요청"), "결과")
        self.assertEqual(call.call_args.args[0][:2], ["claude", "-p"])
        self.assertNotIn("--bare", call.call_args.args[0])

    # Codex 이벤트 중 마지막 agent_message만 반환하는지 확인한다.
    def test_codex_final_message_only(self):
        events = [{"type": "item.completed", "item": {"type": "command_execution", "text": "잡음"}},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "결과"}}]
        result = subprocess.CompletedProcess([], 0, "\n".join(map(json.dumps, events)))
        with patch.object(runner.subprocess, "run", return_value=result) as call:
            self.assertEqual(runner.invoke(self.root, "codex", "research", "요청"), "결과")
        self.assertEqual(call.call_args.args[0][:4], ["codex", "exec", "--sandbox", "workspace-write"])

    # 사건 분석 단계가 director 지침과 세 줄 요청을 hook을 끈 CLI로 실행하는지 확인한다.
    def test_incident_disables_hooks_and_limits_tools(self):
        folder = self.root / ".claude/agents/director"
        folder.mkdir(parents=True)
        (folder / "AGENTS.md").write_text("director 지침", encoding="utf-8")
        (self.root / "log/incident").mkdir(parents=True)
        (self.root / "log/incident/a.md").write_text("# a", encoding="utf-8")
        claude = subprocess.CompletedProcess([], 0, json.dumps({"result": "분석"}))
        codex = subprocess.CompletedProcess([], 0, json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "분석"}}))
        for name, result in (("claude", claude), ("codex", codex)):
            with patch.object(runner.subprocess, "run", return_value=result) as call:
                self.assertEqual(runner.run_incident(self.root, "log/incident/a.md", name), "분석")
            command = call.call_args.args[0]
            self.assertTrue(command[-1].startswith("director 지침"))
            self.assertRegex(command[-1], r"작업 종류: 사건 분석\n사건 문서: log/incident/a\.md\n"
                                          r"현재 시각\(KST\): \d{4}-\d\d-\d\d \d\d:\d\d:\d\d\nnestlab-log-incident 절차")
            if name == "claude":
                self.assertEqual(command[command.index("--settings") + 1], '{"disableAllHooks": true}')
                self.assertEqual(command[command.index("--disallowedTools") + 1], "Bash")
                tools = command[command.index("--allowedTools") + 1].split(",")
                self.assertTrue({"Edit", "Write", "Skill", "WebSearch", "WebFetch"} <= set(tools)
                                and "Bash" not in tools)
            else:
                self.assertEqual(command[:4], ["codex", "exec", "--disable", "hooks"])
        with self.assertRaises(ValueError):
            runner.run_incident(self.root, "log/incident/none.md", "claude")

    # invoke의 subprocess.run이 Windows에서 CREATE_NO_WINDOW를, 그 밖의 OS에서 0을 받는지 확인한다.
    def test_invoke_creationflags(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"result": "결과"}))
        for platform, expected in (("win32", 0x08000000), ("linux", 0)):
            with patch.object(runner.subprocess, "run", return_value=result) as call, \
                 patch.object(runner.sys, "platform", platform), \
                 patch.object(runner.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True):
                runner.invoke(self.root, "claude", "research", "요청")
            self.assertEqual(call.call_args.kwargs["creationflags"], expected)

    # 일반 단계 실행에는 hook 비활성 옵션이 없는지 확인한다.
    def test_regular_stage_keeps_hooks(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"result": "결과"}))
        with patch.object(runner.subprocess, "run", return_value=result) as call:
            runner.invoke(self.root, "claude", "research", "요청")
        self.assertNotIn("--settings", call.call_args.args[0])

    # 빈 결과와 오류 결과가 ValueError로 막히는지 확인한다.
    def test_empty_and_failed_results_block(self):
        for output in ('{"result": ""}', '{"is_error": true, "result": "오류"}'):
            with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output)):
                with self.assertRaises(ValueError):
                    runner.invoke(self.root, "claude", "research", "요청")

    # 선행 조건이 미달이면 CLI를 실행하지 않는지 확인한다.
    def test_validation_prevents_launch(self):
        with patch.object(runner, "validate", side_effect=ValueError("승인되지 않음")), \
                patch.object(runner, "invoke") as call:
            with self.assertRaisesRegex(ValueError, "승인되지 않음"):
                runner.run(self.root, "t", "implement", "codex")
            call.assert_not_called()

    # 단계 실행기에 검증 단계 인자가 없고 조사 단계 인자는 받는지 확인한다.
    def test_review_stage_removed(self):
        self.assertFalse(hasattr(runner, "run_checks"))
        for stage, ok in (("review", False), ("rereview", False), ("research", True)):
            argv = ["x", "--task", "t", "--stage", stage, "--runner", "claude"]
            with self.subTest(stage=stage), patch.object(runner.sys, "argv", argv), \
                    patch.object(runner, "run", return_value="ok"), patch("builtins.print"), \
                    patch.object(runner.sys, "stderr"):
                if ok:
                    self.assertEqual(runner.main(), 0)
                else:
                    with self.assertRaises(SystemExit):
                        runner.main()

    # 요청문이 새 경로 문서 한 줄만 싣고 상태 문서 경로를 싣지 않는지 확인한다.
    def test_request_text_has_one_doc_line(self):
        task = "20260101-000000-t"
        folder = self.root / "log/task" / task
        folder.mkdir(parents=True)
        for name in ("spec", "research", "refute"):
            (folder / f"{name}.md").write_text("x", encoding="utf-8")
        base = f"log/task/{task}"
        self.assertEqual(runner.request_text(self.root, task, "research"),
                         f"작업 종류: 조사\n조사 문서: {base}/research.md")
        self.assertEqual(runner.request_text(self.root, task, "refute"),
                         f"작업 종류: 반증\n반증 문서: {base}/refute.md")
        self.assertEqual(runner.request_text(self.root, task, "implement"),
                         f"작업 종류: 구현\n명세 문서: {base}/spec.md")


if __name__ == "__main__":
    unittest.main()
