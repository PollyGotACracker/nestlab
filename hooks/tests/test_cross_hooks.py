"""생성기 템플릿 출력이 해당 hook을 통과하는지와 차단 사유가 지시하는 호출을 다른 hook이 허용하는지 확인하는 교차 시험.

템플릿 문구와 질문 표본의 원문은 고정하지 않는다. 템플릿이나 차단 사유가 바뀌어도 hook끼리 맞으면 통과한다.
"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import isolate_incidents  # noqa: F401 사건 문서를 저장소에 쓰지 않는다.
import check_workflow_response as stop_hook
import check_tool_use as tool_hook
import sub_docs
import sub_scope
from sub_task_docs import create

TASK = "20261124-120000-cross"
QUESTION_TOOL = "AskUserQuestion"


# 템플릿의 주석을 지우고 자리표시자를 값으로 채운다. 출처 자리는 URL, 번호 자리는 1이다.
def fill(text: str) -> str:
    text = sub_docs.strip_comments(text).replace("<파일:행 | URL>", "https://example.com/a")
    return re.sub(r"<[^<>\n]+>", lambda m: "1" if m[0] == "<번호>" else "값", text)


# 대화 기록 줄 목록을 JSONL 파일로 쓰고 경로를 반환한다.
def save(path: Path, rows: list) -> str:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return str(path)


# 사용자 메시지와 에이전트 텍스트 기록 줄이다.
user = lambda text: {"type": "user", "message": {"content": text}}
agent = lambda text: {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


class CrossHooks(unittest.TestCase):
    # 생성기로 만든 작업 문서와 director 세션 환경을 준비한다.
    def setUp(self):
        role = patch.dict(os.environ, {"NESTLAB_AGENT": "director"})
        role.start()
        self.addCleanup(role.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        create(self.root, TASK, "교차", "plan")
        self.folder = self.root / ".agent-logs" / "task" / TASK
        self.proposal = fill(sub_docs.template_text("info-confirmed"))
        self.seq = 0

    # 대화 기록 파일 경로를 새로 만든다.
    def transcript(self, rows: list) -> str:
        self.seq += 1
        return save(self.root / f"t{self.seq}.jsonl", rows)

    # 질문 템플릿 줄로 만든 질문 도구 입력이다. kind는 질문 머리말이다.
    def question(self, text: str, labels=("확정", "폐기")) -> dict:
        return {"questions": [{"id": "q1", "header": "결정", "question": text,
                               "options": [{"label": l, "description": "값"} for l in labels]}]}

    # director 질문 도구 호출을 PreToolUse hook으로 판정한다. 허용이면 None이다.
    def ask(self, text: str):
        data = {"tool_name": QUESTION_TOOL, "tool_input": self.question(text), "cwd": str(self.root), "agent_type": "director",
                "transcript_path": self.transcript([user("시작"), agent(self.proposal)])}
        return tool_hook.check(data, "claude")

    # director의 작업 문서 쓰기를 PreToolUse hook으로 판정한다. 허용이면 None이다.
    def write(self, name: str, content: str):
        data = {"tool_name": "Write", "tool_input": {"file_path": str(self.folder / name), "content": content},
                "cwd": str(self.root), "agent_type": "director", "transcript_path": self.transcript([user("시작")])}
        return tool_hook.check(data, "claude")

    # 생성기 템플릿으로 만든 작업 문서 네 종류의 원문 쓰기는 PreToolUse hook을 통과한다.
    def test_generated_docs_pass_write_hook(self):
        for name in ("status.md", "spec.md", "research.md", "refute.md"):
            with self.subTest(name=name):
                self.assertIsNone(self.write(name, (self.folder / name).read_text(encoding="utf-8")))

    # question-confirmed 템플릿 질문 줄과 info-confirmed 확정안은 해당 hook을 통과한다.
    def test_confirmed_templates_pass_hooks(self):
        line = next(l for l in fill(sub_docs.template_text("question-confirmed")).splitlines() if l.startswith("요구사항 "))
        self.assertIsNone(self.ask(line))
        message = {"last_assistant_message": self.proposal, "cwd": str(self.root), "agent_type": "director",
                   "transcript_path": self.transcript([user("시작"), agent(self.proposal)])}
        self.assertIsNone(stop_hook.check(message, "claude", record=False))

    # Stop hook이 질문 문장으로 끝난 응답에 지시하는 질문 종류를 질문 hook이 허용하고, 사유가 질문 hook이 막는 종류를 지시하면 실패한다.
    def test_stop_question_reason_is_allowed_by_question_hook(self):
        ending = "어느 쪽으로 할까요?"
        data = {"last_assistant_message": ending, "cwd": str(self.root), "agent_type": "director",
                "transcript_path": self.transcript([user("시작"), agent(ending)])}
        reason = stop_hook.check(data, "claude", record=False)
        self.assertIsNotNone(reason)
        self.assertNotIn("검토", reason)
        kind = re.search(r"(\S+) <번호>\.", reason)[1].strip("`")
        self.assertIsNone(self.ask(f"{kind} 1. 값: 값."))

    # 확정 답을 기록하면 경과 문서 요구사항 확인 절에 요구사항 번호 줄이 생기고, 그 절을 director가 직접 바꾸는 쓰기는 막힌다.
    def test_confirmed_answer_record_is_locked(self):
        line = next(l for l in fill(sub_docs.template_text("question-confirmed")).splitlines() if l.startswith("요구사항 "))
        ask = {"type": "tool_use", "id": "q1", "name": QUESTION_TOOL, "input": self.question(line)}
        rows = [user("시작"), agent(self.proposal), {"type": "assistant", "message": {"content": [ask]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "q1", "content": "답"}]},
                 "toolUseResult": {"answers": {line: "확정"}}}]
        data = {"last_assistant_message": "기록했습니다.", "cwd": str(self.root), "agent_type": "director",
                "transcript_path": self.transcript(rows)}
        stop_hook.check(data, "claude")
        status = (self.folder / "status.md").read_text(encoding="utf-8")
        self.assertRegex(sub_docs.section(status, "요구사항 확인"),
                         r"(?m)^- 요구사항 1\. 값: 확정 \(사용자 확인 시각: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\)$")
        self.assertEqual(sub_docs.review_pending(status), [])
        self.assertEqual(sub_docs.review_done_numbers(status), {"1"})
        self.assertIsNone(self.write("status.md", status))
        self.assertEqual(self.write("status.md", status.replace("값: 확정 (", "값: 폐기 (")), sub_scope.REASON_LOCKED)

    # 요구사항 확인 기록이 없는 요구사항 절에 director가 사용자 확인 시각을 직접 쓰는 명세 쓰기는 막힌다.
    def test_spec_stamp_without_record_is_denied(self):
        spec = (self.folder / "spec.md").read_text(encoding="utf-8")
        forged = spec.replace("### <번호>. <요구사항 이름>", "### 1. 이름").replace("<확정 답의 기록 시각>", "2026-11-24 00:00:00")
        self.assertEqual(self.write("spec.md", forged), sub_scope.REASON_SPEC_LOCKED)


if __name__ == "__main__":
    unittest.main()
