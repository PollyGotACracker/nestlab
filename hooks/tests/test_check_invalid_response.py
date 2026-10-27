"""check_invalid_response.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트, 공용 모듈, check_tool_use 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import check_invalid_response as hook


class RecordIncident(unittest.TestCase):
    # 임시 사건 폴더와 patch를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "log" / "incident"
        self.root_patch = patch.object(hook, "project_root", return_value=Path(self.tmp.name))
        self.root_patch.start()
        self.incident_patch = patch.object(hook, "INCIDENT_ROOT", Path(self.tmp.name))
        self.incident_patch.start()
        self.launch_patch = patch.object(hook, "launch_analysis")  # 실제 분석 세션은 띄우지 않는다.
        self.launch = self.launch_patch.start()
        self.time_patch = patch.object(hook, "datetime")
        self.clock = self.time_patch.start()
        self.clock.now.return_value = datetime(2026, 10, 26, 4, 3, 8)

    # patch와 임시 폴더를 정리한다.
    def tearDown(self):
        self.time_patch.stop()
        self.launch_patch.stop()
        self.incident_patch.stop()
        self.root_patch.stop()
        self.tmp.cleanup()

    # 응답으로 hook을 실행하고 생성된 사건 문서 목록을 반환한다.
    def run_hook(self, message, **extra):
        hook.record({"session_id": "abcdef123456", "agent_type": "rio", "last_assistant_message": message, **extra})
        return list(self.dir.glob("*.md"))

    # 인정 표현이 사건 문서에 기록되는지 확인한다.
    def test_admission_recorded(self):
        files = self.run_hook("제 판단으로 설계를 뒤집었어요.")
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertIn("조치 상태: 미착수", text)
        self.assertIn("감지 표현: 제 판단으로", text)

    # 분이 달라져도 최초 파일과 ID를 유지하며 감지를 누적하는지 확인한다.
    def test_second_hit_appends_same_file(self):
        first = self.run_hook("놓쳤어요.")[0]
        before = first.read_text(encoding="utf-8")
        self.clock.now.return_value = datetime(2026, 10, 26, 5, 7, 9)
        files = self.run_hook("빠뜨렸어요.")
        self.assertEqual(files, [first])
        text = first.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(before))
        self.assertEqual(first.stem, "20261026-0403-abcdef12")
        self.assertEqual(text.count("감지 표현"), 2)
        self.assertIn("2026-10-26 05:07:09", text)

    # 날짜 또는 세션이 바뀌면 별도 시분형 문서를 만드는지 확인한다.
    def test_date_and_session_changes_create_separate_files(self):
        self.run_hook("놓쳤어요.")
        self.run_hook("놓쳤어요.", session_id="12345678-other")
        self.clock.now.return_value = datetime(2026, 10, 27, 0, 1, 2)
        files = self.run_hook("놓쳤어요.")
        self.assertEqual({p.stem for p in files}, {
            "20261026-0403-abcdef12", "20261026-0403-12345678", "20261027-0001-abcdef12"})

    # 세션 ID가 없으면 두 runner의 파일명과 머리에 unknown을 쓰는지 확인한다.
    def test_missing_session_uses_unknown_for_both_runners(self):
        for day, runner in ((26, "claude"), (27, "codex")):
            with self.subTest(runner=runner):
                self.clock.now.return_value = datetime(2026, 10, day, 4, 3, 8)
                hook.record({"last_assistant_message": "놓쳤어요."}, runner)
                path = self.dir / f"202610{day}-0403-unknown.md"
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(f"# {runner}: {path.stem}\n\n- 사건 ID: {path.stem}\n"))
                self.assertIn("- 세션 ID: unknown\n", text)

    # 날짜형 기존 문서를 우선 선택하고 머리 정보와 분석을 보존하는지 확인한다.
    def test_legacy_file_preferred_and_analysis_preserved(self):
        self.dir.mkdir(parents=True)
        legacy = self.dir / "20261026-abcdef12.md"
        header = f"# 기존 제목\n\n- 사건 ID: {legacy.stem}\n- 조치 상태: 분석 완료\n\n"
        analysis = "## 사건 경과\n\n기존 경과\n\n## 원인과 처리\n\n기존 분석\n"
        legacy.write_text(header + "## 감지 기록\n\n" + analysis, encoding="utf-8")
        timed = self.dir / "20261026-0102-abcdef12.md"
        timed.write_text("기존 시분형 문서\n", encoding="utf-8")
        files = self.run_hook("놓쳤어요.")
        self.assertEqual(set(files), {legacy, timed})
        text = legacy.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(header))
        self.assertTrue(text.endswith(analysis))
        self.assertLess(text.index("감지 표현"), text.index("## 사건 경과"))
        self.assertEqual(timed.read_text(encoding="utf-8"), "기존 시분형 문서\n")

    # 기존 시간형 문서 중 ID 순서상 첫 파일에 누적하고 같은 이름의 폴더는 건너뛴다.
    def test_first_time_file_selected_in_id_order(self):
        self.dir.mkdir(parents=True)
        (self.dir / "20261026-0000-abcdef12.md").mkdir()
        later = self.dir / "20261026-0200-abcdef12.md"
        first = self.dir / "20261026-0100-abcdef12.md"
        for path in (later, first):
            path.write_text(f"# claude: {path.stem}\n\n- 사건 ID: {path.stem}\n\n## 감지 기록\n\n",
                            encoding="utf-8")
        before = first.read_text(encoding="utf-8").split("## 감지 기록", 1)[0]
        untouched = later.read_text(encoding="utf-8")
        self.run_hook("놓쳤어요.")
        self.assertEqual(len(list(self.dir.glob("*.md"))), 3)
        self.assertTrue(first.read_text(encoding="utf-8").startswith(before))
        self.assertIn("감지 표현: 놓쳤", first.read_text(encoding="utf-8"))
        self.assertEqual(later.read_text(encoding="utf-8"), untouched)

    # 두 runner에서 응답 전문이 코드 블록으로 기록되는지 확인한다.
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

    # 감지 항목이 다음 절 앞에 들어가는지 확인한다.
    def test_entry_goes_before_next_section(self):
        path = self.run_hook("놓쳤어요.")[0]
        path.write_text(path.read_text(encoding="utf-8") + "\n## 사건 경과\n\n경과\n\n## 원인과 처리\n", encoding="utf-8")
        self.run_hook("빠뜨렸어요.")
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("감지 표현"), 2)
        self.assertLess(text.rindex("감지 표현"), text.index("## 사건 경과"))

    # 실제 인정 표현 표본이 감지되는지 확인한다.
    def test_real_admissions_detected(self):
        samples = ("네, 제 잘못입니다.", "둘 다 제가 틀렸습니다.", "오늘 docs를 빼먹었습니다.",
                   "# 루프 설계 (빠뜨린 부분)", "제 사전 검토 누락입니다.", "제가 지시를 잘못 해석해서 적었습니다.",
                   "제가 번호를 잘못 맞췄습니다.", "확정 8을 제가 직접 어긴 것입니다.",
                   "지금 실제 파일 확인 안 하고 제안했습니다.", "제가 근거 없이 말했습니다.",
                   "제 분류 착오였습니다.", "제가 엉뚱한 쪽으로 갔어요.", "계속 망가뜨리고 있습니다.",
                   "앞의 제안은 철회합니다.", "토큰을 크게 낭비했습니다.",
                   "저의 착오였습니다.", "저의 잘못입니다.", "저의 판단 실수입니다.",
                   "제가 잘못된 판단을 했습니다.", "제 판단은 잘못됐습니다.")
        for text in samples:
            self.assertTrue(hook.ADMIT.search(text), text)

    # 감지 시 분석 세션을 띄우고, block 사유는 사건 요약 없이 재작성만 지시하는지 확인한다.
    def test_block_reason_rewrite_only_and_analysis_launched(self):
        reason = hook.record({"session_id": "abcdef123456", "last_assistant_message": "놓쳤어요."})
        self.assertIn("(놓쳤)", reason)
        self.assertIn("직전 응답에 문제가 있어 사건 문서에 기록했습니다:", reason)
        self.assertNotIn("정정:", reason)
        self.assertNotIn("스킬", reason)
        path, runner = self.launch.call_args.args
        self.assertIn(f"직전 응답에 문제가 있어 사건 문서에 기록했습니다: [{path.stem}]({path})", reason)
        self.assertEqual((path.parent, runner), (self.dir, "claude"))

    # 사유의 사건 ID 링크가 cwd가 저장소 안이면 상대 경로, 밖이면 절대 경로인지 확인한다.
    def test_reason_link_relative_or_absolute(self):
        root = Path(self.tmp.name)
        inside = hook.record({"session_id": "abcdef123456", "last_assistant_message": "놓쳤어요.", "cwd": str(root / "hooks")})
        stem = next(self.dir.glob("*.md")).stem
        self.assertIn(f"기록했습니다: [{stem}](log/incident/{stem}.md)", inside)
        outside = hook.record({"session_id": "abcdef123456", "last_assistant_message": "놓쳤어요.", "cwd": "/work/other"})
        self.assertIn(f"기록했습니다: [{stem}]({self.dir / (stem + '.md')})", outside)

    # 분리 실행 함수가 run_workflow의 incident 단계를 표준 입출력 없이 띄우는지 확인한다.
    def test_launch_analysis_detached(self):
        self.launch_patch.stop()
        try:
            with patch.object(hook.subprocess, "Popen") as popen:
                hook.launch_analysis(self.dir / "a.md", "codex")
            command = popen.call_args.args[0]
            self.assertEqual(command[1:], [str(Path(self.tmp.name) / "tools" / "run_workflow.py"),
                                           "--incident", str(self.dir / "a.md"), "--runner", "codex"])
            kwargs = popen.call_args.kwargs
            self.assertEqual(kwargs["stdout"], hook.subprocess.DEVNULL)
            self.assertTrue(kwargs.get("start_new_session") or kwargs["creationflags"] & hook.BREAKAWAY)
            with patch.object(hook.subprocess, "Popen", side_effect=OSError):
                hook.launch_analysis(self.dir / "a.md", "codex")  # 실행 실패는 무시한다.
        finally:
            self.launch_patch.start()

    # 감지 정규식에 캡처 그룹이 있으면 findall이 튜플을 돌려줘 기록과 차단이 빠진다.
    def test_withdraw_recorded_and_blocked(self):
        reason = hook.record({"session_id": "abcdef123456", "last_assistant_message": "이 안은 철회합니다."})
        self.assertIn("(철회)", reason)
        self.assertEqual(hook.ADMIT.groups, 0)

    # 사건 문서 머리에 실행 위치가 기록되는지 확인한다.
    def test_execution_location_in_header(self):
        for cwd, expected in (("/work/other", "- 실행 위치: /work/other"), (None, "- 실행 위치: 미확인")):
            with self.subTest(cwd=cwd):
                for old in self.dir.glob("*.md"):
                    old.unlink()
                extra = {"cwd": cwd} if cwd else {}
                lines = self.run_hook("놓쳤어요.", **extra)[0].read_text(encoding="utf-8").splitlines()
                session = next(i for i, l in enumerate(lines) if l.startswith("- 세션 ID:"))
                self.assertEqual(lines[session + 2], expected)

    # 같은 사용자 메시지의 1~8번째 감지는 block하고 9번째는 기록만 하며, 분석 실행은 1회인지 확인한다.
    def test_block_limit_eight_per_user_message(self):
        data = {"session_id": "abcdef123456", "last_assistant_message": "놓쳤어요.", "stop_hook_active": True}
        for n in range(1, 10):
            reason = hook.record(data)
            self.assertEqual(reason is not None, n <= 8, n)
        text = next(self.dir.glob("*.md")).read_text(encoding="utf-8")
        self.assertEqual(text.count("감지 표현"), 9)
        self.launch.assert_called_once()

    # 사유에 감지 줄 원문이 없는지 확인한다.
    def test_reason_has_no_detected_line(self):
        reason = hook.record({"session_id": "abcdef123456", "last_assistant_message": "정상 줄입니다.\n놓쳤어요."})
        self.assertNotIn("정상 줄", reason)
        self.assertNotIn("- 놓쳤어요.", reason)

    # claude와 codex runner 모두 main 출력에 decision, reason, systemMessage가 있고 감지가 없으면 출력이 없는지 확인한다.
    def test_main_output_has_system_message(self):
        for runner in ("claude", "codex"):
            for message, expected in (("놓쳤어요.", True), ("정상 응답입니다.", False)):
                out = io.StringIO()
                out.reconfigure = lambda **kwargs: None
                stdin = io.StringIO(json.dumps({"session_id": "abcdef123456", "last_assistant_message": message}))
                stdin.reconfigure = lambda **kwargs: None
                with patch.object(sys, "argv", ["check_invalid_response.py", "--runner", runner]), \
                     patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", out):
                    hook.main()
                if not expected:
                    self.assertEqual(out.getvalue(), "")
                    continue
                result = json.loads(out.getvalue())
                self.assertEqual(result["decision"], "block")
                self.assertTrue(result["systemMessage"].startswith("직전 응답에 문제가 있어 사건 문서에 기록했습니다: ["))
                self.assertIn(".md", result["systemMessage"])

    # 누락됐던 세 표현이 감지되는지 확인한다.
    def test_missing_expressions_detected(self):
        for text in ("제가 이 규칙들을 적용하지 않은 데 있습니다.", "수정안을 내야 했는데 질문을 띄웠습니다.",
                     "말씀하신 불만이 맞습니다."):
            with self.subTest(text=text):
                self.assertTrue(hook.admissions(hook.plain(text)), text)

    # 인용과 설명 문장의 조건부 표현은 감지하지 않고, 1인칭 문맥이나 직전 사용자 지적이 있으면 감지한다. 철회는 항상 감지한다. (요구사항 6 결함 6)
    def test_conditional_expressions_need_speaker_or_pointing(self):
        for text in ("요구사항 이름은 근거 없는 동의와 다짐 차단입니다.", "패턴 목록에 어긴, 확인 없이가 있습니다."):
            self.assertEqual(hook.admissions(hook.plain(text)), [], text)
        self.assertTrue(hook.admissions("제가 근거 없는 주장을 했습니다."))
        self.assertTrue(hook.admissions("근거 없는 주장을 했습니다.", "방금 답이 틀렸다고 지적했잖아"))
        self.assertTrue(hook.admissions("제가 철회합니다."))

    # 문맥 없이도 같은 문장이 자기 행동을 과거형으로 인정하면 감지하고, 인용, 동작 설명, 부정 서술은 감지하지 않는다. (요구사항 6 재작업)
    def test_conditional_self_past_statement(self):
        for text in ("승인 범위 밖 지침을 어긴 것입니다.", "이번 일은 규칙을 어긴 사건입니다.",
                     "원문을 확인 없이 쓴 문장입니다.", "근거 없는 봇 수정안을 냈습니다.",
                     "명세를 읽지 않은 채 다시 제출했습니다.", "원문을 대조하지 않은 채 분류한 것이 원인입니다.",
                     "근거 없는 수정안을 냈었습니다.", "읽지 않은 채 제출했었습니다.",
                     "확인 없이 처리했었습니다."):
            with self.subTest(text=text):
                self.assertTrue(hook.admissions(text), text)
        for text in ("요구사항 12. 근거 없는 동의와 다짐 차단",
                     "읽기 호출은 아무것도 출력하지 않은 채 통과시킵니다.",
                     "지침을 어긴 행동은 찾지 못했습니다.",
                     "이 도구는 확인 없이 덮어쓴 것입니다.", "이 hook은 근거 없는 감지를 낸 것입니다.",
                     "확인 없이 쓴 것입니다만 문제는 없습니다."):
            with self.subTest(text=text):
                self.assertEqual(hook.admissions(text), [], text)

    # Windows 분기 플래그에 DETACHED_PROCESS가 없고 CREATE_NO_WINDOW가 있는지 확인한다.
    def test_windows_flags_no_detached_process(self):
        self.assertFalse(hook.WINDOWS_FLAGS & 0x00000008)
        self.assertTrue(hook.WINDOWS_FLAGS & 0x08000000)

    # 감지 표현이 없으면 기록과 실행이 없는지 확인한다.
    def test_no_detection_no_record_no_launch(self):
        self.assertIsNone(hook.record({"session_id": "abcdef123456", "last_assistant_message": "정상 응답입니다."}))
        self.assertEqual(list(self.dir.glob("*.md")), [])
        self.launch.assert_not_called()

    # 인라인 코드 안의 표현을 감지하지 않는지 확인한다.
    def test_inline_code_ignored(self):
        self.assertEqual(self.run_hook("정규식에 `떠넘겼`, `이미 지적`을 추가했어요."), [])

    # 지침 미준수 표현을 감지하는지 확인한다.
    def test_guideline_noncompliance_detected(self):
        for text in ("정해진 절차를 끝까지 적용하지 않았습니다.", "착수 승인도 받지 않았습니다.",
                     "스킬을 적용하지 않았습니다.", "지침을 따르지 않았습니다.", "스킬을 읽지 않았습니다.", "Skill을 읽지 않았습니다."):
            with self.subTest(text=text):
                self.assertTrue(hook.ADMIT.search(text))
        for text in ("스킬 원문과 기존 변경은 확인했습니다.",):
            with self.subTest(text=text):
                self.assertIsNone(hook.ADMIT.search(text))

    # 다른 단어의 일부로 붙은 표현을 감지하지 않는지 확인한다.
    def test_attached_hangul_prefix_ignored(self):
        for text in ("실제 실수를 인정하는 문장입니다.", "문제 잘못 표기를 고칩니다.", "시행착오를 줄입니다.",
                     "제가 보기엔 잘못된 설계입니다.", "제 판단엔 잘못되었습니다."):
            with self.subTest(text=text):
                self.assertIsNone(hook.ADMIT.search(text))

    # 독립된 표현은 감지하고, 책임 회피 표현은 check_workflow_response가 맡아 이 목록에서 뺐는지 확인한다.
    def test_attached_hangul_prefix_detected(self):
        self.assertTrue(hook.ADMIT.search("제가 놓쳤습니다."))
        self.assertIsNone(hook.ADMIT.search("승인하실 일은 없습니다."))
        self.assertEqual(self.run_hook("이미 충분합니다."), [])

    # 따옴표로 인용한 표현을 감지하지 않는지 확인한다.
    def test_quoted_text_ignored(self):
        for text in ('패턴을 "확인하지 않고"로 바꿨어요.', "패턴을 '놓쳤'으로 바꿨어요.",
                     "패턴을 “빠뜨”로 바꿨어요.", "패턴을 ‘어겼’으로 바꿨어요."):
            with self.subTest(text=text):
                self.assertEqual(self.run_hook(text), [])

    # 지침이 요구하는 확인 범위 서술은 감지하지 않는지 확인한다.
    def test_unverified_scope_report_ignored(self):
        self.assertEqual(len(self.run_hook("파일을 확인하지 않고 고쳤습니다.")), 1)

    # 여러 유형의 표현이 한 번에 감지되는지 확인한다.
    def test_many_types_blocks(self):
        reason = hook.record({"session_id": "abcdef123456",
                              "last_assistant_message": "죄송, 엉뚱, 놓쳤, 어겼, 근거 없이 목록입니다."})
        self.assertIsNotNone(reason)
        self.assertEqual(len(list(self.dir.glob("*.md"))), 1)

    # 감지 항목이 분석 절 앞에 들어가는지 확인한다.
    def test_entry_inserted_before_analysis(self):
        file = self.run_hook("놓쳤어요.")[0]
        file.write_text(file.read_text(encoding="utf-8") + "\n## 원인과 처리\n\n- 분석\n", encoding="utf-8")
        self.run_hook("빠뜨렸어요.")
        text = file.read_text(encoding="utf-8")
        self.assertLess(text.index("감지 표현: 빠뜨"), text.index("## 원인과 처리"))
        self.assertTrue(text.endswith("- 분석\n"))

    # 일반 응답에는 block 사유가 없는지 확인한다.
    def test_plain_response_no_reason(self):
        self.assertIsNone(hook.record({"session_id": "abcdef123456", "last_assistant_message": "완료"}))

    # 일반 응답은 기록하지 않는지 확인한다.
    def test_plain_response_ignored(self):
        self.assertEqual(self.run_hook("검증을 끝냈어요."), [])

    # 코드 블록 안의 표현을 감지하지 않는지 확인한다.
    def test_code_block_ignored(self):
        self.assertEqual(self.run_hook("```text\n죄송\n```\n완료"), [])

    # 서브에이전트 응답을 검사하지 않는지 확인한다.
    def test_subagent_ignored(self):
        self.assertEqual(self.run_hook("죄송해요.", agent_id="sub-1"), [])

    # Codex 메인 thread 응답은 기록하는지 확인한다.
    def test_codex_main_source_recorded(self):
        transcript = Path(self.tmp.name) / "main.jsonl"
        transcript.write_text(
            json.dumps({"type": "session_meta", "payload": {"source": "cli"}}),
            encoding="utf-8")
        files = self.run_hook("제 실수입니다.", transcript_path=str(transcript))
        self.assertEqual(len(files), 1)
        self.assertIn("감지 표현: 제 실수", files[0].read_text(encoding="utf-8"))

    # Codex 하위 thread 응답은 기록하지 않는지 확인한다.
    def test_codex_subagent_source_ignored(self):
        transcript = Path(self.tmp.name) / "child.jsonl"
        transcript.write_text(
            json.dumps({"type": "session_meta", "payload": {
                "source": {"subagent": {"thread_spawn": {"parent_thread_id": "parent"}}}}}),
            encoding="utf-8")
        self.assertEqual(self.run_hook("제 실수입니다.", transcript_path=str(transcript)), [])

    # runner별 대화 기록 파일을 만드는 보조 함수이다.
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

    # 입력 문서 파일을 만들고 상대 경로를 반환하는 보조 함수이다.
    def task_doc(self, task):
        path = Path(self.tmp.name) / "log" / "state" / f"{task}-input.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 입력", encoding="utf-8")
        return f"log/state/{task}-input.md"

    # 두 runner에서 작업 ID와 응답 위치가 기록되는지 확인한다.
    def test_task_and_response_line_for_both_runners(self):
        ref = self.task_doc("explicit")
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                path = self.transcript([("user", f"입력 문서: {ref}"),
                                        ("assistant", "제 실수입니다.")], runner)
                hook.record({"session_id": runner + "-full-session-id", "transcript_path": str(path),
                             "last_assistant_message": "제 실수입니다."}, runner)
                file = next(self.dir.glob(f"*-{(runner + '-full-session-id')[:8]}.md"))
                self.assertEqual(file.stem, f"20261026-0403-{(runner + '-full-session-id')[:8]}")
                text = file.read_text(encoding="utf-8")
                self.assertIn("관련 작업 ID: explicit", text)
                self.assertIn("근거 작업 위치: 1", text)
                self.assertIn(f"세션 ID: {runner}-full-session-id", text)
                lines = text.splitlines()
                self.assertEqual(lines[0], f"# {runner}: {file.stem}")
                self.assertEqual([line.split(":", 1)[0] for line in lines[2:10]],
                                 ["- 사건 ID", "- runner", "- 세션 ID", "- 관련 작업 ID",
                                  "- 실행 위치", "- 발생 및 확인 시점", "- 관련 담당", "- 조치 상태"])
                self.assertEqual(lines[2], f"- 사건 ID: {file.stem}")
                self.assertEqual(lines[3], f"- runner: {runner}")
                self.assertEqual(lines[4], f"- 세션 ID: {runner}-full-session-id")
                self.assertEqual(text.count("세션 ID:"), 1)
                self.assertIn(f"runner: {runner}", text)
                self.assertIn("응답 위치: 2", text)
                self.assertNotIn(str(path), text)
                self.assertNotIn(ref, text)

    # 가장 최근 작업 참조로 바뀌어도 기존 내용이 보존되는지 확인한다.
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

    # 최신 참조가 없거나 모호하면 이전 작업을 쓰지 않는지 확인한다.
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

    # 도구 입력에 적힌 작업 참조도 근거로 쓰는지 확인한다.
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

    # 같은 문구가 이전 턴에 있어도 현재 턴 응답을 기준으로 삼는지 확인한다.
    def test_repeated_response_respects_current_turn(self):
        message = "제 실수입니다."
        path = self.transcript([("user", "처음"), ("assistant", message), ("user", "다음")])
        self.assertIsNone(hook.context(str(path), message)[3])
        self.transcript([("user", "처음"), ("assistant", message),
                         ("user", "다음"), ("assistant", message)])
        self.assertEqual(hook.context(str(path), message)[3], 4)

    # 기록 전 응답에서도 대화 기록 경로가 문서에 남지 않는지 확인한다.
    def test_pending_response_hides_transcript_path(self):
        path = self.transcript([("user", "진행")])
        file = self.run_hook("제 실수입니다.", transcript_path=str(path))[0]
        text = file.read_text(encoding="utf-8")
        self.assertNotIn(str(path), text)
        self.assertNotIn("transcript 경로:", text)
        self.assertIn("응답 위치: 미확인", text)

    # Codex 기록 경로 대체 조회와 명시 경로 우선순위를 확인한다.
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

    # 사용자 메시지와 응답 발췌에서 경로가 제거되는지 확인한다.
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

    # 사건 20261024-13c433b9에서 확인한 오탐 문맥은 기록하지 않는다. 실제 인정은 그대로 기록한다.
    def test_false_positive_contexts_ignored(self):
        user = "통보 금지, 날치기 통과 금지, 검토 없이 승인 요청 금지를 제안 기준으로 해."
        path = self.transcript([("user", user)])
        cases = (
            "- **검토 없이 승인 요청 금지**: 검토 내용이 담긴 승인 요청만 기록한다.",  # 사용자 기준 이름 인용
            "상태 문서에 따르면 director가 정한 결정은 철회된 상태예요.",  # 수동 서술
            "감지된 `빠뜨`는 절차의 목적을 설명한 말이에요.",  # 오탐 분석 줄
            "## 이번 세션에서 지침을 지키지 않음",  # 제목 줄
            "이 문장은 제 실수를 인정한 문장이 아니에요.",  # 부정 서술
            "현재 실행 승인 범위 밖이라 적용하지 않았습니다.",  # 지침이 요구한 미적용 보고
            "상태 문서의 근거 없는 문장은 이미 고쳤습니다.",  # 고친 결과 보고
            "| Bash `claude -p`는 hook을 거치지 않는다 | 거짓 |",  # 기록된 위반 인용 표 행
        )
        for message in cases:
            with self.subTest(message=message):
                self.assertEqual(hook.admissions(hook.plain(message), user), [], message)
        self.assertEqual(self.run_hook(cases[0], transcript_path=str(path)), [])
        for message in ("두 안은 철회할게요.", "제 실수입니다.", "검토 없이 기록했어요.",
                        "응답 전체를 확인하지 않고 '없다'고 판정했습니다."):
            with self.subTest(message=message):
                self.assertTrue(hook.admissions(hook.plain(message), "다른 요청"), message)

    # 경로가 아닌 문장은 그대로 남는지 확인한다.
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
