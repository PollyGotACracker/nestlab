"""컨텍스트 기준에 따른 Stop 판정과 사유의 현재 진행 작업 폴더 안내를 검사한다."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import isolate_incidents  # noqa: F401 사건 문서를 저장소에 쓰지 않는다.
import check_context_size as hook
from sub_task_index import refresh_index

TASKS = {"a": "20261101-010101-a", "b": "20261102-010101-b", "done": "20261103-010101-done"}


class CheckContextSize(unittest.TestCase):
    # 두 runner의 세션 기록과 단계가 다른 경과 문서 셋을 만들고 selected를 현재 진행 작업으로 고른 뒤 판정과 폴더 경로를 반환한다.
    def check_session(self, runner, tokens=210_000, selected="a", stages=None, **extra):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            statuses = {}
            for name, task in TASKS.items():
                status = root / f".agent-logs/task/{task}/status.md"
                status.parent.mkdir(parents=True)
                stage = (stages or {}).get(name, "완료" if name == "done" else "계획")
                status.write_text(f"# {name}\n- 작업 ID: {task}\n- 작업 단계: {stage}\n## 작업 경과\n", encoding="utf-8")
                statuses[name] = f".agent-logs/task/{task}/"
                refresh_index(root, task, current=False)
            if selected:
                refresh_index(root, TASKS[selected], current=True)
            if runner == "claude":
                usage = {"type": "assistant", "message": {"usage": {
                    "input_tokens": tokens - 30_000, "cache_creation_input_tokens": 10_000,
                    "cache_read_input_tokens": 10_000, "output_tokens": 10_000}}}
                rows = [{"type": "user", "message": {"content": "작업 요청"}}]
            else:
                usage = {"type": "event_msg", "payload": {"type": "token_count", "info": {
                    "last_token_usage": {"total_tokens": tokens},
                    "total_token_usage": {"total_tokens": 900_000}}}}
                source = {"subagent": {"thread_spawn": {"parent_thread_id": "parent"}}} if extra.pop("child", False) else "cli"
                rows = [{"type": "session_meta", "payload": {"source": source}},
                        {"type": "response_item", "payload": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": "작업 요청"}]}}]
            transcript = root / "session.jsonl"
            transcript.write_text("\n".join(json.dumps(row) for row in rows + [usage]), encoding="utf-8")
            with (patch.object(hook, "session_path", return_value=str(transcript)),
                  patch.object(hook, "project_root", return_value=root)):
                reason = hook.check({"cwd": str(root), "agent_type": "director", "transcript_path": str(transcript), **extra}, runner)
            return reason, statuses

    # 기준 이상이면 색인의 현재 진행 작업 폴더, 단계별 기록 위치, compact 안내로 막고 다른 작업은 안내하지 않는다.
    def test_blocks_with_selected_task(self):
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                reason, status = self.check_session(runner)
                lines = reason.split("\n")
                self.assertTrue(lines[0].startswith("컨텍스트가 200,000 토큰 이상입니다."))
                self.assertEqual(lines[2:6], [f"- 작업 폴더: {status['a']}", "  - 조사 결과: research.md",
                                              "  - 계획 변경: spec.md", "  - 사용자 지시와 다음 작업: status.md의 작업 경과 절"])
                self.assertNotIn(status["b"], reason)
                self.assertNotIn(status["done"], reason)
                self.assertEqual(lines[-1], "기록을 마친 응답 끝에 사용자가 compact를 실행하도록 안내하십시오.")

    # 현재 진행 작업이 없으면 작업 폴더 없음으로 안내한다.
    def test_no_selection(self):
        reason, _ = self.check_session("claude", selected=None)
        self.assertIn("- 진행 중인 작업 폴더 없음", reason)

    # stop_hook_active이거나 컨텍스트가 기준 미만이거나 서브에이전트이면 통과한다.
    def test_passes(self):
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.assertIsNone(self.check_session(runner, stop_hook_active=True)[0])
                self.assertIsNone(self.check_session(runner, tokens=150_000)[0])
        self.assertIsNone(self.check_session("claude", agent_id="child")[0])
        self.assertIsNone(self.check_session("codex", child=True)[0])

    # 현재 진행 작업이 구현 단계이면 통과하고, 다른 작업만 구현 단계이면 차단한다.
    def test_implement_stage_passes(self):
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.assertIsNone(self.check_session(runner, stages={"a": "구현"})[0])
                self.assertIsNotNone(self.check_session(runner, stages={"b": "구현"})[0])


if __name__ == "__main__":
    unittest.main()
