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
import sub_workflow as flow
import sub_docs

TASK = "20261024-120000-test"
APPROVAL = """
- 사용자 승인 상태: 승인
- 사용자 승인 시각: 2026-10-24 12:00:00
- 연결 대상: 명세 문서
- 승인 행동: 구현
- 영향 범위: hooks/
- 범위 밖: 없음
"""
STATE = f"""# 작업
- 작업 ID: {TASK}
- 작업 상태: 진행
## 착수 승인 범위
{APPROVAL}
## 실행 승인 범위
{APPROVAL}
## 검토 필요
- 검토 1. 도구
  - 상태: 검토 완료
  - 사용자 검토 시각: 2026-10-24 12:00:00
  - 대응 근거: 사용자 선택
## 결정 기록
- 실행 준비
## 검증 결과
- 2026-10-24 12:00:00: 기능 (정적, 검증)
  - PASS: 정상과 차단 사례 통과
  - 남은 문제: 없음
"""
PLAN = f"""# 명세
- 작업 ID: {TASK}
## 요구사항 목록
### 1. 기능
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

| 종류 | 입력 | 명령 | 기대 결과 |
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
- 검증 명령
  - `python -V`
### 잠재적 위험
- 오탐
### 진행 순서
1. 구현
### 작업 기준 자료
- a.py:1
## 재작업 요청
없음
## 재검증 요청
없음
"""
REPORT = "검토 1. 도구\n후보 A 충족, 후보 B 미충족\n제안: A\n출처: a.py:1"
ROUND = "- 주장 1: 지지 | 출처: a.py:1"
LOOP = """## 조사·반증 루프
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
### 검토 1. 도구
- 질문: 도구
## 프로젝트 환경
Python, 출처: a.py:1
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
# 반증 문서가 없던 log/state 문서는 조사 문서 안에 루프와 회차 원문이 있다.
RESEARCH_IN_ONE = RESEARCH + LOOP + f"### 1회차 반증 원문\n```\n{ROUND}\n```\n"


class Gate(unittest.TestCase):
    # 임시 저장소와 log/task 작업 문서 네 개를 준비한다.
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "log/task" / TASK
        self.folder.mkdir(parents=True)
        for kind, body in (("status", STATE), ("spec", PLAN), ("research", RESEARCH), ("refute", REFUTE)):
            (self.folder / f"{kind}.md").write_text(body, encoding="utf-8")

    # 완전한 문서가 준비된 모든 단계를 통과하는지 확인한다.
    def test_valid_documents_pass_all_ready_stages(self):
        for stage in ("kickoff", "research", "refute", "implement", "document", "verify", "complete"):
            with self.subTest(stage=stage):
                flow.validate(self.root, TASK, stage)

    # 제외 범위의 출처가 파일:행이면 통과하고 형식이 아니면 거부하며, 없음은 통과한다.
    def test_exclusion_source_format(self):
        research = lambda line: flow.Document(RESEARCH.replace("- 제외 범위: 없음", line))
        flow.research_items(research("- 제외 범위: 없음"))
        flow.research_items(research("- 제외 범위: 사전 실측 (출처: a.py:1)"))
        with self.assertRaises(ValueError):
            flow.research_items(research("- 제외 범위: 사전 실측 (출처: 사용자 답)"))

    # 단계 목록에 검증 단계가 없는지 확인한다.
    def test_no_review_stage(self):
        self.assertFalse({"review", "rereview"} & set(flow.STAGES))
        with self.assertRaisesRegex(ValueError, "지원하지 않는 단계"):
            flow.validate(self.root, TASK, "review")

    # 승인이 있는 호출은 통과하고 승인이 없으면 deny되는지 확인한다.
    def test_hook_accepts_ready_call_and_denies_missing_approval(self):
        data = {"tool_name": "spawn_agent", "agent_type": "assistant", "cwd": str(self.root),
                "tool_input": {"agent_type": "worker", "fork_turns": "none",
                               "message": f"작업 종류: 구현\n명세 문서: log/task/{TASK}/spec.md"}}
        with patch.object(sub_docs, "project_root", return_value=self.root):
            self.assertIsNone(check_tool_use.check(data, "codex"))
            (self.folder / "status.md").write_text(STATE.replace("사용자 승인 상태: 승인", "사용자 승인 상태: 대기"), encoding="utf-8")
            self.assertIn("승인 상태가 승인이 아닙니다", check_tool_use.check(data, "codex"))

    # 빈 조사 원문과 미해결 중단 판정이 단계 전환을 막는지 확인한다.
    def test_empty_research_and_unresolved_stop_block(self):
        (self.folder / "research.md").write_text(RESEARCH.replace(REPORT, "기록 없음"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "원문 코드 블록"):
            flow.validate(self.root, TASK, "refute")
        (self.folder / "status.md").write_text(STATE.replace("실행 준비", "중단 판정 미해결"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "미해결 중단 판정"):
            flow.validate(self.root, TASK, "research")

    # 양식처럼 하위 절로 쓴 프로젝트 환경과 결정 기록 항목을 절 본문으로 읽는지 확인한다.
    def test_template_subsections_are_section_body(self):
        research = RESEARCH.replace("- 대안 검색 기록: 후보 검색 (출처: a.py:1)\n", "").replace(
            "## 프로젝트 환경\nPython, 출처: a.py:1\n", "## 프로젝트 환경\n### 언어\nPython\n출처: `a.py:1`\n")
        (self.folder / "research.md").write_text(research, encoding="utf-8")
        flow.validate(self.root, TASK, "kickoff")
        (self.folder / "status.md").write_text(
            STATE.replace("- 실행 준비", "### 실행 준비\n- 결정: 진행\n### 중단 판정 미해결\n- 담당: hook"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "미해결 중단 판정"):
            flow.validate(self.root, TASK, "research")

    # 반증 문서가 없으면 반증 단계를 막는지 확인한다.
    def test_refute_needs_document(self):
        (self.folder / "refute.md").unlink()
        with self.assertRaisesRegex(ValueError, "반증 문서가 없습니다"):
            flow.validate(self.root, TASK, "refute")

    # 최종 검증이 PASS가 아니면 완료를 막는지 확인한다.
    def test_complete_requires_final_review(self):
        (self.folder / "status.md").write_text(STATE.replace("PASS:", "FAIL:"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "PASS가 아닙니다"):
            flow.validate(self.root, TASK, "complete")

    # 양식대로 비운 재작업 요청 절은 통과하고 항목이 든 절은 막는지 확인한다.
    def test_complete_empty_rework_section(self):
        empty = PLAN.replace("## 재작업 요청\n없음\n", "## 재작업 요청\n<!-- 주석 -->\n- 없음\n### 재작업 범위\n- 없음\n")
        (self.folder / "spec.md").write_text(empty, encoding="utf-8")
        flow.validate(self.root, TASK, "complete")
        pending = PLAN.replace("## 재작업 요청\n없음\n", "## 재작업 요청\n- 1. 기능: a.py\n")
        (self.folder / "spec.md").write_text(pending, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "처리하지 않은 항목"):
            flow.validate(self.root, TASK, "complete")

    # 코드 표기 안의 꺾쇠를 자리표시자로 보지 않는지 확인한다.
    def test_literal_angle_brackets_are_not_placeholders(self):
        self.assertTrue(flow.filled("`shared/skills/<이름>/template.md` 수정"))
        self.assertFalse(flow.filled("<작업 내용>"))
        self.assertTrue(flow.filled("shared/skills/<워크플로 스킬>/template.md 추가"))
        text = PLAN.replace("작업:\n  - 구현", "작업:\n  - `shared/skills/<이름>/template.md` 수정")
        self.assertEqual(flow.check_plan(flow.Document(text)), ["기능"])
        with self.assertRaisesRegex(ValueError, "`작업` 칸이 비어 있습니다"):
            flow.check_plan(flow.Document(PLAN.replace("작업:\n  - 구현", "작업:\n  - <작업 내용>")))

    # 실제 검증에 확인 수단 출처가 없으면 막고 있으면 통과시키는지 확인한다.
    def test_actual_check_requires_source(self):
        with self.assertRaisesRegex(ValueError, flow.R_CHECK_ACTUAL):
            flow.check_plan(flow.Document(PLAN.replace("fixture (확인 수단 출처: a.py:1)", "사용자 화면으로 확인")))
        self.assertEqual(flow.check_plan(flow.Document(PLAN)), ["기능"])

    # 제목 수준을 바꿔도 단계 판정이 같은지 확인한다.
    def test_heading_levels_do_not_change_stage_decisions(self):
        for kind, body in (("status", STATE), ("spec", PLAN), ("research", RESEARCH), ("refute", REFUTE)):
            (self.folder / f"{kind}.md").write_text(re.sub(r"^#{1,6} ", "# ", body, flags=re.M), encoding="utf-8")
        flow.validate(self.root, TASK, "complete")

    # 폴더 이름과 한글 슬래시 표현은 통과하고 사건 문서 파일 경로는 기록 정보로 잡는지 확인한다.
    def test_record_leak_line(self):
        import sub_input
        root = sub_docs.ROOT
        line = "├── chat_state/   log/incident 폴더   검증/확인"
        self.assertIsNone(sub_docs.record_leak(line))
        self.assertIsNone(sub_input.session_leak(line, str(root)))
        leak = "쓰기: log/incident/20261026-ab12cd34.md"
        self.assertEqual(sub_docs.record_leak(leak), leak)
        self.assertIn("log/incident/", sub_input.session_leak(leak, str(root)))

    # 회차 수 제한이 사용자 선택 전에만 적용되는지 확인한다.
    def test_round_count_blocks_only_before_user_choice(self):
        rounds = REFUTE.replace("| 1 | 0 | 정지(반증 0건) |",
                                "| 1 | 0 | 정지(반증 0건) |\n| 2 | 0 | 정지(반증 0건) |\n| 3 | 0 | 정지(반증 0건) |")
        for n in (2, 3):
            rounds += f"### {n}회차 반증 원문\n```\n{ROUND}\n```\n"
        state, research, refute = flow.Document(STATE), flow.Document(RESEARCH), flow.Document(rounds)
        items = ["검토 1. 도구"]
        flow.check_research(state, research, items, reviewed=True, refute=refute)
        with self.assertRaisesRegex(ValueError, "정지한 뒤에"):
            flow.check_research(state, research, items, reviewed=False, refute=refute)

    # 반증 문서의 회차 표 밖 행은 회차로 세지 않는지 확인한다.
    def test_loop_count_counts_only_loop_section_rows(self):
        text = REFUTE + "\n## 메모\n| 7 | x | y |\n"
        self.assertEqual(sub_docs.loop_count(text), 1)

    # 항목별로 가장 최근 조사 결과를 기준으로 삼는지 확인한다.
    def test_partial_research_keeps_latest_per_item(self):
        second = REPORT.replace("검토 1. 도구", "검토 2. 설정")
        updated = second.replace("제안: A", "제안: B")
        accumulated = REPORT + "\n" + second + "\n\n---\n\n" + updated
        flow.check_research_result(accumulated, ["검토 1. 도구", "검토 2. 설정"])
        with self.assertRaisesRegex(ValueError, "항목이 없습니다"):
            flow.check_research_result(accumulated, ["검토 3. 부재"])
        with self.assertRaisesRegex(ValueError, "`제안` 항목이 없습니다"):
            flow.check_research_result(accumulated + "\n\n---\n\n검토 2. 설정\n후보 충족 출처: a.py:1",
                                       ["검토 1. 도구", "검토 2. 설정"])
        (self.folder / "research.md").write_text(RESEARCH.replace(REPORT, accumulated), encoding="utf-8")
        flow.validate(self.root, TASK, "refute")

    # 구 라벨만 있는 조사 원문을 막는지 확인한다. 구 라벨은 저장소 검색에 걸리지 않게 이스케이프로 쓴다.
    def test_old_label_is_blocked(self):
        old = REPORT.replace("제안: A", chr(0xad8c) + chr(0xace0) + ": A")
        with self.assertRaisesRegex(ValueError, "`제안` 항목이 없습니다"):
            flow.check_research_result(old, ["검토 1. 도구"])


# 새 양식 본문을 기존 절 이름과 칸 이름으로 바꾼다. 기존 이름은 sub_docs.legacy로 구한다.
def old(text: str) -> str:
    for term in ("## 검증 결과", "(정적, 검증)", "- 검증:", "### 검증 계획", "## 재검증 요청", "## 결정 기록"):
        text = text.replace(term, sub_docs.legacy(term))
    return text


class LegacyGate(unittest.TestCase):
    # log/state의 기존 작업 문서(기존 파일명과 기존 절 이름, 반증 문서 없음)를 준비한다.
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "log/state"
        self.folder.mkdir(parents=True)
        self.docs = {"": STATE, "-input": PLAN, "-research": RESEARCH_IN_ONE}
        for suffix, body in self.docs.items():
            (self.folder / f"{TASK}{suffix}.md").write_text(old(body), encoding="utf-8")

    # 기존 문서도 같은 단계를 통과하고 경로 함수가 기존 경로를 돌려주는지 확인한다.
    def test_legacy_documents_pass_stages(self):
        self.assertEqual(sub_docs.task_doc(self.root, TASK, "input"), self.folder / f"{TASK}-input.md")
        for stage in ("kickoff", "research", "refute", "implement", "document", "verify", "complete"):
            with self.subTest(stage=stage):
                flow.validate(self.root, TASK, stage)

    # 기존 문서의 미해결 중단 판정과 검증 실패도 막는지 확인한다.
    def test_legacy_blocks(self):
        (self.folder / f"{TASK}.md").write_text(old(STATE.replace("실행 준비", "중단 판정 미해결")), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "미해결 중단 판정"):
            flow.validate(self.root, TASK, "research")
        (self.folder / f"{TASK}.md").write_text(old(STATE.replace("PASS:", "FAIL:")), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "PASS가 아닙니다"):
            flow.validate(self.root, TASK, "complete")


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
NEW_RESEARCH = RESEARCH.replace("### 검토 1. 도구", "### 검토 1. 도구 (요구사항 1)").replace(REPORT, REPORT.replace("검토 1. 도구", "검토 1. 도구 (요구사항 1)"))


class NumberedGate(unittest.TestCase):
    # 새 양식 작업 문서 네 개를 준비한다.
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "log/task" / TASK
        self.folder.mkdir(parents=True)
        self.put(status=NEW_STATE, spec=NEW_PLAN, research=NEW_RESEARCH, refute=REFUTE)

    # 문서 본문을 쓰는 보조 함수이다.
    def put(self, **docs):
        for kind, body in docs.items():
            (self.folder / f"{kind}.md").write_text(body, encoding="utf-8")

    # 번호 표기가 갖춰진 문서는 완료 단계까지 통과한다.
    def test_numbered_documents_pass(self):
        for stage in ("kickoff", "implement", "complete"):
            flow.validate(self.root, TASK, stage)

    # 번호 없음, 목록에 없는 번호, 이름 표기를 막고 번호 표기는 통과한다.
    def test_plan_items_need_valid_numbers(self):
        for bad in ("- hooks/a.py: 교체", "- hooks/a.py: 교체 (요구사항 9)", "- hooks/a.py: 교체 (요구사항 기능)"):
            self.put(spec=NEW_PLAN.replace("- hooks/a.py: 교체 (요구사항 1)", bad))
            with self.assertRaisesRegex(ValueError, "요구사항 <번호>"):
                flow.validate(self.root, TASK, "implement")
        self.put(spec=NEW_PLAN.replace("(요구사항 1)", "(요구사항 1, 1)"))
        flow.validate(self.root, TASK, "implement")
        self.put(spec=NEW_PLAN.replace("## 재작업 요청\n없음", "## 재작업 요청\n- hooks/a.py"))
        with self.assertRaisesRegex(ValueError, "재작업 요청"):
            flow.validate(self.root, TASK, "implement")

    # 조사 검토 항목과 검증 결과 항목에도 번호 표기를 요구한다.
    def test_research_and_review_items_need_numbers(self):
        self.put(research=NEW_RESEARCH.replace("### 검토 1. 도구 (요구사항 1)", "### 검토 1. 도구"))
        with self.assertRaisesRegex(ValueError, "조사 계획"):
            flow.validate(self.root, TASK, "kickoff")
        self.put(research=NEW_RESEARCH.replace("(요구사항 1)", "(요구사항 7)"))
        with self.assertRaisesRegex(ValueError, "조사 계획"):
            flow.validate(self.root, TASK, "kickoff")
        self.put(research=NEW_RESEARCH, status=NEW_STATE.replace("기능 (요구사항 1) (정적", "기능 (정적"))
        with self.assertRaisesRegex(ValueError, "검증 결과"):
            flow.validate(self.root, TASK, "complete")
        self.put(status=NEW_STATE.replace("(요구사항 1) (정적", "(요구사항 2) (정적"))
        with self.assertRaisesRegex(ValueError, "검증 결과"):
            flow.validate(self.root, TASK, "complete")


LIST_STATE = NEW_STATE.replace("## 착수 승인 범위", "## 요구사항\n### 요구사항 목록\n1. 기능: 변경\n## 착수 승인 범위", 1)


class SpecLessGate(unittest.TestCase):
    # 명세 문서 없이 경과 문서 요구사항 목록과 조사 문서만 있는 작업 폴더를 준비한다.
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "log/task" / TASK
        self.folder.mkdir(parents=True)
        self.put(status=LIST_STATE, research=NEW_RESEARCH, refute=REFUTE)

    # 문서 본문을 쓰는 보조 함수이다.
    def put(self, **docs):
        for kind, body in docs.items():
            (self.folder / f"{kind}.md").write_text(body, encoding="utf-8")

    # 명세 문서가 없어도 kickoff, research 단계와 착수 승인 전이를 통과한다.
    def test_kickoff_without_spec(self):
        for stage in ("kickoff", "research"):
            flow.validate(self.root, TASK, stage)
        old = LIST_STATE.replace("사용자 승인 상태: 승인", "사용자 승인 상태: 대기", 1)
        self.assertIsNone(flow.transition_reason(self.folder / "status.md", LIST_STATE, old, ""))

    # 경과 문서 요구사항 목록이 없거나 검토 항목 번호가 목록에 없으면 막는다.
    def test_kickoff_needs_requirement_list(self):
        self.put(status=NEW_STATE)
        with self.assertRaisesRegex(ValueError, "요구사항 목록"):
            flow.validate(self.root, TASK, "kickoff")
        self.put(status=LIST_STATE, research=NEW_RESEARCH.replace("(요구사항 1)", "(요구사항 7)"))
        with self.assertRaisesRegex(ValueError, "조사 계획"):
            flow.validate(self.root, TASK, "kickoff")

    # 명세 문서가 없으면 실행 이후 단계를 막는다.
    def test_execute_needs_spec(self):
        with self.assertRaisesRegex(ValueError, "명세 문서가 없습니다"):
            flow.validate(self.root, TASK, "implement")

    # 두 번호 집합이 다르면 막고, 같거나 경과 문서에 목록이 없으면 통과한다.
    def test_requirement_numbers_match(self):
        self.put(spec=NEW_PLAN)
        flow.validate(self.root, TASK, "implement")
        self.put(status=LIST_STATE.replace("1. 기능: 변경", "1. 기능: 변경\n2. 추가: 변경"))
        with self.assertRaisesRegex(ValueError, "경과 문서 요구사항 목록 번호와 다릅니다"):
            flow.validate(self.root, TASK, "implement")
        self.put(status=NEW_STATE)
        flow.validate(self.root, TASK, "implement")


class MultiItemRequest(unittest.TestCase):
    # 항목 여러 개의 요청 절을 넣고 해당 단계 검사를 실행한다.
    def check(self, stage, title, key, second):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        folder = root / "log/task" / TASK
        folder.mkdir(parents=True)
        items = f"- hooks/a.py: 교체 (요구사항 1)\n  - {key}: 값 하나\n- hooks/b.py: 교체 (요구사항 1)\n  - {key}: {second}\n"
        plan = NEW_PLAN.replace(f"## {title}\n없음\n", f"## {title}\n{items}")
        for kind, body in dict(status=NEW_STATE, spec=plan, research=NEW_RESEARCH, refute=REFUTE).items():
            (folder / f"{kind}.md").write_text(body, encoding="utf-8")
        flow.validate(root, TASK, stage)

    # 항목마다 칸이 채워진 요청은 통과하고 한 항목이라도 비면 막는다.
    def test_multi_item_request(self):
        for stage, title, key in (("rework", "재작업 요청", "기대 결과"), ("reverify", "재검증 요청", "필요한 근거")):
            self.check(stage, title, key, "값 둘")
            with self.assertRaisesRegex(ValueError, f"{title}.*{key}|{key}.*{title}"):
                self.check(stage, title, key, "")

    # 명세 양식 형식의 요청은 통과하고 콜론 없는 재검증 요청은 막는다.
    def test_spec_template_format(self):
        def run(stage, title, key, item, extra=""):
            root = Path(tempfile.mkdtemp())
            self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
            folder = root / "log/task" / TASK
            folder.mkdir(parents=True)
            plan = NEW_PLAN.replace(f"## {title}\n없음\n", f"## {title}\n{item}\n  - {key}: 값\n{extra}")
            for kind, body in dict(status=NEW_STATE, spec=plan, research=NEW_RESEARCH, refute=REFUTE).items():
                (folder / f"{kind}.md").write_text(body, encoding="utf-8")
            flow.validate(root, TASK, stage)

        run("rework", "재작업 요청", "기대 결과", "- hooks/a.py: 10-20행 (요구사항 1)", "### 재작업 범위\n- hooks/a.py (요구사항 1)\n")
        run("reverify", "재검증 요청", "필요한 근거", "- hooks/a.py: 기능 판정 (요구사항 1)")
        with self.assertRaisesRegex(ValueError, "재검증 요청"):
            run("reverify", "재검증 요청", "필요한 근거", "- 기능 판정 (요구사항 1)")


class ReviewStatus(unittest.TestCase):
    # 허용 값은 템플릿 주석에서 읽고 비허용 값과 주석 누락은 막는다.
    def test_allowed_values_come_from_template_comment(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        task = folder / "log/task" / TASK
        task.mkdir(parents=True)
        for kind, body in (("status", NEW_STATE), ("spec", NEW_PLAN), ("research", NEW_RESEARCH), ("refute", REFUTE)):
            (task / f"{kind}.md").write_text(body, encoding="utf-8")
        self.assertEqual(sub_docs.review_done(), ("검토 완료", "사후실측 필요"))
        flow.validate(folder, TASK, "implement")
        post = NEW_STATE.replace("상태: 검토 완료", "상태: 사후실측 필요")
        (task / "status.md").write_text(post, encoding="utf-8")
        flow.validate(folder, TASK, "implement")
        for value in ("사후검증 필요", "검토 완료(일부)", "미검토"):
            (task / "status.md").write_text(NEW_STATE.replace("상태: 검토 완료", f"상태: {value}"), encoding="utf-8")
            with self.assertRaises(ValueError):
                flow.validate(folder, TASK, "implement")

        # 템플릿 주석이 없는 스킬 원본은 막는다.
        (task / "status.md").write_text(NEW_STATE, encoding="utf-8")
        skills = folder / "skills" / "nestlab-log-state"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("# 주석 없음\n", encoding="utf-8")
        with patch.object(sub_docs, "SKILLS", folder / "skills"), self.assertRaisesRegex(ValueError, "주석이 없습니다"):
            flow.validate(folder, TASK, "implement")


if __name__ == "__main__":
    unittest.main()
