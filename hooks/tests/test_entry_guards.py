"""문서 구조, 원인 분석 선행 조건과 persona 주입의 회귀 검사."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS / "common"), str(HOOKS / "check_tool_use")]
from sub_docs import structure_reason, template_text, constraint_reason
from sub_document import Document
from sub_gate import check_causes, transition_reason
from sub_approval import apply_stage

CAUSE = """## 원인 분석
- 검사 누락: 필수 칸 검사 조건이 빠진 분기
  - 근거 내용: 정상 입력과 누락 입력을 실행해 차이를 확인했다.
  - 출처: hooks/common/sub_gate.py:361
  - 한계: 없음
"""


class Structure(unittest.TestCase):
    def test_templates_and_fenced_example(self):
        for kind in ("state", "input", "research", "refute"):
            text = template_text(kind)
            self.assertIsNone(structure_reason(text, kind), kind)
            self.assertIsNone(structure_reason(text + "\n```markdown\n## example\n```\n", kind))

    def test_extra_moved_duplicate_and_reordered_headings(self):
        text = template_text("state")
        for changed in (
            text + "\n## 임의 절\n",
            text.replace("## 작업 경과", "## 작업 경과\n### 최신 상태와 다음 작업"),
            text.replace("## 원인 분석", "### 원인 분석"),
            text + "\n  ## 임의 절\n",
            text + "\n임의 절\n======\n",
            text.replace("## 원인 분석", "## 검토 필요").replace("## 검토 필요", "## 원인 분석", 1),
        ):
            if changed != text:
                self.assertIsNotNone(structure_reason(changed, "state"))
        # 문제 정의는 명세 문서 요구사항 목록에서만 관리하므로 경과 문서의 `## 문제 정의` 절은 어느 깊이든 거부한다.
        self.assertIsNotNone(structure_reason(text.replace("## 원인 분석", "## 문제 정의\n\n- 문제 1. 범위\n\n## 원인 분석"), "state"))
        self.assertIsNotNone(structure_reason(text.replace("## 원인 분석", "### 문제 정의\n\n## 원인 분석"), "state"))

    def test_notice_does_not_quote_headings_or_template_fields(self):
        result = structure_reason(template_text("state") + "\n## 비공개 추가 제목\n", "state")
        self.assertIn("행", result)
        self.assertIn("log-status 양식", result)
        for forbidden in ("비공개 추가 제목", "원인 분석", "근거 출처", "##"):
            self.assertNotIn(forbidden, result)

    def test_dynamic_children_repeat_in_their_parent(self):
        text = """# 조사 문서
## 조사 결과
### 조사 1. 입력 결과
#### 안 1. 유지
#### 안 2. 변경
#### 주장 목록
#### 한계
### 조사 2. 출력 결과
#### 안 1. 유지
#### 주장 목록
#### 한계
"""
        self.assertIsNone(structure_reason(text, "research"))
        self.assertIsNotNone(structure_reason(text.replace("## 조사 결과", "## 조사 계획"), "research"))


class Causes(unittest.TestCase):
    def test_complete_cause(self):
        check_causes(Document(CAUSE))

    def test_missing_unknown_and_empty_evidence(self):
        for text in ("## 작업 경과\n- 구현", "## 원인 분석\n", CAUSE.replace("필수 칸 검사 조건이 빠진 분기", "미확인"),
                     CAUSE.replace("  - 출처:", "  - 근거 출처:"), CAUSE.replace("  - 한계: 없음", "  - 한계: 외부 형식 변경"),
                     CAUSE.replace("hooks/common/sub_gate.py:361", "<출처>"),
                     CAUSE.replace("정상 입력과 누락 입력을 실행해 차이를 확인했다.", "없음")):
            with self.subTest(text=text), self.assertRaises(ValueError):
                check_causes(Document(text))

    def test_transition_does_not_write_without_causes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "status.md"
            text = "# 경과 문서: 작업\n- 작업 단계: 계획\n## 원인 분석\n"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(apply_stage(path, "구현"), "계획")
            self.assertEqual(path.read_text(encoding="utf-8"), text)
            self.assertIn("log-status 양식", transition_reason(path, text.replace("계획", "구현"), text, ""))
            path.write_text("# 경과 문서: 작업\n- 작업 단계: 계획\n" + CAUSE, encoding="utf-8")
            self.assertEqual(apply_stage(path, "구현"), "구현")


class Persona(unittest.TestCase):
    def test_default_injects_fallback_and_unknown_fails_visibly(self):
        with tempfile.TemporaryDirectory() as folder:
            for role, expected in (("default", 0), ("not-registered", 1)):
                result = subprocess.run(
                    [sys.executable, "-B", str(HOOKS / "load_agent.py"), "--default-role", "buddy"],
                    input=json.dumps({"agent_type": role, "cwd": folder, "hook_event_name": "SessionStart"}),
                    capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, expected, result.stderr)
                if not expected:
                    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
                    self.assertIn("넌 Buddy이다", context)
                    self.assertIn("## Tone Guide", context)
                else:
                    self.assertIn("persona", result.stderr)


class Constraints(unittest.TestCase):
    def test_none_and_sourced_design_limits(self):
        for text in ("# t\n- 한계: 없음", "# t\n### 잠재적 위험\n- 없음",
                     "# t\n- 한계: 메모리 내 자료만 처리한다. (출처: hooks/example.py:12)",
                     "# t\n### 잠재적 위험\n- 외부 API의 요청 한도 초과 (출처: https://example.com/limits)"):
            self.assertIsNone(constraint_reason(text), text)

    def test_each_item_needs_its_own_source(self):
        for text in ("# t\n- 한계: 미검증 (출처: a.py:1)",
                     "# t\n- 한계: 입력 크기를 제한한다.",
                     "# t\n### 잠재적 위험\n- 외부 API 중단\n  - 출처: 설명만 있음",
                     "# t\n- 한계:\n  - 첫 제약 (출처: a.py:1)\n  - 둘째 제약"):
            self.assertIsNotNone(constraint_reason(text), text)

    def test_saved_result_blocks_are_masked(self):
        from sub_docs import mask_results
        bad = "- 한계: 설치 버전은 미확인이다."
        saved = f"# t\n\n## 조사 결과\n\n### 조사 2. 질문 결과\n\n#### 한계\n\n{bad}\n\n## 끝\n\n{bad}\n"
        masked = mask_results(saved)
        self.assertEqual(masked.count("\n"), saved.count("\n"))
        self.assertEqual(masked.count(bad), 1)
        self.assertIsNotNone(constraint_reason(masked))
        self.assertIsNone(constraint_reason(mask_results(saved.replace(f"## 끝\n\n{bad}\n", ""))))
        draft = "# t\n\n## 조사 결과\n\n### 조사 <번호>. <질문> 결과\n\n" + bad + "\n"
        self.assertIsNotNone(constraint_reason(mask_results(draft)))

    def test_template_placeholders_are_only_allowed_in_drafts(self):
        for kind in ("state", "input", "research", "refute"):
            text = template_text(kind)
            self.assertIsNone(constraint_reason(text, kind), kind)
            self.assertIsNotNone(constraint_reason(text), kind)


if __name__ == "__main__":
    unittest.main()
