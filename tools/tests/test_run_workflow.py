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
        folder = self.root / ".claude/agents/director"
        folder.mkdir(parents=True)
        (folder / "AGENTS.md").write_text("director 지침", encoding="utf-8")

    # Claude 최종 결과를 반환하고 --bare를 쓰지 않는지 확인한다.
    def test_claude_final_and_no_bare(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"result": "결과"}))
        with patch.object(runner.subprocess, "run", return_value=result) as call:
            self.assertEqual(runner.invoke(self.root, "claude", "incident", "요청"), "결과")
        self.assertEqual(call.call_args.args[0][:2], ["claude", "-p"])
        self.assertNotIn("--bare", call.call_args.args[0])

    # Codex 이벤트 중 마지막 agent_message만 반환하는지 확인한다.
    def test_codex_final_message_only(self):
        events = [{"type": "item.completed", "item": {"type": "command_execution", "text": "잡음"}},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "결과"}}]
        result = subprocess.CompletedProcess([], 0, "\n".join(map(json.dumps, events)))
        with patch.object(runner.subprocess, "run", return_value=result) as call:
            self.assertEqual(runner.invoke(self.root, "codex", "incident", "요청"), "결과")
        self.assertEqual(call.call_args.args[0][:2], ["codex", "exec"])

    # 사건 분석 단계가 director 지침과 세 줄 요청을 hook을 끈 CLI로 실행하는지 확인한다.
    def test_incident_disables_hooks_and_limits_tools(self):
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
                runner.invoke(self.root, "claude", "incident", "요청")
            self.assertEqual(call.call_args.kwargs["creationflags"], expected)

    # 빈 결과와 오류 결과가 ValueError로 막히는지 확인한다.
    def test_empty_and_failed_results_block(self):
        for output in ('{"result": ""}', '{"is_error": true, "result": "오류"}'):
            with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output)):
                with self.assertRaises(ValueError):
                    runner.invoke(self.root, "claude", "incident", "요청")

    # 단계 실행 인자(--task, --stage)는 argparse 오류로 끝나고 외부 CLI를 실행하지 않는지 확인한다.
    def test_stage_arguments_rejected(self):
        argv = ["x", "--task", "x", "--stage", "research", "--runner", "claude"]
        with patch.object(runner.sys, "argv", argv), patch.object(runner.subprocess, "run") as call,                 patch.object(runner.sys, "stderr"):
            with self.assertRaises(SystemExit) as raised:
                runner.main()
        self.assertEqual(raised.exception.code, 2)
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
