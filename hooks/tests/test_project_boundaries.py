"""외부 프로젝트 기록과 Skill 접근 경계 회귀 테스트이다."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import check_tool_use
import record_incident
import save_agent_result
import sub_approval
import sub_docs
import sub_path
import sub_role


class ProjectBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.other = self.root / "other"
        self.other.mkdir()
        self.git("init", str(self.project))
        self.child = self.project / "nested"
        self.child.mkdir()

    def git(self, *args):
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout

    def transcript(self, base, runner, prompt="계획 수정", message="제 실수입니다."):
        path = base / f"{runner}.jsonl"
        rows = []
        for role, text in (("user", prompt), ("assistant", message)):
            if runner == "codex":
                rows.append({"type": "response_item", "payload": {
                    "type": "message", "role": role, "content": [{"text": text}]}})
            else:
                rows.append({"type": role, "message": {"content": text}})
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
        return str(path)

    def test_git_root_subfolder_worktree_and_plain_folder(self):
        self.assertEqual(sub_docs.project_root(self.child), self.project)
        self.assertEqual(sub_docs.project_root(self.other), self.other)
        self.git("-C", str(self.project), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "--allow-empty", "-m", "test")
        tree = self.root / "worktree"
        self.git("-C", str(self.project), "worktree", "add", "-b", "test-worktree", str(tree))
        nested = tree / "nested"
        nested.mkdir()
        self.assertEqual(sub_docs.project_root(nested), tree)
        self.assertEqual(sub_docs.doc("same", "input", nested), tree / "log/state/same-input.md")
        self.assertEqual(sub_docs.active(nested), tree / "log/state/.active")

    @unittest.skipUnless(os.name == "nt", "Windows 8.3 경로 테스트")
    def test_short_and_long_project_paths_allow_reads_and_writes(self):
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(self.project), buffer, len(buffer))
        self.assertGreater(length, 0)
        short = buffer.value
        if short.casefold() == str(self.project).casefold():
            self.skipTest("테스트 볼륨에 8.3 별칭이 없음")
        self.assertEqual(Path(short).resolve(), self.project)
        with patch.object(sub_path, "TEMP", self.root / "separate-temp"):
            for cwd in (str(self.child), short + "/nested"):
                self.assertEqual(sub_docs.project_root(cwd), self.project)
                for base in (short, str(self.project)):
                    for runner in ("claude", "codex"):
                        for tool in ("Read", "Write"):
                            data = {"cwd": cwd, "agent_type": "buddy", "tool_name": tool,
                                    "tool_input": {"file_path": base + "/log/state/new.md", "content": "x"}}
                            self.assertIsNone(check_tool_use.check(data, runner), (cwd, base, runner, tool))

    def test_project_approval_active_results_incidents_and_director_scope(self):
        task = "same"
        research = "# 조사\n\n### 최종 조사 원문\n\n```\n기록 없음\n```\n"
        for base in (self.project, self.other):
            sub_docs.doc(task, "input", base).parent.mkdir(parents=True)
            sub_docs.doc(task, "input", base).write_text(
                "### 실행 승인 범위\n\n- 승인 상태: " + ("승인" if base == self.project else "승인 대기"),
                encoding="utf-8")
            sub_docs.doc(task, "research", base).write_text(research, encoding="utf-8")
        rework = "작업 종류: 재작업\n입력 문서: log/state/same-input.md"
        self.assertIsNone(sub_approval.approval_reason("계획 수정", rework, str(self.child)))
        self.assertIsNotNone(sub_approval.approval_reason("계획 수정", rework, str(self.other)))
        sub_approval.remember({"cwd": str(self.child), "session_id": "session"}, "researcher", rework)
        self.assertTrue((sub_docs.active(self.project) / "session.json").is_file())
        self.assertFalse(sub_docs.active(self.other).exists())
        for runner in ("claude", "codex"):
            transcript = self.transcript(self.project, runner, "입력 문서: log/state/same-input.md")
            data = {"cwd": str(self.child), "session_id": runner, "transcript_path": transcript,
                    "agent_type": "rio", "last_assistant_message": "제 실수입니다."}
            record_incident.record(data, runner)
            files = list((self.project / "log/incident").glob(f"*-{runner}.md"))
            self.assertEqual(len(files), 1)
            self.assertIn("관련 작업 ID: same", files[0].read_text(encoding="utf-8"))
            self.assertFalse((self.other / "log/incident").exists())
            for path, denied in (("log/state/new.md", False), ("src/new.py", True),
                                 ("log/state/.active/new.json", True)):
                write = {**data, "tool_name": "Write", "tool_input": {
                    "file_path": str(self.project / path), "content": "# 기록"}}
                self.assertEqual(check_tool_use.check(write, runner) is not None, denied)
            request = "작업 종류: 조사\n조사 문서: log/state/same-research.md"
            child_transcript = self.transcript(self.project, runner, request, "조사 결과")
            save_data = {**data, "agent_type": "nico", "agent_transcript_path": child_transcript,
                         "last_assistant_message": "조사 결과"}
            save_agent_result.save(save_data, runner)
            self.assertIn("조사 결과", sub_docs.doc(task, "research", self.child).read_text(encoding="utf-8"))
            self.assertEqual(sub_docs.doc(task, "research", self.other).read_text(encoding="utf-8"), research)
        self.assertEqual(sub_role.ROOT, HOOKS.parent)

    def link(self, path, target):
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(target)],
                           check=True, capture_output=True)
        else:
            path.symlink_to(target, target_is_directory=True)

    def test_skills_links_resources_secrets_and_external_writes(self):
        home = self.root / "home"
        skills = self.root / "source-skills"
        skill = skills / "test"
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_text("skill", encoding="utf-8")
        (skill / "references/data.md").write_text("resource", encoding="utf-8")
        self.link(skill / "escape", self.other)
        (skill / "secrets").mkdir()
        self.link(skill / "secret-link", skill / "secrets")
        installed = []
        for folder in (".agents", ".claude", ".codex"):
            base = home / folder / "skills"
            base.mkdir(parents=True)
            self.link(base / "test", skill)
            installed.append(base / "test")
        settings = home / ".claude/settings.json"
        settings.write_text(json.dumps({"permissions": {"deny": ["Read(//**/secrets/**)", "Read(//**/.env)"]}}),
                            encoding="utf-8")
        with patch.object(sub_path, "HOME", home), patch.object(sub_path, "SKILLS", skills), \
             patch.object(sub_path, "GLOBAL", (home / ".claude", home / ".codex")), \
             patch.object(sub_path, "SETTINGS", settings), patch.object(sub_path, "TEMP", self.root / "temp"):
            for runner in ("claude", "codex"):
                def check(path, write=False):
                    return check_tool_use.check({"cwd": str(self.child), "agent_type": "buddy",
                        "tool_name": "Write" if write else "Read", "tool_input": {
                            "file_path": str(path), "content": "x"}}, runner)
                for base in (skill, *installed):
                    self.assertIsNone(check(base / "SKILL.md"))
                    self.assertIsNone(check(base / "references/data.md"))
                    self.assertIsNotNone(check(base / "escape/data.md"))
                    self.assertIsNotNone(check(base / "secret-link/data.md"))
                    self.assertIsNotNone(check(base / ".env"))
                    self.assertIsNotNone(check(base / "SKILL.md", True))
                self.assertIsNotNone(check(self.other / "unrelated.md"))
                self.assertIsNone(check(settings))
                self.assertIsNotNone(check(settings, True))
                self.assertIsNone(check(self.project / "log/state/new.md"))


if __name__ == "__main__":
    unittest.main()
