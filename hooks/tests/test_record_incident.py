"""record_incident.py 동작 테스트. 실행: python hooks/tests/test_record_incident.py"""
import sys
from pathlib import Path

# 진입 스크립트, 공용 모듈, check_tool_use 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import record_incident as hook


class RecordIncident(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "log" / "incident"
        self.root_patch = patch.object(hook, "project_root", return_value=Path(self.tmp.name))
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.tmp.cleanup()

    def run_hook(self, message, **extra):
        hook.record({"session_id": "abcdef123456", "agent_type": "rio", "last_assistant_message": message, **extra})
        return list(self.dir.glob("*.md"))

    def test_admission_recorded(self):
        files = self.run_hook("제 판단으로 설계를 뒤집었어요.")
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertIn("조치 상태: 미착수", text)
        self.assertIn("감지 표현: 제 판단으로", text)

    def test_second_hit_appends_same_file(self):
        self.run_hook("놓쳤어요.")
        files = self.run_hook("빠뜨렸어요.")
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_text(encoding="utf-8").count("감지 표현"), 2)

    def test_full_message_blocks_and_append_for_both_runners(self):
        samples = (
            ("\n사용자  공백\n" + "긴 요청 " * 300 + "\n````text\n요청 코드\n````\n", "`````",
             "제 실수입니다.\n\n" + "긴 응답 " * 300 + "\n```text\n죄송\n  응답 코드\n```\n", "````"),
            ("다음 요청\n  들여쓰기 보존", "```", "놓쳤어요.\n다음 응답", "```"),
        )
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                entries, before = [], ""
                for user, user_fence, message, message_fence in samples:
                    entries.extend((("user", user), ("assistant", message)))
                    path = self.transcript(entries, runner)
                    hook.record({"session_id": runner + "-blocks-full-id", "transcript_path": str(path),
                                 "last_assistant_message": message}, runner)
                    file = next(self.dir.glob(f"*-{(runner + '-blocks-full-id')[:8]}.md"))
                    text = file.read_text(encoding="utf-8")
                    self.assertTrue(text.startswith(before))
                    appended = text[len(before):]
                    for label, body, fence in (("사용자 메시지", user, user_fence),
                                               ("응답 발췌", message, message_fence)):
                        block = f"  - {label}:\n\n    {fence}\n    " + body.replace("\n", "\n    ") + f"\n    {fence}\n"
                        self.assertIn(block, appended)
                    self.assertNotIn("감지 표현: 죄송", appended)
                    self.assertIn(f"응답 위치: {len(entries)}", appended)
                    before = text
                self.assertEqual(text.count("  - 사용자 메시지:"), 2)
                self.assertEqual(text.count("  - 응답 발췌:"), 2)

    def test_real_admissions_detected(self):
        samples = ("네, 제 잘못입니다.", "둘 다 제가 틀렸습니다.", "오늘 docs를 빼먹었습니다.",
                   "# 루프 설계 (빠뜨린 부분)", "제 사전 검토 누락입니다.", "제가 지시를 잘못 해석해서 적었습니다.",
                   "제가 번호를 잘못 맞췄습니다.", "확정 8을 제가 직접 어긴 것입니다.",
                   "지금 실제 파일 확인 안 하고 제안했습니다.", "제가 근거 없이 말했습니다.",
                   "제 분류 착오였습니다.", "제가 엉뚱한 쪽으로 갔어요.", "계속 망가뜨리고 있습니다.",
                   "앞의 제안은 철회합니다.", "토큰을 크게 낭비했습니다.")
        for text in samples:
            self.assertTrue(hook.ADMIT.search(text), text)

    def test_plain_response_ignored(self):
        self.assertEqual(self.run_hook("검증을 끝냈어요."), [])

    def test_code_block_ignored(self):
        self.assertEqual(self.run_hook("```text\n죄송\n```\n완료"), [])

    def test_subagent_ignored(self):
        self.assertEqual(self.run_hook("죄송해요.", agent_id="sub-1"), [])

    def test_codex_main_source_recorded(self):
        transcript = Path(self.tmp.name) / "main.jsonl"
        transcript.write_text(
            json.dumps({"type": "session_meta", "payload": {"source": "cli"}}),
            encoding="utf-8")
        files = self.run_hook("제 실수입니다.", transcript_path=str(transcript))
        self.assertEqual(len(files), 1)
        self.assertIn("감지 표현: 제 실수", files[0].read_text(encoding="utf-8"))

    def test_codex_subagent_source_ignored(self):
        transcript = Path(self.tmp.name) / "child.jsonl"
        transcript.write_text(
            json.dumps({"type": "session_meta", "payload": {
                "source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent"}}}}}),
            encoding="utf-8")
        self.assertEqual(self.run_hook("제 실수입니다.", transcript_path=str(transcript)), [])

    def transcript(self, entries, runner="claude"):
        path = Path(self.tmp.name) / f"{runner}.jsonl"
        rows = []
        for role, text in entries:
            if runner == "codex":
                rows.append({"type": "response_item", "payload": {
                    "type": "message", "role": role, "content": [{"text": text}]}})
            else:
                rows.append({"type": role, "message": {"content": text}})
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
        return path

    def task_doc(self, task):
        path = Path(self.tmp.name) / "log" / "state" / f"{task}-input.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 입력", encoding="utf-8")
        return f"log/state/{task}-input.md"

    def test_task_and_response_line_for_both_runners(self):
        ref = self.task_doc("explicit")
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                path = self.transcript([("user", f"입력 문서: {ref}"),
                                        ("assistant", "제 실수입니다.")], runner)
                hook.record({"session_id": runner + "-full-session-id", "transcript_path": str(path),
                             "last_assistant_message": "제 실수입니다."}, runner)
                text = next(self.dir.glob(f"*-{(runner + '-full-session-id')[:8]}.md")).read_text(encoding="utf-8")
                self.assertIn("관련 작업 ID: explicit", text)
                self.assertIn("근거 작업 위치: 1", text)
                self.assertIn(f"세션 ID: {runner}-full-session-id", text)
                lines = text.splitlines()
                incident = next(i for i, line in enumerate(lines) if line.startswith("- 사건 ID:"))
                self.assertEqual(lines[incident + 1], f"- 세션 ID: {runner}-full-session-id")
                self.assertEqual(text.count("세션 ID:"), 1)
                self.assertIn(f"runner: {runner}", text)
                self.assertIn("응답 위치: 2", text)
                self.assertNotIn(str(path), text)
                self.assertNotIn(ref, text)

    def test_latest_task_switch_and_existing_content_preserved(self):
        first, second = self.task_doc("first"), self.task_doc("second")
        path = self.transcript([("user", f"입력 문서: {first}"), ("assistant", "놓쳤어요.")])
        file = self.run_hook("놓쳤어요.", transcript_path=str(path))[0]
        before = file.read_text(encoding="utf-8")
        self.transcript([("user", f"입력 문서: {first}"), ("assistant", "놓쳤어요."),
                         ("user", f"입력 문서: {second}"), ("assistant", "빠뜨렸어요.")])
        self.run_hook("빠뜨렸어요.", transcript_path=str(path))
        text = file.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(before))
        self.assertIn("관련 작업 ID: second", text[len(before):])
        self.assertIn("응답 위치: 4", text[len(before):])

    def test_missing_or_ambiguous_latest_task_does_not_use_old_task(self):
        first, second = self.task_doc("first"), self.task_doc("second")
        for reference in ("", "log/state/missing-input.md", f"{first}\n{second}"):
            with self.subTest(reference=reference):
                entries = [("user", reference), ("assistant", "제 실수입니다.")]
                if reference:
                    entries[:0] = [("user", first), ("assistant", "완료")]
                path = self.transcript(entries)
                user, task, evidence, line = hook.context(str(path), "제 실수입니다.")
                self.assertEqual(task, "미확인")

    def test_tool_input_reference(self):
        ref = self.task_doc("tool")
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                path = self.transcript([("user", "진행해 주세요.")], runner)
                tool = {"name": "Read", "input": {"file_path": ref}, "type": "tool_use"}
                row = {"type": "assistant", "message": {"content": [tool]}}
                if runner == "codex":
                    row = {"type": "response_item", "payload": {"type": "function_call", "name": "read",
                           "arguments": json.dumps({"path": ref})}}
                with path.open("a", encoding="utf-8") as f:
                    f.write("\n" + json.dumps(row))
                self.assertEqual(hook.context(str(path), "제 실수입니다.")[1], "tool")

    def test_repeated_response_respects_current_turn(self):
        message = "제 실수입니다."
        path = self.transcript([("user", "처음"), ("assistant", message), ("user", "다음")])
        self.assertIsNone(hook.context(str(path), message)[3])
        self.transcript([("user", "처음"), ("assistant", message),
                         ("user", "다음"), ("assistant", message)])
        self.assertEqual(hook.context(str(path), message)[3], 4)

    def test_pending_response_hides_transcript_path(self):
        path = self.transcript([("user", "진행")])
        file = self.run_hook("제 실수입니다.", transcript_path=str(path))[0]
        text = file.read_text(encoding="utf-8")
        self.assertNotIn(str(path), text)
        self.assertNotIn("transcript 경로:", text)
        self.assertIn("응답 위치: 미확인", text)

    def test_codex_path_fallback_and_explicit_path_priority(self):
        path = self.transcript([("user", "진행"), ("assistant", "제 실수입니다.")], "codex")
        data = {"session_id": "fallback-full-id", "last_assistant_message": "제 실수입니다."}
        with patch.object(hook, "session_path", return_value=str(path)) as find:
            hook.record(data, "codex")
            find.assert_called_once_with(data, "codex")
        text = next(self.dir.glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("응답 위치: 2", text)
        with patch.object(hook, "session_path") as find:
            hook.record({**data, "transcript_path": str(path)}, "codex")
            find.assert_not_called()

    def test_paths_removed_from_message_blocks_for_both_runners(self):
        samples = (
            r"C:\Users\private\secret.md", "/home/private/secret.md", "log/state/secret-input.md",
            r"\\server\share\secret.md", "../secret.md", "./secret.md", "~/secret.md",
            "secret.md", r'"C:\Program Files\private\secret.md"', "'/home/private folder/secret.md'",
            "[설명](/home/private/secret.md:9)", "[잘린 설명](/C:/Users/private/sec",
        )
        for runner in ("claude", "codex"):
            for number, sample in enumerate(samples):
                with self.subTest(runner=runner, sample=sample):
                    message = f"제 실수입니다. 앞 {sample} 뒤 보존"
                    path = self.transcript([("user", f"앞 {sample} 뒤 보존"), ("assistant", message)], runner)
                    session = f"{number:02d}-{runner}-full-id"
                    hook.record({"session_id": session, "transcript_path": str(path),
                                 "last_assistant_message": message}, runner)
                    text = next(self.dir.glob(f"*-{session[:8]}.md")).read_text(encoding="utf-8")
                    self.assertNotIn("private", text)
                    self.assertNotIn("secret", text)
                    self.assertNotIn("log/state", text)
                    self.assertNotIn(str(path), text)
                    self.assertIn("뒤 보존", text)
                    if "설명" in sample:
                        self.assertIn("설명", text)
        long_path = "/home/" + "private/" * 100 + "secret.md"
        file = self.run_hook(f"제 실수입니다. {long_path} 뒤 보존")[0]
        self.assertIn("뒤 보존", file.read_text(encoding="utf-8"))

    def test_non_path_text_preserved(self):
        text = '제 실수입니다. "설명은 보존한다" [웹](https://example.com/help) 42행'
        self.assertEqual(hook.without_paths(text), text)
        for url in ("https://example.com/help", "http://example.com/docs/file.md?view=1",
                    "HTTPS://example.com/data.json"):
            with self.subTest(url=url):
                text = f'앞 "{url}" [웹]({url}) 뒤'
                self.assertEqual(hook.without_paths(text), text)
                self.assertEqual(hook.without_paths(text + " /home/private/secret.md"), text + " ")


if __name__ == "__main__":
    unittest.main()
