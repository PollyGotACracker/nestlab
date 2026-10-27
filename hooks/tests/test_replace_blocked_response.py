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

PLAIN = tempfile.mkdtemp()  # 질문 문장 검사는 log/state가 있는 저장소에서만 적용된다.
(Path(PLAIN) / "log" / "state").mkdir(parents=True)
QUESTION = "어느 쪽으로 할까요?"
GOOD = "수정했습니다.\n\n- `hooks/a.py`: 추가\n"


class Stream(io.StringIO):
    # 표준 입출력 인코딩 설정 호출을 받아 넘긴다.
    def reconfigure(self, **kwargs):
        pass


# batch 입력을 보내고 hook이 출력한 displayContent를 반환하는 보조 함수이다.
def send(message_id, delta, final, transcript=None):
    data = {"message_id": message_id, "index": 0, "final": final, "delta": delta, "cwd": PLAIN}
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


class Display(unittest.TestCase):
    # 위반 응답은 마지막 batch에서 교체 문구로 표시되고 앞 batch는 비어 있는지 확인한다.
    def test_violation_replaced(self):
        # 질문 문장으로 끝나는 응답은 위반이다.
        self.assertEqual(send("viol-1", "첫 줄\n", False), "")
        self.assertEqual(send("viol-1", QUESTION, True), hook.REPLACEMENT)
        self.assertFalse(hook.batch_file("viol-1").exists())

    # 통과 응답은 마지막 batch에서 모은 원문 전체로 표시되는지 확인한다.
    def test_pass_shows_collected_text(self):
        self.assertEqual(send("pass-1", "수정했습니다.\n\n", False), "")
        self.assertEqual(send("pass-1", "- `hooks/a.py`: 추가\n", False), "")
        self.assertEqual(send("pass-1", "", True), GOOD)

    # 질문 문장이어도 stop_reason이 tool_use인 중간 메시지는 원문으로 표시된다.
    def test_midturn_shown(self):
        text = "설치 상태를 확인하겠습니다."
        self.assertEqual(send("mid-1", text, True, transcript(text, "tool_use")), text)
        self.assertEqual(send("mid-2", QUESTION, True, transcript(QUESTION, "tool_use")), QUESTION)

    # stop_reason이 end_turn이거나 항목이 없으면 기존 판정으로 교체된다.
    def test_final_replaced(self):
        self.assertEqual(send("end-1", QUESTION, True, transcript(QUESTION, "end_turn")), hook.REPLACEMENT)
        with unittest.mock.patch.object(hook.time, "sleep") as sleep:
            self.assertEqual(send("none-1", QUESTION, True, transcript("다른 글", "end_turn")), hook.REPLACEMENT)
        self.assertTrue(sleep.called)
        self.assertLessEqual(sum(c.args[0] for c in sleep.call_args_list), 3)

    # 같은 텍스트가 두 번 있으면 마지막 항목의 stop_reason으로 판정한다.
    def test_duplicate_uses_last(self):
        self.assertEqual(send("dup-1", QUESTION, True, transcript(QUESTION, "tool_use", "end_turn")), hook.REPLACEMENT)
        self.assertEqual(send("dup-2", QUESTION, True, transcript(QUESTION, "end_turn", "tool_use")), QUESTION)

    # 같은 text의 api-request-blob 항목이 뒤에 있어도 assistant 항목의 stop_reason으로 판정한다.
    def test_blob_entry_ignored(self):
        path = transcript(QUESTION, "tool_use")
        blob = {"type": "api-request-blob", "message": {"role": "assistant",
                                                        "content": [{"type": "text", "text": QUESTION}]}}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(blob, ensure_ascii=False) + "\n")
        self.assertEqual(send("blob-1", QUESTION, True, path), QUESTION)

    # 완료 알림이 없는 백그라운드 작업이 있으면 end_turn 위반 응답도 원문으로 표시되고, 모두 완료됐거나 기록이 없으면 교체된다.
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
        self.assertEqual(send("bg-4", QUESTION, True, with_lines(agent, bash, done("a1"), done("b1"))), hook.REPLACEMENT)
        self.assertEqual(send("bg-5", QUESTION, True, with_lines()), hook.REPLACEMENT)

    # 판정이 사건 문서 기록이나 분석 실행을 일으키지 않는지 확인한다.
    def test_check_runs_without_recording(self):
        with unittest.mock.patch.object(hook, "check", return_value=None) as check:
            send("rec-1", GOOD, True)
        self.assertIs(check.call_args.kwargs["record"], False)


if __name__ == "__main__":
    unittest.main()
