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

- 검토 1. 구조
  - 상태: {status}

## 작업 기록

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
        self.assertIn("검토 1. 구조", self.run_check(APPROVAL))
        for status in ("검토 완료", "사후실측 필요"):
            (self.state / "t.md").write_text(STATE.format(status=status), encoding="utf-8")
            self.assertIsNone(self.run_check(APPROVAL), status)
        # 템플릿 주석에 없는 값은 앞부분이 같아도 완전 일치가 아니라서 막는다.
        for status in ("사후검증 필요", "검토 완료(일부)"):
            (self.state / "t.md").write_text(STATE.format(status=status), encoding="utf-8")
            self.assertIn("검토 1. 구조", self.run_check(APPROVAL), status)

    # 두 runner에서 director에게만 승인 요청의 상태 문서 경로를 요구하는지 확인한다.
    def test_approval_path_required_for_director_only(self):
        message = "## 작업 승인 필요\n\n- `hooks/a.py`"
        for runner in ("claude", "codex"):
            for role in ("director", "assistant", "researcher", "worker", "reviewer", "documenter", "unknown"):
                with self.subTest(runner=runner, role=role):
                    reason = self.run_check(message, runner, agent_type=role)
                    self.assertEqual(reason, hook.REASON_APPROVAL_DOC if role == "director" else None)

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

    # log/task 경과 문서도 검토 필요 항목을 검사하고 결정 기록 절에 미해결을 기록하는지 확인한다.
    def test_task_folder_state_doc(self):
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True)
        doc = folder / "status.md"
        approval = APPROVAL.replace("log/state/t.md", "log/task/t/status.md")
        doc.write_text(STATE.format(status="미검토").replace("## 작업 기록", "## 결정 기록"), encoding="utf-8")
        self.assertIn("검토 1. 구조", self.run_check(approval))
        self.assertIsNone(self.run_check(approval, stop_hook_active=True))
        text = doc.read_text(encoding="utf-8")
        self.assertLess(text.index("착수"), text.index(hook.UNRESOLVED))

    # 판단 지점이 아닌 선택 요청 블록이 block되는지 확인한다.
    def test_choice_requires_decision_point(self):
        doc = self.state / "t.md"
        message = "## 선택 필요\n\n검토 1. 구조\n- A\n- B"
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


if __name__ == "__main__":
    unittest.main()
