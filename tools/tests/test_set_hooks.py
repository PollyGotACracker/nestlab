#!/usr/bin/env python3
"""임시 bundle·HOME에서만 설치 도구의 설치·재설치·복구 동작을 검증한다.

실행: python -B tools/tests/test_set_hooks.py
실제 사용자 HOME이나 저장소 정책 파일은 install 대상으로 사용하지 않는다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[1] / "set_hooks"
LEGACY_PROJECT_POLICY = """# Project Claude ask/deny commands mapped to Codex execution-policy rules.
# These rules govern execution policy; they do not reproduce all Claude tool permissions.
# Exact allow commands are not expanded into broader prefix allow rules.

# Source: Bash(git checkout *)
prefix_rule(pattern=["git", "checkout"], decision="prompt")

# Source: Bash(git switch *)
prefix_rule(pattern=["git", "switch"], decision="prompt")

# Source: Bash(git restore *)
prefix_rule(pattern=["git", "restore"], decision="prompt")

# Source: Bash(git stash *)
prefix_rule(pattern=["git", "stash"], decision="prompt")

# Source: Bash(git tag *)
prefix_rule(pattern=["git", "tag"], decision="prompt")

# Source: Bash(rm *)
prefix_rule(pattern=["rm"], decision="prompt")

# Source: Bash(rmdir *)
prefix_rule(pattern=["rmdir"], decision="prompt")

# Source: Bash(git commit *)
prefix_rule(pattern=["git", "commit"], decision="forbidden")

# Source: Bash(git push *)
prefix_rule(pattern=["git", "push"], decision="forbidden")

# Source: Bash(git merge *)
prefix_rule(pattern=["git", "merge"], decision="forbidden")

# Source: Bash(git rebase *)
prefix_rule(pattern=["git", "rebase"], decision="forbidden")

# Source: Bash(git reset *)
prefix_rule(pattern=["git", "reset"], decision="forbidden")

# Source: Bash(git revert *)
prefix_rule(pattern=["git", "revert"], decision="forbidden")

# Source: Bash(git cherry-pick *)
prefix_rule(pattern=["git", "cherry-pick"], decision="forbidden")

# Source: Bash(git clean *)
prefix_rule(pattern=["git", "clean"], decision="forbidden")
"""
LEGACY_GLOBAL_BLOCK = """# >>> brainstack >>>
# brainstack 설치 도구가 관리하는 블록이다.
# brainstack Claude 설정 deny의 git 명령을 Codex 실행 정책으로 차단한다.
# 샌드박스 밖에서 실행되는 명령만 대상이다.

# Source: Bash(git commit *)
prefix_rule(pattern=["git", "commit"], decision="forbidden")

# Source: Bash(git push *)
prefix_rule(pattern=["git", "push"], decision="forbidden")

# Source: Bash(git merge *)
prefix_rule(pattern=["git", "merge"], decision="forbidden")

# Source: Bash(git rebase *)
prefix_rule(pattern=["git", "rebase"], decision="forbidden")

# Source: Bash(git reset *)
prefix_rule(pattern=["git", "reset"], decision="forbidden")

# Source: Bash(git revert *)
prefix_rule(pattern=["git", "revert"], decision="forbidden")

# Source: Bash(git cherry-pick *)
prefix_rule(pattern=["git", "cherry-pick"], decision="forbidden")

# Source: Bash(git clean *)
prefix_rule(pattern=["git", "clean"], decision="forbidden")
# <<< brainstack <<<"""
ORCA_HOOK = {"hooks": [{"type": "command", "command": "orca-session-hook"}]}
USER_CLAUDE = {
    "agent": "user-agent",
    "statusLine": {"type": "command", "command": "user-status"},
    "env": {"USER_ENV": "1", "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "9"},
    "permissions": {"allow": ["Bash(user-private *)"]},
    "hooks": {"SessionStart": [ORCA_HOOK]},
}
USER_CODEX_HOOKS = {"hooks": {"SessionStart": [ORCA_HOOK]}}
MANAGED_LABELS = ["Claude 설정", "Codex hooks", "Codex 설정", "Codex 실행 규칙", "Git Bash", "Claude 전역 지침",
                  "Codex 전역 지침"]
USER_BASHRC = "# user bashrc head\nalias ll='ls -l'\n"
USER_CLAUDE_MD = "# user global memory\n"
# settings 원본 끝에 붙여 모든 관리 항목을 바꾸는 코드이다.
CHANGED_SETTINGS = '''
CLAUDE_SETTINGS["agent"] = "rio"
CLAUDE_SETTINGS["env"] = {"NEW_ENV": "x"}
CLAUDE_SETTINGS["statusLine"] = {**CLAUDE_SETTINGS["statusLine"], "padding": 2}
CLAUDE_PERMISSION_RULES["ask"].remove("Bash(rmdir *)")
CLAUDE_PERMISSION_RULES["allow"].append("Bash(changed-allow *)")
CLAUDE_HOOK_RULES["SessionStart"][0]["hooks"][0]["statusMessage"] = "changed session hook"
del CLAUDE_HOOK_RULES["PreToolUse"]
CODEX_HOOK_RULES["SubagentStart"][0]["hooks"][0]["additionalContextLimit"] = 7000
CODEX_RULES_BLOCK = CODEX_RULES_BLOCK.replace('# <<< brainstack <<<', '# Source: changed\\nprefix_rule(pattern=["changed"], decision="prompt")\\n# <<< brainstack <<<')
BASHRC_BLOCK = BASHRC_BLOCK.replace("# <<< brainstack <<<", "alias changed=true\\n# <<< brainstack <<<")
CLAUDE_IMPORT_LINE = "@<BRAINSTACK>/changed.md"
'''


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="brainstack-hooks-test-")
        self.addCleanup(self.workspace.cleanup)
        self.temp = Path(self.workspace.name).resolve()
        self.bundle = self.temp / "bundle"
        self.home = self.temp / "home"
        self.tool = self.bundle / "tools" / "set_hooks"
        shutil.copytree(HOOKS_DIR, self.tool, ignore=shutil.ignore_patterns("__pycache__"))
        self.entry = self.bundle / "tools" / "set_hooks.py"
        shutil.copy2(HOOKS_DIR.parent / "set_hooks.py", self.entry)
        self.home.mkdir()
        (self.bundle / "AGENTS.md").write_text("isolated test principle\n", encoding="utf-8")
        self.local = self.bundle / ".codex/rules/permissions.rules"
        self.local.parent.mkdir(parents=True)
        self.local.write_text(LEGACY_PROJECT_POLICY, encoding="utf-8", newline="")
        self.global_rules = self.home / ".codex/rules/default.rules"
        self.global_rules.parent.mkdir(parents=True)
        self.global_rules_text = ('# user prefix\nprefix_rule(pattern=["user-command"], decision="allow")\n'
                                  + LEGACY_GLOBAL_BLOCK + "\n# user suffix\n")
        self.global_rules.write_text(self.global_rules_text, encoding="utf-8", newline="")
        self.claude = self.home / ".claude/settings.json"
        self.codex_hooks = self.home / ".codex/hooks.json"
        self.bashrc = self.home / ".bashrc"
        self.claude_md = self.home / ".claude/CLAUDE.md"
        self.record = self.home / ".brainstack/install-record.json"
        self.env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home), "PYTHONIOENCODING": "utf-8"}

    def run_cli(self, *args, failure=None):
        if failure is None:
            command = [sys.executable, "-B", str(self.entry), *args]
        else:
            driver = f"import sys; sys.path.insert(0, {str(self.tool)!r})\nimport sub_install as install, sub_paths\n"
            driver += "original=install.write_text\ndef fail(path,text):\n if path == sub_paths." + failure + ": raise OSError('injected write failure')\n return original(path,text)\n"
            driver += "install.write_text=fail\n"
            driver += "raise SystemExit(install.install(False))\n"
            command = [sys.executable, "-B", "-c", driver]
        return subprocess.run(command, cwd=self.bundle, env=self.env, capture_output=True, text=True, encoding="utf-8")

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # install 출력의 파일별 결과가 관리 대상 전부를 덮는지와 check 건수를 확인한다.
    def assert_install_report(self, result, statuses, missing=0):
        files = [line for line in result.stdout.split("\n[check]\n")[0].splitlines()
                 if line.startswith("[") and line.endswith((": 반영", ": 변경 없음"))]
        self.assertEqual([line.split("] ")[0] + "]" for line in files], [f"[{label}]" for label in MANAGED_LABELS],
                         result.stdout)
        for label, line in zip(MANAGED_LABELS, files):
            self.assertTrue(line.endswith(f": {statuses.get(label, statuses.get('*'))}"), line)
        self.assertIn("\n[check]\n", result.stdout)
        self.assertIn(f"미반영: {missing}건", result.stdout)
        self.assertEqual(result.returncode, 0 if missing == 0 else 1, result.stdout + result.stderr)

    def snapshot(self):
        return {str(p): p.read_bytes() for p in self.temp.rglob("*") if p.is_file()}

    def load(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def change_settings(self, code=CHANGED_SETTINGS):
        with open(self.tool / "sub_settings.py", "a", encoding="utf-8") as f:
            f.write(code)

    def seed_user_files(self):
        self.claude.parent.mkdir(parents=True, exist_ok=True)
        self.claude.write_text(json.dumps(USER_CLAUDE), encoding="utf-8")
        self.codex_hooks.write_text(json.dumps(USER_CODEX_HOOKS), encoding="utf-8")
        self.bashrc.write_text(USER_BASHRC, encoding="utf-8", newline="")
        self.claude_md.write_text(USER_CLAUDE_MD, encoding="utf-8", newline="")
        return {p: p.read_bytes() for p in (self.claude, self.codex_hooks, self.bashrc, self.claude_md, self.global_rules)}

    def seed_existing_record(self):
        backup = self.home / ".brainstack/backup/original.rules"
        backup.parent.mkdir(parents=True)
        backup.write_text("original global file", encoding="utf-8")
        record = {
            "version": 1, "installed_at": "original timestamp",
            "backup_dir": str(backup.parent),
            "backups": {str(self.global_rules): str(backup)},
            "items": {
                "codex_rules": {"prefix": "", "suffix": "", "created": False},
                "claude_settings": {"values": {}, "env": {}, "permissions": {}, "hooks": {}, "created": []},
            },
        }
        self.record.write_text(json.dumps(record), encoding="utf-8")
        return record

    def loader_groups(self, data, event):
        return [g for g in data["hooks"].get(event, []) if "load_agent.py" in json.dumps(g)]

    # MV1·MV5·MV6: 신규 설치, check, uninstall
    def test_new_install_check_and_uninstall(self):
        self.assertNotEqual(self.run_cli("check").returncode, 0)
        self.assert_install_report(self.run_cli("install"), {"*": "반영"})
        self.assertEqual(self.local.read_bytes(), LEGACY_PROJECT_POLICY.encode("utf-8"))
        installed = self.global_rules.read_text(encoding="utf-8")
        self.assertEqual(installed.count('decision="prompt"'), 7)
        self.assertEqual(installed.count('decision="forbidden"'), 8)
        self.assertTrue(installed.startswith("# user prefix\n"))
        self.assertTrue(installed.endswith("\n# user suffix\n"))
        self.assertIn('prefix_rule(pattern=["user-command"], decision="allow")', installed)
        self.assert_success(self.run_cli("check"))
        self.assert_success(self.run_cli("uninstall"))
        self.assertEqual(self.local.read_bytes(), LEGACY_PROJECT_POLICY.encode("utf-8"))
        self.assertEqual(self.global_rules.read_text(encoding="utf-8"),
                         self.global_rules_text.replace(LEGACY_GLOBAL_BLOCK, ""))

    # settings 원본 리터럴과 실제 설치 결과 대조
    def test_settings_literals_are_installed_and_user_allow_hooks_are_preserved(self):
        self.claude.parent.mkdir(parents=True)
        user_hook = {"hooks": [{"type": "command", "command": "user-orca-hook"}]}
        user = {"permissions": {"allow": ["Bash(user-private *)"]}, "hooks": {"SessionStart": [user_hook]}}
        self.claude.write_text(json.dumps(user), encoding="utf-8")
        self.change_settings('CLAUDE_PERMISSION_RULES["ask"][-1] = "Bash(custom-approval *)"\n'
                             'CLAUDE_HOOK_RULES["SessionStart"][0]["hooks"][0]["statusMessage"] = "test top hook literal"\n')
        self.assert_success(self.run_cli("install"))
        actual = self.load(self.claude)
        self.assertIn("Bash(user-private *)", actual["permissions"]["allow"])
        self.assertIn("Bash(custom-approval *)", actual["permissions"]["ask"])
        self.assertNotIn("Bash(rmdir *)", actual["permissions"]["ask"])
        self.assertEqual(actual["hooks"]["SessionStart"][0], user_hook)
        hook = actual["hooks"]["SessionStart"][1]["hooks"][0]
        self.assertEqual(hook["statusMessage"], "test top hook literal")
        self.assertIn(str(self.bundle), hook["command"])
        self.assertNotIn("<BRAINSTACK>", hook["command"])
        source = json.loads(subprocess.run(
            [sys.executable, "-B", "-c", "import json, sys; sys.path.insert(0, sys.argv[1]); import sub_merge; "
             "print(json.dumps(sub_merge.load_sources(), ensure_ascii=False))", str(self.tool)],
            env=self.env, capture_output=True, text=True, encoding="utf-8", check=True).stdout)
        for kind, rules in source["claude"]["permissions"].items():
            self.assertEqual([r for r in actual["permissions"][kind] if r in rules], rules)
        for event, groups in source["claude"]["hooks"].items():
            self.assertEqual([g for g in actual["hooks"][event] if g != user_hook], groups)
        codex = self.load(self.codex_hooks)
        self.assertEqual(codex["hooks"], source["codex_hooks"]["hooks"])
        for event in ("SessionStart", "SubagentStart"):
            hook = codex["hooks"][event][0]["hooks"][0]
            self.assertEqual(hook["additionalContextLimit"], 10000)
            self.assertIn("--include-role", hook["command"])
            self.assertIn(str(self.bundle), hook["commandWindows"])
        for key in ("agent", "env", "statusLine"):
            self.assertEqual(actual[key], source["claude"][key])
        self.assert_success(self.run_cli("uninstall"))
        self.assertEqual(self.load(self.claude), user)

    # MV2·MV7: 기존 기록의 재설치는 바뀌지 않은 기록 항목을 이어 쓰고 반복 실행은 noop이다.
    def test_existing_install_keeps_item_records_and_is_idempotent(self):
        before = self.seed_existing_record()
        self.assert_success(self.run_cli("install"))
        after = self.load(self.record)
        self.assertEqual(after["items"]["codex_rules"], before["items"]["codex_rules"])
        values = after["items"]["claude_settings"]["values"]
        self.assertEqual(values["agent"], {})
        self.assertEqual(values["statusLine"], {})
        self.assert_success(self.run_cli("check"))
        snapshot = self.snapshot()
        self.assert_install_report(self.run_cli("install"), {"*": "변경 없음"})
        self.assertEqual(self.snapshot(), snapshot)
        self.assert_success(self.run_cli("uninstall"))
        self.assertNotIn("brainstack >>>", self.global_rules.read_text(encoding="utf-8"))

    # MV4: 신규·기존 설치 dry-run은 파일을 바꾸지 않는다.
    def test_dry_run_does_not_write_new_or_existing_install(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.seed_existing_record()
                before = self.snapshot()
                result = self.run_cli("install", "--dry-run")
                self.assert_success(result)
                self.assertEqual(self.snapshot(), before)

    # MV3: 쓰기 실패는 전역 파일·기록을 복구한다.
    def test_global_and_record_write_failures_restore_files_and_record(self):
        self.seed_existing_record()
        for failure in ("CODEX_RULES", "RECORD"):
            with self.subTest(failure=failure):
                before = self.snapshot()
                self.assertNotEqual(self.run_cli("install", failure=failure).returncode, 0)
                after = self.snapshot()
                self.assertEqual({k: v for k, v in after.items() if "\\backup\\" not in k and "/backup/" not in k},
                                 {k: v for k, v in before.items() if "\\backup\\" not in k and "/backup/" not in k})

    # NV6·NV7: settings 변경 뒤 재설치는 모든 관리 항목을 재반영한다.
    # uninstall은 install이 넣은 항목을 지우고 그 밖의 사용자 내용은 보존한다.
    def test_reinstall_reapplies_all_managed_settings_and_uninstall_removes_them(self):
        originals = self.seed_user_files()
        self.assert_success(self.run_cli("install"))
        first = self.load(self.record)
        self.change_settings()
        self.assertNotEqual(self.run_cli("check").returncode, 0)
        result = self.run_cli("install")
        self.assert_success(result)

        claude = self.load(self.claude)
        self.assertEqual(claude["agent"], "rio")
        self.assertEqual(claude["statusLine"]["padding"], 2)
        self.assertEqual(claude["env"], {"USER_ENV": "1", "NEW_ENV": "x"})
        self.assertIn("Bash(user-private *)", claude["permissions"]["allow"])
        self.assertIn("Bash(changed-allow *)", claude["permissions"]["allow"])
        self.assertNotIn("Bash(rmdir *)", claude["permissions"]["ask"])
        self.assertNotIn("PreToolUse", claude["hooks"])
        self.assertEqual(claude["hooks"]["SessionStart"][0], ORCA_HOOK)
        session = self.loader_groups(claude, "SessionStart")
        self.assertEqual(len(session), 1)
        self.assertEqual(session[0]["hooks"][0]["statusMessage"], "changed session hook")
        self.assertEqual(len(self.loader_groups(claude, "SubagentStart")), 1)

        codex = self.load(self.codex_hooks)
        self.assertEqual(codex["hooks"]["SessionStart"][0], ORCA_HOOK)
        self.assertEqual(len(self.loader_groups(codex, "SessionStart")), 1)
        sub = self.loader_groups(codex, "SubagentStart")
        self.assertEqual(len(sub), 1)
        self.assertEqual(sub[0]["hooks"][0]["additionalContextLimit"], 7000)

        rules = self.global_rules.read_text(encoding="utf-8")
        self.assertIn('prefix_rule(pattern=["changed"], decision="prompt")', rules)
        self.assertEqual(rules.count("# >>> brainstack >>>"), 1)
        self.assertTrue(rules.startswith("# user prefix\n") and rules.endswith("\n# user suffix\n"))
        bashrc = self.bashrc.read_text(encoding="utf-8")
        self.assertTrue(bashrc.startswith(USER_BASHRC))
        self.assertIn("alias changed=true", bashrc)
        self.assertEqual(bashrc.count("# >>> brainstack >>>"), 1)
        claude_md = self.claude_md.read_text(encoding="utf-8")
        self.assertTrue(claude_md.startswith(USER_CLAUDE_MD))
        self.assertIn(f"@{self.bundle.as_posix()}/changed.md", claude_md)
        self.assertNotIn("AGENTS.md", claude_md)
        self.assert_success(self.run_cli("check"))

        after = self.load(self.record)
        values = after["items"]["claude_settings"]["values"]
        self.assertEqual(values["agent"], {})
        self.assertEqual(values["statusLine"], {})
        self.assertEqual(after["items"]["claude_settings"]["env"], {"NEW_ENV": {}})
        self.assertEqual(after["items"]["codex_agents"], first["items"]["codex_agents"])

        self.assert_success(self.run_cli("uninstall"))
        expected_claude = {**USER_CLAUDE, "env": {"USER_ENV": "1"}}
        del expected_claude["agent"], expected_claude["statusLine"]
        self.assertEqual(self.load(self.claude), expected_claude)
        self.assertEqual(self.load(self.codex_hooks), USER_CODEX_HOOKS)
        self.assertEqual(self.global_rules.read_text(encoding="utf-8"),
                         self.global_rules_text.replace(LEGACY_GLOBAL_BLOCK, ""))
        for path in (self.bashrc, self.claude_md):
            self.assertEqual(path.read_bytes(), originals[path], path)
        self.assertFalse(self.record.exists())

    # NV8: 재설치 dry-run·noop은 byte 변경이 없고 실패는 재설치 직전 상태로 복구한다.
    def test_reinstall_dry_run_noop_and_failure_rollback(self):
        self.seed_user_files()
        self.assert_success(self.run_cli("install"))
        snapshot = self.snapshot()
        self.assert_success(self.run_cli("install"))
        self.assertEqual(self.snapshot(), snapshot)
        self.change_settings()
        snapshot = self.snapshot()
        result = self.run_cli("install", "--dry-run")
        self.assert_success(result)
        self.assertIn("교체 마커 블록", result.stdout)
        self.assertIn("삭제 permissions.ask: Bash(rmdir *)", result.stdout)
        self.assertEqual(self.snapshot(), snapshot)
        for failure in ("CLAUDE_SETTINGS", "BASHRC", "CLAUDE_MD", "RECORD"):
            with self.subTest(failure=failure):
                result = self.run_cli("install", failure=failure)
                self.assertNotEqual(result.returncode, 0)
                after = self.snapshot()
                self.assertEqual({k: v for k, v in after.items() if k in snapshot}, snapshot)
        self.assert_success(self.run_cli("install"))
        snapshot = self.snapshot()
        self.assert_success(self.run_cli("install"))
        self.assertEqual(self.snapshot(), snapshot)

    # 설치 뒤 사용자가 바꾼 관리 항목은 재설치에서 settings 원본으로 다시 맞춘다.
    def test_reinstall_overwrites_user_edits_of_managed_items(self):
        self.seed_user_files()
        self.assert_success(self.run_cli("install"))
        bashrc = self.bashrc.read_text(encoding="utf-8")
        self.edit_json(self.claude, lambda d: d.update(agent="user-later"))
        self.edit_json(self.claude, lambda d: d["permissions"]["deny"].remove("Bash(git push *)"))
        self.edit_json(self.codex_hooks, lambda d: d["hooks"]["SubagentStart"].clear())
        self.bashrc.write_text(bashrc.replace("shift 2", "shift 3", 1), encoding="utf-8", newline="")
        self.assert_install_report(self.run_cli("install"), {
            "Claude 설정": "반영", "Codex hooks": "반영", "Git Bash": "반영", "*": "변경 없음"})
        claude = self.load(self.claude)
        self.assertEqual(claude["agent"], "buddy")
        self.assertEqual(claude["permissions"]["deny"].count("Bash(git push *)"), 1)
        self.assertEqual(len(self.loader_groups(claude, "SessionStart")), 1)
        self.assertEqual(len(self.loader_groups(self.load(self.codex_hooks), "SubagentStart")), 1)
        self.assertEqual(self.bashrc.read_text(encoding="utf-8"), bashrc)
        self.assert_success(self.run_cli("check"))
        # 사용자가 hooks = false로 바꾸면 install은 그 값을 두고 check 미반영으로 1을 반환한다.
        config = self.home / ".codex/config.toml"
        config.write_text(config.read_text(encoding="utf-8").replace("hooks = true", "hooks = false"),
                          encoding="utf-8", newline="")
        result = self.run_cli("install")
        self.assert_install_report(result, {"*": "변경 없음"}, missing=1)
        self.assertIn("미반영  [features] hooks = true", result.stdout)

    def edit_json(self, path, change):
        data = self.load(path)
        change(data)
        path.write_text(json.dumps(data), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
