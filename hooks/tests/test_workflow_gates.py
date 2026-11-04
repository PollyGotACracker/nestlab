"""검토 완료 기록 조건, 템플릿 필수 절, spawn 이름, 단독 실행 쓰기 범위 테스트"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import check_tool_use as hook
import sub_call
import sub_docs
import sub_judgment
import sub_path
import sub_role
import sub_scope

REAL_SKILL_CALLED = sub_scope.skill_called



ITEM = "조사 9. 예시 결정"
STATE = """# t

- 작업 ID: t
- 작업 상태: 진행

## 요구사항

### 검토 필요

- {item}
  - 상태: {status}
  - 사용자 검토 시각: 기록 없음
  - 대응 근거: {basis}
  - 대응 방안:
    - 결정 뒤 기록

## 승인

### 착수 승인 범위

- 사용자 승인 상태: 승인
- 사용자 승인 시각: 2026-10-26 10:00:00
- 연결 대상: x
- 승인 행동: x
- 영향 범위: x
- 범위 밖: 없음
"""
LOOP = "## 조사 및 반증 루프\n\n| 회차 | 반증 건수 | 정지 여부 |\n| -- | -- | -- |\n| 1 | 0 | {stop} |\n"
STOP_STATE = STATE + "\n## 중단 사유\n\n- 중단 유형: {kind}\n"
RESEARCH = """# 조사 문서: t

## 조사 계획


## 조사 단계 원문

### 최종 조사 원문: 조사 단계

```
조사 9. 예시 결정
후보 상태 기반 판정: 충족
후보 표현 패턴 판정: 미충족(오탐 높음)
후보 단계 실행기: 충족
제안: 상태 기반 판정
출처: https://a.b
```

### 1회차 반증 원문

```
- 주장 1: 지지 | 출처: https://a.b
```
"""


# Claude 대화 기록 한 줄을 JSON 문자열로 만든다.
def claude_line(role, content):
    return json.dumps({"type": role, "message": {"content": content}}, ensure_ascii=False)


class Gates(unittest.TestCase):
    # 임시 저장소와 patch를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root / "log" / "state"
        self.state.mkdir(parents=True)
        self.skills = self.root / "skills"
        self.patches = [patch.object(sub_scope, "project_root", return_value=self.root),
                        patch.object(sub_docs, "project_root", return_value=self.root),
                        patch.object(sub_docs, "SKILLS", self.skills),
                        patch.object(sub_path, "TEMP", self.root / "ostemp"),
                        patch.object(sub_scope, "skill_called", return_value=True)]
        for p in self.patches:
            p.start()
        (self.state / "t-research.md").write_text(LOOP.format(stop="정지(반증 0건)"), encoding="utf-8")

    # patch와 임시 저장소를 정리한다.
    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    # 양식 주석 안의 형식 예시만 있으면 early_items는 빈 목록이고, 미검토 `검토 1.` 항목은 이름을 반환한다.
    def test_early_items_placeholder(self):
        status = self.root / "status.md"
        body = "# t\n\n## 요구사항\n\n### 검토 필요\n\n{item}\n\n## 승인\n"
        with patch.object(sub_judgment, "review_done", return_value=("사용자 검토 완료", "폐기")):
            status.write_text(body.format(item="<!-- 형식\n  - 검토 <번호>. <제목>\n    - 상태: <미검토>\n-->"), encoding="utf-8")
            self.assertEqual(sub_judgment.early_items(status), [])
            status.write_text(body.format(item="- 검토 1. 도구\n  - 상태: 미검토"), encoding="utf-8")
            self.assertEqual(sub_judgment.early_items(status), ["검토 1. 도구"])

    # 대화 기록 파일을 만들고 경로를 반환하는 보조 함수이다.
    def transcript(self, *lines):
        path = self.root / "t.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    # 사용자 메시지만 있는 대화 기록을 만드는 보조 함수이다.
    def plain(self, user="승인"):
        return self.transcript(claude_line("assistant", [{"type": "text", "text": "보고"}]), claude_line("user", user))

    # 질문 도구 질문과 사용자 답이 있는 대화 기록을 만드는 보조 함수이다.
    def answered(self, question, answer="확정", header="결정", result_id="q1", text=""):
        ask = {"type": "tool_use", "id": "q1", "name": "AskUserQuestion",
               "input": {"questions": [{"header": header, "question": question, "options": []}]}}
        result = {"type": "tool_result", "tool_use_id": result_id, "content": "User has answered"}
        reply = json.dumps({"type": "user", "message": {"content": [result]},
                            "toolUseResult": {"answers": {question: answer} if answer else {}}}, ensure_ascii=False)
        return self.transcript(claude_line("assistant", [{"type": "text", "text": text}, ask] if text else [ask]),
                               reply, claude_line("user", "승인"))

    # Write 도구 입력으로 PreToolUse 검사를 실행하는 보조 함수이다.
    def write(self, rel, content, transcript, role="rio", **extra):
        data = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / rel), "content": content},
                "agent_type": role, "cwd": str(self.root), "transcript_path": transcript, **extra}
        return hook.check(data, "claude")

    # 질문 호출 id와 같은 id의 결과만 답이다. 다른 도구 결과, 답 없는 결과는 미답이다.
    def test_answered_questions_pairs_call_id(self):
        from sub_session import answered_questions
        self.assertEqual(answered_questions(self.answered("조사 9. 예시 결정: 어느 안?")),
                         [("결정\n조사 9. 예시 결정: 어느 안?", "확정")])
        self.assertEqual(answered_questions(self.answered("조사 9. 예시 결정: 어느 안?", result_id="other")), [])
        self.assertEqual(answered_questions(self.answered("조사 9. 예시 결정: 어느 안?", answer="")), [])

    # 중단 사유 절의 중단 판정 미해결 줄은 지우거나 바꿀 수 없고 다른 절 수정은 통과한다.
    def test_unresolved_line_kept(self):
        line = "- 중단 판정 미해결: 사유"
        base = STATE.format(item=ITEM, status="미검토", basis="x") + f"\n## 중단 사유\n\n{line}\n"
        (self.state / "t.md").write_text(base, encoding="utf-8")
        self.assertIn("중단 판정 미해결", self.write("log/state/t.md", base.replace(line + "\n", ""), self.plain()))
        self.assertIn("중단 판정 미해결", self.write("log/state/t.md", base.replace(line, line.replace("사유", "바꿈")), self.plain()))
        self.assertIsNone(self.write("log/state/t.md", base.replace("- 범위 밖: 없음", "- 범위 밖: 변경"), self.plain()))

    # 조사 확인 상태는 착수 승인과 기록된 검토 확정 상태로 판정한다.
    def test_research_confirm_needs_answer_and_approval(self):
        research = "# 조사 문서: t\n\n- 작업 ID: t\n- 확인 상태: {s}\n\n## 조사 계획\n"
        (self.state / "t-research.md").write_text(research.format(s="보류"), encoding="utf-8")
        new = research.format(s="확인")
        # 착수 승인이 없으면 막는다.
        self.assertIn("착수 승인", self.write("log/state/t-research.md", new, self.plain()))
        # 착수 승인이 있어도 미확정 검토가 남으면 막는다.
        approved = ("# t\n\n- 작업 ID: t\n\n## 승인\n\n### 착수 승인 범위\n\n- 사용자 승인 상태: 승인\n"
                    "- 사용자 승인 시각: 2026-10-26 10:00:00\n- 연결 대상: x\n- 승인 행동: x\n- 영향 범위: x\n- 범위 밖: 없음\n")
        (self.state / "t.md").write_text(approved, encoding="utf-8")
        skill = self.skills / "nestlab-log-spec"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("<!-- 모든 목록이 검토 완료 또는 폐기일 때 승인 가능 -->\n", encoding="utf-8")
        review = "\n## 검토 필요\n- 검토 1. 기록 방식\n  - 상태: {status}\n"
        (self.state / "t.md").write_text(approved + review.format(status="미검토"), encoding="utf-8")
        self.assertIn("검토 1.", self.write("log/state/t-research.md", new, self.plain()))
        (self.state / "t.md").write_text(approved + review.format(status="검토 완료"), encoding="utf-8")
        self.assertIsNone(self.write("log/state/t-research.md", new, self.plain()))

    # 중단 유형 칸은 실행 승인과 `중단 유형` 질문 확정 답이 있어야 쓸 수 있다.
    def test_stop_type_needs_answer_and_approval(self):
        new = STOP_STATE.format(item=ITEM, status="미검토", basis="x", kind="선택 필요")
        (self.state / "t.md").write_text(STOP_STATE.format(item=ITEM, status="미검토", basis="x", kind="<충족 불가 | 선택 필요>"),
                                         encoding="utf-8")
        q = "중단 유형: 어느 유형으로 둘까요?"
        # 실행 승인이 없으면 막는다.
        self.assertIn("실행 승인", self.write("log/state/t.md", new, self.answered(q)))
        execute = ("\n### 실행 승인 범위\n\n- 사용자 승인 상태: 승인\n- 사용자 승인 시각: 2026-10-26 11:00:00\n"
                   "- 연결 대상: x\n- 승인 행동: x\n- 영향 범위: x\n- 범위 밖: 없음\n")
        new = new.replace("\n## 중단 사유", execute + "\n## 중단 사유")
        # 실행 승인이 이미 기록된 문서 위에서 검사한다.
        (self.state / "t.md").write_text(new.replace("선택 필요", "<충족 불가 | 선택 필요>"), encoding="utf-8")
        # 질문 답이 없거나 확정이 아니면 막고 확정 답이면 통과한다.
        self.assertIn("중단 유형", self.write("log/state/t.md", new, self.plain()))
        self.assertIn("중단 유형", self.write("log/state/t.md", new, self.answered(q, answer="No preference")))
        self.assertIsNone(self.write("log/state/t.md", new, self.answered(q)))

    # 템플릿 필수 절
    def test_template_titles(self):
        folder = self.skills / "nestlab-log-state"
        folder.mkdir(parents=True)
        template = "# <작업명>\n\n## 요구사항\n\n```\n## 코드 안 제목\n```\n\n### 검토 필요\n"
        (folder / "SKILL.md").write_text(f"# 스킬\n\n## 절차\n\n## 제목 밖\n\n## Template\n\n````markdown\n{template}````\n\n## 끝\n",
                                         encoding="utf-8")
        ok = "# t\n\n## 요구사항\n\n### 검토 필요\n"
        self.assertIn("검토 필요", self.write("log/state/n.md", "# t\n\n## 요구사항\n", self.plain()))
        self.assertIsNone(self.write("log/state/n.md", ok, self.plain()))
        self.assertIsNone(self.write("log/state/n.md", ok.replace("## 요구사항", "# 요구사항"), self.plain()))
        (self.state / "n.md").write_text(ok, encoding="utf-8")
        self.assertIn("요구사항", self.write("log/state/n.md", "# t\n\n### 검토 필요\n", self.plain()))
        legacy = "# 옛 양식\n\n## 다른 절\n"
        (self.state / "old.md").write_text(legacy, encoding="utf-8")
        self.assertIsNone(self.write("log/state/old.md", legacy + "\n- 추가\n", self.plain()))

    # 새 작업 폴더 경과 문서는 필수 절을 지켜야 하고, log/state 기존 문서는 기존 규칙으로 판정한다.
    def test_task_folder_status_titles(self):
        folder = self.skills / "nestlab-log-state"
        folder.mkdir(parents=True)
        template = "<!-- 쓰기 전 확인\n- 이 코드 블록의 모든 제목을 남긴다.\n-->\n# <작업명>\n\n## 요구사항\n\n## 검증 결과\n\n## 결정 기록\n"
        (folder / "SKILL.md").write_text(f"# 스킬\n\n## Template\n\n````markdown\n{template}````\n", encoding="utf-8")
        rel = "log/task/t/status.md"

        # 필수 절이 빠지거나 `검수 결과`로 쓰면 필수 절 누락으로 막는다.
        self.assertIn("검증 결과", self.write(rel, "# t\n\n## 요구사항\n\n## 결정 기록\n", self.plain()) or "")
        old_name = "# t\n\n## 요구사항\n\n## 검수 결과\n\n## 결정 기록\n"
        self.assertIn("검증 결과", self.write(rel, old_name, self.plain()) or "")

        # 템플릿 필수 절을 모두 갖춘 새 경과 문서는 통과한다.
        self.assertIsNone(self.write(rel, "# t\n\n## 요구사항\n\n## 검증 결과\n\n## 결정 기록\n", self.plain()))

        # 기존 log/state 문서는 이전에 있던 절만 필수로 본다.
        legacy = "# 옛 양식\n\n## 요구사항\n"
        (self.state / "old.md").write_text(legacy, encoding="utf-8")
        self.assertIsNone(self.write("log/state/old.md", legacy + "\n- 추가\n", self.plain()))

    # 새 경과 문서는 요구사항 목록 절을 지켜야 하고, 이 절이 없던 기존 문서는 계속 쓸 수 있다.
    def test_state_template_requirement_list(self):
        folder = self.skills / "nestlab-log-state"
        folder.mkdir(parents=True)
        template = "# <작업명>\n\n## 요구사항\n\n### 요구사항 목록\n\n### 문제 정의\n"
        (folder / "SKILL.md").write_text(f"# 스킬\n\n## Template\n\n````markdown\n{template}````\n", encoding="utf-8")
        rel = "log/task/t/status.md"
        self.assertIn("요구사항 목록", self.write(rel, "# t\n\n## 요구사항\n\n### 문제 정의\n", self.plain()) or "")
        self.assertIsNone(self.write(rel, "# t\n\n## 요구사항\n\n### 요구사항 목록\n\n### 문제 정의\n", self.plain()))
        (self.root / "log/task/t").mkdir(parents=True)
        old = "# t\n\n## 요구사항\n\n### 문제 정의\n"
        (self.root / rel).write_text(old, encoding="utf-8")
        self.assertIsNone(self.write(rel, old + "\n## 작업 경과\n\n- 추가\n", self.plain()))

    # 명세 문서가 없는 작업 폴더에서 경과 문서 착수 승인 쓰기는 요구사항 목록과 무관하고, 기존 흐름 코드 블록이 없으면 막힌다.
    def test_kickoff_approval_without_spec(self):
        folder = self.root / "log/task/t"
        folder.mkdir(parents=True)
        flow = "\n## 기존 흐름\n\n```\nhooks/a.py:1 호출 → hooks/b.py:2\n```\n"
        research = ("# 조사\n\n- 작업 ID: t\n\n## 조사 계획\n\n- 제외 범위: 없음\n\n### 조사 1. 도구 (문제 1)\n\n"
                    "- 질문: 도구\n\n## 프로젝트 환경\n\nPython (출처: a.py:1)\n")
        (folder / "research.md").write_text(research + flow, encoding="utf-8")
        old = "# t\n\n- 작업 ID: t\n- 작업 상태: 진행\n\n## 요구사항\n\n### 요구사항 목록\n\n1. 기능: 변경\n\n## 승인\n\n### 착수 승인 범위\n\n"
        fields = "- 사용자 승인 상태: {s}\n- 사용자 승인 시각: 2026-10-26 10:00:00\n- 연결 대상: x\n- 승인 행동: x\n- 영향 범위: x\n- 범위 밖: 없음\n"
        (folder / "status.md").write_text(old + fields.format(s="대기"), encoding="utf-8")
        approved = old + fields.format(s="승인")
        self.assertIsNone(self.write("log/task/t/status.md", approved, self.plain()))
        blank = old.replace("1. 기능: 변경", "1. <요구사항 이름>: <결과>")
        (folder / "status.md").write_text(blank + fields.format(s="대기"), encoding="utf-8")
        self.assertIsNone(self.write("log/task/t/status.md", blank + fields.format(s="승인"), self.plain()))
        (folder / "status.md").write_text(old + fields.format(s="대기"), encoding="utf-8")
        (folder / "research.md").write_text(research, encoding="utf-8")
        self.assertIn("기존 흐름", self.write("log/task/t/status.md", approved, self.plain()) or "")

    # 문서 쓰기 전 스킬 호출 검사
    def test_doc_write_needs_skill_call(self):
        # 스킬 호출 기록과 대상 문서별 스킬 이름을 준비한다.
        skill = lambda name: claude_line("assistant", [{"type": "tool_use", "name": "Skill", "input": {"skill": name}}])
        user, text = claude_line("user", "정리해"), claude_line("assistant", [{"type": "text", "text": "보고"}])
        cases = {"log/incident/a.md": "nestlab-log-incident", "log/task/t/status.md": "nestlab-log-state",
                 "log/task/t/spec.md": "nestlab-log-spec", "log/task/t/research.md": "nestlab-log-research",
                 "log/task/t/refute.md": "nestlab-log-refute", "log/state/t.md": "nestlab-log-state",
                 "AGENTS.md": "nestlab-rule", "shared/skills/x/SKILL.md": "nestlab-rule",
                 ".claude/agents/worker/AGENTS.md": "nestlab-rule"}
        with patch.object(sub_scope, "skill_called", REAL_SKILL_CALLED):
            for rel, name in cases.items():
                # 서브에이전트는 작업 문서 중 명세 문서만 읽을 수 있으므로 작업 문서는 메인 세션 쓰기로 검사한다.
                who = {} if rel.startswith(("log/task/", "log/state/")) else {"role": "jelly", "agent_id": "s"}

                # 호출 없이 쓰면 스킬 이름이 든 사유로 막는다.
                self.assertIn(name, self.write(rel, "# t\n", self.transcript(user, text), **who), rel)

                # 이전 턴에만 호출했으면 막는다.
                self.assertIn(name, self.write(rel, "# t\n", self.transcript(skill(name), user, text), **who), rel)

                # 이번 턴에 호출했으면 통과한다.
                self.assertIsNone(self.write(rel, "# t\n", self.transcript(user, skill(name), text), **who), rel)
            # 대상 밖 파일에는 적용하지 않는다.
            self.assertIsNone(self.write("hooks/a.py", "x", self.transcript(user, text), role="jelly", agent_id="s"))

    # Codex는 현재 턴에 읽기 명령으로 SKILL.md를 읽어야 작업 문서 apply_patch가 통과한다.
    def test_codex_doc_write_needs_skill_read(self):
        # Codex rollout 줄과 apply_patch 입력을 만든다.
        line = lambda payload: json.dumps({"type": "response_item", "payload": payload})
        user = line({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "정리해"}]})
        read = lambda cmd: line({"type": "custom_tool_call", "name": "exec", "input": cmd})
        patch_text = "*** Begin Patch\n*** Add File: log/incident/a.md\n+# t\n*** End Patch"

        # 검사를 실행하는 보조 함수이다.
        def check(*lines):
            data = {"tool_name": "apply_patch", "tool_input": {"command": patch_text}, "cwd": str(self.root),
                    "agent_type": "buddy", "transcript_path": self.transcript(*lines)}
            with patch.object(sub_scope, "skill_called", REAL_SKILL_CALLED), patch.object(sub_scope, "approved", return_value=True):
                return hook.check(data, "codex")

        # 읽기 없음, 이전 턴 읽기, 다른 스킬 읽기는 막고 이번 턴 읽기는 통과한다.
        ok = read("Get-Content -LiteralPath 'C:\\\\s\\\\nestlab-log-incident\\\\SKILL.md'")
        self.assertIn("nestlab-log-incident", check(user) or "")
        self.assertIn("nestlab-log-incident", check(ok, user) or "")
        self.assertIn("nestlab-log-incident", check(user, read("cat shared/skills/nestlab-log-state/SKILL.md")) or "")
        self.assertIsNone(check(user, ok))
        self.assertIsNone(check(user, read("cat shared/skills/nestlab-log-incident/SKILL.md")))

    # Bash 쓰기 명령이 작업 문서를 가리키면 스킬 호출을 확인한다.
    def test_bash_doc_write_needs_skill_call(self):
        skill = claude_line("assistant", [{"type": "tool_use", "name": "Skill", "input": {"skill": "nestlab-log-state"}}])
        user = claude_line("user", "정리해")

        # 서브에이전트 Bash 입력으로 검사를 실행한다.
        def check(*lines):
            data = {"tool_name": "Bash", "tool_input": {"command": "mv a.md log/task/x/status.md", "description": "테스트 명령"},
                    "cwd": str(self.root), "agent_type": "rio", "agent_id": "sub-1", "transcript_path": self.transcript(*lines)}
            with patch.object(sub_scope, "skill_called", REAL_SKILL_CALLED), patch.object(sub_scope, "approved", return_value=True):
                return hook.check(data, "claude")
        self.assertIn("nestlab-log-state", check(user) or "")
        self.assertIsNone(check(user, skill))

    # spawn 이름
    def test_spawn_names(self):
        for name in ("Agent", "spawn_agent", "collaborationspawn_agent"):
            self.assertTrue(sub_call.is_spawn(name), name)
        for name in ("Bash", "spawn_agents", "", None):
            self.assertFalse(sub_call.is_spawn(name), name)

    # 단독 실행 쓰기 범위
    def test_solo_worker_needs_approval_outside_log(self):
        no = self.plain("다시 생각해")
        self.assertIsNotNone(self.write("src/a.py", "x", no, role="jelly"))
        self.assertIsNone(self.write("src/a.py", "x", self.plain(), role="jelly"))
        self.assertIsNone(self.write("src/a.py", "x", no, role="jelly", agent_id="sub-1"))


if __name__ == "__main__":
    unittest.main()
