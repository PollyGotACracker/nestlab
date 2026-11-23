"""linked worktree 결과의 원본 반영과 보류 및 apply 호출 허용 범위를 검증한다."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HOOKS = Path(__file__).resolve().parents[3] / "hooks"
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
        for name in ("a.py", "b.py", "other.py"):
            (self.source / "src" / name).write_text("original\n", encoding="utf-8", newline="\n")
        self.git("add", ".")
        self.git("commit", "-m", "initial")
        self.git("worktree", "add", "--detach", str(self.tree))

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.source), *args], capture_output=True, text=True,
                                encoding="utf-8", env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
                                                      "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com"})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def write(self, root, name, text):
        (root / name).write_text(text, encoding="utf-8", newline="\n")

    # 원본이 기준 커밋과 같은 파일은 수정, 신규, 삭제 모두 반영하고 바뀌지 않은 파일은 건드리지 않는다.
    def test_unchanged_source_files_applied(self):
        self.write(self.tree, "src/a.py", "worker result\n")
        self.write(self.tree, "src/new.py", "new\n")
        (self.tree / "src/b.py").unlink()
        applied, held = transfer.apply(self.source, self.tree)
        self.assertEqual((sorted(applied), held), (["src/a.py", "src/b.py", "src/new.py"], []))
        self.assertEqual((self.source / "src/a.py").read_text(encoding="utf-8"), "worker result\n")
        self.assertEqual((self.source / "src/new.py").read_text(encoding="utf-8"), "new\n")
        self.assertFalse((self.source / "src/b.py").exists())
        self.assertEqual((self.source / "src/other.py").read_text(encoding="utf-8"), "original\n")

    # 원본이 그사이 바뀐 파일은 보류하고 원본 내용을 유지하며, 바뀌지 않은 파일은 함께 반영한다.
    def test_changed_source_file_held(self):
        self.write(self.tree, "src/a.py", "worker result\n")
        self.write(self.tree, "src/b.py", "worker b\n")
        self.write(self.source, "src/a.py", "external change\n")
        applied, held = transfer.apply(self.source, self.tree)
        self.assertEqual((applied, held), (["src/b.py"], ["src/a.py"]))
        self.assertEqual((self.source / "src/a.py").read_text(encoding="utf-8"), "external change\n")
        self.assertEqual((self.tree / "src/a.py").read_text(encoding="utf-8"), "worker result\n")
        self.assertEqual((self.source / "src/b.py").read_text(encoding="utf-8"), "worker b\n")

    # 원본에 같은 이름의 미커밋 신규 파일이 있거나 보안 파일이면 보류한다.
    def test_untracked_collision_and_secret_held(self):
        self.write(self.tree, "src/new.py", "worker\n")
        self.write(self.source, "src/new.py", "source new\n")
        self.write(self.tree, ".env", "KEY=1\n")
        applied, held = transfer.apply(self.source, self.tree)
        self.assertEqual((applied, sorted(held)), ([], [".env", "src/new.py"]))
        self.assertFalse((self.source / ".env").exists())
        self.assertEqual((self.source / "src/new.py").read_text(encoding="utf-8"), "source new\n")

    # worktree가 커밋한 변경은 기준 커밋과 비교해 반영한다.
    def test_committed_worktree_change_applied(self):
        self.write(self.tree, "src/a.py", "committed\n")
        for args in (("add", "."), ("commit", "-m", "work")):
            subprocess.run(["git", "-C", str(self.tree), *args], check=True, capture_output=True,
                           env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.com",
                                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e.com"})
        self.assertEqual(transfer.apply(self.source, self.tree), (["src/a.py"], []))
        self.assertEqual((self.source / "src/a.py").read_text(encoding="utf-8"), "committed\n")

    # core.autocrlf=true 저장소에서 원본 작업 파일이 기준 커밋과 줄바꿈만 다르면 반영하고, 내용이 다르면 보류한다.
    def test_crlf_source_file_applied_and_changed_held(self):
        self.git("config", "core.autocrlf", "true")
        for name in ("a.py", "b.py"):
            (self.source / "src" / name).write_bytes(b"original\r\n")
        self.write(self.tree, "src/a.py", "worker a\n")
        self.write(self.tree, "src/b.py", "worker b\n")
        (self.source / "src/b.py").write_bytes(b"external\r\n")
        applied, held = transfer.apply(self.source, self.tree)
        self.assertEqual((applied, held), (["src/a.py"], ["src/b.py"]))
        self.assertEqual((self.source / "src/b.py").read_bytes(), b"external\r\n")

    # 쓰기 실패 시 이미 쓴 파일을 되돌리고 오류를 전달한다.
    def test_apply_failure_rolls_back(self):
        self.write(self.tree, "src/a.py", "result\n")
        self.write(self.tree, "src/new.py", "new\n")
        original = transfer.restore

        def fail_new(path, state):
            if path == self.source / "src/new.py":
                raise OSError("injected failure")
            return original(path, state)

        with patch.object(transfer, "restore", side_effect=fail_new), self.assertRaises(OSError):
            transfer.apply(self.source, self.tree)
        self.assertEqual((self.source / "src/a.py").read_text(encoding="utf-8"), "original\n")
        self.assertFalse((self.source / "src/new.py").exists())

    # director의 apply 호출만 원본과 worktree 루트에서 경로 검사 예외로 인정한다.
    def test_apply_command_allowed_for_director_only(self):
        script = Path(transfer.__file__).resolve().parents[2] / "shared/tools/manage_task_worktree.py"
        command = f'python -B "{script}" apply --source "{self.source}" --worktree "{self.tree}"'
        for root in (self.source, self.tree):
            self.assertTrue(transfer.transfer_command(command, root))
            worker = {"agent_type": "worker", "agent_id": "a1", "cwd": str(root), "tool_name": "Bash",
                      "tool_input": {"command": command, "description": "결과 반영"}}
            self.assertIsNotNone(sub_path.check_paths(worker, "claude"))
            event = {"agent_type": "director", "cwd": str(root), "tool_name": "Bash",
                     "tool_input": {"command": command, "description": "결과 반영"}}
            self.assertIsNone(sub_path.check_paths(event, "claude"))
        self.assertFalse(transfer.transfer_command(command, self.source.parent))
        self.assertFalse(transfer.transfer_command(command + "; python other.py", self.tree))
        self.assertFalse(transfer.transfer_command(command.replace(" apply ", " prepare "), self.tree))

    # --source 생략 시 원본 추론, 비 worktree 거부, CLI의 반영과 보류 출력을 확인한다.
    def test_origin_and_cli(self):
        script = Path(transfer.__file__).resolve().parents[2] / "shared/tools/manage_task_worktree.py"
        self.assertEqual(transfer.origin(self.tree), self.source)
        with self.assertRaisesRegex(ValueError, "지원 범위"):
            transfer.origin(self.source)
        run = lambda *a: subprocess.run([sys.executable, "-B", str(script), *a], capture_output=True, text=True, encoding="utf-8")
        self.write(self.tree, "src/a.py", "result\n")
        self.write(self.tree, "src/b.py", "worker b\n")
        self.write(self.source, "src/b.py", "external\n")
        result = run("apply", "--worktree", str(self.tree))
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "src/a.py"), result.stderr)
        self.assertIn("src/b.py", result.stderr)
        self.assertEqual(run("apply", "--worktree", str(self.source)).returncode, 1)


if __name__ == "__main__":
    unittest.main()
