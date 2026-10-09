"""save_agent_result.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트, 공용 모듈, check_tool_use 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import save_agent_result as hook
import sub_docs

RESEARCH = """# 조사 문서: t

## 조사·반증 루프

| 회차 | 반증 건수 | 정지 여부 |
| ---- | --------- | --------- |

## 조사 단계 원문

### 최종 조사 원문: 조사 단계

```
기록 없음
```

## 후보 비교
"""


class SaveResult(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "log" / "state"
        self.state.mkdir(parents=True)
        self.doc = self.state / "t-research.md"
        self.doc.write_text(RESEARCH, encoding="utf-8")
        self.patches = [patch.object(sub_docs, "project_root", return_value=self.root)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def stop(self, role, prompt, message):
        path = self.root / f"{role}.jsonl"
        path.write_text(json.dumps({"type": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        hook.save({"agent_type": role, "agent_transcript_path": str(path), "session_id": "s1",
                   "last_assistant_message": message}, "claude")
        return self.doc.read_text(encoding="utf-8")

    def test_researcher_result_saved(self):
        text = self.stop("nico", "작업 종류: 조사\n조사 문서: log/state/t-research.md", "후보 A, 후보 B")
        self.assertIn("```\n후보 A, 후보 B\n```", text)
        self.assertNotIn("회차 반증", text)

    def test_refute_saved_with_loop_row(self):
        (self.state / ".active").mkdir()
        (self.state / ".active" / "s1.json").write_text('{"researcher": "t"}', encoding="utf-8")
        text = self.stop("ricky", "작업 종류: 반증\n주장: x", "- 주장 1: 반증 | 출처: https://a.example")
        self.assertIn("### 1회차 반증 요청\n\n```\n작업 종류: 반증\n주장: x\n```", text)
        self.assertIn("### 1회차 반증 원문\n\n```\n- 주장 1: 반증 | 출처: https://a.example\n```", text)
        self.assertIn("| 1 | 1 | 계속 |", text)
        text = self.stop("ricky", "작업 종류: 반증\n주장: y", "- 주장 1: 지지 | 출처: https://b.example")
        self.assertIn("### 2회차 반증 요청\n\n```\n작업 종류: 반증\n주장: y\n```", text)
        self.assertIn("| 2 | 0 | 정지(반증 0건) |", text)
        self.assertLess(text.index("### 2회차 반증 원문"), text.index("## 후보 비교"))
        self.assertIn("주장 1: 반증", text)

    def test_fenced_result_replaced_whole(self):
        first = "결과\n```py\nx = 1\n```\n끝"
        text = self.stop("nico", "작업 종류: 조사\n조사 문서: log/state/t-research.md", first)
        self.assertIn("````\n" + first + "\n````", text)
        text = self.stop("nico", "작업 종류: 조사\n조사 문서: log/state/t-research.md", "두 번째")
        self.assertIn("최종 조사 원문: 조사 단계\n\n```\n두 번째\n```\n\n## 후보 비교", text)
        self.assertNotIn("x = 1", text)
        self.assertNotIn("끝", text)

    def stop_with_handback(self, role, prompt, handback, message):
        """SubagentHandback 호출이 기록된 하위 기록으로 SubagentStop을 넣는다."""
        path = self.root / f"{role}-hb.jsonl"
        rows = [{"type": "user", "message": {"content": prompt}}]
        if handback:
            rows.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "SubagentHandback", "input": {"message": handback}}]}})
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        hook.save({"agent_type": role, "agent_transcript_path": str(path), "session_id": "s1",
                   "last_assistant_message": message}, "claude")
        return self.doc.read_text(encoding="utf-8")

    def test_followup_does_not_overwrite_report(self):
        ask = "작업 종류: 조사\n조사 문서: log/state/t-research.md"
        text = self.stop_with_handback("nico", ask, "조사 보고 원문", "")
        self.assertIn("```\n조사 보고 원문\n```", text)
        text = self.stop_with_handback("nico", ask, "조사 보고 원문", "이번 알림에서 새로 처리할 내용은 없습니다.")
        self.assertIn("```\n조사 보고 원문\n```", text)
        self.assertNotIn("새로 처리할", text)
        text = self.stop_with_handback("nico", ask, "재조사 보고", "후속 응답")
        self.assertIn("```\n재조사 보고\n```", text)
        self.assertNotIn("조사 보고 원문", text)

    def test_refute_blocked_then_rewritten_saves_once(self):
        (self.state / ".active").mkdir()
        (self.state / ".active" / "s1.json").write_text('{"researcher": "t"}', encoding="utf-8")
        ask = "작업 종류: 반증\n주장: x"
        self.assertEqual(self.stop_with_handback("ricky", ask, "", "결론만"), RESEARCH)
        good = "- 주장 1: 지지 | 출처: https://a.example"
        self.stop_with_handback("ricky", ask, good, "")
        text = self.stop_with_handback("ricky", ask, good, "후속")
        self.assertEqual(text.count("| 1 | 0 |"), 1)
        self.assertEqual(text.count("회차 반증 요청"), 1)
        self.assertEqual(text.count("회차 반증 원문"), 1)

    def test_review_not_saved(self):
        text = self.stop("ricky", "작업 종류: 검수\n입력 문서: log/state/t-input.md", "PASS")
        self.assertEqual(text, RESEARCH)


if __name__ == "__main__":
    unittest.main()
