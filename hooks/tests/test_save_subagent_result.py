"""save_subagent_result.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트, 공용 모듈, check_tool_use 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import save_subagent_result as hook
import sub_docs

RESEARCH = """# 조사 문서: t

## 조사 단계 원문

### 최종 조사 원문: 조사 단계

```
기록 없음
```

## 후보 비교
"""

REFUTE_DOC = """# 반증 문서: t

## 주장 목록

기록 없음

## 조사·반증 루프

| 회차 | 반증 건수 | 정지 여부 |
| ---- | --------- | --------- |

## 회차 기록

```
기록 없음
```

## 후보 비교
"""

ASK_RESEARCH = "작업 종류: 조사\n조사 문서: log/task/t/research.md"
ASK_REFUTE = "작업 종류: 반증\n반증 문서: log/task/t/refute.md"


class SaveResult(unittest.TestCase):
    # 임시 저장소와 조사 문서를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "log" / "task" / "t"
        self.state.mkdir(parents=True)
        self.doc = self.state / "research.md"
        self.doc.write_text(RESEARCH, encoding="utf-8")
        self.refute = self.state / "refute.md"
        self.refute.write_text(REFUTE_DOC, encoding="utf-8")
        self.patches = [patch.object(sub_docs, "project_root", return_value=self.root)]
        for p in self.patches:
            p.start()

    # patch와 임시 저장소를 정리한다.
    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    # 역할 기록을 만들고 종료 hook 저장을 실행해 조사 문서 본문을 반환하는 보조 함수이다.
    def stop(self, role, prompt, message):
        path = self.root / f"{role}.jsonl"
        path.write_text(json.dumps({"type": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        hook.save({"agent_type": role, "agent_transcript_path": str(path), "session_id": "s1",
                   "last_assistant_message": message}, "claude")
        return (self.refute if role == "ricky" else self.doc).read_text(encoding="utf-8")

    # 세션 기록 파일 경로가 든 researcher 보고도 경로 검사 없이 원문으로 저장하는지 확인한다.
    def test_report_with_session_path_saved(self):
        text = self.stop("nico", ASK_RESEARCH, "근거는 a.jsonl 기록에 있다")
        self.assertIn("근거는 a.jsonl 기록에 있다", text)

    # researcher 결과가 최종 조사 원문 칸에 저장되는지 확인한다.
    def test_researcher_result_saved(self):
        text = self.stop("nico", ASK_RESEARCH, "후보 A, 후보 B")
        self.assertIn("```\n후보 A, 후보 B\n```", text)
        self.assertNotIn("회차 반증", text)

    # 반증 결과가 회차 블록과 루프 표 행으로 저장되는지 확인한다.
    def test_refute_saved_with_loop_row(self):
        ask = ASK_REFUTE
        text = self.stop_with_handback("ricky", ask, "- 주장 1: 반증 | 출처: https://a.example", "")
        self.assertIn(f"### 1회차 반증 요청\n\n```\n{ask}\n```", text)
        self.assertIn("### 1회차 반증 원문\n\n```\n- 주장 1: 반증 | 출처: https://a.example\n```", text)
        self.assertIn("| 1 | 1 | 계속 |", text)
        text = self.stop_with_handback("ricky", ask, "- 주장 1: 지지 | 출처: https://b.example", "")
        self.assertIn(f"### 2회차 반증 요청\n\n```\n{ask}\n```", text)
        self.assertIn("| 2 | 0 | 정지(반증 0건) |", text)
        self.assertLess(text.index("### 2회차 반증 원문"), text.index("## 후보 비교"))
        self.assertIn("주장 1: 반증", text)

    # 재조사 결과가 가로줄 뒤에 덧붙는지 확인한다.
    def test_reresearch_appended_after_rule(self):
        first = "결과\n```py\nx = 1\n```\n끝"
        text = self.stop("nico", ASK_RESEARCH, first)
        self.assertIn("````\n" + first + "\n````", text)
        text = self.stop("nico", ASK_RESEARCH + "\n항목: 검토 3", "두 번째")
        self.assertIn("````\n" + first + "\n\n---\n\n두 번째\n````\n\n## 후보 비교", text)

    # SubagentHandback 호출이 기록된 하위 기록으로 SubagentStop을 넣는다.
    def stop_with_handback(self, role, prompt, handback, message):
        path = self.root / f"{role}-hb.jsonl"
        rows = [{"type": "user", "message": {"content": prompt}}]
        if handback:
            rows.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "SubagentHandback", "input": {"message": handback}}]}})
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        hook.save({"agent_type": role, "agent_transcript_path": str(path), "session_id": "s1",
                   "last_assistant_message": message}, "claude")
        return (self.refute if role == "ricky" else self.doc).read_text(encoding="utf-8")

    # 뒤따르는 후속 응답이 보고를 덮어쓰지 않는지 확인한다.
    def test_followup_does_not_overwrite_report(self):
        ask = ASK_RESEARCH
        text = self.stop_with_handback("nico", ask, "조사 보고 원문", "")
        self.assertIn("```\n조사 보고 원문\n```", text)
        text = self.stop_with_handback("nico", ask, "조사 보고 원문", "이번 알림에서 새로 처리할 내용은 없습니다.")
        self.assertIn("```\n조사 보고 원문\n```", text)
        self.assertNotIn("새로 처리할", text)
        self.assertEqual(text.count("조사 보고 원문"), 1)
        text = self.stop_with_handback("nico", ask, "재조사 보고", "후속 응답")
        self.assertIn("```\n조사 보고 원문\n\n---\n\n재조사 보고\n```", text)

    # 판정 줄이 없는 반증 결과는 저장하지 않는지 확인한다.
    def test_refute_without_verdict_line_not_saved(self):
        ask = ASK_REFUTE
        report = "판정 줄 없는 보고"
        self.stop_with_handback("ricky", ask, report, "")
        text = self.stop_with_handback("ricky", ask, report, "후속")
        self.assertNotIn(report, text)
        self.assertEqual(text, REFUTE_DOC)

    # Handback 저장 뒤 SubagentStop이 와도 회차 1개이고, 반증 블록은 원문 코드 블록 밖에 붙는다.
    def test_refute_recorded_once_outside_code_block(self):
        import save_handback_report
        ask = ASK_REFUTE
        report = "반증 없음\n- 주장 1: 지지 | 출처: https://a.example"
        for stop_message, handback in (("반증 없음\n- 주장 1: 지지 | 출처: https://a.example", True),
                                       ("다른 문구의 마지막 응답", True), ("다른 문구의 마지막 응답", False)):
            with self.subTest(stop_message=stop_message, handback=handback):
                self.refute.write_text(REFUTE_DOC.replace("기록 없음\n```", "원문\n## 조사 결과: 가짜 제목\n본문\n```"), encoding="utf-8")
                path = self.root / "ricky-hb.jsonl"
                rows = [{"type": "user", "message": {"content": ask}}]
                if handback:
                    rows.append({"type": "assistant", "message": {"content": [
                        {"type": "tool_use", "name": "SubagentHandback", "input": {"message": report}}]}})
                path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
                base = {"agent_type": "ricky", "agent_transcript_path": str(path), "session_id": "s1"}
                if handback:  # 실제 순서: PreToolUse(SubagentHandback)가 먼저 저장한다.
                    self.assertIsNotNone(save_handback_report.replace(
                        {**base, "tool_name": "SubagentHandback", "tool_input": {"message": report}}))
                hook.save({**base, "last_assistant_message": stop_message}, "claude")
                text = self.refute.read_text(encoding="utf-8")
                self.assertEqual(text.count("| 1 |") + text.count("| 2 |"), 1 if handback else 0)
                self.assertEqual(text.count("회차 반증 원문"), 1 if handback else 0)
                if handback:
                    self.assertGreater(text.index("### 1회차 반증 원문"), text.index("본문\n```"))
                    self.assertLess(text.index("### 1회차 반증 원문"), text.index("## 후보 비교"))

    # 저장할 칸이 없으면 칸을 만들어 저장하는지 확인한다.
    def test_missing_slot_created(self):
        ask = ASK_RESEARCH
        self.doc.write_text("# 조사 문서: t\n\n## 조사 단계 원문\n\n## 후보 비교\n", encoding="utf-8")
        text = self.stop("nico", ask, "후보 A")
        self.assertIn("### 최종 조사 원문\n\n```\n후보 A\n```", text)
        self.assertLess(text.index("### 최종 조사 원문"), text.index("## 후보 비교"))
        self.doc.write_text("# 조사 문서: t\n", encoding="utf-8")
        self.assertIn("## 조사 단계 원문\n\n### 최종 조사 원문\n\n```\n후보 B\n```", self.stop("nico", ask, "후보 B"))

    # reviewer 검증 결과는 저장하지 않는지 확인한다.
    def test_review_not_saved(self):
        text = self.stop("ricky", "작업 종류: 검증\n명세 문서: log/task/t/spec.md", "PASS")
        self.assertEqual(text, REFUTE_DOC)
        self.assertEqual(self.doc.read_text(encoding="utf-8"), RESEARCH)

    # 조사 결과의 주장 목록이 반증 문서의 주장 목록 절에 저장되는지 확인한다.
    def test_claims_written_to_refute_doc(self):
        self.stop("nico", ASK_RESEARCH, "후보 A\n- 주장 1: 사실 하나 (출처: https://a.example)")
        claims = self.refute.read_text(encoding="utf-8")
        self.assertIn("## 주장 목록\n\n- 주장 1: 사실 하나 (출처: https://a.example)\n\n## 조사·반증 루프", claims)


if __name__ == "__main__":
    unittest.main()
