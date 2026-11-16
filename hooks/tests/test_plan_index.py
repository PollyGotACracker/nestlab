"""계획 통합, 색인 선택과 시각 자동 기록의 회귀 시험."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'common'), str(ROOT / 'check_tool_use'), str(ROOT / 'check_workflow_response')]
from sub_task_docs import create, STAGES
from sub_task_index import refresh_index, selected_status, stamp_status
import sub_paths
from sub_docs import structure_reason, constraint_reason, template_text
from sub_question import check_one
from sub_form import source_gap, form_of
from sub_approval import head_line


class PlanIndex(unittest.TestCase):
    def test_creation_selection_and_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ordinary = root / 'log'
            ordinary.mkdir()
            (ordinary / 'data.txt').write_text('preserve')
            task = '20261114-190000-plan-test'
            created = create(root, task, '계획 통합', 'plan')
            self.assertEqual(len(created), 4)
            self.assertEqual(set(STAGES), {'plan'})
            path = selected_status(root)
            self.assertEqual(path.parent.name, task)
            text = path.read_text(encoding='utf-8')
            self.assertRegex(text, r'- 작업 ID: [^\n]+\n- 생성 시각: \d{4}\.\d\d\.\d\d \d\d:\d\d:\d\d\n- 완료 시각: 없음')
            self.assertEqual(create(root,task,'계획 통합','plan'), [])
            self.assertEqual(path.read_text(encoding='utf-8'),text)
            finished = stamp_status(path, text, True)
            self.assertNotIn('- 완료 시각: 없음',finished)
            self.assertEqual(stamp_status(path,finished,True),finished)
            self.assertEqual((ordinary/'data.txt').read_text(),'preserve')

    # 색인 갱신은 지정 작업 한 줄만 옮기고 양식 주석, 정렬, 다른 작업의 경과 문서를 보존한다.
    def test_index_updates_one_line(self):
        from sub_task_index import ITEM, split_index, CURRENT, PENDING, COMPLETE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = '20261101-010101-old-task', '20261102-010101-new-task'
            create(root, old, '이전 작업', 'plan')
            create(root, new, '새 작업', 'plan')
            other = root / '.agent-logs/task' / old / 'status.md'
            other_bytes = other.read_bytes()
            index = root / '.agent-logs/task/index.md'
            head, sections = split_index(index.read_text(encoding='utf-8'))
            self.assertEqual([ITEM.match(x)[2] for x in sections[CURRENT] if ITEM.match(x)], [new])
            self.assertEqual([ITEM.match(x)[2] for x in sections[PENDING] if ITEM.match(x)], [new, old])
            for title in (CURRENT, PENDING, COMPLETE):
                self.assertTrue(any(x.startswith('<!--') for x in sections[title]), title)

            # 완료된 작업은 현재와 미완료 절에서 빠지고 단계 없이 완료 절로 이동하며, 선택하지 않은 작업은 현재 절을 바꾸지 않는다.
            status = root / '.agent-logs/task' / new / 'status.md'
            text = status.read_text(encoding='utf-8').replace('- 작업 단계: 계획', '- 작업 단계: 완료')
            status.write_text(stamp_status(status, text, True), encoding='utf-8')
            refresh_index(root, new)
            refresh_index(root, old, current=False)
            _, sections = split_index(index.read_text(encoding='utf-8'))
            self.assertFalse([x for x in sections[CURRENT] if ITEM.match(x)])
            self.assertEqual([x for x in sections[COMPLETE] if ITEM.match(x)], [f'- [경과 문서: 새 작업]({new}/status.md)'])
            self.assertEqual(other.read_bytes(), other_bytes)
            self.assertIsNone(selected_status(root))
            # 현재 진행 작업이 비어도 자동 선택하지 않고, current=True일 때만 선택한다.
            refresh_index(root, old)
            self.assertIsNone(selected_status(root))
            refresh_index(root, old, current=True)
            self.assertEqual(selected_status(root).parent.name, old)

    # 색인 편집은 현재 진행 작업 절의 형식 맞는 줄 하나만 허용한다.
    def test_index_edit_reason(self):
        from sub_task_index import index_edit_reason, REASON_INDEX_SECTION, REASON_INDEX_LINE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, other = '20261101-010101-a-task', '20261102-010101-b-task'
            create(root, task, '가', 'plan')
            create(root, other, '나', 'plan')
            old = (root / '.agent-logs/task/index.md').read_text(encoding='utf-8')
            mark = '<!-- <작업 링크>, <작업 단계> -->'
            chosen = old.replace(f'## 현재 진행 작업\n\n{mark}\n\n- [경과 문서: 나]({other}/status.md), 계획',
                                 f'## 현재 진행 작업\n\n{mark}\n\n- [경과 문서: 가]({task}/status.md), 계획')
            self.assertNotEqual(chosen, old)
            self.assertIsNone(index_edit_reason(root, old, chosen))
            self.assertIsNone(index_edit_reason(root, old, old.replace(f'- [경과 문서: 나]({other}/status.md), 계획\n\n## 미완료', '## 미완료', 1)))
            self.assertEqual(index_edit_reason(root, old, chosen.replace('## 완료 작업 목록\n', '## 완료 작업 목록\n\n- 임의 줄\n')), REASON_INDEX_SECTION)
            self.assertEqual(index_edit_reason(root, old, chosen.replace(f'{mark}\n\n- [경과 문서: 가]', '- [경과 문서: 가]', 1)), REASON_INDEX_SECTION)
            self.assertEqual(index_edit_reason(root, old, chosen.replace(f'({task}/status.md), 계획', f'({task}/status.md)', 1)), REASON_INDEX_LINE)
            self.assertEqual(index_edit_reason(root, old, chosen.replace(f'- [경과 문서: 가]({task}/status.md), 계획',
                             f'- [경과 문서: 가]({task}/status.md), 계획\n- [경과 문서: 나]({other}/status.md), 계획', 1)), REASON_INDEX_LINE)
            self.assertIn('20261103-010101-none', index_edit_reason(root, old, chosen.replace(task, '20261103-010101-none', 1)))
            self.assertIsNone(index_edit_reason(root, None, (sub_paths.INDEX_TEMPLATE).read_text(encoding='utf-8')))

    def test_stage_names_and_template_structure(self):
        self.assertEqual(head_line('[plan] 계획')[0],'계획')
        for name in ('state','input','research','refute','report-plan'):
            with self.subTest(name=name):
                text = template_text(name)
                self.assertIsNone(structure_reason(text,name))
                self.assertIsNotNone(structure_reason(text+'\n## 임의 제목\n',name))
        self.assertEqual(form_of('## 계획 보고'),'report-plan')

    def test_constraints_in_all_forms(self):
        for label in ('한계','위험','한계와 위험','잠재 위험과 한계','확인 범위와 한계'):
            with self.subTest(label=label):
                self.assertIsNone(constraint_reason('- '+label+': 없음'))
                self.assertIsNotNone(constraint_reason('- '+label+': 미검증 (출처: src/a.py:1)'))
                self.assertIsNotNone(constraint_reason('- '+label+': 외부 서비스 중단'))
                self.assertIsNone(constraint_reason('- '+label+': 외부 서비스 중단 (출처: https://example.com/status)'))

    def test_source_cannot_escape_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'session';root.mkdir()
            (root.parent/'outside.txt').write_text('private')
            self.assertIsNotNone(source_gap('../outside.txt:1',root))

    def test_question_requires_template(self):
        self.assertIsNotNone(check_one({'question':'아무 질문','options':[]},{},'codex'))

    # 작업 선택 질문은 미완료 작업 ID와 `신규 작업` 라벨만 허용하고, 없는 작업·완료 작업·신규 작업 누락은 차단한다.
    def test_task_select_question(self):
        import sub_question
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task, done = '20261101-010101-a-task', '20261102-010101-b-task'
            create(root, task, '가', 'plan')
            create(root, done, '나', 'plan')
            status = root / '.agent-logs/task' / done / 'status.md'
            status.write_text(status.read_text(encoding='utf-8').replace('- 작업 단계: 계획', '- 작업 단계: 완료'), encoding='utf-8')
            ask = lambda *labels: check_one({'question': '[작업 선택] 로그 정리를 진행할 작업을 정한다.',
                                             'options': [{'label': l} for l in labels]}, {'cwd': str(root)}, 'claude')
            self.assertIsNone(ask(task, '신규 작업'))
            self.assertEqual(ask(task), sub_question.REASON_SELECT_NEW)
            self.assertIn('20261103-010101-none', ask('20261103-010101-none', '신규 작업'))
            self.assertEqual(ask(done, '신규 작업'), sub_question.REASON_SELECT_DONE.format(label=done))

    # hook만 쓰는 잠금 절과 칸 이름이 실제 경과 문서와 명세 문서 양식의 제목에 있는지 확인한다.
    def test_locked_names_exist_in_templates(self):
        import re
        import sub_scope
        import sub_spec
        headings = lambda kind: set(re.findall(r'(?m)^#+ (.+)$', template_text(kind)))
        for title in sub_scope.LOCKED:
            self.assertIn(title, headings('state'))
        spec = headings('input')
        for title in sub_spec.LOCKED:
            if title != '결과':  # 결과 칸은 이전 구조 명세의 칸이다.
                self.assertIn(title, spec)

    # `[plan new]`, `[작업ID]` 머리줄 판정과 다른 키워드의 new 거부를 확인한다.
    def test_head_line_new_and_task_only(self):
        from sub_approval import stage_prefix
        task = '20261101-010101-a-task'
        self.assertEqual(head_line('[plan new] 새 작업'), ('계획', 'new'))
        self.assertIsNone(stage_prefix('[plan new]'))
        self.assertEqual(head_line(f'[{task}] 본문'), ('', task))
        self.assertEqual(head_line('[implement new]'), ('보류', None))
        self.assertEqual(head_line('[잘못된 머리줄]'), ('보류', None))


if __name__ == '__main__':
    unittest.main()
