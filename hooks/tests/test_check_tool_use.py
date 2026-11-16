"""check_tool_use.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트, 공용 모듈, check_tool_use 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import isolate_incidents  # noqa: F401 사건 문서를 저장소에 쓰지 않는다.
import check_tool_use as hook
import check_workflow_response  # noqa: F401 sub_scope의 지연 import가 patch된 project_root를 묶지 않게 먼저 불러온다.
import sub_approval
import sub_call
import sub_docs
import sub_input
import sub_role
import sub_path
import sub_scope
import sub_question
import sub_write

RESEARCH ="""# 조사 문서: t

## 조사 계획

### 조사 1. 도구 선택

## 조사 및 반증 루프

| 단계 | 회차 | 반증 건수 | 정지 여부 |
| ---- | ---- | --------- | --------- |

### 최종 조사 원문: 조사 단계

```
기록 없음
```

## 후보 비교

| 후보 | 비용 |
| ---- | ---- |
| A    | 낮음 |
"""

STATE_APPROVED = """# t

## 승인

### 실행 승인 범위

- 사용자 승인 상태: 승인
"""


PLAN_OK = """# t 입력

## 요구사항 목록

### 1. 예시

- 목표:
  - a
- 결과:
  - b
- 작업:
  - c
- 검증:
  - d (정적)
- 기대 결과:
  - e
- 한계:
  - 없음 (출처: hooks/a.py:1, 확인 방법: 테스트)
- Test Case:

| 종류 | 입력 | 커맨드 | 기대 결과 |
| --- | --- | --- | --- |
| 차단 | 누락 문서 | `python -m unittest` | deny |
| 허용 | 정상 문서 | `python -m unittest` | 통과 |

## 작업 계획

### 검증 계획

- 검증 커맨드
  - `python -m unittest`: 요구사항 1
"""


# Claude 대화 기록 한 줄을 JSON 문자열로 만든다.
def claude_line(role: str, text: str) -> str:
    content = text if role == "user" else [{"type": "text", "text": text}]
    return json.dumps({"type": role, "message": {"content": content}}, ensure_ascii=False)


# Codex rollout의 session_meta 줄을 JSON 문자열로 만든다.
def codex_meta(**payload) -> str:
    return json.dumps({"type": "session_meta", "payload": payload}, ensure_ascii=False)


# Codex rollout의 메시지 줄을 JSON 문자열로 만든다.
def codex_msg(role: str, text: str) -> str:
    return json.dumps({"type": "response_item", "payload": {"type": "message", "role": role,
                                                            "content": [{"type": "input_text", "text": text}]}},
                      ensure_ascii=False)


class WriteScope(unittest.TestCase):
    # 임시 저장소와 patch를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root / ".agent-logs" / "task" / "t"
        self.state.mkdir(parents=True, exist_ok=True)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.templates = self.root / "templates"
        self.templates.mkdir()
        for name in ("status", "spec", "research", "refute"):
            (self.templates / f"log-{name}.md").write_text("", encoding="utf-8")
        self.ostemp = tempfile.TemporaryDirectory()  # 프로젝트가 임시 폴더 안에 있어도 OS 임시 폴더 판정과 분리한다.
        # 단계 게이트는 test_sub_workflow의 완전한 세 문서 fixture로 검사한다.
        self.patches = [patch.object(hook, "call_reason", return_value=None),
                        # 문서 구조는 test_entry_guards에서 실제 양식으로 검증한다.
                        patch.object(sub_docs, "structure_reason", return_value=None),
                        patch.object(sub_scope, "transition_reason", return_value=None),
                        patch.object(sub_path, "TEMP", Path(self.ostemp.name).resolve()),
                        patch.object(sub_scope, "project_root", return_value=self.root),
                        # cwd가 없는 입력도 임시 저장소를 대상으로 삼아 실제 저장소의 작업 색인과 경과 문서를 읽거나 쓰지 않는다.
                        patch.object(sub_approval, "project_root", return_value=self.root),
                        patch.object(sub_question, "project_root", return_value=self.root),
                        patch.object(sub_path, "project_root", return_value=self.root),
                        patch.object(sub_docs, "project_root", return_value=self.root), patch.object(sub_role, "CODEX_SESSIONS", self.sessions),
                        # 템플릿 필수 절 검사는 test_template_titles만 다룬다. 다른 테스트는 빈 스킬 폴더를 쓴다.
                        patch.object(sub_docs, "TEMPLATES", self.templates),
                        # 스킬 호출 검사는 test_workflow_gates의 스킬Call만 다룬다.
                        patch.object(sub_scope, "skill_called", return_value=True)]
        for p in self.patches:
            p.start()

    # patch와 임시 폴더를 정리한다.
    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()
        self.ostemp.cleanup()

    # (역할, 문장) 쌍으로 대화 기록 파일을 만들고 경로를 반환하는 보조 함수이다.
    def transcript(self, *pairs, name="t.jsonl") -> str:
        path = self.root / name
        path.write_text("\n".join(claude_line(r, t) for r, t in pairs) + "\n", encoding="utf-8")
        return str(path)

    # Write 도구 입력으로 PreToolUse 검사를 실행하는 보조 함수이다.
    def write(self, rel, content, role="rio", transcript=None):
        data = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / rel), "content": content},
                "agent_type": role, "cwd": str(self.root),
                "transcript_path": transcript or self.transcript(("assistant", "보고"), ("user", "승인"))}
        return hook.check(data, "claude")

    # director 쓰기 범위
    def test_director_scope(self):
        no = self.transcript(("assistant", "계획"), ("user", "다시 생각해"))
        self.assertIsNone(self.write(".agent-logs/task/x/status.md", "- 진행", transcript=no))
        self.assertIsNone(self.write(".agent-logs/incident/a.md", "# 사건", transcript=no))
        self.assertIsNotNone(self.write("hooks/a.py", "x", transcript=no))
        self.assertIsNone(self.write("hooks/a.py", "x"))

    # 기대 결과 변경은 막고 기존 작업 칸과 다른 칸 변경은 허용한다.
    def test_spec_cells_locked(self):
        spec = self.root / ".agent-logs" / "task" / "t" / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(PLAN_OK, encoding="utf-8")
        edit = lambda old, new: hook.check({
            "tool_name": "Edit", "tool_input": {"file_path": str(spec), "old_string": old, "new_string": new},
            "agent_type": "rio", "cwd": str(self.root),
            "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인"))}, "claude")
        self.assertIsNone(edit("- 작업:\n  - c", "- 작업:\n  - x"))
        self.assertEqual(edit("- 기대 결과:\n  - e", "- 기대 결과:\n  - x"), sub_scope.REASON_SPEC_LOCKED)
        self.assertEqual(edit("- 결과:\n  - b", "- 결과:\n  - x"), sub_scope.REASON_SPEC_LOCKED)
        self.assertIsNone(edit("  - d (정적)", "  - x (정적)"))

        # 새 명세 문서는 결과와 기대 결과가 양식 자리표시 값일 때만 쓸 수 있다.
        blank = PLAN_OK.replace("  - e\n", "  - <판정 기준>\n").replace("  - b\n", "  - <목표를 달성했을 때 기대되는 결과>\n")
        self.assertIsNone(self.write(".agent-logs/task/n/spec.md", blank))
        self.assertEqual(self.write(".agent-logs/task/n/spec.md", PLAN_OK), sub_scope.REASON_SPEC_LOCKED)

    # Codex apply_patch에서도 기대 결과 변경을 막고 기존 작업 칸 변경을 허용한다.
    def test_spec_cells_locked_codex(self):
        spec = self.root / ".agent-logs" / "task" / "t" / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(PLAN_OK, encoding="utf-8")

        # 패치 본문을 명세 문서에 적용하는 apply_patch 입력으로 검사를 실행한다.
        def patch_of(body):
            text = f"*** Begin Patch\n*** Update File: {spec}\n{body}\n*** End Patch"
            return hook.check({"tool_name": "apply_patch", "tool_input": {"command": text}, "agent_type": "rio",
                               "cwd": str(self.root),
                               "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인"))}, "codex")
        self.assertIsNone(patch_of("@@\n - 작업:\n-  - c\n+  - x"))
        self.assertEqual(patch_of("@@\n - 기대 결과:\n-  - e\n+  - x"), sub_scope.REASON_SPEC_LOCKED)
        self.assertIsNone(patch_of("@@\n-  - d (정적)\n+  - x (정적)"))

    # 승인 뒤 스킬 본문과 hook 피드백이 user 역할로 쌓여도 사람이 보낸 마지막 메시지로 판정한다.
    def test_injected_user_messages_skipped(self):
        # 주입된 메시지가 섞인 대화 기록을 만든다.
        def build(*tail):
            path = self.root / "inj.jsonl"
            lines = [claude_line("assistant", "계획"), claude_line("user", "승인")] + list(tail)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return str(path)
        # 하네스가 넣은 메시지는 사람 메시지로 세지 않는지 확인한다.
        skill = json.dumps({"type": "user", "sourceToolUseID": "t1", "message": {"content": "Base directory for this skill: x\n# 구현"}})
        feedback = claude_line("user", "Stop hook feedback:\n재작성하십시오")
        self.assertIsNone(self.write("hooks/a.py", "x", transcript=build(skill, feedback)))
        # 사람이 보낸 마지막 메시지에 승인이 없으면 계속 차단한다.
        self.assertIsNotNone(self.write("hooks/a.py", "x", transcript=build(skill, claude_line("user", "다시 생각해"))))

    # director는 승인 전에도 OS 임시 폴더(하네스 scratchpad 포함)에 쓸 수 있다.
    def test_director_temp_write(self):
        no = self.transcript(("assistant", "계획"), ("user", "다시 생각해"))
        temp = Path(self.ostemp.name).resolve() / "scratch.txt"
        data = {"tool_name": "Write", "tool_input": {"file_path": str(temp), "content": "x"},
                "agent_type": "director", "cwd": str(self.root), "transcript_path": no}
        self.assertIsNone(hook.check(data, "claude"))
        self.assertIsNotNone(self.write("src/x.py", "x", role="director", transcript=no))

    # director의 Bash 커맨드가 쓰기 여부에 따라 deny되거나 통과하는지 확인한다.
    def test_director_bash(self):
        # 승인 없는 대화 기록을 만든다.
        no = self.transcript(("assistant", "계획"), ("user", "다시 생각해"))
        # director 세션의 Bash 입력으로 검사를 실행한다.
        def bash(cmd, transcript=no):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 커맨드"},
                               "agent_type": "rio", "cwd": str(self.root), "transcript_path": transcript}, "claude")
        # 읽기 전용 커맨드는 통과하는지 확인한다.
        for cmd in ("git status", "rg -n 'a|b' AGENTS.md", "Get-Content AGENTS.md", "rg -e foo", "ls | head", "date",
                    'rg -n "a => b" AGENTS.md', "rg -n 'x>y' f", "cmd 2>/dev/null", "cmd >/dev/null 2>&1", "cmd > $null",
                    "cmd >NUL", "git log | head", "python -m unittest discover -s hooks/tests 2>&1 | tail -20",
                    "sed -n '1,5p' f"):
            self.assertIsNone(bash(cmd), cmd)
        # 쓰기 호출이 없는 인라인 코드는 통과한다.
        for cmd in ("node -e 'x'", 'python -I -X utf8 -c "print(1)"'):
            self.assertIsNone(bash(cmd), cmd)
        # Bash 내용 쓰기는 승인과 무관하게 막고, 사유는 "다른 방식으로 작업하십시오."로 끝난다.
        approved = self.transcript(("assistant", "계획"), ("user", "승인"), name="yes.jsonl")
        for cmd in ("echo a > f.txt", "echo x > out.txt", "cmd >> f", "Set-Content f x", "git status && echo x > a",
                    "bash -c 'echo hi > f'", "ls | tee out.txt", "cat > notes.md <<'EOF'\nhi\nEOF"):
            self.assertTrue(bash(cmd), cmd)
            self.assertIn(hook.REASON_EDIT_GUIDE, bash(cmd, approved) or "", cmd)  # director 메인의 쓰기 Bash는 승인 턴에도 Edit 안내로 차단한다.
            self.assertNotIn("·", bash(cmd) or "", cmd)
        # 쓰기 호출이 있는 인라인 코드는 승인 전후 모두 막고 승인 턴에는 Edit 안내를 쓴다.
        for cmd in ("python -c \"open('a','w')\"", "node -e \"require('fs').writeFileSync('a','x')\""):
            self.assertIn("인라인 코드", bash(cmd) or "", cmd)
            self.assertIn(hook.REASON_EDIT_GUIDE, bash(cmd, approved) or "", cmd)
        # 리다이렉트 사유에는 따옴표를 벗긴 실제 대상 경로가 나온다.
        self.assertIn("이 커맨드는 리다이렉트로 `C:/tmp/a b.txt`에 내용을 씁니다.", bash('echo x > "C:/tmp/a b.txt"') or "")

    # 조사 문서에 새로 들어가는 출처가 URL이나 파일:행이 아니면 Write와 apply_patch 모두 deny한다.
    def test_research_source_format(self):
        bad = RESEARCH + "\n- 제외 범위: 사전 실측 (출처: 사용자 답)\n"
        self.assertIsNotNone(self.write(".agent-logs/task/n/research.md", bad))
        self.assertIsNone(self.write(".agent-logs/task/n/research.md", bad.replace("사용자 답", "a.py:1")))
        self.assertIsNone(self.write(".agent-logs/task/n/research.md", bad.replace("사용자 답", "hooks/x:3")))
        self.assertIsNotNone(self.write(".agent-logs/task/n/research.md", bad.replace("사용자 답", "사용자 답 12:30")))
        self.assertIsNone(self.write(".agent-logs/task/n/research.md", RESEARCH + "\n- 제외 범위: 없음\n"))
        patch_text = f"*** Begin Patch\n*** Add File: {self.root / '.agent-logs/task/n/research.md'}\n+출처: 사용자 메시지\n*** End Patch"
        data = {"tool_name": "apply_patch", "tool_input": {"command": patch_text}, "agent_type": "rio",
                "cwd": str(self.root), "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인"))}
        self.assertIn("출처", hook.check(data, "codex") or "")

    # 백틱이 든 출처 값은 사유에 백틱 한 겹으로 들어간다.
    def test_source_reason_single_backticks(self):
        reason = self.write(".agent-logs/task/n/research.md", RESEARCH + "\n- 제외 범위: 사전 실측 (출처: `a/b.md`)\n")
        self.assertIn("출처 `", reason)
        self.assertNotIn("``", reason)

    # 경과 문서의 검토 필요 절은 Edit, Write, apply_patch 모두 바꿀 수 없고 다른 절은 바꿀 수 있다.
    def test_state_sections_locked(self):
        template = ("# <작업명>\n\n- 작업 상태: <미개시 | 진행>\n\n### 요구사항 목록\n\n1. <요구사항 이름>: <내용>\n\n### 검토 필요\n\n- <항목>\n\n"
                    "### 작업 경과\n\n- 갱신 시각: <시각>\n")
        (self.templates / "log-status.md").write_text(template, encoding="utf-8")
        rel = ".agent-logs/task/t/status.md"
        path = self.root / rel
        locked = sub_scope.REASON_LOCKED
        doc = template.replace("<작업명>", "t")
        transcript = self.transcript(("assistant", "보고"), ("user", "승인"))

        # 새 문서는 두 절이 양식 원문이어야 통과한다.
        self.assertIsNone(self.write(rel, doc))
        self.assertEqual(self.write(rel, doc.replace("- <항목>", "- 내가 쓴 항목")), locked)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc, encoding="utf-8")

        # Edit과 Write는 두 절 변경을 막고 다른 절 변경은 통과한다.
        def edit(old, new):
            return hook.check({"tool_name": "Edit", "tool_input": {"file_path": str(path), "old_string": old, "new_string": new},
                               "agent_type": "rio", "cwd": str(self.root), "transcript_path": transcript}, "claude")
        self.assertEqual(edit("- <항목>", "- 바꿈"), locked)
        self.assertEqual(self.write(rel, doc.replace("- <항목>\n\n", "")), locked)
        self.assertIsNone(edit("<시각>", "2026-11-03"))

        # apply_patch도 기존 본문에 적용한 결과로 같게 판정한다. 적용할 수 없는 패치는 차단한다.
        def patch_of(body):
            text = f"*** Begin Patch\n*** Update File: {path}\n{body}\n*** End Patch"
            return hook.check({"tool_name": "apply_patch", "tool_input": {"command": text}, "agent_type": "rio",
                               "cwd": str(self.root), "transcript_path": transcript}, "codex")
        self.assertEqual(patch_of("@@\n ### 검토 필요\n \n-- <항목>\n+- 내가 쓴 항목"), locked)
        self.assertIsNone(patch_of("@@\n ### 작업 경과\n \n - 갱신 시각: <시각>\n+- 추가 줄"))
        self.assertEqual(patch_of("@@\n-없는 줄\n+새 줄"), locked)

        # 작업 상태 줄 변경은 차단한다.
        self.assertEqual(edit("- 작업 상태: <미개시 | 진행>", "- 작업 상태: 완료"), locked)
        self.assertEqual(self.write(rel, doc.replace("<미개시 | 진행>", "진행")), locked)

    # 경과 문서의 한계 줄에 출처가 없으면 사유는 파일 이름, 위반 행 번호와 양식 이름만 담고 양식 본문과 자리표시자를 담지 않는다.
    def test_violation_reason_has_location_and_form_name_only(self):
        text = "# t\n\n## 원인 분석\n\n- 조건 누락: 원인\n  - 출처: hooks/a.py:1\n  - 한계: 설명만 있다\n"
        reason = self.write(".agent-logs/task/n/status.md", text) or ""
        self.assertRegex(reason, r"^status\.md 7행: log-status 양식을 따르십시오\.$")
        for body in ("<", "##", "한계:", "자리표시"):
            self.assertNotIn(body, reason)
        # 빠진 절은 절 이름과 양식 이름만 담는다.
        (self.templates / "log-research.md").write_text("# t\n\n## 조사 계획\n\n## 후보 비교\n", encoding="utf-8")
        research = self.root / ".agent-logs/task/n/research.md"
        research.parent.mkdir(parents=True, exist_ok=True)
        research.write_text("# t\n\n## 조사 계획\n\n## 후보 비교\n", encoding="utf-8")
        self.assertEqual(self.write(".agent-logs/task/n/research.md", "# t\n\n## 조사 계획\n"),
                         sub_scope.REASON_MISSING_TITLES.format(name="research.md", titles="후보 비교", template="log-research"))

    # 관리 단계의 메인 세션 쓰기 차단 사유는 작업 단계를 말하고 승인 키워드 부재를 말하지 않는다.
    def test_outside_write_reason_follows_managed_stage(self):
        reason = sub_scope.REASON_OUTSIDE_STAGE.format(stage="계획")
        self.assertIn("[계획]", reason)
        self.assertNotIn("승인 키워드", reason)
        self.assertIn("승인 키워드", sub_scope.REASON_OUTSIDE_LOG)

    # 작업 문서에 사용자가 직접 입력한 20자 이상 문장을 옮기면 막고, 사용자 메시지가 인용한 이전 응답 구절과 압축 요약 속 문장은 허용한다.
    def test_user_quote_excludes_previous_answer_and_compact_summary(self):
        typed = "승인하기 전에 이 문장은 사용자가 직접 입력한 긴 문장입니다"
        shown = "보고서 본문 구절이 여기에 있습니다 길게 이어집니다"
        summary = "요약 항목 hooks/check_tool_use/sub_scope.py 수정 요청과 긴 문장이 이어집니다"
        compact = json.dumps({"type": "user", "isCompactSummary": True, "message": {"content": summary}}, ensure_ascii=False)
        plain = json.dumps({"type": "user", "message": {"content": summary}}, ensure_ascii=False)

        def log(*rows):
            path = self.root / "quote.jsonl"
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            return str(path)
        base = (claude_line("user", typed), claude_line("assistant", shown))
        quoted = log(*base, claude_line("user", f"> {shown} 라고 했는데 승인"))
        doc = ".agent-logs/task/t/status.md"
        self.assertEqual(self.write(doc, f"- 메모: {typed}", transcript=quoted), sub_scope.REASON_USER_QUOTE.format(quote=typed[:20]))
        self.assertNotIn("사용자 메시지 원문", self.write(doc, f"- 메모: {shown}", transcript=quoted) or "")
        self.assertNotIn("사용자 메시지 원문", self.write(doc, f"- 메모: {summary}", transcript=log(*base, compact)) or "")
        self.assertIn("사용자 메시지 원문", self.write(doc, f"- 메모: {summary}", transcript=log(*base, plain)) or "")

    # 질문 도구 입력에 출처 없는 확인 주장이 있으면 막고, 출처가 있거나 주장이 없으면 통과한다.
    def test_question_unsourced_claim(self):
        def ask(description):
            data = {"tool_name": "AskUserQuestion", "agent_type": "rio", "cwd": str(self.root),
                    "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인")),
                    "tool_input": {"questions": [{"header": "결정", "question": "중단 판정: 처리 방식을 고른다.",
                                                  "options": [{"label": "A", "description": description}]}]}}
            return hook.check(data, "claude")
        self.assertIn("근거가 없습니다", ask("두 runner 모두 같은 방식으로 동작합니다."))
        self.assertIsNone(ask("두 runner 모두 같은 방식으로 동작합니다 (`shared/settings/codex/config.example.toml:95`)."))
        self.assertIsNone(ask("두 runner에 같은 설정을 적용합니다."))

    # Claude Bash는 설명 칸이 비었거나 커맨드를 그대로 옮기면 차단한다. Codex 입력에는 설명 칸이 없다.
    def test_bash_description_required(self):
        # description 값을 바꿔 Bash 입력 검사를 실행한다.
        def bash(tin, runner="claude"):
            return hook.check({"tool_name": "Bash", "tool_input": tin, "agent_type": "rio"}, runner)
        # description이 비거나 커맨드 복사이면 막고 정상 값은 통과하는지 확인한다.
        self.assertIn("description", bash({"command": "git status"}))
        self.assertIn("description", bash({"command": "git status", "description": "  "}))
        self.assertIn("description", bash({"command": "git log --oneline -5", "description": "git log --oneline -5"}))
        self.assertIsNone(bash({"command": "git status", "description": "작업 트리 상태 확인"}))
        self.assertIsNone(bash({"command": "git status"}, "codex"))

    # 실행 승인 기록 쓰기에서 명세 문서 계획 검사(transition_reason)가 한 번만 실행되고 그 사유로 deny되는지 확인한다.
    def test_execution_approval_plan_checked_once(self):
        with patch.object(sub_scope, "transition_reason", return_value="계획 칸이 비었습니다.") as check:
            self.assertEqual(self.write(".agent-logs/task/t/status.md", STATE_APPROVED), "계획 칸이 비었습니다.")
            self.assertEqual(check.call_count, 1)
        with patch.object(sub_scope, "transition_reason", return_value=None) as check:
            self.assertIsNone(self.write(".agent-logs/task/t/status.md", STATE_APPROVED))
            self.assertEqual(check.call_count, 1)

    # 사용자 승인 키워드가 있어야 승인 상태를 쓸 수 있는지 확인한다.
    def test_approval_status_needs_user_approval(self):
        for doc in ("### 계획 승인 범위\n\n- 승인 상태: 승인\n", "### 계획 승인 범위\n\n- 사용자 승인 상태: 승인\n"):
            no = self.transcript(("assistant", "계획"), ("user", "좋아 계속"))  # 같은 기록 파일을 덮어쓰므로 매번 만든다.
            self.assertIsNotNone(self.write(".agent-logs/task/t/status.md", doc, transcript=no), doc)
            self.assertIsNone(self.write(".agent-logs/task/t/status.md", doc), doc)

    # 이전 양식의 빈 최종 원문 칸은 지울 수 있고, 내용이 있는 칸은 지울 수 없다.
    def test_empty_legacy_raw_removable(self):
        from sub_judgment import check_raw
        old = "# 조사 문서\n\n## 조사 단계 원문\n\n### 최종 조사 원문: 조사 단계\n\n```\n기록 없음\n```\n"
        self.assertIsNone(check_raw("# 조사 문서\n\n## 조사 결과\n", old))
        self.assertEqual(check_raw("# 조사 문서\n", old.replace("기록 없음", "보고 원문")), "조사 문서의 최종 원문 칸은 결과 저장 hook만 씁니다.")

    # 조사 문서의 최종 원문 칸 변경이 deny되는지 확인한다.
    def test_raw_block_protected(self):
        (self.state / "research.md").write_text(RESEARCH, encoding="utf-8")
        self.assertIsNotNone(self.write(".agent-logs/task/t/research.md", RESEARCH.replace("기록 없음", "고침")))
        self.assertIsNone(self.write(".agent-logs/task/t/research.md", RESEARCH.replace("| A    | 낮음 |", "| A | 중간 |")))

    # 조사 문서의 회차 반증 블록 변경이 deny되는지 확인한다.
    def test_round_blocks_protected(self):
        rounds = RESEARCH + "\n### 1회차 반증 원문\n\n```\n- 주장 1: 반증\n```\n"
        (self.state / "research.md").write_text(rounds, encoding="utf-8")
        self.assertIsNotNone(self.write(".agent-logs/task/t/research.md", rounds.replace("반증\n```", "지지\n```")))
        self.assertIsNotNone(self.write(".agent-logs/task/t/research.md", RESEARCH))
        self.assertIsNone(self.write(".agent-logs/task/t/research.md", rounds.replace("| A    | 낮음 |", "| A | 중간 |")))
        self.assertIsNotNone(self.write(".agent-logs/task/n/research.md", rounds))

    # 반증 문서는 회차 블록이 없는 문서의 빈 양식 덮어쓰기만 허용하고, 기록된 회차를 지우거나 일부만 지우는 쓰기는 deny한다.
    def test_refute_doc_blank_overwrite_only(self):
        blank = "# 반증 문서: t\n\n## 조사 및 반증 루프\n\n## 회차 기록\n\n### 최종 반증 원문\n\n```\n기록 없음\n```\n"
        rounds = ("# 반증 문서: t\n\n## 조사 및 반증 루프\n\n## 회차 기록\n\n"
                  "### 1회차 반증 원문\n\n```\n- 주장 1: 반증\n```\n\n### 2회차 반증 원문\n\n```\n- 주장 1: 지지\n```\n")
        folder = self.root / ".agent-logs" / "task" / "t"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "refute.md").write_text(rounds, encoding="utf-8")
        self.assertIn("결과 저장 hook만", self.write(".agent-logs/task/t/refute.md", blank) or "")
        partial = rounds[:rounds.index("### 2회차")]
        self.assertIn("회차 반증 블록", self.write(".agent-logs/task/t/refute.md", partial) or "")
        (folder / "refute.md").write_text(blank, encoding="utf-8")
        self.assertIsNone(self.write(".agent-logs/task/t/refute.md", blank))

    # 지침 파일 쓰기를 스킬 호출 이력으로 차단하지 않는다.
    def test_rule_skill_required_for_guideline_files(self):
        for rel in ("AGENTS.global.md", ".claude/agents/director/MAIN.md", ".claude/agents/researcher/SUB.md"):
            with self.subTest(rel=rel):
                with patch.object(sub_scope, "skill_called", return_value=False):
                    self.assertIsNone(self.write(rel, "x"))
                self.assertIsNone(self.write(rel, "x"))
        self.assertEqual(sub_scope.skill_for(".claude/agents/director/SOUL.md"), None)

    # 새 조사 문서는 빈 원문 칸일 때만 허용되는지 확인한다.
    def test_new_research_doc_allows_empty_raw_only(self):
        self.assertIsNone(self.write(".agent-logs/task/n/research.md", RESEARCH))
        self.assertIsNotNone(self.write(".agent-logs/task/n/research.md", RESEARCH.replace("기록 없음", "가짜 결과")))

    # 스킬 원본 템플릿 주석과 진행 중인 작업 경과 문서를 준비한다. status는 검토 필요 항목의 상태이고 approved는 실행 승인 여부이며 measure는 실측 필요 항목 (시점, 상태)이다.
    def running_task(self, status, approved=True, measure=None):
        (self.templates / "log-spec.md").write_text("<!-- 모든 목록이 검토 완료 또는 폐기일 때 승인 가능 -->\n", encoding="utf-8")
        folder = self.root / ".agent-logs" / "task" / "t"
        folder.mkdir(parents=True, exist_ok=True)
        approval = "승인" if approved else "승인 대기"
        (folder / "status.md").write_text(
            f"# t\n\n- 작업 상태: 진행\n\n## 실행 승인 범위\n\n- 사용자 승인 상태: {approval}\n\n"
            f"### 검토 필요\n\n- A\n  - 상태: {status}\n"
            + (f"\n### 실측 필요\n\n- B\n  - 시점: {measure[0]}\n  - 상태: {measure[1]}\n" if measure else ""), encoding="utf-8")
        return folder / "status.md"

    # 실행 승인 뒤 허용 값이 아닌 검토 필요 항목이 있으면 log 밖 쓰기를 막고 상태 문서 쓰기는 통과한다.
    def test_review_left_blocks_outside_write_after_approval(self):
        path = self.running_task("미검토")
        self.assertIn("검토", self.write("hooks/a.py", "x") or "")
        self.assertIsNone(self.write(".agent-logs/task/t/status.md", path.read_text(encoding="utf-8")))
        for status in ("사후실측 필요", "사후검증 필요", "검토 완료(일부)"):
            self.running_task(status)
            self.assertIsNotNone(self.write("hooks/a.py", "x"), status)
        self.running_task("검토 완료")
        self.assertIsNone(self.write("hooks/a.py", "x"))

        # 시점이 사전인 실측 대기 항목은 막고, 실측 완료이거나 시점이 사후면 통과한다.
        self.running_task("검토 완료", measure=("사전", "실측 대기"))
        self.assertIn("B", self.write("hooks/a.py", "x") or "")
        for measure in (("사전", "실측 완료"), ("사후", "실측 대기")):
            self.running_task("검토 완료", measure=measure)
            self.assertIsNone(self.write("hooks/a.py", "x"), measure)
        self.running_task("미검토", approved=False)
        self.assertIsNone(self.write("hooks/a.py", "x"))

    # 경과 문서 검토 필요 절에 확정 답이 없는 검토 항목이 있으면 구현 승인 뒤 log 밖 쓰기를 막고, 확정 답 한 줄이나 폐기가 있으면 통과한다.
    def test_unfinished_review_blocks_outside_write(self):
        path = self.running_task("검토 완료")
        base = path.read_text(encoding="utf-8")
        path.write_text(base + "\n- 검토 4. X\n", encoding="utf-8")
        self.assertIn("검토 4. X", self.write("hooks/a.py", "x") or "")
        for answer in ("안 1. A", "폐기"):
            path.write_text(base + f"\n- 검토 4. X: {answer} (사용자 검토 시각: 2026-11-12 03:00:00)\n", encoding="utf-8")
            self.assertIsNone(self.write("hooks/a.py", "x"), answer)

    # 경과 문서 검증 결과 쓰기는 스킬 호출 이력과 독립적이다.
    def test_verdict_skill_required_for_verification_item(self):
        body = "# t\n\n## 검증 결과\n\n- 2026-10-27 05:00:00: PASS\n"
        other = "# t\n\n## 경과\n\n- 2026-10-27 05:00:00: 메모\n"
        with patch.object(sub_scope, "skill_called", side_effect=lambda d, s, r="claude": s != "nestlab-verdict"):
            self.assertIsNone(self.write(".agent-logs/task/t/status.md", body))
            self.assertIsNone(self.write(".agent-logs/task/t/status.md", other))
        self.assertIsNone(self.write(".agent-logs/task/t/status.md", body))

    # 스킬 주석이 없어도 완료 상태인 작업의 쓰기는 허용한다.
    def test_missing_template_comment_blocks_outside_write(self):
        self.running_task("검토 완료")
        (self.templates / "log-spec.md").write_text("# 주석 없음\n", encoding="utf-8")
        self.assertIsNone(self.write("hooks/a.py", "x"))

    # 메인 세션 Bash는 승인 전 쓰기 커맨드를 막고 승인 턴에는 통과시키며 읽기 커맨드는 통과한다.
    def test_main_session_bash_denies_writes(self):
        def bash(cmd, user="승인"):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 커맨드"},
                               "agent_type": "rio", "cwd": str(self.root),
                               "transcript_path": self.transcript(("assistant", "계획"), ("user", user))}, "claude")
        for cmd in ("sed -i s/a/b/ a.py", "echo x > a.txt", "rm a.txt"):
            self.assertTrue(bash(cmd, "다시 생각해"), cmd)
            self.assertIn(hook.REASON_EDIT_GUIDE, bash(cmd) or "", cmd)  # 승인 턴에도 director 메인의 쓰기 Bash는 Edit 안내로 차단한다.
        self.assertIsNone(bash("git status"))
        self.assertIsNone(bash("grep -n def hooks/common/sub_write.py"))
        # 승인 턴에는 인라인 코드와 셸 변수, 반복문, 저장소 안 git -C를 hook이 막지 않는다.
        for cmd in ('python -c "print(1)"', 'D=/x; ls "$D"', "for f in a b; do echo f; done", "git -C hooks log -1",
                    "cat <<'E'\na\nb\nE"):
            self.assertIsNone(bash(cmd), cmd)
        # 승인 턴에도 git -c와 cd는 차단한다.
        self.assertEqual(bash("git -c a.b=c log -1"), sub_write.REASON_GIT_C)
        for cmd in ("cd hooks && git status", "cd hooks && git status --short", "cd hooks && grep -n def sub_write.py"):
            self.assertEqual(bash(cmd), sub_write.REASON_CD, cmd)

    # 실행 승인 뒤 worker와 documenter만 쓰기 Bash를 쓰고 .agent-logs/ 경로가 든 커맨드와 reviewer의 쓰기는 차단한다.
    def test_write_roles_bash_after_approval(self):
        self.running_task("검토 완료")
        prompt = self.root / "p.jsonl"
        prompt.write_text(claude_line("user", "task: implement\ninput: .agent-logs/task/t/spec.md") + "\n", encoding="utf-8")

        def bash(role, cmd):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 커맨드"},
                               "agent_type": role, "agent_id": "sub-1", "cwd": str(self.root),
                               "agent_transcript_path": str(prompt), "transcript_path": str(prompt)}, "claude")
        for role in ("worker", "documenter"):
            self.assertIsNone(bash(role, "sed -i s/a/b/ a.py"), role)
            self.assertIsNone(bash(role, "python -c \"print(1)\""), role)
            self.assertIsNone(bash(role, "sed -i s/a/b/ .agent-logs/task/t/spec.md"), role)
            self.assertIsNone(bash(role, 'D=/x; ls "$D"'), role)
            self.assertIsNone(bash(role, "python - <<'E'\nprint(1)\nprint(2)\nE"), role)
        self.assertIsNone(bash("reviewer", "sed -i s/a/b/ a.py"))

        # 실행 승인 전에는 worker도 쓰기 Bash를 쓸 수 없다.
        self.running_task("검토 완료", approved=False)
        self.assertIsNone(bash("worker", "sed -i s/a/b/ a.py"))

    # 따옴표 안 정규식 인자는 경로로 보지 않고 저장소 밖 경로는 계속 차단한다.
    def test_quoted_regex_is_not_path(self):
        def bash(cmd):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 커맨드"},
                               "agent_type": "worker", "agent_id": "sub-1", "cwd": str(self.root)}, "claude")
        self.assertIsNone(bash(r'grep -rn "\.active" hooks'))
        self.assertIsNone(bash(r"grep -rn 'a\(b\)' hooks"))
        self.assertIsNotNone(bash("cat C:/Windows/win.ini"))

    # 읽기 전용
    def test_readonly_roles(self):
        self.assertIsNotNone(self.write(".agent-logs/task/x/status.md", "x", role="nico"))
        self.assertIsNone(self.write(".agent-logs/task/x/status.md", "x", role="ricky"))
        self.assertIsNone(self.write("src/a.py", "x", role="jelly"))

    # 메인 세션은 역할과 무관하게 승인 없이 .agent-logs/ 밖에서 쓸 수 없고, 서브에이전트는 이 검사를 받지 않는다.
    def test_main_session_write_needs_approval(self):
        for role in ("buddy", "rio", "documenter", "worker", None):
            no = self.transcript(("assistant", "diff"), ("user", "고쳐"))  # 같은 기록 파일을 덮어쓰므로 매번 만든다.
            if role == "rio":
                self.assertIn("승인", self.write("README.md", "x", role=role, transcript=no) or "", role)
            else:
                self.assertIsNone(self.write("README.md", "x", role=role, transcript=no), role)
            self.assertIsNone(self.write("README.md", "x", role=role), role)
        no = self.transcript(("assistant", "diff"), ("user", "고쳐"))
        data = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / "README.md"), "content": "x"},
                "agent_type": "jelly", "agent_id": "sub-1", "cwd": str(self.root), "transcript_path": no}
        self.assertIsNone(hook.check(data, "claude"))

    # assistant 메인 세션은 승인 키워드 턴에만 쓰기 Bash를 쓰고 .agent-logs/ 경로 커맨드는 막으며 저장소 밖 git -C는 계속 차단한다.
    def test_assistant_main_session_not_gated(self):
        self.running_task("미검토")
        yes = self.transcript(("assistant", "계획"), ("user", "승인"))

        def bash(cmd, transcript=yes):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 커맨드"},
                               "agent_type": "buddy", "cwd": str(self.root), "transcript_path": transcript}, "claude")
        for cmd in ("rm a.txt", "mv a b", "sed -i s/a/b/ a.py", 'python -c "print(1)"'):
            self.assertIsNone(bash(cmd), cmd)
        self.assertIsNone(bash("sed -i s/a/b/ .agent-logs/task/t/status.md"))
        self.assertIsNone(bash("git -C hooks log -1"))
        self.assertIsNotNone(bash("git -C /x log -1"))
        no = self.transcript(("assistant", "계획"), ("user", "고쳐"))
        self.assertIsNone(bash("sed -i s/a/b/ a.py", no))
        self.assertIsNone(self.write("hooks/a.py", "x", role="buddy"))

    # git 복구, 삭제 커맨드는 승인 전 director 메인 세션에서 deny되고 승인 턴에는 설정 deny 규칙에 맡겨 hook이 막지 않는다.
    def test_main_session_bash_write_commands_denied(self):
        for cmd in ("git checkout -- README.md", "git restore README.md", "git apply a.patch", "git reset --hard",
                    "find . -name '*.tmp' -delete", "ls | xargs rm"):
            data = {"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 커맨드"}, "agent_type": "rio",
                    "cwd": str(self.root)}
            no = self.transcript(("assistant", "diff"), ("user", "고쳐"))  # 같은 기록 파일을 덮어쓰므로 매번 만든다.
            self.assertIsNotNone(hook.check({**data, "transcript_path": no}, "claude"), cmd)
            yes = self.transcript(("assistant", "diff"), ("user", "승인"))
            self.assertIn(hook.REASON_EDIT_GUIDE, hook.check({**data, "transcript_path": yes}, "claude") or "", cmd)

    # 저장소 안 절대 경로로 이동하는 cd는 deny되고 cd 없는 커맨드는 통과한다.
    def test_absolute_cd_denied(self):
        def bash(cmd):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 커맨드"},
                               "agent_type": "rio", "cwd": str(self.root),
                               "transcript_path": self.transcript(("assistant", "계획"), ("user", "다시 생각해"))}, "claude")
        root = self.root.as_posix()
        for cmd in (f"cd {root} && ls -t .agent-logs/task/ | head -10", f"cd {root} && ls -t .agent-logs/task/"):
            self.assertEqual(bash(cmd), sub_write.REASON_CD, cmd)
        self.assertIsNone(bash("ls -t .agent-logs/task/"))

    # 검사 중 예외가 나면 쓰기 도구만 deny하고 나머지는 통과한다.
    def test_check_error_denies_writes_only(self):
        def run(data):
            out = io.StringIO()
            with patch.object(hook, "check", side_effect=RuntimeError("x")), \
                 patch.object(sys, "argv", ["check_tool_use.py", "--runner", "claude"]), \
                 patch.object(sys, "stdin", io.StringIO(json.dumps(data))), patch.object(sys, "stdout", out):
                hook.main()
            return out.getvalue()
        self.assertIn("deny", run({"tool_name": "Edit", "tool_input": {"file_path": "a"}}))
        self.assertIn("deny", run({"tool_name": "Bash", "tool_input": {"command": "rm a"}}))
        self.assertEqual("", run({"tool_name": "Read", "tool_input": {"file_path": "a"}}))
        self.assertEqual("", run({"tool_name": "Bash", "tool_input": {"command": "ls"}}))

    # 서브에이전트 호출 승인 (Claude)
    def agent(self, prompt, user, target="jelly", **extra):
        data = {"tool_name": "Agent", "tool_input": {"subagent_type": target, "prompt": prompt},
                "agent_type": "rio", "session_id": "s1", "cwd": str(self.root),
                "transcript_path": self.transcript(("assistant", "계획"), ("user", user)), **extra}
        return hook.check(data, "claude")

    # 서브에이전트 호출에 사용자 승인 키워드가 필요한지 확인한다.
    def test_agent_call_needs_approval(self):
        prompt = "task: implement\ninput: .agent-logs/task/t/spec.md"
        self.assertIsNotNone(self.agent(prompt, "다시 생각해"))
        self.assertIsNone(self.agent(prompt, "승인"))
        self.assertIsNone(self.agent(prompt, "다시 생각해", agent_id="sub-1"))

    # 승인 키워드는 사용자 메시지의 마지막 줄에 있을 때만 인정하는지 확인한다.
    def test_approval_last_line_command(self):
        # 승인 키워드는 마지막 줄 끝의 `승인`뿐이다(sub_approval.APPROVAL). 다른 동사는 계획 수정 요청과 구별되지 않는다.
        allowed = ("승인", "승인.", "주의사항 1\n주의사항 2\n\n승인!", "이대로 승인", "실행 승인~")
        denied = ("진행 안 해", "구현하지 마", "승인 안 함", "1", "그러든지.", "진행해", "그래",
                  "승인\n근데 잠깐", "", "안 승인", "못 승인", "이번엔 테스트 빼고 구현해")
        for text in allowed:
            self.assertTrue(sub_approval.approved(text), text)
        for text in denied:
            self.assertFalse(sub_approval.approved(text), text)

    # director가 아닌 역할의 Agent 호출은 검사하지 않는지 확인한다.
    def test_buddy_agent_call_not_checked(self):
        data = {"tool_name": "Agent", "tool_input": {"subagent_type": "Explore", "prompt": "x"},
                "agent_type": "buddy", "transcript_path": self.transcript(("assistant", "a"), ("user", "b"))}
        self.assertIsNone(hook.check(data, "claude"))

    # 실행 승인 뒤 재작업과 재검증은 새 승인 없이 이어지는지 확인한다.
    def test_rework_continues_without_new_approval(self):
        path = self.state / "status.md"
        for kind in sub_approval.IN_SCOPE:
            prompt = f"{kind}\ninput: .agent-logs/task/t/spec.md"
            path.unlink(missing_ok=True)
            target = "jelly" if kind.endswith("reimplement") else "ricky"
            self.assertIsNotNone(self.agent(prompt, "이 부분 고쳐", target=target), kind)
            path.write_text(STATE_APPROVED, encoding="utf-8")
            self.assertIsNone(self.agent(prompt, "이 부분 고쳐", target=target), kind)

    # 복원된 문서 작성과 구현은 같은 작업의 실행 승인과 실제 대기 재작업이 모두 있어야 허용한다.
    def test_restored_work_requires_approved_pending_rework(self):
        folder = self.root / ".agent-logs/task/20261104-234558-test"
        folder.mkdir(parents=True, exist_ok=True)
        status, spec = folder / "status.md", folder / "spec.md"
        pending = "# 명세\n## 재구현 요청\n- hooks/a.py: 입력 검사 수정 (요구사항 1)\n  - 기대 결과: 올바른 입력을 처리한다\n"
        for kind in sub_approval.RESTORED_WORK:
            prompt = f"{kind}\ninput: .agent-logs/task/20261104-234558-test/spec.md"
            spec.write_text(pending, encoding="utf-8")
            status.write_text(STATE_APPROVED.replace("상태: 승인", "상태: 승인 대기"), encoding="utf-8")
            self.assertEqual(sub_approval.approval_reason("수정해 주세요", prompt, str(self.root)), sub_approval.REASON_APPROVAL)
            status.write_text(STATE_APPROVED, encoding="utf-8")
            self.assertIsNone(sub_approval.approval_reason("수정해 주세요", prompt, str(self.root)))
            for empty in ("", "없음", "- 없음", "- hooks/a.py: <수정 위치>\n  - 기대 결과: <동작>"):
                with self.subTest(kind=kind, empty=empty):
                    spec.write_text("# 명세\n## 재구현 요청\n" + empty, encoding="utf-8")
                    self.assertEqual(sub_approval.approval_reason("수정해 주세요", prompt, str(self.root)), sub_approval.REASON_APPROVAL)
            spec.write_text(pending, encoding="utf-8")
            other = prompt.replace("234558-test", "234558-other")
            self.assertEqual(sub_approval.approval_reason("수정해 주세요", other, str(self.root)), sub_approval.REASON_APPROVAL)

    # 항목의 실제 대상과 기대 결과만 판정하고 설명의 질문 양식 예시는 대기 작업을 지우지 않는다.
    def test_pending_rework_ignores_example_in_description(self):
        folder = self.root / ".agent-logs/task/20261104-234558-test"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "status.md").write_text(STATE_APPROVED, encoding="utf-8")
        spec = folder / "spec.md"
        example = "  - 설명: 문제 정의 확인: <보고한 문제 정의와 범위> (작업 MMDD-HHMMSS)\n"
        valid = "- hooks/a.py: 입력 검사 수정 (요구사항 1)\n  - 기대 결과: 올바른 입력을 처리한다\n"
        for kind in sub_approval.RESTORED_WORK:
            prompt = f"{kind}\ninput: .agent-logs/task/20261104-234558-test/spec.md"
            for body in (valid + example, valid + example + "- <다른 대상>: <수정 위치>\n  - 기대 결과: <동작>\n"):
                with self.subTest(kind=kind, body=body):
                    spec.write_text("# 명세\n## 재구현 요청\n" + body, encoding="utf-8")
                    self.assertIsNone(sub_approval.approval_reason("수정해 주세요", prompt, str(self.root)))
            spec.write_text("# 명세\n## 재구현 요청\n" + valid.replace("  - 기대 결과: 올바른 입력을 처리한다\n", "") + example, encoding="utf-8")
            self.assertEqual(sub_approval.approval_reason("수정해 주세요", prompt, str(self.root)), sub_approval.REASON_APPROVAL)
            for target, location, result in (("", "수정", "입력을 처리한다"), ("<파일>", "수정", "입력을 처리한다"),
                                             ("hooks/a.py", "", "입력을 처리한다"), ("hooks/a.py", "<수정 위치>", "입력을 처리한다"),
                                             ("hooks/a.py", "수정", ""), ("hooks/a.py", "수정", "<관측할 동작>")):
                with self.subTest(kind=kind, target=target, location=location, result=result):
                    body = f"- {target}: {location}\n  - 기대 결과: {result}\n" + example
                    spec.write_text("# 명세\n## 재구현 요청\n" + body, encoding="utf-8")
                    self.assertEqual(sub_approval.approval_reason("수정해 주세요", prompt, str(self.root)), sub_approval.REASON_APPROVAL)

    # 암호문과 task_name에서 복원된 두 역할의 읽기 호출도 기존 승인 근거를 확인한다.
    def test_codex_restored_rework_allows_get_content(self):
        folder = self.root / ".agent-logs/task/20261104-234558-test"
        folder.mkdir(parents=True, exist_ok=True)
        status, spec = folder / "status.md", folder / "spec.md"
        status.write_text(STATE_APPROVED, encoding="utf-8")
        spec.write_text("# 명세\n## 재구현 요청\n- hooks/a.py: 입력 검사 수정 (요구사항 1)\n  - 기대 결과: 입력을 처리한다\n", encoding="utf-8")
        parent = self.sessions / "rollout-1-P.jsonl"
        parent.write_text(codex_meta() + "\n" + codex_msg("user", "기존 작업을 수정해 주세요.") + "\n", encoding="utf-8")
        for stage, role in (("document", "pepper"), ("implement", "jelly")):
            child = self.sessions / f"rollout-1-{stage}.jsonl"
            child.write_text(codex_meta(agent_role=role, task_name=f"{stage}__20261104_234558_test",
                                       source={"subagent": {"thread_spawn": {"parent_thread_id": "P"}}})
                             + "\n" + codex_msg("user", "gAAAAAopaque") + "\n", encoding="utf-8")
            data = {"tool_name": "exec_command", "tool_input": {"cmd": f'Get-Content -LiteralPath "{spec}"'},
                    "cwd": str(self.root), "transcript_path": str(child)}
            with self.subTest(stage=stage):
                self.assertIsNone(hook.check(data, "codex"))
                spec.write_text("# 명세\n## 재구현 요청\n없음\n", encoding="utf-8")
                self.assertIsNone(hook.check(data, "codex"))
                spec.write_text("# 명세\n## 재구현 요청\n- hooks/a.py: 입력 검사 수정 (요구사항 1)\n  - 기대 결과: 입력을 처리한다\n", encoding="utf-8")

    # 두 runner의 실제 질문 답 형식과 UTC 시각으로 문제 정의 확인 기록을 만든다. queries는 (질문 문장, 답) 목록이며 답마다 1분씩 늦다.
    def problem_reply(self, runner, queries=(("[문제 정의] 문제 1. 파일 처리 문제와 수정 범위 (작업 1104-234558)", "확정"),)):
        rows = []
        stamp = lambda i: f"2026-11-04T19:{20 + i:02d}:00Z"
        if runner == "claude":
            rows.append({"type": "user", "timestamp": "2026-11-04T19:00:00Z", "message": {"content": "문제를 확인해 주세요."}})
            for i, (text, value) in enumerate(queries):
                qid = f"q{i}"
                rows += [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": qid, "name": "AskUserQuestion",
                          "input": {"questions": [{"question": text, "options": [{"label": "확정"}, {"label": "보류"}]}]}}]}},
                         {"type": "user", "timestamp": stamp(i), "message": {"content": [
                             {"type": "tool_result", "tool_use_id": qid, "content": "답변"}]}, "toolUseResult": {"answers": {text: value}}}]
        else:
            item = lambda payload, at: {"type": "response_item", "timestamp": at, "payload": payload}
            rows.append(item({"type": "message", "role": "user", "content": [{"text": "문제를 확인해 주세요."}]}, "2026-11-04T19:00:00Z"))
            for i, (text, value) in enumerate(queries):
                qid = f"q{i}"
                rows += [item({"type": "function_call", "name": "request_user_input", "call_id": qid,
                               "arguments": json.dumps({"questions": [{"id": qid, "question": text,
                                             "options": [{"label": "확정"}, {"label": "보류"}]}]})}, stamp(i)),
                         item({"type": "function_call_output", "call_id": qid, "output": json.dumps({
                             "answers": {qid: {"answers": [value] if value else []}}})}, stamp(i))]
        path = self.root / f"{runner}-problem.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        return str(path)

    # 작업 폴더가 있는 작업 태그를 단 `[문제 정의] 문제 2.` 형식의 질문은 hook을 통과한다.
    def test_problem_question_head_passes(self):
        path = self.root / ".agent-logs/task/20261104-234558-test/status.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 작업\n", encoding="utf-8")
        sentence = "[문제 정의] 문제 2. 입력 오류를 고친다. 경계값도 포함한다 (작업 1104-234558)"
        question = {"question": sentence, "options": [{"label": "확정"}, {"label": "보류"}]}
        for runner in ("claude", "codex"):
            # 질문 전 응답 본문에는 한계와 위험 두 줄이 있어야 하므로 첫 줄 뒤에 응답 본문 줄을 넣는다.
            log = Path(self.problem_reply(runner, ((sentence, "확정"),)))
            lines = log.read_text(encoding="utf-8").splitlines()
            body = "- 한계: 없음\n- 위험: 없음"
            lines.insert(1, json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": body}]}}) if runner == "claude"
                         else json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": body}]}}))
            asked = log.with_name(f"{runner}-asked.jsonl")
            asked.write_text("\n".join(lines), encoding="utf-8")
            data = {"tool_name": "AskUserQuestion" if runner == "claude" else "request_user_input",
                    "tool_input": {"questions": [question]}, "cwd": str(self.root), "agent_type": "director",
                    "transcript_path": str(asked)}
            self.assertIsNone(hook.check(data, runner), runner)

    # 없음 선언에는 재구현 근거를 요구하지 않고 실제 신규 요청에는 계속 요구한다.
    def test_reimplementation_none_needs_no_evidence(self):
        path = self.root / ".agent-logs/task/20261104-234558-test/spec.md"
        self.assertIsNone(sub_scope.evidence_gap({}, path, "## 재구현 요청\n- 없음\n", ""))
        self.assertEqual(sub_scope.evidence_gap({}, path, "## 재구현 요청\n- hooks/a.py: 수정\n", ""),
                         sub_scope.REASON_EVIDENCE_REQUIRED)

    # 작업 문서에 다른 작업 ID를 새로 쓰면 막고, 자기 작업 ID와 작업 태그는 허용한다.
    def test_other_task_id_blocked(self):
        path = self.root / ".agent-logs/task/20261104-234558-test/status.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        old = "# 작업\n## 문제 정의\n\n## 작업 경과\n- 기록\n"
        path.write_text(old, encoding="utf-8")
        said = self.transcript(("user", "20261104-234558-test 작업과 20261105-010101-other-task 작업을 비교해 주세요"),
                               ("assistant", "보고"), ("user", "승인"))
        write = lambda line: self.write(".agent-logs/task/20261104-234558-test/status.md", old.replace("- 기록", line), transcript=said)
        self.assertEqual(write("- 출처: .agent-logs/task/20261105-010101-other-task/spec.md:3"),
                         sub_scope.REASON_OTHER_TASK.format(task="20261105-010101-other-task"))
        self.assertIsNone(write("- 이 작업 20261104-234558-test의 기록"))
        self.assertIsNone(write("- 작업 태그 1104-234558 확인"))

    # 기존 검토 번호 방식의 역사적 확인 기록은 기존 상태 대조를 유지한다.
    def test_legacy_problem_confirmation_kept(self):
        added = ["  - 요구 구분: director 해석 (검토 3)"]
        self.assertEqual(sub_scope.unconfirmed(added, ""), "3")
        self.assertIsNone(sub_scope.unconfirmed(added, "## 검토 필요\n- 검토 3. 해석\n  - 상태: 검토 완료\n"))

    # 검증 호출에는 사용자 승인이 필요한지 확인한다.
    def test_review_needs_user_approval(self):
        (self.state / "status.md").write_text(STATE_APPROVED, encoding="utf-8")
        prompt = "task: verify\ninput: .agent-logs/task/t/spec.md"
        self.assertIsNotNone(self.agent(prompt, "이 부분 고쳐", target="ricky"))
        self.assertIsNone(self.agent(prompt, "검증 승인", target="ricky"))

    # 문자열 source는 부모 thread가 없는 메인 세션으로 판정하는지 확인한다.
    def test_codex_string_source_is_main_session(self):
        path = self.sessions / "rollout-1-M.jsonl"
        path.write_text(codex_meta(source="vscode") + "\n", encoding="utf-8")
        self.assertIsNone(sub_role.parent_thread(str(path)))
        self.assertTrue(sub_role.main_session({"transcript_path": str(path)}))

    # dict source에 있는 부모 thread id와 하위 세션 판정을 유지하는지 확인한다.
    def test_codex_dict_source_keeps_parent_thread(self):
        path = self.sessions / "rollout-1-C.jsonl"
        path.write_text(codex_meta(source={"subagent": {"thread_spawn": {"parent_thread_id": "P"}}}) + "\n",
                        encoding="utf-8")
        self.assertEqual(sub_role.parent_thread(str(path)), "P")
        self.assertFalse(sub_role.main_session({"transcript_path": str(path)}))

    # 서브에이전트 호출 승인 (Codex 하위 thread)
    def test_codex_child_needs_parent_approval(self):
        parent = self.sessions / "rollout-1-P.jsonl"
        child = self.sessions / "rollout-1-C.jsonl"
        child.write_text("\n".join([
            codex_meta(agent_role="jelly", source={"subagent": {"thread_spawn": {"parent_thread_id": "P"}}}),
            codex_msg("user", "task: implement\ninput: .agent-logs/task/t/spec.md")]) + "\n", encoding="utf-8")
        data = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "transcript_path": str(child)}
        parent.write_text("\n".join([codex_meta(), codex_msg("user", "다시 생각해")]) + "\n", encoding="utf-8")
        self.assertIsNone(hook.check(data, "codex"))
        parent.write_text("\n".join([codex_meta(), codex_msg("user", "승인")]) + "\n", encoding="utf-8")
        self.assertIsNone(hook.check(data, "codex"))

    # 세션 기록 정보 전달 차단
    SID = "6e148eb1-a29f-4953-8529-d3531daf8e3f"
    LEAKS = ("~/.claude/projects/p/s.jsonl", "~/.codex/sessions/2026/x", "기록 s.jsonl", SID, "사건 20261023-6e148eb1",
             "사건 20261026-0b1c2d3e", "사건 20261026-0403-0b1c2d3e",
             "사건 20261026-unknown", "사건 20261026-0403-unknown",
             "세션 0b1c2d3e-1111-2222-3333-444455556666", "agent-a452305bd3f367cc8", "rollout-2026-10-23T00-00-00-x")

    # 세션 기록 정보가 든 Claude 호출을 deny하는지 확인한다.
    def test_session_info_blocks_claude_call(self):
        # 조사 문서와 호출 프롬프트를 준비한다.
        doc = self.root / ".agent-logs/task/t/research.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        prompt = "task: research\ninput: .agent-logs/task/t/research.md"
        # tool_input 값을 바꿔 Agent 호출 검사를 실행한다.
        def call(**tin):
            data = {"tool_name": "Agent", "tool_input": {"subagent_type": "nico", "prompt": prompt, **tin},
                    "agent_type": "rio", "session_id": self.SID, "cwd": str(self.root),
                    "transcript_path": self.transcript(("assistant", "계획"), ("user", "승인"))}
            return hook.check(data, "claude")
        # 누출 표현이 없으면 통과하고 있으면 막는지 확인한다.
        doc.write_text("기준 커밋 cb78b92\n", encoding="utf-8")
        self.assertIsNone(call())
        for leak in self.LEAKS:
            doc.write_text(leak + "\n", encoding="utf-8")
            self.assertIsNotNone(call(), leak)
            doc.write_text("기준 커밋 cb78b92\n", encoding="utf-8")
            self.assertIsNotNone(call(description=leak), leak)  # 프롬프트 필드

    # 세션 기록 정보가 든 Codex 하위 thread 호출을 deny하는지 확인한다.
    def test_session_info_blocks_codex_child(self):
        pid = "0a1b2c3d-1111-2222-3333-444455556666"
        parent = self.sessions / f"rollout-1-{pid}.jsonl"
        child = self.sessions / "rollout-1-C.jsonl"
        parent.write_text("\n".join([codex_meta(), codex_msg("user", "승인")]) + "\n", encoding="utf-8")
        child.write_text("\n".join([
            codex_meta(agent_role="nico", source={"subagent": {"thread_spawn": {"parent_thread_id": pid}}}),
            codex_msg("user", "task: research\ninput: .agent-logs/task/t/research.md")]) + "\n", encoding="utf-8")
        doc = self.state / "research.md"
        data = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "transcript_path": str(child), "cwd": str(self.root)}
        doc.write_text("기준 커밋 cb78b92\n", encoding="utf-8")
        self.assertIsNone(hook.check(data, "codex"))
        for leak in ("부모 0a1b2c3d", *self.LEAKS):
            doc.write_text(leak + "\n", encoding="utf-8")
            self.assertIsNone(hook.check(data, "codex"), leak)


class CodexPath(unittest.TestCase):
    # 임시 홈, 작업 폴더, patch를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmp.name) / "work"
        self.home = Path(self.tmp.name) / "home"
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        settings = self.home / ".claude" / "settings.json"
        settings.write_text(json.dumps({"permissions": {"deny": [
            "Read(//**/.env)", "Read(//**/.env.*)", "Read(//**/secrets/**)", "Read(//**/*.pem)",
            "Read(~/.claude/.credentials.json)", "Read(~/.codex/auth.json)"]}}), encoding="utf-8")
        glob = (self.home / ".claude", self.home / ".codex")
        self.patches = [patch.object(sub_path, "HOME", self.home), patch.object(sub_path, "SETTINGS", settings),
                        patch.object(sub_path, "GLOBAL", glob)]
        self.temp = Path(self.tmp.name) / "ostemp"
        self.temp.mkdir()
        self.patches.append(patch.object(sub_path, "TEMP", self.temp))
        self.patches.append(patch.object(sub_scope, "approved", return_value=True))  # 경로 판정만 본다. 승인 검사는 WriteScope가 다룬다.
        self.local_settings = Path(self.tmp.name) / "local.json"
        self.wiki_skill = Path(self.tmp.name) / "wiki" / "SKILL.md"
        self.patches.extend([patch.object(sub_path, "LOCAL_SETTINGS", self.local_settings),
                             patch.object(sub_path, "WIKI_SKILL", self.wiki_skill)])
        for p in self.patches:
            p.start()

    # patch와 임시 폴더를 정리한다.
    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    # Codex 입력으로 Bash 검사를 실행하는 보조 함수이다.
    def bash(self, cmd):
        return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(self.cwd),
                           "agent_type": "rio"}, "codex")

    # 세션 경로 안 경로와 읽기 커맨드가 통과하는지 확인한다.
    def test_session_path_and_read_commands(self):
        for cmd in ("rg -n 'a|b' AGENTS.md", "Get-Content hooks/a.py", "cat ./x", "git log --oneline", "rg -e foo",
                    f"cat {self.home}/.claude/CLAUDE.md", f"ls {self.home}/.codex", "curl https://a.b/c",
                    'rg -n "a => b" AGENTS.md', "rg -n 'x>y' f", "cmd 2>/dev/null", "cmd > $null", "cmd >NUL"):
            self.assertIsNone(self.bash(cmd), cmd)

    # 보안 문서 경로가 deny되는지 확인한다.
    def test_secret_denied(self):
        for cmd in ("cat .env", "cat .env.local", "cat sub/secrets/a", "cat ./a.pem", f"cat {self.home}/.codex/auth.json",
                    "echo x > .env"):
            self.assertIsNotNone(self.bash(cmd), cmd)

    # 세션 경로 밖 경로가 deny되는지 확인한다.
    def test_outside_session_denied(self):
        for cmd in (f"cat {self.home}/notes.txt", "cat ../x", "cat ~/x", "cat 'C:\\Windows\\x.txt'",
                    "cat ../../outside.txt"):
            self.assertIsNotNone(self.bash(cmd), cmd)

    # 이스케이프 문자열은 경로로 보지 않는다. 인라인 코드는 경로가 아니라 쓰기 판정으로 막힌다.
    def test_escape_text_not_path(self):
        self.assertIsNone(self.bash("printf '%s\\t' x"))
        for cmd in ("python -c \"print('a\\nb')\"", "python - <<'EOF'\nx.replace('\\n',' ')\nEOF"):
            self.assertIsNone(self.bash(cmd), cmd)  # Codex는 인라인 코드를 hook에서 막지 않고 prompt 규칙에 맡긴다.

    # 이스케이프, 슬래시 옵션, 스킬 호출 표기, heredoc 본문, 검색 패턴은 경로로 보지 않는다.
    def test_false_positive_paths_allowed(self):
        for cmd in ("taskkill //PID 3564 //T //F", r'powershell -Command "function K(\$p){ K \$p }"',
                    r"""grep -o 'a [^"\\]\{0,80\}' file.txt""", r"echo \$nestlab-plan", r"printf a\n", """python -c "x='\\n'" """,
                    "python -I - <<'EOF'\nprint(f\"shared/skills/{name}/SKILL.md\")\nEOF",
                    "sed -n '/^18[45][0-9]:/p' a.txt", 'grep -rn x hooks/ | grep -v "/tests/"',
                    r"""for s in a b; do grep -o 'x [^"\\]\{0,80\}' f.txt; done"""):
            self.assertIsNone(self.bash(cmd), cmd)

    # PowerShell 단일 인용 here-string 본문의 예시 경로는 LF, CRLF, 닫는 표식 뒤 파이프에서도 경로로 보지 않는다.
    def test_here_string_body_not_path(self):
        for nl in ("\n", "\r\n"):
            cmd = f"@'{nl}open('/research.md').read(){nl}'@ | python -I -"
            self.assertIsNone(self.bash(cmd), repr(cmd))

    # here-string 밖 경로, 닫히지 않은 문자열, 잘못된 표식, 평가되는 double-quoted here-string은 계속 검사한다.
    def test_here_string_not_hiding_paths(self):
        for cmd in ("@'\nx\n'@ | python -I -; cat /etc/hosts", "@'\nx\n'@ > /etc/out.txt", "@'\ncat /etc/hosts\nx"):
            self.assertIsNotNone(self.bash(cmd), repr(cmd))

    # 세션 밖 경로는 이스케이프나 검색 커맨드 형태에서도 deny한다. 사전실측 사례는 현재 deny 판정을 고정한다.
    def test_outside_paths_still_denied(self):
        for cmd in ("ls -d ~/.hermes", "ls ~/.orca/agent-hooks/", 'cp "/c/Users/Administrator/Desktop/a.png" x.png',
                    r"cat \Windows\x", 'cat "C:/Users/Administrator/.orca/x"', "cat /etc/hosts",
                    "grep x /etc/hosts", "grep -rn x ../outside", "for f in a; do cat ~/.orca/x; done"):
            self.assertIsNotNone(self.bash(cmd), cmd)

    # ~/.claude 및 ~/.codex 쓰기는 승인 턴에도 막고 읽기는 통과한다. Claude 및 Codex 입력이 같다.
    def test_global_folder_write_denied(self):
        # 홈 폴더 경로를 준비한다.
        h = self.home
        # 경로를 추가하는 apply_patch 입력으로 검사를 실행한다.
        def patch_(path):
            text = chr(10).join(["*** Begin Patch", f"*** Add File: {path}", "+x", "*** End Patch"])
            return hook.check({"tool_name": "apply_patch", "tool_input": {"command": text}, "cwd": str(self.cwd),
                               "agent_type": "buddy"}, "codex")
        # 전역 폴더 쓰기는 모두 막고 읽기는 허용하는지 확인한다.
        for path in (h / ".claude/plans/x.md", h / ".claude/settings.json", h / ".codex/config.toml"):
            self.assertIn("~/.claude 또는 ~/.codex", self.claude("Write", {"file_path": str(path), "content": "x"}) or "", path)
            self.assertIn("~/.claude 또는 ~/.codex", patch_(path) or "", path)
            self.assertIn("~/.claude 또는 ~/.codex", self.bash(f"echo a > {path}") or "", path)
            self.assertIsNone(self.claude("Bash", {"command": f"cat {path}"}), path)
        # 원본으로만 쓰인 경로는 통과하고 바뀌는 경로는 deny한다.
        t = self.temp
        self.assertNotIn("~/.claude 또는 ~/.codex", self.bash(f"cp {h}/.claude/settings.json {t}/a.json") or "")
        for cmd in (f"cp a.json {h}/.claude/x.json", f"echo x > {h}/.claude/x", f"rm {h}/.claude/x",
                    f"mv {h}/.claude/x {t}/x"):
            self.assertIn("~/.claude 또는 ~/.codex", self.bash(cmd) or "", cmd)
        for path in (h / ".claude/.credentials.json", h / ".codex/auth.json"):
            self.assertIsNotNone(self.claude("Write", {"file_path": str(path), "content": "x"}), path)
            self.assertIsNotNone(patch_(path), path)
            self.assertIsNotNone(self.bash(f"echo a > {path}"), path)

    # apply_patch 대상 경로가 같은 경로 규칙으로 판정되는지 확인한다.
    def test_apply_patch(self):
        # 경로를 추가하는 apply_patch 입력으로 검사를 실행한다.
        def patch_(path):
            text = chr(10).join(["*** Begin Patch", f"*** Add File: {path}", "+x", "*** End Patch"])
            return hook.check({"tool_name": "apply_patch", "tool_input": {"command": text}, "cwd": str(self.cwd),
                               "agent_type": "buddy"}, "codex")
        # apply_patch 경로별 결과를 확인한다.
        self.assertIsNone(patch_("src/a.py"))
        self.assertIsNotNone(patch_(".env"))
        self.assertIsNotNone(patch_("../a.py"))
        self.assertIsNotNone(patch_(f"{self.home}/.claude/a.md"))

    # Claude runner 입력으로 도구 검사를 실행하는 보조 함수이다.
    def claude(self, name, tin, runner="claude"):
        if name == "Bash":
            tin = {"description": "테스트 커맨드", **tin}
        return hook.check({"tool_name": name, "tool_input": tin, "cwd": str(self.cwd), "agent_type": "rio"}, runner)

    # Claude runner도 읽기와 쓰기 경로를 검사하는지 확인한다.
    def test_claude_runner_checks_reads_and_writes(self):
        run = self.claude
        for name, tin in (("Bash", {"command": "cat .env"}), ("Bash", {"command": f"cat {self.home}/notes.txt"}),
                          ("Read", {"file_path": ".env"}), ("Read", {"file_path": f"{self.home}/notes.txt"}),
                          ("Grep", {"pattern": "x", "path": str(self.home)}), ("Glob", {"pattern": "*", "path": "../x"}),
                          ("Bash", {"command": f"echo a > {self.home}/notes.txt"}),
                          ("Write", {"file_path": f"{self.home}/notes.txt", "content": "x"}),
                          ("Write", {"file_path": ".env", "content": "x"})):
            self.assertIsNotNone(run(name, tin), (name, tin))
        self.assertIn(str(self.home), run("Read", {"file_path": f"{self.home}/notes.txt"}))
        self.assertEqual(sub_write.REASON_REDIRECT.format(target="out.txt") + " " + hook.REASON_EDIT_GUIDE,
                         run("Bash", {"command": "echo hi > out.txt"}))
        for name, tin in (("Bash", {"command": "cat ./x"}),
                          ("Read", {"file_path": "AGENTS.md"}), ("Read", {"file_path": f"{self.home}/.claude/CLAUDE.md"}),
                          ("Grep", {"pattern": "x"}), ("Glob", {"pattern": "*"}),
                          ("Write", {"file_path": "src/a.py", "content": "x"})):
            self.assertIsNone(run(name, tin), (name, tin))

    # Windows 짧은 경로 이름(e.g. ADMINI~1) 표기이다. 다른 OS는 그대로 쓴다.
    def short(self, path):
        if os.name != "nt":
            return str(path)
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
        return buf.value or str(path)

    # OS 임시 폴더는 두 runner에서 읽기와 쓰기를 허용하고 보안 문서만 막는다.
    def test_temp_allowed_both_runners(self):
        (self.temp / "a").write_text("x", encoding="utf-8")
        for base in (str(self.temp), self.short(self.temp), str(self.temp).replace("\\", "/")):
            for runner in ("claude", "codex"):
                run = lambda cmd: self.claude("Bash", {"command": cmd}, runner)
                self.assertIsNone(run(f"cat {base}/a"), (base, runner))
                self.assertIsNotNone(run(f"cat {base}/.env"), (base, runner))
            self.assertIsNone(self.claude("Read", {"file_path": f"{base}/a"}), base)
            self.assertIsNone(self.claude("Write", {"file_path": f"{base}/new", "content": "x"}), base)
            self.assertIsNotNone(self.claude("Read", {"file_path": f"{base}/.env"}), base)

    # 다른 cwd에서도 위키 저장소 설정이 바로 적용되는지 확인한다.
    def test_wiki_repo_settings_apply_immediately_from_other_cwd(self):
        wiki_repo = Path(self.tmp.name) / "wiki_repo"
        wiki_repo.mkdir()
        self.local_settings.write_text(json.dumps({"archive_root": str(wiki_repo)}), encoding="utf-8")
        for runner in ("claude", "codex"):
            for name, tin in (("Read", {"file_path": str(wiki_repo / "AGENTS.md")}),
                              ("Grep", {"path": str(wiki_repo / "wiki"), "pattern": "x"}),
                              ("Glob", {"path": str(wiki_repo), "pattern": "**/*.md"}),
                              ("Write", {"file_path": str(wiki_repo / "wiki" / "new.md"), "content": "x"}),
                              ("Bash", {"command": f"cat {wiki_repo}/AGENTS.md"})):
                self.assertIsNone(self.claude(name, tin, runner), (name, runner))
            self.assertIsNotNone(self.claude("Read", {"file_path": str(wiki_repo) + "-other/a"}, runner))
            self.assertIsNotNone(self.claude("Read", {"file_path": str(wiki_repo / ".." / "other")}, runner))
            self.assertIsNotNone(self.claude("Read", {"file_path": str(wiki_repo / ".env")}, runner))
            self.assertIsNotNone(self.claude("Write", {"file_path": str(wiki_repo / "secrets" / "a")}, runner))
        self.local_settings.write_text("{}", encoding="utf-8")
        self.assertIsNotNone(self.claude("Read", {"file_path": str(wiki_repo / "AGENTS.md")}))
        for content in ("invalid", "[]", '{"archive_root": "../wiki_repo"}',
                        '{"archive_root": 42}', '{"archive_root": null}',
                        json.dumps({"archive_root": str(wiki_repo / "missing")}),
                        json.dumps({"archive_root": wiki_repo.anchor})):
            self.local_settings.write_text(content, encoding="utf-8")
            self.assertIsNotNone(self.claude("Read", {"file_path": str(wiki_repo / "AGENTS.md")}), content)
        self.local_settings.unlink()
        self.assertIsNotNone(self.claude("Read", {"file_path": str(wiki_repo / "AGENTS.md")}))

    # 세션 밖에서는 스킬 원본과 local.json만 읽을 수 있는지 확인한다.
    def test_only_skill_and_local_settings_are_readable_outside_session(self):
        for path in (self.local_settings, self.wiki_skill):
            self.assertIsNone(self.claude("Read", {"file_path": str(path)}))
            self.assertIsNone(self.bash(f"cat {path}"))
            self.assertIsNotNone(self.claude("Write", {"file_path": str(path), "content": "x"}))
            self.assertIsNotNone(self.bash(f"echo x > {path}"))
            self.assertIsNotNone(self.claude("Read", {"file_path": str(path.parent / "other.json")}))

    # 폴더 링크(junction 또는 symlink)를 만드는 보조 함수이다.
    def directory_link(self, link, target):
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           check=True, capture_output=True)
        else:
            link.symlink_to(target, target_is_directory=True)

    # 짧은 이름과 긴 이름 경로를 같은 경로로 판정하는지 확인한다.
    def test_short_and_long_wiki_repo_and_settings_paths_are_equivalent(self):
        wiki_repo = Path(self.tmp.name) / "wiki_repo-long-directory-name"
        wiki_repo.mkdir()
        for setting_root in (str(wiki_repo), self.short(wiki_repo)):
            self.local_settings.write_text(json.dumps({"archive_root": setting_root}), encoding="utf-8")
            for base in (str(wiki_repo), self.short(wiki_repo)):
                for runner in ("claude", "codex"):
                    self.assertIsNone(self.claude("Read", {"file_path": f"{base}/AGENTS.md"}, runner))
                    self.assertIsNone(self.claude("Write", {"file_path": f"{base}/new.md"}, runner))
                    self.assertIsNotNone(self.claude("Read", {"file_path": f"{base}/.env"}, runner))
                    self.assertIsNotNone(self.claude("Read", {"file_path": f"{base}/../outside"}, runner))
            self.assertIsNone(self.claude("Read", {"file_path": self.short(self.local_settings)}))
            self.assertIsNotNone(self.claude("Write", {"file_path": self.short(self.local_settings)}))

    # 전역 wiki 링크가 내부 자료 읽기를 허용하는지 확인한다.
    def test_global_wiki_directory_link_reads_internal_resources(self):
        self.wiki_skill.parent.mkdir()
        self.wiki_skill.write_text("skill", encoding="utf-8")
        link = self.home / ".agents" / "skills" / "wiki"
        link.parent.mkdir(parents=True, exist_ok=True)
        self.directory_link(link, self.wiki_skill.parent)
        for runner in ("claude", "codex"):
            self.assertIsNone(self.claude("Read", {"file_path": str(link / "SKILL.md")}, runner))
            self.assertIsNone(self.claude("Bash", {"command": f"cat {link}/SKILL.md"}, runner))
            self.assertIsNotNone(self.claude("Write", {"file_path": str(link / "SKILL.md")}, runner))
            self.assertIsNone(self.claude("Read", {"file_path": str(link / "other.md")}, runner))

    # 위키 저장소 밖으로 나가는 링크와 보안 문서 대상이 deny되는지 확인한다.
    def test_wiki_repo_symlink_escape_and_secret_target_are_denied(self):
        wiki_repo = Path(self.tmp.name) / "wiki_repo"
        wiki_repo.mkdir()
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        self.local_settings.write_text(json.dumps({"archive_root": str(wiki_repo)}), encoding="utf-8")
        (wiki_repo / "secrets").mkdir()
        self.directory_link(wiki_repo / "escape", outside)
        self.directory_link(wiki_repo / "secret-link", wiki_repo / "secrets")
        for runner in ("claude", "codex"):
            for name, tin in (("Read", {"file_path": str(wiki_repo / "escape" / "a")}),
                              ("Write", {"file_path": str(wiki_repo / "escape" / "a"), "content": "x"}),
                              ("Grep", {"path": str(wiki_repo / "escape"), "pattern": "x"}),
                              ("Glob", {"path": str(wiki_repo / "escape"), "pattern": "*"}),
                              ("Bash", {"command": f"cat {wiki_repo}/escape/a"}),
                              ("Read", {"file_path": str(wiki_repo / "secret-link" / "a")})):
                self.assertIsNotNone(self.claude(name, tin, runner), (name, runner))
            self.assertIsNotNone(self.claude("Read", {"file_path": self.short(wiki_repo) + "/escape/a"}, runner))


# 서브에이전트 입력 형식
STATE_IN = "상태 문서: .agent-logs/task/20261006-0555-x/status.md\n절: 요구사항 목록"
RESEARCH_IN = "task: research\ninput: .agent-logs/task/20261006-0555-x/research.md"
INPUT_IN = "task: implement\ninput: .agent-logs/task/20261006-0555-x/spec.md"
REVIEW_IN = "task: verify\ninput: .agent-logs/task/20261006-0555-x/spec.md"
REFUTE = "task: refute\ninput: .agent-logs/task/20261006-0555-x/refute.md"
OLD_REFUTE = "task: refute\n주장: 1. 변경이 요구를 충족한다.\n증거: 코드 발췌\n출처: hooks/a.py:12\n판정 기준: 요구 충족\n원문 발췌: 확인할 원문"
# 역할별로 허용하는 정상 배정이다.
GOOD = {"jelly": INPUT_IN, "worker": INPUT_IN, "pepper": INPUT_IN, "documenter": INPUT_IN, "ricky": REVIEW_IN, "reviewer": REVIEW_IN}


# Claude Agent 호출 입력을 만든다. sub이면 서브에이전트 안의 호출이다.
def claude(target, prompt, sub=False):
    d = {"tool_name": "Agent", "tool_input": {"subagent_type": target, "prompt": prompt}}
    return {**d, "agent_id": "a1"} if sub else d


# Codex spawn_agent 호출 입력을 만든다.
def codex(target, prompt):
    return {"tool_name": "spawn_agent", "tool_input": {"agent_type": target, "message": prompt, "fork_turns": "none"}}


# UTF-8을 강제하지 않은 환경에서 hook 스크립트를 실행하고 stdout을 반환한다.
def run_utf8(data, *args, cwd=None):
    env = {**os.environ, "PYTHONIOENCODING": "", "PYTHONUTF8": "0"}
    return subprocess.run([sys.executable, "-B", hook.__file__, *args], env=env, capture_output=True, check=True,
                          input=json.dumps(data, ensure_ascii=False).encode("utf-8"), cwd=cwd).stdout.decode("utf-8")


class RecordLeak(unittest.TestCase):
    """작업 문서 쓰기와 서브에이전트 호출에서 사건 문서 경로와 세션 기록 정보를 차단한다."""
    transcript, write = WriteScope.transcript, WriteScope.write

    # 임시 저장소 보조 상태를 준비한다.
    def setUp(self):
        WriteScope.setUp(self)

    # 상위 정리를 호출한다.
    def tearDown(self):
        WriteScope.tearDown(self)

    # 조사 문서를 만들고 reviewer 반증 호출 검사를 실행한다.
    def refute(self, research_text):
        folder = self.root / ".agent-logs/task/t"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "refute.md").write_text(research_text, encoding="utf-8")
        data = claude("ricky", "task: refute\ninput: .agent-logs/task/t/refute.md")
        return sub_input.check_input({**data, "cwd": str(self.root)}, "claude")

    # 작업 문서 쓰기에서 사건 문서 파일 경로와 세션 기록 정보는 deny되고 폴더 이름만 쓴 줄은 통과하는지 확인한다.
    def test_log_doc_write(self):
        self.assertIsNotNone(self.write(".agent-logs/task/x/status.md", "- 근거: .agent-logs/incident/20261026-ab12cd34.md"))
        self.assertIsNotNone(self.write(".agent-logs/task/x/status.md", "- 근거: 기록 a.jsonl"))
        self.assertIsNone(self.write(".agent-logs/task/x/status.md", "- 근거: .agent-logs/incident 폴더, 검증/확인, chat_state/a.json"))
        self.assertIsNone(self.write("README.md", "- 근거: .agent-logs/incident/a.md"))

    # 출처로 쓸 수 없는 경로(작업 문서 행 인용, 세션 id 앞 8자, 저장소 밖 경로)는 모든 역할의 작업 문서 쓰기와 질문에서 deny되고 추적 파일 경로와 URL은 통과한다.
    def test_source_leak(self):
        (self.root / "hooks").mkdir()
        (self.root / "hooks/a.py").write_text("x\n" * 3, encoding="utf-8")
        reason = lambda found: sub_path.REASON_SOURCE_PATH.format(found=found)
        bad = {"- 근거: .agent-logs/task/t/status.md:5": ".agent-logs/task/t/status.md:5",
               "- 근거: spec.md:12": "spec.md:12",
               "- 근거: C:/Windows/system32/a.txt": "C:/Windows/system32/a.txt"}
        for role in ("rio", "jelly"):
            for line, found in bad.items():
                with self.subTest(role=role, line=line):
                    self.assertEqual(self.write(".agent-logs/task/t/spec.md", line, role=role), reason(found))
            self.assertIsNone(self.write(".agent-logs/task/t/spec.md", "- 근거: hooks/a.py:1, https://example.com/a/b.md:3", role=role))
        data = {"session_id": "abcdef12-0000-0000-0000-000000000000"}
        self.assertEqual(sub_path.source_leak("기록 abcdef12 확인", data, self.root), "abcdef12")
        self.assertIsNone(sub_path.source_leak("hooks/a.py:2", data, self.root))
        question = {"tool_name": "AskUserQuestion", "cwd": str(self.root), "tool_input": {"questions": [
            {"question": "검토 1. X: Y을 정한다. (문제 1) (작업 1110-005442)",
             "options": [{"label": "안 1. A", "description": "C:/Windows/a.txt를 읽는다."}, {"label": "안 2. B", "description": "B를 한다."}]}]}}
        self.assertIn("C:/Windows/a.txt", sub_question.check_question(question) or "")

    # 표 칸의 `\|`, 정규식 이스케이프와 검색 패턴은 출처로 쓸 수 없는 경로가 아니다.
    def test_source_leak_ignores_escapes_and_patterns(self):
        data = {"session_id": "abcdef12-0000-0000-0000-000000000000"}
        for line in ("| 열 \\| 열 | hooks/a.py:1 |", "검색: grep -n 'a\\.agent' hooks", "정규식 `^\\.agent-logs/` 확인", "a \\ b"):
            self.assertIsNone(sub_path.source_leak(line, data, self.root), line)
        self.assertEqual(sub_path.source_leak("C:/Windows/a.txt", data, self.root), "C:/Windows/a.txt")

    # 조사 원문 밖 절의 세션 기록은 deny되고 조사 원문 절의 세션 기록은 통과하는지 확인한다.
    def test_refute_call_skips_research_section(self):
        leak = "기록 a.jsonl"
        self.assertIsNotNone(self.refute(f"# t\n\n## 메모\n{leak}\n\n## 조사 단계 원문\n"))
        self.assertIsNone(self.refute(f"# t\n\n## 메모\n없음\n\n## 조사 단계 원문\n{leak}\n"))


class SubagentPath(unittest.TestCase):
    """서브에이전트는 저장소 안(사건 문서 폴더 제외) 경로만 접근한다."""

    # 임시 저장소, 홈, 임시 폴더와 patch를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.project, self.home, self.temp = base / "project", base / "home", base / "ostemp"
        for d in (self.project / "hooks", self.project / ".agent-logs" / "incident", self.home / ".claude", self.temp):
            d.mkdir(parents=True, exist_ok=True)
        self.patches = [patch.object(sub_path, "HOME", self.home), patch.object(sub_path, "TEMP", self.temp),
                        patch.object(sub_path, "GLOBAL", (self.home / ".claude", self.home / ".codex")),
                        patch.object(sub_path, "INCIDENT", self.project / ".agent-logs" / "incident"),
                        patch.object(sub_path, "SETTINGS", self.home / ".claude" / "settings.json")]
        for p in self.patches:
            p.start()
        # Codex 하위 thread rollout을 만든다.
        self.rollout = base / "rollout.jsonl"
        self.rollout.write_text(codex_meta(source={"subagent": {"thread_spawn": {"parent_thread_id": "P"}}}) + "\n",
                                encoding="utf-8")

    # patch와 임시 폴더를 정리한다.
    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    # 도구 입력으로 경로 검사를 실행한다. sub가 "claude"면 agent_id, "codex"면 하위 thread, 아니면 메인 세션이다.
    def check(self, name, tin, sub):
        data = {"tool_name": name, "tool_input": tin, "cwd": str(self.project), "agent_type": "buddy"}
        if sub == "claude":
            data["agent_id"] = "a1"
        elif sub == "codex":
            data["transcript_path"] = str(self.rollout)
        return sub_path.check_paths(data, "codex" if sub == "codex" else "claude")

    # 서브에이전트가 홈 설정 폴더를 가리키면 deny하고, 사건 문서 폴더와 임시 폴더 전체(하네스 기록, scratchpad 포함)는 통과한다.
    def test_sub_blocked(self):
        harness = self.temp / "claude" / "h" / "s"
        for sub in ("claude", "codex"):
            self.assertIsNone(self.check("Read", {"file_path": str(self.project / ".agent-logs/incident/a.md")}, sub), sub)
            self.assertIsNone(self.check("Read", {"file_path": str(harness / "tasks" / "a.output")}, sub), sub)
            self.assertIsNone(self.check("Write", {"file_path": str(harness / "scratchpad" / "a.py"), "content": "x"}, sub), sub)
            self.assertIsNotNone(self.check("Read", {"file_path": str(self.home / ".claude/a.md")}, sub), sub)
            self.assertIsNone(self.check("Bash", {"command": f"cat {self.temp}/a.txt"}, sub), sub)

    # 서브에이전트의 저장소 안 경로와 메인 세션의 홈 설정 폴더 경로는 통과하는지 확인한다.
    def test_sub_inside_and_main_unchanged(self):
        for sub in ("claude", "codex"):
            self.assertIsNone(self.check("Read", {"file_path": str(self.project / "hooks/a.py")}, sub), sub)
        self.assertIsNone(self.check("Read", {"file_path": str(self.home / ".claude/a.md")}, None))

    # 서브에이전트가 입력 문서의 작업 폴더에서는 입력 문서만 읽고, 그 작업 폴더를 포함하는 폴더는 대상으로 삼지 못하며, 반증 reviewer는 Bash를 쓰지 못하는지 확인한다.
    def test_sub_task_doc_access_by_input_document(self):
        def extra(prompt):
            transcript = Path(self.tmp.name) / "agent.jsonl"
            transcript.write_text(claude_line("user", prompt) + "\n", encoding="utf-8")
            return {"agent_transcript_path": str(transcript)}
        task = self.project / ".agent-logs/task/20261006-0555-x"
        in_task = lambda name: {"file_path": str(task / f"{name}.md")}

        # 입력 문서와 .agent-logs/ 밖 폴더 검색은 허용하고 같은 작업의 다른 문서는 입력 문서 안내로 deny한다.
        for role, prompt, doc in (("jelly", INPUT_IN, "spec"), ("nico", RESEARCH_IN, "research"), ("ricky", REVIEW_IN, "spec")):
            with self.subTest(role=role):
                opt = extra(prompt)
                self.assertIsNone(self.check_as(role, "Read", in_task(doc), **opt))
                self.assertIsNone(self.check_as(role, "Grep", {"path": str(self.project / "hooks"), "pattern": "x"}, **opt))
                other = "status" if doc != "status" else "spec"
                self.assertIn(f".agent-logs/task/20261006-0555-x/{doc}.md", self.check_as(role, "Read", in_task(other), **opt) or "")
                # 다른 작업 폴더의 작업 문서는 입력 문서 경계 밖이므로 통과한다.
                other_task = self.project / ".agent-logs/task/20261007-0000-y"
                other_task.mkdir(parents=True, exist_ok=True)
                (other_task / "spec.md").write_text("x", encoding="utf-8")
                self.assertIsNone(self.check_as(role, "Read", {"file_path": str(other_task / "spec.md")}, **opt))

        # 저장소 루트, .agent-logs/, 작업 폴더를 Grep 또는 Glob 대상으로 삼으면 deny한다.
        opt = extra(INPUT_IN)
        for folder in (self.project, self.project / ".agent-logs", self.project / ".agent-logs/task", task):
            self.assertIn("입력 문서", self.check_as("pepper", "Grep", {"path": str(folder), "pattern": "x"}, **opt) or "")
            self.assertIn("입력 문서", self.check_as("ricky", "Glob", {"path": str(folder), "pattern": "*.md"}, **extra(REVIEW_IN)) or "")

        # 반증 reviewer는 반증 문서만 읽고 Bash는 막힌다.
        opt = extra(REFUTE)
        self.assertIsNone(self.check_as("ricky", "Read", in_task("refute"), **opt))
        self.assertIn("입력 문서", self.check_as("ricky", "Read", in_task("spec"), **opt) or "")
        self.assertIn("Bash", self.check_as("ricky", "Bash", {"command": "ls"}, **opt) or "")

    # 서브에이전트 역할을 지정해 경로 검사를 실행하는 보조 함수이다.
    def check_as(self, role, name, tin, **extra):
        data = {"tool_name": name, "tool_input": tin, "cwd": str(self.project), "agent_type": role,
                "agent_id": "a1", **extra}
        return sub_path.check_paths(data, "claude")


class AgentInput(unittest.TestCase):
    check = staticmethod(sub_input.check_input)

    # 자유 문장 요청이 deny되는지 확인한다.
    def test_free_text_denied(self):
        for runner, make in (("claude", claude), ("codex", codex)):
            with self.subTest(runner=runner):
                self.assertIsNotNone(self.check(make("jelly", "이전 대화 요약: ..."), runner))
                self.assertIsNotNone(self.check(make("jelly", INPUT_IN + "\n추가 설명"), runner))
                self.assertIsNone(self.check(make("jelly", INPUT_IN), runner))
                self.assertIsNone(self.check(make("nico", RESEARCH_IN), runner))

    # Agent 호출의 model 인자는 deny하고, model 인자가 없는 정상 배정은 통과하는지 확인한다.
    def test_model_argument_denied(self):
        call = claude("jelly", INPUT_IN)
        self.assertEqual(self.check({**call, "tool_input": {**call["tool_input"], "model": "fable"}}, "claude"), sub_input.REASON_MODEL)
        self.assertIsNone(self.check(call, "claude"))

    # 한국어 작업 종류 줄과 대문자 키는 지원하지 않는 형식 사유로, 허용 목록 밖 task 값은 task 값 사유로 deny된다.
    def test_legacy_and_unknown_formats_denied(self):
        doc = ".agent-logs/task/20261006-0555-x/research.md"
        cases = {"작업 종류: 조사\ninput:" + doc: sub_input.REASON_UNSUPPORTED,
                 "Task: research\nDocument: " + doc: sub_input.REASON_UNSUPPORTED,
                 "task: research\n조사 문서: " + doc: sub_input.REASON_DOCUMENT.format(task="research", file="research"),
                 "task: document\ninput:" + doc.replace("research", "spec"): sub_input.REASON_UNSUPPORTED_TASK.format(task="document")}
        for runner, make in (("claude", claude), ("codex", codex)):
            for prompt, reason in cases.items():
                with self.subTest(runner=runner, prompt=prompt):
                    self.assertEqual(self.check(make("nico", prompt), runner), reason)

    # 역할이 맡지 않는 task 값과 문서 종류가 맞지 않는 배정은 deny된다.
    def test_task_matches_role_and_document(self):
        self.assertEqual(self.check(claude("reviewer", INPUT_IN), "claude"), sub_input.REASON_STAGE_ROLE.format(role="reviewer", task="implement"))
        self.assertIsNotNone(self.check(claude("jelly", REVIEW_IN), "claude"))
        self.assertEqual(self.check(claude("jelly", INPUT_IN.replace("spec", "research")), "claude"),
                         sub_input.REASON_DOCUMENT.format(task="implement", file="spec"))

    # worker와 reviewer가 입력 문서 경로만 받는지 확인한다.
    def test_worker_reviewer_take_input_doc_only(self):
        for target, good in (("jelly", INPUT_IN), ("worker", INPUT_IN), ("ricky", REVIEW_IN), ("reviewer", REVIEW_IN)):
            self.assertIsNotNone(self.check(claude(target, STATE_IN), "claude"))
            self.assertIsNone(self.check(claude(target, good), "claude"))

    # researcher가 조사 문서 경로만 받는지 확인한다.
    def test_researcher_takes_research_doc_only(self):
        self.assertIsNotNone(self.check(claude("researcher", INPUT_IN), "claude"))
        self.assertIsNotNone(self.check(claude("researcher", STATE_IN), "claude"))
        self.assertIsNotNone(self.check(claude("researcher", RESEARCH_IN + "\n추가 설명"), "claude"))
        self.assertIsNone(self.check(claude("researcher", RESEARCH_IN), "claude"))

    # researcher 재조사 요청은 `item: research <번호>` 줄만 더할 수 있고, 차단 사유가 실제 허용 형식을 안내한다.
    def test_researcher_reason_matches_allowed_format(self):
        reason = self.check(claude("researcher", STATE_IN), "claude")
        for word in ("task:", "input:", "item: research"):
            self.assertIn(word, reason)
        self.assertIsNone(self.check(claude("researcher", RESEARCH_IN + "\nitem: research 1"), "claude"))
        self.assertIsNotNone(self.check(claude("researcher", RESEARCH_IN + "\n항목: 조사 1. 도구"), "claude"))

    # 구현과 재구현 배정은 `item: requirement <번호>[, <번호>]` 줄로 요구사항을 나눌 수 있고, 다른 단계와 형식은 막는다.
    def test_requirement_item_line(self):
        reimpl = INPUT_IN.replace("implement", "reimplement", 1)
        for prompt in (INPUT_IN + "\nitem: requirement 28", reimpl + "\nitem: requirement 40, 11"):
            self.assertIsNone(self.check(claude("jelly", prompt), "claude"))
        self.assertIsNone(self.check(claude("pepper", INPUT_IN + "\nitem: requirement 3"), "claude"))
        self.assertIsNotNone(self.check(claude("jelly", INPUT_IN + "\nitem: research 1"), "claude"))
        self.assertIsNotNone(self.check(claude("jelly", INPUT_IN + "\nitem: requirement 28만"), "claude"))
        self.assertIsNotNone(self.check(claude("researcher", RESEARCH_IN + "\nitem: requirement 1"), "claude"))
        self.assertIn("item: requirement", self.check(claude("jelly", INPUT_IN + "\n추가"), "claude"))

    # documenter가 task 값과 명세 문서 경로만 받는지 확인한다.
    def test_documenter_takes_kind_and_input_doc_only(self):
        self.assertIsNone(self.check(claude("pepper", INPUT_IN), "claude"))
        self.assertIsNone(self.check(claude("documenter", INPUT_IN), "claude"))
        for bad in ("기록 문구", STATE_IN, "task: implement\n상태 문서: .agent-logs/task/a/status.md", INPUT_IN + "\n절: 요구사항",
                    INPUT_IN + "\n항목: 1", INPUT_IN + "\n" + INPUT_IN):
            self.assertIsNotNone(self.check(claude("pepper", bad), "claude"), bad)

    # 호출자가 메인 세션인지 서브에이전트인지에 따라 검사 여부가 달라지는지 확인한다.
    def test_caller_detection(self):
        self.assertIsNone(self.check(claude("jelly", "자유 문장", sub=True), "claude"))  # Claude 서브에이전트
        self.assertIsNotNone(self.check(codex("nico", "자유 문장"), "codex"))  # director의 researcher 호출
        self.assertIsNotNone(self.check(codex("jelly", "자유 문장"), "codex"))

    # fork_turns가 none이 아닌 Codex 호출이 deny되는지 확인한다.
    def test_codex_fork_denied(self):
        base = codex("jelly", INPUT_IN)
        for fork, denied in ((None, True), ("all", True), ("none", False)):
            tin = {k: v for k, v in base["tool_input"].items() if k != "fork_turns"}
            if fork:
                tin["fork_turns"] = fork
            reason = hook.check_fork({**base, "tool_input": tin}, "codex")
            self.assertEqual(reason is not None, denied, fork)

    # 서브에이전트 호출이 아닌 도구는 입력 형식 검사를 받지 않는지 확인한다.
    def test_other_tools_ignored(self):
        self.assertIsNone(self.check({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "claude"))

    # 등록된 역할만 검사하고 다른 에이전트는 통과하는지 확인한다.
    def test_registered_roles_and_unrelated_agents(self):
        for runner, make in (("claude", claude), ("codex", codex)):
            for target in ("Explore", "general-purpose", "custom-agent", "director", "rio", "assistant", "buddy"):
                with self.subTest(runner=runner, target=target):
                    self.assertIsNone(self.check(make(target, "자유 형식 입력"), runner))
            for target in sub_call.ROLES - sub_call.RESEARCHER:
                with self.subTest(runner=runner, target=target):
                    self.assertIsNone(self.check(make(target, GOOD[target]), runner))
                    self.assertIsNotNone(self.check(make(target, "자유 형식 입력"), runner))
            for target in sub_call.RESEARCHER:
                self.assertIsNone(self.check(make(target, RESEARCH_IN), runner))
                self.assertIsNotNone(self.check(make(target, "자유 형식 입력"), runner))

    # Codex follow-up 담당은 같은 세션 기록의 같은 task_name spawn 호출 agent_type으로 정하고, 기록이 없으면 새 배정을 요구한다.
    def test_followup_role_from_spawn_record(self):
        name = "implement__20261006_055500_x"
        data = {"tool_name": "followup_task", "tool_input": {"target": "/root/" + name, "message": "gAAAAAopaque"}}
        with tempfile.TemporaryDirectory() as folder:
            rollout = Path(folder) / "rollout.jsonl"
            spawn = {"type": "response_item", "payload": {"type": "function_call", "name": "spawn_agent", "call_id": "c1",
                                                          "arguments": json.dumps({"agent_type": "pepper", "task_name": name})}}
            rollout.write_text(json.dumps(spawn) + "\n", encoding="utf-8")
            self.assertEqual(sub_call.call_info({**data, "transcript_path": str(rollout)})[0], "documenter")
            self.assertIsNone(self.check({**data, "transcript_path": str(rollout)}, "codex"))
            rollout.write_text("", encoding="utf-8")
            self.assertEqual(self.check({**data, "transcript_path": str(rollout)}, "codex"), sub_input.REASON_NO_SPAWN)

    # reviewer 반증 요청이 정해진 두 줄 형식만 허용하는지 확인한다.
    def test_reviewer_explicit_refutation_mode(self):
        invalid = (REFUTE + "\ntask: refute", REFUTE.replace("refute", "verify", 1),
                   REFUTE + "\n" + INPUT_IN, REFUTE + "\n" + STATE_IN,
                   REFUTE + "\n전체 대화: 이전 대화", REFUTE + "\n도구 출력: 로그",
                   REFUTE + "\n중간 설명: 작업 설명", REFUTE + "\n추론: 사고 과정",
                   REFUTE + "\n이전 대화 요약", REFUTE.replace("task: refute\n", ""),
                   "task: refute", "task: refute\n주장: ", OLD_REFUTE,
                   REFUTE.replace("document", "state").replace("refute.md", "status.md"))
        for runner, make in (("claude", claude), ("codex", codex)):
            for target in sub_call.REVIEWER:
                with self.subTest(runner=runner, target=target):
                    self.assertIsNone(self.check(make(target, REFUTE), runner))
                    self.assertIsNone(self.check(make(target, REVIEW_IN), runner))
                    for prompt in invalid:
                        self.assertIsNotNone(self.check(make(target, prompt), runner), prompt)
        self.assertIsNone(self.check(claude("ricky", REFUTE, sub=True), "claude"))
        self.assertIsNotNone(self.check(claude("ricky", OLD_REFUTE, sub=True), "claude"))
        self.assertIsNotNone(self.check(claude("ricky", REFUTE + "\n전체 대화: 로그", sub=True), "claude"))

    # researcher 안의 하위 호출이 모두 deny되는지 확인한다.
    def test_researcher_cannot_call_any_agent(self):
        for target, prompt in (("ricky", REFUTE), ("reviewer", REVIEW_IN), ("jelly", INPUT_IN), ("pepper", INPUT_IN)):
            with self.subTest(target=target):
                data = {**claude(target, prompt, sub=True), "agent_type": "researcher"}
                self.assertIsNotNone(self.check(data, "claude"))

    # 일반 검증과 구현 요청이 두 줄만 허용하는지 확인한다.
    def test_general_review_and_worker_allow_path_only(self):
        for runner, make in (("claude", claude), ("codex", codex)):
            for target in sub_call.WORKER_REVIEWER:
                for prompt in (GOOD[target] + "\n절: 검증", GOOD[target] + "\n항목: 1", REFUTE if target not in sub_call.REVIEWER else STATE_IN):
                    self.assertIsNotNone(self.check(make(target, prompt), runner))


# 진입 스크립트: 호출 승인과 입력 형식을 한 번에 판정한다.
class Entry(unittest.TestCase):
    # 저장소 루트와 하위 폴더에서 실행해도 같은 판정이 나오는지 확인한다.
    def test_root_and_subdirectory_have_same_decisions(self):
        root = Path(hook.__file__).resolve().parents[1]
        for runner, make in (("claude", claude), ("codex", codex)):
            for cwd in (root, root / "hooks"):
                for target, prompt, denied in (("jelly", INPUT_IN, False), ("ricky", STATE_IN, True),
                                               ("pepper", INPUT_IN, False), ("nico", RESEARCH_IN, False),
                                               ("ricky", REFUTE, False), ("Explore", "자유 입력", False),
                                               ("director", "자유 입력", False), ("rio", "자유 입력", False),
                                               ("assistant", "자유 입력", False), ("buddy", "자유 입력", False)):
                    with self.subTest(runner=runner, cwd=cwd, target=target):
                        data = {**make(target, prompt), "agent_type": "buddy"}
                        output = run_utf8(data, "--runner", runner, cwd=cwd)
                        self.assertEqual(output, "")

    # director 호출에 승인과 입력 형식이 모두 필요한지 확인한다.
    def test_director_call_needs_approval_and_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            for user, prompt, denied in (("승인", INPUT_IN, False), ("승인", "자유 입력", True),
                                         ("다시 생각해", INPUT_IN, True)):
                with self.subTest(user=user, prompt=prompt):
                    path.write_text("\n".join(claude_line(r, t) for r, t in (("assistant", "계획"), ("user", user))) + "\n",
                                    encoding="utf-8")
                    data = {**claude("jelly", prompt), "agent_type": "rio", "transcript_path": str(path), "cwd": tmp}
                    with patch.object(sub_docs, "project_root", return_value=Path(tmp)), \
                            patch.object(hook, "call_reason", return_value=None):
                        self.assertEqual(hook.check(data, "claude") is not None, denied)

    # deny 출력의 필드 구성을 확인한다.
    def test_deny_output_shape(self):
        out = hook.deny("x")["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "deny"))

    # StringIO 입출력에서도 main이 hook 프로토콜을 지키는지 확인한다.
    def test_stringio_main_preserves_protocol(self):
        data = codex("reviewer", REFUTE + "\n전체 대화: 로그")
        output = io.StringIO()
        with patch.object(sys, "argv", ["check_tool_use.py", "--runner", "codex"]), \
             patch.object(sys, "stdin", io.StringIO(json.dumps(data))), patch.object(sys, "stdout", output):
            self.assertEqual(hook.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")

    # 기본 인코딩이 UTF-8이 아닌 환경에서도 한글 사유를 출력하는지 확인한다.
    def test_utf8_stdin_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            transcript = Path(folder) / "session.jsonl"
            transcript.write_text(claude_line("user", "승인") + "\n", encoding="utf-8")
            for prompt, expected in (("이전 대화 요약", "input:"), (INPUT_IN.replace("implement", "document"), "task 값")):
                out = run_utf8({**claude("jelly", prompt), "agent_type": "director", "transcript_path": str(transcript),
                                "cwd": folder}, "--runner", "claude")
                self.assertIn(expected, json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"])


class ApprovalGate(unittest.TestCase):
    """승인 전과 승인 턴의 메인 세션 Bash 판정, 스크립트 파일 쓰기, cd, 보안 파일 문자열, 패치 파일 구간을 검사한다."""
    setUp, tearDown, transcript, write = WriteScope.setUp, WriteScope.tearDown, WriteScope.transcript, WriteScope.write

    # 승인 여부와 도구 이름을 바꿔 director 메인 세션 셸 입력을 검사하는 보조 함수이다.
    def shell(self, cmd, approved=False, tool="Bash", runner="claude", **extra):
        user = "승인" if approved else "다시 생각해"
        tin = {"command": cmd, "description": "테스트 커맨드", **extra.pop("tin", {})}
        return hook.check({"tool_name": tool, "tool_input": tin, "agent_type": "rio", "cwd": str(self.root),
                           "transcript_path": self.transcript(("assistant", "계획"), ("user", user)), **extra}, runner)

    # 승인 전 메인 세션은 git 쓰기, 쓰기 호출이 있는 인라인 코드, 허용 목록 밖 스크립트, 세션 밖 경로를 deny한다.
    def test_approval_turn_before_denies(self):
        for cmd in ("git commit -m x", "git push", "git add a.py", "python -c \"open('a.txt','w').write('x')\"",
                    "python tools/run_workflow.py", "python -m pytest", "cat /etc/hosts"):
            self.assertTrue((self.shell(cmd) or "").endswith((sub_write.RETRY, sub_write.APPROVAL_RETRY, hook.REASON_EDIT_GUIDE)), cmd)

    # 승인 필요 차단 사유 7개가 APPROVAL_RETRY로 끝나고 RETRY를 포함하지 않으며, 일반 차단은 RETRY를 유지한다.
    def test_approval_reasons_use_approval_retry(self):
        for reason in (sub_scope.REASON_NO_APPROVAL, sub_scope.REASON_OUTSIDE_LOG, hook.REASON_SCRIPT,
                       sub_write.REASON_GIT_WRITE, sub_approval.REASON_APPROVAL):
            self.assertIn("nestlab-approval", reason)
            self.assertNotIn("권한 일시 우회", reason)
        self.assertTrue(sub_scope.REASON_NO_APPROVAL.endswith(sub_write.APPROVAL_RETRY))
        self.assertTrue(sub_write.REASON_INLINE.endswith(sub_write.RETRY))

    # 승인 전 메인 세션도 읽기, 쓰기 호출 없는 인라인 코드, heredoc, 셸 변수, 허용 목록 커맨드는 통과한다.
    def test_approval_turn_before_allows(self):
        for cmd in ('python -I -X utf8 -c "print(1)"', "cat <<'EOF'\na\nEOF", 'D=/x; ls "$D"', "git status",
                    "python -I - <<'EOF'\nif 2 > 1:\n    print(1)\nEOF",
                    "python -m unittest discover -s hooks/tests 2>&1 | tail -20"):
            self.assertIsNone(self.shell(cmd), cmd)

    # 승인 전에도 저장소 안 git -C와 셸 변수 앞붙임 커맨드는 통과하고, git -c와 python 스크립트 실행은 deny한다.
    def test_approval_turn_before_decisions(self):
        for cmd in ("git -C hooks log -1", "FOO=1 git status"):
            self.assertIsNone(self.shell(cmd), cmd)
        self.assertEqual(self.shell("git -c a=b status"), sub_write.REASON_GIT_C)
        self.assertTrue(self.shell("python x.py"))

    # 승인 턴에는 git 쓰기와 스크립트 실행이 통과하고, git -c와 저장소 밖 git -C만 deny한다.
    def test_approval_turn_after(self):
        for cmd in ("git commit -m x", "python tools/run_workflow.py", "git -C hooks log -1"):
            self.assertIsNone(self.shell(cmd, approved=True), cmd)
        temp = Path(self.ostemp.name).resolve() / "a.json"
        self.assertIn(hook.REASON_EDIT_GUIDE, self.shell("cp a.txt b.txt", approved=True) or "")
        self.assertTrue(self.shell("cp a.txt b.txt"))  # 승인 전 director의 .agent-logs/ 밖 쓰기는 deny
        # director의 Bash 쓰기는 OS 임시 폴더 대상도 Bash 쓰기 차단 사유를 받는다.
        self.assertIn(hook.REASON_EDIT_GUIDE, self.shell(f"cp ~/.claude/settings.json {temp.as_posix()}", approved=True) or "")
        self.assertEqual(self.shell("git -c core.pager=cat log", approved=True), sub_write.REASON_GIT_C)
        self.assertIn("/outside", self.shell("git -C /outside log -1", approved=True) or "")

    # 메인 세션이 승인 전에 .agent-logs/에 스크립트 확장자 파일을 쓰면 deny하고, OS 임시 폴더 쓰기와 문서 쓰기는 통과한다.
    def test_script_file_write(self):
        no = self.transcript(("assistant", "계획"), ("user", "다시 생각해"))
        temp = Path(self.ostemp.name).resolve() / "tmp_parse.py"
        self.assertEqual(self.write(".agent-logs/tmp_parse.py", "x", role="rio", transcript=no), sub_scope.REASON_SCRIPT_FILE)
        self.assertIsNone(self.write(str(temp), "x", role="rio", transcript=no))
        self.assertNotEqual(self.write(".agent-logs/task/x/status.md", "x", role="rio", transcript=no), sub_scope.REASON_SCRIPT_FILE)
        # 승인 턴에도 .agent-logs/ 아래에는 작업 문서 네 종류와 사건 문서만 쓸 수 있다.
        self.assertIn(".agent-logs/tmp_parse.py", self.write(".agent-logs/tmp_parse.py", "x", role="rio") or "")
        self.assertIn("notes.md", self.write(".agent-logs/task/x/notes.md", "x", role="rio") or "")
        self.assertNotIn(".agent-logs/ 아래에는", self.write(".agent-logs/task/x/status.md", "x", role="rio") or "")
        self.assertNotIn(".agent-logs/ 아래에는", self.write(".agent-logs/incident/20261110-abcdef12.md", "x", role="rio") or "")

    # Claude와 Codex 셸의 cd류 커맨드는 승인 턴에도, heredoc이 섞여도 REASON_CD로 deny한다.
    def test_cd_commands_denied(self):
        reason = sub_write.REASON_CD
        self.assertEqual(self.shell("cd ~/.codex && python -I - <<'EOF'\nprint(1)\nEOF"), reason)
        self.assertEqual(self.shell("cd hooks && ls", approved=True), reason)
        self.assertEqual(self.shell("Set-Location hooks; Get-ChildItem", tool="PowerShell"), reason)
        self.assertEqual(self.shell('bash -lc "cd hooks > out.txt"'), reason)
        self.assertEqual(self.shell('pwsh -c "Set-Location hooks"', tool="PowerShell"), reason)
        self.assertEqual(self.shell("ls", runner="codex", tin={"workdir": str(self.root / "hooks")}), reason)
        for cmd in ("ls hooks", 'grep -n "cd " hooks/common/sub_write.py'):
            self.assertIsNone(self.shell(cmd), cmd)
        self.assertIsNone(self.shell("ls", runner="codex", tin={"workdir": str(self.root)}))

    # 인라인 코드와 heredoc 본문에 보안 파일 패턴이 있으면 승인 턴에도 deny하고 무관한 커맨드는 통과한다.
    def test_secret_text_denied(self):
        patterns = ["//**/.env", "//**/.env.*", "//**/secrets/**", "//**/*.pem", "//**/id_rsa*", "~/.codex/auth.json"]
        with patch.object(sub_path, "secret_patterns", return_value=patterns):
            for cmd in ("python -c \"print(open('.env').read())\"", "python - <<'EOF'\nopen('id_rsa')\nEOF"):
                for approved in (False, True):
                    self.assertIn("보안 문서", self.shell(cmd, approved=approved) or "", cmd)
            for cmd in ('python -c "print(1)"', "grep -n env hooks/check_tool_use.py"):
                self.assertIsNone(self.shell(cmd, approved=True), cmd)

    # apply_patch가 두 문서를 함께 쓰면 각 문서의 추가 줄만 그 문서 규칙으로 검사한다.
    def test_patch_files_checked_separately(self):
        def patched(research_line):
            text = chr(10).join(["*** Begin Patch", f"*** Add File: {self.root / '.agent-logs/task/n/status.md'}", "+근거 출처: 로그 시각",
                                 f"*** Add File: {self.root / '.agent-logs/task/n/research.md'}", f"+{research_line}",
                                 "*** End Patch"])
            data = {"tool_name": "apply_patch", "tool_input": {"command": text}, "agent_type": "rio", "cwd": str(self.root),
                    "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인"))}
            return hook.check(data, "codex") or ""
        reason = patched("출처: 사용자 제공")
        self.assertIn("status.md 1행", reason)
        self.assertIn("log-status", reason)
        self.assertNotIn("출처가 URL", patched("- 제외 범위: 없음"))


class CodexTaskName(unittest.TestCase):
    """Codex spawn_agent의 암호문 message는 task_name으로 요청을 복원해 검사한다."""
    CIPHER = "gAAAAABabcdefghij-_klmn=="
    NAME = "implement__20261024_120000_test"

    # 임시 저장소에 완전한 작업 문서 네 개를 준비한다.
    def setUp(self):
        approval = patch.object(hook, "check_agent_call", return_value=None)
        approval.start()
        self.addCleanup(approval.stop)
        from hooks.tests.test_sub_workflow import PLAN, REFUTE as REFUTE_DOC, RESEARCH as RESEARCH_DOC, STATE, TASK
        structure = patch.object(sub_docs, "structure_reason", return_value=None)
        structure.start()
        self.addCleanup(structure.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / ".agent-logs" / "task" / TASK
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state = STATE
        for kind, body in (("status", STATE), ("spec", PLAN), ("research", RESEARCH_DOC), ("refute", REFUTE_DOC)):
            (self.folder / f"{kind}.md").write_text(body, encoding="utf-8")
        patcher = patch.object(sub_docs, "project_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    # Codex spawn_agent 입력을 만든다.
    def call(self, message=CIPHER, **tin):
        return {"tool_name": "spawn_agent", "agent_type": "director", "cwd": str(self.root),
                "tool_input": {"agent_type": "worker", "message": message, "fork_turns": "none", **tin}}

    # 형식에 맞지 않는 task_name은 사유와 함께 막고, 형식에 맞는 task_name이나 평문 message는 입력 검사를 통과한다.
    def test_task_name_format(self):
        for name in ("implementation", None, "implement__2026_063721_x"):
            with self.subTest(name=name):
                data = self.call(**({} if name is None else {"task_name": name}))
                self.assertEqual(hook.check(data, "codex"), sub_input.REASON_CIPHER)
        self.assertIsNone(sub_input.check_input(self.call(task_name=self.NAME), "codex"))
        self.assertIsNone(sub_input.check_input(self.call(message=INPUT_IN, task_name="implementation"), "codex"))

    # 형식에 맞는 task_name은 단계 선행 조건 검사를 받아, 작업 단계가 구현이면 통과하고 계획이면 차단한다.
    def test_task_name_stage_gate(self):
        self.assertIsNone(hook.check(self.call(task_name=self.NAME), "codex"))
        (self.folder / "status.md").write_text(self.state.replace("- 작업 단계: 구현", "- 작업 단계: 계획"), encoding="utf-8")
        self.assertIn("작업 단계 [계획]", hook.check(self.call(task_name=self.NAME), "codex") or "")


REPORT_DOC = """# 조사 문서: t

- 확인 상태: 확인

## 조사 계획

- 제외 범위: 없음

## 검토 보고

### 검토 3. X

- 전제 대조: 성립 | 근거: 코드 확인. 관측 범위: claude, 메인 (출처: hooks/a.py:1)
- 검토 필요성: 두 안이 모두 요구를 충족해 사용자 선택이 필요하다 (출처: hooks/a.py:2)

| **안** | **기준** |
| :----- | :------- |
| A | 충족 |
| B | 미충족 |

#### 안 1. A

- 적용할 작업: a

#### 안 2. B

- 적용할 작업: b
"""
PROBLEM_DEF = ""


# 확정 문제를 담은 통합 구조 명세 본문이다. items는 (번호, 이름, 사용자 확인 시각 또는 None)이다.
def problem_spec(*items):
    blocks = [f"### {n}. {name}\n\n#### 사용자 확인 시각\n\n{stamp or '<YYYY-MM-DD HH:MM:SS>'}\n\n#### 문제\n\n{name}\n"
              for n, name, stamp in items]
    return "# t 명세\n\n## 요구사항 목록\n\n" + "\n".join(blocks) + "\n## 작업 계획\n"


CONFIRMED_SPEC = problem_spec((1, "범위", "2026-11-12 03:00:00"))
REVIEW_STATE = """# t

- 작업 상태: 진행

### 검토 필요

- 검토 3. X: A를 정한다.
  - 상태: {status}
  - 확정: A
  - 근거 위치: 조사 문서 > 검토 보고 > 검토 3. X > 안 1. A
"""


class QuestionGuards(unittest.TestCase):
    """번호 접두 질문의 형식, 확정 라벨, 검토 보고, 구현 질문 시점을 PreToolUse에서 검사한다."""

    # 임시 저장소와 경과 문서를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        status = self.root / ".agent-logs" / "task" / "20261110-005442-t" / "status.md"
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text("# t\n\n" + PROBLEM_DEF, encoding="utf-8")
        status.with_name("spec.md").write_text(CONFIRMED_SPEC, encoding="utf-8")
        (self.root / ".agent-logs" / "task" / "index.md").write_text(
            "# Task Index\n\n## 현재 진행 작업\n\n- [t](20261110-005442-t/status.md), 계획\n\n## 미완료 작업 목록\n\n## 완료 작업 목록\n",
            encoding="utf-8")
        self.status = status
        self.research = status.with_name("research.md")

    # 경과 문서를 쓴 기록(written=True)이나 쓰지 않은 기록 파일 경로를 runner 형식으로 만든다.
    def transcript(self, runner, written):
        if runner == "claude":
            write = {"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": str(self.status), "content": "x"}}
            lines = [{"type": "user", "message": {"content": "시작"}}]
            if written:
                lines.append({"type": "assistant", "message": {"content": [write]}})
        else:
            patch_text = "*** Begin Patch\n*** Update File: .agent-logs/task/20261110-005442-t/status.md\n@@\n-x\n+y\n*** End Patch"
            item = lambda p: {"type": "response_item", "payload": p}
            lines = [item({"type": "message", "role": "user", "content": [{"text": "시작"}]})]
            if written:
                lines.append(item({"type": "custom_tool_call", "name": "apply_patch", "call_id": "c1", "input": patch_text}))
        path = self.root / f"{runner}-{written}.jsonl"
        path.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n", encoding="utf-8")
        return str(path)

    # 질문 문장과 라벨 목록으로 질문 도구 입력을 만들어 검사한다. 이름은 runner별 질문 도구이다.
    def ask(self, labels, question="검토 1 폐기: 사유", runner="claude", written=True, tool=None, role="director", preview="- 기록이 남는다."):
        tool = tool or ("AskUserQuestion" if runner == "claude" else "request_user_input")
        body = {"id": "q1", "header": "결정", "question": question,
                "options": [{"label": l, "description": "설명", "preview": preview} for l in labels]}
        data = {"tool_name": tool, "tool_input": {"questions": [body]}, "cwd": str(self.root), "agent_type": role,
                "transcript_path": self.transcript(runner, written)}
        return hook.check(data, runner)

    # 형식을 어긴 번호 접두 질문, 물음표 끝 질문과 양식 없는 질문은 막고, 형식을 지킨 질문과 중단 판정 질문은 통과한다.
    def test_prefixed_format(self):
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                for question in ("검토 3. X: 언제 기록할까요?", "조사 1. Codex 입력: 안 2를 채택한다.", "요구사항 1 이름 없는 문장",
                                 "구현 1. 요약", "요구사항 1. 이름: 결과 문장?"):
                    self.assertIsNotNone(self.ask(["A", "B"], question, runner), question)
                self.assertIsNone(self.ask(["안 1. A", "안 2. B"], "검토 3. X: 기록 방식을 정한다. (문제 1)", runner))
                self.assertEqual(self.ask(["확정", "보류"], "요구사항 1. 이름: 결과 문장", runner), sub_question.REASON_KIND.format(kind="요구사항"))
                self.status.write_text(REVIEW_STATE.format(status="검토 완료"), encoding="utf-8")
                self.assertEqual(self.ask(["확정", "보류"], "구현 1. 요약 (요구사항 1)", runner), sub_question.REASON_KIND.format(kind="구현"))
                self.assertEqual(self.ask(["확정", "보류"], "요구사항 1 폐기: 사유", runner), sub_question.REASON_KIND.format(kind="요구사항"))
                self.assertIsNone(self.ask(["확정", "보류"], "검토 1 폐기: 사유", runner))
                # 문제 정의, 검토, 중단 판정 질문이 아닌 질문은 양식 위반이다.
                self.assertEqual(self.ask(["A", "B"], "어느 쪽으로 할까요?", runner), sub_question.REASON_NO_FORM)
                self.assertIsNone(self.ask(["A", "B"], "중단 판정: 처리 방식을 고른다.", runner))
        self.assertEqual(self.ask(["A"], "조사 1. Codex 입력: 안 2를 채택한다."), sub_question.REASON_KIND.format(kind="조사"))

    # 검토 폐기 질문에는 확정 라벨을 요구하고 검토 질문에는 안 번호 라벨을 쓴다.
    def test_confirm_label_required(self):
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.assertEqual(self.ask(["허용 (Recommended)", "현행 유지"], runner=runner), sub_question.REASON_NO_CONFIRM)
                self.assertIsNone(self.ask(["확정 (Recommended)", "보류"], runner=runner))
                # 응답 작성 여부와 관계없이 확정 라벨이 없는 폐기 질문은 막는다.
                self.assertEqual(self.ask(["A", "B"], runner=runner, written=False), sub_question.REASON_NO_CONFIRM)
                self.assertIsNone(self.ask(["안 1. A", "안 2. B"], "검토 3. X: 기록 방식을 정한다. (문제 1)", runner))

    # 라벨이 `수정`인 선택지는 막고, 다른 라벨 조합은 통과한다.
    def test_edit_label_denied(self):
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.assertEqual(self.ask(["확정", "수정"], runner=runner), sub_question.REASON_EDIT_LABEL)
                self.assertIsNone(self.ask(["확정", "보류"], runner=runner))

    # 선택지 설명이 다른 안이나 문서를 가리키거나 확인하지 않은 내용을 담으면 막고, 직접 적은 설명은 통과한다.
    def test_wording_denied(self):
        review = "검토 3. X: 기록 방식을 정한다. (문제 1)"
        problem = "[문제 정의] 문제 1. 범위 (작업 1110-005442)"
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                for question, desc in ((review, "안 2에 더해 B를 한다."), (review, "위와 같이 한다."),
                                       (problem + " status.md 작업 경과", "설명"), (review, "미확인 동작을 쓴다.")):
                    body = {"id": "q1", "header": "결정", "question": question,
                            "options": [{"label": "안 1. A" if question == review else "확정", "description": desc}]}
                    data = {"tool_name": "AskUserQuestion" if runner == "claude" else "request_user_input",
                            "tool_input": {"questions": [body]}, "cwd": str(self.root), "agent_type": "director",
                            "transcript_path": self.transcript(runner, True)}
                    reason = hook.check(data, runner)
                    self.assertTrue(reason and ("직접 작성" in reason or "직접 확인" in reason), (question, desc, reason))
                body = {"id": "q1", "header": "결정", "question": review,
                        "options": [{"label": "안 1. A", "description": "hooks/a.py의 검사를 b.py로 옮긴다.", "preview": "- 검사가 b.py에서 실행된다."}]}
                data = {"tool_name": "AskUserQuestion" if runner == "claude" else "request_user_input",
                        "tool_input": {"questions": [body]}, "cwd": str(self.root), "agent_type": "director",
                        "transcript_path": self.transcript(runner, True)}
                self.assertIsNone(hook.check(data, runner))

    # 경과 문서를 쓴 Codex 세션의 request_user_input_async는 막고, 쓰지 않은 세션은 통과한다.
    def test_async_question_denied(self):
        self.assertEqual(self.ask(["확정"], "어느 안으로 할까요?", "codex", tool="request_user_input_async"), hook.REASON_ASYNC)
        self.assertIsNone(self.ask(["확정"], "어느 안으로 할까요?", "codex", written=False, tool="request_user_input_async", role="assistant"))

    # 단일 안 검토는 `폐기`, `재조사` 라벨을 허용하고, 안 번호 형식이 아닌 다른 라벨은 차단한다.
    def test_single_row_review_extra_labels(self):
        question = "검토 3. X: 기록 방식을 정한다. (문제 1)"
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.assertIsNone(self.ask(["안 1. A", "폐기", "재조사"], question, runner))
                self.assertIsNotNone(self.ask(["안 1. A", "수정 필요"], question, runner))

    # 문제 정의 질문은 긍정어와 완전히 같은 라벨이 있어야 통과한다.
    def test_problem_definition_label_required(self):
        for label, blocked in (("확정", False), ("확정 (Recommended)", False), ("확정하고 진행", True), ("수정", True)):
            question = {"question": "[문제 정의] 문제 5. 범위를 확인한다. (작업 1110-005442)", "options": [{"label": label}]}
            self.assertEqual(sub_question.check_one(question, {"cwd": str(self.root)}, "claude") is not None, blocked, label)

    # 문제 정의 질문은 작업 폴더가 있는 작업 태그가 문장 끝에 있어야 통과한다. 태그가 없거나 폴더가 없는 작업이면 막는다.
    def test_problem_definition_tag_at_end(self):
        head = sub_question.REASON_PROBLEM_TAG.split("{")[0]
        for text, blocked in (("문제 정의 확인: 문제 5. 범위를 확인한다. (작업 1110-005442)", False),
                              ("문제 정의 확인: 문제 5. 범위 (작업 1110-005442) 세부 내용", True),
                              ("문제 정의 확인: 문제 5. 범위를 확인한다. (작업 1108-152434)", True),
                              ("문제 정의 확인: 문제 5. 범위를 확인한다.", True)):
            question = {"question": text, "options": [{"label": "확정"}]}
            reason = sub_question.check_one(question, {"cwd": str(self.root)}, "claude") or ""
            self.assertEqual(reason.startswith(head), blocked, text)

    # 문제 정의 질문은 question-plan 양식의 문제 번호 머리말(확정, 폐기, 추가 합의, 교체)이어야 통과하고 위반 사유는 양식 이름을 담는다.
    def test_problem_definition_number_format(self):
        for text, blocked in (("[문제 정의] 문제 5. 범위를 확인한다.", False),
                              ("[문제 정의] 문제 1. 폐기: 문제 5와 중복", False),
                              ("[문제 정의] 문제 1. 추가 합의: 경계값 포함", False),
                              ("[문제 정의] 문제 1. 교체: 새 원문", False),
                              ("문제 정의 확인: 문제 1 폐기: 문제 5와 중복", False),
                              ("[문제 정의] 범위를 확인한다.", True),
                              ("문제 정의 확인: 범위를 확인한다.", True),
                              ("문제 정의 확인: 문제 범위를 확인한다.", True)):
            question = {"question": text + " (작업 1110-005442)", "options": [{"label": "확정"}, {"label": "보류"}]}
            reason = sub_question.check_one(question, {"cwd": str(self.root)}, "claude")
            self.assertEqual(reason == sub_question.REASON_PROBLEM_FORMAT, blocked, (text, reason))
            self.assertTrue(reason is None or "question-plan" in reason, text)

    # 검토 질문 선택지 미리보기는 기대 결과 목록만 허용한다. 없거나 장단점 줄이 있거나 목록이 아니면 막는다.
    def test_review_preview_expected_results_only(self):
        question = "검토 3. X: 기록 방식을 정한다. (문제 1)"
        labels = ["안 1. A", "안 2. B"]
        self.assertIsNone(self.ask(labels, question, preview="- 기록이 남는다.\n- 재실행해도 같다."))
        self.assertEqual(self.ask(labels, question, preview=""), sub_question.REASON_NO_PREVIEW.format(label="안 1. A"))
        self.assertIn("`안 1. A`", self.ask(labels, question, preview=None))
        for line in ("- 장점: 빠르다.", "- 단점: 느리다.", "- 한계: 없다.", "- 잠재 위험: 있다.", "- 근거: 문서", "- 비용: 낮다."):
            reason = self.ask(labels, question, preview="- 기록이 남는다.\n" + line)
            self.assertEqual(reason, sub_question.REASON_PREVIEW_FIELD.format(label="안 1. A", line=line), line)
        self.assertEqual(self.ask(labels, question, preview="기록이 남는다."),
                         sub_question.REASON_PREVIEW_FORMAT.format(label="안 1. A", line="기록이 남는다."))
        # 참조 표현과 확인하지 않은 내용은 미리보기에도 적용하고, 폐기와 재조사 선택지는 미리보기 없이 통과한다.
        self.assertIn("직접 작성", self.ask(labels, question, preview="- 안 2에 더해 기록한다.") or "")
        self.assertIn("직접 확인", self.ask(labels, question, preview="- 미확인 동작을 쓴다.") or "")
        extras = {"options": [{"label": "안 1. A", "preview": "- 기록이 남는다."}, {"label": "폐기"}, {"label": "재조사", "preview": ""}]}
        self.assertIsNone(sub_question.preview_gap(extras))

    # 줄 연속이 붙은 표준 입력 python은 인라인 코드로 판정한다.
    def test_stdin_python_with_continuation_is_inline(self):
        import sub_write
        self.assertEqual(sub_write.interpreter_kind("python -I - \\"), "inline")
        self.assertEqual(sub_write.interpreter_kind("python - <<'EOF'"), "inline")
        self.assertEqual(sub_write.interpreter_kind("python tool.py -"), "script")

    # 검토 질문은 문서의 비교표와 안 블록을 읽지 않고 라벨 형식과 번호 순서만 판정한다.
    def test_review_label_format_only(self):
        question = "검토 3. X: 기록 방식을 정한다. (문제 1)"
        # 검토 보고 절이 없는 작업에서 오름차순 안 라벨은 통과하고, 라벨이 없는 질문과 순서가 어긋난 라벨은 차단한다.
        for runner in ("claude", "codex"):
            self.assertIsNone(self.ask(["안 1. A", "안 2. B (Recommended)"], question, runner), runner)
        self.assertEqual(self.ask([], question), sub_question.REASON_NO_LABELS)
        self.assertEqual(self.ask(["안 2. B", "안 1. A"], question), sub_question.REASON_LABEL_ORDER)
        reason = self.ask(["A", "안 2. B"], question)
        self.assertIn("`A`", reason)
        self.assertIn("question-choice", reason)

        # 폐기된 번호는 같은 번호로 다시 묻지 못한다.
        self.status.write_text("# t\n\n" + PROBLEM_DEF + "### 검토 필요\n\n- 검토 3. X: 폐기 (사용자 검토 시각: 2026-11-12 03:00:00)\n", encoding="utf-8")
        self.assertIn("검토 4.", self.ask(["안 1. A", "안 2. B"], question))
        self.assertIsNone(self.ask(["안 1. A", "안 2. B"], "검토 4. Y: 기록 방식을 정한다. (문제 1)"))

    # 검토 질문은 문제 번호 괄호가 있고, 번호가 확인 시각이 있으며 폐기되지 않은 문제여야 통과한다.
    def test_review_requires_confirmed_problem(self):
        tag = " (작업 1110-005442)"
        self.status.with_name("spec.md").write_text(problem_spec((1, "범위", "2026-11-12 03:00:00"), (2, "폐기", "2026-11-12 03:00:00"),
                                                                 (3, "시각 없음", None)), encoding="utf-8")
        labels = ["안 1. A", "안 2. B"]
        base = "검토 3. X: 기록 방식을 정한다."
        self.assertIsNone(self.ask(labels, f"{base} (문제 1){tag}"))
        self.assertIn("문제 2", self.ask(labels, f"{base} (문제 2){tag}"))
        self.assertIn("`(문제 3)`", self.ask(labels, f"{base} (문제 3){tag}"))
        self.assertIn("question-choice", self.ask(labels, f"{base} (문제 3){tag}"))
        self.assertIn("`(문제 99)`", self.ask(labels, f"{base} (문제 99){tag}"))
        self.assertEqual(self.ask(labels, f"{base}{tag}"), sub_question.REASON_FORMAT)

    # 계획 단계에서 조사 계획 항목과 연결된 검토 질문은 반증 최종 회차가 전부 지지이거나 반증이 2회에 도달한 뒤에만 허용하고, 연결되지 않은 검토 질문은 허용한다.
    def test_review_waits_for_refutation(self):
        self.status.write_text("# t\n\n- 작업 단계: 계획\n\n" + PROBLEM_DEF, encoding="utf-8")
        refute = self.status.with_name("refute.md")
        plan = lambda number: ("# 조사 문서: t\n\n## 조사 계획\n\n- 제외 범위: 없음\n\n"
                               f"### 조사 1. 도구 선택 (문제 {number})\n\n- 질문: 도구\n\n## 조사 결과\n")
        self.research.write_text(plan(1), encoding="utf-8")
        question = "검토 3. X: 기록 방식을 정한다. (문제 1)"
        labels = ["안 1. A", "안 2. B"]
        row = lambda n, count: f"| {n} | {count} | 계속 |\n"
        block = lambda n, verdicts: f"### {n}회차 반증 원문\n\n```\n{verdicts}\n```\n"
        one = "주장 1: 지지 | 출처: https://a.example/x\n주장 2: 반증 | 출처: https://b.example/y"
        refute.write_text("## 조사 및 반증 루프\n\n| 회차 | 반증 건수 | 정지 여부 |\n| --- | --- | --- |\n" + row(1, 1)
                          + "\n## 회차 기록\n\n" + block(1, one), encoding="utf-8")
        self.assertEqual(self.ask(labels, question), sub_question.REASON_REFUTE_PENDING)
        # 조사 계획 항목과 연결되지 않은 문제 번호의 검토 질문과 조사 문서가 없는 작업의 검토 질문은 반증이 남아도 통과한다.
        self.research.write_text(plan(2), encoding="utf-8")
        self.assertIsNone(self.ask(labels, question))
        self.research.unlink()
        self.assertIsNone(self.ask(labels, question))
        self.research.write_text(plan(1), encoding="utf-8")
        done = "주장 1: 지지 | 출처: https://a.example/x\n주장 2: 지지 | 출처: https://b.example/y"
        refute.write_text("## 조사 및 반증 루프\n\n| 회차 | 반증 건수 | 정지 여부 |\n| --- | --- | --- |\n" + row(1, 1) + row(2, 0)
                          + "\n## 회차 기록\n\n" + block(1, one) + block(2, done), encoding="utf-8")
        self.assertIsNone(self.ask(labels, question))
        refute.write_text("## 조사 및 반증 루프\n\n| 회차 | 반증 건수 | 정지 여부 |\n| --- | --- | --- |\n" + row(1, 1)
                          + "\n## 회차 기록\n\n" + block(1, done), encoding="utf-8")
        self.assertIsNone(self.ask(labels, question))
        refute.write_text("## 조사 및 반증 루프\n\n| 회차 | 반증 건수 | 정지 여부 |\n| --- | --- | --- |\n" + row(1, 1) + row(2, 1)
                          + "\n## 회차 기록\n\n" + block(1, one) + block(2, one), encoding="utf-8")
        self.assertIsNone(self.ask(labels, question))

    # 문제 정의 확인 질문은 확정, 보류, 폐기 라벨만 허용하고, 이미 있는 번호를 확정 형식으로 다시 묻지 못한다.
    def test_problem_question_labels_and_repeat(self):
        folder = self.status.parent.parent / "20261110-005442-t"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "status.md").write_text("# t\n\n" + PROBLEM_DEF, encoding="utf-8")
        (folder / "spec.md").write_text(CONFIRMED_SPEC, encoding="utf-8")
        tag = " (작업 1110-005442)"
        ask = lambda labels, text: sub_question.check_one(
            {"question": text + tag, "options": [{"label": l} for l in labels]}, {"cwd": str(self.root)}, "claude")
        self.assertIsNone(ask(["확정", "보류", "폐기"], "[문제 정의] 문제 12. 새 범위"))
        reason = ask(["확정", "수정 필요"], "[문제 정의] 문제 12. 새 범위")
        self.assertIn("`수정 필요`", reason)
        self.assertIn("question-plan", reason)
        self.assertEqual(ask(["확정", "보류"], "[문제 정의] 문제 1. 새 범위"), sub_question.REASON_PROBLEM_REPEAT.format(number=1))
        self.assertIsNone(ask(["확정", "보류"], "[문제 정의] 문제 1. 추가 합의: 경계값 포함"))
        self.assertIsNone(ask(["확정", "보류"], "[문제 정의] 문제 1. 교체: 새 원문"))
        self.assertEqual(ask(["확정", "보류"], "[문제 정의] 문제 7. 추가 합의: 내용"), sub_question.REASON_PROBLEM_MISSING.format(number=7))

    # 대기 중인 queue-operation 사용자 메시지가 있으면 Claude 메인에 전달 문맥을 만들고, 전달된 메시지, 하네스의 계속 요청과 Codex는 만들지 않는다.
    def test_waiting_user_message(self):
        queue = {"type": "queue-operation", "operation": "enqueue", "content": "다시 확인해 주세요"}
        delivered = {"type": "user", "message": {"content": "다시 확인해 주세요"}}
        system = {"type": "queue-operation", "operation": "enqueue", "content": "Continue from where you left off."}
        dequeue = {"type": "queue-operation", "operation": "dequeue"}
        limit = {"type": "queue-operation", "operation": "enqueue", "content": "I hit my usage limit while you were working, "
                 "but it has reset now. Please continue from where you left off."}
        for lines, runner, blocked in (([queue], "claude", True), ([queue, delivered], "claude", False), ([queue], "codex", False),
                                       ([system], "claude", False), ([limit], "claude", False), ([system, queue], "claude", True),
                                       ([queue, system, dequeue], "claude", False), ([system, dequeue, queue], "claude", True)):
            path = self.root / f"queue-{runner}-{len(lines)}-{int(blocked)}-{lines[0]['content'][:3]}.jsonl"
            path.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n", encoding="utf-8")
            data = {"tool_name": "AskUserQuestion" if runner == "claude" else "request_user_input", "cwd": str(self.root),
                    "agent_type": "director", "transcript_path": str(path),
                    "tool_input": {"questions": [{"question": "어느 쪽으로 할까요?", "options": [{"label": "A"}, {"label": "B"}]}]}}
            context = hook.waiting_context(data, runner)
            self.assertEqual(bool(context and "다시 확인해 주세요" in context), blocked, (runner, len(lines)))

    # 문제 정의 확인 질문 전 응답 본문에 한계와 위험 두 줄이 있어야 하고, 내용이 있는 줄은 URL 출처여야 한다.
    def test_problem_question_needs_limit_and_risk(self):
        question = {"question": "[문제 정의] 문제 5. 범위 (작업 1110-005442)", "options": [{"label": "확정"}, {"label": "보류"}]}
        cases = (("본문", True), ("- 한계: 없음\n- 위험: 파일 때문 (출처: hooks/a.py:1)", True),
                 ("- 한계: 없음\n- 위험: 외부 제약 (출처: https://example.com/a)", False))
        for text, blocked in cases:
            path = self.root / f"limit-{blocked}-{len(text)}.jsonl"
            lines = [{"type": "user", "message": {"content": "시작"}},
                     {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}]
            path.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n", encoding="utf-8")
            data = {"tool_name": "AskUserQuestion", "cwd": str(self.root), "agent_type": "director", "transcript_path": str(path),
                    "tool_input": {"questions": [question]}}
            reason = hook.check(data, "claude")
            self.assertEqual(reason is not None, blocked, (text, reason))
            self.assertTrue(reason is None or ("한계" in reason or "위험" in reason) and "question-plan" in reason, reason)

    # 구현 질문은 조사 확인 및 검토 상태와 관계없이 차단한다.
    def test_implementation_question_timing(self):
        question = "구현 1. 요약 (요구사항 1)"
        self.research.write_text(REPORT_DOC.replace("확인 상태: 확인", "확인 상태: <확인 | 보류 | 기각>"), encoding="utf-8")
        self.status.write_text(REVIEW_STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertEqual(sub_question.REASON_KIND.format(kind="구현"), self.ask(["확정"], question))

        # 조사 문서 확인이 끝나도 미완료 검토나 답 없는 검토 보고가 있으면 차단한다.
        self.research.write_text(REPORT_DOC, encoding="utf-8")
        self.status.write_text(REVIEW_STATE.format(status="사용자 검토 필요"), encoding="utf-8")
        self.assertEqual(sub_question.REASON_KIND.format(kind="구현"), self.ask(["확정"], question))
        self.status.write_text("# t\n\n- 작업 상태: 진행\n", encoding="utf-8")
        self.assertEqual(sub_question.REASON_KIND.format(kind="구현"), self.ask(["확정"], question))

        # 검토가 끝났거나 조사 항목이 없어도 구현 질문은 차단한다.
        self.status.write_text(REVIEW_STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertEqual(sub_question.REASON_KIND.format(kind="구현"), self.ask(["확정"], question))
        plain = REPORT_DOC.replace("확인 상태: 확인", "확인 상태: <확인 | 보류 | 기각>").replace("- 제외 범위: 없음", "- 조사 항목 없음")
        self.research.write_text(plain, encoding="utf-8")
        self.assertEqual(sub_question.REASON_KIND.format(kind="구현"), self.ask(["확정"], question))


class SpecTiming(unittest.TestCase):
    """구형 명세의 검토 선행 검사와 새 명세 초안의 쓰기를 확인한다."""

    # 임시 저장소와 patch를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.folder = self.root / ".agent-logs" / "task" / "t"
        self.folder.mkdir(parents=True, exist_ok=True)
        templates = self.root / "templates"
        templates.mkdir()
        (templates / "log-spec.md").write_text("", encoding="utf-8")
        patches = [patch.object(sub_scope, "project_root", return_value=self.root),
                   patch.object(sub_docs, "structure_reason", return_value=None),
                   patch.object(sub_docs, "project_root", return_value=self.root),
                   patch.object(sub_docs, "TEMPLATES", templates),
                   patch.object(sub_scope, "skill_called", return_value=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        log = self.root / "t.jsonl"
        log.write_text(claude_line("assistant", "보고") + "\n" + claude_line("user", "승인") + "\n", encoding="utf-8")
        self.log = str(log)
        (templates / "log-spec.md").write_text("<!-- 모든 목록이 검토 완료 또는 폐기일 때 승인 가능 -->\n", encoding="utf-8")

    # 명세 문서 Write 입력으로 검사를 실행한다.
    def write_spec(self, content="# t 입력\n\n## 요구사항 목록\n\n- 요구\n"):
        data = {"tool_name": "Write", "tool_input": {"file_path": str(self.folder / "spec.md"), "content": content},
                "agent_type": "rio", "cwd": str(self.root), "transcript_path": self.log}
        return hook.check(data, "claude")

    # 구형 명세는 검토 상태로 판정하며 조사 확인 상태와 무관하다.
    def test_spec_write_waits_for_review(self):
        (self.folder / "status.md").write_text(REVIEW_STATE.format(status="검토 완료"), encoding="utf-8")
        (self.folder / "research.md").write_text(REPORT_DOC.replace("확인 상태: 확인", "확인 상태: <확인 | 보류 | 기각>"), encoding="utf-8")
        self.assertIsNone(self.write_spec())
        (self.folder / "research.md").write_text(REPORT_DOC, encoding="utf-8")
        (self.folder / "status.md").write_text(REVIEW_STATE.format(status="사용자 검토 필요"), encoding="utf-8")
        self.assertIn("검토 3. X", self.write_spec() or "")
        # 검토 미완료여도 요구사항 목록과 작업 계획을 바꾸지 않는 검토 보고 쓰기는 허용한다.
        self.assertIsNone(self.write_spec("# t 입력\n\n## 검토\n\n### 검토 보고\n\n#### 검토 3. X\n"))
        (self.folder / "status.md").write_text(REVIEW_STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertIsNone(self.write_spec())


class StageGate(WriteScope):
    """사용자 메시지 첫 줄의 단계 머리줄을 경과 문서 작업 단계에 기록하고 단계별 쓰기와 배정을 판정한다."""
    TASK = "20261110-005442-t"
    STATUS = ("# t\n\n- 작업 ID: 20261110-005442-t\n- 생성 시각: 2026.11.10 00:54:42\n- 완료 시각: 없음\n- 작업 단계: 계획\n\n"
              "## 원인 분석\n\n- 조건 누락: 확인된 원인\n  - 근거 내용: 입력으로 재현했다.\n  - 출처: hooks/a.py:1\n  - 한계: 없음\n")
    INDEX = "# Task Index\n\n## 현재 진행 작업\n\n- [t](20261110-005442-t/status.md), 계획\n\n## 미완료 작업 목록\n\n## 완료 작업 목록\n"

    # 작업 문서 경로이다.
    def doc(self, name):
        return f".agent-logs/task/{self.TASK}/{name}.md"

    # 경과 문서와 작업 색인을 만들고 사용자 메시지로 대화 기록을 만들어 (경로, 경과 문서)를 반환한다.
    def stage_log(self, *messages, stage=None):
        import check_workflow_response
        p = patch.object(check_workflow_response, "project_root", return_value=self.root)
        p.start()
        self.addCleanup(p.stop)
        status = self.root / self.doc("status")
        status.parent.mkdir(parents=True, exist_ok=True)
        if not status.exists():
            status.write_text(self.STATUS.replace("작업 단계: 계획", f"작업 단계: {stage}") if stage else self.STATUS, encoding="utf-8")
            (self.root / ".agent-logs" / "task" / "index.md").write_text(self.INDEX, encoding="utf-8")
        edit = {"type": "tool_use", "id": "w0", "name": "Edit", "input": {"file_path": str(status), "old_string": "a", "new_string": "b"}}
        rows = [json.dumps({"type": "assistant", "message": {"content": [edit]}})] + [claude_line("user", m) for m in messages]
        path = self.root / "s.jsonl"
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return str(path), status

    # [implement] 머리줄은 경과 문서에 기록되고 머리줄 없는 메시지에서도 유지되며, 다른 머리줄이 오면 .agent-logs/ 밖 쓰기를 차단한다.
    def test_stage_recorded_and_kept(self):
        log, status = self.stage_log("[implement]\n구현해")
        self.assertIsNone(self.write("hooks/a.py", "x", transcript=log))
        self.assertIn("- 작업 단계: 구현", status.read_text(encoding="utf-8"))
        log, _ = self.stage_log("[implement]\n구현해", "다른 거 해")
        self.assertIsNone(self.write("hooks/a.py", "x", transcript=log))
        log, _ = self.stage_log("[implement]", "[plan]\n다시 봐")
        self.assertIsNotNone(self.write("hooks/a.py", "x", transcript=log))
        # 한국어 키워드, 잘못된 작업 ID, 인용 줄은 단계를 바꾸지 않는다.
        for bad in ("[구현]", "[implement 잘못된id]", "> [implement]"):
            log, _ = self.stage_log(bad)
            self.assertIsNotNone(self.write("hooks/a.py", "x", transcript=log))
        self.assertIn("- 작업 단계: 계획", status.read_text(encoding="utf-8"))

    # 이전 단계 이름(착수, 조사, 진행)은 현재 단계 이름으로 읽는다.
    def test_legacy_stage_names_read_as_current(self):
        for old, current in (("착수", "계획"), ("조사", "계획"), ("진행", "구현"), ("계획", "계획"), ("없는 단계", None)):
            self.assertEqual(sub_approval.stage_of(f"- 작업 단계: {old}\n"), current, old)

    # 구현 단계 전환은 경과 문서 양식의 출처 줄(`출처:`)로 검사하며 양식 인자를 넘겨 판정한다.
    def test_implement_prefix_checks_state_form(self):
        log, status = self.stage_log("[implement]")
        broken = self.STATUS.replace("  - 한계: 없음", "  - 한계: 외부 제약이 있다 (출처: 사용자 제공)")
        status.write_text(broken, encoding="utf-8")
        self.assertEqual(sub_approval.apply_stage(status, "구현"), "계획")
        status.write_text(self.STATUS, encoding="utf-8")
        self.assertEqual(sub_approval.apply_stage(status, "구현"), "구현")

    # 작업 단계 줄은 director가 바꿀 수 없고, 계획 단계에서는 조사, 반증, 명세 문서를 쓸 수 있으며 완료 단계에서는 명세 문서를 쓸 수 없다.
    def test_stage_line_locked_and_spec_blocked(self):
        log, status = self.stage_log("[plan]")
        self.assertIsNone(self.write(self.doc("research"), "# r" + "\n", transcript=log))
        reason = self.write(self.doc("status"), self.STATUS.replace("작업 단계: 계획", "작업 단계: 구현"), transcript=log) or ""
        self.assertIn("hook만", reason)
        self.assertEqual(reason.count("[plan]"), 1)
        self.assertNotIn("작업 단계", self.write(self.doc("spec"), "# s" + "\n", transcript=log) or "")
        status.write_text(self.STATUS.replace("작업 단계: 계획", "작업 단계: 완료"), encoding="utf-8")
        log, _ = self.stage_log("완료 뒤 메시지")
        self.assertIn("spec.md를 쓸 수 없습니다", self.write(self.doc("spec"), "# s" + "\n", transcript=log) or "")

    # 배정은 단계별 작업 종류만 허용한다.
    def test_stage_limits_agent_call(self):
        log, _ = self.stage_log("[plan]")
        call = lambda kind: sub_approval.check_agent_call({
            "tool_name": "Agent", "tool_input": {"prompt": f"{kind}\ninput: {self.doc('research')}", "subagent_type": "nico"},
            "agent_type": "rio", "cwd": str(self.root), "transcript_path": log})
        self.assertIsNone(call("task: research"))
        self.assertIn("[계획]", call("task: implement") or "")
        log, _ = self.stage_log("[implement]")
        self.assertIsNone(call("task: implement"))
        # 완료 선행 검사에 실패하면 구현 단계에 남아 보완 작업을 배정할 수 있다.
        log, _ = self.stage_log("[complete]")
        self.assertIsNone(call("task: implement"))
        self.assertIn("[완료]", sub_approval.stage_call_reason("완료", "task: implement") or "")

    # 단계별 쓰기 대상: 계획은 research, refute, spec을 허용하고 구현은 spec만 허용하며 research를 막는다.
    def test_stage_write_targets(self):
        log, status = self.stage_log("[plan]")
        self.assertIsNone(self.write(self.doc("refute"), "# r" + "\n", transcript=log))
        self.assertIsNone(self.write(self.doc("spec"), "# s" + "\n", transcript=log))
        log, _ = self.stage_log("[implement]")
        self.assertEqual(self.write(self.doc("spec"), "# s" + "\n", transcript=log), sub_scope.REASON_PLAN_LOCKED)
        self.assertIn("research.md를 쓸 수 없습니다", self.write(self.doc("research"), "# r" + "\n", transcript=log) or "")

    # 첫 줄 시작의 머리줄에서 단계를 읽으며 머리줄 뒤의 본문과 줄바꿈 표시는 허용한다.
    def test_stage_prefix_head_line_only(self):
        task = "20261110-005442-director-misstep-hooks"
        cases = {"[plan]": "계획", "[plan]\n본문": "계획", f"[implement {task}]\n본문": "구현", "[complete]": "완료",
                 "[plan] 그리고 이거 해": "계획", "[구현]": "보류", "[implement 잘못된id]": "보류", "[plan][complete]": "계획",
                 "[implement]\\\n본문": "구현", f"[implement {task}] 본문": "구현",
                 "설명\n[implement]": None, "> [complete]": None, "": None}
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(sub_approval.stage_prefix(message), expected)
        self.assertEqual(sub_approval.head_line(f"[implement {task}]"), ("구현", task))

    # 작업 도중 보낸 메시지(queued_command attachment)는 턴 시작 메시지가 아니므로 단계와 쓰기 허용을 바꾸지 않는다.
    def test_queued_command_message_keeps_stage(self):
        log, status = self.stage_log("[implement]\n구현해")
        self.assertIsNone(self.write("hooks/a.py", "x", transcript=log))
        queued = lambda text, mode="prompt": json.dumps(
            {"type": "attachment", "attachment": {"type": "queued_command", "prompt": text, "commandMode": mode}})
        for text in ("[plan]\n다시 봐", "머리줄 없는 메시지"):
            with open(log, "a", encoding="utf-8") as f:
                f.write(queued(text) + "\n")
            self.assertIsNone(self.write("hooks/a.py", "x", transcript=log), text)
            self.assertIn("- 작업 단계: 구현", status.read_text(encoding="utf-8"))

    # queued_command 중 commandMode가 prompt인 메시지만 사용자 메시지로 읽고, 턴 시작 메시지는 바꾸지 않는다.
    def test_queued_command_excludes_harness_items(self):
        import sub_session
        queued = lambda text, mode: json.dumps(
            {"type": "attachment", "attachment": {"type": "queued_command", "prompt": text, "commandMode": mode}})
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "c.jsonl"
            log.write_text("\n".join([
                claude_line("user", "처음"), queued("[plan]\n사용자 메시지", "prompt"),
                queued("<task-notification>끝</task-notification>", "task-notification"),
                queued("<agent-message from=\"x\">보고</agent-message>", "prompt"),
                queued("<task-notification>끝</task-notification>", "prompt")]) + "\n", encoding="utf-8")
            entries = sub_session.read_session(str(log))
            self.assertEqual(sub_session.turn(entries)[0], "처음")
            self.assertEqual(sub_session.user_messages(entries), ["처음", "[plan]\n사용자 메시지"])

    # 맥락 압축 요약 항목(isCompactSummary)은 사용자 메시지로 읽지 않는다.
    def test_compact_summary_is_not_user_message(self):
        import sub_session
        compact = json.dumps({"type": "user", "isCompactSummary": True, "message": {"content": "요약 본문"}}, ensure_ascii=False)
        log = self.root / "compact.jsonl"
        log.write_text("\n".join([claude_line("user", "처음"), compact]) + "\n", encoding="utf-8")
        entries = sub_session.read_session(str(log))
        self.assertEqual(sub_session.user_messages(entries), ["처음"])
        self.assertEqual(sub_session.turn(entries)[0], "처음")

    # [재진행] 접두어는 잘못된 접두어이므로 보류하고 저장 단계를 복원하지 않는다.
    def test_legacy_resume_prefix_is_invalid(self):
        self.assertEqual(sub_approval.stage_prefix("[재진행] 20261110-015333-test"), "보류")


class NoSkillGate(unittest.TestCase):
    """director 메인 세션은 스킬 호출 없이도 Skill 외 도구를 호출할 수 있다."""

    # 스킬 호출이 없는 director 세션의 Bash `ls`는 통과한다.
    def test_bash_without_skill_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "c.jsonl"
            log.write_text(claude_line("user", "시작") + chr(10), encoding="utf-8")
            data = {"tool_name": "Bash", "tool_input": {"command": "ls", "description": "테스트 커맨드"},
                    "agent_type": "rio", "cwd": tmp, "transcript_path": str(log)}
            self.assertIsNone(hook.check(data, "claude"))


class ApplyPatchFullText(unittest.TestCase):
    """apply_patch가 Write와 같은 쓰기 후 파일 전체 내용으로 전이, 필수 절 검사를 받는다."""
    setUp, tearDown, transcript, write = WriteScope.setUp, WriteScope.tearDown, WriteScope.transcript, WriteScope.write

    # 파일 한 개를 갱신하는 apply_patch 입력으로 검사를 실행한다.
    def patch_of(self, path, body):
        text = f"*** Begin Patch\n*** Update File: {path}\n{body}\n*** End Patch"
        return hook.check({"tool_name": "apply_patch", "tool_input": {"command": text}, "agent_type": "rio",
                           "cwd": str(self.root), "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인"))}, "codex")

    # added_lines가 apply_patch 입력에서 쓰기 후 파일 전체 내용을 반환하고, 패치 구간을 찾지 못하면 None을 반환한다.
    def test_added_lines_full_text(self):
        doc = self.root / ".agent-logs" / "task" / "t" / "status.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("a\nb\n", encoding="utf-8")
        data = lambda body: {"tool_name": "apply_patch", "cwd": str(self.root), "tool_input": {
            "command": f"*** Begin Patch\n*** Update File: {doc}\n{body}\n*** End Patch"}}
        self.assertEqual(sub_write.added_lines(data("@@\n a\n+x"), doc), (["x"], "a\nx\nb\n"))
        self.assertEqual(sub_write.added_lines(data("@@\n-없음\n+x"), doc), (["x"], None))

    # 실행 승인 줄을 더하는 패치는 전이 검사를 받아 deny하고, 작업 경과 절만 고치는 패치는 통과한다.
    def test_execution_approval_transition(self):
        status = self.root / ".agent-logs" / "task" / "t" / "status.md"
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text("# t\n\n### 실행 승인 범위\n\n- 사용자 승인 상태: 승인 대기\n\n### 작업 경과\n\n- 갱신 시각: x\n", encoding="utf-8")
        gate = lambda path, new, old, plan: "전이 미달" if "승인 상태: 승인\n" in (new or "") else None
        with patch.object(sub_scope, "transition_reason", side_effect=gate):
            self.assertEqual(self.patch_of(status, "@@\n-- 사용자 승인 상태: 승인 대기\n+- 사용자 승인 상태: 승인"), "전이 미달")
            self.assertIsNone(self.patch_of(status, "@@\n-- 갱신 시각: x\n+- 갱신 시각: y"))

    # 조사 문서 필수 절을 지우는 패치는 deny한다.
    def test_required_section_removed(self):
        (self.templates / "log-research.md").write_text("# t\n\n## 조사 계획\n\n## 후보 비교\n", encoding="utf-8")
        research = self.root / ".agent-logs" / "task" / "t" / "research.md"
        research.parent.mkdir(parents=True, exist_ok=True)
        research.write_text("# t\n\n## 조사 계획\n\n## 후보 비교\n", encoding="utf-8")
        reason = self.patch_of(research, "@@\n ## 조사 계획\n \n-## 후보 비교")
        self.assertIn("`후보 비교`", reason)
        self.assertIn("log-research", reason)


class ApprovedPlanLock(unittest.TestCase):
    """실행 승인 뒤에는 명세 문서 계획과 조사 문서 검토 보고를 고칠 수 없다."""
    setUp, tearDown, transcript, write = WriteScope.setUp, WriteScope.tearDown, WriteScope.transcript, WriteScope.write

    # 실행 승인 상태를 정해 작업 폴더를 만들고 명세 문서와 조사 문서를 반환한다.
    def task(self, state="승인"):
        folder = self.root / ".agent-logs" / "task" / "t"
        folder.mkdir(parents=True, exist_ok=True)
        patcher = patch.object(sub_scope, "early_items", return_value=[])  # 검토 완료 판정은 이 테스트가 다루지 않는다.
        patcher.start()
        self.addCleanup(patcher.stop)
        (folder / "status.md").write_text(f"# t\n\n### 실행 승인 범위\n\n- 사용자 승인 상태: {state}\n", encoding="utf-8")
        (folder / "spec.md").write_text(PLAN_OK + "\n## 재구현 요청\n\n### 재작업 범위\n", encoding="utf-8")
        report = REPORT_DOC.replace("- 적용할 작업: a", "- 적용할 작업: a\n- 장점: 동작 개선 (출처: hooks/a.py:1)\n- 단점: 수정 필요 (출처: hooks/a.py:2)")
        (folder / "research.md").write_text(report, encoding="utf-8")
        return folder / "spec.md", folder / "research.md"

    # Edit 입력으로 검사를 실행한다.
    def edit(self, path, old, new):
        return hook.check({"tool_name": "Edit", "tool_input": {"file_path": str(path), "old_string": old, "new_string": new},
                           "agent_type": "rio", "cwd": str(self.root),
                           "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인"))}, "claude")

    # 승인 뒤 명세 문서 계획과 조사 문서 검토 보고 수정은 deny하고 재구현 요청 추가는 통과한다. 승인 전에는 모두 통과한다.
    def test_locked_after_approval(self):
        spec, research = self.task()
        self.assertEqual(self.edit(spec, "  - `python -m unittest`: 요구사항 1", "  - `python -m pytest`: 요구사항 1"),
                         sub_scope.REASON_PLAN_LOCKED)
        # 재구현 요청의 새 항목은 근거 표시가 있고 그 시각이 검증 결과의 FAIL 줄과 일치할 때만 통과한다.
        add = lambda tail="": self.edit(spec, "### 재작업 범위\n", f"### 재작업 범위\n\n- 수정 위치: a.py:1{tail}\n")
        self.assertEqual(add(), sub_scope.REASON_EVIDENCE_REQUIRED)
        self.assertEqual(add(" (근거: 검증 2026-11-12 03:00:00)"),
                         sub_scope.REASON_EVIDENCE_TIME.format(kind="검증", stamp="2026-11-12 03:00:00"))
        status = spec.with_name("status.md")
        status.write_text(status.read_text(encoding="utf-8") + "\n### 검증 결과\n\n- 2026-11-12 03:00:00: 기능 (정적, 검증)\n  - FAIL: 입력 오류\n", encoding="utf-8")
        self.assertIsNone(add(" (근거: 검증 2026-11-12 03:00:00)"))
        spec, research = self.task("승인 대기")
        self.assertIsNone(self.edit(spec, "  - `python -m unittest`: 요구사항 1", "  - `python -m pytest`: 요구사항 1"))
        self.assertIsNone(self.edit(research, "- 적용할 작업: a", "- 적용할 작업: z"))

    # 재검증 요청의 판정 기준 칸에 파일:행이나 코드 식별자가 있으면 deny하고 요구사항 번호와 동작만 있으면 통과한다.
    def test_reverify_basis_rejects_code(self):
        spec, _ = self.task()
        spec.write_text(spec.read_text(encoding="utf-8") + "\n## 재검증 요청\n\n- 없음\n", encoding="utf-8")
        add = lambda basis: self.edit(spec, "## 재검증 요청\n\n- 없음\n",
                                      f"## 재검증 요청\n\n- hooks/a.py: 기능 (요구사항 35)\n  - 판정 기준: {basis}\n  - 커맨드: `python -m unittest`\n")
        for basis in ("`hooks/common/sub_gate.py:26-56`이 거부한다", "`check_plan`이 거부한다"):
            self.assertEqual(add(basis), sub_scope.REASON_BASIS_CODE.format(value=sub_scope.BASIS_CODE.search(basis)[0]), basis)
        self.assertIsNone(add("요구사항 35의 재검증 요청이 판정 기준과 커맨드 칸을 요구한다"))
        # 한글 단어 뒤 괄호는 허용하고 영문 함수 이름 뒤 괄호는 계속 거부한다.
        self.assertIsNone(add("요구사항 35의 칸 검사(판정 기준)가 거부한다"))
        self.assertEqual(add("check(x)가 거부한다"), sub_scope.REASON_BASIS_CODE.format(value="check("))

    # 사용자 근거 시각은 질문 답 기록 hook이 남긴 확정 답 시각(경과 문서 검토 필요 절, 명세 요구사항 사용자 확인 시각)일 때만 허용하고 검증 FAIL 근거는 유지한다.
    # 경과 문서의 이전 문제 정의 절 시각은 근거가 아니다.
    def test_user_evidence_is_confirmed_answer_time(self):
        spec, _ = self.task()
        status = spec.with_name("status.md")
        status.write_text(status.read_text(encoding="utf-8")
                          + "\n## 검토 필요\n\n- 검토 1. 도구: 안 1. A (사용자 검토 시각: 2026-11-12 04:00:00)\n"
                          "\n## 문제 정의\n\n- 문제 1. 범위\n  - 사용자 확인 시각: 2026-11-12 05:00:00\n"
                          "\n## 검증 결과\n\n- 2026-11-12 03:00:00: 기능 (정적, 검증)\n  - FAIL: 입력 오류\n", encoding="utf-8")
        add = lambda tail: self.edit(spec, "### 재작업 범위\n", f"### 재작업 범위\n\n- 수정 위치: a.py:1{tail}\n")
        # 일반 사용자 메시지 시각은 근거가 아니다.
        self.assertEqual(add(" (근거: 사용자 2026-11-12 06:00:00)"),
                         sub_scope.REASON_EVIDENCE_TIME.format(kind="사용자", stamp="2026-11-12 06:00:00"))
        self.assertIsNone(add(" (근거: 사용자 2026-11-12 04:00:00)"))
        self.assertEqual(add(" (근거: 사용자 2026-11-12 05:00:00)"),
                         sub_scope.REASON_EVIDENCE_TIME.format(kind="사용자", stamp="2026-11-12 05:00:00"))
        self.assertIsNone(add(" (근거: 검증 2026-11-12 03:00:00)"))
        self.assertIn("log-spec", sub_scope.REASON_EVIDENCE_TIME)
        spec_text = "## 요구사항 목록\n\n### 1. 기능\n\n#### 사용자 확인 시각\n\n2026-11-12 07:00:00\n\n#### 문제\n\n범위\n"
        self.assertIn("2026-11-12 07:00:00", sub_scope.confirmed_times("", spec_text))

    # 따옴표 안 이스케이프 따옴표, `>`, 경로 형식 문자열은 판정에서 빠지고 따옴표 밖 리다이렉트는 차단한다.
    def test_bash_quoted_strings(self):
        bash = lambda cmd: hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 커맨드"},
                                       "agent_type": "rio", "cwd": str(self.root),
                                       "transcript_path": self.transcript(("assistant", "보고"), ("user", "다시 생각해"))}, "claude")
        regex = r'''python -I -c "re.findall(r'[^.\"]*>', l)"'''
        self.assertIsNone(bash(regex))
        self.assertIsNone(bash(r'python -I -c "print(\"/opt/x/y\")"'))
        self.assertIsNotNone(bash("echo a > out.txt"))
        self.assertFalse(sub_write.is_write_bash(regex))
        self.assertTrue(sub_write.is_write_bash("echo a > out.txt"))


class ConstraintSource(unittest.TestCase):
    """한계와 위험 값의 출처 판정은 URL과 `파일:행`만 인정하고 `해소 판단 조건` 줄은 출처를 요구하지 않는다."""

    # 위험 항목에 `해소 판단 조건:` 줄이 있어도 출처 줄이 있으면 통과한다.
    def test_resolution_condition_line_needs_no_source(self):
        text = ("# t\n\n## 잠재적 위험\n\n- 미커밋 변경이 많다\n  - 출처: hooks/a.py:1\n"
                "  - 위험 확인 방법: git status\n  - 해소 판단 조건: 충돌 없이 반영된다\n")
        self.assertIsNone(sub_docs.constraint_reason(text, "state", "status.md"))

    # 표 칸 백슬래시와 grep 패턴은 출처로 보지 않고 위반 위치와 양식 이름을 반환한다.
    def test_escape_and_search_pattern_are_not_sources(self):
        for value in ("(출처: a\\|b:3)", '(출처: grep -n "a:1" hooks)', "(출처: 사용자 제공)"):
            text = f"# t\n\n## 원인 분석\n\n- 조건 누락: 원인\n  - 출처: hooks/a.py:1\n  - 한계: 외부 제약 {value}\n"
            self.assertEqual(sub_docs.constraint_reason(text, "state", "status.md"), "status.md 7행: log-status 양식을 따르십시오.", value)
        for value in ("(출처: hooks/a.py:12)", "(출처: https://example.com/a)", "(출처: .gitignore:2)"):
            text = f"# t\n\n## 원인 분석\n\n- 조건 누락: 원인\n  - 출처: hooks/a.py:1\n  - 한계: 외부 제약 {value}\n"
            self.assertIsNone(sub_docs.constraint_reason(text, "state", "status.md"), value)


class PlaceholderFill(unittest.TestCase):
    # 구현 승인 뒤 명세 잠금은 요구사항 절의 양식 자리표시 줄을 값으로 바꾸는 쓰기만 허용한다.
    def test_fill_only(self):
        old = "### 1. a\n\n- 문제: 문제 <번호>\n- 목표:\n  - 기존\n"
        self.assertTrue(sub_scope.placeholder_fill(old, old.replace("문제 <번호>", "문제 3")))
        self.assertFalse(sub_scope.placeholder_fill(old, old.replace("기존", "바뀜")))
        self.assertFalse(sub_scope.placeholder_fill(old, old + "- 추가\n"))
        # 통합 구조(`#### 칸` 제목) 절도 자리표시 줄을 채우는 쓰기만 허용하고 확정된 칸의 변경은 막는다.
        unified = "### 1. a\n\n#### 목표\n\n- <요구사항의 목표>\n\n#### 기대 결과\n\n- 확정 결과\n"
        self.assertTrue(sub_scope.placeholder_fill(unified, unified.replace("<요구사항의 목표>", "입력 오류를 거부한다.")))
        self.assertFalse(sub_scope.placeholder_fill(unified, unified.replace("확정 결과", "다른 결과")))


class TurnGuards(unittest.TestCase):
    """대기 메시지, 같은 턴 거부 호출 재실행, 쓰기 커맨드 우회, 판정 줄 삭제, 조사 재배정과 양식 줄 사유를 검사한다."""

    setUp = WriteScope.setUp
    tearDown = WriteScope.tearDown

    # JSON 항목 목록으로 대화 기록 파일을 만들고 경로를 반환한다.
    def lines(self, rows, name="turn.jsonl") -> str:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        return str(path)

    # tool_use 호출 항목과 그 결과 항목이다.
    @staticmethod
    def call(name, args, result, error=True, call_id="c1"):
        return [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": call_id, "name": name, "input": args}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": call_id, "is_error": error,
                                                          "content": result}]}}]

    # director 메인 세션의 도구 입력이다.
    def data(self, name, args, transcript, **extra):
        return {"tool_name": name, "tool_input": args, "agent_type": "rio", "cwd": str(self.root),
                "transcript_path": transcript, **extra}

    # Claude 메인에 대기 메시지가 있으면 도구를 막지 않고 메시지를 전달 문맥으로 만들며, 대기 없음, 서브에이전트, Codex는 만들지 않는다.
    def test_waiting_message_delivered_without_deny(self):
        user = {"type": "user", "message": {"content": "승인"}}
        queue = {"type": "queue-operation", "operation": "enqueue", "content": "멈추고 이것부터 봐 주세요"}
        waiting, idle = self.lines([user, queue], "wait.jsonl"), self.lines([user], "idle.jsonl")
        read = ("Read", {"file_path": str(self.root / "a.md")})
        self.assertIsNone(hook.check(self.data(*read, waiting), "claude"))
        context = hook.waiting_context(self.data(*read, waiting), "claude")
        self.assertTrue(context.startswith(sub_question.WAITING_HEAD) and context.endswith("멈추고 이것부터 봐 주세요"))
        self.assertIsNone(hook.waiting_context(self.data(*read, idle), "claude"))
        self.assertIsNone(hook.waiting_context(self.data(*read, waiting, agent_id="a1"), "claude"))
        self.assertIsNone(hook.waiting_context(self.data(*read, waiting), "codex"))

    # 작업 중 받은 메시지(queued_command)는 턴 시작 메시지의 승인을 풀지 않는다.
    def test_queued_message_keeps_turn_start_approval(self):
        user = {"type": "user", "message": {"content": "승인"}}
        queued = {"type": "attachment", "attachment": {"type": "queued_command", "commandMode": "prompt", "prompt": "왜 이래"}}
        path = self.lines([user, queued], "queued.jsonl")
        import sub_session
        self.assertEqual(sub_session.turn(sub_session.read_session(path))[0], "승인")

    # 이번 턴에 hook이 거부한 호출과 같은 입력은 막고, 고친 입력, 이전 턴 거부, 일반 도구 오류 뒤 재실행은 통과시킨다.
    def test_same_turn_denied_repeat(self):
        args = {"command": "ls x", "description": "목록 확인"}
        user = {"type": "user", "message": {"content": "승인"}}
        denied = self.call("Bash", args, "<error>PreToolUse:Bash hook error: 차단 사유</error>")
        failed = self.call("Bash", args, "Exit code 2\nls: cannot access 'x'")
        cases = (([user] + denied, args, True), ([user] + denied, {**args, "command": "ls y"}, False),
                 ([user] + denied + [{"type": "user", "message": {"content": "다시 해 주세요"}}], args, False),
                 ([user] + failed, args, False))
        for index, (rows, call, blocked) in enumerate(cases):
            reason = hook.check(self.data("Bash", call, self.lines(rows, f"repeat-{index}.jsonl")), "claude")
            self.assertEqual(reason == hook.REASON_REPEAT, blocked, (index, reason))

    # perl 제자리 편집과 hook 결과 쓰기 함수 호출은 쓰기 커맨드로 막고, perl 읽기와 쓰기 없는 인라인 코드는 통과시킨다.
    def test_perl_and_hook_write_calls(self):
        transcript = self.lines([{"type": "user", "message": {"content": "승인"}}], "bash.jsonl")
        bash = lambda cmd: hook.check(self.data("Bash", {"command": cmd, "description": "치환 확인"}, transcript), "claude")
        for cmd in ("perl -pi -e 's/a/b/' f.md", "perl -i.bak -pe 's/a/b/' f.md", "perl -0pi -e 's/a/b/' f.md",
                    'python -c "from save_subagent_result import write_result; write_result(1, 2, 3, False)"'):
            self.assertIsNotNone(bash(cmd), cmd)
            self.assertTrue(sub_write.is_write_bash(cmd) or sub_write.bash_write_reason(cmd), cmd)
        for cmd in ("perl -ne 'print' f.md", 'python -c "import json; print(1)"'):
            self.assertIsNone(bash(cmd), cmd)

    # 명세 결과에 없는 함수의 판정 줄을 지우는 hooks .py Edit은 막고, 결과에 함수가 있거나 판정 줄을 유지하면 통과시킨다.
    def test_judgment_line_drop(self):
        source = self.root / "hooks" / "a.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("def check_question(claims):\n    if claims:\n        return 1\n    return None\n", encoding="utf-8")
        head = patch.object(sub_scope, "head_text", return_value=source.read_text(encoding="utf-8"))
        head.start()
        self.addCleanup(head.stop)
        spec =self.root / ".agent-logs" / "task" / "20261116-000000-t" / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        agent = self.lines([{"type": "user", "message": {"content": "task: implement\ninput: .agent-logs/task/20261116-000000-t/spec.md"}}],
                           "agent.jsonl")
        edit = lambda old, new: hook.check({"tool_name": "Edit", "agent_type": "jelly", "agent_id": "a1", "cwd": str(self.root),
                                            "transcript_path": agent, "agent_transcript_path": agent,
                                            "tool_input": {"file_path": str(source), "old_string": old, "new_string": new}}, "claude")
        drop = ("    if claims:\n        return 1\n", "")
        spec.write_text("### 1. a\n\n- 결과:\n  - 다른 함수 수정\n", encoding="utf-8")
        reason = edit(*drop)
        self.assertIn("check_question", reason or "")
        self.assertIn("if claims:", reason or "")
        self.assertIsNone(edit("    if claims:\n", "    # 빈 목록이면 통과한다.\n    if claims:\n"))
        # 기준 커밋에 없는 판정 줄은 이번 작업에서 추가한 줄이므로 지울 수 있다.
        head.stop()
        with patch.object(sub_scope, "head_text", return_value=""):
            self.assertIsNone(edit(*drop))
        head.start()
        spec.write_text("### 1. a\n\n- 결과:\n  - check_question의 판정 삭제\n", encoding="utf-8")
        self.assertIsNone(edit(*drop))
        # 재구현 요청 절의 기대 결과 줄에 함수 이름이 있으면 허용하고, 요구사항 결과와 재구현 요청 어디에도 없으면 계속 막는다.
        spec.write_text("## 요구사항 목록\n\n### 1. a\n\n- 결과:\n  - 다른 함수 수정\n\n## 재구현 요청\n\n"
                        "- worker, hooks/a.py: 판정 삭제\n  - 기대 결과: check_question의 판정 줄을 지운다.\n", encoding="utf-8")
        self.assertIsNone(edit(*drop))
        spec.write_text("## 요구사항 목록\n\n### 1. a\n\n- 결과:\n  - 다른 함수 수정\n\n## 재구현 요청\n\n"
                        "- worker, hooks/a.py: 다른 수정\n  - 기대 결과: other_function을 고친다.\n", encoding="utf-8")
        self.assertIn("check_question", edit(*drop) or "")

    # 같은 조사 항목 보고가 서브에이전트 기록에 있고 결과 절이 비면 research 재배정을 막고, 보고 기록이 없거나 결과 절이 있으면 통과시킨다.
    def test_research_retry_after_saved_report(self):
        folder = self.root / ".agent-logs" / "task" / "20261116-000000-t"
        folder.mkdir(parents=True, exist_ok=True)
        research = folder / "research.md"
        plan = "# 조사 문서: t\n\n## 조사 계획\n\n### 조사 8. 도구 선택 (문제 1)\n\n## 조사 결과\n\n"
        research.write_text(plan + "### 조사 <번호>. <항목명> 결과\n", encoding="utf-8")
        prompt = "task: research\ninput: .agent-logs/task/20261116-000000-t/research.md\nitem: research 8"
        main = self.lines([{"type": "user", "message": {"content": "[plan] 조사"}}], "main.jsonl")
        data = {"tool_name": "Agent", "tool_input": {"subagent_type": "nico", "prompt": prompt, "description": "조사"},
                "agent_type": "rio", "cwd": str(self.root), "transcript_path": main}
        self.assertIsNone(sub_input.research_retry_reason(data, "claude"))
        report = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "h1", "name": "SubagentHandback",
                                                                "input": {"message": "### 조사 8. 도구 선택 결과\n\n- 결론: 안 1"}}]}}
        self.lines([{"type": "user", "message": {"content": prompt}}, report], "main/subagents/agent-a1.jsonl")
        reason = sub_input.research_retry_reason(data, "claude")
        self.assertEqual(reason, sub_input.REASON_RESEARCH_SAVED.format(number=8))
        self.assertIn("복구", reason)
        self.assertIsNone(sub_input.research_retry_reason(data, "codex"))
        research.write_text(plan + "### 조사 8. 도구 선택 결과\n\n- 결론: 안 1\n", encoding="utf-8")
        self.assertIsNone(sub_input.research_retry_reason(data, "claude"))

    # implement와 reimplement 배정 서브에이전트의 시험 실행 Bash는 막고, verify 배정과 시험이 아닌 커맨드는 통과시킨다.
    def test_implement_session_test_run(self):
        def run(task, command):
            agent = self.lines([{"type": "user", "message": {"content": f"task: {task}\ninput: .agent-logs/task/t/spec.md"}}], "agent.jsonl")
            return hook.check({"tool_name": "Bash", "agent_type": "jelly", "agent_id": "a1", "cwd": str(self.root), "transcript_path": agent,
                               "agent_transcript_path": agent, "tool_input": {"command": command, "description": "시험 실행"}}, "claude")
        self.assertEqual(run("implement", "python -m unittest discover -s hooks/tests"), hook.REASON_TEST_RUN)
        self.assertEqual(run("reimplement", "pytest"), hook.REASON_TEST_RUN)
        self.assertNotEqual(run("implement", "python -m pytest -q"), None)
        self.assertNotEqual(run("verify", "python -m unittest discover -s hooks/tests"), hook.REASON_TEST_RUN)
        self.assertNotEqual(run("implement", "git status"), hook.REASON_TEST_RUN)

    # 양식 사유에는 어긴 양식 줄 원문이 들어가고, 양식을 지킨 라벨은 통과한다.
    def test_form_reason_shows_template_line(self):
        self.assertIn("`- 안 <번호>. <안 이름>`", sub_question.label_gap(["1안"]))
        self.assertIn(sub_question.QUESTION_LINE, sub_question.REASON_FORMAT)
        self.assertIsNone(sub_question.label_gap(["안 1. 기존 유지", "안 2. 교체", "재조사"]))


if __name__ == "__main__":
    unittest.main()
