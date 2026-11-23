"""replace_blocked_response.py 동작 테스트"""
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

import replace_blocked_response as hook

PLAIN = tempfile.mkdtemp()  # 질문 문장 검사는 .agent-logs/state가 있는 저장소에서만 적용된다.
(Path(PLAIN) / ".agent-logs" / "state").mkdir(parents=True)
QUESTION = "설치 상태를 확인하겠습니다."  # 예고로 끝난 응답이며 Stop에서 차단되는 응답 미흡(REASON_EMPTY)이다.
GOOD = "수정했습니다.\n\n- `hooks/a.py`: 추가\n"


class Stream(io.StringIO):
    # 표준 입출력 인코딩 설정 호출을 받아 넘긴다.
    def reconfigure(self, **kwargs):
        pass


# batch 입력을 보내고 hook이 출력한 displayContent를 반환하는 보조 함수이다.
def send(message_id, delta, final, transcript=None):
    data = {"message_id": message_id, "index": 0, "final": final, "delta": delta, "cwd": PLAIN, "agent_type": "director"}
    if transcript:
        data["transcript_path"] = transcript
    out = Stream()
    with unittest.mock.patch.object(sys, "stdin", Stream(json.dumps(data))), \
         unittest.mock.patch.object(sys, "stdout", out):
        hook.main()
    return json.loads(out.getvalue())["hookSpecificOutput"]["displayContent"]


# 같은 text의 assistant 항목을 stop_reason마다 하나씩 순서대로 가진 transcript 파일을 만들고 경로를 반환하는 보조 함수이다.
def transcript(text, *stop_reasons):
    entries = [{"type": "assistant", "message": {"role": "assistant", "stop_reason": s,
                                                 "content": [{"type": "text", "text": text}]}}
               for s in stop_reasons]
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    f.write("잘린 줄{\n" + "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
    f.close()
    return f.name


class BlankLines(unittest.TestCase):
    # 백틱 코드 span 안의 꺾쇠괄호 형식 예시는 빈 칸이 아니다.
    def test_code_span_allowed(self):
        from sub_gate import blank_lines
        self.assertEqual(blank_lines("- 기대 결과: `<작업-id>` 형식으로 기록한다.\n"), [])

    # 코드 span 밖에 자리표시자가 남은 줄은 빈 칸이다.
    def test_placeholder_blank(self):
        from sub_gate import blank_lines
        text = "- 목표: <목표>\n- 기대 결과: `<a>` 와 <b>\n- 한계: 채움\n"
        self.assertEqual(blank_lines(text), ["- 목표: <목표>", "- 기대 결과: `<a>` 와 <b>"])


class Display(unittest.TestCase):
    # 통과한 응답의 구현 승인 요청 줄은 생성한 승인 요청 본문으로 바꿔 표시하고, 생성 검사가 막히면 원문을 표시한다.
    def test_request_line_replaced_with_generated_body(self):
        line = "구현 승인 요청: 20261116-060912-agent-api"
        with unittest.mock.patch.object(hook, "check", return_value=None), \
             unittest.mock.patch.object(hook, "approval_request", return_value=(None, "# 구현 승인 요청\n\n본문")):
            shown = send("request-1", line, True)
        self.assertEqual(shown, "# 구현 승인 요청\n\n본문" + hook.NOTICE_REQUEST)
        with unittest.mock.patch.object(hook, "check", return_value=None), \
             unittest.mock.patch.object(hook, "approval_request", return_value=("구현 승인 요청: 빈 칸", "")):
            self.assertEqual(send("request-2", line, True), line)

    def test_specialist_deltas_skip_batch_and_wait(self):
        for role in ("assistant", "worker", "documenter", "researcher", "reviewer", "unknown"):
            for final in (False, True):
                data = {"agent_type": role, "message_id": "specialist", "delta": "원문", "final": final,
                        "transcript_path": "missing.jsonl"}
                with unittest.mock.patch.object(hook, "batch_file") as batch, \
                        unittest.mock.patch.object(hook.time, "sleep") as sleep:
                    self.assertEqual(hook.display(data), "원문")
                    batch.assert_not_called()
                    sleep.assert_not_called()

    # 예고로 끝난 응답은 마지막 batch에서 원문으로 표시되고 앞 batch는 비어 있는지 확인한다.
    def test_announce_shown(self):
        self.assertEqual(send("viol-1", "첫 줄\n", False), "")
        self.assertEqual(send("viol-1", QUESTION, True), "첫 줄\n" + QUESTION)
        self.assertFalse(hook.batch_file("viol-1").exists())

    # 질문 문장 종료로 차단될 응답도 원문으로 표시된다.
    def test_other_block_shown(self):
        self.assertEqual(send("other-1", "어느 쪽으로 할까요?", True), "어느 쪽으로 할까요?")

    # 통과 응답은 마지막 batch에서 누적한 원문 전체로 표시되는지 확인한다.
    def test_pass_shows_collected_text(self):
        self.assertEqual(send("pass-1", "수정했습니다.\n\n", False), "")
        self.assertEqual(send("pass-1", "- `hooks/a.py`: 추가\n", False), "")
        self.assertEqual(send("pass-1", "", True), GOOD)

    # 질문 문장이어도 stop_reason이 tool_use인 중간 메시지는 원문으로 표시된다.
    def test_midturn_shown(self):
        text = "설치 상태를 확인하겠습니다."
        self.assertEqual(send("mid-1", text, True, transcript(text, "tool_use")), text)
        self.assertEqual(send("mid-2", QUESTION, True, transcript(QUESTION, "tool_use")), QUESTION)

    # stop_reason이 end_turn이거나 항목이 없으면 차단될 응답도 원문으로 표시된다.
    def test_final_replaced(self):
        self.assertEqual(send("end-1", QUESTION, True, transcript(QUESTION, "end_turn")), QUESTION)
        with unittest.mock.patch.object(hook.time, "sleep") as sleep:
            self.assertEqual(send("none-1", QUESTION, True, transcript("다른 글", "end_turn")), QUESTION)
        self.assertTrue(sleep.called)
        self.assertLessEqual(sum(c.args[0] for c in sleep.call_args_list), 3)

    # 같은 텍스트가 두 번 있으면 마지막 항목의 stop_reason으로 판정한다.
    def test_duplicate_uses_last(self):
        self.assertEqual(send("dup-1", QUESTION, True, transcript(QUESTION, "tool_use", "end_turn")), QUESTION)
        self.assertEqual(send("dup-2", QUESTION, True, transcript(QUESTION, "end_turn", "tool_use")), QUESTION)

    # 같은 text의 api-request-blob 항목이 뒤에 있어도 assistant 항목의 stop_reason으로 판정한다.
    def test_blob_entry_ignored(self):
        path = transcript(QUESTION, "tool_use")
        blob = {"type": "api-request-blob", "message": {"role": "assistant",
                                                        "content": [{"type": "text", "text": QUESTION}]}}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(blob, ensure_ascii=False) + "\n")
        self.assertEqual(send("blob-1", QUESTION, True, path), QUESTION)

    # 완료 알림이 없는 백그라운드 작업이 있으면 end_turn 응답도 원문으로 표시되고, 모두 완료됐거나 기록이 없어도 원문으로 표시된다.
    def test_background_pending(self):
        def with_lines(*lines):
            path = transcript(QUESTION, "end_turn")
            with open(path, "a", encoding="utf-8") as f:
                f.write("".join(l + "\n" for l in lines))
            return path
        agent = json.dumps({"toolUseResult": {"isAsync": True, "status": "async_launched", "agentId": "a1"}}, separators=(",", ":"))
        bash = json.dumps({"toolUseResult": {"backgroundTaskId": "b1"}}, separators=(",", ":"))
        done = lambda i: json.dumps({"message": {"content": f"<task-notification><task-id>{i}</task-id></task-notification>"}})
        self.assertEqual(send("bg-1", QUESTION, True, with_lines(agent)), QUESTION)
        self.assertEqual(send("bg-2", QUESTION, True, with_lines(bash)), QUESTION)
        self.assertEqual(send("bg-3", QUESTION, True, with_lines(agent, bash, done("a1"))), QUESTION)
        self.assertEqual(send("bg-4", QUESTION, True, with_lines(agent, bash, done("a1"), done("b1"))), QUESTION)
        self.assertEqual(send("bg-5", QUESTION, True, with_lines()), QUESTION)

    # 완료 알림이 없는 백그라운드 작업이 있어도 다짐 문장과 다짐이 없는 응답 모두 원문으로 표시된다.
    def test_background_pending_checks_pledge(self):
        path = transcript("x", "end_turn")
        agent = json.dumps({"toolUseResult": {"isAsync": True, "status": "async_launched", "agentId": "a9"}}, separators=(",", ":"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(agent + "\n")
        pledge = "다음 세션에서 같은 일이 반복되지 않도록 기록하겠습니다."
        self.assertEqual(send("pl-1", pledge, True, path), pledge)
        self.assertEqual(send("pl-2", "worker가 작업 중입니다.", True, path), "worker가 작업 중입니다.")

    # 판정이 사건 문서 기록이나 분석 실행을 일으키지 않는지 확인한다.
    def test_check_runs_without_recording(self):
        with unittest.mock.patch.object(hook, "check", return_value=None) as check:
            send("rec-1", GOOD, True)
        self.assertIs(check.call_args.kwargs["record"], False)


if __name__ == "__main__":
    unittest.main()
