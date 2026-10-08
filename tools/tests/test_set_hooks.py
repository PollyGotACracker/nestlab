#!/usr/bin/env python3
"""임시 HOME에서만 install(bashrc, 자리표시 채우기, 전역 설정 병합)을 검증한다.

실행: python -B tools/tests/test_set_hooks.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import set_hooks

CLAUDE = {"agent": "x", "permissions": {"deny": ["Read(a)"]},
          "hooks": {"Stop": [{"hooks": [{"command": "<PYTHON> <BRAINSTACK>"}]}]}}
CODEX = ('sandbox_mode = "danger-full-access"\napproval_policy = "on-request"\n\n[features]\nhooks = true\n\n'
         "[[hooks.Stop]]\n\n[[hooks.Stop.hooks]]\ncommand = '<PYTHON> <BRAINSTACK>'\n")
BEGIN = "# >>> brainstack >>>"


class SetHooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.bashrc = self.home / ".bashrc"
        self.settings = self.tmp / "settings"
        for rel in ("claude", "codex/rules"):
            (self.settings / rel).mkdir(parents=True)
        (self.settings / "bashrc.sh").write_text(f"{BEGIN}\nA\n# <<< brainstack <<<\n", encoding="utf-8")
        (self.settings / "claude/settings.example.json").write_text(json.dumps(CLAUDE), encoding="utf-8")
        (self.settings / "codex/config.example.toml").write_text(CODEX, encoding="utf-8")
        (self.settings / "codex/rules/default.rules").write_text('prefix_rule(pattern=["rm"], decision="prompt")\n', encoding="utf-8")

    def run_install(self):
        set_hooks.install(self.settings, self.bashrc, self.home)

    def test_fills_placeholders(self):
        self.run_install()
        for rel in ("claude/settings.json", "codex/config.toml"):
            text = (self.settings / rel).read_text(encoding="utf-8")
            self.assertNotIn("<BRAINSTACK>", text)
            self.assertNotIn("<PYTHON>", text)
            self.assertIn(set_hooks.ROOT.as_posix(), text)

    def test_bashrc_block_is_appended_replaced_and_user_lines_kept(self):
        self.bashrc.parent.mkdir(parents=True)
        self.bashrc.write_text("export X=1\n", encoding="utf-8")
        self.run_install()
        self.run_install()
        text = self.bashrc.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("export X=1\n"))
        self.assertEqual(text.count(BEGIN), 1)

    def test_claude_merge_keeps_user_items_and_is_idempotent(self):
        path = self.home / ".claude/settings.json"
        path.parent.mkdir(parents=True)
        user = {"model": "m", "permissions": {"deny": ["Read(u)"], "allow": ["Bash(u)"]},
                "hooks": {"Stop": [{"hooks": [{"command": "user"}]}]}}
        path.write_text(json.dumps(user), encoding="utf-8")
        self.run_install()
        first = path.read_text(encoding="utf-8")
        self.run_install()
        self.assertEqual(first, path.read_text(encoding="utf-8"))
        data = json.loads(first)
        self.assertEqual(data["model"], "m")
        self.assertEqual(data["permissions"]["deny"], ["Read(u)", "Read(a)"])
        self.assertEqual(data["permissions"]["allow"], ["Bash(u)"])
        self.assertEqual(len(data["hooks"]["Stop"]), 2)
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"], "user")

    def test_codex_merge_keeps_user_items_and_is_idempotent(self):
        path = self.home / ".codex/config.toml"
        path.parent.mkdir(parents=True)
        path.write_text('approval_policy = "never"\napprovals_reviewer = "auto_review"\n\n[features]\nfoo = true\n\n[plugins.p]\nenabled = true\n',
                        encoding="utf-8")
        rules = self.home / ".codex/rules/default.rules"
        rules.parent.mkdir(parents=True)
        rules.write_text('prefix_rule(pattern=["rg"], decision="allow")\n', encoding="utf-8")
        self.run_install()
        first, first_rules = path.read_text(encoding="utf-8"), rules.read_text(encoding="utf-8")
        self.run_install()
        self.assertEqual(first, path.read_text(encoding="utf-8"))
        self.assertEqual(first_rules, rules.read_text(encoding="utf-8"))
        data = tomllib.loads(first)
        self.assertNotIn("approvals_reviewer", data)
        self.assertEqual(data["approval_policy"], "on-request")
        self.assertEqual(data["sandbox_mode"], "danger-full-access")
        self.assertEqual(data["features"], {"foo": True, "hooks": True})
        self.assertTrue(data["plugins"]["p"]["enabled"])
        self.assertEqual(len(data["hooks"]["Stop"]), 1)
        self.assertTrue(first_rules.startswith('prefix_rule(pattern=["rg"]'))
        self.assertIn('pattern=["rm"]', first_rules)

    def test_removed_and_changed_items_propagate_to_global(self):
        path = self.home / ".claude/settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"model": "m", "permissions": {"deny": ["Read(u)"]}}), encoding="utf-8")
        example = self.settings / "claude/settings.example.json"
        self.run_install()
        new = {"agent": "y", "permissions": {"deny": ["Read(a)", "Read(b)"]},
               "hooks": {"Stop": [{"hooks": [{"command": "<PYTHON> <BRAINSTACK> v2"}]}]}}
        example.write_text(json.dumps(new), encoding="utf-8")
        self.run_install()
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["agent"], "y")
        self.assertEqual(data["permissions"]["deny"], ["Read(u)", "Read(a)", "Read(b)"])
        self.assertEqual(len(data["hooks"]["Stop"]), 1)
        self.assertIn("v2", data["hooks"]["Stop"][0]["hooks"][0]["command"])
        example.write_text(json.dumps({"permissions": {"deny": ["Read(b)"]}}), encoding="utf-8")
        self.run_install()
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("agent", data)
        self.assertNotIn("hooks", data)
        self.assertEqual(data["model"], "m")
        self.assertEqual(data["permissions"]["deny"], ["Read(u)", "Read(b)"])

    def test_real_example_files_install_into_temp_home(self):
        real = self.tmp / "real"
        shutil.copytree(set_hooks.SETTINGS, real, ignore=shutil.ignore_patterns("settings.json", "config.toml"))
        set_hooks.install(real, self.bashrc, self.home)
        self.assertIn("permissions", json.loads((self.home / ".claude/settings.json").read_text(encoding="utf-8")))
        text = (self.home / ".codex/config.toml").read_text(encoding="utf-8")
        self.assertNotIn("<BRAINSTACK>", text)
        self.assertNotIn("<PYTHON>", text)
        data = tomllib.loads(text)
        for groups in data["hooks"].values():
            for group in groups:
                for h in group["hooks"]:
                    self.assertEqual(h["commandWindows"], "& " + h["command"], h["command"])


if __name__ == "__main__":
    unittest.main()
