"""check_workflow_response.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOKS))

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
import unittest.mock

import check_workflow_response as hook
import check_invalid_response
import sub_session as hook_session
import sub_completion
import sub_form
import sub_record
import sub_approval
import sub_docs

# 응답 미흡 판정이 사건 문서를 기록하므로 이 모듈의 모든 시험은 저장소 밖 임시 폴더에 쓰고 분석 세션을 띄우지 않는다.
_INCIDENTS = tempfile.TemporaryDirectory()
check_invalid_response.INCIDENT_ROOT = Path(_INCIDENTS.name)
check_invalid_response.launch_analysis = lambda *args, **kwargs: None


# 실제 Codex harness metadata를 가진 Stop 입력이다. 원문 태그만 있는 사용자 인용과 구분한다.
def codex_hook(text, turn_id="turn-fixture"):
    return {"type": "response_item", "metadata": {"client_authored": False}, "payload": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}],
        "internal_chat_message_metadata_passthrough": {"turn_id": turn_id, "create_time": "2026-11-10T08:00:00Z",
            "content_item_kinds": ["unknown"], "content_item_metadata": [{"provenance": {"type": "harness", "hook": True}}]}}}


# project_root를 불러 쓰는 모든 모듈의 참조를 같은 폴더로 바꾸는 patch이다. start/stop 또는 with로 쓴다.
def patch_root(root):
    stack = contextlib.ExitStack()
    for module in (hook, sub_completion, sub_form, sub_record):
        if hasattr(module, "project_root"):
            stack.enter_context(unittest.mock.patch.object(module, "project_root", return_value=root))
    stack.start = lambda: None
    stack.stop = stack.close
    return stack


ANNOUNCE = "필요한 내용만 교체하겠습니다. 아직 파일은 변경하지 않았습니다."
EXCUSE = "맞습니다! 저는 요청한 문제를 해결하는 대신 상태만 보고했습니다."
GOOD = "수정했습니다.\n\n- `hooks/load_agent.py`: 세션 파일 추가\n- 테스트 통과"
CODE = "아래처럼 변경합니다.\n\n```py\nx = 1\n```"


# 현재 승인 스킬의 필수 절에 구체적인 본문을 채운 응답이다.
def approval_message(link="", heading="# 구현 승인 요청"):
    return heading + "\n\n" + link + """

## 관련 요구사항

- 요구사항 1. 기능: 입력 오류를 거부한다.
  - 이번 변경: 입력 검사를 추가한다.

## 사전 작업

해당 없음

## 구현 요약

hooks/a.py에 입력 검사를 추가하고 입력 처리 호출자가 오류 반환을 받게 한다.

## 저장소 영향

- hook: 입력 처리 hook이 잘못된 입력을 거부한다.
- 스킬: 없음
- 다른 역할: 없음
- Codex 경로: 없음

## 명세 문서

- [입력 검사 명세](spec.md)
"""


PLAIN = tempfile.mkdtemp()  # log 폴더가 없는 폴더라 작업 흐름 검사를 건너뛴다.



class Stream(io.StringIO):
    # 표준 입출력 인코딩 설정 호출을 받아 넘긴다.
    def reconfigure(self, **kwargs):
        pass


# hook 검사 결과(block 사유 또는 None)를 반환하는 보조 함수이다.
def block(msg, **extra):
    return hook.check({"last_assistant_message": msg, "cwd": PLAIN, "agent_type": "director", **extra})


class Check(unittest.TestCase):
    def setUp(self):
        role = unittest.mock.patch.dict(os.environ, {"NESTLAB_AGENT": "director"})
        role.start()
        self.addCleanup(role.stop)
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
        self.assertIsNotNone(block("nestlab-implement-doc이 호출됐습니다."))
        self.assertIsNone(block("`claude_baseline.jsonl` 3행에서 호출됐습니다."))
        self.assertIsNone(block("호출됐을 것입니다 [추론]."))

    # 출처가 응답 어디에 있어도 통과하는지 확인한다.
    def test_source_anywhere_in_response_passes(self):
        self.assertIsNone(block("nestlab-implement-doc이 호출됐습니다. 근거는 `claude_baseline.jsonl` 3행입니다."))
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
        # `확인하지 않` 문장은 출처 판정이 아니라 응답 미흡 판정이 다룬다. (요구사항 17)
        self.assertEqual(block("아직 확인하지 않았습니다."), hook.REASON_EMPTY)

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

    # 입력 특성의 관형형은 작업 불가 주장과 구분하고 실제 불가 판정과 확인 주장 검사는 유지한다.
    def test_input_properties_are_not_inaccessible_claims(self):
        for text in ("변환 불가능한 문자열을 입력 오류로 처리한다.", "읽을 수 없는 문자열을 오류로 처리한다.",
                     "문제 정의 확인: 변환 불가능한 문자열의 오류 처리 범위 (작업 1110-160000)"):
            self.assertEqual(hook.unsourced_claims(text, text), [])
        for text in ("현재 환경에서 변환은 불가능합니다.", "현재 실행은 불가능한 상태입니다.", "이 파일을 읽을 수 없습니다.",
                     "입력을 확인했습니다.", "동작을 측정했습니다."):
            self.assertTrue(hook.unsourced_claims(text, text))

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

    # 응답 미흡은 결과물과 길이에 관계없이 포기, 다짐, 확인하지 않은 내용 표현으로 판정하고 [미조사] 줄과 사후 모델 호출 실측 줄은 제외한다. (요구사항 17)
    def test_deficiency_phrases(self):
        with tempfile.TemporaryDirectory() as tmp, \
             unittest.mock.patch.object(check_invalid_response, "INCIDENT_ROOT", Path(tmp)), \
             unittest.mock.patch.object(check_invalid_response, "launch_analysis"):
            for text in ("- 항목 하나\n- 항목 둘\n\n앞으로 이렇게 하겠습니다.", "이 동작은 미확인입니다.", "폴리님이 하실 일은 없습니다.",
                         "수정 결과를 `a.py`에 반영했습니다.\n\n여기서 마치겠습니다."):
                self.assertEqual(block(text, session_id="abcdef123456"), hook.REASON_EMPTY, text)
            self.assertEqual(len(list((Path(tmp) / "log" / "incident").glob("*.md"))), 1)
            self.assertIsNone(block("- 항목 [미조사]\n  - 사유: 범위 밖", session_id="abcdef123456"))
            self.assertIsNone(block("- 사후 모델 호출 실측: 확인하지 않은 동작, reviewer 세션, 구현 뒤", session_id="abcdef123456"))

    # 응답 미흡 표현은 block하고 사건 문서에 기록하며 분석 세션을 띄우고, record가 거짓이면 판정만 한다.
    def test_shift_blocked_and_recorded(self):
        data = {"last_assistant_message": "이미 충분합니다.\n\n- `a.py` 수정", "session_id": "abcdef123456", "cwd": PLAIN}
        with tempfile.TemporaryDirectory() as tmp, \
             unittest.mock.patch.object(check_invalid_response, "INCIDENT_ROOT", Path(tmp)), \
             unittest.mock.patch.object(check_invalid_response, "launch_analysis") as launch:
            self.assertEqual(hook.check(data), hook.REASON_EMPTY)
            text = next((Path(tmp) / "log" / "incident").glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("감지 표현: 이미 충분", text)
            launch.assert_called_once()
            self.assertIsNone(check_invalid_response.record(data))  # 사건 감지 표현이 아니다.
            self.assertIsNotNone(hook.check(data, record=False))
            self.assertEqual(launch.call_count, 1)
            self.assertIsNone(hook.check({**data, "stop_hook_active": True}))  # 재작성 뒤에는 한 번만 block한다.
            self.assertEqual(launch.call_count, 1)
            for _ in range(6):  # 감지 기록 8건이 되면 다음 감지는 기록만 하고 통과한다.
                hook.check(data)
            self.assertIsNone(hook.check(data))
            self.assertIsNone(hook.check(data, record=False))  # 화면 교체 판정도 같은 한도를 쓴다.

    # 책임 회피 block 출력에 systemMessage와 사건 문서 경로가 있고 REASON_EMPTY 문구가 쓰이는지 확인한다.
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
            self.assertIn(hook.REASON_EMPTY, result["reason"])
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
APPROVAL = approval_message("확인할 문서: log/task/t/status.md", "## 작업 승인 필요")


class Workflow(unittest.TestCase):
    # 임시 저장소와 상태 문서를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "log" / "task" / "t"
        self.state.mkdir(parents=True)
        self.patch = patch_root(self.root)
        self.patch.start()
        # 이 클래스는 기존 경과·전이 검사를 분리해 검사하며 새 명세 대조는 ApprovalFields에서 검사한다.
        parser = sub_form.approval_form_gap
        form = unittest.mock.patch.object(hook, "approval_form_gap", side_effect=lambda body, plans=None: parser(body))
        form.start()
        self.addCleanup(form.stop)

    # patch와 임시 저장소를 정리한다.
    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    # 임시 저장소를 cwd로 hook 검사를 실행하는 보조 함수이다.
    def run_check(self, msg, runner="claude", **extra):
        return hook.check({"last_assistant_message": msg, "agent_type": "director", "cwd": str(self.root), **extra}, runner)

    # 검토 필요 항목이 끝나지 않으면 승인 요청이 block되고 끝나면 통과하는지 확인한다.
    def test_approval_needs_reviewed_items(self):
        (self.state / "status.md").write_text(STATE.format(status="미검토"), encoding="utf-8")
        self.assertIn("조사 1. 구조", self.run_check(APPROVAL))
        (self.state / "status.md").write_text(STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertIsNone(self.run_check(APPROVAL))
        # 템플릿 주석에 없는 값은 앞부분이 같아도 완전 일치가 아니라서 차단한다.
        for status in ("사후실측 필요", "사후검증 필요", "검토 완료(일부)"):
            (self.state / "status.md").write_text(STATE.format(status=status), encoding="utf-8")
            self.assertIn("조사 1. 구조", self.run_check(APPROVAL), status)

    # 시점이 사전인 실측 필요 항목은 실측 완료일 때만 승인 요청이 통과하고, 사후 항목은 막지 않는다.
    def test_approval_needs_measured_items(self):
        measure = "\n## 실측 필요\n\n- 재현\n  - 시점: {when}\n  - 상태: {status}\n"
        for when, status, blocked in (("사전", "실측 대기", True), ("사전", "실측 완료", False), ("사후", "실측 대기", False)):
            text = STATE.format(status="검토 완료") + measure.format(when=when, status=status)
            (self.state / "status.md").write_text(text, encoding="utf-8")
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

    # `# 구현 승인 요청` 제목도 승인 블록으로 인식해 director에게 경과 문서 링크를 요구하고, 링크가 있고 검토가 끝났으면 통과한다.
    def test_approval_request_title_recognized(self):
        message = approval_message()
        self.assertEqual(self.run_check(message, agent_type="director"), hook.REASON_APPROVAL_DOC)
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "status.md").write_text(STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertIsNone(self.run_check(message + "\n\n[경과 문서](log/task/t/status.md)", agent_type="director"))

    # 구현 승인 요청이 가리킨 경과 문서의 요구사항 목록 번호가 명세 문서와 다르면 block하고, 같거나 명세 문서가 없으면 통과한다.
    def test_approval_request_number_match(self):
        from hooks.tests.test_sub_workflow import NEW_PLAN
        import sub_document
        import sub_gate
        block_of = NEW_PLAN[NEW_PLAN.index("### 1. 기능"):NEW_PLAN.index("## 작업 금지 목록")]
        spec = NEW_PLAN.replace(block_of, block_of + block_of.replace("1. 기능", "2. 기능") + block_of.replace("1. 기능", "3. 기능"))
        state = STATE.format(status="검토 완료").replace("### 검토 필요", "### 요구사항 목록\n\n{items}\n\n### 검토 필요")
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True, exist_ok=True)
        message = approval_message("[경과 문서](log/task/t/status.md)")

        # 목록 1, 3과 명세 1, 2, 3은 차단한다.
        (folder / "spec.md").write_text(spec, encoding="utf-8")
        (folder / "status.md").write_text(state.replace("{items}", "1. 가: 나\n3. 다: 라"), encoding="utf-8")
        self.assertIn(sub_document.R_NUM_MISMATCH, self.run_check(message) or "")

        # 번호가 같거나 명세 문서가 없으면 통과한다.
        (folder / "status.md").write_text(state.replace("{items}", "1. 가: 나\n2. 다: 라\n3. 마: 바"), encoding="utf-8")
        self.assertIsNone(self.run_check(message))
        (folder / "spec.md").unlink()
        (folder / "status.md").write_text(state.replace("{items}", "1. 가: 나\n3. 다: 라"), encoding="utf-8")
        self.assertIsNone(self.run_check(message))

    # 구현 승인 요청이 가리킨 경과 문서에 실행 승인을 기록하면 막히는 작업은 block하고, 전이 조건을 채운 작업은 통과한다.
    def test_approval_request_transition(self):
        from hooks.tests.test_sub_workflow import NEW_PLAN, NEW_RESEARCH, NEW_STATE, REFUTE, TASK
        folder = self.root / "log" / "task" / TASK
        folder.mkdir(parents=True, exist_ok=True)
        head, _, tail = NEW_STATE.rpartition("사용자 승인 상태: 승인")
        pending = head + "사용자 승인 상태: 승인 대기" + tail
        for name, body in (("spec", NEW_PLAN), ("research", NEW_RESEARCH), ("refute", REFUTE)):
            (folder / f"{name}.md").write_text(body, encoding="utf-8")
        message = approval_message(f"[경과 문서](log/task/{TASK}/status.md)")

        # 전이 조건을 채운 작업은 통과한다.
        (folder / "status.md").write_text(pending, encoding="utf-8")
        self.assertIsNone(self.run_check(message))

        # 검토 필요 기록이 빠진 작업은 전이 검사 사유로 block한다.
        from hooks.tests.test_sub_workflow import REVIEW_ITEM
        broken = pending.replace(REVIEW_ITEM, "- 검토 9. 다른: 안 1. A (사용자 검토 시각: 2026-10-24 12:00:00)"+chr(10))
        (folder / "status.md").write_text(broken, encoding="utf-8")
        self.assertIn(hook.REASON_APPROVAL_TRANSITION.split("{")[0], self.run_check(message) or "")

    # 조사 원문이 빈 작업에서 조사 도구 호출 없는 해결안 제안은 block하고, 조사 원문이 있거나 안 표지가 없는 응답은 통과한다.
    def test_proposal_before_research_blocked(self):
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "status.md").write_text(STATE.format(status="미검토"), encoding="utf-8")
        proposal = "제 추천은 안 1입니다.\n\n| 안 | 장점 |\n| -- | -- |\n| 1 | 빠름 |"
        self.assertIsNone(self.run_check(proposal, agent_type="director"))
        self.assertIsNone(self.run_check("현재 구조는 `a.py`에 있습니다.", agent_type="director"))
        (folder / "research.md").write_text("### 최종 조사 원문: 조사 단계\n\n```\n제안: A\n```\n", encoding="utf-8")
        self.assertIsNone(self.run_check(proposal, agent_type="director"))

    # 질문 문장으로 끝난 응답이 Claude에서만 block되는지 확인한다.
    def test_question_end_blocked_for_claude_only(self):
        (self.state / "status.md").write_text(STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertIn("질문 도구", self.run_check("결과는 아래와 같아요.\n\n- `a.py`\n\n어느 쪽으로 할까요?"))
        self.assertIsNone(self.run_check("어느 쪽으로 할까요?", "codex"))
        self.assertIsNone(self.run_check("결과는 `a.py`에 있어요.\n\n왜 그런지는 아래 표에 정리했어요."))

    # 같은 위반이 반복되면 통과시키고 상태 문서에 미해결을 기록하는지 확인한다.
    def test_repeated_violation_records_unresolved(self):
        doc = self.state / "status.md"
        doc.write_text(STATE.format(status="미검토"), encoding="utf-8")
        reason = self.run_check(APPROVAL)
        with unittest.mock.patch.object(hook, "previous_stop_reason", return_value=reason):
            self.assertIsNone(self.run_check(APPROVAL, stop_hook_active=True))
        text = doc.read_text(encoding="utf-8")
        self.assertIn(sub_record.UNRESOLVED, text)
        self.assertLess(text.index("착수"), text.index(sub_record.UNRESOLVED))

    # log 폴더가 없는 저장소는 작업 흐름 검사를 하지 않는지 확인한다.
    def test_repo_without_state_not_checked(self):
        empty = Path(self.tmp.name) / "plain"
        empty.mkdir()
        with patch_root(empty):
            self.assertIsNone(self.run_check("어느 쪽으로 할까요?"))


STATUS = """# t

- 작업 ID: t
- 작업 상태: 미개시

## 요구사항

### 검토 필요

<!-- hook이 `검토 <번호>.` 질문의 확정 답으로 아래 한 줄을 쓴다. 직접 편집하지 않는다. -->

- 검토 <번호>. <이름>: <확정 안 | 폐기> (사용자 검토 시각: <YYYY-MM-DD HH:MM:SS>)

### 재검토 조건

- 조건

## 작업 경과

- 갱신 시각: x

## 중단 사유

<!-- 작업 상태가 중단일 때만 채운다. -->

- 중단 유형: <충족 불가>
"""


class ResponseForms(unittest.TestCase):
    # 양식 원본의 자리표시자를 값으로 채운 응답은 통과하고, 항목이나 제목을 지운 응답은 누락으로 막히는지 확인한다.
    def test_form_gap_allows_filled_and_blocks_missing(self):
        import re
        import sub_docs
        for form in ("request-stop", "approval-complete", "request-choice",
                     "approval-research", "approval-measure"):
            with self.subTest(form=form):
                filled = re.sub(r"<[^<>\n]+>", "내용", re.sub(r"<!--.*?-->", "", sub_docs.template_text(form), flags=re.S))
                if form == "request-choice":
                    filled = filled.replace("검토 내용 (내용/내용)", "검토 3 (1/2)")
                self.assertEqual(sub_form.form_of(filled), form)
                self.assertEqual(sub_form.form_gap(filled, form), [])
                lines = filled.splitlines()
                labeled = next(i for i, l in enumerate(lines) if re.match(r"\s*- [^:\n]+:", l))
                self.assertTrue(sub_form.form_gap("\n".join(lines[:labeled] + lines[labeled + 1:]), form))

    # 양식 원본의 반복 블록(안 블록, 목록 항목)마다 모든 항목 줄이 있는지, 자리표시자가 남지 않았는지 확인한다. (요구사항 11)
    def test_form_gap_checks_every_repeated_block(self):
        import re
        import sub_docs
        fill = lambda form: re.sub(r"<[^<>\n]+>", "내용", re.sub(r"<!--.*?-->", "", sub_docs.template_text(form), flags=re.S)).replace("검토 내용 (내용/내용)", "검토 3 (1/3)")
        choice = fill("request-choice")
        block = choice[choice.index("#### 안"):choice.index("### 추천 안")]
        three = choice.replace(block, block.replace("안 내용.", "안 1.") + block.replace("안 내용.", "안 2.") + block.replace("안 내용.", "안 3."))
        self.assertEqual(sub_form.form_gap(three, "request-choice"), [])

        # 두 번째 안 블록에서만 단점 줄을 지우면 안 2의 단점 누락을 사유에 담는다.
        head, rest = three.split("#### 안 2.")
        second, tail = rest.split("#### 안 3.")
        broken = head + "#### 안 2." + second.replace("- 단점: 내용 (출처: 내용)\n", "") + "#### 안 3." + tail
        self.assertTrue(any(g.startswith("안 2.") and g.endswith("단점") for g in sub_form.form_gap(broken, "request-choice")))

        # 안 제목에 양식의 안 이름 자리표시자가 남으면 막는다.
        self.assertTrue(any("자리표시자" in g for g in sub_form.form_gap(three.replace("#### 안 1. 내용", "#### 안 1. <안 이름>"), "request-choice")))

        # 조사 계획 항목 두 개 중 둘째의 결론 형식 칸이 없으면 둘째 항목의 누락으로 막고, 완료 보고의 최상위 칸 누락도 막는다.
        kickoff = fill("approval-research")
        item = kickoff[kickoff.index("1. 조사"):kickoff.index("### 잠재 위험과 한계")]
        two = kickoff.replace(item, item + item.replace("1. 조사", "2. 조사"))
        self.assertEqual(sub_form.form_gap(two, "approval-research"), [])
        cut = two.replace(item.replace("1. 조사", "2. 조사"), item.replace("1. 조사", "2. 조사").replace("   - 결론 형식: 내용 (근거: 내용)\n", ""))
        self.assertTrue(any("2. 조사" in g and "결론 형식" in g for g in sub_form.form_gap(cut, "approval-research")))
        complete = fill("approval-complete")
        self.assertEqual(sub_form.form_gap(complete, "approval-complete"), [])
        self.assertTrue(any("경과 문서" in g for g in sub_form.form_gap(complete.replace("- 경과 문서: 내용\n", ""), "approval-complete")))

        # 구현 승인의 사전 작업 항목에서 절차 칸이 비면 항목 이름과 함께 막는다.
        prior = approval_message().replace("해당 없음", "작업 요약\n\n- 호출자 대조\n  - 목적: 대조\n  - 절차:\n  - 발생 가능한 상황: 없음")
        self.assertTrue(any("호출자 대조" in g and "절차" in g for g in sub_form.repeat_gaps(sub_form.body_of(prior), "approval-implement")))
        self.assertEqual(sub_form.repeat_gaps(sub_form.body_of(approval_message()), "approval-implement"), [])

    # 안 블록 밖 양식 줄의 출처 칸도 실재 검사 대상이다. (요구사항 10, 11)
    def test_form_source_gaps_cover_lines_outside_choice_blocks(self):
        root = Path(hook.__file__).resolve().parents[1]
        line = lambda source: f"- 전제 대조: 성립 | 근거: 확인 (출처: {source})\n"
        self.assertEqual(sub_form.source_gaps(line("https://example.com/a"), "request-choice", root), [])
        self.assertEqual(sub_form.source_gaps(line("hooks/check_workflow_response.py:1"), "request-choice", root), [])
        self.assertTrue(any("출처 파일 없음" in g for g in sub_form.source_gaps(line("hooks/none.py:1"), "request-choice", root)))
        self.assertTrue(any("출처 필요" in g for g in sub_form.source_gaps("- 전제 대조: 성립\n", "request-choice", root)))

    # 후보 보고의 안 블록은 줄마다 실재하는 출처를 요구하고, 한계와 위험은 URL만 허용하며, 자리표시자와 확인하지 않은 내용을 막는다. (요구사항 10, 11, 12)
    def test_choice_blocks_require_real_sources(self):
        root = Path(hook.__file__).resolve().parents[1]
        block = lambda n, **over: "#### 안 {n}. 이름\n\n".format(n=n) + "".join(
            f"- {label}: {over.get(label, default)}\n" for label, default in (
                ("적용할 작업", "hooks/a.py를 고친다. (사용자 지시)"),
                ("장점", "동작한다. (출처: hooks/check_workflow_response.py:1)"),
                ("단점", "크다. (출처: https://example.com/a)"),
                ("현재 동작", "차단한다. (출처: hooks/check_workflow_response.py:1-3)"),
                ("한계와 위험", "없음"),
                ("사후 모델 호출 실측", "reviewer 세션 1회, 브라우저 도구 호출, 구현 뒤"),
                ("예시", "입력과 결과")))
        gaps = lambda *blocks: sub_form.choice_gaps("\n".join(blocks), root)
        self.assertEqual(gaps(block(1), block(2)), [])
        self.assertTrue(any("안 2 단점 출처 필요" in g for g in gaps(block(1), block(2, 단점="크다."))))
        self.assertTrue(any("출처 파일 없음 `hooks/none.py`" in g for g in gaps(block(1, 장점="동작한다. (출처: hooks/none.py:1)"))))
        self.assertTrue(any("행 범위 초과" in g for g in gaps(block(1, **{"현재 동작": "차단한다. (출처: hooks/check_workflow_response.py:99999)"}))))
        self.assertTrue(any("URL이어야" in g for g in gaps(block(1, **{"한계와 위험": "제약 (출처: hooks/check_workflow_response.py:1)"}))))
        self.assertEqual(gaps(block(1, **{"한계와 위험": "제약 (출처: https://example.com/limit)"})), [])
        self.assertTrue(any("자리표시자" in g for g in sub_form.choice_gaps(block(1).replace("#### 안 1. 이름", "#### 안 1. <안 이름>"), root)))
        self.assertTrue(any("안 2 단점 줄" in g for g in gaps(block(1), block(2).replace("- 단점: 크다. (출처: https://example.com/a)\n", ""))))
        self.assertTrue(any("직접 확인" in g for g in gaps(block(1, 단점="확인하지 않았음 (출처: https://example.com/a)"))))
        self.assertEqual(gaps(block(1, **{"사후 모델 호출 실측": "확인하지 않은 동작은 실측한다, 세션, 구현 뒤"})), [])

    # 코드 블록만 있는 기존 흐름 절은 허용하고, 빈 절과 코드 예시 안에만 있는 라벨은 누락으로 판정한다.
    def test_form_gap_reads_sections_with_code_blocks(self):
        import re
        import sub_docs
        form = "approval-research"
        filled = re.sub(r"<[^<>\n]+>", "내용", re.sub(r"<!--.*?-->", "", sub_docs.template_text(form), flags=re.S))
        flow = filled.replace("### 기존 흐름\n\n내용", "### 기존 흐름\n\n```text\nhooks/a.py:1 main -> check\n```")
        self.assertEqual(sub_form.form_gap(sub_form.body_of(flow), form, flow), [])
        empty = filled.replace("### 기존 흐름\n\n내용", "### 기존 흐름\n")
        self.assertIn("기존 흐름", sub_form.form_gap(sub_form.body_of(empty), form, empty))
        label = next(l for l in filled.splitlines() if re.match(r"- [^:\n]+:", l))
        fenced = filled.replace(label + "\n", "", 1).replace("### 기존 흐름\n\n내용", f"### 기존 흐름\n\n```text\n{label}\n```")
        self.assertTrue(sub_form.form_gap(sub_form.body_of(fenced), form, fenced))

    # 독립 원본을 채운 실측 양식에서 최상위 항목만 요구사항 연결로 인정한다.
    def test_measure_form_requires_top_level_item_in_measure_section(self):
        import re
        original = (HOOKS.parent / "shared" / "templates" / "workflow" / "approval-measure.md").read_text(encoding="utf-8")
        item = "- 저장 경로 확인: 요구사항 1. 결과 저장"
        filled = original.replace("- <항목>: 요구사항 <번호>. <요구사항 이름>", item)
        filled = re.sub(r"<[^<>\n]+>", "내용", re.sub(r"<!--.*?-->", "", filled, flags=re.S))
        self.assertEqual(sub_form.form_gap(filled, "approval-measure"), [])
        missing_item = filled.replace(item + "\n", "")
        cases = {
            "하위 라벨만 존재": missing_item,
            "하위 항목으로 이동": filled.replace(item, "  " + item),
            "다른 절에 존재": missing_item.replace("## 관측할 결과", "## 관측할 결과\n\n" + item),
            "하위 절에 존재": missing_item.replace("## 관측할 결과", "### 별도 항목\n\n" + item + "\n\n## 관측할 결과"),
            "요구사항 누락": filled.replace(item, "- 저장 경로 확인:"),
            "요구사항 이름 누락": filled.replace(item, "- 저장 경로 확인: 요구사항 1."),
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                self.assertIn("모델 호출 실측 항목의 항목명과 요구사항", sub_form.form_gap(body, "approval-measure"))


class ExecPatches(unittest.TestCase):
    # 큰따옴표, 작은따옴표, 템플릿 리터럴로 전달한 같은 패치가 같은 변경 대상으로 추출되는지 확인한다.
    def test_quote_forms_give_same_targets(self):
        patch = "*** Begin Patch\n*** Update File: log/task/t/status.md\n@@\n-a\n+b\n*** End Patch"
        escaped = patch.replace("\n", chr(92) + "n")
        for quote in ('"', "'", "`"):
            with self.subTest(quote=quote):
                self.assertEqual(sub_record.call_paths("exec", {"input": f"await tools.apply_patch({quote}{escaped}{quote})"}),
                                 ["log/task/t/status.md"])


class RecordAnswers(unittest.TestCase):
    """Stop hook이 질문 도구 답을 경과 문서의 요구사항 목록과 검토 필요 절에 기록한다."""

    # 임시 저장소와 경과 문서를 준비한다.
    def setUp(self):
        role = unittest.mock.patch.dict(os.environ, {"NESTLAB_AGENT": "director"})
        role.start()
        self.addCleanup(role.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.status = self.root / "log" / "task" / "t" / "status.md"
        self.status.parent.mkdir(parents=True)
        self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
        patcher = patch_root(self.root)
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

    # 검토 질문의 확정 답은 경과 문서 검토 필요 절에 한 줄로 기록하고, 근거 위치와 상태 줄 없이 검토 필요 절 밖 본문은 같은지 확인한다. (Claude, Codex)
    def test_review_recorded(self):
        options = [{"label": "안 1. A", "description": "A로 한다."}, {"label": "안 2. B", "description": "B를 한다."}]
        question = "검토 3. X: 기록 방식을 정한다. (문제 1)"
        line = r"(?m)^- 검토 3\. X: {label} \(사용자 검토 시각: \d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}:\d{{2}}\)$"
        for runner, transcript in (("claude", self.claude(question, "안 1. A", options)),
                                   ("codex", self.codex(question, ["안 1. A"], options))):
            with self.subTest(runner=runner):
                self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
                self.stop(transcript, runner)
                text = self.text().replace("\r\n", "\n")
                self.assertRegex(text, line.format(label=r"안 1\. A"))
                self.assertNotIn("근거 위치", text)
                self.assertNotIn("  - 상태", text)
                self.assertNotIn("<번호>", __import__("re").sub(r"<!--.*?-->", "", text, flags=__import__("re").S))
                self.assertEqual(sub_form.splice(text, "검토 필요", lambda b: ""), sub_form.splice(STATUS, "검토 필요", lambda b: ""))

        # 같은 번호를 다시 물으면 항목이 바뀌고 하나만 남는다.
        self.stop(self.claude("검토 3. X: 다시 정한다. (문제 1)", "안 2. B", options), "claude")
        text = self.text().replace("\r\n", "\n")
        self.assertEqual(text.count("- 검토 3."), 1)
        self.assertRegex(text, line.format(label=r"안 2\. B"))

        # 직접 입력 답과 번호 접두가 없는 질문은 기록하지 않는다.
        self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
        self.stop(self.claude(question, "직접 입력한 답", options), "claude")
        self.stop(self.claude("어느 안으로 할까요?", "안 1. A", options), "claude")
        self.assertEqual(self.text(), STATUS.replace("\n", "\r\n"))

    # `요구사항 <번호>.` 질문은 폐지되어 답을 기록하지 않는다.
    def test_requirement_question_not_recorded(self):
        self.stop(self.claude("요구사항 2. 이름: 내용 한 문장", "확정"), "claude")
        self.assertEqual(self.text(), STATUS.replace("\n", "\r\n"))

    # 안 이름 답은 확정 안으로, `폐기` 답은 폐기로 기록하고 `재조사` 답은 기록하지 않는다.
    def test_single_option_review_answers(self):
        options = [{"label": "안 1. A"}, {"label": "폐기"}, {"label": "재조사"}]
        question = "검토 3. X: 기록 방식을 정한다. (문제 1)"
        self.stop(self.claude(question, "재조사", options), "claude")
        self.assertEqual(self.text(), STATUS.replace("\n", "\r\n"))
        self.stop(self.claude(question, "안 1. A", options), "claude")
        self.assertIn("- 검토 3. X: 안 1. A (사용자 검토 시각: ", self.text())
        self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
        self.stop(self.claude(question, "폐기", options), "claude")
        self.assertIn("- 검토 3. X: 폐기 (사용자 검토 시각: ", self.text())

    # 확정 어휘가 섞인 자유 입력(Desktop 형식)과 `user_note:` 메모(CLI 형식)는 선택 답이 아니므로 기록하지 않는다.
    def test_free_input_not_recorded(self):
        options = [{"label": "확정"}, {"label": "보류"}]
        question = "검토 2. 이름: 내용을 정한다. (문제 1)"
        for selected in (["확정하지 말고 다시 봐"], ["None of the above", "user_note: 확정 보류"]):
            self.stop(self.codex(question, selected, options), "codex")
            self.assertEqual(self.text(), STATUS.replace("\n", "\r\n"), selected)

    # 폐기 질문의 확정 답은 알려진 검토 이름으로 폐기를 기록한다.
    def test_review_dropped(self):
        self.status.write_text(STATUS.replace("- 검토 <번호>. <이름>: <확정 안 | 폐기> (사용자 검토 시각: <YYYY-MM-DD HH:MM:SS>)",
                                              "- 검토 5. X: 안 1. A (사용자 검토 시각: 2026-11-12 03:00:00)"), encoding="utf-8")
        self.stop(self.claude("검토 5 폐기: 사유", "확정"), "claude")
        text = self.text().replace("\r\n", "\n")
        self.assertIn("- 검토 5. X: 폐기 (사용자 검토 시각: ", text)
        self.assertEqual(text.count("- 검토 5."), 1)

    # 확정 답이 없는 검토 항목이 있는 경과 문서의 승인 요청은 block하고, 폐기 답이 기록되면 통과한다.
    def test_unfinished_review_blocks_approval(self):
        self.status.write_text(STATUS.replace("- 검토 <번호>. <이름>: <확정 안 | 폐기> (사용자 검토 시각: <YYYY-MM-DD HH:MM:SS>)", "- 검토 4. X"), encoding="utf-8")
        message = approval_message("[경과 문서](log/task/t/status.md)")
        check = lambda: hook.check({"last_assistant_message": message, "cwd": str(self.root), "agent_type": "director"}, "claude")
        with unittest.mock.patch.object(hook, "approval_form_gap", return_value=[]):
            self.assertIn("검토 4. X", check() or "")
            self.stop(self.claude("검토 4 폐기: 사유", "확정"), "claude")
            self.assertIsNone(check())

    # Codex 질문 답 형식에서 답을 얻고, 답 dict가 아닌 출력은 빈 답이다.
    def test_codex_answers_format(self):
        output = json.dumps({"answers": {"q1": {"answers": ["확정 (Recommended)"]}}}, ensure_ascii=False)
        self.assertEqual(hook_session._codex_answer(output, "q1"), "확정 (Recommended)")
        self.assertEqual(hook_session._codex_answer('{"answers":{}}', "q1"), "")

    # exec 입력 안 apply_patch로 쓴 경과 문서를 찾고, 경과 문서가 아닌 파일은 None이다.
    def test_exec_written_status(self):
        question = "요구사항 2. 이름: 내용 한 문장"
        data = lambda t: {"cwd": str(self.root), "transcript_path": t}
        self.assertEqual(sub_record.written_status(data(self.codex(question, ["확정"], via_exec=True)), "codex"), self.status)
        other = self.codex(question, ["확정"], via_exec=True)
        Path(other).write_text(Path(other).read_text(encoding="utf-8").replace("log/task/t/status.md", "hooks/x.py"), encoding="utf-8")
        self.assertIsNone(sub_record.written_status(data(other), "codex"))

    # --record는 답을 한 번 기록하고 작업이 하나면 문서를 쓰지 않은 세션에서도 그 작업을 고른다.
    def test_record_mode(self):
        question = "검토 2. 이름: 내용을 정한다. (문제 1)"
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
                self.assertIn("- 검토 2. 이름: 확정 (사용자 검토 시각: ", first)
                self.stop(transcript, runner)
                self.assertEqual(self.text(), first)
        self.status.write_bytes(STATUS.replace("\n", "\r\n").encode("utf-8"))
        empty = self.save([{"type": "user", "message": {"content": "시작"}}])
        hook.record_tool({"agent_type": "director", "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": question, "options": [{"label": "확정"}]}]},
                          "tool_response": {"answers": {question: "확정"}}, "cwd": str(self.root), "transcript_path": empty}, "claude")
        self.assertIn("- 검토 2. 이름: 확정 (사용자 검토 시각: ", self.text())

    # background_tasks가 남은 Stop에서도 답을 기록하고 block이 없으며, 서브에이전트 Stop은 기록하지 않는다.
    def test_background_tasks_records(self):
        question = "검토 2. 이름: 내용을 정한다. (문제 1)"
        transcript = self.claude(question, "확정")
        data = {"last_assistant_message": GOOD, "cwd": str(self.root), "transcript_path": transcript}
        self.assertIsNone(hook.check({**data, "agent_id": "a1", "background_tasks": [{"id": "t1"}]}))
        self.assertEqual(self.text(), STATUS.replace("\n", "\r\n"))
        self.assertIsNone(hook.check({**data, "background_tasks": [{"id": "t1"}]}))
        self.assertIn("- 검토 2. 이름: 확정 (사용자 검토 시각: ", self.text())

    # 작업 상태를 승인과 중단 유형, 완료 검사로 계산한다.
    def test_work_state(self):
        approved = STATUS.replace("## 작업 경과", "## 승인\n\n### 실행 승인 범위\n\n- 사용자 승인 상태: 승인\n\n## 작업 경과")
        self.assertEqual(sub_record.work_state(self.status, STATUS), "미개시")
        self.assertEqual(sub_record.work_state(self.status, approved), "구현")
        stopped = approved.replace("- 중단 유형: <충족 불가>", "- 중단 유형: 충족 불가")
        self.assertEqual(sub_record.work_state(self.status, stopped), "중단")
        self.status.write_text(approved, encoding="utf-8")
        sub_record.record_answers({"cwd": str(self.root), "transcript_path": self.claude("q?", "확정")}, "claude", [])
        self.assertIn("- 작업 상태: 구현", self.text())

    # 관리 단계의 진행 상태는 이전 승인 값이 없어도 유지한다.
    def test_managed_work_state_uses_stage(self):
        for stage, expected in (("착수", "미개시"), ("조사", "미개시"), ("계획", "미개시"), ("구현", "구현")):
            with self.subTest(stage=stage):
                text = STATUS.replace("- 작업 상태:", f"- 작업 단계: {stage}\n- 작업 상태:", 1)
                self.assertEqual(sub_record.work_state(self.status, text), expected)

    # 명세 문서가 바뀌는 확정 답은 승인을 승인 대기로 바꾸고, 같은 답 재처리는 승인을 유지한다.
    def test_spec_change_resets_approval(self):
        approved = STATUS.replace("## 작업 경과", "## 승인\n\n### 실행 승인 범위\n\n- 사용자 승인 상태: 승인\n\n## 작업 경과")
        self.status.with_name("spec.md").write_text("# t 명세\n\n## 요구사항 목록\n\n### 1. 하나\n\n- 기대 결과:\n  - 기존\n\n## 작업 계획\n", encoding="utf-8")
        self.status.write_text(approved, encoding="utf-8")
        options = [{"label": "안 1. A", "description": "A를 한다."}]
        data = {"cwd": str(self.root), "transcript_path": self.claude("검토 2. 이름: 내용을 정한다. (문제 1)", "안 1. A", options)}
        sub_record.record_answers(data, "claude")
        self.assertIn("- 사용자 승인 상태: 승인 대기", self.text())
        self.status.write_text(self.text().replace("승인 대기", "승인"), encoding="utf-8")
        sub_record.record_answers(data, "claude")
        self.assertNotIn("승인 대기", self.text())

    # 검토 확정 답은 명세 요구사항 목록 끝에 다음 번호 항목과 기대 결과로 추가하고, 폐기와 재조사는 요구사항 목록을 바꾸지 않는다.
    def test_spec_requirement_appended(self):
        spec = self.status.with_name("spec.md")
        cell = "- 목표:\n  - 기존 목표\n- 기대 결과:\n  - 기존 결과\n"
        spec.write_text(f"# t 명세\n\n## 요구사항 목록\n\n### 1. 하나\n\n{cell}\n### 2. 둘\n\n{cell}\n## 작업 계획\n", encoding="utf-8")
        text = lambda: spec.read_text(encoding="utf-8")
        options = [{"label": "안 1. A", "description": "B를 한다."}, {"label": "안 2. C", "description": "D를 한다."}, {"label": "폐기"}, {"label": "재조사"}]
        record = lambda q, a: self.stop(self.claude(q, a, options), "claude")

        # 확정 안은 다음 번호 `### 3. X` 항목에 라벨과 선택지 설명으로 추가한다.
        record("검토 3. X: 방식을 정한다. (문제 1)", "안 1. A")
        self.assertIn("### 3. X\n", text())
        self.assertIn("- 기대 결과:\n  - 안 1. A: B를 한다.\n", text())
        self.assertEqual(text().count("- 목표:\n  - 기존 목표\n"), 2)
        # 새 항목의 결과 칸은 양식 자리표시 대신 선택지 설명으로 채운다.
        self.assertIn("- 결과:\n  - B를 한다.\n", text())
        self.assertNotIn("<목표를 달성했을 때 기대되는 결과>", text())
        # 질문의 문제 번호는 새 항목의 문제 칸에 쓴다.
        self.assertIn("### 3. X\n\n- 문제: 문제 1\n", text())

        # 같은 답 재처리는 문서를 바꾸지 않고, 같은 항목을 다른 안으로 재확정하면 그 항목의 기대 결과만 바꾼다.
        before = text()
        record("검토 3. X: 방식을 정한다. (문제 1)", "안 1. A")
        self.assertEqual(text(), before)
        record("검토 3. X: 방식을 정한다. (문제 1)", "안 2. C")
        self.assertEqual(text().count("### 3. X"), 1)
        self.assertIn("  - 안 2. C: D를 한다.\n", text())
        self.assertNotIn("B를 한다.", text())

        # 폐기와 재조사 답은 요구사항 목록을 바꾸지 않는다.
        before = text()
        record("검토 4. Y: 방식을 정한다. (문제 1)", "폐기")
        record("검토 5. Z: 방식을 정한다. (문제 1)", "재조사")
        self.assertEqual(text(), before)
        self.assertIn("- 검토 4. Y: 폐기 (사용자 검토 시각: ", self.text())

    # 문장이 세 개인 선택지 설명은 결과 칸에 세 줄 목록으로 기록하고, 여러 문제 번호는 문제 칸에 함께 쓴다. (요구사항 19)
    def test_spec_result_cell_sentences(self):
        spec = self.status.with_name("spec.md")
        spec.write_text("# t 명세\n\n## 요구사항 목록\n\n### 1. 하나\n\n- 목표:\n  - 기존\n- 기대 결과:\n  - 기존\n\n## 작업 계획\n", encoding="utf-8")
        options = [{"label": "안 1. A", "description": "첫째를 한다. 둘째를 한다. 셋째를 한다."}]
        self.stop(self.claude("검토 2. Y: 방식을 정한다. (문제 1, 2)", "안 1. A", options), "claude")
        text = spec.read_text(encoding="utf-8")
        self.assertIn("- 문제: 문제 1, 2\n", text)
        self.assertIn("- 결과:\n  - 첫째를 한다.\n  - 둘째를 한다.\n  - 셋째를 한다.\n", text)

    # 명세 문서가 없으면 질문 답은 경과 문서에만 기록된다.
    def test_spec_missing_skipped(self):
        self.stop(self.claude("검토 2. 이름: 내용을 정한다. (문제 1)", "확정"), "claude")
        self.assertFalse(self.status.with_name("spec.md").exists())
        self.assertIn("- 검토 2. 이름: 확정 (사용자 검토 시각: ", self.text())

    # 중단 사유 절 끝에 미해결 줄을 덧붙이는지 확인한다.
    def test_unresolved_line_appended(self):
        sub_record.record_unresolved([self.status], "사유\n두 줄")
        text = self.text().replace("\r\n", "\n")
        self.assertTrue(text.endswith("- 중단 유형: <충족 불가>\n- 중단 판정 미해결: 사유 두 줄\n"))


class ApprovalForm(unittest.TestCase):
    def setUp(self):
        role = unittest.mock.patch.dict(os.environ, {"NESTLAB_AGENT": "director"})
        role.start()
        self.addCleanup(role.stop)
    # 도구 결과가 없어도 명시적 승인 입력 요구와 불완전한 승인 서식을 차단한다.
    def test_explicit_requests_need_form(self):
        for message in ("승인으로 답해 주십시오.", "승인을 입력해 주세요.",
                        "다음 행동은 `승인` 입력을 보내면 실행합니다.",
                        "# 구현 승인 요청\n\n작업을 시작합니다.",
                        approval_message().replace("hooks/a.py에 입력 검사를 추가하고 입력 처리 호출자가 오류 반환을 받게 한다.", "<!-- 작성 -->")):
            with self.subTest(message=message):
                reason, _ = hook.workflow_reason({"cwd": PLAIN}, message, sub_form.body_of(message), "codex")
                self.assertIn(sub_form.REASON_APPROVAL_FORM, reason or "")
                self.assertIn("누락 절:", reason)

    # 승인 사실과 예시 및 인용의 표현은 실제 입력 요구로 보지 않는다.
    def test_reports_and_examples_pass(self):
        for message in ("승인을 받았습니다.", "추가 승인이 필요 없습니다.", "검증 결과를 확인했습니다.",
                        "```text\n승인으로 답해 주십시오.\n```", "예시: 승인으로 답해 주십시오.",
                        "- e.g. `승인` 입력을 보내면 실행합니다.", "> 승인으로 답해 주십시오."):
            with self.subTest(message=message):
                self.assertFalse(sub_form.requests_approval(message))
                self.assertEqual(hook.workflow_reason({"cwd": PLAIN}, message, sub_form.body_of(message), "codex"), (None, []))

    # 실제 차단과 열람한 소스 및 과거 기록의 차단 문구는 응답 자체의 승인 요청과 구분한다.
    def test_tool_results_do_not_request_approval(self):
        reason = "사용자 마지막 메시지에 승인 키워드가 없습니다. 작업 전 사용자 승인을 받으십시오."
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
                        self.assertIn(sub_form.REASON_APPROVAL_FORM, hook.check(data, runner, record=False) or "")
                        data["last_assistant_message"] = approval_message()
                        self.assertIsNone(hook.check(data, runner, record=False))


class ApprovalFields(unittest.TestCase):
    plan = "# 명세\n## 요구사항 목록\n### 1. 기능\n- 기대 결과:\n  - 입력 오류를 거부한다.\n"

    # 명세 문서 절에 링크가 없으면 승인 요청을 막고, 파일별 목록과 Test Case가 없어도 링크가 있으면 통과한다.
    def test_spec_link_required(self):
        message = approval_message()
        self.assertEqual(sub_form.approval_form_gap(message, [self.plan]), [])
        self.assertIn("명세 문서 > 명세 문서 링크 필요",
                      sub_form.approval_form_gap(message.replace("[입력 검사 명세](spec.md)", "입력 검사 명세"), [self.plan]))

    # 완결 승인안도 연결된 명세 전체의 Test Case와 검증 유형 및 번호 연결을 통과해야 한다.
    def test_actual_request_checks_entire_plan_before_approval(self):
        from hooks.tests.test_sub_workflow import NEW_PLAN
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = root / "log/task/t"
            task.mkdir(parents=True)
            (task / "status.md").write_text("# 작업\n- 작업 상태: 미개시\n- 작업 단계: 계획\n### 문제 정의\n- 문제 1. 범위\n  - 사용자 확인 시각: 2026-10-24 11:00:00\n", encoding="utf-8")
            spec = task / "spec.md"
            message = approval_message("[경과 문서](log/task/t/status.md)")
            invalid_plans = (
                (NEW_PLAN.replace("| 허용 | 정상 | `python -V` | 통과 |", ""), "Test Case"),
                (NEW_PLAN.replace("확인 (정적)", "확인"), "검증"),
                (NEW_PLAN.replace("- hooks/a.py: 교체 (요구사항 1)", "- hooks/a.py: 교체"), "요구사항"),
            )
            for runner in ("claude", "codex"):
                data = {"cwd": str(root), "agent_type": "director", "last_assistant_message": message}
                spec.write_text(NEW_PLAN, encoding="utf-8")
                self.assertIsNone(hook.check(data, runner, record=False))
                for plan, expected in invalid_plans:
                    spec.write_text(plan, encoding="utf-8")
                    first = hook.check(data, runner, record=False)
                    self.assertIn(sub_form.REASON_APPROVAL_PLAN, first or "")
                    self.assertIn(expected, first or "")
                    with unittest.mock.patch.object(hook, "previous_stop_reason", return_value=first), \
                            unittest.mock.patch.object(hook, "record_unresolved") as record:
                        self.assertEqual(hook.check({**data, "stop_hook_active": True}, runner, record=False), first)
                        record.assert_not_called()

    # 개별 필드 누락, 빈 값과 자리표시자 및 요구사항 불일치를 두 runner에서 차단한다.
    def test_fields_and_spec_match(self):
        message = approval_message()
        self.assertEqual(sub_form.approval_form_gap(message, [self.plan]), [])
        replacements = [
            ("- 요구사항 1. 기능: 입력 오류를 거부한다.", "- 기능을 고친다."),
            ("- 요구사항 1. 기능: 입력 오류를 거부한다.", "- 요구사항 1. 기능: "),
            ("  - 이번 변경: 입력 검사를 추가한다.", ""),
            ("hooks/a.py에 입력 검사를 추가하고 입력 처리 호출자가 오류 반환을 받게 한다.", ""),
            ("요구사항 1. 기능: 입력 오류를 거부한다.", "요구사항 99. 기능: 입력 오류를 거부한다."),
            ("요구사항 1. 기능: 입력 오류를 거부한다.", "요구사항 1. 다른 이름: 입력 오류를 거부한다."),
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = root / "log/task/t"
            task.mkdir(parents=True)
            (task / "spec.md").write_text(self.plan, encoding="utf-8")
            for runner in ("claude", "codex"):
                for before, after in replacements:
                    invalid = message.replace(before, after) + "\n[경과 문서](log/task/t/status.md)"
                    with self.subTest(runner=runner, before=before):
                        data = {"cwd": str(root), "agent_type": "director", "last_assistant_message": invalid,
                                "stop_hook_active": True}
                        # 같은 승인 서식 오류는 직전 사유와 같아도 반복 허용하지 않는다.
                        reason = hook.workflow_reason(data, invalid, sub_form.body_of(invalid), runner)[0]
                        with unittest.mock.patch.object(hook, "previous_stop_reason", return_value=reason):
                            self.assertIn(sub_form.REASON_APPROVAL_FORM, hook.check(data, runner, record=False) or "")
        for result in ("<판정 기준>", "미확정", "확정 대기"):
            self.assertTrue(sub_form.approval_form_gap(message, [self.plan.replace("입력 오류를 거부한다.", result)]))

    # 사전 작업과 실측의 조건부 필드를 검사하며 정당한 검증 불필요는 허용한다.
    def test_conditional_fields(self):
        message = approval_message()
        prior = message.replace("해당 없음", "- 호환 확인\n  - 목적: 호출자 대조\n  - 절차: 호출자 읽기\n  - 발생 가능한 상황: 호출자 누락")
        self.assertEqual(sub_form.approval_form_gap(prior, [self.plan]), [])
        self.assertTrue(sub_form.approval_form_gap(prior.replace("  - 목적: 호출자 대조", ""), [self.plan]))


class RepeatedStop(unittest.TestCase):
    # 승인 시각은 실행 시각 대신 단계 메시지의 실제 기록 시각을 쓰며 반복 처리에도 동일하다.
    def test_approval_time_uses_recognized_message(self):
        text = ("# 작업\n- 작업 단계: 조사\n## 조사 승인 범위\n- 사용자 승인 시각: 기록 없음\n"
                "## 구현 승인 범위\n- 사용자 승인 시각: 기록 없음\n")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.jsonl"
            for runner in ("claude", "codex"):
                for stage, title in (("조사", "조사 승인 범위"), ("구현", "구현 승인 범위")):
                    keyword = {"조사": "research", "구현": "implement"}[stage]
                    if runner == "claude":
                        row = {"type": "user", "timestamp": "2026-11-10T02:13:34Z", "message": {"content": f"[{keyword}]"}}
                    else:
                        row = {"type": "response_item", "timestamp": "2026-11-10T02:13:34Z", "payload": {
                            "type": "message", "role": "user", "content": [{"type": "input_text", "text": f"[{keyword}]"}]}}
                    path.write_text(json.dumps(row), encoding="utf-8")
                    data = {"transcript_path": str(path)}
                    original = text.replace("단계: 조사", f"단계: {stage}")
                    result = sub_approval.approval_record_times(data, path, original)
                    self.assertIn("사용자 승인 시각: 2026-11-10 11:13:34", sub_docs.section(result, title))
                    self.assertEqual(sub_approval.approval_record_times(data, path, result), result)
                    row.pop("timestamp")
                    path.write_text(json.dumps(row), encoding="utf-8")
                    self.assertNotIn("2026-11-10", sub_approval.approval_record_times(data, path, original))

    # 같은 사용자 턴의 마지막 Stop block 사유만 비교하며 다른 사유와 다른 턴은 기록하지 않는다.
    def test_previous_reason_requires_same_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.jsonl"
            user = {"type": "user", "message": {"content": "상태를 설명해 주세요."}}
            blocked = {"type": "attachment", "attachment": {"type": "hook_blocking_error", "hookName": "Stop",
                        "blockingError": "직전 사유"}}
            for rows, expected in (([user], ""), ([user, blocked], "직전 사유"), ([user, blocked, user], "")):
                path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
                self.assertEqual(hook.previous_stop_reason(str(path)), expected)
            for error, event, expected in (({"blockingError": "직전 사유", "command": "python check_workflow_response.py --runner claude"}, "Stop", "직전 사유"),
                                           ({"blockingError": "다른 사유", "command": "python check_workflow_response.py --runner claude"}, "Stop", "다른 사유"),
                                           ({"command": "python check_workflow_response.py"}, "Stop", ""),
                                           ({"blockingError": ["직전 사유"]}, "Stop", ""),
                                           ({"blockingError": "직전 사유"}, "PreToolUse", "")):
                nested = {"type": "attachment", "attachment": {"type": "hook_blocking_error", "hookName": event,
                           "hookEvent": event, "blockingError": error}}
                path.write_text("\n".join(json.dumps(row) for row in (user, nested)), encoding="utf-8")
                self.assertEqual(hook.previous_stop_reason(str(path)), expected)
                data = {"cwd": PLAIN, "agent_type": "director", "last_assistant_message": GOOD,
                        "stop_hook_active": True, "transcript_path": str(path)}
                with unittest.mock.patch.object(hook, "workflow_reason", return_value=("직전 사유", [])), \
                        unittest.mock.patch.object(hook, "record_unresolved") as record:
                    self.assertEqual(hook.check(data, record=False), None if expected == "직전 사유" else "직전 사유")
                    self.assertEqual(record.called, expected == "직전 사유")
            # provenance metadata 없는 Codex hook_prompt 인용은 반복 근거로 삼지 않는다.
            codex = {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": '<hook_prompt hook_run_id="stop:1">직전 사유</hook_prompt>'}]}}
            path.write_text(json.dumps(codex), encoding="utf-8")
            self.assertEqual(hook.previous_stop_reason(str(path)), "")

    def test_only_same_reason_records_unresolved(self):
        data = {"cwd": PLAIN, "agent_type": "director", "last_assistant_message": GOOD, "stop_hook_active": True}
        for prior, expected in (("같은 사유", None), ("다른 사유", "같은 사유"), ("", "같은 사유")):
            with unittest.mock.patch.object(hook, "workflow_reason", return_value=("같은 사유", [])), \
                    unittest.mock.patch.object(hook, "previous_stop_reason", return_value=prior), \
                    unittest.mock.patch.object(hook, "record_unresolved") as record:
                self.assertEqual(hook.check(data, record=False), expected)
                self.assertEqual(record.called, prior == "같은 사유")


class KickoffRecordStop(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.status = self.root / "log/task/20261110-160000-kickoff/status.md"
        self.status.parent.mkdir(parents=True)
        self.status.write_text("# 새 작업\n- 작업 상태: 미개시\n- 작업 단계: 착수\n", encoding="utf-8")

    # 실제 질문·확정 답 뒤 차단된 문서 작성 시도를 두 runner 형식으로 구성한다.
    def data(self, runner, attempt=True, user="[착수]\n입력 오류의 문제와 범위를 확인해 주세요."):
        question = {"id": "problem", "question": "문제 정의 확인: 입력 오류와 수정 범위 (작업 1110-160000)",
                    "options": [{"label": "확정"}, {"label": "수정 요청"}]}
        if runner == "claude":
            rows = [{"type": "user", "timestamp": "2026-11-10T07:54:26Z", "message": {"content": user}},
                    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "q1", "name": "AskUserQuestion",
                        "input": {"questions": [question]}}]}},
                    {"type": "user", "timestamp": "2026-11-10T07:55:43Z", "message": {"content": [
                        {"type": "tool_result", "tool_use_id": "q1", "content": "확정"}]},
                        "toolUseResult": {"answers": {question["question"]: "확정"}}}]
            if attempt:
                rows.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "w1", "name": "Write",
                             "input": {"file_path": str(self.status), "content": "# 새 기록"}}]}})
        else:
            item = lambda payload, stamp="2026-11-10T07:55:43Z": {"type": "response_item", "timestamp": stamp, "payload": payload}
            rows = [item({"type": "message", "role": "user", "content": [{"type": "input_text", "text": user}]}, "2026-11-10T07:54:26Z"),
                    item({"type": "function_call", "name": "request_user_input", "call_id": "q1", "arguments": json.dumps({"questions": [question]})}),
                    item({"type": "function_call_output", "call_id": "q1", "output": json.dumps({"answers": {"problem": {"answers": ["확정"]}}})})]
            if attempt:
                rows.append(item({"type": "custom_tool_call", "name": "apply_patch", "call_id": "w1",
                                  "input": f"*** Begin Patch\n*** Update File: {self.status.as_posix()}\n@@\n-# 새 작업\n+# 새 기록\n*** End Patch"}))
        transcript = self.root / f"{runner}.jsonl"
        transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        return {"cwd": str(self.root), "agent_type": "director", "transcript_path": str(transcript),
                "last_assistant_message": "문제 정의 기록이 형식 검사로 차단됐습니다. 근거: hooks/check_tool_use/sub_scope.py:1"}

    def test_reports_explanation_and_incident_reply_keep_existing_paths(self):
        for runner in ("claude", "codex"):
            data = self.data(runner)
            for form in ("approval-research", "request-stop"):
                message = re.sub(r"<!--.*?-->", "", sub_docs.template_text(form), flags=re.S)
                while re.search(r"<[^<>\n]+>", message):
                    message = re.sub(r"<[^<>\n]+>", "기록", message)
                self.assertIsNone(hook.check({**data, "last_assistant_message": message}, runner, record=False))
            for message in ('위반한 지침: AGENTS.md:1 "기존 지침"', "오탐: 인용한 문장의 표현입니다."):
                self.assertIsNone(hook.check({**data, "last_assistant_message": message}, runner, record=False))
            explanation = self.data(runner)
            transcript = Path(explanation["transcript_path"])
            if runner == "claude":
                row = {"type": "user", "message": {"content": "사용자 지시와 해석의 차이를 설명해 주세요."}}
            else:
                row = {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "사용자 지시와 해석의 차이를 설명해 주세요."}]}}
            with transcript.open("a", encoding="utf-8") as stream:
                stream.write("\n" + json.dumps(row))
            self.assertIsNone(hook.check({**explanation, "last_assistant_message": GOOD}, runner, record=False))


class StopSupplements(unittest.TestCase):
    def setUp(self):
        KickoffRecordStop.setUp(self)

    def filled(self, form):
        text = re.sub(r"<!--.*?-->", "", sub_docs.template_text(form), flags=re.S)
        while re.search(r"<[^<>\n]+>", text):
            text = re.sub(r"<[^<>\n]+>", "기록", text)
        return text

    # 첫 보고와 실제 Stop 기록을 같은 턴에 넣는다. 보완 조각도 같은 경로로 누적한다.
    def append(self, data, runner, message, reason=None):
        if runner == "codex":
            rows = [{"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer",
                     "internal_chat_message_metadata_passthrough": {"turn_id": "turn-fixture"},
                     "content": [{"type": "output_text", "text": message}]}}]
            if reason:
                rows.append(codex_hook(f'<hook_prompt hook_run_id="stop:fixture">{reason}</hook_prompt>'))
        else:
            rows = [{"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": message}]}}]
            if reason:
                rows.append({"type": "attachment", "attachment": {"type": "hook_blocking_error", "hookName": "Stop", "hookEvent": "Stop",
                             "blockingError": {"blockingError": reason, "command": "python check_workflow_response.py --runner claude"}}})
        with Path(data["transcript_path"]).open("a", encoding="utf-8") as stream:
            stream.write("\n" + "\n".join(json.dumps(row) for row in rows))

    def test_kickoff_sections_are_checked_as_one_response(self):
        full = self.filled("approval-research")
        names = ["잠재 위험과 한계", "승인 상태"]
        partial = full
        for title in names:
            partial = sub_form.splice(partial, title, lambda _: "\n")
        for runner in ("claude", "codex"):
            data = KickoffRecordStop.data(self, runner)
            first = hook.check({**data, "last_assistant_message": partial}, runner, record=False)
            self.assertIn("누락", first or "")
            self.append(data, runner, partial, first)
            fragment = f"### {names[0]}\n" + sub_docs.section(full, names[0])
            second = hook.check({**data, "last_assistant_message": fragment, "stop_hook_active": True}, runner, record=False)
            self.assertIn(names[1], second or "")
            self.assertNotIn("착수 단계에서 같은 작업", second or "")
            self.append(data, runner, fragment, second)
            final = f"### {names[1]}\n" + sub_docs.section(full, names[1])
            self.assertIsNone(hook.check({**data, "last_assistant_message": final, "stop_hook_active": True}, runner, record=False))
            combined = sub_form.completed_response(data, runner, final)
            self.assertEqual(sub_form.form_gap(sub_form.body_of(combined), "approval-research", combined), [])
            self.append(data, runner, final)
            self.assertEqual(sub_form.completed_response(data, runner, final), combined)
            replacement = full.replace("기록", "새 기록")
            self.assertEqual(sub_form.completed_response(data, runner, replacement), replacement)
            incident = '위반한 지침: AGENTS.md:1 "지침 원문"'
            self.assertEqual(sub_form.completed_response(data, runner, incident), incident)

    def test_approval_supplement_still_checks_entire_spec(self):
        from hooks.tests.test_sub_workflow import NEW_PLAN
        self.status.write_text("# 작업\n- 작업 단계: 계획\n- 작업 상태: 미개시\n### 문제 정의\n- 문제 1. 범위\n  - 사용자 확인 시각: 2026-10-24 11:00:00\n", encoding="utf-8")
        spec = self.status.with_name("spec.md")
        full = approval_message(f"[경과 문서]({self.status.as_posix()})")
        partial = sub_form.splice(full, "검증 계획", lambda _: "\n")
        fragment = "## 검증 계획\n" + sub_docs.section(full, "검증 계획")
        for runner in ("claude", "codex"):
            data = KickoffRecordStop.data(self, runner, user="[계획]\n명세를 보완해 주세요.")
            spec.write_text(NEW_PLAN.replace("docs/a.md:1", "docs/a.md"), encoding="utf-8")
            first = hook.check({**data, "last_assistant_message": partial}, runner, record=False)
            self.append(data, runner, partial, first)
            second = hook.check({**data, "last_assistant_message": fragment}, runner, record=False)
            self.assertIn(sub_form.REASON_APPROVAL_PLAN, second or "")
            self.assertIn("문서 갱신 대상", second or "")
            self.append(data, runner, fragment, second)
            self.assertEqual(hook.check({**data, "last_assistant_message": fragment, "stop_hook_active": True}, runner, record=False), second)
            spec.write_text(NEW_PLAN, encoding="utf-8")
            self.assertIsNone(hook.check({**data, "last_assistant_message": fragment, "stop_hook_active": True}, runner, record=False))

    # 실제 승인 서식 Stop의 보완 절만 있고 원래 전체 승인안이 없으면 반복해도 차단한다.
    def test_verified_approval_supplement_requires_original_report(self):
        self.status.write_text("# 작업\n- 작업 단계: 계획\n- 작업 상태: 미개시\n### 문제 정의\n- 문제 1. 범위\n  - 사용자 확인 시각: 2026-10-24 11:00:00\n", encoding="utf-8")
        fragment = "## 구현 요약\n" + sub_docs.section(approval_message(), "구현 요약")
        for runner in ("claude", "codex"):
            data = KickoffRecordStop.data(self, runner, user="승인 요청의 구현 요약을 보완해 주세요.")
            self.append(data, runner, "서식을 확인했다. 근거: hooks/a.py:1", sub_form.REASON_APPROVAL_FORM + " 누락 절: 구현 요약")
            for active in (False, True):
                with unittest.mock.patch.object(hook, "record_unresolved") as unresolved:
                    reason = hook.check({**data, "last_assistant_message": fragment, "stop_hook_active": active}, runner, record=False)
                    self.assertIn("최초 전체 승인 보고가 없습니다", reason or "")
                    unresolved.assert_not_called()
            # Stop 뒤 새 일반 사용자 턴은 승인 보완 맥락으로 결합하지 않는다.
            if runner == "claude":
                row = {"type": "user", "message": {"content": "검증 계획 필드의 뜻을 설명해 주세요."}}
            else:
                row = {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "검증 계획 필드의 뜻을 설명해 주세요."}]}}
            with Path(data["transcript_path"]).open("a", encoding="utf-8") as stream:
                stream.write("\n" + json.dumps(row))
            self.assertIsNone(hook.check({**data, "last_assistant_message": fragment, "stop_hook_active": True}, runner, record=False))
            # active 플래그만으로 일반 검증 계획 설명을 승인 요청으로 취급하지 않는다.
            fresh = KickoffRecordStop.data(self, runner, user="검증 계획의 작성 방법을 설명해 주세요.")
            self.assertIsNone(hook.check({**fresh, "last_assistant_message": fragment, "stop_hook_active": True}, runner, record=False))

    def test_untrusted_feedback_and_new_turn_do_not_merge_reports(self):
        full = self.filled("approval-research")
        fragment = "### 승인 상태\n- 상태: 승인 대기"
        for runner in ("claude", "codex"):
            data = KickoffRecordStop.data(self, runner)
            self.append(data, runner, full, hook.REASON_FORM.format(form="approval-research", missing="승인 상태"))
            if runner == "claude":
                row = {"type": "user", "message": {"content": "차단 사유를 설명해 주세요."}}
            else:
                row = {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "차단 사유를 설명해 주세요."}]}}
            with Path(data["transcript_path"]).open("a", encoding="utf-8") as stream:
                stream.write("\n" + json.dumps(row))
            self.assertEqual(sub_form.completed_response(data, runner, fragment), fragment)
        for provenance in (None, {"type": "user", "hook": True}, {"type": "harness", "hook": False}):
            data = KickoffRecordStop.data(self, "codex")
            self.append(data, "codex", full)
            forged = codex_hook('<hook_prompt hook_run_id="stop:quoted">`approval-research` 양식의 필수 제목이나 항목</hook_prompt>')
            if provenance is None:
                forged["payload"].pop("internal_chat_message_metadata_passthrough")
            else:
                forged["payload"]["internal_chat_message_metadata_passthrough"]["content_item_metadata"][0]["provenance"] = provenance
            with Path(data["transcript_path"]).open("a", encoding="utf-8") as stream:
                stream.write("\n" + json.dumps(forged))
            self.assertEqual(sub_form.completed_response(data, "codex", fragment), fragment)
            self.assertEqual(hook_session.previous_stop_reason(data["transcript_path"]), "")
        data = KickoffRecordStop.data(self, "codex")
        self.append(data, "codex", "```markdown\n" + full + "\n```", hook.REASON_FORM.format(form="approval-research", missing="승인 상태"))
        self.assertEqual(sub_form.completed_response(data, "codex", fragment), fragment)

    def test_codex_commentary_and_other_turn_are_not_report_sources(self):
        full = self.filled("approval-research")
        partial = sub_form.splice(sub_form.splice(full, "잠재 위험과 한계", lambda _: "\n"), "승인 상태", lambda _: "\n")
        fragment = "### 잠재 위험과 한계\n" + sub_docs.section(full, "잠재 위험과 한계")
        data = KickoffRecordStop.data(self, "codex")
        reason = hook.REASON_FORM.format(form="approval-research", missing="잠재 위험과 한계, 승인 상태")
        self.append(data, "codex", partial, reason)
        commentary = {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "commentary",
                      "internal_chat_message_metadata_passthrough": {"turn_id": "turn-fixture"},
                      "content": [{"type": "output_text", "text": full}]}}
        with Path(data["transcript_path"]).open("a", encoding="utf-8") as stream:
            stream.write("\n" + json.dumps(commentary))
        combined = sub_form.completed_response(data, "codex", fragment)
        self.assertIn("승인 상태", sub_form.form_gap(sub_form.body_of(combined), "approval-research", combined))
        entries = hook_session.read_session(data["transcript_path"])
        self.assertEqual(entries[-1]["phase"], "commentary")
        self.assertEqual(entries[-1]["turn_id"], "turn-fixture")
        # 명시한 turn_id가 다르면 같은 파일 안의 Stop 메시지여도 보고 보완에 사용하지 않는다.
        path = Path(data["transcript_path"])
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for row in rows:
            payload = row.get("payload") or {}
            if payload.get("role") == "assistant" and payload.get("phase") == "final_answer":
                payload["internal_chat_message_metadata_passthrough"]["turn_id"] = "other-turn"
        path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        self.assertEqual(sub_form.completed_response(data, "codex", fragment), fragment)
        # phase가 없던 기존 입력은 같은 metadata 경계 안에서 계속 읽는다.
        for row in rows:
            payload = row.get("payload") or {}
            if payload.get("role") == "assistant" and payload.get("phase") == "final_answer":
                payload.pop("phase")
                payload["internal_chat_message_metadata_passthrough"]["turn_id"] = "turn-fixture"
        path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        self.assertNotEqual(sub_form.completed_response(data, "codex", fragment), fragment)

    def test_all_template_missing_fields_are_reported_together(self):
        message = "## 조사 승인 요청\n"
        reason, _ = hook.workflow_reason({"cwd": str(self.root)}, message, sub_form.body_of(message), "codex")
        missing = sub_form.form_gap(sub_form.body_of(message), "approval-research", message)
        self.assertGreater(len(missing), 5)
        self.assertTrue(all(item in reason for item in missing))


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
        patcher = patch_root(self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    # 현재 턴에 연결 문서를 읽고 선택적으로 질문이나 도구 실패가 있는 실제 runner 기록을 만든다.
    def data(self, runner, message, user="승인된 작업을 계속 진행해 주세요.", question=None, denied=False, initial=False, **extra):
        # 작업 연결은 사용자 메시지의 작업 ID로만 하므로 요청 끝 줄에 작업 ID를 둔다.
        user = f"{user}\n{self.folder.name}"
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
        return {"cwd": str(self.root), "agent_type": "director", "transcript_path": str(transcript), "last_assistant_message": message, **extra}

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

    # 최신 검증의 FAIL과 미실행 및 명세에 남은 재작업 및 재검증 요청도 종료를 차단한다.
    def test_pending_evidence(self):
        for result in ("FAIL", "미측정"):
            self.status.write_text(self.complete_state.replace("PASS:", result + ":"), encoding="utf-8")
            self.assertIn("승인된 작업이 남아", sub_completion.unfinished_reason(self.data("claude", GOOD), GOOD, "claude"))
        self.status.write_text(sub_form.splice(self.complete_state, "검증 결과", lambda _: "\n- 검증 미개시\n"), encoding="utf-8")
        self.assertIn("검증을 시작하지 않은", sub_completion.unfinished_reason(self.data("claude", GOOD), GOOD, "claude"))
        for title in ("재구현 요청", "재검증 요청"):
            self.status.write_text(self.complete_state, encoding="utf-8")
            self.spec.write_text(self.plan.replace(f"## {title}\n없음\n", f"## {title}\n- hooks/a.py: 수정 (요구사항 1)\n"), encoding="utf-8")
            self.assertIn(title, sub_completion.unfinished_reason(self.data("codex", GOOD), GOOD, "codex"))

    # 정식 완료 보고와 실제 완료 접두어는 남은 문제의 고정값을 포함한 기존 완료 검사를 먼저 통과한다.
    def test_completion_report_and_prefix_require_complete_record(self):
        report = (f"## 완료 승인 요청\n- 경과 문서: {self.status.as_posix()}\n"
                  "| 요구사항 | 판정 | 근거 |\n| --- | --- | --- |\n| 1. 기능 | PASS | hooks/a.py:1 |\n"
                  "### 모델 호출 실측 결과\n- 해당 없음\n"
                  "### 확인 범위와 한계\n- 문자열 입력을 확인했다. 근거: hooks/a.py:1\n"
                  "### 구조 변경\n없음\n### 커밋 메시지\n```text\nfix: 입력 검사 추가\n\n- 입력 검사\n```\n")
        for runner in ("claude", "codex"):
            for message, user in ((report, "결과를 보고해 주세요."), (GOOD, "[complete]")):
                self.status.write_text(self.complete_state.replace("남은 문제: 없음", "남은 문제: 없음. 문자열 외 입력은 범위 밖"), encoding="utf-8")
                for active in (False, True):
                    data = self.data(runner, message, user=user, stop_hook_active=active)
                    reason = hook.check(data, runner, record=False)
                    self.assertIn(sub_completion.REASON_COMPLETION.split("{", 1)[0], reason or "")
                    self.assertIn("남은 문제가 없음으로", reason or "")
                # 일반 설명 요청은 완료 보고와 구분하며 기록을 자동으로 고치지 않는다.
                self.assertIsNone(hook.check(self.data(runner, GOOD, user="남은 문제 칸의 뜻을 설명해 주세요."), runner, record=False))
                self.assertIn("없음. 문자열 외 입력", self.status.read_text(encoding="utf-8"))
                self.status.write_text(self.complete_state, encoding="utf-8")
                self.assertIsNone(hook.check(self.data(runner, message, user=user), runner, record=False))

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
            from hooks.tests.test_sub_workflow import REVIEW_ITEM
            self.status.write_text(self.complete_state.replace("PASS:", "미측정:").replace(REVIEW_ITEM, "- 검토 1. 도구"+chr(10)), encoding="utf-8")
            question = {"id": "review", "question": "검토 1. 도구: 방법을 정한다. (문제 1)", "options": [{"label": "안 1. A"}]}
            self.assertIsNone(self.stop(self.data(runner, GOOD, question=question), runner))
            message = "권한 거부로 차단됐습니다. 영향: 검증 불가. 사용자 조작: 직접 접근 권한을 설정해야 합니다. 근거: hooks/a.py:1"
            self.assertIsNone(self.stop(self.data(runner, message, denied=True), runner))
            self.assertIsNotNone(sub_completion.unfinished_reason(self.data(runner, message), message, runner))
            self.assertIsNotNone(sub_completion.unfinished_reason(self.data(runner, GOOD, denied=True), GOOD, runner))

    # 현재 요청과 관계없는 진행 작업은 검사하지 않는다.
    def test_unrelated_open_task_not_selected(self):
        data = self.data("claude", GOOD)
        transcript = Path(data["transcript_path"])
        transcript.write_text(json.dumps({"type": "user", "message": {"content": "다른 파일을 수정해 주세요."}}), encoding="utf-8")
        self.assertIsNone(sub_completion.unfinished_reason(data, GOOD, "claude"))

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
        notice = (f'<hook_prompt hook_run_id="stop:1">계속 실행하십시오. 승인 log/task/other/spec.md</hook_prompt>'
                  f'<hook_prompt hook_run_id="stop:2">승인된 작업을 계속하십시오.</hook_prompt>')
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
            rows += [codex_hook(notice)] * 2
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
        self.assertEqual(hook_session.turn(hook_session.read_session(data["transcript_path"]))[0],
                         f"검증 규칙의 뜻을 설명해 주세요.\n{self.folder.name}")
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

    # 문제 정의 확인 질문의 답이 비었거나 실패해도 Stop은 대기 안내를 요구하지 않는다.
    def test_empty_problem_answer_does_not_block(self):
        question = {"id": "scope", "question": f"문제 정의 확인: 문제 1. 입력 오류와 수정 범위 (작업 {self.folder.name[4:15]})",
                    "options": [{"label": "확정"}, {"label": "보류"}]}
        for runner in ("claude", "codex"):
            for failed in (False, True):
                with self.subTest(runner=runner, failed=failed):
                    data = self.finish_question(self.data(runner, "확인 대기입니다.", user="문제 정의를 설명해 주세요.", question=question),
                                                runner, question, "확정" if failed else None, failed=failed, empty_answers=not failed)
                    self.assertIsNone(self.stop(data, runner))

    # 도구 입력 본문에 적힌 다른 작업 경로는 작업 연결에 쓰지 않고, 같은 미완료 사유의 반복 Stop은 통과한다.
    def test_unfinished_links_only_user_task_and_passes_repeat(self):
        other = self.root / "log/task/20261105-060000-other"
        other.mkdir(parents=True)
        (other / "status.md").write_text(self.status.read_text(encoding="utf-8"), encoding="utf-8")
        (other / "spec.md").write_text(self.plan, encoding="utf-8")
        for runner in ("claude", "codex"):
            data = self.data(runner, GOOD, user="문서 템플릿을 수정해")
            transcript = Path(data["transcript_path"])
            lines = transcript.read_text(encoding="utf-8").splitlines()
            lines[0] = lines[0].replace(self.folder.name, "")
            transcript.write_text("\n".join(lines), encoding="utf-8")
            edit = {"file_path": str(self.root / "notes.md"), "old_string": "a",
                    "new_string": f"근거 출처: {(other / 'spec.md').as_posix()}"}
            row = ({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "e1", "name": "Edit", "input": edit}]}}
                   if runner == "claude" else
                   {"type": "response_item", "payload": {"type": "function_call", "call_id": "e1", "name": "Edit", "arguments": json.dumps(edit)}})
            with transcript.open("a", encoding="utf-8") as stream:
                stream.write("\n" + json.dumps(row))
            self.assertIsNone(sub_completion.unfinished_reason(data, GOOD, runner))
            linked = self.data(runner, GOOD)
            reason = sub_completion.unfinished_reason(linked, GOOD, runner)
            self.assertIn("승인된 작업이 남아", reason)
            with unittest.mock.patch.object(hook, "previous_stop_reason", return_value=reason + "\n추가 안내"):
                self.assertIsNone(hook.check({**linked, "stop_hook_active": True}, runner, record=False))
                self.assertIsNotNone(hook.check(linked, runner, record=False))


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
        item = codex_hook
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
