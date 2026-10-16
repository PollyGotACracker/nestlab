#!/usr/bin/env python3
"""임시 폴더에서만 set_agents link가 미설치 도구를 건너뛰는지 검증한다.

실행: python -B tools/tests/test_set_agents.py
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import set_agents


class SetAgentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = self.tmp / "src"
        self.source.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.dest = self.home / ".codex" / "agents"

    def link(self) -> int:
        links = [("Codex", self.source, self.dest)]
        with mock.patch.object(set_agents, "LINKS", links), contextlib.redirect_stdout(io.StringIO()) as out:
            code = set_agents.run("link")
        self.out = out.getvalue()
        return code

    def test_missing_tool_home_is_skipped(self):
        self.assertEqual(self.link(), 0)
        self.assertFalse((self.home / ".codex").exists())
        self.assertIn("건너뜀", self.out)

    def test_existing_tool_home_gets_link(self):
        (self.home / ".codex").mkdir()
        self.assertEqual(self.link(), 0)
        self.assertTrue(self.dest.exists())


if __name__ == "__main__":
    unittest.main()
