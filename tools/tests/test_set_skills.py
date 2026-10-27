#!/usr/bin/env python3
"""임시 폴더에서만 set_skills link가 미설치 도구를 건너뛰는지 검증한다.

실행: python -B tools/tests/test_set_skills.py
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "common"))
sys.path.insert(0, str(TOOLS / "set_skills"))
import sub_config
import sub_link
import sub_status


class Set스킬sTest(unittest.TestCase):
    # 임시 폴더와 skill 원본을 준비한다.
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = (self.tmp / "shared" / "skills").resolve()
        (self.source / "demo").mkdir(parents=True)
        (self.source / "demo" / "SKILL.md").write_text("x", encoding="utf-8")
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.skills = self.home / ".agents" / "skills"

    # link를 실행해 종료 코드와 출력을 보관하는 보조 함수이다.
    def link(self) -> int:
        dirs = [(".agents", self.skills, "Codex", [self.home / ".codex", self.home / ".cursor"])]
        with (mock.patch.object(sub_link, "LINK_DIRS", dirs),
              mock.patch.object(sub_status, "SOURCE", self.source),
              contextlib.redirect_stdout(io.StringIO()) as out):
            code = sub_link.link()
        self.out = out.getvalue()
        return code

    # 도구 설정 폴더가 없으면 건너뛰는지 확인한다.
    def test_missing_tool_homes_are_skipped(self):
        self.assertEqual(self.link(), 0)
        self.assertFalse(self.skills.exists())
        self.assertIn("건너뜀", self.out)

    # 도구 설정 폴더가 하나라도 있으면 링크를 만드는지 확인한다.
    def test_one_tool_home_gets_link(self):
        (self.home / ".cursor").mkdir()
        self.assertEqual(self.link(), 0)
        self.assertTrue((self.skills / "demo").exists())


if __name__ == "__main__":
    unittest.main()
