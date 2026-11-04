"""check_tool_use.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트, 공용 모듈, check_tool_use 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_tool_use as hook
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

| 종류 | 입력 | 명령 | 기대 결과 |
| --- | --- | --- | --- |
| 차단 | 누락 문서 | `python -m unittest` | deny |
| 허용 | 정상 문서 | `python -m unittest` | 통과 |

## 작업 계획

### 검증 계획

- 검증 명령
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
        self.state = self.root / "log" / "state"
        self.state.mkdir(parents=True)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.ostemp = tempfile.TemporaryDirectory()  # 프로젝트가 임시 폴더 안에 있어도 OS 임시 폴더 판정과 분리한다.
        # 단계 게이트는 test_sub_workflow의 완전한 세 문서 fixture로 검사한다.
        self.patches = [patch.object(hook, "call_reason", return_value=None),
                        patch.object(sub_scope, "transition_reason", return_value=None),
                        patch.object(sub_path, "TEMP", Path(self.ostemp.name).resolve()),
                        patch.object(sub_scope, "project_root", return_value=self.root),
                        patch.object(sub_docs, "project_root", return_value=self.root), patch.object(sub_role, "CODEX_SESSIONS", self.sessions),
                        # 템플릿 필수 절 검사는 test_template_titles만 다룬다. 다른 테스트는 빈 스킬 폴더를 쓴다.
                        patch.object(sub_docs, "SKILLS", self.root / "skills"),
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
        self.assertIsNone(self.write("log/state/x.md", "- 진행", transcript=no))
        self.assertIsNone(self.write("log/incident/a.md", "# 사건", transcript=no))
        self.assertIsNotNone(self.write("hooks/a.py", "x", transcript=no))
        self.assertIsNone(self.write("hooks/a.py", "x"))

    # 기대 결과 변경은 막고 기존 작업 칸과 다른 칸 변경은 허용한다.
    def test_spec_cells_locked(self):
        spec = self.root / "log" / "task" / "t" / "spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text(PLAN_OK, encoding="utf-8")
        edit = lambda old, new: hook.check({
            "tool_name": "Edit", "tool_input": {"file_path": str(spec), "old_string": old, "new_string": new},
            "agent_type": "rio", "cwd": str(self.root),
            "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인"))}, "claude")
        self.assertIsNone(edit("- 작업:\n  - c", "- 작업:\n  - x"))
        self.assertEqual(edit("- 기대 결과:\n  - e", "- 기대 결과:\n  - x"), sub_scope.REASON_SPEC_LOCKED)
        self.assertIsNone(edit("  - d (정적)", "  - x (정적)"))

        # 새 명세 문서는 기대 결과가 양식 자리표시 값일 때만 쓸 수 있다.
        blank = PLAN_OK.replace("  - e\n", "  - <판정 기준>\n")
        self.assertIsNone(self.write("log/task/n/spec.md", blank))
        self.assertEqual(self.write("log/task/n/spec.md", PLAN_OK), sub_scope.REASON_SPEC_LOCKED)

    # Codex apply_patch에서도 기대 결과 변경을 막고 기존 작업 칸 변경을 허용한다.
    def test_spec_cells_locked_codex(self):
        spec = self.root / "log" / "task" / "t" / "spec.md"
        spec.parent.mkdir(parents=True)
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

    # director가 OS 임시 폴더에 승인 없이 쓸 수 있는지 확인한다.
    def test_director_temp_write(self):
        no = self.transcript(("assistant", "계획"), ("user", "다시 생각해"))
        temp = Path(self.ostemp.name).resolve() / "scratch.txt"
        data = {"tool_name": "Write", "tool_input": {"file_path": str(temp), "content": "x"},
                "agent_type": "director", "cwd": str(self.root), "transcript_path": no}
        self.assertIsNone(hook.check(data, "claude"))
        self.assertIsNotNone(self.write("src/x.py", "x", role="director", transcript=no))

    # director의 Bash 명령이 쓰기 여부에 따라 deny되거나 통과하는지 확인한다.
    def test_director_bash(self):
        # 승인 없는 대화 기록을 만든다.
        no = self.transcript(("assistant", "계획"), ("user", "다시 생각해"))
        # director 세션의 Bash 입력으로 검사를 실행한다.
        def bash(cmd, transcript=no):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"},
                               "agent_type": "rio", "transcript_path": transcript}, "claude")
        # 읽기 전용 명령은 통과하는지 확인한다.
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
            self.assertTrue((bash(cmd) or "").endswith(sub_write.RETRY), cmd)
            self.assertIsNone(bash(cmd, approved), cmd)  # 승인 턴은 보안, 외부 경로, git 비가역 작업만 막는다.
            self.assertNotIn("·", bash(cmd) or "", cmd)
        # 쓰기 호출이 있는 인라인 코드는 승인 전에 막고 승인 턴에는 통과한다.
        for cmd in ("python -c \"open('a','w')\"", "node -e \"require('fs').writeFileSync('a','x')\""):
            self.assertIn("인라인 코드", bash(cmd) or "", cmd)
            self.assertIsNone(bash(cmd, approved), cmd)
        # 리다이렉트 사유에는 따옴표를 벗긴 실제 대상 경로가 나온다.
        self.assertIn("이 명령은 리다이렉트로 `C:/tmp/a b.txt`에 내용을 씁니다.", bash('echo x > "C:/tmp/a b.txt"') or "")

    # 조사 문서에 새로 들어가는 출처가 URL이나 파일:행이 아니면 Write와 apply_patch 모두 deny한다.
    def test_research_source_format(self):
        bad = RESEARCH + "\n- 제외 범위: 사전 실측 (출처: 사용자 답)\n"
        self.assertIsNotNone(self.write("log/state/n-research.md", bad))
        self.assertIsNone(self.write("log/state/n-research.md", bad.replace("사용자 답", "a.py:1")))
        self.assertIsNone(self.write("log/state/n-research.md", bad.replace("사용자 답", "hooks/x:3")))
        self.assertIsNotNone(self.write("log/state/n-research.md", bad.replace("사용자 답", "사용자 답 12:30")))
        self.assertIsNone(self.write("log/state/n-research.md", RESEARCH + "\n- 제외 범위: 없음\n"))
        patch_text = f"*** Begin Patch\n*** Add File: {self.root / 'log/state/n-research.md'}\n+출처: 사용자 메시지\n*** End Patch"
        data = {"tool_name": "apply_patch", "tool_input": {"command": patch_text}, "agent_type": "rio",
                "cwd": str(self.root), "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인"))}
        self.assertIn("출처", hook.check(data, "codex") or "")

    # 백틱이 든 출처 값은 사유에 백틱 한 겹으로 들어간다.
    def test_source_reason_single_backticks(self):
        reason = self.write("log/state/n-research.md", RESEARCH + "\n- 제외 범위: 사전 실측 (출처: `a/b.md`)\n")
        self.assertIn("출처 `", reason)
        self.assertNotIn("``", reason)

    # 경과 문서의 요구사항 목록과 검토 필요 절은 Edit, Write, apply_patch 모두 바꿀 수 없고 다른 절은 바꿀 수 있다.
    def test_state_sections_locked(self):
        template = ("# <작업명>\n\n- 작업 상태: <미개시 | 진행>\n\n### 요구사항 목록\n\n1. <요구사항 이름>: <내용>\n\n### 검토 필요\n\n- <항목>\n\n"
                    "### 작업 경과\n\n- 갱신 시각: <시각>\n")
        skill = self.root / "skills" / "nestlab-log-state"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"## Template\n\n```markdown\n{template}```\n", encoding="utf-8")
        rel = "log/task/t/status.md"
        path = self.root / rel
        locked = sub_scope.REASON_LOCKED
        doc = template.replace("<작업명>", "t")
        transcript = self.transcript(("assistant", "보고"), ("user", "승인"))

        # 새 문서는 두 절이 양식 원문이어야 통과한다.
        self.assertIsNone(self.write(rel, doc))
        self.assertEqual(self.write(rel, doc.replace("1. <요구사항 이름>", "1. 내가 쓴 요구사항")), locked)
        path.parent.mkdir(parents=True)
        path.write_text(doc, encoding="utf-8")

        # Edit과 Write는 두 절 변경을 막고 다른 절 변경은 통과한다.
        def edit(old, new):
            return hook.check({"tool_name": "Edit", "tool_input": {"file_path": str(path), "old_string": old, "new_string": new},
                               "agent_type": "rio", "cwd": str(self.root), "transcript_path": transcript}, "claude")
        self.assertEqual(edit("1. <요구사항 이름>", "1. 바꿈"), locked)
        self.assertEqual(self.write(rel, doc.replace("- <항목>\n\n", "")), locked)
        self.assertIsNone(edit("<시각>", "2026-11-03"))

        # apply_patch도 기존 본문에 적용한 결과로 같게 판정한다. 적용할 수 없는 패치는 막는다.
        def patch_of(body):
            text = f"*** Begin Patch\n*** Update File: {path}\n{body}\n*** End Patch"
            return hook.check({"tool_name": "apply_patch", "tool_input": {"command": text}, "agent_type": "rio",
                               "cwd": str(self.root), "transcript_path": transcript}, "codex")
        self.assertEqual(patch_of("@@\n ### 검토 필요\n \n-- <항목>\n+- 내가 쓴 항목"), locked)
        self.assertIsNone(patch_of("@@\n ### 작업 경과\n \n - 갱신 시각: <시각>\n+- 추가 줄"))
        self.assertEqual(patch_of("@@\n-없는 줄\n+새 줄"), locked)

        # 작업 상태 줄 변경은 막는다.
        self.assertEqual(edit("- 작업 상태: <미개시 | 진행>", "- 작업 상태: 완료"), locked)
        self.assertEqual(self.write(rel, doc.replace("<미개시 | 진행>", "진행")), locked)

    # 질문 도구 입력에 출처 없는 확인 주장이 있으면 막고, 출처가 있거나 주장이 없으면 통과한다.
    def test_question_unsourced_claim(self):
        def ask(description):
            data = {"tool_name": "AskUserQuestion", "agent_type": "rio", "cwd": str(self.root),
                    "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인")),
                    "tool_input": {"questions": [{"header": "결정", "question": "어느 안으로 할까요?",
                                                  "options": [{"label": "A", "description": description}]}]}}
            return hook.check(data, "claude")
        self.assertIn("근거가 없습니다", ask("두 runner 모두 같은 방식으로 동작합니다."))
        self.assertIsNone(ask("두 runner 모두 같은 방식으로 동작합니다 (`shared/settings/codex/config.example.toml:95`)."))
        self.assertIsNone(ask("두 runner에 같은 설정을 적용합니다."))

    # Claude Bash는 설명 칸이 비었거나 명령을 그대로 옮기면 막는다. Codex 입력에는 설명 칸이 없다.
    def test_bash_description_required(self):
        # description 값을 바꿔 Bash 입력 검사를 실행한다.
        def bash(tin, runner="claude"):
            return hook.check({"tool_name": "Bash", "tool_input": tin, "agent_type": "rio"}, runner)
        # description이 비거나 명령 복사이면 막고 정상 값은 통과하는지 확인한다.
        self.assertIn("description", bash({"command": "git status"}))
        self.assertIn("description", bash({"command": "git status", "description": "  "}))
        self.assertIn("description", bash({"command": "git log --oneline -5", "description": "git log --oneline -5"}))
        self.assertIsNone(bash({"command": "git status", "description": "작업 트리 상태 확인"}))
        self.assertIsNone(bash({"command": "git status"}, "codex"))

    # 실행 승인 기록 쓰기에서 명세 문서 계획 검사(transition_reason)가 한 번만 실행되고 그 사유로 deny되는지 확인한다.
    def test_execution_approval_plan_checked_once(self):
        with patch.object(sub_scope, "transition_reason", return_value="계획 칸이 비었습니다.") as check:
            self.assertEqual(self.write("log/state/t.md", STATE_APPROVED), "계획 칸이 비었습니다.")
            self.assertEqual(check.call_count, 1)
        with patch.object(sub_scope, "transition_reason", return_value=None) as check:
            self.assertIsNone(self.write("log/state/t.md", STATE_APPROVED))
            self.assertEqual(check.call_count, 1)

    # 사용자 승인 명령이 있어야 승인 상태를 쓸 수 있는지 확인한다.
    def test_approval_status_needs_user_approval(self):
        for doc in ("### 착수 승인 범위\n\n- 승인 상태: 승인\n", "### 착수 승인 범위\n\n- 사용자 승인 상태: 승인\n"):
            no = self.transcript(("assistant", "계획"), ("user", "좋아 계속"))  # 같은 기록 파일을 덮어쓰므로 매번 만든다.
            self.assertIsNotNone(self.write("log/state/t.md", doc, transcript=no), doc)
            self.assertIsNone(self.write("log/state/t.md", doc), doc)

    # 조사 문서의 최종 원문 칸 변경이 deny되는지 확인한다.
    def test_raw_block_protected(self):
        (self.state / "t-research.md").write_text(RESEARCH, encoding="utf-8")
        self.assertIsNotNone(self.write("log/state/t-research.md", RESEARCH.replace("기록 없음", "고침")))
        self.assertIsNone(self.write("log/state/t-research.md", RESEARCH.replace("| A    | 낮음 |", "| A | 중간 |")))

    # 조사 문서의 회차 반증 블록 변경이 deny되는지 확인한다.
    def test_round_blocks_protected(self):
        rounds = RESEARCH + "\n### 1회차 반증 원문\n\n```\n- 주장 1: 반증\n```\n"
        (self.state / "t-research.md").write_text(rounds, encoding="utf-8")
        self.assertIsNotNone(self.write("log/state/t-research.md", rounds.replace("반증\n```", "지지\n```")))
        self.assertIsNotNone(self.write("log/state/t-research.md", RESEARCH))
        self.assertIsNone(self.write("log/state/t-research.md", rounds.replace("| A    | 낮음 |", "| A | 중간 |")))
        self.assertIsNotNone(self.write("log/state/n-research.md", rounds))

    # 반증 문서는 빈 양식 전체 덮어쓰기만 허용하고 회차 블록 일부만 지우는 쓰기는 deny한다.
    def test_refute_doc_blank_overwrite_only(self):
        blank = "# 반증 문서: t\n\n## 조사 및 반증 루프\n\n## 회차 기록\n\n### 최종 반증 원문\n\n```\n기록 없음\n```\n"
        rounds = ("# 반증 문서: t\n\n## 조사 및 반증 루프\n\n## 회차 기록\n\n"
                  "### 1회차 반증 원문\n\n```\n- 주장 1: 반증\n```\n\n### 2회차 반증 원문\n\n```\n- 주장 1: 지지\n```\n")
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True)
        (folder / "refute.md").write_text(rounds, encoding="utf-8")
        self.assertIsNone(self.write("log/task/t/refute.md", blank))
        partial = rounds[:rounds.index("### 2회차")]
        self.assertIn("회차 반증 블록", self.write("log/task/t/refute.md", partial) or "")

    # nestlab-rule 호출 없이 전역 지침과 역할 MAIN.md, SUB.md를 고치는 쓰기는 deny하고 호출 뒤에는 통과한다.
    def test_rule_skill_required_for_guideline_files(self):
        for rel in ("AGENTS.global.md", ".claude/agents/director/MAIN.md", ".claude/agents/researcher/SUB.md"):
            with self.subTest(rel=rel):
                with patch.object(sub_scope, "skill_called", return_value=False):
                    self.assertIn("nestlab-rule", self.write(rel, "x") or "")
                self.assertIsNone(self.write(rel, "x"))
        self.assertEqual(sub_scope.skill_for(".claude/agents/director/SOUL.md"), None)

    # 새 조사 문서는 빈 원문 칸일 때만 허용되는지 확인한다.
    def test_new_research_doc_allows_empty_raw_only(self):
        self.assertIsNone(self.write("log/state/n-research.md", RESEARCH))
        self.assertIsNotNone(self.write("log/state/n-research.md", RESEARCH.replace("기록 없음", "가짜 결과")))

    # 스킬 원본 템플릿 주석과 진행 중인 작업 경과 문서를 준비한다. status는 검토 필요 항목의 상태이고 approved는 실행 승인 여부이며 measure는 실측 필요 항목 (시점, 상태)이다.
    def running_task(self, status, approved=True, measure=None):
        skill = self.root / "skills" / "nestlab-log-spec"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("<!-- 모든 목록이 검토 완료 또는 폐기일 때 승인 가능 -->\n", encoding="utf-8")
        folder = self.root / "log" / "task" / "t"
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
        self.assertIsNone(self.write("log/task/t/status.md", path.read_text(encoding="utf-8")))
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

    # 조사 문서에 답 없는 `### 검토 <번호>.` 보고가 있으면 실행 승인 뒤 log 밖 쓰기를 막고, 경과 문서에 검토 완료나 폐기로 있으면 통과한다.
    def test_unanswered_report_blocks_outside_write(self):
        path = self.running_task("검토 완료")
        (path.parent / "research.md").write_text("# 조사\n\n## 검토 보고\n\n### 검토 4. X\n\n- 비교\n", encoding="utf-8")
        self.assertIn("검토 4. X", self.write("hooks/a.py", "x") or "")
        for status in ("검토 완료", "폐기"):
            path.write_text(path.read_text(encoding="utf-8") + f"\n- 검토 4. X: 정한다.\n  - 상태: {status}\n", encoding="utf-8")
            self.assertIsNone(self.write("hooks/a.py", "x"), status)
            path.write_text(path.read_text(encoding="utf-8").split("\n- 검토 4.")[0] + "\n", encoding="utf-8")

    # 경과 문서 검증 결과에 새 항목을 쓰면 nestlab-verdict 호출을 요구한다.
    def test_verdict_skill_required_for_verification_item(self):
        body = "# t\n\n## 검증 결과\n\n- 2026-10-27 05:00:00: PASS\n"
        other = "# t\n\n## 경과\n\n- 2026-10-27 05:00:00: 메모\n"
        with patch.object(sub_scope, "skill_called", side_effect=lambda d, s, r="claude": s != "nestlab-verdict"):
            self.assertIn("nestlab-verdict", self.write("log/task/t/status.md", body) or "")
            self.assertIsNone(self.write("log/task/t/status.md", other))
        self.assertIsNone(self.write("log/task/t/status.md", body))

    # 템플릿 주석이 없으면 진행 중인 작업이 있는 log 밖 쓰기를 막는다.
    def test_missing_template_comment_blocks_outside_write(self):
        self.running_task("검토 완료")
        (self.root / "skills" / "nestlab-log-spec" / "SKILL.md").write_text("# 주석 없음\n", encoding="utf-8")
        self.assertIn("주석이 없습니다", self.write("hooks/a.py", "x") or "")

    # 메인 세션 Bash는 승인 전 쓰기 명령을 막고 승인 턴에는 통과시키며 읽기 명령은 통과한다.
    def test_main_session_bash_denies_writes(self):
        def bash(cmd, user="승인"):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"},
                               "agent_type": "rio", "cwd": str(self.root),
                               "transcript_path": self.transcript(("assistant", "계획"), ("user", user))}, "claude")
        for cmd in ("sed -i s/a/b/ a.py", "echo x > a.txt", "rm a.txt"):
            self.assertTrue((bash(cmd, "다시 생각해") or "").endswith((sub_write.RETRY, sub_write.APPROVAL_RETRY)), cmd)
            self.assertIsNone(bash(cmd), cmd)
        self.assertIsNone(bash("git status"))
        self.assertIsNone(bash("grep -n def hooks/common/sub_write.py"))
        # 승인 턴에는 인라인 코드와 셸 변수, 반복문, 저장소 안 git -C를 hook이 막지 않는다.
        for cmd in ('python -c "print(1)"', 'D=/x; ls "$D"', "for f in a b; do echo f; done", "git -C hooks log -1",
                    "cat <<'E'\na\nb\nE"):
            self.assertIsNone(bash(cmd), cmd)
        # 승인 턴에도 git -c와 cd는 막는다.
        self.assertEqual(bash("git -c a.b=c log -1"), sub_write.REASON_GIT_C)
        for cmd in ("cd hooks && git status", "cd hooks && git status --short", "cd hooks && grep -n def sub_write.py"):
            self.assertEqual(bash(cmd), sub_write.REASON_CD, cmd)

    # 실행 승인 뒤 worker와 documenter만 쓰기 Bash를 쓰고 log/ 경로가 든 명령과 reviewer의 쓰기는 막는다.
    def test_write_roles_bash_after_approval(self):
        self.running_task("검토 완료")
        prompt = self.root / "p.jsonl"
        prompt.write_text(claude_line("user", "작업 종류: 구현\n명세 문서: log/task/t/spec.md") + "\n", encoding="utf-8")

        def bash(role, cmd):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"},
                               "agent_type": role, "agent_id": "sub-1", "cwd": str(self.root),
                               "agent_transcript_path": str(prompt), "transcript_path": str(prompt)}, "claude")
        for role in ("worker", "documenter"):
            self.assertIsNone(bash(role, "sed -i s/a/b/ a.py"), role)
            self.assertIsNone(bash(role, "python -c \"print(1)\""), role)
            self.assertIn("log/", bash(role, "sed -i s/a/b/ log/task/t/spec.md") or "", role)
            self.assertIsNone(bash(role, 'D=/x; ls "$D"'), role)
            self.assertIsNone(bash(role, "python - <<'E'\nprint(1)\nprint(2)\nE"), role)
        self.assertIsNotNone(bash("reviewer", "sed -i s/a/b/ a.py"))

        # 실행 승인 전에는 worker도 쓰기 Bash를 쓸 수 없다.
        self.running_task("검토 완료", approved=False)
        self.assertIsNotNone(bash("worker", "sed -i s/a/b/ a.py"))

    # 따옴표 안 정규식 인자는 경로로 보지 않고 저장소 밖 경로는 계속 막는다.
    def test_quoted_regex_is_not_path(self):
        def bash(cmd):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"},
                               "agent_type": "worker", "agent_id": "sub-1", "cwd": str(self.root)}, "claude")
        self.assertIsNone(bash(r'grep -rn "\.active" hooks'))
        self.assertIsNone(bash(r"grep -rn 'a\(b\)' hooks"))
        self.assertIsNotNone(bash("cat C:/Windows/win.ini"))

    # 읽기 전용
    def test_readonly_roles(self):
        self.assertIsNotNone(self.write("log/state/x.md", "x", role="nico"))
        self.assertIsNotNone(self.write("log/state/x.md", "x", role="ricky"))
        self.assertIsNone(self.write("src/a.py", "x", role="jelly"))

    # 메인 세션은 역할과 무관하게 승인 없이 log/ 밖에 쓸 수 없고, 서브에이전트는 이 검사를 받지 않는다.
    def test_main_session_write_needs_approval(self):
        for role in ("buddy", "rio", "documenter", "worker", None):
            no = self.transcript(("assistant", "diff"), ("user", "고쳐"))  # 같은 기록 파일을 덮어쓰므로 매번 만든다.
            self.assertIn("승인", self.write("README.md", "x", role=role, transcript=no) or "", role)
            self.assertIsNone(self.write("README.md", "x", role=role), role)
        no = self.transcript(("assistant", "diff"), ("user", "고쳐"))
        data = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / "README.md"), "content": "x"},
                "agent_type": "jelly", "agent_id": "sub-1", "cwd": str(self.root), "transcript_path": no}
        self.assertIsNone(hook.check(data, "claude"))

    # assistant 메인 세션은 승인 명령 턴에만 쓰기 Bash를 쓰고 log/ 경로 명령은 막으며 저장소 밖 git -C는 계속 막는다.
    def test_assistant_main_session_not_gated(self):
        self.running_task("미검토")
        yes = self.transcript(("assistant", "계획"), ("user", "승인"))

        def bash(cmd, transcript=yes):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"},
                               "agent_type": "buddy", "cwd": str(self.root), "transcript_path": transcript}, "claude")
        for cmd in ("rm a.txt", "mv a b", "sed -i s/a/b/ a.py", 'python -c "print(1)"'):
            self.assertIsNone(bash(cmd), cmd)
        self.assertIn("log/", bash("sed -i s/a/b/ log/task/t/status.md") or "")
        self.assertIsNone(bash("git -C hooks log -1"))
        self.assertIsNotNone(bash("git -C /x log -1"))
        no = self.transcript(("assistant", "계획"), ("user", "고쳐"))
        self.assertIsNotNone(bash("sed -i s/a/b/ a.py", no))
        self.assertIsNone(self.write("hooks/a.py", "x", role="buddy"))

    # git 복구, 삭제 명령은 승인 전 director 메인 세션에서 deny되고 승인 턴에는 설정 deny 규칙에 맡겨 hook이 막지 않는다.
    def test_main_session_bash_write_commands_denied(self):
        for cmd in ("git checkout -- README.md", "git restore README.md", "git apply a.patch", "git reset --hard",
                    "find . -name '*.tmp' -delete", "ls | xargs rm"):
            data = {"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"}, "agent_type": "rio",
                    "cwd": str(self.root)}
            no = self.transcript(("assistant", "diff"), ("user", "고쳐"))  # 같은 기록 파일을 덮어쓰므로 매번 만든다.
            self.assertIsNotNone(hook.check({**data, "transcript_path": no}, "claude"), cmd)
            yes = self.transcript(("assistant", "diff"), ("user", "승인"))
            self.assertIsNone(hook.check({**data, "transcript_path": yes}, "claude"), cmd)

    # 저장소 안 절대 경로로 이동하는 cd는 deny되고 cd 없는 명령은 통과한다.
    def test_absolute_cd_denied(self):
        def bash(cmd):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"},
                               "agent_type": "rio", "cwd": str(self.root),
                               "transcript_path": self.transcript(("assistant", "계획"), ("user", "다시 생각해"))}, "claude")
        root = self.root.as_posix()
        for cmd in (f"cd {root} && ls -t log/task/ | head -10", f"cd {root} && ls -t log/task/"):
            self.assertEqual(bash(cmd), sub_write.REASON_CD, cmd)
        self.assertIsNone(bash("ls -t log/task/"))

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
    def agent(self, prompt, user, **extra):
        data = {"tool_name": "Agent", "tool_input": {"subagent_type": "jelly", "prompt": prompt},
                "agent_type": "rio", "session_id": "s1",
                "transcript_path": self.transcript(("assistant", "계획"), ("user", user)), **extra}
        return hook.check(data, "claude")

    # 서브에이전트 호출에 사용자 승인 명령이 필요한지 확인한다.
    def test_agent_call_needs_approval(self):
        prompt = "작업 종류: 구현\n입력 문서: log/state/t-input.md"
        self.assertIsNotNone(self.agent(prompt, "다시 생각해"))
        self.assertIsNone(self.agent(prompt, "승인"))
        self.assertEqual(self.agent(prompt, "다시 생각해", agent_id="sub-1"), hook.REASON_SUB_SPAWN)

    # 승인 명령은 사용자 메시지의 마지막 줄에 있을 때만 인정하는지 확인한다.
    def test_approval_last_line_command(self):
        # 승인 명령은 마지막 줄 끝의 `승인`뿐이다(sub_approval.APPROVAL). 다른 동사는 계획 수정 요청과 구별되지 않는다.
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
        path = self.state / "t.md"
        for kind in sub_approval.IN_SCOPE:
            prompt = f"{kind}\n입력 문서: log/state/t-input.md"
            path.unlink(missing_ok=True)
            self.assertIsNotNone(self.agent(prompt, "이 부분 고쳐"), kind)
            path.write_text(STATE_APPROVED, encoding="utf-8")
            self.assertIsNone(self.agent(prompt, "이 부분 고쳐"), kind)

    # 복원된 문서 작성과 구현은 같은 작업의 실행 승인과 실제 대기 재작업이 모두 있어야 허용한다.
    def test_restored_work_requires_approved_pending_rework(self):
        folder = self.root / "log/task/20261104-234558-test"
        folder.mkdir(parents=True)
        status, spec = folder / "status.md", folder / "spec.md"
        pending = "# 명세\n## 재작업 요청\n- hooks/a.py: 입력 검사 수정 (요구사항 1)\n  - 기대 결과: 올바른 입력을 처리한다\n"
        for kind in sub_approval.RESTORED_WORK:
            prompt = f"{kind}\n명세 문서: log/task/20261104-234558-test/spec.md"
            spec.write_text(pending, encoding="utf-8")
            status.write_text(STATE_APPROVED.replace("상태: 승인", "상태: 승인 대기"), encoding="utf-8")
            self.assertEqual(sub_approval.approval_reason("수정해 주세요", prompt, str(self.root)), sub_approval.REASON_APPROVAL)
            status.write_text(STATE_APPROVED, encoding="utf-8")
            self.assertIsNone(sub_approval.approval_reason("수정해 주세요", prompt, str(self.root)))
            for empty in ("", "없음", "- 없음", "- hooks/a.py: <수정 위치>\n  - 기대 결과: <동작>"):
                with self.subTest(kind=kind, empty=empty):
                    spec.write_text("# 명세\n## 재작업 요청\n" + empty, encoding="utf-8")
                    self.assertEqual(sub_approval.approval_reason("수정해 주세요", prompt, str(self.root)), sub_approval.REASON_APPROVAL)
            spec.write_text(pending, encoding="utf-8")
            other = prompt.replace("234558-test", "234558-other")
            self.assertEqual(sub_approval.approval_reason("수정해 주세요", other, str(self.root)), sub_approval.REASON_APPROVAL)

    # 항목의 실제 대상과 기대 결과만 판정하고 설명의 질문 양식 예시는 대기 작업을 지우지 않는다.
    def test_pending_rework_ignores_example_in_description(self):
        folder = self.root / "log/task/20261104-234558-test"
        folder.mkdir(parents=True)
        (folder / "status.md").write_text(STATE_APPROVED, encoding="utf-8")
        spec = folder / "spec.md"
        example = "  - 설명: 문제 정의 확인: <보고한 문제 정의와 범위> (작업 MMDD-HHMMSS)\n"
        valid = "- hooks/a.py: 입력 검사 수정 (요구사항 1)\n  - 기대 결과: 올바른 입력을 처리한다\n"
        for kind in sub_approval.RESTORED_WORK:
            prompt = f"{kind}\n명세 문서: log/task/20261104-234558-test/spec.md"
            for body in (valid + example, valid + example + "- <다른 대상>: <수정 위치>\n  - 기대 결과: <동작>\n"):
                with self.subTest(kind=kind, body=body):
                    spec.write_text("# 명세\n## 재작업 요청\n" + body, encoding="utf-8")
                    self.assertIsNone(sub_approval.approval_reason("수정해 주세요", prompt, str(self.root)))
            spec.write_text("# 명세\n## 재작업 요청\n" + valid.replace("  - 기대 결과: 올바른 입력을 처리한다\n", "") + example, encoding="utf-8")
            self.assertEqual(sub_approval.approval_reason("수정해 주세요", prompt, str(self.root)), sub_approval.REASON_APPROVAL)
            for target, location, result in (("", "수정", "입력을 처리한다"), ("<파일>", "수정", "입력을 처리한다"),
                                             ("hooks/a.py", "", "입력을 처리한다"), ("hooks/a.py", "<수정 위치>", "입력을 처리한다"),
                                             ("hooks/a.py", "수정", ""), ("hooks/a.py", "수정", "<관측할 동작>")):
                with self.subTest(kind=kind, target=target, location=location, result=result):
                    body = f"- {target}: {location}\n  - 기대 결과: {result}\n" + example
                    spec.write_text("# 명세\n## 재작업 요청\n" + body, encoding="utf-8")
                    self.assertEqual(sub_approval.approval_reason("수정해 주세요", prompt, str(self.root)), sub_approval.REASON_APPROVAL)

    # 암호문과 task_name에서 복원된 두 역할의 읽기 호출도 기존 승인 근거를 확인한다.
    def test_codex_restored_rework_allows_get_content(self):
        folder = self.root / "log/task/20261104-234558-test"
        folder.mkdir(parents=True)
        status, spec = folder / "status.md", folder / "spec.md"
        status.write_text(STATE_APPROVED, encoding="utf-8")
        spec.write_text("# 명세\n## 재작업 요청\n- hooks/a.py: 입력 검사 수정 (요구사항 1)\n  - 기대 결과: 입력을 처리한다\n", encoding="utf-8")
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
                spec.write_text("# 명세\n## 재작업 요청\n없음\n", encoding="utf-8")
                self.assertEqual(hook.check(data, "codex"), sub_approval.REASON_APPROVAL)
                spec.write_text("# 명세\n## 재작업 요청\n- hooks/a.py: 입력 검사 수정 (요구사항 1)\n  - 기대 결과: 입력을 처리한다\n", encoding="utf-8")

    # 두 runner의 실제 질문 답 형식과 UTC 시각을 사용해 문제 정의 확인 기록을 만든다.
    def problem_reply(self, runner, answer="확인", tag="1104-234558", later=None):
        question = f"문제 정의 확인: 파일 처리 문제와 수정 범위 (작업 {tag})"
        queries = [("q1", question, answer)]
        if later is not None:
            queries.append(("q2", question.replace("수정 범위", "추가 수정 범위"), later))
        rows = []
        if runner == "claude":
            rows.append({"type": "user", "message": {"content": "문제를 확인해 주세요."}})
            for qid, text, value in queries:
                rows += [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": qid, "name": "AskUserQuestion",
                          "input": {"questions": [{"question": text, "options": [{"label": "확인"}, {"label": "수정 요청"}]}]}}]}},
                         {"type": "user", "timestamp": "2026-11-04T19:20:00Z", "message": {"content": [
                             {"type": "tool_result", "tool_use_id": qid, "content": "답변"}]}, "toolUseResult": {"answers": {text: value}}}]
        else:
            item = lambda payload: {"type": "response_item", "timestamp": "2026-11-04T19:20:00Z", "payload": payload}
            rows.append(item({"type": "message", "role": "user", "content": [{"text": "문제를 확인해 주세요."}]}))
            for qid, text, value in queries:
                rows += [item({"type": "function_call", "name": "request_user_input", "call_id": qid,
                               "arguments": json.dumps({"questions": [{"id": qid, "question": text,
                                             "options": [{"label": "확인"}, {"label": "수정 요청"}]}]})}),
                         item({"type": "function_call_output", "call_id": qid, "output": json.dumps({
                             "answers": {qid: {"answers": [value] if value else []}}})})]
        path = self.root / f"{runner}-problem.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        return str(path)

    # 새 문제 정의는 실제 같은 작업의 긍정 답, 답 시각과 확인 요약을 갖춰야 쓸 수 있다.
    def test_problem_definition_confirmation(self):
        path = self.root / "log/task/20261104-234558-test/status.md"
        path.parent.mkdir(parents=True)
        old = "# 작업\n## 문제 정의\n"
        path.write_text(old, encoding="utf-8")
        block = ("- 파일 처리 문제: 입력 오류를 확인한다\n"
                 "  - 요구 구분: {kind}\n  - 사용자 확인 시각: {stamp}\n  - 확인 내용: {summary}\n")
        stamp = "2026-11-05 04:20:00"
        for runner in ("claude", "codex"):
            for kind in (f"director 해석 (사용자 확인 {stamp})", "사용자 지시 (로그 2026-11-05 04:00:00)"):
                content = old + block.format(kind=kind, stamp=stamp, summary="보고한 문제와 범위를 확정했다")
                data = {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": content}, "agent_type": "rio",
                        "cwd": str(self.root), "transcript_path": self.problem_reply(runner)}
                with self.subTest(runner=runner, kind=kind):
                    self.assertIsNone(hook.check(data, runner))
                    for answer, tag, later in (("수정 요청", "1104-234558", None), ("", "1104-234558", None),
                                               ("확인", "1104-999999", None), ("확인", "1104-234558", "수정 요청")):
                        data["transcript_path"] = self.problem_reply(runner, answer, tag, later)
                        self.assertIn("실제 사용자 확인", hook.check(data, runner) or "")
                    data["transcript_path"] = self.problem_reply(runner)
                    for summary in ("", "<사용자 답 요약>"):
                        data["tool_input"]["content"] = content.replace("보고한 문제와 범위를 확정했다", summary)
                        self.assertIn("실제 사용자 확인", hook.check(data, runner) or "")
                    data["tool_input"]["content"] = content.replace("  - 확인 내용: 보고한 문제와 범위를 확정했다\n", "")
                    self.assertIn("실제 사용자 확인", hook.check(data, runner) or "")
                    for value in ("", "<시각>", "2026-11-05 04:21:00"):
                        data["tool_input"]["content"] = content.replace(f"사용자 확인 시각: {stamp}", f"사용자 확인 시각: {value}")
                        self.assertIn("실제 사용자 확인", hook.check(data, runner) or "")
                    data["tool_input"]["content"] = content.replace(f"  - 사용자 확인 시각: {stamp}\n", "")
                    self.assertIn("실제 사용자 확인", hook.check(data, runner) or "")
                    if kind.startswith("director"):
                        data["tool_input"]["content"] = content.replace(f"사용자 확인 {stamp}", "사용자 확인 2026-11-05 04:21:00")
                        self.assertIn("실제 사용자 확인", hook.check(data, runner) or "")

    # 기존 검토 번호 방식의 역사적 확인 기록은 기존 상태 대조를 유지한다.
    def test_legacy_problem_confirmation_kept(self):
        added = ["  - 요구 구분: director 해석 (검토 3)"]
        self.assertEqual(sub_scope.unconfirmed(added, ""), "3")
        self.assertIsNone(sub_scope.unconfirmed(added, "## 검토 필요\n- 검토 3. 해석\n  - 상태: 검토 완료\n"))

    # 실제 문제 항목의 누락과 임의 값은 다른 정상 항목의 확인 근거로도 통과할 수 없다.
    def test_problem_definition_kind_required(self):
        path = self.root / "log/task/20261104-234558-test/status.md"
        path.parent.mkdir(parents=True)
        old = "# 작업\n## 문제 정의\n"
        path.write_text(old, encoding="utf-8")
        confirmation = "  - 사용자 확인 시각: 2026-11-05 04:20:00\n  - 확인 내용: 문제와 범위를 확정했다\n"
        valid = "- 첫 문제: 입력 오류\n  - 요구 구분: 사용자 지시 (로그 2026-11-05 04:00:00)\n" + confirmation
        for runner in ("claude", "codex"):
            for kind in (None, "", "임의 값", "사용자 지시 임의 값", "<사용자 지시 | director 해석>"):
                for prefix in ("", valid):
                    with self.subTest(runner=runner, kind=kind, prefix=bool(prefix)):
                        field = f"  - 요구 구분: {kind}\n" if kind is not None else ""
                        content = old + prefix + "- 새 문제: 다른 입력 오류\n" + field + confirmation
                        data = {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": content},
                                "agent_type": "rio", "cwd": str(self.root), "transcript_path": self.problem_reply(runner)}
                        self.assertIn("실제 사용자 확인", hook.check(data, runner) or "")
                        self.assertIn("실제 사용자 확인", sub_scope.check_doc(data, path, "log/task/20261104-234558-test/status.md", "") or "")

    # 빈 양식과 변경하지 않은 역사적 항목은 새 문제 정의로 판정하지 않는다.
    def test_problem_definition_template_and_history_kept(self):
        path = self.root / "log/task/20261104-234558-test/status.md"
        path.parent.mkdir(parents=True)
        base = "# 작업\n## 문제 정의\n"
        template = base + "- <항목>: <관측 사실>\n  - 요구 구분: <사용자 지시 | director 해석>\n"
        history = base + "- 이전 문제: 과거 기록\n  - 요구 구분: 이전 분류\n"
        for old, new in ((base, template), (history, history)):
            path.write_text(old, encoding="utf-8")
            self.assertIsNone(sub_scope.problem_confirmation_gap({}, path, new, old))
        self.assertIn("실제 사용자 확인", sub_scope.problem_confirmation_gap({}, path, history.replace("과거 기록", "변경한 기록"), history) or "")

    # 검증 호출에는 사용자 승인이 필요한지 확인한다.
    def test_review_needs_user_approval(self):
        (self.state / "t.md").write_text(STATE_APPROVED, encoding="utf-8")
        prompt = "작업 종류: 검증\n명세 문서: log/state/t-input.md"
        self.assertIsNotNone(self.agent(prompt, "이 부분 고쳐"))
        self.assertIsNone(self.agent(prompt, "검증 승인"))

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
            codex_msg("user", "작업 종류: 구현\n입력 문서: log/state/t-input.md")]) + "\n", encoding="utf-8")
        data = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "transcript_path": str(child)}
        parent.write_text("\n".join([codex_meta(), codex_msg("user", "다시 생각해")]) + "\n", encoding="utf-8")
        self.assertIsNotNone(hook.check(data, "codex"))
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
        doc = self.state / "t-research.md"
        prompt = "작업 종류: 조사\n조사 문서: log/state/t-research.md"
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
            codex_msg("user", "작업 종류: 조사\n조사 문서: log/state/t-research.md")]) + "\n", encoding="utf-8")
        doc = self.state / "t-research.md"
        data = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "transcript_path": str(child), "cwd": str(self.root)}
        doc.write_text("기준 커밋 cb78b92\n", encoding="utf-8")
        self.assertIsNone(hook.check(data, "codex"))
        for leak in ("부모 0a1b2c3d", *self.LEAKS):
            doc.write_text(leak + "\n", encoding="utf-8")
            self.assertIsNotNone(hook.check(data, "codex"), leak)


class CodexPath(unittest.TestCase):
    # 임시 홈, 작업 폴더, patch를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmp.name) / "work"
        self.home = Path(self.tmp.name) / "home"
        (self.home / ".claude").mkdir(parents=True)
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

    # 세션 경로 안 경로와 읽기 명령이 통과하는지 확인한다.
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
                    r"""grep -o 'a [^"\\]\{0,80\}' file.txt""", r"echo \$nestlab-kickoff", r"printf a\n", """python -c "x='\\n'" """,
                    "python -I - <<'EOF'\nprint(f\"shared/skills/{name}/SKILL.md\")\nEOF",
                    "sed -n '/^18[45][0-9]:/p' a.txt", 'grep -rn x hooks/ | grep -v "/tests/"',
                    r"""for s in a b; do grep -o 'x [^"\\]\{0,80\}' f.txt; done"""):
            self.assertIsNone(self.bash(cmd), cmd)

    # 세션 밖 경로는 이스케이프나 검색 명령 형태에서도 deny한다. 사전실측 사례는 현재 deny 판정을 고정한다.
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
            tin = {"description": "테스트 명령", **tin}
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
        self.assertEqual("이 명령은 리다이렉트로 `out.txt`에 내용을 씁니다. " + sub_write.RETRY,
                         run("Bash", {"command": "echo hi > out.txt"}))
        for name, tin in (("Bash", {"command": "cat ./x"}),
                          ("Read", {"file_path": "AGENTS.md"}), ("Read", {"file_path": f"{self.home}/.claude/CLAUDE.md"}),
                          ("Grep", {"pattern": "x"}), ("Glob", {"pattern": "*"}),
                          ("Write", {"file_path": "src/a.py", "content": "x"})):
            self.assertIsNone(run(name, tin), (name, tin))

    # Windows 8.3 짧은 이름 표기이다. 다른 OS는 그대로 쓴다.
    def short(self, path):
        if os.name != "nt":
            return str(path)
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
        return buf.value or str(path)

    # OS 임시 폴더가 두 runner에서 모두 허용되는지 확인한다.
    def test_temp_allowed_both_runners(self):
        (self.temp / "a").write_text("x", encoding="utf-8")
        for base in (str(self.temp), self.short(self.temp), str(self.temp).replace("\\", "/")):
            for runner in ("claude", "codex"):
                run = lambda cmd: self.claude("Bash", {"command": cmd}, runner)
                self.assertIsNone(run(f"cat {base}/a"), (base, runner))
                self.assertTrue((run(f"echo x > {base}/new") or "").endswith((sub_write.RETRY, sub_write.APPROVAL_RETRY)), (base, runner))
                self.assertIsNotNone(run(f"cat {base}/.env"), (base, runner))
            self.assertIsNone(self.claude("Read", {"file_path": f"{base}/a"}), base)
            self.assertIsNone(self.claude("Write", {"file_path": f"{base}/new", "content": "x"}), base)
            self.assertIsNotNone(self.claude("Read", {"file_path": f"{base}/.env"}), base)

    # 다른 cwd에서도 Nodebase 설정이 바로 적용되는지 확인한다.
    def test_nodebase_settings_apply_immediately_from_other_cwd(self):
        nodebase = Path(self.tmp.name) / "nodebase"
        nodebase.mkdir()
        self.local_settings.write_text(json.dumps({"archive_root": str(nodebase)}), encoding="utf-8")
        for runner in ("claude", "codex"):
            for name, tin in (("Read", {"file_path": str(nodebase / "AGENTS.md")}),
                              ("Grep", {"path": str(nodebase / "wiki"), "pattern": "x"}),
                              ("Glob", {"path": str(nodebase), "pattern": "**/*.md"}),
                              ("Write", {"file_path": str(nodebase / "wiki" / "new.md"), "content": "x"}),
                              ("Bash", {"command": f"cat {nodebase}/AGENTS.md"})):
                self.assertIsNone(self.claude(name, tin, runner), (name, runner))
            self.assertIsNotNone(self.claude("Read", {"file_path": str(nodebase) + "-other/a"}, runner))
            self.assertIsNotNone(self.claude("Read", {"file_path": str(nodebase / ".." / "other")}, runner))
            self.assertIsNotNone(self.claude("Read", {"file_path": str(nodebase / ".env")}, runner))
            self.assertIsNotNone(self.claude("Write", {"file_path": str(nodebase / "secrets" / "a")}, runner))
        self.local_settings.write_text("{}", encoding="utf-8")
        self.assertIsNotNone(self.claude("Read", {"file_path": str(nodebase / "AGENTS.md")}))
        for content in ("invalid", "[]", '{"archive_root": "../nodebase"}',
                        '{"archive_root": 42}', '{"archive_root": null}',
                        json.dumps({"archive_root": str(nodebase / "missing")}),
                        json.dumps({"archive_root": nodebase.anchor})):
            self.local_settings.write_text(content, encoding="utf-8")
            self.assertIsNotNone(self.claude("Read", {"file_path": str(nodebase / "AGENTS.md")}), content)
        self.local_settings.unlink()
        self.assertIsNotNone(self.claude("Read", {"file_path": str(nodebase / "AGENTS.md")}))

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
    def test_short_and_long_nodebase_and_settings_paths_are_equivalent(self):
        nodebase = Path(self.tmp.name) / "nodebase-long-directory-name"
        nodebase.mkdir()
        for setting_root in (str(nodebase), self.short(nodebase)):
            self.local_settings.write_text(json.dumps({"archive_root": setting_root}), encoding="utf-8")
            for base in (str(nodebase), self.short(nodebase)):
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
        link.parent.mkdir(parents=True)
        self.directory_link(link, self.wiki_skill.parent)
        for runner in ("claude", "codex"):
            self.assertIsNone(self.claude("Read", {"file_path": str(link / "SKILL.md")}, runner))
            self.assertIsNone(self.claude("Bash", {"command": f"cat {link}/SKILL.md"}, runner))
            self.assertIsNotNone(self.claude("Write", {"file_path": str(link / "SKILL.md")}, runner))
            self.assertIsNone(self.claude("Read", {"file_path": str(link / "other.md")}, runner))

    # Nodebase 밖으로 나가는 링크와 보안 문서 대상이 deny되는지 확인한다.
    def test_nodebase_symlink_escape_and_secret_target_are_denied(self):
        nodebase = Path(self.tmp.name) / "nodebase"
        nodebase.mkdir()
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        self.local_settings.write_text(json.dumps({"archive_root": str(nodebase)}), encoding="utf-8")
        (nodebase / "secrets").mkdir()
        self.directory_link(nodebase / "escape", outside)
        self.directory_link(nodebase / "secret-link", nodebase / "secrets")
        for runner in ("claude", "codex"):
            for name, tin in (("Read", {"file_path": str(nodebase / "escape" / "a")}),
                              ("Write", {"file_path": str(nodebase / "escape" / "a"), "content": "x"}),
                              ("Grep", {"path": str(nodebase / "escape"), "pattern": "x"}),
                              ("Glob", {"path": str(nodebase / "escape"), "pattern": "*"}),
                              ("Bash", {"command": f"cat {nodebase}/escape/a"}),
                              ("Read", {"file_path": str(nodebase / "secret-link" / "a")})):
                self.assertIsNotNone(self.claude(name, tin, runner), (name, runner))
            self.assertIsNotNone(self.claude("Read", {"file_path": self.short(nodebase) + "/escape/a"}, runner))


# 서브에이전트 입력 형식
STATE_IN = "상태 문서: log/task/20261006-0555-x/status.md\n절: 요구사항 목록"
RESEARCH_IN = "작업 종류: 조사\n조사 문서: log/task/20261006-0555-x/research.md"
INPUT_IN = "명세 문서: log/task/20261006-0555-x/spec.md"
REFUTE = "작업 종류: 반증\n반증 문서: log/task/20261006-0555-x/refute.md"
OLD_REFUTE = "작업 종류: 반증\n주장: 1. 변경이 요구를 충족한다.\n증거: 코드 발췌\n출처: hooks/a.py:12\n판정 기준: 요구 충족\n원문 발췌: 확인할 원문"


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
    """작업 문서 쓰기와 서브에이전트 호출에서 사건 문서 경로와 세션 기록 정보를 막는다."""
    transcript, write = WriteScope.transcript, WriteScope.write

    # 임시 저장소 보조 상태를 준비한다.
    def setUp(self):
        WriteScope.setUp(self)

    # 상위 정리를 호출한다.
    def tearDown(self):
        WriteScope.tearDown(self)

    # 조사 문서를 만들고 reviewer 반증 호출 검사를 실행한다.
    def refute(self, research_text):
        folder = self.root / "log/task/t"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "refute.md").write_text(research_text, encoding="utf-8")
        data = claude("ricky", "작업 종류: 반증\n반증 문서: log/task/t/refute.md")
        return sub_input.check_input({**data, "cwd": str(self.root)}, "claude")

    # 작업 문서 쓰기에서 사건 문서 파일 경로와 세션 기록 정보는 deny되고 폴더 이름만 쓴 줄은 통과하는지 확인한다.
    def test_log_doc_write(self):
        self.assertIsNotNone(self.write("log/state/x.md", "- 근거: log/incident/20261026-ab12cd34.md"))
        self.assertIsNotNone(self.write("log/state/x.md", "- 근거: 기록 a.jsonl"))
        self.assertIsNone(self.write("log/state/x.md", "- 근거: log/incident 폴더, 검증/확인, chat_state/a.json"))
        self.assertIsNone(self.write("README.md", "- 근거: log/incident/a.md"))

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
        for d in (self.project / "hooks", self.project / "log" / "incident", self.home / ".claude", self.temp):
            d.mkdir(parents=True)
        self.patches = [patch.object(sub_path, "HOME", self.home), patch.object(sub_path, "TEMP", self.temp),
                        patch.object(sub_path, "GLOBAL", (self.home / ".claude", self.home / ".codex")),
                        patch.object(sub_path, "INCIDENT", self.project / "log" / "incident"),
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

    # 서브에이전트가 사건 문서 폴더, 홈 설정 폴더, 임시 폴더를 가리키면 deny되는지 확인한다.
    def test_sub_blocked(self):
        for sub in ("claude", "codex"):
            self.assertIsNotNone(self.check("Read", {"file_path": str(self.project / "log/incident/a.md")}, sub), sub)
            self.assertIsNotNone(self.check("Read", {"file_path": str(self.home / ".claude/a.md")}, sub), sub)
            self.assertIsNotNone(self.check("Bash", {"command": f"cat {self.temp}/a.txt"}, sub), sub)

    # 서브에이전트의 저장소 안 경로와 메인 세션의 홈 설정 폴더 경로는 통과하는지 확인한다.
    def test_sub_inside_and_main_unchanged(self):
        for sub in ("claude", "codex"):
            self.assertIsNone(self.check("Read", {"file_path": str(self.project / "hooks/a.py")}, sub), sub)
        self.assertIsNone(self.check("Read", {"file_path": str(self.home / ".claude/a.md")}, None))

    # 서브에이전트가 역할별 허용 작업 문서만 읽고 반증 reviewer는 Bash를 쓰지 못하는지 확인한다.
    def test_sub_task_doc_access_by_role(self):
        # 역할별 (읽을 수 있는 문서, 읽을 수 없는 문서)이다.
        cases = {"nico": ("research", ("status", "spec", "refute")),
                 "jelly": ("spec", ("status", "research", "refute")),
                 "pepper": ("spec", ("status", "research", "refute")),
                 "ricky": ("spec", ("status", "research", "refute"))}
        read = lambda role, name: self.check_as(role, "Read", {"file_path": str(self.project / "log/task/t" / f"{name}.md")})
        for role, (ok, denied) in cases.items():
            self.assertIsNone(read(role, ok), role)
            for name in denied:
                self.assertIn("작업 문서만 읽을 수 있습니다", read(role, name) or "", f"{role} {name}")

        # 반증 reviewer는 반증 문서만 읽고 Bash는 막힌다.
        refute = Path(self.tmp.name) / "refute-agent.jsonl"
        refute.write_text(claude_line("user", REFUTE) + "\n", encoding="utf-8")
        extra = {"agent_transcript_path": str(refute)}
        path = lambda name: {"file_path": str(self.project / "log/task/t" / f"{name}.md")}
        self.assertIsNone(self.check_as("ricky", "Read", path("refute"), **extra))
        self.assertIn("작업 문서만", self.check_as("ricky", "Read", path("spec"), **extra) or "")
        self.assertIn("Bash", self.check_as("ricky", "Bash", {"command": "ls"}, **extra) or "")

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

    # worker와 reviewer가 입력 문서 경로만 받는지 확인한다.
    def test_worker_reviewer_take_input_doc_only(self):
        for target in ("jelly", "worker", "ricky", "reviewer"):
            self.assertIsNotNone(self.check(claude(target, STATE_IN), "claude"))
            self.assertIsNone(self.check(claude(target, INPUT_IN), "claude"))

    # researcher가 조사 문서 경로만 받는지 확인한다.
    def test_researcher_takes_research_doc_only(self):
        self.assertIsNotNone(self.check(claude("researcher", INPUT_IN), "claude"))
        self.assertIsNotNone(self.check(claude("researcher", STATE_IN), "claude"))
        self.assertIsNotNone(self.check(claude("researcher", RESEARCH_IN + "\n추가 설명"), "claude"))
        self.assertIsNone(self.check(claude("researcher", RESEARCH_IN), "claude"))

    # researcher 입력 차단 사유가 실제 허용 형식(작업 종류, 조사 문서, 항목 줄)을 안내하고 그 형식은 통과하는지 확인한다.
    def test_researcher_reason_matches_allowed_format(self):
        reason = self.check(claude("researcher", INPUT_IN), "claude")
        for word in ("작업 종류", "조사 문서", "항목"):
            self.assertIn(word, reason)
        self.assertIsNone(self.check(claude("researcher", RESEARCH_IN + "\n항목: 조사 1. 도구"), "claude"))

    # documenter가 작업 종류와 입력 문서 경로만 받는지 확인한다.
    def test_documenter_takes_kind_and_input_doc_only(self):
        kind = "작업 종류: 문서 작성\n"
        self.assertIsNone(self.check(claude("pepper", kind + INPUT_IN), "claude"))
        self.assertIsNone(self.check(claude("documenter", INPUT_IN), "claude"))
        for bad in ("기록 문구", STATE_IN, kind + "상태 문서: log/state/a.md", kind + INPUT_IN + "\n절: 요구사항",
                    kind + INPUT_IN + "\n항목: 1", kind + INPUT_IN + "\n" + INPUT_IN):
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
                    good = INPUT_IN
                    self.assertIsNone(self.check(make(target, good), runner))
                    self.assertIsNotNone(self.check(make(target, "자유 형식 입력"), runner))
            for target in sub_call.RESEARCHER:
                self.assertIsNone(self.check(make(target, RESEARCH_IN), runner))
                self.assertIsNotNone(self.check(make(target, "자유 형식 입력"), runner))

    # reviewer 반증 요청이 정해진 두 줄 형식만 허용하는지 확인한다.
    def test_reviewer_explicit_refutation_mode(self):
        invalid = (REFUTE + "\n작업 종류: 반증", REFUTE.replace("반증", "검증", 1),
                   REFUTE + "\n" + INPUT_IN, REFUTE + "\n" + STATE_IN,
                   REFUTE + "\n전체 대화: 이전 대화", REFUTE + "\n도구 출력: 로그",
                   REFUTE + "\n중간 설명: 작업 설명", REFUTE + "\n추론: 사고 과정",
                   REFUTE + "\n이전 대화 요약", REFUTE.replace("작업 종류: 반증\n", ""),
                   "작업 종류: 반증", "작업 종류: 반증\n주장: ", OLD_REFUTE,
                   REFUTE.replace("반증 문서", "상태 문서").replace("refute", "status"))
        for runner, make in (("claude", claude), ("codex", codex)):
            for target in sub_call.REVIEWER:
                with self.subTest(runner=runner, target=target):
                    self.assertIsNone(self.check(make(target, REFUTE), runner))
                    self.assertIsNone(self.check(make(target, INPUT_IN), runner))
                    for prompt in invalid:
                        self.assertIsNotNone(self.check(make(target, prompt), runner), prompt)
        self.assertIsNone(self.check(claude("ricky", REFUTE, sub=True), "claude"))
        self.assertIsNotNone(self.check(claude("ricky", OLD_REFUTE, sub=True), "claude"))
        self.assertIsNotNone(self.check(claude("ricky", REFUTE + "\n전체 대화: 로그", sub=True), "claude"))

    # researcher 안의 하위 호출이 모두 deny되는지 확인한다.
    def test_researcher_cannot_call_any_agent(self):
        for target, prompt in (("ricky", REFUTE), ("reviewer", INPUT_IN), ("jelly", INPUT_IN), ("pepper", INPUT_IN)):
            with self.subTest(target=target):
                data = {**claude(target, prompt, sub=True), "agent_type": "researcher"}
                self.assertIsNotNone(self.check(data, "claude"))

    # 일반 검증과 구현 요청이 경로 한 줄만 허용하는지 확인한다.
    def test_general_review_and_worker_allow_path_only(self):
        for runner, make in (("claude", claude), ("codex", codex)):
            for target in sub_call.WORKER_REVIEWER:
                for prompt in (INPUT_IN + "\n절: 검증", INPUT_IN + "\n항목: 1", REFUTE if target not in sub_call.REVIEWER else STATE_IN):
                    self.assertIsNotNone(self.check(make(target, prompt), runner))

    # 형식이 틀린 입력의 사유가 두 줄 형식을 안내하고, 그대로 쓴 입력은 통과하는지 확인한다.
    def test_worker_format_reason_matches_allowed_format(self):
        for runner, make in (("claude", claude), ("codex", codex)):
            self.assertEqual(self.check(make("jelly", "구현\n" + INPUT_IN.split("\n")[-1]), runner),
                             sub_input.REASON_WORKER_FORMAT)
            self.assertIsNone(self.check(make("jelly", "작업 종류: 구현\n" + INPUT_IN), runner))


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
                        self.assertEqual(bool(output), denied or target in sub_call.ROLES)
                        if denied:
                            self.assertEqual(json.loads(output)["hookSpecificOutput"]["permissionDecision"], "deny")

    # director 호출에 승인과 입력 형식이 모두 필요한지 확인한다.
    def test_director_call_needs_approval_and_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            for user, prompt, denied in (("승인", INPUT_IN, False), ("승인", "자유 입력", True),
                                         ("다시 생각해", INPUT_IN, True)):
                with self.subTest(user=user, prompt=prompt):
                    path.write_text("\n".join(claude_line(r, t) for r, t in (("assistant", "계획"), ("user", user))) + "\n",
                                    encoding="utf-8")
                    data = {**claude("jelly", prompt), "agent_type": "rio", "transcript_path": str(path)}
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
        out = run_utf8({**claude("jelly", "이전 대화 요약"), "agent_type": "buddy"}, "--runner", "claude")
        self.assertIn("명세 문서", json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"])
        out = run_utf8({**claude("jelly", INPUT_IN), "agent_type": "buddy"}, "--runner", "claude")
        self.assertIn("작업 종류", json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"])


class ApprovalGate(unittest.TestCase):
    """승인 전과 승인 턴의 메인 세션 Bash 판정, 스크립트 파일 쓰기, cd, 보안 파일 문자열, 패치 파일 구간을 검사한다."""
    setUp, tearDown, transcript, write = WriteScope.setUp, WriteScope.tearDown, WriteScope.transcript, WriteScope.write

    # 승인 여부와 도구 이름을 바꿔 director 메인 세션 셸 입력을 검사하는 보조 함수이다.
    def shell(self, cmd, approved=False, tool="Bash", runner="claude", **extra):
        user = "승인" if approved else "다시 생각해"
        tin = {"command": cmd, "description": "테스트 명령", **extra.pop("tin", {})}
        return hook.check({"tool_name": tool, "tool_input": tin, "agent_type": "rio", "cwd": str(self.root),
                           "transcript_path": self.transcript(("assistant", "계획"), ("user", user)), **extra}, runner)

    # 승인 전 메인 세션은 git 쓰기, 쓰기 호출이 있는 인라인 코드, 허용 목록 밖 스크립트, 세션 밖 경로를 deny한다.
    def test_approval_turn_before_denies(self):
        for cmd in ("git commit -m x", "git push", "git add a.py", "python -c \"open('a.txt','w').write('x')\"",
                    "python tools/run_workflow.py", "python -m pytest", "cat /etc/hosts"):
            self.assertTrue((self.shell(cmd) or "").endswith((sub_write.RETRY, sub_write.APPROVAL_RETRY)), cmd)

    # 승인 필요 차단 사유 7개가 APPROVAL_RETRY로 끝나고 RETRY를 포함하지 않으며, 일반 차단은 RETRY를 유지한다.
    def test_approval_reasons_use_approval_retry(self):
        for reason in (sub_scope.REASON_NO_APPROVAL, sub_scope.REASON_OUTSIDE_LOG, sub_scope.REASON_SCRIPT_FILE, hook.REASON_SCRIPT,
                       sub_write.REASON_GIT_WRITE, sub_approval.REASON_APPROVAL):
            self.assertIn("nestlab-approval", reason)
            self.assertNotIn("권한 일시 우회", reason)
        self.assertTrue(sub_scope.REASON_NO_APPROVAL.endswith(sub_write.APPROVAL_RETRY))
        self.assertTrue(sub_write.REASON_INLINE.endswith(sub_write.RETRY))

    # 승인 전 메인 세션도 읽기, 쓰기 호출 없는 인라인 코드, heredoc, 셸 변수, 허용 목록 명령은 통과한다.
    def test_approval_turn_before_allows(self):
        for cmd in ('python -I -X utf8 -c "print(1)"', "cat <<'EOF'\na\nEOF", 'D=/x; ls "$D"', "git status",
                    "python -I - <<'EOF'\nif 2 > 1:\n    print(1)\nEOF",
                    "python -m unittest discover -s hooks/tests 2>&1 | tail -20"):
            self.assertIsNone(self.shell(cmd), cmd)

    # 승인 전에도 저장소 안 git -C와 셸 변수 앞붙임 명령은 통과하고, git -c와 python 스크립트 실행은 deny한다.
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
        for cmd in ("cp a.txt b.txt", f"cp ~/.claude/settings.json {temp.as_posix()}"):
            self.assertIsNone(self.shell(cmd, approved=True), cmd)
            self.assertTrue(self.shell(cmd))  # 승인 전 director의 log/ 밖 쓰기는 deny
        self.assertEqual(self.shell("git -c core.pager=cat log", approved=True), sub_write.REASON_GIT_C)
        self.assertIn("/outside", self.shell("git -C /outside log -1", approved=True) or "")

    # 메인 세션이 승인 전에 log/와 OS 임시 폴더에 스크립트 확장자 파일을 쓰면 deny하고 문서 쓰기는 통과한다.
    def test_script_file_write(self):
        no = self.transcript(("assistant", "계획"), ("user", "다시 생각해"))
        temp = Path(self.ostemp.name).resolve() / "tmp_parse.py"
        self.assertEqual(self.write("log/tmp_parse.py", "x", role="rio", transcript=no), sub_scope.REASON_SCRIPT_FILE)
        self.assertEqual(self.write(str(temp), "x", role="rio", transcript=no), sub_scope.REASON_SCRIPT_FILE)
        self.assertNotEqual(self.write("log/task/x/status.md", "x", role="rio", transcript=no), sub_scope.REASON_SCRIPT_FILE)
        self.assertIsNone(self.write("log/tmp_parse.py", "x", role="rio"))  # 승인 턴

    # Claude와 Codex 셸의 cd류 명령은 승인 턴에도, heredoc이 섞여도 REASON_CD로 deny한다.
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

    # 인라인 코드와 heredoc 본문에 보안 파일 패턴이 있으면 승인 턴에도 deny하고 무관한 명령은 통과한다.
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
            text = chr(10).join(["*** Begin Patch", f"*** Add File: {self.root / 'log/state/n.md'}", "+근거 출처: 로그 시각",
                                 f"*** Add File: {self.root / 'log/state/n-research.md'}", f"+{research_line}",
                                 "*** End Patch"])
            data = {"tool_name": "apply_patch", "tool_input": {"command": text}, "agent_type": "rio", "cwd": str(self.root),
                    "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인"))}
            return hook.check(data, "codex") or ""
        self.assertIn("출처", patched("출처: 사용자 제공"))
        self.assertNotIn("출처가 URL", patched("- 제외 범위: 없음"))


class CodexTaskName(unittest.TestCase):
    """Codex spawn_agent의 암호문 message는 task_name으로 요청을 복원해 검사한다. (요구사항 13)"""
    CIPHER = "gAAAAABabcdefghij-_klmn=="
    NAME = "implement__20261024_120000_test"

    # 임시 저장소에 완전한 작업 문서 네 개를 준비한다.
    def setUp(self):
        from hooks.tests.test_sub_workflow import PLAN, REFUTE as REFUTE_DOC, RESEARCH as RESEARCH_DOC, STATE, TASK
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / "log" / "task" / TASK
        self.folder.mkdir(parents=True)
        self.state = STATE
        for kind, body in (("status", STATE), ("spec", PLAN), ("research", RESEARCH_DOC), ("refute", REFUTE_DOC)):
            (self.folder / f"{kind}.md").write_text(body, encoding="utf-8")
        patcher = patch.object(sub_docs, "project_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    # Codex spawn_agent 입력을 만든다.
    def call(self, message=CIPHER, **tin):
        return {"tool_name": "spawn_agent", "agent_type": "assistant", "cwd": str(self.root),
                "tool_input": {"agent_type": "worker", "message": message, "fork_turns": "none", **tin}}

    # 형식에 맞지 않는 task_name은 사유와 함께 막고, 형식에 맞는 task_name이나 평문 message는 입력 검사를 통과한다.
    def test_task_name_format(self):
        for name in ("implementation", None, "implement__2026_063721_x"):
            with self.subTest(name=name):
                data = self.call(**({} if name is None else {"task_name": name}))
                self.assertEqual(hook.check(data, "codex"), sub_input.REASON_CIPHER)
        self.assertIsNone(sub_input.check_input(self.call(task_name=self.NAME), "codex"))
        self.assertIsNone(sub_input.check_input(self.call(message=INPUT_IN, task_name="implementation"), "codex"))

    # 형식에 맞는 task_name은 단계 선행 조건 검사를 받아, 실행 승인 기록이 있으면 통과하고 승인이 없으면 막는다.
    def test_task_name_stage_gate(self):
        self.assertIsNone(hook.check(self.call(task_name=self.NAME), "codex"))
        (self.folder / "status.md").write_text(
            self.state.replace("사용자 승인 상태: 승인", "사용자 승인 상태: 대기"), encoding="utf-8")
        self.assertIn("승인 상태가 승인이 아닙니다", hook.check(self.call(task_name=self.NAME), "codex") or "")


REPORT_DOC = """# 조사 문서: t

- 확인 상태: 확인

## 조사 계획

- 제외 범위: 없음

## 검토 보고

### 검토 3. X

| **안** | **기준** |
| :----- | :------- |
| A | 충족 |
| B | 미충족 |

#### 안 1. A

- 적용할 작업: a

#### 안 2. B

- 적용할 작업: b
"""
REVIEW_STATE = """# t

- 작업 상태: 진행

### 검토 필요

- 검토 3. X: A를 정한다.
  - 상태: {status}
  - 확정: A
  - 근거 위치: 조사 문서 > 검토 보고 > 검토 3. X > 안 1. A
"""


class QuestionGuards(unittest.TestCase):
    """번호 접두 질문의 형식, 확정 라벨, 검토 보고, 구현 질문 시점을 PreToolUse에서 검사한다. (요구사항 2, 4, 7, 8)"""

    # 임시 저장소와 경과 문서를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        status = self.root / "log" / "task" / "t" / "status.md"
        status.parent.mkdir(parents=True)
        status.write_text("# t\n", encoding="utf-8")
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
            patch_text = "*** Begin Patch\n*** Update File: log/task/t/status.md\n@@\n-x\n+y\n*** End Patch"
            item = lambda p: {"type": "response_item", "payload": p}
            lines = [item({"type": "message", "role": "user", "content": [{"text": "시작"}]})]
            if written:
                lines.append(item({"type": "custom_tool_call", "name": "apply_patch", "call_id": "c1", "input": patch_text}))
        path = self.root / f"{runner}-{written}.jsonl"
        path.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n", encoding="utf-8")
        return str(path)

    # 질문 문장과 라벨 목록으로 질문 도구 입력을 만들어 검사한다. 이름은 runner별 질문 도구이다.
    def ask(self, labels, question="요구사항 1. 이름: 결과 문장", runner="claude", written=True, tool=None):
        tool = tool or ("AskUserQuestion" if runner == "claude" else "request_user_input")
        body = {"id": "q1", "header": "결정", "question": question,
                "options": [{"label": l, "description": "설명"} for l in labels]}
        data = {"tool_name": tool, "tool_input": {"questions": [body]}, "cwd": str(self.root),
                "transcript_path": self.transcript(runner, written)}
        return hook.check(data, runner)

    # 형식을 어긴 번호 접두 질문과 물음표 끝 질문은 막고, 형식을 지킨 질문과 접두 없는 질문은 통과한다.
    def test_prefixed_format(self):
        self.research.write_text(REPORT_DOC, encoding="utf-8")
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                for question in ("검토 3. X: 언제 기록할까요?", "조사 1. Codex 입력: 안 2를 채택한다.", "요구사항 1 이름 없는 문장",
                                 "구현 1. 요약", "요구사항 1. 이름: 결과 문장?"):
                    self.assertIsNotNone(self.ask(["A", "B"], question, runner), question)
                self.assertIsNone(self.ask(["안 1. A", "안 2. B"], "검토 3. X: 기록 방식을 정한다.", runner))
                self.assertIsNone(self.ask(["확정", "보류"], "요구사항 1. 이름: 결과 문장", runner))
                self.status.write_text(REVIEW_STATE.format(status="검토 완료"), encoding="utf-8")
                self.assertEqual(self.ask(["확정", "보류"], "구현 1. 요약 (요구사항 1)", runner), sub_question.REASON_IMPLEMENTATION)
                self.assertIsNone(self.ask(["확정", "보류"], "요구사항 1 폐기: 사유", runner))
                self.assertIsNone(self.ask(["A", "B"], "어느 쪽으로 할까요?", runner))
        self.assertEqual(self.ask(["A"], "조사 1. Codex 입력: 안 2를 채택한다."), sub_question.REASON_RESEARCH)

    # 요구사항 질문에는 확정 라벨을 요구하고 검토 질문에는 안 번호 라벨을 쓴다.
    def test_confirm_label_required(self):
        self.research.write_text(REPORT_DOC, encoding="utf-8")
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.assertEqual(self.ask(["허용 (Recommended)", "현행 유지"], runner=runner), sub_question.REASON_NO_CONFIRM)
                self.assertIsNone(self.ask(["확정 (Recommended)", "보류"], runner=runner))
                self.assertIsNone(self.ask(["A", "B"], runner=runner, written=False))
                self.assertIsNone(self.ask(["안 1. A", "안 2. B"], "검토 3. X: 기록 방식을 정한다.", runner))

    # 라벨이 `수정`인 선택지는 막고, 다른 라벨 조합은 통과한다.
    def test_edit_label_denied(self):
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.assertEqual(self.ask(["확정", "수정"], runner=runner), sub_question.REASON_EDIT_LABEL)
                self.assertIsNone(self.ask(["확정", "보류"], runner=runner))

    # 경과 문서를 쓴 Codex 세션의 request_user_input_async는 막고, 쓰지 않은 세션은 통과한다.
    def test_async_question_denied(self):
        self.assertEqual(self.ask(["확정"], "어느 안으로 할까요?", "codex", tool="request_user_input_async"), hook.REASON_ASYNC)
        self.assertIsNone(self.ask(["확정"], "어느 안으로 할까요?", "codex", written=False, tool="request_user_input_async"))

    # 비교표 데이터 행이 하나인 검토는 `폐기`, `재조사` 라벨을 행 없이 허용하고, 비교표에 없는 다른 라벨은 막는다.
    def test_single_row_review_extra_labels(self):
        one = REPORT_DOC.replace("| B | 미충족 |\n", "").replace("#### 안 2. B\n\n- 적용할 작업: b\n", "")
        self.research.write_text(one, encoding="utf-8")
        question = "검토 3. X: 기록 방식을 정한다."
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                self.assertIsNone(self.ask(["안 1. A", "폐기", "재조사"], question, runner))
                self.assertIsNotNone(self.ask(["안 1. A", "수정 필요"], question, runner))
        # 행이 둘인 검토는 `폐기` 라벨도 행이 있어야 한다.
        self.research.write_text(REPORT_DOC, encoding="utf-8")
        self.assertIsNotNone(self.ask(["안 1. A", "안 2. B", "폐기"], question))

    # 검토 질문은 검토 보고 절의 비교표 행과 안 블록에 모든 라벨이 있어야 통과한다.
    def test_review_report_required(self):
        question = "검토 3. X: 기록 방식을 정한다."
        # 조사 문서가 없거나 해당 번호의 보고가 없으면 막는다.
        self.assertIn("검토 3.", self.ask(["안 1. A", "안 2. B"], question))
        self.research.write_text(REPORT_DOC.replace("### 검토 3. X", "### 검토 4. X"), encoding="utf-8")
        self.assertIn("검토 3.", self.ask(["안 1. A", "안 2. B"], question))

        # heading만 있고 표가 없으면 막는다.
        table = REPORT_DOC[REPORT_DOC.index("| **안**"):REPORT_DOC.index("#### 안 1. A")]
        self.research.write_text(REPORT_DOC.replace(table, ""), encoding="utf-8")
        self.assertIn("비교표", self.ask(["안 1. A", "안 2. B"], question))

        # 표에 라벨 하나가 없거나 안 블록이 하나 없으면 막는다.
        self.research.write_text(REPORT_DOC.replace("| B | 미충족 |\n", ""), encoding="utf-8")
        self.assertIn("행이 없습니다", self.ask(["안 1. A", "안 2. B"], question))
        self.research.write_text(REPORT_DOC.replace("#### 안 2. B", "#### 비고"), encoding="utf-8")
        self.assertIn("블록이 없습니다", self.ask(["안 1. A", "안 2. B"], question))

        # 모든 라벨이 표 행과 안 블록에 있으면 통과하고 라벨이 없는 질문은 막는다.
        self.research.write_text(REPORT_DOC, encoding="utf-8")
        for runner in ("claude", "codex"):
            self.assertIsNone(self.ask(["안 1. A", "안 2. B (Recommended)"], question, runner), runner)
        self.assertEqual(self.ask([], question), sub_question.REASON_NO_LABELS)

    # 구현 질문은 조사 확인 및 검토 상태와 관계없이 막는다.
    def test_implementation_question_timing(self):
        question = "구현 1. 요약 (요구사항 1)"
        self.research.write_text(REPORT_DOC.replace("확인 상태: 확인", "확인 상태: <확인 | 보류 | 기각>"), encoding="utf-8")
        self.status.write_text(REVIEW_STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertEqual(sub_question.REASON_IMPLEMENTATION, self.ask(["확정"], question))

        # 조사 문서 확인이 끝나도 미완료 검토나 답 없는 검토 보고가 있으면 막는다.
        self.research.write_text(REPORT_DOC, encoding="utf-8")
        self.status.write_text(REVIEW_STATE.format(status="사용자 검토 필요"), encoding="utf-8")
        self.assertEqual(sub_question.REASON_IMPLEMENTATION, self.ask(["확정"], question))
        self.status.write_text("# t\n\n- 작업 상태: 진행\n", encoding="utf-8")
        self.assertEqual(sub_question.REASON_IMPLEMENTATION, self.ask(["확정"], question))

        # 검토가 끝났거나 조사 항목이 없어도 구현 질문은 막는다.
        self.status.write_text(REVIEW_STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertEqual(sub_question.REASON_IMPLEMENTATION, self.ask(["확정"], question))
        plain = REPORT_DOC.replace("확인 상태: 확인", "확인 상태: <확인 | 보류 | 기각>").replace("- 제외 범위: 없음", "- 조사 항목 없음")
        self.research.write_text(plain, encoding="utf-8")
        self.assertEqual(sub_question.REASON_IMPLEMENTATION, self.ask(["확정"], question))


class SpecTiming(unittest.TestCase):
    """구형 명세의 검토 선행 검사와 새 명세 초안의 쓰기를 확인한다."""

    # 임시 저장소와 patch를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.folder = self.root / "log" / "task" / "t"
        self.folder.mkdir(parents=True)
        patches = [patch.object(sub_scope, "project_root", return_value=self.root),
                   patch.object(sub_docs, "project_root", return_value=self.root),
                   patch.object(sub_docs, "SKILLS", self.root / "skills"),
                   patch.object(sub_scope, "skill_called", return_value=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        log = self.root / "t.jsonl"
        log.write_text(claude_line("assistant", "보고") + "\n" + claude_line("user", "승인") + "\n", encoding="utf-8")
        self.log = str(log)
        skill = self.root / "skills" / "nestlab-log-spec"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("<!-- 모든 목록이 검토 완료 또는 폐기일 때 승인 가능 -->\n", encoding="utf-8")

    # 명세 문서 Write 입력으로 검사를 실행한다.
    def write_spec(self):
        data = {"tool_name": "Write", "tool_input": {"file_path": str(self.folder / "spec.md"), "content": "# t 입력\n"},
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
        (self.folder / "status.md").write_text(REVIEW_STATE.format(status="검토 완료"), encoding="utf-8")
        self.assertIsNone(self.write_spec())


class NoKickoffGate(unittest.TestCase):
    """director 메인 세션은 nestlab-kickoff 호출 없이도 Skill 외 도구를 호출할 수 있다."""

    # kickoff 호출이 없는 director 세션의 Bash `ls`는 통과한다.
    def test_bash_without_kickoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "c.jsonl"
            log.write_text(claude_line("user", "시작") + chr(10), encoding="utf-8")
            data = {"tool_name": "Bash", "tool_input": {"command": "ls", "description": "테스트 명령"},
                    "agent_type": "rio", "cwd": tmp, "transcript_path": str(log)}
            self.assertIsNone(hook.check(data, "claude"))


class ApplyPatchFullText(unittest.TestCase):
    """apply_patch가 Write와 같은 쓰기 후 전문으로 전이, 필수 절 검사를 받는다. (요구사항 2)"""
    setUp, tearDown, transcript, write = WriteScope.setUp, WriteScope.tearDown, WriteScope.transcript, WriteScope.write

    # 파일 한 개를 갱신하는 apply_patch 입력으로 검사를 실행한다.
    def patch_of(self, path, body):
        text = f"*** Begin Patch\n*** Update File: {path}\n{body}\n*** End Patch"
        return hook.check({"tool_name": "apply_patch", "tool_input": {"command": text}, "agent_type": "rio",
                           "cwd": str(self.root), "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인"))}, "codex")

    # added_lines가 apply_patch 입력에서 쓰기 후 전문을 반환하고, 패치 구간을 찾지 못하면 None을 반환한다.
    def test_added_lines_full_text(self):
        doc = self.root / "log" / "task" / "t" / "status.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("a\nb\n", encoding="utf-8")
        data = lambda body: {"tool_name": "apply_patch", "cwd": str(self.root), "tool_input": {
            "command": f"*** Begin Patch\n*** Update File: {doc}\n{body}\n*** End Patch"}}
        self.assertEqual(sub_write.added_lines(data("@@\n a\n+x"), doc), (["x"], "a\nx\nb\n"))
        self.assertEqual(sub_write.added_lines(data("@@\n-없음\n+x"), doc), (["x"], None))

    # 실행 승인 줄을 더하는 패치는 전이 검사를 받아 deny하고, 작업 경과 절만 고치는 패치는 통과한다.
    def test_execution_approval_transition(self):
        status = self.root / "log" / "task" / "t" / "status.md"
        status.parent.mkdir(parents=True)
        status.write_text("# t\n\n### 실행 승인 범위\n\n- 사용자 승인 상태: 승인 대기\n\n### 작업 경과\n\n- 갱신 시각: x\n", encoding="utf-8")
        gate = lambda path, new, old, plan: "전이 미달" if "승인 상태: 승인\n" in (new or "") else None
        with patch.object(sub_scope, "transition_reason", side_effect=gate):
            self.assertEqual(self.patch_of(status, "@@\n-- 사용자 승인 상태: 승인 대기\n+- 사용자 승인 상태: 승인"), "전이 미달")
            self.assertIsNone(self.patch_of(status, "@@\n-- 갱신 시각: x\n+- 갱신 시각: y"))

    # 조사 문서 필수 절을 지우는 패치는 deny한다.
    def test_required_section_removed(self):
        skill = self.root / "skills" / "nestlab-log-research"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("## Template\n\n```markdown\n# t\n\n## 조사 계획\n\n## 후보 비교\n```\n", encoding="utf-8")
        research = self.root / "log" / "task" / "t" / "research.md"
        research.parent.mkdir(parents=True)
        research.write_text("# t\n\n## 조사 계획\n\n## 후보 비교\n", encoding="utf-8")
        self.assertIn("필수 절", self.patch_of(research, "@@\n ## 조사 계획\n \n-## 후보 비교"))


class ApprovedPlanLock(unittest.TestCase):
    """실행 승인 뒤에는 명세 문서 계획과 조사 문서 검토 보고를 고칠 수 없다. (요구사항 3, 5)"""
    setUp, tearDown, transcript, write = WriteScope.setUp, WriteScope.tearDown, WriteScope.transcript, WriteScope.write

    # 실행 승인 상태를 정해 작업 폴더를 만들고 명세 문서와 조사 문서를 반환한다.
    def task(self, state="승인"):
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True, exist_ok=True)
        patcher = patch.object(sub_scope, "early_items", return_value=[])  # 검토 완료 판정은 이 테스트가 다루지 않는다.
        patcher.start()
        self.addCleanup(patcher.stop)
        (folder / "status.md").write_text(f"# t\n\n### 실행 승인 범위\n\n- 사용자 승인 상태: {state}\n", encoding="utf-8")
        (folder / "spec.md").write_text(PLAN_OK + "\n## 재작업 요청\n\n### 재작업 범위\n", encoding="utf-8")
        report = REPORT_DOC.replace("- 적용할 작업: a", "- 적용할 작업: a\n- 장점: 동작 개선 (출처: hooks/a.py:1)\n- 단점: 수정 필요 (출처: hooks/a.py:2)")
        (folder / "research.md").write_text(report, encoding="utf-8")
        return folder / "spec.md", folder / "research.md"

    # Edit 입력으로 검사를 실행한다.
    def edit(self, path, old, new):
        return hook.check({"tool_name": "Edit", "tool_input": {"file_path": str(path), "old_string": old, "new_string": new},
                           "agent_type": "rio", "cwd": str(self.root),
                           "transcript_path": self.transcript(("assistant", "보고"), ("user", "승인"))}, "claude")

    # 승인 뒤 명세 문서 계획과 조사 문서 검토 보고 수정은 deny하고 재작업 요청 추가는 통과한다. 승인 전에는 모두 통과한다.
    def test_locked_after_approval(self):
        spec, research = self.task()
        self.assertEqual(self.edit(spec, "  - `python -m unittest`: 요구사항 1", "  - `python -m pytest`: 요구사항 1"),
                         sub_scope.REASON_PLAN_LOCKED)
        self.assertEqual(self.edit(research, "- 적용할 작업: a", "- 적용할 작업: z"), sub_scope.REASON_PLAN_LOCKED)
        self.assertIsNone(self.edit(spec, "### 재작업 범위\n", "### 재작업 범위\n\n- 수정 위치: a.py:1\n"))
        spec, research = self.task("승인 대기")
        self.assertIsNone(self.edit(spec, "  - `python -m unittest`: 요구사항 1", "  - `python -m pytest`: 요구사항 1"))
        self.assertIsNone(self.edit(research, "- 적용할 작업: a", "- 적용할 작업: z"))

    # 따옴표 안 이스케이프 따옴표, `>`, 경로 형식 문자열은 판정에서 빠지고 따옴표 밖 리다이렉트는 막는다.
    def test_bash_quoted_strings(self):
        bash = lambda cmd: hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"},
                                       "agent_type": "rio", "cwd": str(self.root),
                                       "transcript_path": self.transcript(("assistant", "보고"), ("user", "다시 생각해"))}, "claude")
        regex = r'''python -I -c "re.findall(r'[^.\"]*>', l)"'''
        self.assertIsNone(bash(regex))
        self.assertIsNone(bash(r'python -I -c "print(\"/opt/x/y\")"'))
        self.assertIsNotNone(bash("echo a > out.txt"))
        self.assertFalse(sub_write.is_write_bash(regex))
        self.assertTrue(sub_write.is_write_bash("echo a > out.txt"))

if __name__ == "__main__":
    unittest.main()
