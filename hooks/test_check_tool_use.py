"""check_tool_use.py 동작 테스트"""
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
import sub_scope

RESEARCH = """# 조사 문서: t

## 조사 계획

- 외부 조사: 필요 (이유: 도구 선택)

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

INPUT_APPROVED = """# t 입력

### 실행 승인 범위

- 승인 상태: 승인
"""


PLAN_OK = """# t 입력

### 요구사항 목록

#### 1. 예시

목표: a
결과: b

- 작업: c
- 검수: d (정적)
  기대 결과: e

## 작업 계획

### 검수 계획

- 검증 명령
  - `python -m unittest`: 요구사항 1

## 승인

### 실행 승인 범위

- 승인 상태: 승인
"""


def claude_line(role: str, text: str) -> str:
    content = text if role == "user" else [{"type": "text", "text": text}]
    return json.dumps({"type": role, "message": {"content": content}}, ensure_ascii=False)


def codex_meta(**payload) -> str:
    return json.dumps({"type": "session_meta", "payload": payload}, ensure_ascii=False)


def codex_msg(role: str, text: str) -> str:
    return json.dumps({"type": "response_item", "payload": {"type": "message", "role": role,
                                                            "content": [{"type": "input_text", "text": text}]}},
                      ensure_ascii=False)


class WriteScope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "log" / "state"
        self.state.mkdir(parents=True)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.patches = [patch.object(sub_scope, "ROOT", self.root), patch.object(sub_approval, "ACTIVE", self.state / ".active"),
                        patch.object(sub_docs, "STATE", self.state), patch.object(sub_role, "CODEX_SESSIONS", self.sessions)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def transcript(self, *pairs) -> str:
        path = self.root / "t.jsonl"
        path.write_text("\n".join(claude_line(r, t) for r, t in pairs) + "\n", encoding="utf-8")
        return str(path)

    def write(self, rel, content, role="rio", transcript=None):
        data = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / rel), "content": content},
                "agent_type": role, "cwd": str(self.root),
                "transcript_path": transcript or self.transcript(("assistant", "보고"), ("user", "승인"))}
        return hook.check(data, "claude")

    # director 쓰기 범위
    def test_director_scope(self):
        self.assertIsNone(self.write("log/state/x.md", "- 진행"))
        self.assertIsNotNone(self.write("log/incident/a.md", "# 사건"))
        self.assertIsNotNone(self.write("hooks/a.py", "x"))
        self.assertIsNotNone(self.write("log/state/.active/a.json", "{}"))

    def test_director_bash_write_denied(self):
        data = {"tool_name": "Bash", "tool_input": {"command": "echo a > f.txt"}, "agent_type": "rio"}
        self.assertIsNotNone(hook.check(data, "claude"))
        data["tool_input"]["command"] = "git status"
        self.assertIsNone(hook.check(data, "claude"))

    def test_director_bash_allowlist(self):
        allowed = ("git status", "git log -1 --format=%h", "git diff HEAD~1", "git show HEAD:AGENTS.md",
                   "git branch --show-current", "date", 'date "+%Y-%m-%d %H:%M"')
        denied = ("ls", "cat AGENTS.md", "git log && rm x", "git log | head", "git log --output=a.txt",
                  "git branch -D x", "git -c core.pager=x log", "date -s 2020", "echo $(git log)", "git push")
        for cmd in allowed:
            data = {"tool_name": "Bash", "tool_input": {"command": cmd}, "agent_type": "rio"}
            self.assertIsNone(hook.check(data, "claude"), cmd)
        for cmd in denied:
            data = {"tool_name": "Bash", "tool_input": {"command": cmd}, "agent_type": "rio"}
            self.assertIsNotNone(hook.check(data, "claude"), cmd)

    def test_execution_approval_needs_filled_plan(self):
        self.assertIsNone(self.write("log/state/t-input.md", PLAN_OK))
        for broken in (PLAN_OK.replace("`python -m unittest`", "`<명령>`"),
                       PLAN_OK.replace("  기대 결과: e\n", ""),
                       PLAN_OK.replace("목표: a", "목표: <요구사항의 목표>"),
                       PLAN_OK.replace("#### 1. 예시", "")):
            self.assertIsNotNone(self.write("log/state/t-input.md", broken))
        commented = PLAN_OK.replace("### 검수 계획", "<!-- 안내 -->\n\n### 검수 계획").replace("목표: a", "목표: `<타입>` 형식 확인")
        self.assertIsNone(self.write("log/state/t-input.md", commented))

    def test_approval_status_needs_user_approval(self):
        doc = "### 착수 승인 범위\n\n- 승인 상태: 승인\n"
        no = self.transcript(("assistant", "계획"), ("user", "좋아 계속"))
        self.assertIsNotNone(self.write("log/state/t-input.md", doc, transcript=no))
        self.assertIsNone(self.write("log/state/t-input.md", doc))

    def test_raw_block_protected(self):
        (self.state / "t-research.md").write_text(RESEARCH, encoding="utf-8")
        self.assertIsNotNone(self.write("log/state/t-research.md", RESEARCH.replace("기록 없음", "고침")))
        self.assertIsNone(self.write("log/state/t-research.md", RESEARCH.replace("| A    | 낮음 |", "| A | 중간 |")))

    def test_round_blocks_protected(self):
        rounds = RESEARCH + "\n### 1회차 반증 원문\n\n```\n- 주장 1: 반증\n```\n"
        (self.state / "t-research.md").write_text(rounds, encoding="utf-8")
        self.assertIsNotNone(self.write("log/state/t-research.md", rounds.replace("반증\n```", "지지\n```")))
        self.assertIsNotNone(self.write("log/state/t-research.md", RESEARCH))
        self.assertIsNone(self.write("log/state/t-research.md", rounds.replace("| A    | 낮음 |", "| A | 중간 |")))
        self.assertIsNotNone(self.write("log/state/n-research.md", rounds))

    def test_new_research_doc_allows_empty_raw_only(self):
        self.assertIsNone(self.write("log/state/n-research.md", RESEARCH))
        self.assertIsNotNone(self.write("log/state/n-research.md", RESEARCH.replace("기록 없음", "가짜 결과")))

    def test_decision_needs_research_result(self):
        (self.state / "t-research.md").write_text(RESEARCH, encoding="utf-8")
        user = "### 확정 결정\n\n1. Windows만 대상으로 한다.\n   - 출처: 사용자 결정 요약\n"
        self.assertIsNone(self.write("log/state/t-input.md", user))
        doc = "### 확정 결정\n\n1. A를 쓴다.\n   - 출처: 조사 문서 결정 1과 사용자 판단 요약\n"
        self.assertIsNotNone(self.write("log/state/t-input.md", doc))
        (self.state / "t-research.md").write_text(RESEARCH.replace("기록 없음", "결정 1 권고: A"), encoding="utf-8")
        self.assertIsNone(self.write("log/state/t-input.md", doc))

    # 읽기 전용
    def test_readonly_roles(self):
        self.assertIsNotNone(self.write("log/state/x.md", "x", role="nico"))
        self.assertIsNotNone(self.write("log/state/x.md", "x", role="ricky"))
        self.assertIsNone(self.write("src/a.py", "x", role="jelly"))

    # assistant 쓰기 승인
    def test_assistant_write_needs_approval(self):
        no = self.transcript(("assistant", "diff"), ("user", "고쳐"))
        self.assertIsNotNone(self.write("src/a.py", "x", role="buddy", transcript=no))
        self.assertIsNone(self.write("src/a.py", "x", role="buddy"))
        data = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / "a.py"), "content": "x"},
                "agent_type": "buddy", "agent_id": "sub-1", "transcript_path": no}
        self.assertIsNone(hook.check(data, "claude"))

    # 하위 에이전트 호출 승인 (Claude)
    def agent(self, prompt, user, **extra):
        data = {"tool_name": "Agent", "tool_input": {"subagent_type": "jelly", "prompt": prompt},
                "agent_type": "rio", "session_id": "s1",
                "transcript_path": self.transcript(("assistant", "계획"), ("user", user)), **extra}
        return hook.check(data, "claude")

    def test_agent_call_needs_approval(self):
        prompt = "작업 종류: 구현\n입력 문서: log/state/t-input.md"
        self.assertIsNotNone(self.agent(prompt, "다시 생각해"))
        self.assertIsNone(self.agent(prompt, "승인"))
        self.assertIsNone(self.agent(prompt, "다시 생각해", agent_id="sub-1"))

    def test_approval_last_line_command(self):
        allowed = ("승인", "이번엔 테스트 빼고 구현해", "시작해.", "진행", "그래", "응",
                   "검수해 줘", "주의사항 1\n주의사항 2\n\n진행해!", "그거 안 해도 돼 진행해")
        denied = ("진행 안 해", "구현하지 마", "승인 안 함", "1", "그러든지.", "반응",
                  "진행해\n근데 잠깐", "", "안 그래", "안  그래", "못 해", "안 진행해")
        for text in allowed:
            self.assertTrue(sub_approval.approved(text), text)
        for text in denied:
            self.assertFalse(sub_approval.approved(text), text)

    def test_buddy_agent_call_not_checked(self):
        data = {"tool_name": "Agent", "tool_input": {"subagent_type": "Explore", "prompt": "x"},
                "agent_type": "buddy", "transcript_path": self.transcript(("assistant", "a"), ("user", "b"))}
        self.assertIsNone(hook.check(data, "claude"))

    def test_rework_continues_without_new_approval(self):
        prompt = "작업 종류: 재작업\n입력 문서: log/state/t-input.md"
        self.assertIsNotNone(self.agent(prompt, "이 부분 고쳐"))
        (self.state / "t-input.md").write_text(INPUT_APPROVED, encoding="utf-8")
        self.assertIsNone(self.agent(prompt, "이 부분 고쳐"))

    # 하위 에이전트 호출 승인 (Codex 하위 thread)
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


# 하위 에이전트 입력 형식
STATE_IN = "상태 문서: log/state/20261006-0555-x.md\n절: 요구사항 목록"
RESEARCH_IN = "작업 종류: 조사\n조사 문서: log/state/20261006-0555-x-research.md"
INPUT_IN = "입력 문서: log/state/20261006-0555-x-input.md"
REFUTE = "작업 종류: 반증\n주장: 1. 변경이 요구를 충족한다.\n증거: 코드 발췌\n출처: hooks/a.py:12\n판정 기준: 요구 충족\n원문 발췌: 확인할 원문"


def claude(target, prompt, sub=False):
    d = {"tool_name": "Agent", "tool_input": {"subagent_type": target, "prompt": prompt}}
    return {**d, "agent_id": "a1"} if sub else d


def codex(target, prompt):
    return {"tool_name": "spawn_agent", "tool_input": {"agent_type": target, "message": prompt}}


def run_utf8(data, *args, cwd=None):
    env = {**os.environ, "PYTHONIOENCODING": "", "PYTHONUTF8": "0"}
    return subprocess.run([sys.executable, "-B", hook.__file__, *args], env=env, capture_output=True, check=True,
                          input=json.dumps(data, ensure_ascii=False).encode("utf-8"), cwd=cwd).stdout.decode("utf-8")


class AgentInput(unittest.TestCase):
    check = staticmethod(sub_input.check_input)

    def test_free_text_denied(self):
        for runner, make in (("claude", claude), ("codex", codex)):
            with self.subTest(runner=runner):
                self.assertIsNotNone(self.check(make("jelly", "이전 대화 요약: ..."), runner))
                self.assertIsNotNone(self.check(make("jelly", INPUT_IN + "\n추가 설명"), runner))
                self.assertIsNone(self.check(make("jelly", INPUT_IN), runner))
                self.assertIsNone(self.check(make("nico", RESEARCH_IN), runner))

    def test_worker_reviewer_take_input_doc_only(self):
        for target in ("jelly", "worker", "ricky", "reviewer"):
            self.assertIsNotNone(self.check(claude(target, STATE_IN), "claude"))
            self.assertIsNone(self.check(claude(target, INPUT_IN), "claude"))

    def test_researcher_takes_research_doc_only(self):
        self.assertIsNotNone(self.check(claude("researcher", INPUT_IN), "claude"))
        self.assertIsNotNone(self.check(claude("researcher", STATE_IN), "claude"))
        self.assertIsNotNone(self.check(claude("researcher", RESEARCH_IN + "\n추가 설명"), "claude"))
        self.assertIsNone(self.check(claude("researcher", RESEARCH_IN), "claude"))

    def test_documenter_record_request_left_to_other_hook(self):
        self.assertIsNone(self.check(claude("pepper", "기록 문구"), "claude"))
        self.assertIsNotNone(self.check(claude("pepper", INPUT_IN), "claude"))

    def test_documenter_takes_state_and_input_doc(self):
        both = STATE_IN + "\n" + INPUT_IN
        self.assertIsNone(self.check(claude("pepper", both), "claude"))
        self.assertIsNone(self.check(claude("documenter", STATE_IN), "claude"))
        self.assertIsNotNone(self.check(claude("pepper", both + "\n입력 문서: log/state/a-input.md"), "claude"))

    def test_caller_detection(self):
        self.assertIsNone(self.check(claude("jelly", "자유 문장", sub=True), "claude"))  # Claude 서브에이전트
        self.assertIsNotNone(self.check(codex("nico", "자유 문장"), "codex"))  # director의 researcher 호출
        self.assertIsNotNone(self.check(codex("jelly", "자유 문장"), "codex"))

    def test_other_tools_ignored(self):
        self.assertIsNone(self.check({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "claude"))

    def test_registered_roles_and_unrelated_agents(self):
        for runner, make in (("claude", claude), ("codex", codex)):
            for target in ("Explore", "general-purpose", "custom-agent", "director", "rio", "assistant", "buddy"):
                with self.subTest(runner=runner, target=target):
                    self.assertIsNone(self.check(make(target, "자유 형식 입력"), runner))
            for target in sub_call.ROLES - sub_call.DOCUMENTER - sub_call.RESEARCHER:
                with self.subTest(runner=runner, target=target):
                    good = INPUT_IN if target in sub_call.WORKER_REVIEWER else STATE_IN
                    self.assertIsNone(self.check(make(target, good), runner))
                    self.assertIsNotNone(self.check(make(target, "자유 형식 입력"), runner))
            for target in sub_call.RESEARCHER:
                self.assertIsNone(self.check(make(target, RESEARCH_IN), runner))
                self.assertIsNotNone(self.check(make(target, "자유 형식 입력"), runner))

    def test_reviewer_explicit_refutation_mode(self):
        invalid = (REFUTE + "\n작업 종류: 반증", REFUTE.replace("반증", "검수", 1),
                   REFUTE + "\n" + INPUT_IN, REFUTE + "\n" + STATE_IN,
                   REFUTE + "\n전체 대화: 이전 대화", REFUTE + "\n도구 출력: 로그",
                   REFUTE + "\n중간 설명: 작업 설명", REFUTE + "\n추론: 사고 과정",
                   REFUTE + "\n이전 대화 요약", REFUTE.replace("작업 종류: 반증\n", ""),
                   "작업 종류: 반증", "작업 종류: 반증\n주장: ")
        for runner, make in (("claude", claude), ("codex", codex)):
            for target in sub_call.REVIEWER:
                with self.subTest(runner=runner, target=target):
                    self.assertIsNone(self.check(make(target, REFUTE), runner))
                    self.assertIsNone(self.check(make(target, INPUT_IN), runner))
                    for prompt in invalid:
                        self.assertIsNotNone(self.check(make(target, prompt), runner), prompt)
        self.assertIsNone(self.check(claude("ricky", REFUTE, sub=True), "claude"))
        self.assertIsNotNone(self.check(claude("ricky", REFUTE + "\n전체 대화: 로그", sub=True), "claude"))

    def test_general_review_and_worker_allow_path_only(self):
        for runner, make in (("claude", claude), ("codex", codex)):
            for target in sub_call.WORKER_REVIEWER:
                for prompt in (INPUT_IN + "\n절: 검수", INPUT_IN + "\n항목: 1", REFUTE if target not in sub_call.REVIEWER else STATE_IN):
                    self.assertIsNotNone(self.check(make(target, prompt), runner))


# 진입 스크립트: 호출 승인과 입력 형식을 한 번에 판정한다.
class Entry(unittest.TestCase):
    def test_root_and_subdirectory_have_same_decisions(self):
        root = Path(hook.__file__).resolve().parents[1]
        for runner, make in (("claude", claude), ("codex", codex)):
            for cwd in (root, root / "hooks"):
                for target, prompt, denied in (("jelly", INPUT_IN, False), ("ricky", STATE_IN, True),
                                               ("pepper", STATE_IN, False), ("nico", RESEARCH_IN, False),
                                               ("ricky", REFUTE, False), ("Explore", "자유 입력", False),
                                               ("director", "자유 입력", False), ("rio", "자유 입력", False),
                                               ("assistant", "자유 입력", False), ("buddy", "자유 입력", False)):
                    with self.subTest(runner=runner, cwd=cwd, target=target):
                        data = {**make(target, prompt), "agent_type": "buddy"}
                        output = run_utf8(data, "--runner", runner, cwd=cwd)
                        self.assertEqual(bool(output), denied)
                        if denied:
                            self.assertEqual(json.loads(output)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_director_call_needs_approval_and_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            for user, prompt, denied in (("승인", INPUT_IN, False), ("승인", "자유 입력", True),
                                         ("다시 생각해", INPUT_IN, True)):
                with self.subTest(user=user, prompt=prompt):
                    path.write_text("\n".join(claude_line(r, t) for r, t in (("assistant", "계획"), ("user", user))) + "\n",
                                    encoding="utf-8")
                    data = {**claude("jelly", prompt), "agent_type": "rio", "transcript_path": str(path)}
                    with patch.object(sub_approval, "ACTIVE", Path(tmp) / ".active"):
                        self.assertEqual(hook.check(data, "claude") is not None, denied)

    def test_deny_output_shape(self):
        out = hook.deny("x")["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "deny"))

    def test_stringio_main_preserves_protocol(self):
        data = codex("reviewer", REFUTE + "\n전체 대화: 로그")
        output = io.StringIO()
        with patch.object(sys, "argv", ["check_tool_use.py", "--runner", "codex"]), \
             patch.object(sys, "stdin", io.StringIO(json.dumps(data))), patch.object(sys, "stdout", output):
            self.assertEqual(hook.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_utf8_stdin_stdout(self):
        out = run_utf8({**claude("jelly", "이전 대화 요약"), "agent_type": "buddy"}, "--runner", "claude")
        self.assertIn("입력 문서", json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(run_utf8({**claude("jelly", INPUT_IN), "agent_type": "buddy"}, "--runner", "claude"), "")


if __name__ == "__main__":
    unittest.main()
