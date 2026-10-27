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
import sub_plan

RESEARCH = """# 조사 문서: t

## 조사 계획

### 검토 1. 도구 선택

## 조사·반증 루프

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
                        patch("sub_workflow.plan_gaps", side_effect=sub_plan.plan_gaps),
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
                    'git commit -m "x; y > z"', "sed -n '1,5p' f"):
            self.assertIsNone(bash(cmd), cmd)
        # Bash 내용 쓰기는 승인과 무관하게 전용 도구를 안내하며 막는다.
        approved = self.transcript(("assistant", "계획"), ("user", "승인"), name="yes.jsonl")
        for cmd in ("echo a > f.txt", "echo x > out.txt", "cmd >> f", "Set-Content f x", "git status && echo x > a",
                    "python -c \"open('a','w')\"", "node -e 'x'", "bash -c 'echo hi > f'", "ls | tee out.txt",
                    "cat > notes.md <<'EOF'\nhi\nEOF"):
            guide = "인라인 코드" if cmd.startswith(("python", "node")) else "다른 쓰기 도구"
            self.assertIn(guide, bash(cmd) or "", cmd)
            if guide == "인라인 코드":
                self.assertIsNone(bash(cmd, approved), cmd)  # 승인 턴에는 인라인 코드를 막지 않는다.
            else:
                self.assertIn(guide, bash(cmd, approved) or "", cmd)
            self.assertIn("다른 쓰기 도구를 선택하여 작업하십시오", bash(cmd) or "", cmd)
            self.assertNotIn("파일로 만들어", bash(cmd) or "", cmd)
            self.assertNotIn("·", bash(cmd) or "", cmd)
            if guide == "인라인 코드":
                self.assertNotIn("리다이렉트", bash(cmd))
        # 리다이렉트 사유에는 따옴표를 벗긴 실제 대상 경로가 나온다.
        self.assertIn("이 명령은 리다이렉트로 `C:/tmp/a b.txt`에 내용을 씁니다.", bash('echo x > "C:/tmp/a b.txt"') or "")

    # 승인 창을 띄우는 셸 변수, 반복문, 임시 폴더 경로 명령은 deny하고 환경 변수 접두와 작은따옴표 안 $는 통과한다.
    def test_bash_prompt_commands_denied(self):
        def bash(cmd):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"},
                               "agent_type": "rio"}, "claude")
        temp = str(Path(self.ostemp.name).resolve() / "hooks_doc.md").replace("\\", "/")
        for cmd, text in (('D="docs"; sed -n 1,5p "$D/a.md"', "셸 변수나 명령 치환을 씁니다"),
                          ("ls $(pwd)", "셸 변수나 명령 치환을 씁니다"),
                          ("for f in a b; do echo f; done", "반복문을 씁니다"), (f"sed -n 1,5p {temp}", "임시 폴더")):
            self.assertIn(text, bash(cmd) or "", cmd)
        for cmd in ("TZ=KST-9 date '+%H:%M'", "grep -n '$HOME' a.md"):
            self.assertIsNone(bash(cmd), cmd)
        # 스크립트 실행은 임시 폴더 경로여도 deny하지 않고 승인 창 규칙에 맡긴다.
        self.assertIsNone(bash(f"python {temp.replace('.md', '.py')}"))

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

    # 입력 문서 계획이 채워져야 실행 승인 기록이 통과하는지 확인한다.
    def test_execution_approval_needs_filled_plan(self):
        self.assertIsNotNone(self.write("log/state/t.md", STATE_APPROVED))  # 입력 문서 없음
        plan = self.state / "t-input.md"
        plan.write_text(PLAN_OK, encoding="utf-8")
        self.assertIsNone(self.write("log/state/t.md", STATE_APPROVED))
        for broken in (PLAN_OK.replace("`python -m unittest`", "`<명령>`"),
                       PLAN_OK.replace("- 기대 결과:\n  - e\n", ""),
                       PLAN_OK.replace("목표:\n  - a", "목표:\n  - <요구사항의 목표>"),
                       PLAN_OK.replace("### 1. 예시", ""),
                       PLAN_OK.replace("- 한계:", "- 제약:"),
                       PLAN_OK.replace("| 허용 | 정상 문서 | `python -m unittest` | 통과 |\n", ""),
                       PLAN_OK.replace("| 차단 | 누락 문서 | `python -m unittest` | deny |", "| 차단 | 누락 문서 |  | deny |")):
            plan.write_text(broken, encoding="utf-8")
            self.assertIsNotNone(self.write("log/state/t.md", STATE_APPROVED))
        commented = PLAN_OK.replace("### 검증 계획", "<!-- 안내 -->\n\n### 검증 계획").replace("목표:\n  - a", "목표:\n  - `<타입>` 형식 확인")
        plan.write_text(commented, encoding="utf-8")
        self.assertIsNone(self.write("log/state/t.md", STATE_APPROVED))
        self.assertIsNone(self.write("log/state/t-input.md", "# 입력 문서 쓰기는 계획 검사 대상이 아니다"))

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

    # 새 조사 문서는 빈 원문 칸일 때만 허용되는지 확인한다.
    def test_new_research_doc_allows_empty_raw_only(self):
        self.assertIsNone(self.write("log/state/n-research.md", RESEARCH))
        self.assertIsNotNone(self.write("log/state/n-research.md", RESEARCH.replace("기록 없음", "가짜 결과")))

    # 조사 결과 없이 조사 문서 검토를 근거로 쓰는 것이 deny되는지 확인한다.
    def test_decision_needs_research_result(self):
        (self.state / "t-research.md").write_text(RESEARCH, encoding="utf-8")
        user = "### 검토 필요\n\n- Windows만 대상\n  - 대응 근거: 사용자 메시지 시각 2026-10-22 22:00\n"
        self.assertIsNone(self.write("log/state/t.md", user))
        doc = "### 검토 필요\n\n- A 사용\n  - 대응 근거: 조사 문서 검토 1\n"
        self.assertIsNotNone(self.write("log/state/t.md", doc))
        (self.state / "t-research.md").write_text(RESEARCH.replace("기록 없음", "검토 1 제안: A"), encoding="utf-8")
        self.assertIsNone(self.write("log/state/t.md", doc))

    # 스킬 원본 템플릿 주석과 진행 중인 작업 경과 문서를 준비한다. status는 검토 필요 항목의 상태이고 approved는 실행 승인 여부이다.
    def running_task(self, status, approved=True):
        skill = self.root / "skills" / "nestlab-log-state"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("<!-- 모든 목록이 검토 완료 또는 사후실측 필요일 때 승인 가능 -->\n", encoding="utf-8")
        folder = self.root / "log" / "task" / "t"
        folder.mkdir(parents=True, exist_ok=True)
        approval = "승인" if approved else "승인 대기"
        (folder / "status.md").write_text(
            f"# t\n\n- 작업 상태: 진행\n\n## 실행 승인 범위\n\n- 사용자 승인 상태: {approval}\n\n"
            f"### 검토 필요\n\n- A\n  - 상태: {status}\n", encoding="utf-8")
        return folder / "status.md"

    # 실행 승인 뒤 허용 값이 아닌 검토 필요 항목이 있으면 log 밖 쓰기를 막고 상태 문서 쓰기는 통과한다.
    def test_review_left_blocks_outside_write_after_approval(self):
        path = self.running_task("미검토")
        self.assertIn("검토", self.write("hooks/a.py", "x") or "")
        self.assertIsNone(self.write("log/task/t/status.md", path.read_text(encoding="utf-8")))
        for status in ("사후검증 필요", "검토 완료(일부)"):
            self.running_task(status)
            self.assertIsNotNone(self.write("hooks/a.py", "x"), status)
        for status in ("검토 완료", "사후실측 필요"):
            self.running_task(status)
            self.assertIsNone(self.write("hooks/a.py", "x"), status)
        self.running_task("미검토", approved=False)
        self.assertIsNone(self.write("hooks/a.py", "x"))

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
        (self.root / "skills" / "nestlab-log-state" / "SKILL.md").write_text("# 주석 없음\n", encoding="utf-8")
        self.assertIn("주석이 없습니다", self.write("hooks/a.py", "x") or "")

    # 메인 세션 Bash는 쓰기 명령을 승인과 관계없이 막고 다른 쓰기 도구를 안내하며 읽기 명령은 통과한다.
    def test_main_session_bash_denies_writes(self):
        def bash(cmd):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"},
                               "agent_type": "rio", "cwd": str(self.root),
                               "transcript_path": self.transcript(("assistant", "계획"), ("user", "승인"))}, "claude")
        for cmd in ("sed -i s/a/b/ a.py", "echo x > a.txt", "rm a.txt"):
            reason = bash(cmd) or ""
            self.assertIn("다른 쓰기 도구를 선택하여 작업하십시오", reason, cmd)
            self.assertNotIn("파일로 만들어", reason, cmd)
        self.assertIsNone(bash("git status"))
        self.assertIsNone(bash("grep -n def hooks/common/sub_write.py"))
        # 승인 턴에는 인라인 코드와 승인 창 형태 명령을 hook이 막지 않는다.
        for cmd in ('python -c "print(1)"', 'D=/x; ls "$D"', "for f in a b; do echo f; done", "git -C hooks log -1",
                    "git -c a.b=c log -1", "cat <<'E'\na\nb\nE"):
            self.assertIsNone(bash(cmd), cmd)
        # 승인 턴에도 cd는 막는다.
        self.assertIn("cd 없이", bash("cd hooks && git status") or "")
        for cmd in ("cd hooks && git status --short", "cd hooks && grep -n def sub_write.py"):
            self.assertIn("cd 없이 경로를 인자로", bash(cmd) or "", cmd)

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
            self.assertIsNotNone(bash(role, 'D=/x; ls "$D"'), role)
            self.assertIn("heredoc", bash(role, "python - <<'E'\nprint(1)\nprint(2)\nE") or "", role)
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

    # assistant 메인 세션은 승인 명령 턴에만 쓰기 Bash를 쓰고 log/ 경로 명령은 막으며 prompt_reason은 계속 받는다.
    def test_assistant_main_session_not_gated(self):
        self.running_task("미검토")
        yes = self.transcript(("assistant", "계획"), ("user", "승인"))

        def bash(cmd, transcript=yes):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"},
                               "agent_type": "buddy", "cwd": str(self.root), "transcript_path": transcript}, "claude")
        for cmd in ("rm a.txt", "mv a b", "sed -i s/a/b/ a.py", 'python -c "print(1)"'):
            self.assertIsNone(bash(cmd), cmd)
        self.assertIn("log/", bash("sed -i s/a/b/ log/task/t/status.md") or "")
        self.assertIsNone(bash("git -C /x log -1"))
        no = self.transcript(("assistant", "계획"), ("user", "고쳐"))
        self.assertIsNotNone(bash("sed -i s/a/b/ a.py", no))
        self.assertIsNone(self.write("hooks/a.py", "x", role="buddy"))

    # git 복구, 삭제 명령은 승인 여부와 관계없이 director 메인 세션에서 deny된다.
    def test_main_session_bash_write_commands_denied(self):
        for cmd in ("git checkout -- README.md", "git restore README.md", "git apply a.patch", "git reset --hard",
                    "find . -name '*.tmp' -delete", "ls | xargs rm"):
            data = {"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"}, "agent_type": "rio",
                    "cwd": str(self.root)}
            no = self.transcript(("assistant", "diff"), ("user", "고쳐"))  # 같은 기록 파일을 덮어쓰므로 매번 만든다.
            self.assertIsNotNone(hook.check({**data, "transcript_path": no}, "claude"), cmd)
            yes = self.transcript(("assistant", "diff"), ("user", "승인"))
            self.assertIn("다른 쓰기 도구", hook.check({**data, "transcript_path": yes}, "claude") or "", cmd)

    # 승인 창 형태 명령은 승인 턴과 Codex에서 통과하고, 비승인 턴 claude에서는 main이 ask로, 그 밖의 사유는 deny로 출력한다.
    def test_soft_reasons_ask_or_pass(self):
        temp = str(Path(self.ostemp.name).resolve() / "a.md").replace("\\", "/")
        soft_cmds = ('python -c "print(1)"', f"sed -n 1,5p {temp}", 'D=/x; ls "$D"', "for f in a b; do echo f; done",
                     "git -C hooks log -1", "cat <<'E'\na\nb\nE")
        yes = self.transcript(("assistant", "계획"), ("user", "승인"), name="yes.jsonl")
        no = self.transcript(("assistant", "계획"), ("user", "다시 생각해"))

        def data(cmd, transcript):
            return {"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"},
                    "agent_type": "rio", "cwd": str(self.root), "transcript_path": transcript}

        def decision(cmd, runner="claude"):
            out = io.StringIO()
            with patch.object(sys, "argv", ["check_tool_use.py", "--runner", runner]), \
                 patch.object(sys, "stdin", io.StringIO(json.dumps(data(cmd, no)))), patch.object(sys, "stdout", out):
                hook.main()
            return json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"]
        for cmd in soft_cmds:
            for runner in ("claude", "codex"):
                self.assertIsNone(hook.check(data(cmd, yes), runner), (cmd, runner))
            self.assertIsNone(hook.check(data(cmd, no), "codex"), cmd)
            self.assertEqual(decision(cmd), "ask", cmd)
        self.assertEqual(decision("echo x > a.txt"), "deny")
        self.assertEqual(decision("cd hooks && git status"), "deny")
        # 승인 턴에도 쓰기 명령과 cd는 막는다.
        self.assertIn("다른 쓰기 도구", hook.check(data("echo x > a.txt", yes), "claude") or "")
        self.assertIn("cd 없이", hook.check(data("cd hooks && git status", yes), "claude") or "")

    # 저장소 안 절대 경로로 이동하는 cd는 deny되고 cd 없는 명령은 통과한다.
    def test_absolute_cd_denied(self):
        def bash(cmd):
            return hook.check({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "테스트 명령"},
                               "agent_type": "rio", "cwd": str(self.root),
                               "transcript_path": self.transcript(("assistant", "계획"), ("user", "다시 생각해"))}, "claude")
        root = self.root.as_posix()
        for cmd in (f"cd {root} && ls -t log/task/ | head -10", f"cd {root} && ls -t log/task/"):
            self.assertIn("cd 없이", bash(cmd) or "", cmd)
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

    # ~/.claude·~/.codex 쓰기는 통과하고 인증 파일만 막는다. Claude·Codex 입력이 같다.
    def test_global_folder_write_left_to_ask(self):
        # 홈 폴더 경로를 준비한다.
        h = self.home
        # 경로를 추가하는 apply_patch 입력으로 검사를 실행한다.
        def patch_(path):
            text = chr(10).join(["*** Begin Patch", f"*** Add File: {path}", "+x", "*** End Patch"])
            return hook.check({"tool_name": "apply_patch", "tool_input": {"command": text}, "cwd": str(self.cwd),
                               "agent_type": "buddy"}, "codex")
        # 일반 전역 폴더 쓰기는 사용자 확인에 맡기고 비밀 파일은 막는지 확인한다.
        for path in (h / ".claude/plans/x.md", h / ".claude/settings.json", h / ".codex/config.toml"):
            self.assertIsNone(self.claude("Write", {"file_path": str(path), "content": "x"}), path)
            self.assertIsNone(patch_(path), path)
            self.assertIn("다른 쓰기 도구", self.bash(f"echo a > {path}") or "", path)
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
        self.assertIsNone(patch_(f"{self.home}/.claude/a.md"))

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
        self.assertEqual("이 명령은 리다이렉트로 `out.txt`에 내용을 씁니다. 다른 쓰기 도구를 선택하여 작업하십시오.",
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
                if runner == "claude":
                    self.assertIn("임시 폴더", run(f"cat {base}/a") or "", (base, runner))
                else:
                    self.assertIsNone(run(f"cat {base}/a"), (base, runner))  # Codex는 샌드박스 승인에 맡긴다.
                self.assertIn("다른 쓰기 도구", run(f"echo x > {base}/new") or "", (base, runner))
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


class PlanGaps(unittest.TestCase):
    # 문서 갱신 대상을 넣은 입력 문서 본문을 만든다.
    def doc(self, target: str, extra: str = "") -> str:
        return ("## 요구사항 목록\n\n### 1. a\n\n- 검증: x\n  기대 결과: y\n\n## 작업 계획\n\n"
                f"{extra}- 문서 갱신 대상: {target}\n\n### 검증 계획\n\n- 검증 명령\n  - `python t.py`: 1\n")

    # 문서 갱신 대상 또는 미충족 칸 관련 사유만 골라 반환한다.
    def gaps(self, text: str) -> list[str]:
        import sub_plan
        return [g for g in sub_plan.plan_gaps(text) if "문서 갱신 대상" in g or "채우지" in g]

    # 들여쓴 코드 블록 안의 꺾쇠를 자리표시로 보지 않는지 확인한다.
    def test_indented_fence_placeholder_ignored(self):
        extra = "- 문구 diff:\n\n  ```diff\n  +<a> b\n  ```\n"
        self.assertEqual(self.gaps(self.doc("docs/a.md:12", extra)), [])

    # 문서 갱신 대상이 `파일:행` 또는 검색어 없음 기록일 때만 통과하는지 확인한다.
    def test_doc_target_format(self):
        self.assertTrue(self.gaps(self.doc("docs/a.md")))
        self.assertEqual(self.gaps(self.doc("docs/a.md:12 (권한)")), [])
        self.assertEqual(self.gaps(self.doc('"검색어와 일치 없음"(검색어: 권한)')), [])


if __name__ == "__main__":
    unittest.main()
