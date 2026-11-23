"""check_response_quality.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOKS))

import io
import json
import os
import tempfile
import unittest
import unittest.mock

import check_response_quality as hook
import check_invalid_response

# 응답 미흡 판정이 사건 문서를 기록하므로 이 모듈의 모든 시험은 저장소 밖 임시 폴더에 쓰고 분석 세션을 띄우지 않는다.
_INCIDENTS = tempfile.TemporaryDirectory()
check_invalid_response.INCIDENT_ROOT = Path(_INCIDENTS.name)
check_invalid_response.launch_analysis = lambda *args, **kwargs: None

ANNOUNCE = "필요한 내용만 교체하겠습니다. 아직 파일은 변경하지 않았습니다."
EXCUSE = "맞습니다! 저는 요청한 문제를 해결하는 대신 상태만 보고했습니다."
GOOD = "수정했습니다.\n\n- `hooks/load_agent.py`: 세션 파일 추가\n- 테스트 통과"
CODE = "아래처럼 변경합니다.\n\n```py\nx = 1\n```"
PLAIN = tempfile.mkdtemp()  # .agent-logs 폴더가 없는 폴더이다.


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
    # 짧은 예고, 300자를 넘는 예고, 목록을 포함한 예고가 block되고 안내에 결과물 작성 요구가 없으며, 인정 표현만 있는 응답은 통과하는지 확인한다.
    def test_announce_blocked_admission_passes(self):
        for text in (ANNOUNCE, "설명입니다. " * 60 + "수정하겠습니다.", "- 항목 하나\n- 항목 둘\n\n수정하겠습니다."):
            self.assertEqual(block(text), hook.REASON_EMPTY, text)
        self.assertNotIn("결과물", hook.stop_output(hook.REASON_EMPTY, "", "claude")["reason"])
        self.assertIsNone(block(EXCUSE))
        self.assertIsNone(block("맞습니다."))

    # 당위형 종결의 예고를 block하고 완료 결과와 인정 표현만 있는 응답은 통과시키는지 확인한다.
    def test_obligation_announce_blocks(self):
        for text in ("수정해야 합니다.", "수정해야 하는 상황입니다."):
            self.assertEqual(block(text), hook.REASON_EMPTY, text)

    # 완료 결과 응답은 통과하고, 경로가 있어도 예고로 끝나면 block되는지 확인한다.
    def test_deliverable_passes(self):
        self.assertIsNone(block(GOOD))
        self.assertIsNone(block(CODE))
        self.assertNotEqual(block("수정했고 테스트 3개가 통과했습니다."), hook.REASON_EMPTY)  # 예고 판정 사유가 없다.
        self.assertEqual(block("바꿀 파일은 hooks/load_agent.py 입니다. 수정하겠습니다."), hook.REASON_EMPTY)

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

    # 한도와 작업 불가 표현이 한 줄에 함께 있는 응답은 사건 기록 없이 block하고, 한도만 언급한 응답은 통과하는지 확인한다.
    def test_limit_refusal_blocked(self):
        self.assertEqual(block("주간 사용량 한도로 배정할 수 없습니다."), hook.REASON_LIMIT)
        self.assertEqual(hook.shift_hits({"last_assistant_message": "주간 사용량 한도로 배정할 수 없습니다.", "cwd": PLAIN}, "claude"), [])
        self.assertIsNone(block("주간 사용량 한도로 배정할 수 없습니다.", stop_hook_active=True))
        self.assertIsNone(block("한도 재설정 시각은 11월 22일입니다."))

    # 위치 이동을 뜻하는 `앞으로` 문장은 책임 회피로 보지 않고, 다짐은 계속 책임 회피로 본다.
    def test_move_direction_not_shift(self):
        self.assertEqual(hook.shift_hits({"last_assistant_message": "묶음 절 안에서 절차를 앞으로 옮겼습니다.", "cwd": PLAIN}, "claude"), [])
        self.assertTrue(hook.shift_hits({"last_assistant_message": "앞으로는 원문을 먼저 확인하겠습니다.", "cwd": PLAIN}, "claude"))

    # `앞으로`, `앞으로는`은 `겠습니다`나 `게요`로 끝나는 다짐만, `미확인`은 표기 설명이 아닐 때만 책임 회피로 보고, `하실 일은 없`은 계속 책임 회피로 본다.
    def test_shift_narrowed_expressions(self):
        hits = lambda text: hook.shift_hits({"last_assistant_message": text, "cwd": PLAIN}, "claude")
        for text in ("표를 앞으로 옮겼습니다.", "앞으로는 hook이 기록합니다.", "미확인 표기를 지웠습니다.", "확인하지 않으면 거부합니다."):
            self.assertEqual(hits(text), [], text)
        for text in ("앞으로는 확인하겠습니다.", "앞으로 확인할게요.", "하실 일은 없습니다.", "이 동작은 미확인입니다."):
            self.assertTrue(hits(text), text)

    # `다음` 뒤 `부터(는)`와 같은 문장의 `하겠습니다`, `할게요`만 책임 회피로 보고 다른 `다음` 문장은 통과한다.
    def test_next_branch_narrowed(self):
        hits = lambda text: hook.shift_hits({"last_assistant_message": text, "cwd": PLAIN}, "claude")
        self.assertTrue(hits("다음부터는 원문을 먼저 확인하겠습니다."))
        self.assertTrue(hits("다음 세션부터 고정 양식을 쓰도록 할게요."))
        self.assertEqual(hits("승인 요청은 다음 턴에 내도록 정하고 있어, 이번에는 결과만 보고합니다."), [])
        self.assertEqual(hits("다음 절에는 표를 둡니다."), [])
        self.assertEqual(hits("다음부터는 표를 둡니다."), [])

    # background_tasks가 남은 응답은 짧은 예고를 통과시키되 다짐 문장은 막고, 빈 목록이면 모두 검사하는지 확인한다.
    def test_background_tasks_pass(self):
        self.assertIsNone(block(ANNOUNCE, background_tasks=[{"id": "t1"}]))
        self.assertIsNotNone(block(ANNOUNCE, background_tasks=[]))
        pledge = "다음 세션에서 같은 일이 반복되지 않도록 기록하겠습니다."
        self.assertIsNotNone(block(pledge, background_tasks=[{"id": "t1"}]))

    # 부정형 주장은 검사하지 않는지 확인한다.
    def test_negative_claim_not_checked(self):
        self.assertIsNone(block("측정하지 않았습니다."))
        # `확인하지 않` 문장은 출처 판정이 아니라 응답 미흡 판정이 다룬다.
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

    # 응답 미흡은 결과물과 길이에 관계없이 포기, 다짐, 확인하지 않은 내용 표현으로 판정하고 [미조사] 줄과 사후 모델 호출 실측 줄은 제외한다.
    def test_deficiency_phrases(self):
        with tempfile.TemporaryDirectory() as tmp, \
             unittest.mock.patch.object(check_invalid_response, "INCIDENT_ROOT", Path(tmp)), \
             unittest.mock.patch.object(check_invalid_response, "launch_analysis"):
            for text in ("- 항목 하나\n- 항목 둘\n\n앞으로 이렇게 하겠습니다.", "이 동작은 미확인입니다.", "하실 일은 없습니다.",
                         "수정 결과를 `a.py`에 반영했습니다.\n\n여기서 마치겠습니다."):
                self.assertEqual(block(text, session_id="abcdef123456"), hook.REASON_EMPTY, text)
            self.assertEqual(len(list((Path(tmp) / ".agent-logs" / "incident").glob("*.md"))), 1)
            self.assertIsNone(block("- 항목 [미조사]\n  - 사유: 범위 밖", session_id="abcdef123456"))
            self.assertIsNone(block("- 사후 모델 호출 실측: 확인하지 않은 동작, reviewer 세션, 구현 뒤", session_id="abcdef123456"))

    # 응답 미흡 표현은 block하고 사건 문서에 기록하되 분석 세션은 띄우지 않고, record가 거짓이면 판정만 한다.
    def test_shift_blocked_and_recorded(self):
        data = {"last_assistant_message": "이미 충분합니다.\n\n- `a.py` 수정", "session_id": "abcdef123456", "cwd": PLAIN}
        with tempfile.TemporaryDirectory() as tmp, \
             unittest.mock.patch.object(check_invalid_response, "INCIDENT_ROOT", Path(tmp)), \
             unittest.mock.patch.object(check_invalid_response, "launch_analysis") as launch:
            self.assertEqual(hook.check(data), hook.REASON_EMPTY)
            text = next((Path(tmp) / ".agent-logs" / "incident").glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("감지 표현: 이미 충분", text)
            launch.assert_not_called()  # 응답 미흡 표현은 약한 유형이라 기록만 하고 분석 세션을 띄우지 않는다.
            self.assertIsNone(check_invalid_response.record(data))  # 사건 감지 표현이 아니다.
            self.assertIsNotNone(hook.check(data, record=False))
            self.assertEqual(launch.call_count, 0)
            self.assertIsNone(hook.check({**data, "stop_hook_active": True}))  # 재작성 뒤에는 한 번만 block한다.
            self.assertEqual(launch.call_count, 0)
            for _ in range(6):  # 감지 기록 8건이 되면 다음 감지는 기록만 하고 통과한다.
                hook.check(data)
            self.assertIsNone(hook.check(data))
            self.assertIsNone(hook.check(data, record=False))  # 화면 교체 판정도 같은 한도를 쓴다.

    # 책임 회피 block 출력에 사건 문서 경로가 있고 REASON_EMPTY 문구가 쓰이는지 확인한다.
    # Claude는 systemMessage로, Codex는 systemMessage를 표시하지 않으므로 block reason 앞에 사건 안내를 담는다.
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
            shown = result["reason"] if runner == "codex" else result["systemMessage"]
            self.assertEqual("systemMessage" in result, runner == "claude")
            self.assertIn("사건 감지(이미 충분): ", shown)
            self.assertIn(".md", shown)
        self.assertIsNone(block("수정했습니다.\n\n- `a.py` 수정"))

    # 저장소 cwd의 감지 알림은 절대 경로 대신 `[사건-id](.agent-logs/incident/<사건-id>.md)` 링크 전체이고, 감지 표현이 없으면 알림이 없다.
    def test_notice_uses_repo_relative_link(self):
        data = {"last_assistant_message": "앞으로는 원문을 먼저 확인하겠습니다.\n\n- `a.py` 수정", "session_id": "abcdef123456"}
        with tempfile.TemporaryDirectory() as tmp, \
             unittest.mock.patch.object(check_invalid_response, "INCIDENT_ROOT", Path(tmp)):
            reason, notice = hook.inspect({**data, "cwd": tmp})
            self.assertEqual(reason, hook.REASON_EMPTY)
            self.assertRegex(notice, r"^사건 감지\(앞으로는?\): \[(\d{8}-\d{4}-[0-9a-f]{8})\]\(\.agent-logs/incident/\1\.md\)$")
            self.assertEqual(hook.inspect({"last_assistant_message": GOOD, "cwd": tmp})[1], "")

    # 검토 제목, 추가 제안, 에이전트 제안이 든 응답은 http 링크가 없으면 block하고, 링크가 있거나 그런 표현이 없는 응답은 통과한다.
    def test_proposal_needs_web_link(self):
        # 괄호 개수가 있는 검토 제목도 링크가 없으면 block한다.
        self.assertEqual(block("## 검토 1 (1/1)\n\n에이전트 제안은 안 1입니다."), hook.REASON_LINK)
        review = "## 검토 1\n\n에이전트 제안은 안 1입니다."
        extra = "## 추가 제안\n\n- 검색 절차를 줄입니다."
        for text in (review, extra):
            self.assertEqual(block(text), hook.REASON_LINK, text)
            self.assertIsNone(block(text, stop_hook_active=True))  # 재작성 뒤에는 한 번만 block한다.
        self.assertIsNone(block(review + "\n\n출처: https://code.claude.com/docs/en/hooks"))
        self.assertIsNone(block(GOOD))

    # 두 runner의 block 출력에 systemMessage가 없는지 확인한다. Codex block reason은 Hook feedback으로 표시된다.
    def test_codex_block_has_system_message(self):
        for runner, expected in (("codex", False), ("claude", False)):
            out = Stream()
            with unittest.mock.patch.object(sys, "argv", ["check_workflow_response.py", "--runner", runner]), \
                 unittest.mock.patch.object(sys, "stdin", Stream(json.dumps(
                     {"last_assistant_message": ANNOUNCE, "cwd": PLAIN}))), \
                 unittest.mock.patch.object(sys, "stdout", out):
                hook.main()
            result = json.loads(out.getvalue())
            self.assertEqual(result["decision"], "block")
            self.assertEqual("systemMessage" in result, expected)


if __name__ == "__main__":
    unittest.main()
