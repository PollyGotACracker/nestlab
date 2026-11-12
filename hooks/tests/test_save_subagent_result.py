"""save_subagent_result.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트, 공용 모듈, check_tool_use 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import save_subagent_result as hook
import sub_docs

RESEARCH = """# 조사 문서: t

## 조사 계획

- 제외 범위: 없음

### 조사 1. 구조 (문제 1)

- 질문: 구조는 무엇입니까?

## 조사 결과

### 조사 <번호>. <항목명> 결과

- 결론: <추천 안 번호와 근거>
- 이전 반증 반영: <해당 없음>

#### 안 <번호>. <안 이름>

- 적용할 작업: <대상>
- 판단 기준 충족: <기준>
- 장점: <장점>
- 단점: <단점>
- 현재 동작: <동작>
- 계획 초안: <초안>

#### 주장 목록

- 주장 <번호>: <사실>

#### 한계

- 한계: <내용>

## 후보 비교
"""

REFUTE_DOC = """# 반증 문서

- 작업 ID: t

## 주장 목록

- 주장 <번호>: <사실>

## 조사 및 반증 루프

| 회차   | 반증 건수 | 정지 여부 |
| ------ | --------- | --------- |
| <회차> | <건수>    | <계속>    |

## 회차 결과

### <n>회차 반증 결과

- 결론: <지지 n건>

#### 주장 판정

- 주장 <번호>: <판정> | 출처: <출처>

#### 열람 범위

- 열람 범위: <범위>

#### 한계

- 한계: <내용>

## 후보 비교
"""

ASK_RESEARCH = "task: research\ninput: log/task/t/research.md"
ASK_REFUTE = "task: refute\ninput: log/task/t/refute.md"


# 조사 항목 하나의 결과 보고이다. 칸 값은 override로 바꾼다.
def research_report(number=1, claim="- 주장 1: [조사 1. 구조는 무엇입니까?] 사실 하나 (출처: https://a.example)", **override):
    fields = {"결론": "안 1을 추천합니다.", "적용할 작업": "hooks/a.py를 고칩니다. (사용자 지시)", "판단 기준 충족": "충족합니다.",
              "장점": "단순합니다. (출처: https://a.example)", "단점": "큽니다. (출처: https://a.example)",
              "현재 동작": "차단합니다. (출처: https://a.example)", "계획 초안": "한 단계로 처리합니다.", **override}
    return f"""### 조사 {number}. 구조 결과

- 결론: {fields['결론']}
- 이전 반증 반영: 해당 없음

#### 안 1. 첫 안

- 적용할 작업: {fields['적용할 작업']}
- 판단 기준 충족: {fields['판단 기준 충족']}
- 장점: {fields['장점']}
- 단점: {fields['단점']}
- 현재 동작: {fields['현재 동작']}
- 계획 초안: {fields['계획 초안']}

#### 주장 목록

{claim}

#### 한계

- 한계: 없음
"""


# 반증 회차 하나의 결과 보고이다.
def refute_report(verdicts="- 주장 1: 지지 | 출처: https://a.example", n=1, scope="원출처 전체"):
    return f"""### {n}회차 반증 결과

- 결론: 판정을 마쳤습니다.

#### 주장 판정

{verdicts}

#### 열람 범위

- 열람 범위: {scope}

#### 한계

- 한계: 없음
"""


class SaveResult(unittest.TestCase):
    # 임시 저장소와 조사 문서를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.parent = self.root / "parent.jsonl"
        self.parent.write_text(json.dumps({"payload": {"agent_role": "director"}}) + "\n", encoding="utf-8")
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

    # 역할 기록을 만들고 종료 hook 저장을 실행해 대상 문서 본문을 반환하는 보조 함수이다.
    def stop(self, role, prompt, message, handback=""):
        path = self.root / f"{role}.jsonl"
        rows = [{"type": "user", "message": {"content": prompt}}]
        if handback:
            rows.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "SubagentHandback", "input": {"message": handback}}]}})
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        self.reason = hook.save({"agent_type": role, "agent_transcript_path": str(path), "session_id": "s1",
                                 "transcript_path": str(self.parent), "last_assistant_message": message}, "claude")
        return (self.refute if role == "ricky" else self.doc).read_text(encoding="utf-8")

    # 모든 칸이 채워진 조사 보고는 코드 블록 없이 조사 결과 절에 삽입되고 양식 자리표시자 블록은 지워지는지 확인한다.
    def test_filled_research_inserted_without_code_block(self):
        report = research_report()
        text = self.stop("nico", ASK_RESEARCH, report)
        self.assertIsNone(self.reason)
        self.assertIn("## 조사 결과\n\n" + report.strip(), text)
        self.assertNotIn("```", text)
        self.assertNotIn("<번호>", text)
        self.assertLess(text.index("### 조사 1. 구조 결과"), text.index("## 후보 비교"))

    # 후보 안 블록 칸이 비거나 자리표시자가 남은 보고는 사유와 함께 막고 조사 문서를 바꾸지 않는지 확인한다.
    def test_blank_or_placeholder_field_blocked(self):
        for override, expected in (({"단점": ""}, "단점"), ({"장점": "<장점> (출처: https://a.example)"}, "자리표시자"),
                                   ({"계획 초안": ""}, "계획 초안")):
            with self.subTest(override=override):
                text = self.stop("nico", ASK_RESEARCH, research_report(**override))
                self.assertIn("다시 제출", self.reason)
                self.assertIn(expected, self.reason)
                self.assertEqual(text, RESEARCH)
        # 결론 칸이 비어도 막는다.
        self.stop("nico", ASK_RESEARCH, research_report(결론=""))
        self.assertIn("결론", self.reason)

    # 안 블록이나 주장 목록이 없는 보고와 요청한 항목의 결과 블록이 없는 보고는 막고, 결론 불가 보고는 안 블록 없이 허용하는지 확인한다.
    def test_missing_blocks_blocked_and_inconclusive_allowed(self):
        self.stop("nico", ASK_RESEARCH, research_report(number=2))
        self.assertIn("조사 1 결과 블록이 없습니다", self.reason)
        self.stop("nico", ASK_RESEARCH, research_report(claim=""))
        self.assertIn("주장 목록", self.reason)
        inconclusive = ("### 조사 1. 구조 결과\n\n- 결론: 결론 불가입니다. 근거가 부족합니다.\n- 이전 반증 반영: 해당 없음\n\n"
                        "#### 주장 목록\n\n- 주장 1: [조사 1. 구조는 무엇입니까?] 사실 (출처: https://a.example)\n\n#### 한계\n\n- 한계: 자료 부족\n")
        text = self.stop("nico", ASK_RESEARCH, inconclusive)
        self.assertIsNone(self.reason)
        self.assertIn("결론 불가입니다", text)

    # 재조사 결과는 같은 번호의 기존 결과 블록을 바꾸고 다른 항목 블록은 유지하는지 확인한다.
    def test_reresearch_replaces_same_item_block(self):
        self.stop("nico", ASK_RESEARCH, research_report(결론="안 1을 추천합니다."))
        text = self.stop("nico", ASK_RESEARCH + "\nitem: research 1", research_report(결론="안 2로 바꿉니다."))
        self.assertEqual(text.count("### 조사 1. 구조 결과"), 1)
        self.assertIn("안 2로 바꿉니다.", text)
        self.assertNotIn("안 1을 추천합니다.", text)

    # 재조사 주장 번호가 기존 번호 뒤에 이어 붙는지 확인한다.
    def test_reresearch_claims_continue_numbering(self):
        self.stop("nico", ASK_RESEARCH, research_report(claim="- 주장 1: [조사 1. 질문] 하나 (출처: https://a.example)"))
        self.stop("nico", ASK_RESEARCH, research_report(claim="- 주장 1: [조사 1. 질문] 둘 (출처: https://b.example)", 결론="다시 봅니다."))
        claims = self.refute.read_text(encoding="utf-8")
        self.assertIn("- 주장 1: [조사 1. 질문] 하나", claims)
        self.assertIn("- 주장 2: [조사 1. 질문] 둘", claims)

    # 조사 결과의 주장 목록이 반증 문서의 주장 목록 절에 저장되는지 확인한다.
    def test_claims_written_to_refute_doc(self):
        self.stop("nico", ASK_RESEARCH, research_report())
        claims = self.refute.read_text(encoding="utf-8")
        self.assertIn("## 주장 목록\n\n- 주장 1: [조사 1. 구조는 무엇입니까?] 사실 하나 (출처: https://a.example)\n\n## 조사 및 반증 루프", claims)

    # 반증 문서가 없으면 주장 줄이 있는 조사 결과 저장 때 양식으로 만드는지 확인한다.
    def test_refute_doc_created_from_template(self):
        self.refute.unlink()
        self.stop("nico", ASK_RESEARCH, research_report())
        created = self.refute.read_text(encoding="utf-8")
        self.assertIn("- 작업 ID: t\n", created)
        self.assertIn("- 주장 1: [조사 1. 구조는 무엇입니까?] 사실 하나", created)

    # 모든 칸이 채워진 반증 보고는 회차 결과 절에 회차 번호로 삽입되고 루프 표 행이 추가되는지 확인한다.
    def test_refute_inserted_with_loop_row(self):
        self.refute.write_text(REFUTE_DOC.replace("- 주장 <번호>: <사실>", "- 주장 1: a"), encoding="utf-8")
        first = refute_report("- 주장 1: 반증 | 출처: https://a.example", n=9)
        text = self.stop("ricky", ASK_REFUTE, "", handback=first)
        self.assertIsNone(self.reason)
        self.assertIn("## 회차 결과\n\n### 1회차 반증 결과", text)
        self.assertNotIn("9회차", text)
        self.assertNotIn("<n>회차", text)
        self.assertNotIn("```", text)
        self.assertIn("| 1 | 1 | 계속 |", text)
        second = refute_report("- 주장 1: 지지 | 출처: https://b.example", n=2)
        text = self.stop("ricky", ASK_REFUTE, "", handback=second)
        self.assertIn("| 2 | 0 | 정지(반증 0건) |", text)
        self.assertLess(text.index("### 2회차 반증 결과"), text.index("## 후보 비교"))
        self.assertLess(text.index("### 1회차 반증 결과"), text.index("### 2회차 반증 결과"))

    # 반증 보고의 칸이 비었거나 판정 줄이 없으면 막고 반증 문서를 바꾸지 않는지 확인한다.
    def test_refute_blank_field_or_no_verdict_blocked(self):
        for report, expected in ((refute_report(scope=""), "열람 범위"), ("판정 줄 없는 보고", "블록이 없습니다"),
                                 (refute_report("- 주장 1: 지지 출처 없음"), "판정")):
            with self.subTest(expected=expected):
                text = self.stop("ricky", ASK_REFUTE, "", handback=report)
                self.assertIn(expected, self.reason)
                self.assertEqual(text, REFUTE_DOC)

    # 주장 목록 중 판정 없는 번호가 있으면 회차를 기록하지 않고 그 번호를 사유에 적는지 확인한다.
    def test_refute_missing_verdict_blocked(self):
        self.refute.write_text(REFUTE_DOC.replace("- 주장 <번호>: <사실>", "- 주장 1: a\n- 주장 2: b"), encoding="utf-8")
        reason = hook.write_result(self.refute, ASK_REFUTE, refute_report("- 주장 1: 지지 | 출처: https://a.example"), True)
        self.assertIn("주장 2 판정이 없습니다", reason)
        self.assertNotIn("1회차 반증 결과", self.refute.read_text(encoding="utf-8").split("## 회차 결과")[1].split("### <n>")[0])
        both = refute_report("- 주장 1: 지지 | 출처: https://a.example\n- 주장 2: 지지 | 출처: https://b.example")
        self.assertIsNone(hook.write_result(self.refute, ASK_REFUTE, both, True))
        self.assertIn("| 1 | 0 | 정지(반증 0건) |", self.refute.read_text(encoding="utf-8"))

    # Handback 저장 뒤 SubagentStop이 와도 회차 1개만 기록하는지 확인한다.
    def test_refute_recorded_once_with_followup_stop(self):
        import save_handback_report
        report = refute_report()
        for stop_message in (report, "다른 문구의 마지막 응답"):
            with self.subTest(stop_message=stop_message[:10]):
                self.refute.write_text(REFUTE_DOC, encoding="utf-8")
                path = self.root / "ricky-hb.jsonl"
                rows = [{"type": "user", "message": {"content": ASK_REFUTE}},
                        {"type": "assistant", "message": {"content": [
                            {"type": "tool_use", "name": "SubagentHandback", "input": {"message": report}}]}}]
                path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
                base = {"agent_type": "ricky", "agent_transcript_path": str(path), "session_id": "s1", "transcript_path": str(self.parent)}
                self.assertIsNotNone(save_handback_report.replace({**base, "tool_name": "SubagentHandback", "tool_input": {"message": report}}))
                hook.save({**base, "last_assistant_message": stop_message}, "claude")
                text = self.refute.read_text(encoding="utf-8")
                self.assertEqual(text.count("회차 반증 결과"), 1)
                self.assertEqual(text.count("| 1 |"), 1)

    # 뒤따르는 후속 응답이 보고를 덮어쓰지 않는지 확인한다.
    def test_followup_does_not_overwrite_report(self):
        report = research_report()
        text = self.stop("nico", ASK_RESEARCH, "", handback=report)
        self.assertEqual(text.count("### 조사 1. 구조 결과"), 1)
        text = self.stop("nico", ASK_RESEARCH, "이번 알림에서 새로 처리할 내용은 없습니다.", handback=report)
        self.assertNotIn("새로 처리할", text)
        self.assertEqual(text.count("### 조사 1. 구조 결과"), 1)
        self.assertEqual(self.refute.read_text(encoding="utf-8").count("사실 하나"), 1)

    # 작업 ID를 찾지 못한 director 배정 종료는 block 사유를 반환하고, director 배정이 아닌 실행과 이미 저장된 보고는 출력 없이 통과한다. (요구사항 27)
    def test_missing_task_id_blocked(self):
        path = self.root / "nico-no-id.jsonl"
        path.write_text(json.dumps({"type": "user", "message": {"content": "task: research"}}) + "\n", encoding="utf-8")
        data = {"agent_type": "nico", "agent_transcript_path": str(path), "session_id": "s1", "transcript_path": str(self.parent),
                "last_assistant_message": research_report()}
        self.assertIn("작업 ID를 찾지 못해 결과를 저장하지 못했습니다", hook.save(data, "claude"))
        self.assertIsNone(hook.save({**data, "transcript_path": str(self.root / "no-parent.jsonl")}, "claude"))
        with patch.object(hook, "saved_report", return_value=True):
            self.assertIsNone(hook.save(data, "claude"))

    # 메인 세션 Stop 입력(두 runner 형식)에서 researcher 결과만 첫 요청의 조사 문서에 저장하는지 확인한다.
    def test_main_session_stop_saved_for_researcher_only(self):
        report = research_report()
        for runner in ("claude", "codex"):
            for role, saved in (("nico", True), ("buddy", False)):
                with self.subTest(runner=runner, role=role):
                    self.doc.write_text(RESEARCH, encoding="utf-8")
                    path = self.root / f"main-{runner}-{role}.jsonl"
                    if runner == "claude":
                        rows = [{"type": "user", "message": {"content": ASK_RESEARCH}}]
                    else:
                        rows = [{"type": "session_meta", "payload": {"agent_role": role}},
                                {"type": "response_item", "payload": {"type": "message", "role": "user",
                                 "content": [{"type": "input_text", "text": ASK_RESEARCH}]}}]
                    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
                    hook.save({"agent_type": role, "transcript_path": str(path), "session_id": "s1",
                               "last_assistant_message": report}, runner)
                    self.assertEqual("### 조사 1. 구조 결과" in self.doc.read_text(encoding="utf-8"), saved)

    # 부모 rollout에 agent_role이 없는 Codex 배정은 부모에 주입된 성향 문서가 director일 때만 저장하는지 확인한다.
    def test_codex_parent_without_role_uses_injected_persona(self):
        for name, saved in (("Rio", True), ("Nico", False)):
            with self.subTest(name=name), patch.dict("os.environ", {"NESTLAB_AGENT": "rio"}):
                context = f"# Persona\n\n---\ntitle: {name} persona\n"
                rows = [{"payload": {"source": "exec"}}, {"payload": {"content": [{"text": context}]}}]
                self.parent.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
                self.doc.write_text(RESEARCH, encoding="utf-8")
                self.assertEqual("### 조사 1. 구조 결과" in self.stop("nico", ASK_RESEARCH, research_report()), saved)

    # reviewer 검증 결과는 저장하지 않는지 확인한다.
    def test_review_not_saved(self):
        text = self.stop("ricky", "task: verify\ninput: log/task/t/spec.md", "PASS")
        self.assertEqual(text, REFUTE_DOC)
        self.assertEqual(self.doc.read_text(encoding="utf-8"), RESEARCH)

    # Stop 경로의 빈 보고는 block 사유를 반환하고 문서를 바꾸지 않는다.
    def test_stop_empty_report_blocked(self):
        self.stop("nico", ASK_RESEARCH, "")
        self.assertIn("비어 있습니다", self.reason)
        self.assertEqual(self.doc.read_text(encoding="utf-8"), RESEARCH)

    # 저장 중 예외는 정상 제외와 달리 오류 종류가 든 block JSON을 출력한다.
    def test_main_save_error_reports_error_type(self):
        out = io.StringIO()
        with patch.object(hook, "save", side_effect=OSError("x")), patch.object(sys, "argv", ["h", "--runner", "claude"]), \
                patch.object(sys, "stdin", io.StringIO("{}")), patch.object(sys, "stdout", out), patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(hook.main(), 0)
        self.assertIn("OSError", json.loads(out.getvalue())["reason"])


if __name__ == "__main__":
    unittest.main()
