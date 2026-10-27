#!/usr/bin/env python3
"""임시 HOME에서만 install(bashrc, 자리표시 채우기, 전역 설정 병합)을 검증한다.

실행: python -B tools/tests/test_set_hooks.py
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import tomllib
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import set_hooks

CLAUDE = {"agent": "x", "permissions": {"deny": ["Read(a)"]},
          "hooks": {"Stop": [{"hooks": [{"command": "<PYTHON> <NESTLAB>"}]}]}}
CODEX = ('sandbox_mode = "workspace-write"\napproval_policy = "on-request"\n\n[features]\nhooks = true\n\n'
         "[[hooks.Stop]]\n\n[[hooks.Stop.hooks]]\ncommand = '<PYTHON> <NESTLAB>'\n")
BEGIN = "# >>> nestlab >>>"


class SetHooksTest(unittest.TestCase):
    # 임시 홈과 설정 원본을 준비한다.
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.bashrc = self.home / ".bashrc"
        self.settings = self.tmp / "settings"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".codex").mkdir()
        for rel in ("claude", "codex/rules"):
            (self.settings / rel).mkdir(parents=True)
        (self.settings / "bashrc.sh").write_text(f"{BEGIN}\nA\n# <<< nestlab <<<\n", encoding="utf-8")
        (self.settings / "claude/settings.example.json").write_text(json.dumps(CLAUDE), encoding="utf-8")
        (self.settings / "codex/config.example.toml").write_text(CODEX, encoding="utf-8")
        (self.settings / "codex/rules/default.rules").write_text('prefix_rule(pattern=["rm"], decision="prompt")\n', encoding="utf-8")

    # 임시 홈 기준으로 설치를 실행하는 보조 함수이다.
    def run_install(self):
        set_hooks.install(self.settings, self.bashrc, self.home)

    # 자리표시가 실제 경로로 채워지는지 확인한다.
    def test_fills_placeholders(self):
        self.run_install()
        for rel in ("claude/settings.json", "codex/config.toml"):
            text = (self.settings / rel).read_text(encoding="utf-8")
            self.assertNotIn("<NESTLAB>", text)
            self.assertNotIn("<PYTHON>", text)
            self.assertIn(set_hooks.ROOT.as_posix(), text)

    # bashrc 블록이 추가와 교체되고 사용자 줄이 보존되는지 확인한다.
    def test_bashrc_block_is_appended_replaced_and_user_lines_kept(self):
        self.bashrc.parent.mkdir(parents=True, exist_ok=True)
        self.bashrc.write_text("export X=1\n", encoding="utf-8")
        self.run_install()
        self.run_install()
        text = self.bashrc.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("export X=1\n"))
        self.assertEqual(text.count(BEGIN), 1)

    # Claude 설정 병합이 사용자 항목을 보존하고 반복 실행해도 같은지 확인한다.
    def test_claude_merge_keeps_user_items_and_is_idempotent(self):
        path = self.home / ".claude/settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
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

    # Codex 설정 병합이 사용자 항목을 보존하고 반복 실행해도 같은지 확인한다.
    def test_codex_merge_keeps_user_items_and_is_idempotent(self):
        path = self.home / ".codex/config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('approval_policy = "never"\napprovals_reviewer = "auto_review"\n\n[features]\nfoo = true\n\n[plugins.p]\nenabled = true\n',
                        encoding="utf-8")
        rules = self.home / ".codex/rules/default.rules"
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.write_text('prefix_rule(pattern=["rg"], decision="allow")\n', encoding="utf-8")
        self.run_install()
        first, first_rules = path.read_text(encoding="utf-8"), rules.read_text(encoding="utf-8")
        self.run_install()
        self.assertEqual(first, path.read_text(encoding="utf-8"))
        self.assertEqual(first_rules, rules.read_text(encoding="utf-8"))
        data = tomllib.loads(first)
        self.assertNotIn("approvals_reviewer", data)
        self.assertEqual(data["approval_policy"], "on-request")
        self.assertEqual(data["sandbox_mode"], "workspace-write")
        self.assertEqual(data["features"], {"foo": True, "hooks": True})
        self.assertTrue(data["plugins"]["p"]["enabled"])
        self.assertEqual(len(data["hooks"]["Stop"]), 1)
        self.assertTrue(first_rules.startswith('prefix_rule(pattern=["rg"]'))
        self.assertIn('pattern=["rm"]', first_rules)

    # 예제 [features]의 새 키는 추가되고 사용자 값은 보존되는지 확인한다.
    def test_codex_features_keys_added_and_user_values_kept(self):
        path = self.home / ".codex/config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[features]\nhooks = false\n", encoding="utf-8")
        example = self.settings / "codex/config.example.toml"
        example.write_text(CODEX.replace("hooks = true\n", "hooks = true\nnew_flag = true\n", 1), encoding="utf-8")
        self.run_install()
        self.assertEqual(tomllib.loads(path.read_text(encoding="utf-8"))["features"], {"hooks": False, "new_flag": True})

    # 예제 [windows] 키가 사용자 값을 덮어쓰고 표가 없으면 추가되는지 확인한다.
    def test_codex_windows_table_overwritten_and_added(self):
        path = self.home / ".codex/config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        example = self.settings / "codex/config.example.toml"
        example.write_text(CODEX.replace("[[hooks.Stop]]", '[windows]\nsandbox = "elevated"\n\n[[hooks.Stop]]', 1), encoding="utf-8")
        path.write_text('[windows]\nsandbox = "unelevated"\n\n[features]\nhooks = true\n', encoding="utf-8")
        self.run_install()
        self.assertEqual(tomllib.loads(path.read_text(encoding="utf-8"))["windows"], {"sandbox": "elevated"})
        path.write_text("[features]\nhooks = true\n", encoding="utf-8")
        self.run_install()
        self.assertEqual(tomllib.loads(path.read_text(encoding="utf-8"))["windows"], {"sandbox": "elevated"})

    # 원본에서 지우거나 바꾼 항목이 전역 설정에 반영되는지 확인한다.
    def test_removed_and_changed_items_propagate_to_global(self):
        path = self.home / ".claude/settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": "m", "permissions": {"deny": ["Read(u)"]}}), encoding="utf-8")
        example = self.settings / "claude/settings.example.json"
        self.run_install()
        new = {"agent": "y", "permissions": {"deny": ["Read(a)", "Read(b)"]},
               "hooks": {"Stop": [{"hooks": [{"command": "<PYTHON> <NESTLAB> v2"}]}]}}
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

    # 실제 예제 파일을 임시 홈에 설치할 수 있는지 확인한다.
    def test_real_example_files_install_into_temp_home(self):
        real = self.tmp / "real"
        shutil.copytree(set_hooks.SETTINGS, real, ignore=shutil.ignore_patterns("settings.json", "config.toml"))
        set_hooks.install(real, self.bashrc, self.home)
        self.assertIn("permissions", json.loads((self.home / ".claude/settings.json").read_text(encoding="utf-8")))
        text = (self.home / ".codex/config.toml").read_text(encoding="utf-8")
        self.assertNotIn("<NESTLAB>", text)
        self.assertNotIn("<PYTHON>", text)
        data = tomllib.loads(text)
        self.assertEqual(data["personality"], "none")
        self.assertTrue(data["features"]["default_mode_request_user_input"])
        for groups in data["hooks"].values():
            for group in groups:
                for h in group["hooks"]:
                    self.assertEqual(h["commandWindows"], "& " + h["command"], h["command"])

    # 임시 폴더와 Nodebase 자리표시가 채워지고, archive_root가 없으면 Nodebase 항목이 빠지는지 확인한다.
    def test_fills_temp_and_nodebase(self):
        example = self.settings / "claude/settings.example.json"
        example.write_text(json.dumps({"permissions": {"additionalDirectories": ["~/.claude", "<TEMP>", "<NODEBASE>"]}}),
                           encoding="utf-8")
        dirs = lambda: json.loads((self.settings / "claude/settings.json").read_text(encoding="utf-8"))[
            "permissions"]["additionalDirectories"]
        temp = Path(tempfile.gettempdir()).resolve().as_posix()
        (self.settings / "local.json").write_text(json.dumps({"archive_root": "C:/nodebase"}), encoding="utf-8")
        self.run_install()
        self.assertEqual(dirs(), ["~/.claude", temp, "C:/nodebase"])
        for content in ("{}", ""):
            (self.settings / "local.json").write_text(content, encoding="utf-8")
            self.run_install()
            self.assertEqual(dirs(), ["~/.claude", temp])

    # 실제 설정 원본이 설치되면 sandbox 블록, 권한 프로필, deny 규칙이 들어가고 sandbox_mode와 ask 항목은 없는지 확인한다.
    def test_real_example_sandbox_and_permission_profile(self):
        real = self.tmp / "real"
        shutil.copytree(set_hooks.SETTINGS, real, ignore=shutil.ignore_patterns("settings.json", "config.toml"))
        (self.home / ".codex/config.toml").write_text('sandbox_mode = "workspace-write"\n', encoding="utf-8")
        with mock.patch.object(sys, "platform", "linux"):
            set_hooks.install(real, self.bashrc, self.home)
        claude = json.loads((self.home / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertTrue(claude["sandbox"]["enabled"])
        self.assertNotIn("ask", claude["permissions"])
        self.assertIn("Bash(cd *)", claude["permissions"]["deny"])
        self.assertEqual(claude["env"]["CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR"], "1")
        cfg = tomllib.loads((self.home / ".codex/config.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["sandbox_mode"], "workspace-write")
        self.assertNotIn("default_permissions", cfg)
        self.assertNotIn("permissions", cfg)
        rules = (self.home / ".codex/rules/default.rules").read_text(encoding="utf-8")
        self.assertNotIn('decision="prompt"', rules)
        self.assertIn('pattern=["git", "-c"], decision="forbidden"', rules)

    # 이전 설치가 남긴 권한 프로필 선택 줄과 표가 재설치로 사라지고 sandbox_mode가 돌아오는지 확인한다.
    def test_reinstall_removes_old_permission_profile(self):
        real = self.tmp / "real"
        shutil.copytree(set_hooks.SETTINGS, real, ignore=shutil.ignore_patterns("settings.json", "config.toml"))
        path = self.home / ".codex/config.toml"
        path.write_text('default_permissions = "nestlab"\napproval_policy = "on-request"\n\n'
                        f'{BEGIN}\n[permissions.nestlab]\nextends = ":workspace"\n# <<< nestlab <<<\n', encoding="utf-8")
        set_hooks.install(real, self.bashrc, self.home)
        cfg = tomllib.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(cfg["sandbox_mode"], "workspace-write")
        self.assertNotIn("default_permissions", cfg)
        self.assertNotIn("permissions", cfg)

    # 네이티브 Windows 설치는 Claude sandbox 블록을 넣지 않고 이전 블록을 지우며, 그 밖의 플랫폼은 넣는지 확인한다.
    def test_claude_sandbox_by_platform(self):
        real = self.tmp / "real"
        shutil.copytree(set_hooks.SETTINGS, real, ignore=shutil.ignore_patterns("settings.json", "config.toml"))
        target = self.home / ".claude/settings.json"
        for platform, expected in (("win32", None), ("linux", {"enabled": True})):
            target.write_text('{"sandbox": {"enabled": true}}' if platform == "win32" else "{}", encoding="utf-8")
            with mock.patch.object(sys, "platform", platform):
                set_hooks.install(real, self.bashrc, self.home)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")).get("sandbox"), expected, platform)

    # 설치 결과 줄이 대상 파일 내용에서 만들어지는지 확인한다.
    def test_install_returns_result_lines_from_target_files(self):
        real = self.tmp / "real"
        shutil.copytree(set_hooks.SETTINGS, real, ignore=shutil.ignore_patterns("settings.json", "config.toml"))
        pre = self.home / ".codex/config.toml"
        pre.parent.mkdir(parents=True, exist_ok=True)
        pre.write_text('[hooks.state."x:y"]\ntrusted_hash = "h"\n', encoding="utf-8")
        rules = self.home / ".codex/rules/default.rules"
        rules.parent.mkdir(parents=True, exist_ok=True)
        rules.write_text('prefix_rule(pattern=["rg"], decision="allow")\n', encoding="utf-8")
        lines = set_hooks.install(real, self.bashrc, self.home)
        cfg = tomllib.loads((self.home / ".codex/config.toml").read_text(encoding="utf-8"))
        n = set_hooks.count_hooks(cfg["hooks"])
        block = set_hooks.BLOCK_RE.search(rules.read_text(encoding="utf-8")).group()
        k = len(set_hooks.PREFIX_RE.findall(block))
        self.assertEqual(lines[4], f"- ~/.codex/rules/default.rules: prefix_rule {k}개 (블록 밖 1개)")
        self.assertEqual(lines[0], "설치 완료")
        self.assertEqual(len(lines), 5)
        self.assertIn(f"~/.codex/config.toml: nestlab hook 명령 {n}개", lines[3])
        self.assertIn(f'sandbox_mode = "{cfg["sandbox_mode"]}"', lines[3])
        self.assertTrue(lines[4].startswith("- ~/.codex/rules/default.rules: prefix_rule "))

    # hooks 항목이 없는 빈 그룹이 있어도 nestlab hook 명령만 세는지 확인한다.
    def test_count_hooks_ignores_group_without_hooks(self):
        hooks = {"UserPromptSubmit": [{}],
                 "Stop": [{"hooks": [{"command": "py nestlab/hooks/a.py"}, {"command": "other"}]}]}
        self.assertEqual(set_hooks.count_hooks(hooks), 1)

    # Codex 폴더가 없으면 건너뛰는지 확인한다.
    def test_missing_codex_home_is_skipped(self):
        shutil.rmtree(self.home / ".codex")
        lines = set_hooks.install(self.settings, self.bashrc, self.home)
        self.assertFalse((self.home / ".codex").exists())
        self.assertTrue(any("건너뜀" in l and ".codex" in l for l in lines))

    # Claude 폴더가 없으면 건너뛰는지 확인한다.
    def test_missing_claude_home_is_skipped(self):
        shutil.rmtree(self.home / ".claude")
        lines = set_hooks.install(self.settings, self.bashrc, self.home)
        self.assertFalse((self.home / ".claude").exists())
        self.assertTrue(any("건너뜀" in l and ".claude" in l for l in lines))

    # 설정 파일이 깨져 있으면 main이 종료 코드 1로 실패를 알리는지 확인한다.
    def test_main_reports_failure_with_exit_1(self):
        bad = self.home / ".claude/settings.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("{broken", encoding="utf-8")
        err = io.StringIO()
        orig = set_hooks.install
        with (
            mock.patch.object(sys, "argv", ["set_hooks.py", "install"]),
            mock.patch.object(set_hooks, "install", lambda: orig(self.settings, self.bashrc, self.home)),
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(set_hooks.main(), 1)
        self.assertIn("설치 실패:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
