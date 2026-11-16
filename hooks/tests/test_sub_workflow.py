"""네 작업 문서(경과, 명세, 조사, 반증 문서)를 사용하는 단계 게이트의 통합 테스트"""
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
                str(Path(__file__).resolve().parents[1] / "common")]
import check_tool_use
import sub_document
import sub_gate
import sub_docs
STRUCTURE_REASON = sub_docs.structure_reason

TASK = "20261024-120000-test"
APPROVAL = """
- 사용자 승인 상태: 승인
- 사용자 승인 시각: 2026-10-24 12:00:00
- 연결 대상: 명세 문서
- 승인 행동: 구현
- 영향 범위: hooks/
- 범위 밖: 없음
"""
REVIEW_ITEM = "- 검토 1. 도구: 안 1. A (사용자 검토 시각: 2026-10-24 12:00:00)\n"
STATE = f"""# 작업
- 작업 ID: {TASK}
- 작업 단계: 구현
## 문제 정의
- 문제 1. 범위
  - 사용자 확인 시각: 2026-10-24 11:00:00
## 원인 분석
- 검사 누락: fixture 입력의 필수 칸 누락
  - 근거 내용: fixture 입력에서 누락을 재현했다.
  - 출처: hooks/a.py:1
  - 한계: 없음
## 검토 필요
{REVIEW_ITEM}## 프로젝트 환경
Python, 출처: a.py:1
## 기존 흐름
```
hooks/a.py:1 호출 → hooks/b.py:2
```
## 조사 계획
- 제외 범위: 없음
### 조사 1. 도구 (문제 1)
- 질문: 도구
## 승인
### 조사 승인 범위
{APPROVAL}
### 구현 승인 범위
{APPROVAL}
## 중단 사유
- 실행 준비
## 검증 결과
- 2026-10-24 12:00:00: 기능 (정적, 검증)
  - PASS: 정상과 차단 Test Case 통과
  - 남은 문제: 없음
"""
PLAN = f"""# 명세
- 작업 ID: {TASK}
## 요구사항 목록
### 1. 기능
- 문제: 문제 1
- 목표:
  - 검사
- 결과:
  - 차단
- 작업:
  - 구현
- 검증:
  - 확인 (정적)
- 기대 결과:
  - 통과
- 한계:
  - 없음 (출처: a.py:1, 확인 방법: 테스트)
- Test Case:

| 종류 | 입력 | 커맨드 | 기대 결과 |
| --- | --- | --- | --- |
| 차단 | 누락 | `python -V` | 차단 |
| 허용 | 정상 | `python -V` | 통과 |
## 작업 금지 목록
- 사용자 설정
## 작업 계획
### 변경 범위
- 읽기 범위
  - hooks/
- 변경 대상
  - hooks/a.py
- 유지 대상
  - 설정
- 배정 범위
  - worker
- 문구 diff: 코드
- 변경 전 원본: HEAD
- 문서 갱신 대상: docs/a.md:1
### 검증 계획
- 실행 위치: 저장소
- 실제 검증: fixture (확인 수단 출처: a.py:1)
- 검증 커맨드
  - `python -V`
### 잠재적 위험
- 외부 입력 형식 변경 (출처: hooks/a.py:1)
### 진행 순서
1. 구현
### 작업 기준 자료
- a.py:1
## 재구현 요청
없음
## 재검증 요청
없음
"""
REPORT = "조사 1. 도구 (문제 1)\n후보 A 충족, 후보 B 미충족\n제안: A\n출처: a.py:1"
ROUND = "- 주장 1: 지지 | 출처: a.py:1"
LOOP = """## 조사 및 반증 루프
| 회차 | 반증 건수 | 정지 여부 |
| --- | --- | --- |
| 1 | 0 | 정지(반증 0건) |
"""
RESEARCH = f"""# 조사
- 작업 ID: {TASK}
- 확인 상태: 확인
- 확인 시각: 2026-10-24 12:00:00
## 조사 계획
- 제외 범위: 없음
- 대안 검색 기록: 후보 검색 (출처: a.py:1)
### 조사 1. 도구 (문제 1)
- 질문: 도구
## 프로젝트 환경
Python, 출처: a.py:1
### 문제 1 조사 필요 판정
- 방식 결정: 근거 없음
## 기존 흐름
```
hooks/a.py:1 호출 → hooks/b.py:2
```
## 검토 보고
### 검토 1. 도구
| 안 | 기준 |
| --- | --- |
| A | 충족 |
#### 안 1. A
- 적용할 작업: 도구 선택
## 조사 단계 원문
### 최종 조사 원문: 조사 단계
```
{REPORT}
```
"""
REFUTE = f"""# 반증
- 작업 ID: {TASK}
## 주장 목록
- 주장 1: 도구는 파이썬이다 (출처: a.py:1)
{LOOP}
## 회차 기록
### 1회차 반증 원문
```
{ROUND}
```
"""
TEMPLATE_RULE = r"log-(?:status|spec|research|refute) 양식"
STAGE_VALUE = {"plan": "계획", "research": "계획", "refute": "계획", "implement": "구현", "verify": "구현",
               "reimplement": "구현", "reverify": "구현", "complete": "완료"}


# 경과 문서 본문의 작업 단계를 지정 단계 이름에 맞는 값으로 바꾼다.
def staged(stage: str, text: str = STATE) -> str:
    return text.replace("- 작업 단계: 구현", "- 작업 단계: " + STAGE_VALUE[stage], 1)


# 단계별 조건 단위 테스트의 생략형 fixture는 구조 검사와 분리한다.
# 실제 양식 구조와 구현 진입 연결은 test_entry_guards에서 검사한다.
class StageConditions(unittest.TestCase):
    def setUp(self):
        guard = patch.object(sub_docs, "structure_reason", return_value=None)
        guard.start()
        self.addCleanup(guard.stop)


class Gate(StageConditions):
    # 임시 저장소와 .agent-logs/task 작업 문서 네 개를 준비한다.
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / ".agent-logs/task" / TASK
        self.folder.mkdir(parents=True)
        for kind, body in (("status", STATE), ("spec", PLAN), ("research", RESEARCH), ("refute", REFUTE)):
            (self.folder / f"{kind}.md").write_text(body, encoding="utf-8")

    # 완전한 문서가 준비된 모든 단계를 통과하는지 확인한다.
    def test_valid_documents_pass_all_ready_stages(self):
        for stage in ("plan", "research", "refute", "implement", "verify", "complete"):
            with self.subTest(stage=stage):
                (self.folder / "status.md").write_text(staged(stage), encoding="utf-8")
                sub_gate.validate(self.root, TASK, stage)

    # 구현 단계는 조사 문서와 승인 절 값 없이 실행하며 명세 및 검토 필요 검사는 유지한다.
    def test_progress_without_research_or_approval_value(self):
        status = STATE.replace("사용자 승인 상태: 승인", "사용자 승인 상태: 승인 대기")
        (self.folder / "status.md").write_text(status, encoding="utf-8")
        (self.folder / "research.md").unlink()
        for stage in ("implement", "verify"):
            with self.subTest(stage=stage):
                sub_gate.validate(self.root, TASK, stage)
        (self.folder / "spec.md").write_text(PLAN.replace("- 목표:\n  - 검사", "- 목표:"), encoding="utf-8")
        with self.assertRaises(ValueError):
            sub_gate.validate(self.root, TASK, "implement")

        # 경과 문서의 이전 `## 문제 정의` 절은 판정에 쓰지 않으므로 문제 연결 줄과 경과 문서 문제 항목은 결과를 바꾸지 않는다.
        state = sub_document.Document(status)
        linked = sub_document.Document(PLAN)
        self.assertEqual(sub_gate.check_plan(linked, state), ["기능"])
        self.assertEqual(sub_gate.check_plan(sub_document.Document(PLAN.replace("- 문제: 문제 1\n", "")), state), ["기능"])
        extra = sub_document.Document(status.replace("## 문제 정의\n", "## 문제 정의\n- 문제 4. 두번째\n  - 사용자 확인 시각: 2026-10-24 11:00:00\n"))
        self.assertEqual(sub_gate.check_plan(linked, extra), ["기능"])

    # 구현 문서는 실행과 완료 검사를 유지하며 실패한 완료 요청은 구현에 남긴다.
    def test_single_stage_completion(self):
        from sub_approval import apply_stage
        import record_stage
        status = self.folder / "status.md"
        body = STATE
        status.write_text(body, encoding="utf-8")
        sub_gate.validate(self.root, TASK, "implement")
        self.assertEqual(apply_stage(status, "완료"), "완료")
        self.assertRegex(status.read_text(encoding="utf-8"), r"- 완료 시각: \d{4}\.\d\d\.\d\d \d\d:\d\d:\d\d")
        sub_gate.validate(self.root, TASK, "complete")
        status.write_text(body.replace("  - PASS:", "  - FAIL:"), encoding="utf-8")
        message = record_stage.decide_stage({"prompt": f"[complete {TASK}]", "cwd": str(self.root)})
        self.assertIn("구현 단계를 유지합니다", message)
        self.assertEqual(apply_stage(status, None), "구현")
        stopped = body.replace("## 중단 사유\n", "## 중단 사유\n- 중단 유형: 선택 필요\n")
        status.write_text(stopped, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "중단"):
            sub_gate.validate(self.root, TASK, "implement")
        self.assertEqual(apply_stage(status, "완료"), "구현")

    # 계획의 환경과 흐름은 경과 문서에서 읽으며 조사와 명세가 없어도 검사할 수 있다.
    def test_plan_without_research_and_spec(self):
        (self.folder / "status.md").write_text(staged("plan"), encoding="utf-8")
        (self.folder / "research.md").unlink()
        (self.folder / "spec.md").unlink()
        sub_gate.validate(self.root, TASK, "plan")

    # 계획 단계의 구현 배정은 승인 값과 관계없이 거부하며 조사 문서 누락 오류로 오판하지 않는다.
    def test_plan_rejects_dispatch_without_research(self):
        (self.folder / "status.md").write_text(staged("plan"), encoding="utf-8")
        (self.folder / "research.md").unlink()
        with self.assertRaisesRegex(ValueError, "작업 단계"):
            sub_gate.validate(self.root, TASK, "implement")

    # 제외 범위의 출처가 파일:행이면 통과하고 형식이 아니면 거부하며, 없음은 통과한다.
    def test_exclusion_source_format(self):
        research = lambda line: sub_document.Document(RESEARCH.replace("- 제외 범위: 없음", line))
        sub_gate.research_items(research("- 제외 범위: 없음"))
        sub_gate.research_items(research("- 제외 범위: 사전 실측 (출처: a.py:1)"))
        with self.assertRaises(ValueError):
            sub_gate.research_items(research("- 제외 범위: 사전 실측 (출처: 사용자 답)"))

    # 작업 단계는 plan, implement, complete뿐이고 검증 단계 이름은 거부하는지 확인한다.
    def test_no_review_stage(self):
        self.assertEqual(sub_gate.STAGES, ("plan", "implement", "complete"))
        self.assertFalse({"review", "rereview"} & set(sub_gate.STAGE_OF))
        with self.assertRaisesRegex(ValueError, "지원하지 않는 단계"):
            sub_gate.validate(self.root, TASK, "review")

    # 구현 단계의 구현 배정은 통과하고 계획 단계이면 deny되는지 확인한다.
    def test_hook_accepts_ready_call_and_denies_wrong_stage(self):
        data = {"tool_name": "spawn_agent", "agent_type": "director", "cwd": str(self.root),
                "tool_input": {"agent_type": "worker", "fork_turns": "none",
                               "message": f"task: implement\ninput: .agent-logs/task/{TASK}/spec.md"}}
        with patch.object(sub_docs, "project_root", return_value=self.root), \
                patch.object(check_tool_use, "check_agent_call", return_value=None):
            self.assertIsNone(check_tool_use.check(data, "codex"))
            (self.folder / "status.md").write_text(staged("plan"), encoding="utf-8")
            self.assertIn("작업 단계 [계획]", check_tool_use.check(data, "codex"))

    # 빈 조사 원문과 미해결 중단 판정이 단계 전환을 막는지 확인한다.
    def test_empty_research_and_unresolved_stop_block(self):
        (self.folder / "status.md").write_text(staged("refute"), encoding="utf-8")
        (self.folder / "research.md").write_text(RESEARCH.replace(REPORT, "기록 없음"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "최종 조사 원문"):
            sub_gate.validate(self.root, TASK, "refute")
        (self.folder / "status.md").write_text(staged("research").replace("실행 준비", "중단 판정 미해결"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "미해결 중단 판정"):
            sub_gate.validate(self.root, TASK, "research")

    # 양식처럼 하위 절로 쓴 프로젝트 환경과 중단 사유 항목을 절 본문으로 읽는지 확인한다.
    def test_template_subsections_are_section_body(self):
        state = staged("plan").replace("## 프로젝트 환경\nPython, 출처: a.py:1\n", "## 프로젝트 환경\n### 언어\nPython\n출처: `a.py:1`\n")
        (self.folder / "status.md").write_text(state, encoding="utf-8")
        sub_gate.validate(self.root, TASK, "plan")
        (self.folder / "status.md").write_text(
            staged("research").replace("- 실행 준비", "### 실행 준비\n- 결정: 진행\n### 중단 판정 미해결\n- 담당: hook"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "미해결 중단 판정"):
            sub_gate.validate(self.root, TASK, "research")

    # 반증 문서가 없으면 반증 단계를 막는지 확인한다.
    def test_refute_needs_document(self):
        (self.folder / "status.md").write_text(staged("refute"), encoding="utf-8")
        (self.folder / "refute.md").unlink()
        with self.assertRaisesRegex(ValueError, "반증 문서가 없습니다"):
            sub_gate.validate(self.root, TASK, "refute")

    # 최종 검증이 PASS가 아니면 완료를 막는지 확인한다.
    def test_complete_requires_final_review(self):
        (self.folder / "status.md").write_text(staged("complete").replace("PASS:", "FAIL:"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "PASS가 아닙니다"):
            sub_gate.validate(self.root, TASK, "complete")

    # 양식대로 비운 재구현 요청 절은 통과하고 항목이 든 절은 막는지 확인한다.
    def test_complete_empty_rework_section(self):
        (self.folder / "status.md").write_text(staged("complete"), encoding="utf-8")
        empty = PLAN.replace("## 재구현 요청\n없음\n", "## 재구현 요청\n<!-- 주석 -->\n- 없음\n### 재작업 범위\n- 없음\n")
        (self.folder / "spec.md").write_text(empty, encoding="utf-8")
        sub_gate.validate(self.root, TASK, "complete")
        pending = PLAN.replace("## 재구현 요청\n없음\n", "## 재구현 요청\n- 1. 기능: a.py\n")
        (self.folder / "spec.md").write_text(pending, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            sub_gate.validate(self.root, TASK, "complete")

    # 코드 표기 안의 꺾쇠를 자리표시자로 보지 않는지 확인한다.
    def test_literal_angle_brackets_are_not_placeholders(self):
        self.assertTrue(sub_document.filled("`shared/skills/<이름>/template.md` 수정"))
        self.assertFalse(sub_document.filled("<작업 내용>"))
        self.assertTrue(sub_document.filled("shared/skills/<워크플로 스킬>/template.md 추가"))
        text = PLAN.replace("기대 결과:\n  - 통과", "기대 결과:\n  - `shared/skills/<이름>/template.md`에 규칙이 기록된다")
        self.assertEqual(sub_gate.check_plan(sub_document.Document(text)), ["기능"])
        with self.assertRaisesRegex(ValueError, "`기대 결과` 칸이 비어 있습니다"):
            sub_gate.check_plan(sub_document.Document(PLAN.replace("기대 결과:\n  - 통과", "기대 결과:\n  - <관측할 동작>")))

    # 실제 검증에 확인 수단 출처가 없으면 막고 있으면 통과시키는지 확인한다.
    def test_actual_check_requires_source(self):
        with self.assertRaisesRegex(ValueError, sub_gate.R_CHECK_ACTUAL):
            sub_gate.check_plan(sub_document.Document(PLAN.replace("fixture (확인 수단 출처: a.py:1)", "사용자 화면으로 확인")))
        self.assertEqual(sub_gate.check_plan(sub_document.Document(PLAN)), ["기능"])

    # 명세 문서의 출처 없는 한계 줄은 해당 양식 대신 log-spec 양식 이름으로 차단하는지 확인한다.
    def test_plan_constraint_reason_names_template(self):
        text = PLAN.replace("기대 결과:\n  - 통과", "기대 결과:\n  - 통과\n- 한계: 외부 서비스 중단")
        with self.assertRaisesRegex(ValueError, r"log-spec 양식을 따르십시오\.$") as raised:
            sub_gate.check_plan(sub_document.Document(text))
        self.assertNotIn("해당 양식", str(raised.exception))

    # 제목 수준을 바꾼 작업 문서는 새 구조 검사에서 거부한다.
    def test_heading_levels_must_match_template(self):
        for kind, body in (("status", STATE), ("spec", PLAN), ("research", RESEARCH), ("refute", REFUTE)):
            body = staged("complete", body) if kind == "status" else body
            (self.folder / f"{kind}.md").write_text(re.sub(r"^#{1,6} ", "# ", body, flags=re.M), encoding="utf-8")
        with patch.object(sub_docs, "structure_reason", wraps=STRUCTURE_REASON):
            with self.assertRaises(ValueError):
                sub_gate.validate(self.root, TASK, "complete")

    # 첫 사용자 메시지가 비거나 암호문이면 rollout 첫 줄의 task_name에서 검증 요청 두 줄을 복원한다.
    def test_first_prompt_from_task_name(self):
        import json
        import sub_role
        first = json.dumps({"type": "session_meta", "payload": {"agent_path": "/root/verify__20261103_063721_response_check_cost"}})
        expected = "task: verify\ninput: .agent-logs/task/20261103-063721-response-check-cost/spec.md"
        rollout = self.root / "rollout.jsonl"
        rollout.write_text(first + "\n", encoding="utf-8")
        self.assertEqual(sub_role.first_prompt(str(rollout)), expected)

        # 암호문 첫 메시지도 같은 결과이고, 평문 첫 메시지는 그대로 반환한다.
        message = lambda text: json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}})
        rollout.write_text(first + "\n" + message("gAAAAABabc-_def==") + "\n", encoding="utf-8")
        self.assertEqual(sub_role.first_prompt(str(rollout)), expected)
        rollout.write_text(first + "\n" + message("task: implement") + "\n", encoding="utf-8")
        self.assertEqual(sub_role.first_prompt(str(rollout)), "task: implement")

    # 폴더 이름과 한글 슬래시 표현은 통과하고 사건 문서 파일 경로는 기록 정보로 잡는지 확인한다.
    def test_record_leak_line(self):
        import sub_input
        root = sub_docs.ROOT
        line = "├── chat_state/   .agent-logs/incident 폴더   검증/확인"
        self.assertIsNone(sub_docs.record_leak(line))
        self.assertIsNone(sub_input.session_leak(line, str(root)))
        leak = "쓰기: .agent-logs/incident/20261026-ab12cd34.md"
        self.assertEqual(sub_docs.record_leak(leak), leak)
        self.assertIn(".agent-logs/incident/", sub_input.session_leak(leak, str(root)))

    # 반증 문서의 회차 표 밖 행은 회차로 세지 않는지 확인한다.
    def test_loop_count_counts_only_loop_section_rows(self):
        text = REFUTE + "\n## 메모\n| 7 | x | y |\n"
        self.assertEqual(sub_docs.loop_count(text), 1)

    # 항목별로 가장 최근 조사 결과를 기준으로 삼는지 확인한다.
    def test_partial_research_keeps_latest_per_item(self):
        second = REPORT.replace("조사 1. 도구", "조사 2. 설정")
        updated = second.replace("제안: A", "제안: B")
        accumulated = REPORT + "\n" + second + "\n\n---\n\n" + updated
        sub_gate.check_research_result(accumulated, ["조사 1. 도구", "조사 2. 설정"])
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            sub_gate.check_research_result(accumulated, ["조사 3. 부재"])
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            sub_gate.check_research_result(accumulated + "\n\n---\n\n조사 2. 설정\n후보 충족 출처: a.py:1",
                                       ["조사 1. 도구", "조사 2. 설정"])
        (self.folder / "status.md").write_text(staged("refute"), encoding="utf-8")
        (self.folder / "research.md").write_text(RESEARCH.replace(REPORT, accumulated), encoding="utf-8")
        sub_gate.validate(self.root, TASK, "refute")

    # 구현 승인 범위와 이전 명칭 실행 승인 범위가 모두 같은 절로 읽히는지 확인한다.
    def test_approval_section_reads_both_names(self):
        for title in ("구현 승인 범위", "실행 승인 범위"):
            text = f"# t\n\n## {title}\n\n- 사용자 승인 상태: 승인\n"
            self.assertIn("승인 상태: 승인", sub_document.Document(text).section("구현 승인 범위"))
            self.assertIn("승인 상태: 승인", sub_docs.section(text, "구현 승인 범위"))

    # 승인 대상 요구사항과 연결되지 않은 사전 실측 대기는 빼고 연결된 항목은 남기는지 확인한다.
    def test_measure_pending_skips_placeholder_item(self):
        text = "## 실측 필요\n\n- <항목>\n  - 시점: <사전 | 사후>\n- 가\n  - 시점: 사전\n"
        self.assertEqual(sub_docs.measure_pending(text), ["가"])

    # 없음 선언은 실측 대기가 아니며 실제 미완료 항목은 계속 남긴다.
    def test_measure_pending_skips_none(self):
        text = "## 모델 호출 실측 필요\n- 없음\n- 실제 항목\n  - 시점: 사전\n  - 상태: 실측 대기\n"
        self.assertEqual(sub_docs.measure_pending(text), ["실제 항목"])

    # 차단 기능 없는 요구사항만 허용 행 하나로 충분하고 기존 문서는 양쪽 행을 요구한다.
    def test_test_cases_follow_blocking_behavior(self):
        body = sub_document.requirements(sub_document.Document(PLAN))[0][1]
        allowed = body.replace("| 차단 | 누락 | `python -V` | 차단 |\n", "")
        self.assertTrue(sub_gate.case_gaps("1. 기능", allowed))
        self.assertTrue(sub_gate.case_gaps("1. 기능", allowed + "\n  - 차단 동작: 있음\n"))
        self.assertEqual(sub_gate.case_gaps("1. 기능", allowed + "\n  - 차단 동작: 없음\n"), [])
        self.assertTrue(sub_gate.case_gaps("1. 기능", "  - 차단 동작: 없음\n"))

    def test_measure_pending_filters_unrelated_items(self):
        text = ("## 구현 승인 범위\n\n- 연결 대상: 요구사항 1, 3\n\n### 실측 필요\n\n"
                "- 가\n  - 시점: 사전\n  - 요구사항: 2\n  - 상태: 실측 대기\n"
                "- 나\n  - 시점: 사전\n  - 요구사항: 3\n  - 상태: 실측 대기\n"
                "- 다\n  - 시점: 사전\n  - 상태: 실측 대기\n")
        self.assertEqual(sub_docs.measure_pending(text, targets=sub_docs.approval_targets(text)), ["나", "다"])
        self.assertEqual(sub_docs.measure_pending(text), ["가", "나", "다"])

    # 현재 명세에 없는 요구사항 실측은 이전 승인 연결 대상과 무관하게 제외한다.
    def test_measurements_use_current_plan(self):
        text = STATE
        text += "\n## 실측 필요\n- 무관 항목\n  - 요구사항: 2\n  - 시점: 사전\n  - 상태: 실측 대기\n"
        sub_gate.check_review_left(sub_document.Document(text), False, plan=sub_document.Document(PLAN))
        with self.assertRaisesRegex(ValueError, "실측"):
            sub_gate.check_review_left(sub_document.Document(text.replace("요구사항: 2", "요구사항: 1")), False, plan=sub_document.Document(PLAN))

    # 주장 목록 줄은 통과하고 판정어 뒤 형식이 틀린 줄은 거부하는지 확인한다.
    def test_refutation_claim_list_line_passes(self):
        sub_gate.check_refutation("- 주장 1: 사실 하나\n- 주장 1: 지지 | 출처: a.py:1")
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            sub_gate.check_refutation("- 주장 1: 지지 출처 없음")

    # 다른 항목의 표시로 항목 검사를 통과하지 못하는지 확인한다.
    def test_research_labels_checked_per_item(self):
        message = "조사 1. 도구\n제안: A\n후보 충족\n출처: a.py:1\n조사 2. 설정\n후보 A"
        sub_gate.check_research_result(message, ["조사 1. 도구"])
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            sub_gate.check_research_result(message, ["조사 2. 설정"])

    # 주장 속 조사 참조와 코드·인용 예시는 제목이나 다른 항목의 근거로 사용하지 않는다.
    def test_research_references_and_examples_are_not_item_boundaries(self):
        first = ("### 조사 1. 도구\n- 주장 1: 조사 1. 도구의 후보 A는 충족한다.\n"
                 "후보 A 충족\n출처: a.py:1\n제안: A\n"
                 "```text\n조사 2. 설정\n후보 B 충족\n제안: B\n출처: b.py:1\n```\n"
                 "> 조사 2. 설정\n> 제안: B\n")
        sub_gate.check_research_result(first, ["조사 1. 도구"])
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            sub_gate.check_research_result(first, ["조사 2. 설정"])
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            sub_gate.check_research_result(first + "조사 2. 설정\n후보 B 충족\n출처: b.py:1", ["조사 2. 설정"])
        same_round = "조사 1. 도구\n후보 A 충족\n출처: a.py:1\n\n조사 1. 도구\n제안: A"
        sub_gate.check_research_result(same_round, ["조사 1. 도구"])
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            sub_gate.check_research_result(same_round + "\n\n---\n\n조사 1. 도구\n후보 A 충족\n출처: a.py:1", ["조사 1. 도구"])

    # 구 라벨만 있는 조사 원문을 막는지 확인한다. 구 라벨은 저장소 검색에 걸리지 않게 이스케이프로 쓴다.
    def test_old_label_is_blocked(self):
        old = REPORT.replace("제안: A", chr(0xad8c) + chr(0xace0) + ": A")
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            sub_gate.check_research_result(old, ["조사 1. 도구"])

    # 실행 승인 가정 검사가 남긴 미해결 줄은 막지 않고 그 밖의 미해결 줄은 막는지 확인한다.
    def test_transition_unresolved_line_is_ignored(self):
        line = "중단 판정 미해결: 경과 문서 'status.md'에 구현 승인을 기록하면 차단됩니다. 사유: x"
        (self.folder / "status.md").write_text(STATE.replace("실행 준비", line), encoding="utf-8")
        sub_gate.validate(self.root, TASK, "implement")
        (self.folder / "status.md").write_text(STATE.replace("실행 준비", "중단 판정 미해결: 충족 불가"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "미해결 중단 판정"):
            sub_gate.validate(self.root, TASK, "implement")



# 새 양식 본문을 기존 절 이름과 칸 이름으로 바꾼다. 기존 이름은 sub_docs.legacy로 구한다.
def old(text: str) -> str:
    for term in ("## 검증 결과", "(정적, 검증)", "- 검증:", "### 검증 계획", "## 재검증 요청"):
        text = text.replace(term, sub_docs.legacy(term))
    return text


# 변경 범위 양식을 대상별 절과 `(요구사항 <번호>)` 표기로 바꾼 새 양식 문서이다.
SCOPE = PLAN[PLAN.index("### 변경 범위"):PLAN.index("### 검증 계획")]
NEW_SCOPE = """### 배정 범위
- worker: hooks/ (요구사항 1)
### 조회 대상
- hooks/a.py
### 변경 대상
- hooks/a.py: 교체 (요구사항 1)
### 생성 대상
- 없음
### 문서 갱신 대상
- docs/a.md:1 (요구사항 1)
### 작업 유지 대상
- 설정
### 작업 금지 대상
- 사용자 설정
"""
NEW_PLAN = PLAN.replace(SCOPE, NEW_SCOPE)
NEW_STATE = STATE.replace("기능 (정적, 검증)", "기능 (요구사항 1) (정적, 검증)")
NEW_RESEARCH = RESEARCH


class NumberedGate(StageConditions):
    # 새 양식 작업 문서 네 개를 준비한다.
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / ".agent-logs/task" / TASK
        self.folder.mkdir(parents=True)
        self.put(status=NEW_STATE, spec=NEW_PLAN, research=NEW_RESEARCH, refute=REFUTE)

    # 문서 본문을 쓰는 보조 함수이다.
    def put(self, **docs):
        for kind, body in docs.items():
            (self.folder / f"{kind}.md").write_text(body, encoding="utf-8")

    # 번호 표기가 갖춰진 문서는 완료 단계까지 통과한다.
    def test_numbered_documents_pass(self):
        for stage in ("plan", "implement", "complete"):
            self.put(status=staged(stage, NEW_STATE))
            sub_gate.validate(self.root, TASK, stage)

    # 번호 없음, 목록에 없는 번호, 이름 표기를 막고 번호 표기는 통과한다.
    def test_plan_items_need_valid_numbers(self):
        for bad in ("- hooks/a.py: 교체", "- hooks/a.py: 교체 (요구사항 9)", "- hooks/a.py: 교체 (요구사항 기능)"):
            self.put(spec=NEW_PLAN.replace("- hooks/a.py: 교체 (요구사항 1)", bad))
            with self.assertRaisesRegex(ValueError, "요구사항 <번호>"):
                sub_gate.validate(self.root, TASK, "implement")
        self.put(spec=NEW_PLAN.replace("(요구사항 1)", "(요구사항 1, 1)"))
        sub_gate.validate(self.root, TASK, "implement")
        self.put(spec=NEW_PLAN.replace("## 재구현 요청\n없음", "## 재구현 요청\n- hooks/a.py"))
        with self.assertRaisesRegex(ValueError, "재구현 요청"):
            sub_gate.validate(self.root, TASK, "implement")

    # 배정 범위는 알려진 담당 역할만 허용하고, 삭제와 통합 목록 항목은 `파일:행` 근거를 요구한다.
    def test_assignment_roles_and_list_sources(self):
        self.put(spec=NEW_PLAN.replace("- worker: hooks/", "- coder: hooks/"))
        with self.assertRaisesRegex(ValueError, "배정 범위의 담당"):
            sub_gate.validate(self.root, TASK, "implement")
        listed = NEW_PLAN.replace("### 문서 갱신 대상", "### 삭제 목록\n- {item} (요구사항 1)\n### 문서 갱신 대상")
        self.put(spec=listed.format(item="a.md 문장"))
        with self.assertRaisesRegex(ValueError, "삭제 목록"):
            sub_gate.validate(self.root, TASK, "implement")
        self.put(spec=listed.format(item="a.md:3 문장"))
        sub_gate.validate(self.root, TASK, "implement")

    # 미해결 중단 판정과 사전 실측 대기는 서로 다른 사유로 막고, 한쪽이 풀려도 다른 쪽은 계속 차단한다.
    def test_stop_and_measurement_blocks_are_separate(self):
        waiting = "\n## 실측 필요\n- 재현\n  - 시점: 사전\n  - 상태: 실측 대기\n"
        self.put(status=NEW_STATE + waiting)
        with self.assertRaisesRegex(ValueError, "실측이 끝나지 않은"):
            sub_gate.validate(self.root, TASK, "implement")
        self.put(status=(NEW_STATE + waiting).replace("실행 준비", "중단 판정 미해결"))
        with self.assertRaisesRegex(ValueError, "미해결 중단 판정"):
            sub_gate.validate(self.root, TASK, "implement")
        self.put(status=NEW_STATE.replace("실행 준비", "중단 판정 미해결"))
        with self.assertRaisesRegex(ValueError, "미해결 중단 판정"):
            sub_gate.validate(self.root, TASK, "implement")

    # 조사 항목에는 `(문제 <번호>)`, 검증 결과 항목에는 요구사항 번호 표기를 요구한다.
    def test_research_and_review_items_need_numbers(self):
        for title in ("### 조사 1. 도구 (요구사항 1)", "### 조사 1. 도구"):
            self.put(status=staged("plan", NEW_STATE).replace("### 조사 1. 도구 (문제 1)", title))
            with self.assertRaises(ValueError):
                sub_gate.validate(self.root, TASK, "plan")
        self.put(status=staged("research", NEW_STATE), research=NEW_RESEARCH.replace("### 조사 1. 도구 (문제 1)", "### 조사 1. 도구 (문제 7)"))
        with self.assertRaises(ValueError):
            sub_gate.validate(self.root, TASK, "research")
        self.put(research=NEW_RESEARCH, status=staged("complete", NEW_STATE).replace("기능 (요구사항 1) (정적", "기능 (정적"))
        with self.assertRaisesRegex(ValueError, "검증 결과"):
            sub_gate.validate(self.root, TASK, "complete")
        self.put(status=staged("complete", NEW_STATE).replace("(요구사항 1) (정적", "(요구사항 2) (정적"))
        with self.assertRaisesRegex(ValueError, "검증 결과"):
            sub_gate.validate(self.root, TASK, "complete")


LIST_STATE = NEW_STATE.replace("## 승인\n", "## 요구사항\n### 요구사항 목록\n1. 기능: 변경\n## 승인\n", 1)


class SpecLessGate(StageConditions):
    # 명세 문서 없이 경과 문서 요구사항 목록과 조사 문서만 있는 작업 폴더를 준비한다.
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / ".agent-logs/task" / TASK
        self.folder.mkdir(parents=True)
        self.put(status=LIST_STATE, research=NEW_RESEARCH, refute=REFUTE)

    # 문서 본문을 쓰는 보조 함수이다.
    def put(self, **docs):
        for kind, body in docs.items():
            (self.folder / f"{kind}.md").write_text(body, encoding="utf-8")

    # 명세 문서가 없어도 계획 단계와 조사 배정을 통과하고, 조사 승인은 전환 검사 대상이 아니다.
    def test_plan_without_spec(self):
        self.put(status=staged("plan", LIST_STATE))
        for stage in ("plan", "research"):
            sub_gate.validate(self.root, TASK, stage)
        old = LIST_STATE.replace("사용자 승인 상태: 승인", "사용자 승인 상태: 대기", 1)
        self.assertIsNone(sub_gate.transition_reason(self.folder / "status.md", LIST_STATE, old, ""))

    # 구현 승인 전환은 원인 분석만 확인하고, 원인 분석 출처가 없으면 차단한다.
    def test_execute_transition_checks_causes(self):
        state = staged("plan", LIST_STATE.replace("1. 기능: 변경", "1. 기능: 변경\n2. 추가: 변경"))
        status = self.folder / "status.md"
        head, _, tail = state.rpartition("사용자 승인 상태: 승인")
        old_execute = head + "사용자 승인 상태: 대기" + tail
        self.assertIsNone(sub_gate.transition_reason(status, state, old_execute, NEW_PLAN))
        for bad in ("  - 출처: hooks/a.py:1\n", "  - 근거 출처: hooks/a.py:1\n"):
            broken = state.replace("  - 출처: hooks/a.py:1\n", "" if bad.startswith("  - 출처") else bad)
            self.assertRegex(sub_gate.transition_reason(status, broken, old_execute, NEW_PLAN) or "", TEMPLATE_RULE)
        limited = state.replace("  - 한계: 없음", "  - 한계: 외부 API 응답 형식이 바뀌면 다시 확인한다")
        self.assertRegex(sub_gate.transition_reason(status, limited, old_execute, NEW_PLAN) or "", TEMPLATE_RULE)
        sourced = limited.replace("다시 확인한다", "다시 확인한다 (출처: https://example.com/api)")
        self.assertIsNone(sub_gate.transition_reason(status, sourced, old_execute, NEW_PLAN))

    # 계획은 요구사항 목록이 자리표시여도 통과하고, 기존 흐름 코드 블록이나 프로젝트 환경이 없으면 차단한다.
    def test_plan_without_requirement_list(self):
        placeholder = staged("plan", NEW_STATE).replace("## 승인\n", "## 요구사항\n### 요구사항 목록\n1. <요구사항 이름>: <결과>\n## 승인\n", 1)
        self.put(status=placeholder)
        sub_gate.validate(self.root, TASK, "plan")
        self.put(status=placeholder.replace("## 기존 흐름\n```\nhooks/a.py:1 호출 → hooks/b.py:2\n```\n", ""))
        with self.assertRaises(ValueError):
            sub_gate.validate(self.root, TASK, "plan")
        self.put(status=placeholder.replace("Python, 출처: a.py:1", ""))
        with self.assertRaises(ValueError):
            sub_gate.validate(self.root, TASK, "plan")

    # 조사 배정의 조사 항목 번호가 조사 필요 판정 번호에 없으면 차단한다.
    def test_problem_number_must_be_judged(self):
        self.put(status=staged("research", LIST_STATE), research=NEW_RESEARCH.replace("(문제 1)", "(문제 7)"))
        with self.assertRaises(ValueError):
            sub_gate.validate(self.root, TASK, "research")

    # 명세 문서가 없으면 실행 이후 단계를 차단한다.
    def test_execute_needs_spec(self):
        with self.assertRaisesRegex(ValueError, "명세 문서가 없습니다"):
            sub_gate.validate(self.root, TASK, "implement")

    # 두 번호 집합이 다르면 막고, 같거나 경과 문서에 목록이 없으면 통과한다.
    def test_requirement_numbers_match(self):
        self.put(spec=NEW_PLAN)
        sub_gate.validate(self.root, TASK, "implement")
        self.put(status=LIST_STATE.replace("1. 기능: 변경", "1. 기능: 변경\n2. 추가: 변경"))
        with self.assertRaisesRegex(ValueError, "경과 문서 요구사항 목록 번호와 다릅니다"):
            sub_gate.validate(self.root, TASK, "implement")
        self.put(status=NEW_STATE)
        sub_gate.validate(self.root, TASK, "implement")


class MultiItemRequest(StageConditions):
    # 항목 여러 개의 요청 절을 넣고 해당 단계 검사를 실행한다.
    def check(self, stage, title, key, second):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        folder = root / ".agent-logs/task" / TASK
        folder.mkdir(parents=True)
        extra = "  - 커맨드: python -m unittest\n" if key == "판정 기준" else ""
        detail = f"  - {key}: {second}\n{extra}" if second is not None else ""
        items = f"- hooks/a.py: 교체 (요구사항 1)\n  - {key}: 첫 동작이 바뀐다\n{extra}- hooks/b.py: 교체 (요구사항 1)\n{detail}"
        plan = NEW_PLAN.replace(f"## {title}\n없음\n", f"## {title}\n{items}")
        for kind, body in dict(status=NEW_STATE, spec=plan, research=NEW_RESEARCH, refute=REFUTE).items():
            (folder / f"{kind}.md").write_text(body, encoding="utf-8")
        sub_gate.validate(root, TASK, stage)

    # 재작업 기대 결과와 재검증 근거의 누락 및 자리표시자를 차단한다.
    def test_multi_item_request(self):
        for stage, title, key in (("reimplement", "재구현 요청", "기대 결과"), ("reverify", "재검증 요청", "판정 기준")):
            self.check(stage, title, key, "둘째 동작이 바뀐다")
            for value in ("", "<수정 후 관측할 동작>"):
                with self.subTest(stage=stage, value=value), self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
                    self.check(stage, title, key, value)

    # 첫 대상의 기대 결과가 있어도 다음 대상에서 줄 전체가 빠지면 배정을 차단한다.
    def test_rework_second_item_missing_expected_line(self):
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            self.check("reimplement", "재구현 요청", "기대 결과", None)

    # 명세 양식 형식의 요청은 통과하고 콜론 없는 재검증 요청은 차단한다.
    def test_spec_template_format(self):
        def run(stage, title, key, item):
            root = Path(tempfile.mkdtemp())
            self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
            folder = root / ".agent-logs/task" / TASK
            folder.mkdir(parents=True)
            detail = f"  - {key}: 입력에 맞는 결과가 기록된다\n" if key else ""
            detail += "  - 커맨드: python -m unittest\n" if key == "판정 기준" else ""
            plan = NEW_PLAN.replace(f"## {title}\n없음\n", f"## {title}\n{item}\n{detail}")
            for kind, body in dict(status=NEW_STATE, spec=plan, research=NEW_RESEARCH, refute=REFUTE).items():
                (folder / f"{kind}.md").write_text(body, encoding="utf-8")
            sub_gate.validate(root, TASK, stage)

        run("reimplement", "재구현 요청", "기대 결과", "- hooks/a.py: 10-20행 (요구사항 1)")
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            run("reimplement", "재구현 요청", "", "- hooks/a.py: 10-20행 (요구사항 1)")
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            run("reimplement", "재구현 요청", "기대 결과", "- 설명 목록 (요구사항 1)")
        run("reverify", "재검증 요청", "판정 기준", "- hooks/a.py: 기능 판정 (요구사항 1)")
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            run("reverify", "재검증 요청", "판정 기준", "- 기능 판정 (요구사항 1)")
        # 판정 기준만 있고 커맨드 칸이 없거나 필요한 근거 칸만 있으면 차단한다.
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            run("reverify", "재검증 요청", "필요한 근거", "- hooks/a.py: 기능 판정 (요구사항 1)")


class ReviewStatus(StageConditions):
    # 허용 값은 코드 정의를 따르고 비허용 값은 차단한다.
    def test_allowed_values_are_independent_of_skill_comment(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        task = folder / ".agent-logs/task" / TASK
        task.mkdir(parents=True)
        for kind, body in (("status", NEW_STATE), ("spec", NEW_PLAN), ("research", NEW_RESEARCH), ("refute", REFUTE)):
            (task / f"{kind}.md").write_text(body, encoding="utf-8")
        sub_gate.validate(folder, TASK, "implement")

        # 실측 필요 항목은 사전이면 실행 단계를, 사후면 완료 단계를 막고 실측 완료면 통과한다.
        measure = "\n## 실측 필요\n- 재현\n  - 시점: {when}\n  - 상태: {status}\n"
        for when, status, stage, blocked in (("사전", "실측 대기", "implement", True), ("사전", "실측 완료", "implement", False),
                                            ("사후", "실측 대기", "implement", False), ("사후", "실측 대기", "complete", True),
                                            ("사후", "실측 완료", "complete", False)):
            (task / "status.md").write_text(staged(stage, NEW_STATE) + measure.format(when=when, status=status), encoding="utf-8")
            if blocked:
                with self.assertRaisesRegex(ValueError, "실측"):
                    sub_gate.validate(folder, TASK, stage)
            else:
                sub_gate.validate(folder, TASK, stage)

        # 확정 답이 없거나 형식이 맞지 않는 검토 필요 항목은 구현 단계를 막는다.
        for value in ("- 검토 1. 도구\n", "- 검토 1. 도구: 안 1. A\n", "- 검토 1. 도구: 안 1. A (사용자 검토 시각: 어제)\n"):
            (task / "status.md").write_text(NEW_STATE.replace(REVIEW_ITEM, value), encoding="utf-8")
            with self.assertRaises(ValueError):
                sub_gate.validate(folder, TASK, "implement")

        # 명세 양식의 주석 유무는 승인 상태 판정에 영향을 주지 않는다.
        (task / "status.md").write_text(NEW_STATE, encoding="utf-8")
        templates = folder / "templates"
        templates.mkdir()
        for name in ("status", "spec", "research", "refute"):
            (templates / f"log-{name}.md").write_text("# 주석 없음\n", encoding="utf-8")
        with patch.object(sub_docs, "TEMPLATES", templates):
            sub_gate.validate(folder, TASK, "implement")


class ReviewReport(unittest.TestCase):
    """검토 보고 기록, 폐기 절, 미질문 검토를 판정하는 함수 검사이다."""

    # 검토마다 같은 안 제목은 허용하고, 같은 검토 안의 중복 안 제목은 차단한다.
    def test_same_option_title_per_review(self):
        sub_document.Document("#### 검토 1. A\n\n##### 안 1. 유지\n\n#### 검토 2. B\n\n##### 안 1. 유지\n")
        with self.assertRaisesRegex(ValueError, "중복"):
            sub_document.Document("#### 검토 1. A\n\n##### 안 1. 유지\n\n##### 안 1. 유지\n")

    # 조사 항목 제목에 `(문제 <번호>)`를 붙여도 옛 제목으로 저장된 조사 원문을 번호로 찾는다.
    def test_renamed_item_matches_raw_by_number(self):
        sub_gate.check_research_result(REPORT, ["조사 1. 도구 (문제 1)"])
        with self.assertRaisesRegex(ValueError, TEMPLATE_RULE):
            sub_gate.check_research_result(REPORT, ["조사 10. 도구 (문제 1)"])

    # 기대 결과가 폐기인 요구사항 절은 필수 칸 검사에서 빠지고, 폐기하지 않은 절의 빈 칸은 차단한다.
    def test_dropped_requirement_skips_required_cells(self):
        dropped = NEW_PLAN
        extra = "### 2. 추가\n- 작업:\n- 기대 결과:\n  - 폐기\n"
        plan = dropped.replace("## 작업 금지 목록", extra + "## 작업 금지 목록")
        self.assertEqual(sub_gate.check_plan(sub_document.Document(plan)), ["1"])
        heading = dropped.replace("## 작업 금지 목록", "### 요구사항 2. 폐기\n\n- 기대 결과:\n  - 폐기\n\n## 작업 금지 목록")
        self.assertEqual(sub_gate.check_plan(sub_document.Document(heading)), ["1"])
        blank = dropped.replace("## 작업 금지 목록", "### 2. 추가\n- 작업:\n- 기대 결과:\n  - 통과\n## 작업 금지 목록")
        with self.assertRaisesRegex(ValueError, "`2. 추가`의 .* 칸이 비어 있습니다"):
            sub_gate.check_plan(sub_document.Document(blank))

    # 폐기 줄 `요구사항 <번호>. 폐기`도 요구사항 목록 번호로 읽는다.
    def test_dropped_line_is_listed_number(self):
        state = sub_document.Document(LIST_STATE.replace("1. 기능: 변경", "1. 기능: 변경\n요구사항 2. 폐기"))
        self.assertEqual(sub_document.state_numbers(state), {"1", "2"})

    # 양식의 `<`로 시작하는 출처 자리표시자는 통과하고, 형식 밖 출처는 값을 반환한다.
    def test_bad_source_placeholder(self):
        self.assertIsNone(sub_gate.bad_source("- 판단 근거: <장점과 단점> (출처: <파일:행 | URL>)"))
        self.assertEqual(sub_gate.bad_source("- 판단 근거: x (출처: 사용자 지시)"), "사용자 지시")

    # 경과 문서 검토 필요 절에 확정 답이 없는 항목이 있으면 검토 잔여로 차단하고 폐기 답 한 줄은 통과시킨다.
    def test_unanswered_review_blocks_review_left(self):
        sub_gate.check_review_left(sub_document.Document(STATE), False)
        open_item = STATE.replace("## 프로젝트 환경", "- 검토 4. 추가\n## 프로젝트 환경")
        with self.assertRaisesRegex(ValueError, "검토가 끝나지 않은"):
            sub_gate.check_review_left(sub_document.Document(open_item), False)
        done = STATE.replace("## 프로젝트 환경", "- 검토 4. 추가: 폐기 (사용자 검토 시각: 2026-11-12 03:00:00)\n## 프로젝트 환경")
        sub_gate.check_review_left(sub_document.Document(done), False)

    # 양식 주석 안의 형식 예시는 검토 잔여로 막지 않는다.
    def test_placeholder_status_skips_review_left(self):
        placeholder = STATE.replace("## 프로젝트 환경", "<!-- 형식\n  - 검토 <번호>. <제목>\n    - 상태: <미검토>\n-->\n## 프로젝트 환경")
        sub_gate.check_review_left(sub_document.Document(placeholder), False)


class ResultSections(unittest.TestCase):
    PLAN = "# 조사\n\n## 조사 계획\n\n- 제외 범위: 없음\n\n### 조사 1. 구조 (문제 1)\n\n"
    SAVED = "## 조사 결과\n\n### 조사 1. 구조 결과\n\n- 결론: 안 1\n\n#### 주장 목록\n\n- 주장 1: a\n\n#### 한계\n\n- 한계: 없음\n"

    # 항목별 결과 절이 있으면 통과하고, 항목의 결과 절이 없으면 차단한다. 결과 절이 여러 항목에 있어도 하위 제목 중복으로 막지 않는다.
    def test_result_section_per_item(self):
        two = self.PLAN + self.SAVED + "\n### 조사 2. 다른 결과\n\n- 결론: 안 2\n\n#### 주장 목록\n\n- 주장 2: b\n\n#### 한계\n\n- 한계: 없음\n"
        research = sub_document.Document(two)
        sub_gate.check_result_sections(research, ["조사 1. 구조 (문제 1)", "조사 2. 다른 (문제 1)"])
        with self.assertRaises(ValueError):
            sub_gate.check_result_sections(sub_document.Document(self.PLAN + self.SAVED), ["조사 2. 다른 (문제 1)"])

    # 결과 절의 저장된 블록 변경은 결과 저장 hook 전용이다. 자리표시자 양식은 비교하지 않는다.
    def test_saved_result_blocks_are_protected(self):
        import sub_judgment
        placeholder = self.PLAN + "## 조사 결과\n\n### 조사 <번호>. <항목명> 결과\n\n- 결론: <내용>\n"
        saved = self.PLAN + self.SAVED
        self.assertIsNone(sub_judgment.check_raw(placeholder, ""))
        self.assertIsNone(sub_judgment.check_raw(placeholder, placeholder))
        self.assertEqual(sub_judgment.check_raw(saved, placeholder), sub_judgment.REASON_RAW)
        self.assertEqual(sub_judgment.check_raw(saved.replace("안 1", "안 9"), saved), sub_judgment.REASON_RAW)
        self.assertIsNone(sub_judgment.check_raw(saved + "\n## 후보 비교\n", saved))
        self.assertTrue(sub_judgment.research_empty(placeholder))
        self.assertFalse(sub_judgment.research_empty(saved))


# 명세 작업 계획 항목의 요구사항 번호 표기를 쓰는 시점에 판정한다.
class PlanRefAtWrite(unittest.TestCase):
    SPEC = "## 요구사항 목록\n\n### 1. 첫 요구\n\n- 문제: 문제 1\n\n### 2. 둘째 요구\n\n- 문제: 문제 2\n\n## 작업 계획\n\n### 배정 범위\n\n{line}\n"

    # 없는 번호나 번호 표기가 빠진 항목은 막고, 설명이 붙은 번호와 자리표시자 줄은 통과한다.
    def test_plan_ref_reason(self):
        import sub_scope
        old = self.SPEC.format(line="- <담당>: <대상> (요구사항 <번호>)")
        check = lambda line: sub_scope.plan_ref_reason(self.SPEC.format(line=line), old)
        self.assertIsNotNone(check("- worker: a.py (요구사항 3)"))
        self.assertIsNotNone(check("- worker: a.py"))
        self.assertIsNone(check("- worker: a.py (요구사항 1, 2의 결과 저장 검사)"))
        self.assertIsNone(check("- worker: a.py (요구사항 1, 2)"))


if __name__ == "__main__":
    unittest.main()
