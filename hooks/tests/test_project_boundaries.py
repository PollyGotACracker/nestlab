"""외부 프로젝트 기록과 스킬 접근 경계 회귀 테스트"""
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
import check_invalid_response
import save_subagent_result
import sub_approval
import sub_docs
import sub_path
import sub_role
import sub_scope


# 경계 테스트는 kickoff 게이트와 분리한다(test_check_tool_use의 KickoffGate가 다룬다).
def setUpModule():
    patcher = patch.object(check_tool_use, "kickoff_missing", return_value=False)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class ProjectBoundaries(unittest.TestCase):
    # 임시 프로젝트와 다른 저장소 폴더를 준비한다.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        # 프로젝트가 OS 임시 폴더 안에 있어도 임시 폴더 허용 판정과 분리한다.
        patcher = patch.object(sub_path, "TEMP", self.root / "separate-temp")
        patcher.start()
        self.addCleanup(patcher.stop)
        # 경계 테스트는 템플릿 필수 절 검사와 분리한다(test_check_tool_use의 템플릿 테스트가 다룬다).
        skills = patch.object(sub_docs, "SKILLS", self.root / "skills")
        skills.start()
        self.addCleanup(skills.stop)
        called = patch.object(sub_scope, "skill_called", return_value=True)
        called.start()
        self.addCleanup(called.stop)
        self.project = self.root / "project"
        self.project.mkdir()
        self.other = self.root / "other"
        self.other.mkdir()
        self.git("init", str(self.project))
        self.child = self.project / "nested"
        self.child.mkdir()
        self.hub = self.root / "hub"
        self.hub.mkdir()

    # 임시 저장소에서 git 명령을 실행하는 보조 함수이다.
    def git(self, *args):
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout

    # runner별 대화 기록 파일을 만드는 보조 함수이다.
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

    # git 루트, 하위 폴더, worktree, 일반 폴더에서 프로젝트 루트를 구하는지 확인한다.
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
        self.assertEqual(sub_docs.doc("same", "input", nested), tree / "log/task/same/spec.md")

    # 짧은 경로와 긴 경로로 프로젝트 안의 읽기와 쓰기가 허용되는지 확인한다.
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

    # 프로젝트별 승인, 활성 작업, 결과, 사건 문서, director 범위가 저장소별로 분리되는지 확인한다.
    def test_project_approval_active_results_incidents_and_director_scope(self):
        task = "same"
        research = "# 조사\n\n### 최종 조사 원문\n\n```\n기록 없음\n```\n"
        for base in (self.project, self.other):
            sub_docs.doc(task, "state", base).parent.mkdir(parents=True)
            sub_docs.doc(task, "state", base).write_text(
                "### 실행 승인 범위\n\n- 사용자 승인 상태: " + ("승인" if base == self.project else "승인 대기"),
                encoding="utf-8")
            sub_docs.doc(task, "research", base).write_text(research, encoding="utf-8")
            sub_docs.doc(task, "input", base).write_text("# same 입력\n", encoding="utf-8")  # 사건 hook이 작업 ID를 찾는 문서
        rework = "작업 종류: 재작업\n명세 문서: log/task/same/spec.md"
        self.assertIsNone(sub_approval.approval_reason("계획 수정", rework, str(self.child)))
        self.assertIsNotNone(sub_approval.approval_reason("계획 수정", rework, str(self.other)))
        review = "작업 종류: 검증\n명세 문서: log/task/same/spec.md"
        self.assertIsNotNone(sub_approval.approval_reason("계획 수정", review, str(self.child)))
        reverify = "작업 종류: 재검증\n명세 문서: log/task/same/spec.md"
        self.assertIsNone(sub_approval.approval_reason("계획 수정", reverify, str(self.child)))
        self.assertIsNotNone(sub_approval.approval_reason("계획 수정", reverify, str(self.other)))
        for runner in ("claude", "codex"):
            transcript = self.transcript(self.project, runner, "명세 문서: log/task/same/spec.md")
            data = {"cwd": str(self.child), "session_id": runner, "transcript_path": transcript,
                    "agent_type": "rio", "last_assistant_message": "제 실수입니다."}
            # 사건 문서는 세션 저장소와 무관하게 hook 원본 저장소(여기서는 hub)에만 생긴다.
            with patch.object(check_invalid_response, "INCIDENT_ROOT", self.hub):
                check_invalid_response.record(data, runner)
                check_invalid_response.record({**data, "cwd": str(self.other), "session_id": runner + "x"}, runner)
            files = list((self.hub / "log/incident").glob(f"*-{runner}.md"))
            self.assertEqual(len(files), 1)
            self.assertIn("관련 작업 ID: same", files[0].read_text(encoding="utf-8"))
            self.assertIn(f"실행 위치: {self.child}", files[0].read_text(encoding="utf-8"))
            self.assertEqual(len(list((self.hub / "log/incident").glob(f"*-{runner}x.md"))), 1)
            self.assertFalse((self.project / "log/incident").exists())
            self.assertFalse((self.other / "log/incident").exists())
            for path, denied in (("log/task/new/status.md", False), ("src/new.py", True)):
                write = {**data, "tool_name": "Write", "tool_input": {
                    "file_path": str(self.project / path), "content": "# 기록"}}
                self.assertEqual(check_tool_use.check(write, runner) is not None, denied)
            request = "작업 종류: 조사\n조사 문서: log/task/same/research.md"
            child_transcript = self.transcript(self.project, runner, request, "조사 결과")
            save_data = {**data, "agent_type": "nico", "agent_transcript_path": child_transcript,
                         "last_assistant_message": "조사 결과"}
            save_subagent_result.save(save_data, runner)
            self.assertIn("조사 결과", sub_docs.doc(task, "research", self.child).read_text(encoding="utf-8"))
            self.assertEqual(sub_docs.doc(task, "research", self.other).read_text(encoding="utf-8"), research)
        self.assertEqual(sub_role.ROOT, HOOKS.parent)

    # 폴더 링크(junction 또는 symlink)를 만드는 보조 함수이다.
    def link(self, path, target):
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(target)],
                           check=True, capture_output=True)
        else:
            path.symlink_to(target, target_is_directory=True)

    # 다른 저장소 세션이 nestlab 사건 문서 폴더에만 접근하는지 확인한다.
    def test_other_repo_session_reaches_only_hub_incident_folder(self):
        # 허브 사건 폴더를 만든다.
        (self.hub / "log/incident").mkdir(parents=True)
        for runner in ("claude", "codex"):
            for role in ("buddy", "director"):
                # 다른 저장소 세션에서 허브 저장소 경로에 도구를 호출하는 검사를 실행한다.
                def check(path, tool):
                    return check_tool_use.check({"cwd": str(self.other), "agent_type": role,
                        "tool_name": tool, "tool_input": {"file_path": str(self.hub / path), "content": "x"}}, runner)
                # 사건 폴더만 허용하고 나머지 허브 경로는 막는지 확인한다.
                with patch.object(sub_path, "INCIDENT", self.hub / "log/incident"):
                    for tool in ("Write", "Read"):
                        self.assertIsNone(check("log/incident/x.md", tool), (runner, role, tool))
                    self.assertIsNotNone(check("hooks/x.py", "Write"), (runner, role))
                    self.assertIsNotNone(check("hooks/x.py", "Read"), (runner, role))
                    self.assertIsNotNone(check("log/state/x.md", "Write"), (runner, role))

    # 스킬 링크, 내부 자료, 보안 문서, 외부 쓰기 경계를 확인한다.
    def test_skills_links_resources_secrets_and_external_writes(self):
        # 원본 스킬 폴더와 비밀 파일 구조를 만든다.
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
                # 프로젝트 하위 폴더 세션에서 경로를 읽거나 쓰는 검사를 실행한다.
                def check(path, write=False):
                    return check_tool_use.check({"cwd": str(self.child), "agent_type": "buddy",
                        "tool_name": "Write" if write else "Read", "tool_input": {
                            "file_path": str(path), "content": "x"}}, runner)
                # 원본과 설치본에서 읽기와 쓰기 결과를 확인한다.
                for base in (skill, *installed):
                    self.assertIsNone(check(base / "SKILL.md"))
                    self.assertIsNone(check(base / "references/data.md"))
                    self.assertIsNotNone(check(base / "escape/data.md"))
                    self.assertIsNotNone(check(base / "secret-link/data.md"))
                    self.assertIsNotNone(check(base / ".env"))
                    self.assertIsNotNone(check(base / "SKILL.md", True))
                self.assertIsNotNone(check(self.other / "unrelated.md"))
                self.assertIsNone(check(settings))
                with patch.object(sub_scope, "approved", return_value=True):  # 메인 세션 승인 검사는 통과시킨다.
                    self.assertIsNotNone(check(settings, True))  # 설정 파일 쓰기는 승인 턴에도 막는다.
                self.assertIsNone(check(self.project / "log/state/new.md"))


if __name__ == "__main__":
    unittest.main()
