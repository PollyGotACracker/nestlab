"""linked worktree의 dirty 입력 전달과 담당 결과 반영, 충돌 및 실패 복구를 검증한다."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]
import sub_worktree as transfer
import sub_path


class TaskWorktree(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name).resolve() / "source"
        self.tree = Path(self.temp.name).resolve() / "worktree"
        self.source.mkdir()
        self.git("init")
        (self.source / "src").mkdir()
        (self.source / "src/a.py").write_text("original\n", encoding="utf-8")
        (self.source / "src/other.py").write_text("other\n", encoding="utf-8")
        self.task = "20261110-015333-transfer"
        self.spec = f"log/task/{self.task}/spec.md"
        (self.source / self.spec).parent.mkdir(parents=True)
        (self.source / self.spec).write_text(f"# spec\n- 작업 ID: {self.task}\n## 배정 범위\n- worker: src\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "initial")
        self.git("worktree", "add", "--detach", str(self.tree))
        (self.source / "src/a.py").write_text("dirty source\n", encoding="utf-8")
        (self.source / self.spec).write_text(f"# dirty spec\n- 작업 ID: {self.task}\n## 배정 범위\n- worker: src\n", encoding="utf-8")

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.source), *args], capture_output=True, text=True,
                                encoding="utf-8", env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
                                                      "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com"})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def prepare(self, files=None):
        return transfer.prepare(self.source, self.tree, self.spec, "worker", files or ["src/a.py"])

    def test_dirty_spec_and_source_delivered_only_owned_results_applied(self):
        self.prepare(["src/a.py", "src/new.py"])
        self.assertEqual((self.tree / self.spec).read_bytes(), (self.source / self.spec).read_bytes())
        self.assertEqual((self.tree / "src/a.py").read_text(encoding="utf-8"), "dirty source\n")
        (self.tree / "src/a.py").unlink()
        (self.tree / "src/new.py").write_text("new\n", encoding="utf-8")
        (self.tree / "src/other.py").write_text("unassigned\n", encoding="utf-8")
        self.assertEqual(transfer.apply(self.source, self.tree), ["src/a.py", "src/new.py"])
        self.assertFalse((self.source / "src/a.py").exists())
        self.assertEqual((self.source / "src/new.py").read_text(encoding="utf-8"), "new\n")
        self.assertEqual((self.source / "src/other.py").read_text(encoding="utf-8"), "other\n")
        self.assertFalse(transfer.metadata(self.source, self.tree).exists())

    def test_source_conflict_preserves_both_checkouts_and_metadata(self):
        self.prepare()
        (self.tree / "src/a.py").write_text("worker result\n", encoding="utf-8")
        (self.source / "src/a.py").write_text("external change\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "원본이 기준과 달라"):
            transfer.apply(self.source, self.tree)
        self.assertEqual((self.source / "src/a.py").read_text(encoding="utf-8"), "external change\n")
        self.assertEqual((self.tree / "src/a.py").read_text(encoding="utf-8"), "worker result\n")
        self.assertTrue(transfer.metadata(self.source, self.tree).exists())

    # 원본과 worktree 양쪽의 고정 prepare 호출만 좁은 경로 접근 예외로 인정한다.
    def test_fixed_transfer_command_from_both_checkout_roots(self):
        script = Path(transfer.__file__).resolve().parents[2] / "tools/manage_task_worktree.py"
        command = f'python -B "{script}" prepare --source "{self.source}" --worktree "{self.tree}" --spec {self.spec} --role worker --file src/a.py'
        for root in (self.source, self.tree):
            self.assertTrue(transfer.transfer_command(command, root))
            self.assertTrue(transfer.transfer_command(command, root, "worker"))
            self.assertFalse(transfer.transfer_command(command, root, "documenter"))
            event = {"agent_type": "worker", "agent_id": "child", "cwd": str(root), "tool_name": "Bash",
                     "tool_input": {"command": command, "description": "배정 파일 전달"}}
            for runner in ("claude", "codex"):
                self.assertIsNone(sub_path.check_paths(event, runner))
        event.update(cwd=str(self.tree), tool_name="Read", tool_input={"file_path": str(self.source / "src/a.py")})
        # 시험 원본이 OS 임시 폴더 안에 있으므로 임시 폴더 허용과 분리해 원본 읽기 차단만 확인한다.
        with patch.object(sub_path, "TEMP", self.source / "none"):
            self.assertIsNotNone(sub_path.check_paths(event, "claude"))
        self.assertFalse(transfer.transfer_command(command, self.source.parent))
        self.assertFalse(transfer.transfer_command(command + "; python other.py", self.tree))
        self.assertFalse(transfer.transfer_command(command.replace(" prepare ", " apply "), self.tree, "worker"))

    def test_scope_and_existing_worktree_changes_rejected(self):
        with self.assertRaisesRegex(ValueError, "배정 범위 밖"):
            self.prepare(["README.md"])
        (self.tree / "src/a.py").write_text("existing work\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "기존 변경"):
            self.prepare()
        self.assertEqual((self.tree / "src/a.py").read_text(encoding="utf-8"), "existing work\n")

    def test_task_identity_role_and_security_patterns_checked(self):
        for role in ("researcher", "reviewer"):
            with self.subTest(role=role), self.assertRaises(ValueError):
                transfer.prepare(self.source, self.tree, self.spec, role, ["src/a.py"])
        plan = self.source / self.spec
        original = plan.read_text(encoding="utf-8")
        plan.write_text(original.replace(self.task, "20261110-015334-other"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "작업 ID"):
            self.prepare()
        plan.write_text(original, encoding="utf-8")
        with patch("sub_path.secret_patterns", return_value=["**/a.py"]), self.assertRaisesRegex(ValueError, "보안 파일"):
            self.prepare()

    def test_prepare_failure_preserves_metadata_for_recovery(self):
        with patch.object(transfer, "write_files", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertTrue(transfer.metadata(self.source, self.tree).exists())
        transfer.recover(self.source, self.tree)
        self.assertEqual((self.tree / "src/a.py").read_text(encoding="utf-8"), "original\n")
        self.assertFalse(transfer.metadata(self.source, self.tree).exists())

    def test_apply_failure_rolls_back_and_retries(self):
        self.prepare(["src/a.py", "src/new.py"])
        (self.tree / "src/a.py").write_text("result\n", encoding="utf-8")
        (self.tree / "src/new.py").write_text("new\n", encoding="utf-8")
        original, failed = transfer.restore, False

        def fail_once(path, state):
            nonlocal failed
            if path == self.source / "src/new.py" and not failed:
                failed = True
                raise OSError("injected failure")
            return original(path, state)

        with patch.object(transfer, "restore", side_effect=fail_once), self.assertRaises(OSError):
            transfer.apply(self.source, self.tree)
        self.assertEqual((self.source / "src/a.py").read_text(encoding="utf-8"), "dirty source\n")
        self.assertFalse((self.source / "src/new.py").exists())
        transfer.apply(self.source, self.tree)
        self.assertEqual((self.source / "src/a.py").read_text(encoding="utf-8"), "result\n")

    # --source 생략 시 원본 추론, 비 worktree 거부, 미반영 기록이 있는 worker 보고 차단과 반영 후 허용을 확인한다.
    def test_origin_cli_and_handback_gate(self):
        import save_handback_report
        script = Path(transfer.__file__).resolve().parents[2] / "tools/manage_task_worktree.py"
        self.assertEqual(transfer.origin(self.tree), self.source)
        with self.assertRaisesRegex(ValueError, "지원 범위"):
            transfer.origin(self.source)
        event = {"tool_name": "SubagentHandback", "agent_type": "worker", "agent_id": "c", "cwd": str(self.tree),
                 "tool_input": {"message": "완료"}}
        self.assertIsNone(save_handback_report.replace(event))
        run = lambda *a: subprocess.run([sys.executable, "-B", str(script), *a], capture_output=True, text=True, encoding="utf-8")
        result = run("prepare", "--worktree", str(self.tree), "--spec", self.spec, "--role", "worker", "--file", "src/a.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        denied = save_handback_report.replace(event)
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        (self.tree / "src/a.py").write_text("result\n", encoding="utf-8")
        self.assertEqual(run("apply", "--worktree", str(self.tree)).returncode, 0)
        self.assertEqual((self.source / "src/a.py").read_text(encoding="utf-8"), "result\n")
        self.assertIsNone(save_handback_report.replace(event))
        self.assertEqual(run("apply", "--worktree", str(self.source)).returncode, 1)

    def test_path_traversal_and_links_rejected(self):
        for name in ("../outside", ".git/config", ".env", str(self.source / "src/a.py")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.prepare([name])
        link = self.source / "src/link.py"
        try:
            link.symlink_to(self.source / "src/a.py")
        except OSError as error:
            self.skipTest(str(error))
        with self.assertRaisesRegex(ValueError, "링크"):
            self.prepare(["src/link.py"])


if __name__ == "__main__":
    unittest.main()
