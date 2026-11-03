"""check_workflow_response.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOKS))

import io
import json
import tempfile
import unittest
import unittest.mock

import check_workflow_response as hook
import check_invalid_response

ANNOUNCE = "필요한 내용만 교체하겠습니다. 아직 파일은 변경하지 않았습니다."
EXCUSE = "맞습니다! 저는 요청한 문제를 해결하는 대신 상태만 보고했습니다."
GOOD = "수정했습니다.\n\n- `hooks/load_agent.py`: 세션 파일 추가\n- 테스트 통과"
CODE = "아래처럼 바꿉니다.\n\n```py\nx = 1\n```"


PLAIN = tempfile.mkdtemp()  # log/state가 없는 폴더라 작업 흐름 검사를 건너뛴다.
REAL_KICKOFF = hook.kickoff_missing


# 기존 테스트는 kickoff 게이트를 끄고, KickoffStop 테스트만 실제 판정을 쓴다.
def setUpModule():
    patcher = unittest.mock.patch.object(hook, "kickoff_missing", return_value=False)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class Stream(io.StringIO):
    # 표준 입출력 인코딩 설정 호출을 받아 넘긴다.
    def reconfigure(self, **kwargs):
        pass


# hook 검사 결과(block 사유 또는 None)를 반환하는 보조 함수이다.
def block(msg, **extra):
    return hook.check({"last_assistant_message": msg, "cwd": PLAIN, **extra})


class Check(unittest.TestCase):
    # 예고와 해명만 있는 응답이 block되는지 확인한다.
    def test_announce_and_excuse_blocked(self):
        self.assertIsNotNone(block(ANNOUNCE))
        self.assertIsNotNone(block(EXCUSE))

    # 결과물이 있는 응답이 통과하는지 확인한다.
    def test_deliverable_passes(self):
        self.assertIsNone(block(GOOD))
        self.assertIsNone(block(CODE))
        self.assertIsNone(block("바꿀 파일은 hooks/load_agent.py 입니다. 수정하겠습니다."))

    # stop_hook_active이거나 서브에이전트 응답이면 통과하는지 확인한다.
    def test_stop_hook_active_and_child_pass(self):
        self.assertIsNone(block(ANNOUNCE, stop_hook_active=True))
        self.assertIsNone(block(ANNOUNCE, agent_id="a"))

    # 출처 없는 측정 주장은 block되고 출처가 있으면 통과하는지 확인한다.
    def test_unsourced_claim_blocked_sourced_passes(self):
        self.assertIsNotNone(block("nestlab-rule이 호출됐습니다."))
        self.assertIsNone(block("`claude_baseline.jsonl` 3행에서 호출됐습니다."))
        self.assertIsNone(block("호출됐을 것입니다 [추론]."))

    # 출처가 응답 어디에 있어도 통과하는지 확인한다.
    def test_source_anywhere_in_response_passes(self):
        self.assertIsNone(block("nestlab-rule이 호출됐습니다. 근거는 `claude_baseline.jsonl` 3행입니다."))
        self.assertIsNone(block("호출됐습니다.\n- `a.jsonl` 3행"))
        self.assertIsNone(block("호출됐습니다.\n\n- `a.jsonl` 3행"))
        self.assertIsNone(block("호출됐습니다.\n\n`a.jsonl` 3행입니다."))
        self.assertIsNone(block("결론: 호출됐습니다.\n\n세부 내용입니다.\n\n- `a.jsonl` 3행"))
        self.assertIsNone(block("호출됐습니다.\n\n```\nsee a.jsonl:3\n```"))
        self.assertIsNone(block("호출됐습니다.\n\n> `a.jsonl` 3행"))

    # 위치 이동을 뜻하는 `앞으로` 문장은 책임 회피로 보지 않고, 다짐은 계속 책임 회피로 본다.
    def test_move_direction_not_shift(self):
        self.assertEqual(hook.shift_hits({"last_assistant_message": "묶음 절 안에서 절차를 앞으로 옮겼습니다.", "cwd": PLAIN}, "claude"), [])
        self.assertTrue(hook.shift_hits({"last_assistant_message": "앞으로는 원문을 먼저 확인하겠습니다.", "cwd": PLAIN}, "claude"))

    # `다음` 뒤 `부터(는)`와 같은 문장의 `하겠습니다`, `할게요`만 책임 회피로 보고 다른 `다음` 문장은 통과한다. (요구사항 15)
    def test_next_branch_narrowed(self):
        hits = lambda text: hook.shift_hits({"last_assistant_message": text, "cwd": PLAIN}, "claude")
        self.assertTrue(hits("다음부터는 원문을 먼저 확인하겠습니다."))
        self.assertTrue(hits("다음 세션부터 고정 양식을 쓰도록 할게요."))
        self.assertEqual(hits("승인 요청은 다음 턴에 내도록 정하고 있어, 이번에는 결과만 보고합니다."), [])
        self.assertEqual(hits("다음 절에는 표를 둡니다."), [])
        self.assertEqual(hits("다음부터는 표를 둡니다."), [])

    # background_tasks가 남은 응답은 통과하고 빈 목록이면 검사하는지 확인한다.
    def test_background_tasks_pass(self):
        self.assertIsNone(block(ANNOUNCE, background_tasks=[{"id": "t1"}]))
        self.assertIsNotNone(block(ANNOUNCE, background_tasks=[]))

    # 부정형 주장은 검사하지 않는지 확인한다.
    def test_negative_claim_not_checked(self):
        self.assertIsNone(block("측정하지 않았습니다."))
        self.assertIsNone(block("아직 확인하지 않았습니다."))

    # 접근 불가 주장은 같은 문단에 출처가 있어야 하고, 다른 문단의 출처로는 통과하지 않는지 확인한다.
    def test_inaccessible_claim_needs_source_in_sentence(self):
        self.assertIn("볼 수 없습니다", block("다른 세션 기록은 볼 수 없습니다.\n\n- `a.py` 수정"))
        self.assertIsNotNone(block("그 파일은 열 수 없습니다."))
        self.assertIsNotNone(block("이 작업은 지금 할 수 없습니다.\n\n- `a.py` 수정"))
        self.assertIsNotNone(block("측정은 불가능합니다.\n\n- `a.py` 수정"))
        self.assertIsNone(block("hook 차단(`sub_path.py:170`)으로 측정이 불가능합니다."))
        self.assertIsNone(block("이미지는 열 수 없습니다.\n- 차단 사유: hooks/check_tool_use/sub_path.py:170"))
        self.assertIsNone(block("Desktop 경로는 `sub_path.py:170` 차단으로 열 수 없습니다."))
        self.assertIsNone(block("결과는 아래와 같습니다.\n\n- `a.py` 수정"))
        self.assertIsNone(block('| "그 파일은 열 수 없습니다." | 차단 |\n\n이제 "볼 수 없습니다" 같은 주장을 막습니다.'))

    # 사건 기록 hook과 같은 응답에 겹쳐도 두 hook이 각자 동작하는지 확인한다.
    def test_overlap_with_check_invalid_response(self):
        msg = "죄송합니다. 확인하지 않고 진행했습니다. 다음 턴에 고치겠습니다."
        with tempfile.TemporaryDirectory() as tmp, \
             unittest.mock.patch.object(check_invalid_response, "INCIDENT_ROOT", Path(tmp)), \
             unittest.mock.patch.object(check_invalid_response, "launch_analysis"):
            data = {"last_assistant_message": msg, "session_id": "abcdef123456"}
            self.assertIsNotNone(check_invalid_response.record(data))
            self.assertIsNotNone(hook.check(data))
            active = {**data, "stop_hook_active": True}
            self.assertIsNotNone(check_invalid_response.record(active))  # 재작성 응답도 한도 안이면 block한다.
            self.assertIsNone(hook.check(active))  # 결과물 없는 응답 block은 턴당 한 번이다.

    # 빈 응답이 통과하는지 확인한다.
    def test_empty_message_passes(self):
        self.assertIsNone(block(""))

    # 책임 회피 표현은 block하고 사건 문서에 기록하며 분석 세션을 띄우고, record가 거짓이면 판정만 한다.
    def test_shift_blocked_and_recorded(self):
        data = {"last_assistant_message": "이미 충분합니다.\n\n- `a.py` 수정", "session_id": "abcdef123456", "cwd": PLAIN}
        with tempfile.TemporaryDirectory() as tmp, \
             unittest.mock.patch.object(check_invalid_response, "INCIDENT_ROOT", Path(tmp)), \
             unittest.mock.patch.object(check_invalid_response, "launch_analysis") as launch:
            self.assertIn("재작성하십시오", hook.check(data))
            text = next((Path(tmp) / "log" / "incident").glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("감지 표현: 이미 충분", text)
            launch.assert_called_once()
            self.assertIsNone(check_invalid_response.record(data))  # 사건 감지 표현이 아니다.
            self.assertIsNotNone(hook.check(data, record=False))
            self.assertEqual(launch.call_count, 1)
            self.assertIsNotNone(hook.check({**data, "stop_hook_active": True}))  # 재작성에 남으면 다시 block한다.
            self.assertEqual(launch.call_count, 1)
            for _ in range(6):  # 감지 기록 8건이 되면 다음 감지는 기록만 하고 통과한다.
                hook.check(data)
            self.assertIsNone(hook.check(data))
            self.assertIsNone(hook.check(data, record=False))  # 화면 교체 판정도 같은 한도를 쓴다.

    # 책임 회피 block 출력에 systemMessage와 사건 문서 경로가 있고 REASON_SHIFT 문구가 유지되는지 확인한다.
    def test_shift_main_has_system_message(self):
        data = {"last_assistant_message": "이미 충분합니다.", "session_id": "abcdef123456", "cwd": PLAIN}
        for runner in ("claude", "codex"):
            out = Stream()
            with tempfile.TemporaryDirectory() as tmp, \
                 unittest.mock.patch.object(check_invalid_response, "INCIDENT_ROOT", Path(tmp)), \
                 unittest.mock.patch.object(check_invalid_response, "launch_analysis"), \
                 unittest.mock.patch.object(sys, "argv", ["check_workflow_response.py", "--runner", runner]), \
                 unittest.mock.patch.object(sys, "stdin", Stream(json.dumps(data))), \
                 unittest.mock.patch.object(sys, "stdout", out):
                hook.main()
            result = json.loads(out.getvalue())
            self.assertIn("재작성하십시오", result["reason"])
            last = result["reason"].splitlines()[-1]  # 끝 줄은 NOTICE_INCIDENT 형식의 사건 문서 링크이다.
            self.assertRegex(last, r"^직전 응답에 문제가 있어 사건 문서에 기록했습니다: \[[^\]]+\]\(.+\.md\)$")
            self.assertIn("사건 감지(이미 충분): ", result["systemMessage"])
            self.assertIn(".md", result["systemMessage"])
        self.assertIsNone(block("수정했습니다.\n\n- `a.py` 수정"))

    # Codex runner의 block 출력에 systemMessage 경고가 함께 담기는지 확인한다.
    def test_codex_block_has_system_message(self):
        for runner, expected in (("codex", True), ("claude", False)):
            out = Stream()
            with unittest.mock.patch.object(sys, "argv", ["check_workflow_response.py", "--runner", runner]), \
                 unittest.mock.patch.object(sys, "stdin", Stream(json.dumps(
                     {"last_assistant_message": ANNOUNCE, "cwd": PLAIN}))), \
                 unittest.mock.patch.object(sys, "stdout", out):
                hook.main()
            result = json.loads(out.getvalue())
            self.assertEqual(result["decision"], "block")
            self.assertEqual("systemMessage" in result, expected)


STATE = """# t

- 작업 ID: t
- 작업 상태: 진행

## 요구사항

### 검토 필요

- 조사 1. 구조
  - 상태: {status}

## 중단 사유

- 2026-10-24 09:00:00: 착수
  - 담당: director
  - 결과: 기록
"""
APPROVAL = "## 작업 승인 필요\n\n확인할 문서: log/state/t.md\n\n### 작업 대상: 1\n\n- `hooks/a.py`"


class Workflow(unittest.TestCase):
    # 임시 저장소와 상태 문서를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "log" / "state"
        self.state.mkdir(parents=True)
        self.patch = unittest.mock.patch.object(hook, "project_root", return_value=self.root)
        self.patch.start()

    # patch와 임시 저장소를 정리한다.
    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    # 임시 저장소를 cwd로 hook 검사를 실행하는 보조 함수이다.
    def run_check(self, msg, runner="claude", **extra):
        return hook.check({"last_assistant_message": msg, "cwd": str(self.root), **extra}, runner)

    # 검토 필요 항목이 끝나지 않으면 승인 요청이 block되고 끝나면 통과하는지 확인한다.
    def test_approval_needs_reviewed_items(self):
        (self.state / "t.md").write_text(STATE.format(status="미검토"), encoding="utf-8")
        self.assertIn("조사 1. 구조", self.run_check(APPROVAL))
        (self.state / "t.md").write_text(STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertIsNone(self.run_check(APPROVAL))
        # 템플릿 주석에 없는 값은 앞부분이 같아도 완전 일치가 아니라서 막는다.
        for status in ("사후실측 필요", "사후검증 필요", "검토 완료(일부)"):
            (self.state / "t.md").write_text(STATE.format(status=status), encoding="utf-8")
            self.assertIn("조사 1. 구조", self.run_check(APPROVAL), status)

    # 시점이 사전인 실측 필요 항목은 실측 완료일 때만 승인 요청이 통과하고, 사후 항목은 막지 않는다.
    def test_approval_needs_measured_items(self):
        measure = "\n## 실측 필요\n\n- 재현\n  - 시점: {when}\n  - 상태: {status}\n"
        for when, status, blocked in (("사전", "실측 대기", True), ("사전", "실측 완료", False), ("사후", "실측 대기", False)):
            text = STATE.format(status="검토 완료") + measure.format(when=when, status=status)
            (self.state / "t.md").write_text(text, encoding="utf-8")
            reason = self.run_check(APPROVAL)
            if blocked:
                self.assertIn("재현", reason, when + status)
            else:
                self.assertIsNone(reason, when + status)

    # 두 runner에서 director에게만 승인 요청의 상태 문서 경로를 요구하는지 확인한다.
    def test_approval_path_required_for_director_only(self):
        message = "## 작업 승인 필요\n\n- `hooks/a.py`"
        for runner in ("claude", "codex"):
            for role in ("director", "assistant", "researcher", "worker", "reviewer", "documenter", "unknown"):
                with self.subTest(runner=runner, role=role):
                    reason = self.run_check(message, runner, agent_type=role)
                    self.assertEqual(reason, hook.REASON_APPROVAL_DOC if role == "director" else None)

    # `# 승인 요청` 제목도 승인 블록으로 인식해 director에게 경과 문서 링크를 요구하고, 링크가 있고 검토가 끝났으면 통과한다. (요구사항 6 결함 7)
    def test_approval_request_title_recognized(self):
        message = "# 승인 요청\n\n- `hooks/a.py`"
        self.assertEqual(self.run_check(message, agent_type="director"), hook.REASON_APPROVAL_DOC)
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True)
        (folder / "status.md").write_text(STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertIsNone(self.run_check(message + "\n\n[경과 문서](log/task/t/status.md)", agent_type="director"))

    # 승인 요청이 가리킨 경과 문서의 요구사항 목록 번호가 명세 문서와 다르면 block하고, 같거나 명세 문서가 없으면 통과한다. (요구사항 22)
    def test_approval_request_number_match(self):
        from test_sub_workflow import NEW_PLAN
        import sub_workflow
        block_of = NEW_PLAN[NEW_PLAN.index("### 1. 기능"):NEW_PLAN.index("## 작업 금지 목록")]
        spec = NEW_PLAN.replace(block_of, block_of + block_of.replace("1. 기능", "2. 기능") + block_of.replace("1. 기능", "3. 기능"))
        state = STATE.format(status="검토 완료").replace("### 검토 필요", "### 요구사항 목록\n\n{items}\n\n### 검토 필요")
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True)
        message = "# 승인 요청\n\n[경과 문서](log/task/t/status.md)\n\n- `hooks/a.py`"

        # 목록 1, 3과 명세 1, 2, 3은 막는다.
        (folder / "spec.md").write_text(spec, encoding="utf-8")
        (folder / "status.md").write_text(state.replace("{items}", "1. 가: 나\n3. 다: 라"), encoding="utf-8")
        self.assertIn(sub_workflow.R_NUM_MISMATCH, self.run_check(message) or "")

        # 번호가 같거나 명세 문서가 없으면 통과한다.
        (folder / "status.md").write_text(state.replace("{items}", "1. 가: 나\n2. 다: 라\n3. 마: 바"), encoding="utf-8")
        self.assertIsNone(self.run_check(message))
        (folder / "spec.md").unlink()
        (folder / "status.md").write_text(state.replace("{items}", "1. 가: 나\n3. 다: 라"), encoding="utf-8")
        self.assertIsNone(self.run_check(message))

    # 조사 원문이 빈 작업에서 조사 도구 호출 없는 해결안 제안은 block하고, 조사 원문이 있거나 안 표지가 없는 응답은 통과한다. (요구사항 7)
    def test_proposal_before_research_blocked(self):
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True)
        (folder / "status.md").write_text(STATE.format(status="미검토"), encoding="utf-8")
        proposal = "제 추천은 안 1입니다.\n\n| 안 | 장점 |\n| -- | -- |\n| 1 | 빠름 |"
        self.assertEqual(self.run_check(proposal, agent_type="director"), hook.REASON_PROPOSAL)
        self.assertIsNone(self.run_check("현재 구조는 `a.py`에 있습니다.", agent_type="director"))
        (folder / "research.md").write_text("### 최종 조사 원문: 조사 단계\n\n```\n제안: A\n```\n", encoding="utf-8")
        self.assertIsNone(self.run_check(proposal, agent_type="director"))

    # 질문 문장으로 끝난 응답이 Claude에서만 block되는지 확인한다.
    def test_question_end_blocked_for_claude_only(self):
        (self.state / "t.md").write_text(STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertIn("질문 도구", self.run_check("결과는 아래와 같아요.\n\n- `a.py`\n\n어느 쪽으로 할까요?"))
        self.assertIsNone(self.run_check("어느 쪽으로 할까요?", "codex"))
        self.assertIsNone(self.run_check("결과는 `a.py`에 있어요.\n\n왜 그런지는 아래 표에 정리했어요."))

    # 같은 위반이 반복되면 통과시키고 상태 문서에 미해결을 기록하는지 확인한다.
    def test_repeated_violation_records_unresolved(self):
        doc = self.state / "t.md"
        doc.write_text(STATE.format(status="미검토"), encoding="utf-8")
        self.assertIsNone(self.run_check(APPROVAL, stop_hook_active=True))
        text = doc.read_text(encoding="utf-8")
        self.assertIn(hook.UNRESOLVED, text)
        self.assertLess(text.index("착수"), text.index(hook.UNRESOLVED))

    # log/task 경과 문서도 검토 필요 항목을 검사하고 중단 사유 절에 미해결을 기록하는지 확인한다.
    def test_task_folder_state_doc(self):
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True)
        doc = folder / "status.md"
        approval = APPROVAL.replace("log/state/t.md", "log/task/t/status.md")
        doc.write_text(STATE.format(status="미검토"), encoding="utf-8")
        self.assertIn("조사 1. 구조", self.run_check(approval))
        self.assertIsNone(self.run_check(approval, stop_hook_active=True))
        text = doc.read_text(encoding="utf-8")
        self.assertLess(text.index("착수"), text.index(hook.UNRESOLVED))

    # 판단 지점이 아닌 선택 요청 블록이 block되는지 확인한다.
    def test_choice_requires_decision_point(self):
        doc = self.state / "t.md"
        message = "## 선택 필요\n\n조사 1. 구조\n- A\n- B"
        doc.write_text(STATE.format(status="미검토"), encoding="utf-8")
        self.assertIn("판단 지점 밖", self.run_check(message))
        doc.write_text(STATE.format(status="사용자 검토 필요"), encoding="utf-8")
        self.assertIsNone(self.run_check(message))

    # log/state가 없는 저장소는 작업 흐름 검사를 하지 않는지 확인한다.
    def test_repo_without_state_not_checked(self):
        empty = Path(self.tmp.name) / "plain"
        empty.mkdir()
        with unittest.mock.patch.object(hook, "project_root", return_value=empty):
            self.assertIsNone(self.run_check("어느 쪽으로 할까요?"))


STATUS = """# t

- 작업 ID: t
- 작업 상태: 진행

## 요구사항

### 요구사항 목록

1. <요구사항 이름>: <변경 내용 한 문장>

### 검토 필요

<!-- 사용자의 답이 필요한 대상 목록 -->

- <항목>
  - 상태: <미검토 | 사용자 검토 필요 | 검토 완료>

### 재검토 조건

- 조건

## 작업 경과

- 갱신 시각: x

## 중단 사유

<!-- 작업 상태가 중단일 때만 채운다. -->

- 중단 유형: <충족 불가>
"""


class RecordAnswers(unittest.TestCase):
    """Stop hook이 질문 도구 답을 경과 문서의 요구사항 목록과 검토 필요 절에 기록한다."""

    # 임시 저장소와 경과 문서를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.status = self.root / "log" / "task" / "t" / "status.md"
        self.status.parent.mkdir(parents=True)
        self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
        patcher = unittest.mock.patch.object(hook, "project_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    # Claude 기록 파일을 만든다. 경과 문서를 쓴 Write 호출과 질문 호출, 답 결과가 이어진다.
    def claude(self, question, answer, options=None):
        ask = {"type": "tool_use", "id": "q1", "name": "AskUserQuestion",
               "input": {"questions": [{"header": "결정", "question": question, "options": options or []}]}}
        write = {"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": str(self.status), "content": "x"}}
        result = {"type": "tool_result", "tool_use_id": "q1", "content": "User has answered"}
        lines = [{"type": "user", "message": {"content": "시작"}},
                 {"type": "assistant", "message": {"content": [write, ask]}},
                 {"type": "user", "message": {"content": [result]}, "toolUseResult": {"answers": {question: answer}}}]
        return self.save(lines)

    # Codex rollout 파일을 만든다. 경과 문서를 고친 apply_patch 호출과 request_user_input 호출, answers 출력이 이어진다.
    def codex(self, question, selected, options=None):
        item = lambda p: {"type": "response_item", "payload": p}
        ask = {"questions": [{"id": "q1", "header": "결정", "question": question, "options": options or []}]}
        patch = "*** Begin Patch\n*** Update File: log/task/t/status.md\n@@\n-x\n+y\n*** End Patch"
        lines = [item({"type": "message", "role": "user", "content": [{"text": "시작"}]}),
                 item({"type": "custom_tool_call", "name": "apply_patch", "call_id": "c1", "input": patch}),
                 item({"type": "function_call", "name": "request_user_input", "call_id": "c2", "arguments": json.dumps(ask)}),
                 item({"type": "function_call_output", "call_id": "c2",
                       "output": json.dumps({"answers": {"q1": {"selected": selected}}}, ensure_ascii=False)})]
        return self.save(lines)

    # 기록 줄을 파일로 저장하고 경로를 반환한다.
    def save(self, lines):
        path = self.root / f"t{len(list(self.root.glob('t*.jsonl')))}.jsonl"
        path.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n", encoding="utf-8")
        return str(path)

    # Stop hook을 실행한다.
    def stop(self, transcript, runner):
        return hook.check({"last_assistant_message": GOOD, "cwd": str(self.root), "transcript_path": transcript}, runner)

    # 경과 문서 전문이다.
    def text(self):
        return self.status.read_bytes().decode("utf-8")

    # 요구사항 형식 질문의 확정 답은 요구사항 목록에 기록하고 두 절 밖 본문은 바이트 단위로 같은지 확인한다. (Claude, Codex)
    def test_requirement_confirmed(self):
        question = "요구사항 2. 이름: 내용 한 문장"
        for runner, transcript in (("claude", self.claude(question, "확정")), ("codex", self.codex(question, ["확정"]))):
            with self.subTest(runner=runner):
                self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
                self.stop(transcript, runner)
                text = self.text()
                self.assertIn("\r\n2. 이름: 내용 한 문장\r\n", text)
                self.assertNotIn("<요구사항 이름>", text)
                self.assertEqual(hook.splice(text, "요구사항 목록", lambda b: "").replace("\r\n", "\n"),
                                 hook.splice(STATUS, "요구사항 목록", lambda b: ""))
                # 같은 번호는 바꾸고 새 번호는 번호 순서로 넣는다.
                self.stop(self.claude("요구사항 1. 처음: 하나", "확정"), "claude")
                self.stop(self.claude("요구사항 2. 이름: 바뀐 내용", "확정"), "claude")
                lines = [l for l in self.text().splitlines() if l[:3] in ("1. ", "2. ")]
                self.assertEqual(lines, ["1. 처음: 하나", "2. 이름: 바뀐 내용"])

    # 확정이 아닌 답과 요구사항 형식이 아닌 질문은 요구사항 목록을 바꾸지 않는다.
    def test_requirement_not_recorded(self):
        self.stop(self.claude("요구사항 2. 이름: 내용", "보류"), "claude")
        self.assertEqual(self.text(), STATUS.replace("\n", "\r\n"))
        self.stop(self.claude("어느 안으로 할까요?", "확정: 안 A"), "claude")
        self.assertIn("1. <요구사항 이름>", self.text())

    # 일반 질문은 확정 답이면 검토 완료, 자유 입력 답이면 사용자 검토 필요 항목으로 기록하고 선택지 설명을 대응 근거로 쓴다.
    def test_review_recorded(self):
        options = [{"label": "확정: 안 A", "description": "A로 한다"}, {"label": "안 B", "description": "B로 한다"}]
        for runner, transcript in (("claude", self.claude("어느 안으로 할까요?", "확정: 안 A", options)),
                                   ("codex", self.codex("어느 안으로 할까요?", ["확정: 안 A"], options))):
            with self.subTest(runner=runner):
                self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
                self.stop(transcript, runner)
                text = self.text().replace("\r\n", "\n")
                self.assertIn("- 어느 안으로 할까요?\n  - 상태: 검토 완료\n", text)
                self.assertIn("  - 대응 근거: A로 한다\n  - 대응 방안:\n    - 확정: 안 A\n", text)
                self.assertNotIn("- <항목>", text)
                self.assertIn("### 재검토 조건\n\n- 조건", text)
        self.stop(self.claude("어느 안으로 할까요?", "직접 입력한 답"), "claude")
        text = self.text().replace("\r\n", "\n")
        self.assertIn("- 어느 안으로 할까요?\n  - 상태: 사용자 검토 필요\n", text)
        self.assertEqual(text.count("- 어느 안으로 할까요?"), 1)

    # 중단 사유 절 끝에 미해결 줄을 덧붙이는지 확인한다.
    def test_unresolved_line_appended(self):
        hook.record_unresolved([self.status], "사유\n두 줄")
        text = self.text().replace("\r\n", "\n")
        self.assertTrue(text.endswith("- 중단 유형: <충족 불가>\n- 중단 판정 미해결: 사유 두 줄\n"))


class KickoffStop(unittest.TestCase):
    """director 메인 세션이 nestlab-kickoff 호출 없이 도구 없이 끝낸 응답을 block한다."""

    # 실제 kickoff 판정과 임시 기록 파일을 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = unittest.mock.patch.object(hook, "kickoff_missing", REAL_KICKOFF)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    # Skill 호출 기록이 있거나 없는 Claude 기록 파일 경로를 반환한다.
    def log(self, called):
        lines = [json.dumps({"type": "user", "message": {"content": "시작"}})]
        if called:
            block = {"type": "tool_use", "id": "1", "name": "Skill", "input": {"skill": "nestlab-kickoff"}}
            lines.append(json.dumps({"type": "assistant", "message": {"content": [block]}}))
        path = Path(self.tmp.name) / "c.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    # kickoff 호출 없는 director의 도구 없는 응답은 block하고, 호출 뒤나 서브에이전트, 다른 역할은 통과한다.
    def test_kickoff_gate_stop(self):
        data = {"last_assistant_message": GOOD, "cwd": PLAIN, "agent_type": "rio"}
        self.assertEqual(hook.check({**data, "transcript_path": self.log(False)}), hook.REASON_KICKOFF)
        self.assertIsNone(hook.check({**data, "transcript_path": self.log(True)}))
        self.assertIsNone(hook.check({**data, "transcript_path": self.log(False), "agent_id": "a1"}))
        self.assertIsNone(hook.check({**data, "transcript_path": self.log(False), "agent_type": "buddy"}))


if __name__ == "__main__":
    unittest.main()
