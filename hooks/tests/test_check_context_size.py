"""컨텍스트 기준에 따른 Stop 판정과 사유의 진행 중 경과 문서 목록을 검사한다."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_context_size as hook


class CheckContextSize(unittest.TestCase):
    # 두 runner의 세션 기록과 상태가 다른 경과 문서 셋을 임시 폴더에 만들고 판정과 문서 경로를 반환한다.
    def check_session(self, runner, tokens=210_000, current=False, mixed=False, **extra):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            statuses = {}
            for name, state in (("a", "진행"), ("b", "미개시"), ("done", "완료")):
                status = root / f"log/task/{name}/status.md"
                status.parent.mkdir(parents=True)
                status.write_text(f"- 작업 상태: {state}\n## 작업 경과\n", encoding="utf-8")
                statuses[name] = f"log/task/{name}/"
            if runner == "claude":
                user = {"type": "user", "message": {"content": "작업 요청"}}
                write = lambda s: {"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Write", "input": {"file_path": str(s)}}]}}
                usage = {"type": "assistant", "message": {"usage": {
                    "input_tokens": tokens - 30_000, "cache_creation_input_tokens": 10_000,
                    "cache_read_input_tokens": 10_000, "output_tokens": 10_000}}}
                rows = []
            else:
                user = {"type": "response_item", "payload": {"type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": "작업 요청"}]}}
                write = lambda s: {"type": "response_item", "payload": {"type": "custom_tool_call",
                         "name": "apply_patch", "input": f"*** Update File: {s.as_posix()}\n"}}
                usage = {"type": "event_msg", "payload": {"type": "token_count", "info": {
                    "last_token_usage": {"total_tokens": tokens},
                    "total_token_usage": {"total_tokens": 900_000}}}}
                source = {"subagent": {"thread_spawn": {"parent_thread_id": "parent"}}} if extra.pop("child", False) else "cli"
                rows = [{"type": "session_meta", "payload": {"source": source}}]
            # 직전 턴에 세 문서를 쓰고, current이면 이번 턴에도 경과 문서를 쓴다.
            paths = {k: root / v / "status.md" for k, v in statuses.items()}
            written = []
            for status in paths.values():
                written.append(status)
                if mixed:
                    spec = status.with_name("spec.md")
                    spec.write_text("# 명세\n", encoding="utf-8")
                    written.append(spec)
            rows += [user] + [write(s) for s in written] + [user]
            rows += ([write(paths["a"])] if current else []) + [usage]
            transcript = root / "session.jsonl"
            transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            with (patch.object(hook, "session_path", return_value=str(transcript)),
                  patch("check_workflow_response.project_root", return_value=root)):
                reason = hook.check({"cwd": str(root), "transcript_path": str(transcript), **extra}, runner)
            return reason, statuses

    # 기준 이상이면 진행 중 작업 폴더 상대 경로 전부, 단계별 기록 위치, compact 안내로 막고 완료 작업은 뺀다.
    # 이번 턴에 경과 문서를 써도 막는다.
    def test_blocks_with_open_statuses(self):
        for runner in ("claude", "codex"):
            for current in (False, True):
                with self.subTest(runner=runner, current=current):
                    reason, status = self.check_session(runner, current=current)
                    lines = reason.split("\n")
                    self.assertTrue(lines[0].startswith("컨텍스트가 200,000 토큰 이상입니다. 대화에만 있는 작업 정보를 진행 중인 작업 폴더의 문서에 기록하십시오."))
                    self.assertEqual(lines[1:5], [f"- 작업 폴더: {status['a']}", "  - 조사 결과: research.md",
                                                  "  - 계획 변경: spec.md", "  - 사용자 지시와 다음 작업: status.md의 작업 경과 절"])
                    self.assertIn(f"- 작업 폴더: {status['b']}", lines)
                    self.assertNotIn(status["done"], reason)
                    self.assertEqual(lines[-1], "기록을 마친 응답 끝에 사용자가 compact를 실행하도록 안내하십시오.")

    # 명세와 경과 문서를 모두 써도 작업을 한 번 안내하고 완료 상태는 경과 문서에서 읽는다.
    def test_mixed_writes_use_status_once(self):
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                reason, statuses = self.check_session(runner, mixed=True)
                for name in ("a", "b"):
                    self.assertEqual(reason.count(f"- 작업 폴더: {statuses[name]}"), 1)
                self.assertNotIn(statuses["done"], reason)

    # stop_hook_active이거나 컨텍스트가 기준 미만이거나 서브에이전트이면 통과한다.
    def test_passes(self):
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.assertIsNone(self.check_session(runner, stop_hook_active=True)[0])
                self.assertIsNone(self.check_session(runner, tokens=150_000)[0])
        self.assertIsNone(self.check_session("claude", agent_id="child")[0])
        self.assertIsNone(self.check_session("codex", child=True)[0])


if __name__ == "__main__":
    unittest.main()
