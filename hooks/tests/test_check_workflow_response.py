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
import sub_session as hook_session

ANNOUNCE = "필요한 내용만 교체하겠습니다. 아직 파일은 변경하지 않았습니다."
EXCUSE = "맞습니다! 저는 요청한 문제를 해결하는 대신 상태만 보고했습니다."
GOOD = "수정했습니다.\n\n- `hooks/load_agent.py`: 세션 파일 추가\n- 테스트 통과"
CODE = "아래처럼 바꿉니다.\n\n```py\nx = 1\n```"


# 현재 승인 스킬의 필수 절에 구체적인 본문을 채운 응답이다.
def approval_message(link="", heading="# 승인 요청"):
    return heading + "\n\n" + link + "\n\n" + "\n\n".join(
        f"## {title}\n\n- hooks/a.py의 입력 검사 규칙을 바꾼다."
        for title in hook.template_titles("nestlab-approval") if title != "승인 요청")


PLAIN = tempfile.mkdtemp()  # log/state가 없는 폴더라 작업 흐름 검사를 건너뛴다.



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

    # `다음` 뒤 `부터(는)`와 같은 문장의 `하겠습니다`, `할게요`만 책임 회피로 보고 다른 `다음` 문장은 통과한다.
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
            self.assertIsNotNone(check_invalid_response.record(active))  # 재작성 응답도 감지하면 알림을 반환한다.
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
            self.assertNotIn("사건 문서에 기록했습니다", result["reason"])
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
APPROVAL = approval_message("확인할 문서: log/state/t.md", "## 작업 승인 필요")


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
        message = approval_message(heading="## 작업 승인 필요")
        for runner in ("claude", "codex"):
            for role in ("director", "assistant", "researcher", "worker", "reviewer", "documenter", "unknown"):
                with self.subTest(runner=runner, role=role):
                    reason = self.run_check(message, runner, agent_type=role)
                    self.assertEqual(reason, hook.REASON_APPROVAL_DOC if role == "director" else None)

    # `# 승인 요청` 제목도 승인 블록으로 인식해 director에게 경과 문서 링크를 요구하고, 링크가 있고 검토가 끝났으면 통과한다.
    def test_approval_request_title_recognized(self):
        message = approval_message()
        self.assertEqual(self.run_check(message, agent_type="director"), hook.REASON_APPROVAL_DOC)
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True)
        (folder / "status.md").write_text(STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertIsNone(self.run_check(message + "\n\n[경과 문서](log/task/t/status.md)", agent_type="director"))

    # 승인 요청이 가리킨 경과 문서의 요구사항 목록 번호가 명세 문서와 다르면 block하고, 같거나 명세 문서가 없으면 통과한다.
    def test_approval_request_number_match(self):
        from hooks.tests.test_sub_workflow import NEW_PLAN
        import sub_workflow
        block_of = NEW_PLAN[NEW_PLAN.index("### 1. 기능"):NEW_PLAN.index("## 작업 금지 목록")]
        spec = NEW_PLAN.replace(block_of, block_of + block_of.replace("1. 기능", "2. 기능") + block_of.replace("1. 기능", "3. 기능"))
        state = STATE.format(status="검토 완료").replace("### 검토 필요", "### 요구사항 목록\n\n{items}\n\n### 검토 필요")
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True)
        message = approval_message("[경과 문서](log/task/t/status.md)")

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

    # 승인 요청이 가리킨 경과 문서에 실행 승인을 기록하면 막히는 작업은 block하고, 전이 조건을 채운 작업은 통과한다.
    def test_approval_request_transition(self):
        from hooks.tests.test_sub_workflow import NEW_PLAN, NEW_RESEARCH, NEW_STATE, REFUTE, TASK
        folder = self.root / "log" / "task" / TASK
        folder.mkdir(parents=True)
        head, _, tail = NEW_STATE.rpartition("사용자 승인 상태: 승인")
        pending = head + "사용자 승인 상태: 승인 대기" + tail
        for name, body in (("spec", NEW_PLAN), ("research", NEW_RESEARCH), ("refute", REFUTE)):
            (folder / f"{name}.md").write_text(body, encoding="utf-8")
        message = approval_message(f"[경과 문서](log/task/{TASK}/status.md)")

        # 전이 조건을 채운 작업은 통과한다.
        (folder / "status.md").write_text(pending, encoding="utf-8")
        self.assertIsNone(self.run_check(message))

        # 검토 필요 기록이 빠진 작업은 전이 검사 사유로 block한다.
        broken = pending.replace("- 검토 1. 도구: 안을 정한다.", "- 검토 9. 다른: 안을 정한다.")
        (folder / "status.md").write_text(broken, encoding="utf-8")
        self.assertIn(hook.REASON_APPROVAL_TRANSITION.split("{")[0], self.run_check(message) or "")

    # 조사 원문이 빈 작업에서 조사 도구 호출 없는 해결안 제안은 block하고, 조사 원문이 있거나 안 표지가 없는 응답은 통과한다.
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

    # log/state가 없는 저장소는 작업 흐름 검사를 하지 않는지 확인한다.
    def test_repo_without_state_not_checked(self):
        empty = Path(self.tmp.name) / "plain"
        empty.mkdir()
        with unittest.mock.patch.object(hook, "project_root", return_value=empty):
            self.assertIsNone(self.run_check("어느 쪽으로 할까요?"))


STATUS = """# t

- 작업 ID: t
- 작업 상태: 미개시

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
               "input": {"questions": [{"header": "결정", "question": question,
                                        "options": [{"label": answer}] if options is None else options}]}}
        write = {"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": str(self.status), "content": "x"}}
        result = {"type": "tool_result", "tool_use_id": "q1", "content": "User has answered"}
        lines = [{"type": "user", "message": {"content": "시작"}},
                 {"type": "assistant", "message": {"content": [write, ask]}},
                 {"type": "user", "message": {"content": [result]}, "toolUseResult": {"answers": {question: answer}}}]
        return self.save(lines)

    # Codex rollout 파일을 만든다. 경과 문서를 고친 apply_patch 호출과 request_user_input 호출, answers 출력이 이어진다.
    def codex(self, question, selected, options=None, via_exec=False):
        item = lambda p: {"type": "response_item", "payload": p}
        ask = {"questions": [{"id": "q1", "header": "결정", "question": question,
                              "options": [{"label": selected[0]}] if options is None else options}]}
        patch = "*** Begin Patch\n*** Update File: log/task/t/status.md\n@@\n-x\n+y\n*** End Patch"
        write = (item({"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                       "input": "text(await tools.apply_patch(" + json.dumps(patch) + "))"}) if via_exec else
                 item({"type": "custom_tool_call", "name": "apply_patch", "call_id": "c1", "input": patch}))
        lines = [item({"type": "message", "role": "user", "content": [{"text": "시작"}]}), write,
                 item({"type": "function_call", "name": "request_user_input", "call_id": "c2", "arguments": json.dumps(ask)}),
                 item({"type": "function_call_output", "call_id": "c2",
                       "output": json.dumps({"answers": {"q1": {"answers": selected}}}, ensure_ascii=False)})]
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

    # 확정이 아닌 답, 요구사항 형식이 아닌 질문, 확정 단어가 든 직접 입력 답은 요구사항 목록을 바꾸지 않는다.
    def test_requirement_not_recorded(self):
        self.stop(self.claude("요구사항 2. 이름: 내용", "보류"), "claude")
        self.assertEqual(self.text(), STATUS.replace("\n", "\r\n"))
        self.stop(self.claude("어느 안으로 할까요?", "확정: 안 A"), "claude")
        self.assertIn("1. <요구사항 이름>", self.text())
        options = [{"label": "확정 (Recommended)"}, {"label": "보류"}]
        self.stop(self.claude("요구사항 2. 이름: 내용", "확정이라고 직접 입력", options), "claude")
        self.assertEqual(self.text(), STATUS.replace("\n", "\r\n"))

    # 검토 질문은 고른 안 이름을 확정에, 조사 문서 검토 보고의 안 제목을 근거 위치에 쓰고 안 블록 원문은 옮기지 않는다. 같은 번호는 한 항목만 남긴다.
    def test_review_recorded(self):
        self.status.with_name("research.md").write_text(
            "# 조사\n\n## 검토 보고\n\n### 검토 3. X\n\n| 안 | 기준 |\n| -- | -- |\n| A | 충족 |\n\n"
            "#### 안 1. A\n\n- 적용할 작업: a를 바꾼다\n\n#### 안 2. B\n\n- 적용할 작업: b를 바꾼다\n", encoding="utf-8")
        options = [{"label": "A", "description": "A로 한다"}, {"label": "B", "description": "B로 한다"}]
        question = "검토 3. X: 기록 방식을 정한다."
        for runner, transcript in (("claude", self.claude(question, "A", options)),
                                   ("codex", self.codex(question, ["A"], options))):
            with self.subTest(runner=runner):
                self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
                self.stop(transcript, runner)
                text = self.text().replace("\r\n", "\n")
                self.assertIn(f"- {question}\n  - 상태: 검토 완료\n", text)
                self.assertIn("  - 확정: A\n  - 근거 위치: 조사 문서 > 검토 보고 > 검토 3. X > 안 1. A\n", text)
                self.assertNotIn("a를 바꾼다", text)
                self.assertNotIn("- <항목>", text)
                self.assertIn("### 재검토 조건\n\n- 조건", text)

        # 같은 번호를 다시 물으면 항목이 바뀌고 하나만 남는다.
        self.stop(self.claude("검토 3. X: 다시 정한다.", "B", options), "claude")
        text = self.text().replace("\r\n", "\n")
        self.assertEqual(text.count("- 검토 3."), 1)
        self.assertIn("  - 확정: B\n  - 근거 위치: 조사 문서 > 검토 보고 > 검토 3. X > 안 2. B\n", text)

        # 직접 입력 답과 번호 접두가 없는 질문은 기록하지 않는다.
        self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
        self.stop(self.claude(question, "직접 입력한 답", options), "claude")
        self.stop(self.claude("어느 안으로 할까요?", "A", options), "claude")
        self.assertEqual(self.text(), STATUS.replace("\n", "\r\n"))

    # 비교표 행이 하나인 검토에서 안 이름 답은 검토 완료로, `폐기` 답은 폐기로 기록하고 `재조사` 답은 기록하지 않는다.
    def test_single_row_review_answers(self):
        research = self.status.with_name("research.md")
        report = ("# 조사\n\n## 검토 보고\n\n### 검토 3. X\n\n| 안 | 기준 |\n| -- | -- |\n| A | 충족 |\n\n"
                  "#### 안 1. A\n\n- 적용할 작업: a를 바꾼다\n\n## 끝\n")
        options = [{"label": "A"}, {"label": "폐기"}, {"label": "재조사"}]
        question = "검토 3. X: 기록 방식을 정한다."
        research.write_text(report, encoding="utf-8")
        self.stop(self.claude(question, "재조사", options), "claude")
        self.assertEqual(self.text(), STATUS.replace("\n", "\r\n"))
        self.stop(self.claude(question, "A", options), "claude")
        self.assertIn("  - 상태: 검토 완료\n", self.text().replace("\r\n", "\n"))
        self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
        self.stop(self.claude(question, "폐기", options), "claude")
        self.assertIn("- 검토 3. 폐기\n  - 상태: 폐기", self.text().replace("\r\n", "\n"))
        self.assertIn("### 검토 3. 폐기", research.read_text(encoding="utf-8"))

    # 확정 어휘가 섞인 자유 입력(Desktop 형식)과 `user_note:` 메모(CLI 형식)는 선택 답이 아니므로 기록하지 않는다.
    def test_free_input_not_recorded(self):
        options = [{"label": "확정"}, {"label": "보류"}]
        question = "요구사항 2. 이름: 내용 한 문장"
        for selected in (["확정하지 말고 다시 봐"], ["None of the above", "user_note: 확정 보류"]):
            self.stop(self.codex(question, selected, options), "codex")
            self.assertEqual(self.text(), STATUS.replace("\n", "\r\n"), selected)

    # 폐기 답은 경과 문서 검토 필요와 조사 문서 검토 보고를 `검토 <번호>. 폐기`만 남기도록 바꾼다.
    def test_review_dropped(self):
        research = self.status.with_name("research.md")
        research.write_text("# 조사\n\n## 검토 보고\n\n### 검토 5. X\n\n- 내용\n\n## 끝\n", encoding="utf-8")
        self.stop(self.claude("검토 5 폐기: 사유", "확정"), "claude")
        self.assertIn("- 검토 5. 폐기\n  - 상태: 폐기", self.text().replace("\r\n", "\n"))
        self.assertEqual(research.read_text(encoding="utf-8"), "# 조사\n\n## 검토 보고\n\n### 검토 5. 폐기\n\n## 끝\n")

    # 폐기만 있는 경과 문서의 승인 요청 응답은 통과하고, 답 없는 검토 보고가 있으면 block한다.
    def test_drop_and_unanswered_report_in_approval(self):
        message = approval_message("[경과 문서](log/task/t/status.md)")
        research = self.status.with_name("research.md")
        research.write_text("# 조사\n\n## 검토 보고\n\n### 검토 4. X\n\n- 내용\n", encoding="utf-8")
        check = lambda: hook.check({"last_assistant_message": message, "cwd": str(self.root), "agent_type": "director"}, "claude")
        self.assertIn("검토 4. X", check() or "")
        self.stop(self.claude("검토 4 폐기: 사유", "확정"), "claude")
        self.assertIsNone(check())

    # 선택 요청 응답은 아직 답을 받지 않은 검토 보고 이름이 본문에 있어야 통과한다.
    def test_choice_needs_unanswered_report(self):
        research = self.status.with_name("research.md")
        research.write_text("# 조사\n\n## 검토 보고\n\n### 검토 4. X\n\n- 내용\n", encoding="utf-8")
        self.status.write_text(STATUS.replace("미개시", "진행"), encoding="utf-8")
        check = lambda msg: hook.check({"last_assistant_message": msg, "cwd": str(self.root), "agent_type": "director"}, "claude")
        self.assertIn("판단 지점 밖", check("## 선택 요청\n\n- A\n- B") or "")
        self.assertIsNone(check("## 선택 요청\n\n검토 4. X\n- A\n- B"))
        self.stop(self.claude("검토 4. X: 안을 정한다.", "A", [{"label": "A"}]), "claude")
        self.assertIn("판단 지점 밖", check("## 선택 요청\n\n검토 4. X\n- A\n- B") or "")

    # Codex 질문 답 형식에서 답을 얻고, 답 dict가 아닌 출력은 빈 답이다.
    def test_codex_answers_format(self):
        output = json.dumps({"answers": {"q1": {"answers": ["확정 (Recommended)"]}}}, ensure_ascii=False)
        self.assertEqual(hook_session._codex_answer(output, "q1"), "확정 (Recommended)")
        self.assertEqual(hook_session._codex_answer('{"answers":{}}', "q1"), "")

    # exec 입력 안 apply_patch로 쓴 경과 문서를 찾고, 경과 문서가 아닌 파일은 None이다.
    def test_exec_written_status(self):
        question = "요구사항 2. 이름: 내용 한 문장"
        data = lambda t: {"cwd": str(self.root), "transcript_path": t}
        self.assertEqual(hook.written_status(data(self.codex(question, ["확정"], via_exec=True)), "codex"), self.status)
        other = self.codex(question, ["확정"], via_exec=True)
        Path(other).write_text(Path(other).read_text(encoding="utf-8").replace("log/task/t/status.md", "hooks/x.py"), encoding="utf-8")
        self.assertIsNone(hook.written_status(data(other), "codex"))

    # --record는 답을 한 번 기록하고 작업이 하나면 문서를 쓰지 않은 세션에서도 그 작업을 고른다.
    def test_record_mode(self):
        question = "요구사항 2. 이름: 내용 한 문장"
        for runner, transcript, response in (
                ("claude", self.claude(question, "확정"), {"answers": {question: "확정"}}),
                ("codex", self.codex(question, ["확정"]), json.dumps({"answers": {"q1": {"answers": ["확정"]}}}))):
            with self.subTest(runner=runner):
                self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
                name = "AskUserQuestion" if runner == "claude" else "request_user_input"
                tin = {"questions": [{"id": "q1", "question": question, "options": [{"label": "확정"}]}]}
                data = {"tool_name": name, "tool_input": tin, "tool_response": response, "cwd": str(self.root), "transcript_path": transcript}
                out = Stream()
                with unittest.mock.patch.object(sys, "argv", ["check_workflow_response.py", "--runner", runner, "--record"]), \
                     unittest.mock.patch.object(sys, "stdin", Stream(json.dumps(data))), \
                     unittest.mock.patch.object(sys, "stdout", out):
                    hook.main()
                self.assertEqual(out.getvalue(), "")
                first = self.text()
                self.assertIn("\r\n2. 이름: 내용 한 문장\r\n", first)
                self.stop(transcript, runner)
                self.assertEqual(self.text(), first)
        self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
        empty = self.save([{"type": "user", "message": {"content": "시작"}}])
        hook.record_tool({"tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": question, "options": [{"label": "확정"}]}]},
                          "tool_response": {"answers": {question: "확정"}}, "cwd": str(self.root), "transcript_path": empty}, "claude")
        self.assertIn("\r\n2. 이름: 내용 한 문장\r\n", self.text())

    # background_tasks가 남은 Stop에서도 답을 기록하고 block이 없으며, 서브에이전트 Stop은 기록하지 않는다.
    def test_background_tasks_records(self):
        question = "요구사항 2. 이름: 내용 한 문장"
        transcript = self.claude(question, "확정")
        data = {"last_assistant_message": GOOD, "cwd": str(self.root), "transcript_path": transcript}
        self.assertIsNone(hook.check({**data, "agent_id": "a1", "background_tasks": [{"id": "t1"}]}))
        self.assertEqual(self.text(), STATUS.replace("\n", "\r\n"))
        self.assertIsNone(hook.check({**data, "background_tasks": [{"id": "t1"}]}))
        self.assertIn("\r\n2. 이름: 내용 한 문장\r\n", self.text())

    # 작업 상태를 승인과 중단 유형, 완료 검사로 계산한다.
    def test_work_state(self):
        approved = STATUS.replace("## 작업 경과", "## 승인\n\n### 실행 승인 범위\n\n- 사용자 승인 상태: 승인\n\n## 작업 경과")
        self.assertEqual(hook.work_state(self.status, STATUS), "미개시")
        self.assertEqual(hook.work_state(self.status, approved), "진행")
        stopped = approved.replace("- 중단 유형: <충족 불가>", "- 중단 유형: 충족 불가")
        self.assertEqual(hook.work_state(self.status, stopped), "중단")
        self.status.write_text(approved, encoding="utf-8")
        hook.record_answers({"cwd": str(self.root), "transcript_path": self.claude("q?", "확정")}, "claude", [])
        self.assertIn("- 작업 상태: 진행", self.text())

    # 새 요구사항 확정 답은 승인을 승인 대기로 바꾸고, 같은 문장 재확정은 승인을 유지한다.
    def test_requirement_change_resets_approval(self):
        approved = STATUS.replace("## 작업 경과", "## 승인\n\n### 실행 승인 범위\n\n- 사용자 승인 상태: 승인\n\n## 작업 경과")
        question = "요구사항 2. 이름: 내용 한 문장"
        self.status.write_text(approved, encoding="utf-8")
        data = {"cwd": str(self.root), "transcript_path": self.claude(question, "확정")}
        hook.record_answers(data, "claude")
        self.assertIn("- 사용자 승인 상태: 승인 대기", self.text())
        self.status.write_text(self.text().replace("승인 대기", "승인"), encoding="utf-8")
        hook.record_answers(data, "claude")
        self.assertNotIn("승인 대기", self.text())

    # 요구사항 확정과 재확정 및 폐기는 기대 결과 칸을 갱신하고 다른 칸은 보존한다.
    def test_spec_cells_recorded(self):
        spec = self.status.with_name("spec.md")
        cell = "- 목표:\n  - 기존 목표\n- 기대 결과:\n  - <판정 기준>\n"
        spec.write_text(f"# t 명세\n\n## 요구사항 목록\n\n### 2. 이름\n\n{cell}\n### 3. 셋\n\n{cell}\n## 작업 계획\n", encoding="utf-8")
        record = lambda q, a="확정": self.stop(self.claude(q, a), "claude")
        text = lambda: spec.read_text(encoding="utf-8")

        # 요구사항 문장은 해당 번호의 기대 결과 칸에만 쓴다.
        record("요구사항 2. 이름: 문장 A")
        self.assertIn("- 기대 결과:\n  - 문장 A\n", text())
        self.assertEqual(text().count("- 목표:\n  - 기존 목표\n"), 2)

        # 같은 문장 재기록은 문서를 바꾸지 않고, 자유 입력 답은 명세를 바꾸지 않는다.
        before = text()
        record("요구사항 2. 이름: 문장 A")
        record("요구사항 2. 이름: 다른 문장", "직접 입력")
        self.assertEqual(text(), before)

        # 재확정하면 같은 번호의 기대 결과만 바꾼다.
        record("요구사항 2. 이름: 문장 B")
        self.assertIn("- 기대 결과:\n  - 문장 B\n", text())
        self.assertNotIn("문장 A", text())
        self.assertNotIn("재확정 필요", self.text())

        # 폐기는 해당 절만 바꾸고 다른 요구사항의 기대 결과를 보존한다.
        record("요구사항 3. 셋: 문장 C")
        record("요구사항 2 폐기: 사유")
        self.assertIn("\r\n요구사항 2. 폐기\r\n", self.text())
        self.assertIn("### 요구사항 2. 폐기\n\n- 기대 결과:\n  - 폐기\n", text())
        self.assertIn("- 기대 결과:\n  - 문장 C\n", text())
        self.assertEqual(hook.check_numbers(hook.Document(self.text()), hook.Document(text())), None)

    # Codex 답 형식에서도 요구사항 확정과 폐기가 기대 결과 칸에 기록된다.
    def test_spec_cells_recorded_codex(self):
        spec = self.status.with_name("spec.md")
        cell = "- 목표:\n  - 기존 목표\n- 기대 결과:\n  - <판정 기준>\n"
        spec.write_text(f"# t 명세\n\n## 요구사항 목록\n\n### 2. 이름\n\n{cell}\n### 3. 셋\n\n{cell}\n## 작업 계획\n", encoding="utf-8")
        self.stop(self.codex("요구사항 2. 이름: 문장 A", ["확정"]), "codex")
        self.stop(self.codex("요구사항 3. 셋: 문장 B", ["확정"]), "codex")
        self.stop(self.codex("요구사항 2 폐기: 사유", ["확정"]), "codex")
        text = spec.read_text(encoding="utf-8")
        self.assertIn("### 요구사항 2. 폐기\n\n- 기대 결과:\n  - 폐기\n", text)
        self.assertIn("- 기대 결과:\n  - 문장 B\n", text)
        self.assertEqual(text.count("- 목표:\n  - 기존 목표\n"), 1)

    # 명세 문서가 없으면 질문 답은 경과 문서에만 기록된다.
    def test_spec_missing_skipped(self):
        self.stop(self.claude("구현 1. 요약 (요구사항 2)", "확정"), "claude")
        self.assertFalse(self.status.with_name("spec.md").exists())

    # 중단 사유 절 끝에 미해결 줄을 덧붙이는지 확인한다.
    def test_unresolved_line_appended(self):
        hook.record_unresolved([self.status], "사유\n두 줄")
        text = self.text().replace("\r\n", "\n")
        self.assertTrue(text.endswith("- 중단 유형: <충족 불가>\n- 중단 판정 미해결: 사유 두 줄\n"))


class ApprovalForm(unittest.TestCase):
    # 도구 결과가 없어도 명시적 승인 입력 요구와 불완전한 승인 서식을 막는다.
    def test_explicit_requests_need_form(self):
        for message in ("승인으로 답해 주십시오.", "승인을 입력해 주세요.",
                        "다음 행동은 `승인` 입력을 보내면 실행합니다.",
                        "# 승인 요청\n\n작업을 시작합니다.",
                        approval_message().replace("## 완료 기준\n\n- hooks/a.py의 입력 검사 규칙을 바꾼다.", "## 완료 기준\n\n<!-- 작성 -->")):
            with self.subTest(message=message):
                reason, _ = hook.workflow_reason({"cwd": PLAIN}, message, hook.body_of(message), "codex")
                self.assertIn(hook.REASON_APPROVAL_FORM, reason or "")
                self.assertIn("누락 절:", reason)

    # 승인 사실과 예시 및 인용의 표현은 실제 입력 요구로 보지 않는다.
    def test_reports_and_examples_pass(self):
        for message in ("승인을 받았습니다.", "추가 승인이 필요 없습니다.", "검증 결과를 확인했습니다.",
                        "```text\n승인으로 답해 주십시오.\n```", "예시: 승인으로 답해 주십시오.",
                        "- e.g. `승인` 입력을 보내면 실행합니다.", "> 승인으로 답해 주십시오."):
            with self.subTest(message=message):
                self.assertFalse(hook.requests_approval(message))
                self.assertEqual(hook.workflow_reason({"cwd": PLAIN}, message, hook.body_of(message), "codex"), (None, []))

    # 실제 차단과 열람한 소스 및 과거 기록의 차단 문구는 응답 자체의 승인 요청과 구분한다.
    def test_tool_results_do_not_request_approval(self):
        reason = "사용자 마지막 메시지에 승인 명령이 없습니다. 작업 전 사용자 승인을 받으십시오."
        results = ("Command blocked by PreToolUse hook: " + reason,
                   f'소스 원문:\nREASON_APPROVAL = "{reason}"',
                   "과거 작업 기록:\n- 당시 차단 사유: " + reason)
        with tempfile.TemporaryDirectory() as folder:
            transcript = Path(folder) / "input.jsonl"
            for runner in ("claude", "codex"):
                for result in results:
                    with self.subTest(runner=runner, result=result):
                        if runner == "claude":
                            rows = [{"type": "user", "message": {"content": "실측 결과를 설명해 주세요."}},
                                    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "r1",
                                        "name": "Read", "input": {"file_path": "hooks/a.py"}}]}},
                                    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "r1", "content": result}]}}]
                        else:
                            item = lambda payload: {"type": "response_item", "payload": payload}
                            rows = [item({"type": "message", "role": "user", "content": [{"text": "실측 결과를 설명해 주세요."}]}),
                                    item({"type": "function_call", "call_id": "r1", "name": "exec_command",
                                          "arguments": json.dumps({"cmd": "Get-Content hooks/a.py"})}),
                                    item({"type": "function_call_output", "call_id": "r1", "output": result})]
                        transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
                        data = {"cwd": PLAIN, "transcript_path": str(transcript), "last_assistant_message": GOOD}
                        self.assertIsNone(hook.check(data, runner, record=False))
                        data["last_assistant_message"] = "승인으로 답해 주십시오."
                        self.assertIn(hook.REASON_APPROVAL_FORM, hook.check(data, runner, record=False) or "")
                        data["last_assistant_message"] = approval_message()
                        self.assertIsNone(hook.check(data, runner, record=False))


class UnfinishedStop(unittest.TestCase):
    # 승인된 작업의 완전한 문서 fixture에서 최신 검증만 미측정으로 둔다.
    def setUp(self):
        from hooks.tests.test_sub_workflow import NEW_PLAN, NEW_RESEARCH, NEW_STATE, REFUTE, TASK
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.folder = self.root / "log/task" / TASK
        self.folder.mkdir(parents=True)
        self.status = self.folder / "status.md"
        self.spec = self.folder / "spec.md"
        self.complete_state = NEW_STATE
        self.plan = NEW_PLAN
        self.status.write_text(NEW_STATE.replace("PASS:", "미측정:"), encoding="utf-8")
        for name, body in (("spec", NEW_PLAN), ("research", NEW_RESEARCH), ("refute", REFUTE)):
            (self.folder / f"{name}.md").write_text(body, encoding="utf-8")
        patcher = unittest.mock.patch.object(hook, "project_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    # 현재 턴에 연결 문서를 읽고 선택적으로 질문이나 도구 실패가 있는 실제 runner 기록을 만든다.
    def data(self, runner, message, user="승인된 작업을 계속 진행해 주세요.", question=None, denied=False, initial=False, **extra):
        calls = [] if initial else [("Read", {"file_path": str(self.spec)})]
        if question:
            calls.append(("AskUserQuestion" if runner == "claude" else "request_user_input", {"questions": [question]}))
        if runner == "claude":
            rows = [{"type": "user", "message": {"content": user}},
                    {"type": "assistant", "message": {"content": [
                        {"type": "tool_use", "id": f"c{i}", "name": name, "input": args} for i, (name, args) in enumerate(calls, start=int(initial))]}}]
            if denied:
                rows.append({"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "c0", "content": "Permission denied"}]}})
        else:
            item = lambda payload: {"type": "response_item", "payload": payload}
            rows = [item({"type": "message", "role": "user", "content": [{"text": user}]})]
            rows += [item({"type": "function_call", "call_id": f"c{i}", "name": name, "arguments": json.dumps(args)})
                     for i, (name, args) in enumerate(calls, start=int(initial))]
            if denied:
                rows.append(item({"type": "function_call_output", "call_id": "c0", "output": "Permission denied"}))
        transcript = self.root / f"{runner}.jsonl"
        transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        return {"cwd": str(self.root), "transcript_path": str(transcript), "last_assistant_message": message, **extra}

    # 실제 Stop 진입점의 출력 JSON 또는 무출력을 반환한다.
    def stop(self, data, runner):
        out = Stream()
        with unittest.mock.patch.object(sys, "argv", ["check_workflow_response.py", "--runner", runner]), \
             unittest.mock.patch.object(sys, "stdin", Stream(json.dumps(data))), \
             unittest.mock.patch.object(sys, "stdout", out):
            self.assertEqual(hook.main(), 0)
        return json.loads(out.getvalue()) if out.getvalue() else None

    # 부분 결과의 형식이나 길이 및 다음 작업 예고, 재작성 여부로 미완료 종료를 통과할 수 없다.
    def test_partial_reports_block_through_stop(self):
        for runner in ("claude", "codex"):
            for message in ("일부 구현했습니다.\n- hooks/a.py 수정", "검증은 실행하지 않았습니다.",
                            f"[결과물]({self.spec.as_posix()})", "다음 작업으로 검증하겠습니다.", "결과 설명입니다. " * 50):
                for active in (False, True):
                    with self.subTest(runner=runner, message=message, active=active):
                        result = self.stop(self.data(runner, message, stop_hook_active=active), runner)
                        self.assertEqual(result["decision"], "block")
                        self.assertIn("승인된 작업이 남아", result["reason"])
                        self.assertIn("status.md", result["reason"])

    # 최신 검증의 FAIL과 미실행 및 명세에 남은 재작업 및 재검증 요청도 종료를 막는다.
    def test_pending_evidence(self):
        for result in ("FAIL", "미측정"):
            self.status.write_text(self.complete_state.replace("PASS:", result + ":"), encoding="utf-8")
            self.assertIn("승인된 작업이 남아", hook.unfinished_reason(self.data("claude", GOOD), GOOD, "claude"))
        self.status.write_text(hook.splice(self.complete_state, "검증 결과", lambda _: "\n- 검증 미개시\n"), encoding="utf-8")
        self.assertIn("검증을 시작하지 않은", hook.unfinished_reason(self.data("claude", GOOD), GOOD, "claude"))
        for title in ("재작업 요청", "재검증 요청"):
            self.status.write_text(self.complete_state, encoding="utf-8")
            self.spec.write_text(self.plan.replace(f"## {title}\n없음\n", f"## {title}\n- hooks/a.py: 수정 (요구사항 1)\n"), encoding="utf-8")
            self.assertIn(title, hook.unfinished_reason(self.data("codex", GOOD), GOOD, "codex"))

    # 완료 근거, 일반 질문, 중단, 하위 역할 및 기존 background 처리는 Stop 종료를 허용한다.
    def test_allowed_endings_through_stop(self):
        for runner in ("claude", "codex"):
            for extra in ({"user": "검증 규칙의 뜻을 설명해 주세요."}, {"user": "작업을 중단해 주세요."},
                          {"agent_id": "worker"}, {"background_tasks": [{"id": "work"}]}):
                with self.subTest(runner=runner, extra=extra):
                    self.assertIsNone(self.stop(self.data(runner, GOOD, **extra), runner))
            self.status.write_text(self.complete_state, encoding="utf-8")
            self.assertIsNone(self.stop(self.data(runner, GOOD), runner))
            self.status.write_text(self.complete_state.replace("PASS:", "미측정:"), encoding="utf-8")

    # 질문 도구의 실제 미확정 검토 요청과 권한 차단에 근거한 사용자 조작 보고를 허용한다.
    def test_real_waits(self):
        for runner in ("claude", "codex"):
            self.status.write_text(self.complete_state.replace("PASS:", "미측정:").replace("상태: 검토 완료", "상태: 사용자 검토 필요"), encoding="utf-8")
            question = {"id": "review", "question": "검토 1. 도구: 방법을 정한다.", "options": [{"label": "안 1. A"}]}
            self.assertIsNone(self.stop(self.data(runner, GOOD, question=question), runner))
            requirement = {"id": "requirement", "question": "요구사항 2. 추가: 새 입력을 처리한다.", "options": [{"label": "확정"}]}
            self.assertIsNone(self.stop(self.data(runner, GOOD, question=requirement), runner))
            message = "권한 거부로 차단됐습니다. 영향: 검증 불가. 사용자 조작: 폴리님이 접근 권한을 설정해야 합니다. 근거: hooks/a.py:1"
            self.assertIsNone(self.stop(self.data(runner, message, denied=True), runner))
            self.assertIsNotNone(hook.unfinished_reason(self.data(runner, message), message, runner))
            self.assertIsNotNone(hook.unfinished_reason(self.data(runner, GOOD, denied=True), GOOD, runner))

    # 현재 요청과 관계없는 진행 작업은 검사하지 않는다.
    def test_unrelated_open_task_not_selected(self):
        data = self.data("claude", GOOD)
        transcript = Path(data["transcript_path"])
        transcript.write_text(json.dumps({"type": "user", "message": {"content": "다른 파일을 수정해 주세요."}}), encoding="utf-8")
        self.assertIsNone(hook.unfinished_reason(data, GOOD, "claude"))

    # 필수 절을 채운 승인 요청도 기존 승인 전이 검사를 통과해야 종료할 수 있다.
    def test_valid_approval_request_through_stop(self):
        message = approval_message(f"[경과 문서]({self.status.as_posix()})")
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.assertIsNone(self.stop(self.data(runner, message), runner))

    # 실제 질문 답과 뒤따르는 Stop 알림 및 선택적인 실제 재개 발화를 기록에 덧붙인다.
    def finish_question(self, data, runner, question, answer=None, failed=False, resume=False, empty_answers=False):
        transcript = Path(data["transcript_path"])
        rows = []
        notice = (f'<hook_prompt hook_run_id="stop-1">계속 실행하십시오. 승인 log/task/other/spec.md</hook_prompt>'
                  f'<hook_prompt hook_run_id="stop-2">승인된 작업을 계속하십시오.</hook_prompt>')
        if runner == "claude":
            if answer is not None or empty_answers:
                rows.append({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c1",
                    "content": "답변", "is_error": failed}]}, "toolUseResult": {"answers": {} if empty_answers else {question["question"]: answer}}})
            rows += [{"type": "user", "message": {"content": "Stop hook feedback:\n계속 실행하십시오. 승인"}}] * 2
            if resume:
                rows.append({"type": "user", "message": {"content": f"작업을 재개하고 계속 진행해 주세요. {self.spec.as_posix()}"}})
        else:
            item = lambda payload: {"type": "response_item", "payload": payload}
            if answer is not None or empty_answers:
                rows.append(item({"type": "function_call_output", "call_id": "c1", "is_error": failed,
                                  "output": json.dumps({"answers": {} if empty_answers else {question["id"]: {"answers": [answer]}}})}))
            rows += [item({"type": "message", "role": "user", "content": [{"type": "input_text", "text": notice}]})] * 2
            if resume:
                rows.append(item({"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": f"작업을 재개하고 계속 진행해 주세요. {self.spec.as_posix()}"}]}))
        with transcript.open("a", encoding="utf-8") as stream:
            stream.write("\n" + "\n".join(json.dumps(row) for row in rows))
        return data

    # 같은 작업의 실제 중단 선택은 반복 Stop에서도 종료를 허용하고 이후 실제 재개에는 다시 검사한다.
    def test_selected_stop_and_real_resume(self):
        self.status.write_text(self.complete_state.replace("PASS:", "FAIL:").replace("남은 문제: 없음", "남은 문제: 사용자 확인 필요"), encoding="utf-8")
        tag = self.folder.name[4:15]
        question = {"id": "next", "question": f"작업 진행: 다음 행동을 정한다. (작업 {tag})",
                    "options": [{"label": "작업 중단"}, {"label": "계속 진행"}]}
        for runner in ("claude", "codex"):
            for active in (False, True):
                with self.subTest(runner=runner, active=active):
                    original = self.status.read_bytes()
                    data = self.finish_question(self.data(runner, "작업을 멈추겠습니다.", question=question,
                                                          stop_hook_active=active), runner, question, "작업 중단")
                    self.assertIsNone(self.stop(data, runner))
                    self.assertIsNone(self.stop(data, runner))
                    self.assertEqual(self.status.read_bytes(), original)
                    self.finish_question(data, runner, question, resume=True)
                    result = self.stop(data, runner)
                    self.assertIn("승인된 작업이 남아", result["reason"])

    # 실패, 무응답, 자유 입력 인용과 다른 작업의 선택은 중단 근거가 아니다.
    def test_non_stop_answers_do_not_end_work(self):
        tag = self.folder.name[4:15]
        for runner in ("claude", "codex"):
            for answer, failed, question_tag in ((None, False, tag), ("작업 중단", True, tag),
                                                 ("예시로 작업 중단을 인용합니다", False, tag),
                                                 ("계속 진행", False, tag), ("작업 중단", False, "9999-999999")):
                with self.subTest(runner=runner, answer=answer, failed=failed, question_tag=question_tag):
                    question = {"id": "next", "question": f"작업 진행: 다음 행동을 정한다. (작업 {question_tag})",
                                "options": [{"label": "작업 중단"}, {"label": "계속 진행"}]}
                    data = self.finish_question(self.data(runner, "에이전트 설명에 중단 표현이 있습니다.", question=question,
                                                          stop_hook_active=True), runner, question, answer, failed)
                    self.assertIn("승인된 작업이 남아", self.stop(data, runner)["reason"])

    # 설명 질문 뒤 반복된 hook 알림은 실제 사용자 요청이나 승인으로 취급하지 않는다.
    def test_explanation_with_repeated_hook_prompts(self):
        question = {"id": "next", "question": "설명 질문", "options": [{"label": "확인"}]}
        data = self.finish_question(self.data("codex", GOOD, user="검증 규칙의 뜻을 설명해 주세요."), "codex", question)
        self.assertEqual(hook_session.turn(hook_session.read_session(data["transcript_path"]))[0], "검증 규칙의 뜻을 설명해 주세요.")
        self.assertIsNone(self.stop(data, "codex"))

    # 설명과 인용 속 계속 표현은 재개 지시가 아니며 실제 계속 명령은 미완료 검사를 유지한다.
    def test_continue_explanation_and_command_are_distinct(self):
        question = {"id": "next", "question": "설명 질문", "options": [{"label": "확인"}]}
        descriptions = ("조사와 논의를 계속하면서 반복하는 과정이 문안의 어디에 있나요?",
                        "계속 진행해 주세요라는 표현은 무엇을 뜻하나요?",
                        "‘계속 진행해 주세요’라는 문장을 설명해 주세요.",
                        "예시를 설명해 주세요.\n```text\n계속\n```",
                        "인용한 표현을 설명해 주세요.\n> 계속")
        for runner in ("claude", "codex"):
            for user in descriptions:
                with self.subTest(runner=runner, user=user):
                    data = self.finish_question(self.data(runner, GOOD, user=user), runner, question)
                    self.assertIsNone(self.stop(data, runner))
            for user in ("계속", "계속 진행해 주세요.", "계속해 주세요", "계속 진행해줘"):
                for active in (False, True):
                    with self.subTest(runner=runner, user=user, active=active):
                        data = self.finish_question(self.data(runner, GOOD, user=user, stop_hook_active=active), runner, question)
                        self.assertIn("승인된 작업이 남아", self.stop(data, runner)["reason"])

    # 한 작업의 중단 선택으로 같은 요청에 연결된 다른 미완료 작업을 종료하지 않는다.
    def test_stop_does_not_finish_another_task(self):
        other = self.root / "log/task/20261105-060000-other"
        other.mkdir(parents=True)
        other_status = other / "status.md"
        other_status.write_text(self.status.read_text(encoding="utf-8"), encoding="utf-8")
        (other / "spec.md").write_text(self.plan, encoding="utf-8")
        original = other_status.read_bytes()
        question = {"id": "next", "question": f"작업 진행: 다음 행동을 정한다. (작업 {self.folder.name[4:15]})",
                    "options": [{"label": "작업 중단"}, {"label": "계속 진행"}]}
        for runner in ("claude", "codex"):
            user = f"두 작업을 계속 진행해 주세요. {self.spec.as_posix()} {(other / 'spec.md').as_posix()}"
            data = self.finish_question(self.data(runner, GOOD, user=user, question=question), runner, question, "작업 중단")
            self.assertIn("20261105-060000-other/status.md", self.stop(data, runner)["reason"])
            self.assertEqual(other_status.read_bytes(), original)

    # 빈 answers와 실패 뒤 확인 안내가 없으면 반복 Stop에서도 같은 문제 확인 질문을 되돌린다.
    def test_problem_question_requires_waiting_guidance(self):
        question = {"id": "scope", "question": f"문제 정의 확인: 입력 오류와 수정 범위 (작업 {self.folder.name[4:15]})",
                    "options": [{"label": "확인"}, {"label": "수정 요청"}, {"label": "취소"}]}
        guidance = "문제 정의와 수정 범위는 미확정입니다. 질문의 선택지로 답하거나 수정할 내용을 입력해 주세요."
        for runner in ("claude", "codex"):
            for failed in (False, True):
                for active in (False, True):
                    with self.subTest(runner=runner, failed=failed, active=active):
                        data = self.finish_question(self.data(runner, GOOD, question=question, stop_hook_active=active),
                                                    runner, question, "확인" if failed else None, failed=failed, empty_answers=not failed)
                        self.assertIn(hook.REASON_PROBLEM_WAIT.split(". ")[0], self.stop(data, runner)["reason"])
                        data["last_assistant_message"] = "확인 대기입니다."
                        self.assertIn(hook.REASON_PROBLEM_WAIT.split(". ")[0], self.stop(data, runner)["reason"])
                        data["last_assistant_message"] = guidance
                        self.assertIsNone(self.stop(data, runner))
                        if not active:
                            data["last_assistant_message"] = "문제 정의와 범위를 확인하려면 승인으로 답해 주십시오. 질문의 선택지에 답을 입력해 주세요."
                            self.assertIn(hook.REASON_APPROVAL_FORM, self.stop(data, runner)["reason"])

    # 답과 대기 단어가 있는 범위 초안 보고도 실제 답변 요청이나 입력 방법이 없으면 차단한다.
    def test_problem_wait_report_needs_actual_reply_guidance(self):
        question = {"id": "scope", "question": "문제 정의 확인: 입력 검사의 범위 초안 (작업 1105-092020)",
                    "options": [{"label": "확인"}, {"label": "수정 요청"}]}
        reports = (
            "문제 정의와 수정 범위의 확인을 기다리고 있습니다.\n- 범위 초안: 입력 검사의 오류를 고칩니다.\n"
            "질문 도구의 답이 비어 범위는 미확정입니다. 확인 후 문서를 작성하겠습니다.",
            "문제 정의와 범위에 대한 답을 기다립니다. 질문 결과가 비어 범위를 미확정으로 두었습니다.",
            "문제 정의와 범위가 미확정이며 답이 비어 있습니다. 확인을 기다립니다. 기존 문서는 보존해 주세요.",
            "질문의 선택지에 답이 비어 있습니다. 선택을 기다리고 있습니다.",
            "수정할 내용에 대한 답이 비어 있습니다. 확인을 기다립니다.")
        guidance = (
            "문제 정의와 범위 초안이 맞는지 질문에 답해 주세요.",
            "문제 정의와 수정 범위는 미확정입니다. 질문의 선택지로 답하면 됩니다.",
            "문제 정의와 수정 범위에 대한 답을 주십시오.",
            "질문의 선택지에서 하나를 골라 주세요.",
            "질문의 선택지로 답하면 됩니다.",
            "수정 범위를 직접 입력해 주세요.",
            "수정할 내용을 답해 주시면 됩니다.",
            "선택지를 제출하거나 채팅으로 수정할 내용을 답하시면 됩니다.",
            "입력 검사의 문제 정의와 범위에 동의하는지, 수정할 내용을 알려 주세요.")
        for runner in ("claude", "codex"):
            for failed in (False, True):
                for active in (False, True):
                    with self.subTest(runner=runner, failed=failed, active=active):
                        data = self.finish_question(self.data(runner, GOOD, user="새 작업의 문제와 범위를 확인해 주세요.",
                                                             question=question, initial=True, stop_hook_active=active),
                                                    runner, question, "확인" if failed else None, failed=failed, empty_answers=not failed)
                        for message in reports:
                            data["last_assistant_message"] = message
                            self.assertIn(hook.REASON_PROBLEM_WAIT.split(". ")[0], self.stop(data, runner)["reason"])
                        for message in guidance:
                            data["last_assistant_message"] = message
                            self.assertIsNone(self.stop(data, runner))

    # 실제 답 완료와 취소 및 다른 작업의 문제 질문은 미응답으로 오판하지 않는다.
    def test_problem_question_answered_cancelled_or_unrelated(self):
        for runner in ("claude", "codex"):
            question = {"id": "scope", "question": f"문제 정의 확인: 입력 오류와 수정 범위 (작업 {self.folder.name[4:15]})",
                        "options": [{"label": "확인"}, {"label": "수정 요청"}, {"label": "작업 중단"}, {"label": "취소"}]}
            for answer in ("확인", "수정 요청", "작업 중단", "취소"):
                with self.subTest(runner=runner, answer=answer):
                    data = self.finish_question(self.data(runner, GOOD, user="문제 정의와 범위를 설명해 주세요.", question=question), runner, question, answer)
                    self.assertIsNone(self.stop(data, runner))
            other = self.root / "log/task/20261105-091010-other/status.md"
            other.parent.mkdir(parents=True, exist_ok=True)
            other.write_text("# 다른 작업\n", encoding="utf-8")
            question["question"] = "문제 정의 확인: 다른 작업의 범위 (작업 1105-091010)"
            data = self.finish_question(self.data(runner, GOOD, user="문제 정의와 범위를 설명해 주세요.", question=question), runner, question, empty_answers=True)
            self.assertIsNone(self.stop(data, runner))

    # 문서 생성 전 질문은 자기 태그로 검사하며 다른 기존 작업의 문서를 연결하지 않는다.
    def test_initial_problem_question_without_task_documents(self):
        question = {"id": "scope", "question": "문제 정의 확인: 새 입력 오류와 수정 범위 (작업 1105-090001)",
                    "options": [{"label": "확인"}, {"label": "수정 요청"}, {"label": "취소"}, {"label": "작업 중단"}]}
        guidance = "새 입력 오류의 문제 정의와 수정 범위는 미확정입니다. 질문의 선택지로 답하거나 수정할 내용을 입력해 주세요."
        for runner in ("claude", "codex"):
            original = {p: p.read_bytes() for p in self.folder.iterdir() if p.is_file()}
            for initial in (False, True):
                for failed in (False, True):
                    for active in (False, True):
                        with self.subTest(runner=runner, initial=initial, failed=failed, active=active):
                            data = self.finish_question(self.data(runner, GOOD, user="새 기능의 문제와 범위를 확인해 주세요.",
                                                                 question=question, initial=initial, stop_hook_active=active),
                                                        runner, question, "확인" if failed else None, failed=failed, empty_answers=not failed)
                            reason = self.stop(data, runner)["reason"]
                            self.assertIn("작업 1105-090001 (경과 문서 생성 전)", reason)
                            self.assertNotIn(self.folder.name, reason)
                            data["last_assistant_message"] = guidance
                            self.assertIsNone(self.stop(data, runner))
            for answer in ("확인", "수정 요청", "취소", "작업 중단"):
                with self.subTest(runner=runner, answer=answer):
                    data = self.finish_question(self.data(runner, GOOD, user="새 기능의 문제와 범위를 확인해 주세요.",
                                                         question=question, initial=True), runner, question, answer)
                    self.assertIsNone(self.stop(data, runner))
                    for path, body in original.items():
                        self.assertEqual(path.read_bytes(), body)
            other = {**question, "question": f"문제 정의 확인: 다른 기존 작업 (작업 {self.folder.name[4:15]})"}
            data = self.finish_question(self.data(runner, GOOD, user="새 기능의 문제와 범위를 확인해 주세요.",
                                                 question=other, initial=True), runner, other, empty_answers=True)
            self.assertIsNone(self.stop(data, runner))

    # 같은 내용의 질문을 다시 호출했으면 이전 호출의 답으로 마지막 빈 결과를 통과시키지 않는다.
    def test_problem_question_checks_last_call_id(self):
        question = {"id": "scope", "question": f"문제 정의 확인: 입력 오류와 수정 범위 (작업 {self.folder.name[4:15]})",
                    "options": [{"label": "확인"}, {"label": "수정 요청"}]}
        for runner in ("claude", "codex"):
            data = self.finish_question(self.data(runner, GOOD, user="문제 정의와 범위를 설명해 주세요.", question=question), runner, question, "확인")
            self.assertIsNone(self.stop(data, runner))
            if runner == "claude":
                row = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c2", "name": "AskUserQuestion", "input": {"questions": [question]}}]}}
            else:
                row = {"type": "response_item", "payload": {"type": "function_call", "call_id": "c2", "name": "request_user_input", "arguments": json.dumps({"questions": [question]})}}
            with Path(data["transcript_path"]).open("a", encoding="utf-8") as stream:
                stream.write("\n" + json.dumps(row))
            self.assertIn(hook.REASON_PROBLEM_WAIT.split(". ")[0], self.stop(data, runner)["reason"])


class HookPromptParsing(unittest.TestCase):
    # hook 요소가 없는 일반 사용자 발화의 앞뒤 공백과 빈 줄을 원문 그대로 읽는다.
    def test_regular_user_whitespace_preserved(self):
        original = "\n\n  실제 사용자 원문입니다.  \n\n "
        row = {"type": "response_item", "payload": {"type": "message", "role": "user",
               "content": [{"type": "input_text", "text": original}]}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.jsonl"
            path.write_text(json.dumps(row), encoding="utf-8")
            entries = hook_session.read_session(str(path))
            self.assertEqual(entries[0]["text"], original)
            self.assertEqual(hook_session.turn(entries)[0], original)
            self.assertEqual(hook_session.user_messages(entries), [original])

    # 독립 알림은 제외하고 여러 알림 사이의 실제 본문과 XML 예시 및 실제 승인은 보존한다.
    def test_codex_hook_elements_preserve_real_user_text(self):
        first = '<hook_prompt hook_run_id="one">계속 진행 승인 log/task/other/spec.md</hook_prompt>'
        second = "<hook_prompt hook_run_id='two'>계속 실행하십시오.</hook_prompt>"
        item = lambda text: {"type": "response_item", "payload": {"type": "message", "role": "user",
                              "content": [{"type": "input_text", "text": text}]}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.jsonl"
            rows = [item("원래 설명 질문입니다."), item(first + second)]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            entries = hook_session.read_session(str(path))
            self.assertEqual(hook_session.turn(entries)[0], "원래 설명 질문입니다.")
            self.assertEqual(hook_session.user_messages(entries), ["원래 설명 질문입니다."])
            rows += [item(first + "\n실제 사용자 본문입니다.\n" + second), item("<hook_prompt>사용자 XML 예시</hook_prompt>"), item(first + "\n승인\n" + second)]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            entries = hook_session.read_session(str(path))
            self.assertEqual(hook_session.user_messages(entries), ["원래 설명 질문입니다.", "실제 사용자 본문입니다.",
                                                                  "<hook_prompt>사용자 XML 예시</hook_prompt>", "승인"])
            self.assertEqual(hook_session.turn(entries)[0], "승인")


if __name__ == "__main__":
    unittest.main()
