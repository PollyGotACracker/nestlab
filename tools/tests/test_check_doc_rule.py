"""경로 목록 절 허용 파일(저장소 공통 지침, 프로젝트 전역 지침)의 검사5를 검증한다.

실행: python -m unittest discover -s tools/tests -p "test_*.py"
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "check_doc_rule"))
import sub_rules

# 같은 이름의 다른 도구 모듈(sub_config)과 충돌하지 않도록 캐시에서 뺀다.
for _name in ("sub_config", "sub_paths", "sub_sections", "sub_rules"):
    sys.modules.pop(_name, None)
sys.path.remove(str(TOOLS / "check_doc_rule"))

IN_SECTION = "## 경로 목록\n\n- `ls log/`\n"
OUT_SECTION = "## 규칙\n\n- `ls log/`\n"


class DirectPathTest(unittest.TestCase):
    # 임시 루트에 지침 파일을 만들고 검사5 위반 수를 반환한다.
    def count_direct(self, name: str, text: str) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / name).write_text(text, encoding="utf-8")
            with mock.patch.object(sub_rules, "ROOT", Path(tmp)):
                found = sub_rules.check_file(name, set(), {})
        return sum(1 for v in found if v[2] == sub_rules.KIND_DIRECT)

    def test_allowed_in_both_files(self):
        for name in ("AGENTS.md", "AGENTS.global.md"):
            self.assertEqual(self.count_direct(name, IN_SECTION), 0)

    def test_blocked_outside_section(self):
        for name in ("AGENTS.md", "AGENTS.global.md"):
            self.assertEqual(self.count_direct(name, OUT_SECTION), 1)


if __name__ == "__main__":
    unittest.main()
