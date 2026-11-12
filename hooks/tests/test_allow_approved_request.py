"""allow_approved_request.py 동작 테스트"""
import sys
from pathlib import Path

# 진입 스크립트와 공용 모듈 폴더를 불러온다.
HOOKS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HOOKS), str(HOOKS / "common"), str(HOOKS / "check_tool_use")]

import io
import ast
import fnmatch
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import allow_approved_request as hook
import check_tool_use
import sub_role
import sub_path
import sub_task_docs
import sub_write


# Claude 대화 기록 한 줄을 JSON 문자열로 만든다.
def claude_line(role: str, text: str) -> str:
    content = text if role == "user" else [{"type": "text", "text": text}]
    return json.dumps({"type": role, "message": {"content": content}}, ensure_ascii=False)


# Codex rollout의 session_meta 줄을 JSON 문자열로 만든다.
def codex_meta(**payload) -> str:
    return json.dumps({"type": "session_meta", "payload": payload}, ensure_ascii=False)


# Codex rollout의 메시지 줄을 JSON 문자열로 만든다.
def codex_msg(role: str, text: str) -> str:
    return json.dumps({"type": "response_item", "payload": {"type": "message", "role": role,
                                                            "content": [{"type": "input_text", "text": text}]}},
                      ensure_ascii=False)



# decide 결과를 "allow", "deny", None으로 줄여 반환한다.
def verdict(data, runner):
    out = hook.decide(data, runner)
    return out["behavior"] if isinstance(out, dict) else out

class AllowApprovedRequest(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"NESTLAB_AGENT": "director"})
        environment.start()
        self.addCleanup(environment.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        p = patch.object(sub_role, "CODEX_SESSIONS", self.root)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    # 사용자 마지막 메시지가 user인 Claude 기록과 도구 입력으로 판정한다.
    def claude(self, user, command="python a.py", tool="Bash", **extra):
        path = self.root / "t.jsonl"
        kickoff = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "1", "name": "Skill", "input": {"skill": "nestlab-kickoff"}}]}})
        path.write_text(kickoff + "\n" + claude_line("assistant", "계획") + "\n" + claude_line("user", user) + "\n",
                        encoding="utf-8")
        return verdict({"tool_name": tool, "tool_input": {"command": command, "description": "커맨드 실행"},
                            "transcript_path": str(path), **extra}, "claude")

    # 승인 턴에 PreToolUse를 통과한 스크립트는 allow이고 git 비가역 작업은 출력 없이 승인 창을 유지한다.
    def test_permission_request_approval_turn(self):
        self.assertEqual(self.claude("승인", "python a.py"), "allow")
        self.assertIsNone(self.claude("승인", "git commit -m x"))
        for command in ("python a.py", "git commit -m x"):
            self.assertEqual(self.claude("안 B로 해", command), "deny")  # 승인 키워드가 없으면 PreToolUse 사유로 deny한다.

    # 질문 도구는 승인 여부와 관계없이 출력 없이 둔다.
    def test_permission_request_question_tools(self):
        for tool in ("AskUserQuestion", "ExitPlanMode", "request_user_input"):
            self.assertIsNone(self.claude("승인", tool=tool), tool)
            self.assertIsNone(self.claude("안 B로 해", tool=tool), tool)

    # 허용 목록의 검증 커맨드는 allow이며 임의 스크립트가 섞이면 runner 권한 판정을 따른다.
    def test_permission_request_allow_list(self):
        unit = "python -m unittest discover -s hooks/tests"
        self.assertEqual(self.claude("안 B로 해", unit), "allow")
        self.assertEqual(self.claude("안 B로 해", unit + " && python a.py"), "deny")
        self.assertEqual(self.claude("안 B로 해", unit, permission_mode="dontAsk"), "allow")

    # fd 병합만 제거하며 따옴표 안 인자와 파일 쓰기 리다이렉트는 원문대로 남긴다.
    def test_command_parts_preserves_quoted_fd_argument(self):
        generator = 'nestlab-task-docs --task 20261107-130000-example --title "example 2>&1; title" --stage review'
        self.assertEqual(sub_write.command_parts(generator + " 2>&1 && git status", preserve_quotes=True),
                         [generator, "git status"])
        command = "python -m unittest discover -s hooks/tests"
        self.assertEqual(sub_write.command_parts(command + " 2>&1 | tail -20", preserve_quotes=True),
                         [command, "tail -20"])
        self.assertIsNone(check_tool_use.script_reason(command + " 2>&1 | tail -20"))
        for redirection in (" > result.txt", " &> result.txt", " 2> result.txt"):
            self.assertIsNotNone(check_tool_use.main_write_reason(command + redirection))
            self.assertTrue(any("result.txt" in part for part in sub_write.command_parts(command + redirection, preserve_quotes=True)))

    # 승인 전 원본 생성기는 두 hook을 통과하고 출력 탈출과 임의 스크립트는 거부한다.
    def test_task_docs_pretool_and_permission_entry(self):
        script = self.root / "tools" / "create_task_docs.py"
        base = 'python tools/create_task_docs.py --task 20261107-130000-example --title "한글 제목" --stage review'
        transcript = self.root / "before-approval.jsonl"
        transcript.write_text(claude_line("user", "문서를 준비해 주세요.") + "\n", encoding="utf-8")
        with patch.object(hook, "TASK_DOCS_SCRIPT", script):
            for runner, shell in (("claude", "Bash"), ("codex", "PowerShell")):
                data = {"agent_type": "director", "cwd": str(self.root), "transcript_path": str(transcript),
                        "tool_name": shell, "tool_input": {"command": base, "description": "검토 문서 생성"}}
                for command in (base, base + ' --root "."', base.replace("tools/create_task_docs.py", f'"{script}"'),
                                base.replace("python ", "python -B -X utf8 ", 1),
                                base.replace('"한글 제목"', '"*"'), base.replace('"한글 제목"', "'{a,b}'"),
                                base.replace('"한글 제목"', "title") + " && git status"):
                    event = {**data, "tool_input": {**data["tool_input"], "command": command}}
                    with self.subTest(runner=runner, command=command):
                        self.assertIsNone(check_tool_use.check(event, runner))
                        self.assertEqual(verdict(event, runner), "allow")
                rejected = (base + ' --root ".."', base.replace("tools/create_task_docs.py", "tools/other.py"),
                            base + " && python tools/other.py", base + " --unknown x",
                            base.replace('"한글 제목"', "title") + ' --root ".."',
                            base + ' --root "$HOME"', base + " --root *", base + " --root ~/.codex",
                            base.replace("python ", "python -B -X arbitrary ", 1),
                            base.replace('"한글 제목"', "*"), base.replace('"한글 제목"', "{a,b}"),
                            base.replace("20261107-130000-example", "../escape"),
                            base + " --stage kickoff")
                for command in rejected:
                    event = {**data, "tool_input": {**data["tool_input"], "command": command}}
                    with self.subTest(runner=runner, command=command):
                        self.assertIsNotNone(check_tool_use.check(event, runner))
                        self.assertEqual(verdict(event, runner), "deny")
            self.assertFalse(hook.in_allow_list(base.replace("review", "unknown"), self.root))

    # 외부 프로젝트는 설치된 고정 커맨드를 사용하고 원본 경로 직접 접근은 거부한다.
    def test_external_project_trusted_generator_and_report_output(self):
        python = str(Path(sys.executable).resolve())
        script = str(hook.TASK_DOCS_SCRIPT.resolve())
        direct = f'"{python}" -B -X utf8 "{script}" --print-template approval-implement'
        home, path_value = self.install_launcher()
        args = "nestlab-task-docs"
        create = args + f' --task 20261107-130000-example --title "외부 프로젝트" --stage review --root "{self.root}"'
        with patch.object(sub_path, "TEMP", self.root / "os-temp"), \
                patch.dict(os.environ, {"PATH": path_value}), patch.object(hook, "TASK_DOCS_HOME", home):
            for runner, shell, prefix in (("claude", "Bash", ""), ("codex", "PowerShell", "& ")):
                data = {"agent_type": "director", "cwd": str(self.root), "tool_name": shell,
                        "tool_input": {"description": "외부 프로젝트 작업 문서 준비"}}
                for command in (create, args + " --print-template approval-implement", args + " --print-template approval-research"):
                    event = {**data, "tool_input": {**data["tool_input"], "command": prefix + command}}
                    self.assertIsNone(check_tool_use.check(event, runner))
                    self.assertEqual(verdict(event, runner), "allow")
                for command in (create.replace(str(self.root), str(self.root.parent)),
                                direct, direct.replace(script, str(Path(script).with_name("other.py"))),
                                args + " --print-template ../other", args + " --print-template approval-implement --root ."):
                    event = {**data, "tool_input": {**data["tool_input"], "command": prefix + command}}
                    self.assertIsNotNone(check_tool_use.check(event, runner))
                    self.assertEqual(verdict(event, runner), "deny")
                template = hook.TASK_DOCS_SCRIPT.parents[1] / "shared" / "templates" / "workflow" / "approval.md"
                self.assertIsNotNone(check_tool_use.check({**data, "tool_name": "Read",
                                                          "tool_input": {"file_path": str(template)}}, runner))
                self.assertIsNotNone(check_tool_use.check({**data, "agent_type": "worker", "agent_id": "child",
                                                          "tool_input": {"command": prefix + direct}}, runner))
        fixed = ["cmd", "/d", "/c", "nestlab-task-docs"] if sys.platform == "win32" else ["nestlab-task-docs"]
        # 명세 문서는 경과 문서의 작업 단계가 조사일 때만 생성되므로 착수 생성 뒤 단계를 기록한다.
        run = lambda stage: subprocess.run(fixed + ["--task", "20261107-130000-example", "--title", "외부 프로젝트",
                                                    "--stage", stage, "--root", str(self.root)],
                                           cwd=self.root, env={**os.environ, "PATH": path_value}, check=True, capture_output=True)
        run("kickoff")
        status = self.root / "log" / "task" / "20261107-130000-example" / "status.md"
        status.write_text(status.read_text(encoding="utf-8").replace("- 작업 단계: 착수", "- 작업 단계: 조사"), encoding="utf-8")
        run("review")
        spec = self.root / "log" / "task" / "20261107-130000-example" / "spec.md"
        self.assertIn("외부 프로젝트", spec.read_text(encoding="utf-8"))
        self.assertIsNone(check_tool_use.check({"agent_type": "worker", "agent_id": "child", "cwd": str(self.root),
                                                "tool_name": "Read", "tool_input": {"file_path": str(spec)}}, "claude"))

    # 승인 전 양식 출력의 읽기 결합을 허용하고 생성 및 임의 스크립트 결합은 차단한다.
    def test_template_output_with_read_commands_before_approval(self):
        home, path_value = self.install_launcher()
        output = "nestlab-task-docs --print-template approval-research"
        transcript = self.root / "before-approval.jsonl"
        transcript.write_text(claude_line("user", "양식을 확인해 주세요.") + "\n", encoding="utf-8")
        with patch.dict(os.environ, {"PATH": path_value}), patch.object(hook, "TASK_DOCS_HOME", home):
            for runner, shell in (("claude", "Bash"), ("codex", "PowerShell")):
                generator = 'nestlab-task-docs --task 20261107-130000-example --title "example title; value" --stage kickoff'
                for command in (output + "; git status --short", "git status --short; " + output,
                                generator + "; git status", generator + " && nestlab-task-docs --print-template question-kickoff"):
                    event = {"agent_type": "director", "cwd": str(self.root), "transcript_path": str(transcript),
                             "tool_name": shell, "tool_input": {"command": command, "description": "양식 출력과 상태 확인"}}
                    with self.subTest(runner=runner, command=command):
                        self.assertIsNone(check_tool_use.check(event, runner))
                        self.assertEqual(verdict(event, runner), "allow")
                for command in (output + "; python other.py", output + " --root .; git status",
                                generator + "; python other.py", generator + " --root ..; git status"):
                    event = {"agent_type": "director", "cwd": str(self.root), "transcript_path": str(transcript),
                             "tool_name": shell, "tool_input": {"command": command, "description": "차단 조건 확인"}}
                    with self.subTest(runner=runner, command=command):
                        self.assertIsNotNone(check_tool_use.check(event, runner))
                        self.assertEqual(verdict(event, runner), "deny")
            fixed = ["cmd", "/d", "/c", "nestlab-task-docs"] if sys.platform == "win32" else ["nestlab-task-docs"]
            result = subprocess.run(fixed + ["--print-template", "approval-research"], cwd=self.root,
                                    env={**os.environ, "PATH": path_value}, check=True, capture_output=True)
            expected = hook.TASK_DOCS_SCRIPT.parents[1] / "shared" / "templates" / "workflow" / "approval-research.md"
            self.assertEqual(result.stdout.decode("utf-8").replace("\r\n", "\n").strip(),
                             expected.read_text(encoding="utf-8").strip())

    def install_launcher(self):
        home = self.root / "command-home"
        for path, body in sub_task_docs.launcher_files(home).items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body.encode("utf-8"))
            if sys.platform != "win32":
                path.chmod(0o755)
        path_value = str(home / ".local" / "bin") + os.pathsep + os.environ.get("PATH", "")
        return home, path_value

    def test_fixed_command_uses_only_installed_launcher(self):
        home, path_value = self.install_launcher()
        command = 'nestlab-task-docs --task 20261107-130000-example --title "외부 프로젝트" --stage review'
        with patch.dict(os.environ, {"PATH": path_value}), patch.object(hook, "TASK_DOCS_HOME", home):
            for runner, shell in (("claude", "Bash"), ("codex", "PowerShell")):
                data = {"agent_type": "director", "cwd": str(self.root), "tool_name": shell,
                        "tool_input": {"command": command, "description": "작업 문서 생성"}}
                self.assertIsNone(check_tool_use.check(data, runner))
                self.assertEqual(verdict(data, runner), "allow")
                for text in ("nestlab-task-docs --print-template approval-implement", "nestlab-task-docs --print-template approval-research"):
                    self.assertTrue(hook.task_docs_command(text, self.root))
            short_name = self.root / "ADMINI~1" / "project"
            short_name.mkdir(parents=True)
            self.assertTrue(hook.task_docs_command(command + f' --root "{short_name}"', short_name))
            self.assertFalse(hook.task_docs_command(command + ' --root "~/project"', short_name))
            found = Path(__import__("shutil").which("nestlab-task-docs"))
            found.write_bytes(b"unknown user executable")
            self.assertFalse(hook.task_docs_command(command, self.root))
            self.assertIsNotNone(check_tool_use.check(data, runner))
            self.assertEqual(verdict(data, runner), "deny")

    # PreToolUse를 통과한 메인 읽기는 allow이며 쓰기는 runner 권한 판정을 따른다.
    def test_permission_request_read_before_approval(self):
        for command in ('python -I -X utf8 -c "print(1)"', "sed -n '/^## 경로 목록/,$p' AGENTS.md"):
            self.assertEqual(self.claude("안 B로 해", command), "allow", command)
        for command in ("cp a.txt b.txt", "python -c \"open('a','w')\"", "git commit -m x"):
            self.assertEqual(self.claude("안 B로 해", command), "deny", command)
        self.assertEqual(self.claude("안 B로 해", "print(1)", tool="Edit"), "deny")

    # director 외 다섯 역할의 읽기 셸 커맨드는 allow이고 파일을 바꾸는 커맨드는 두 runner 모두 None이다.
    def test_permission_request_non_director_read_shell(self):
        for runner in ("claude", "codex"):
            for role in ("assistant", "researcher", "reviewer", "worker", "documenter"):
                write = "deny" if role == "researcher" else "allow"  # researcher는 읽기 전용이라 PreToolUse가 쓰기를 deny한다.
                for command, expected in (("cat AGENTS.md", "allow"), ("grep -n x AGENTS.md", "allow"), ("git status", "allow"),
                                          ("rm a.txt", write), ("echo x > a.txt", write), ("sed -i s/a/b/ a.txt", write),
                                          ("git checkout master", None)):
                    with self.subTest(runner=runner, role=role, command=command):
                        data = {"agent_type": role, "tool_name": "Bash", "cwd": str(Path(__file__).resolve().parents[2]),
                                "tool_input": {"command": command, "description": "읽기"}}
                        self.assertEqual(verdict(data, runner), expected)

    # main()은 검사 위반 호출을 deny JSON으로, 질문 도구를 빈 출력으로 반환한다.
    def test_permission_request_output(self):
        path = self.root / "t.jsonl"
        path.write_text(claude_line("assistant", "계획") + "\n" + claude_line("user", "다시 봐") + "\n", encoding="utf-8")

        def run(tool):
            out = io.StringIO()
            data = {"tool_name": tool, "tool_input": {"command": "git commit -m x"}, "transcript_path": str(path)}
            with patch.object(sys, "argv", ["allow_approved_request.py", "--runner", "claude"]), \
                 patch.object(sys, "stdin", io.StringIO(json.dumps(data))), patch.object(sys, "stdout", out):
                hook.main()
            return out.getvalue()
        self.assertEqual(json.loads(run("Bash"))["hookSpecificOutput"]["decision"]["behavior"], "deny")
        self.assertEqual(run("AskUserQuestion"), "")

    # 하네스 메시지와 사용자 승인 여부는 메인 쓰기의 자동 허용 범위를 바꾸지 않는다.
    def test_permission_request_survives_harness(self):
        path = self.root / "h.jsonl"
        lines = [claude_line("assistant", "계획"), claude_line("user", "승인"),
                 claude_line("user", "Skill /x is already loaded"),
                 claude_line("user", "Another Claude session sent a message:\n보고")]
        data = {"tool_name": "Bash", "tool_input": {"command": "python a.py"}, "transcript_path": str(path)}
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(verdict(data, "claude"), "deny")
        path.write_text("\n".join(lines + [claude_line("user", "다시 봐")]) + "\n", encoding="utf-8")
        self.assertEqual(verdict(data, "claude"), "deny")

    # 이전 명칭의 구현 승인 상태가 있는 작업 폴더를 만들고 프로젝트 경로를 반환한다.
    def project(self, approval="승인", task="T1"):
        proj = self.root / "proj"
        folder = proj / "log" / "task" / task
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "status.md").write_text(f"# t\n\n## 실행 승인 범위\n\n- 사용자 승인 상태: {approval}\n", encoding="utf-8")
        (folder / "spec.md").write_text("old\n", encoding="utf-8")
        return proj

    # 서브에이전트 호출 입력을 만든다. runner가 claude면 agent_id와 agent 기록, codex면 부모 thread가 있는 rollout을 쓴다.
    def child(self, runner, first, proj, role=None, tool="Edit", parent_user="안 B로 해"):
        extra = {"agent_type": role} if role else {}
        if runner == "claude":
            main, agent = self.root / "m.jsonl", self.root / "agent.jsonl"
            main.write_text(claude_line("assistant", "계획") + "\n" + claude_line("user", parent_user) + "\n", encoding="utf-8")
            agent.write_text(claude_line("user", first) + "\n", encoding="utf-8")
            extra.update(agent_id="a1", transcript_path=str(main), agent_transcript_path=str(agent))
        else:
            (self.root / "rollout-1-P.jsonl").write_text(
                codex_meta() + "\n" + codex_msg("user", parent_user) + "\n", encoding="utf-8")
            rollout = self.root / "rollout-1-C.jsonl"
            rollout.write_text(codex_meta(source={"subagent": {"thread_spawn": {"parent_thread_id": "P"}}}) + "\n"
                               + (codex_msg("user", first) + "\n" if first else ""), encoding="utf-8")
            extra.update(transcript_path=str(rollout))
        return {"tool_name": tool, "tool_input": {"file_path": "a.py"}, "cwd": str(proj), **extra}

    FIRST = "task: implement\ninput: log/task/T1/spec.md"

    # PreToolUse를 통과한 하위 실행은 구현 승인 기록과 무관하게 allow이다.
    def test_permission_request_subagent_after_pretool(self):
        for runner in ("claude", "codex"):
            with self.subTest(runner=runner):
                proj = self.project("승인 대기")
                with patch.object(check_tool_use, "check", return_value=None):
                    self.assertEqual(verdict(self.child(runner, self.FIRST, proj), runner), "allow")
                proj = self.project("승인")
                with patch.object(check_tool_use, "check", return_value=None):
                    self.assertEqual(verdict(self.child(runner, self.FIRST, proj), runner), "allow")

    # 메인 세션의 Edit은 승인 턴에 PreToolUse를 통과하면 allow이다.
    def test_permission_request_main_edit_requires_runner_approval(self):
        self.assertEqual(self.claude("승인", "print(1)", tool="Edit"), "allow")

    # PreToolUse 통과 여부만 하위 실행의 자동 허용을 결정한다.
    def test_permission_request_any_role(self):
        prompts = {"worker": self.FIRST, "documenter": "task: implement\ninput: log/task/T1/spec.md",
                   "reviewer": "task: refute\ninput: log/task/T1/refute.md",
                   "researcher": "task: research\ninput: log/task/T1/research.md", None: self.FIRST}
        for runner in ("claude", "codex"):
            for role, first in prompts.items():
                with self.subTest(runner=runner, role=role):
                    proj = self.project("승인")
                    with patch.object(check_tool_use, "check", return_value=None):
                        self.assertEqual(verdict(self.child(runner, first, proj, role), runner), "allow")
                    missing = self.child(runner, first.replace("T1", "T9"), proj, role)
                    with patch.object(check_tool_use, "check", return_value="차단"):
                        self.assertEqual(verdict(missing, runner), "deny")
                    proj = self.project("승인 대기")
                    with patch.object(check_tool_use, "check", return_value=None):
                        self.assertEqual(verdict(self.child(runner, first, proj, role), runner), "allow")

    # Codex 첫 메시지와 task_name 형식은 하위 실행 자동 허용에 영향을 주지 않는다.
    def test_permission_request_codex_child_task_name(self):
        proj = self.project("승인")
        data = self.child("codex", "", proj)
        spawn = {"subagent": {"thread_spawn": {"parent_thread_id": "P"}}}
        rollout = Path(data["transcript_path"])
        rollout.write_text(codex_meta(agent_path="/root/implement__T1", source=spawn) + "\n", encoding="utf-8")
        with patch.object(check_tool_use, "check", return_value=None):
            self.assertEqual(verdict(data, "codex"), "allow")
        self.project("승인", task="20261103-063721-x")
        rollout.write_text(codex_meta(agent_path="/root/implement__20261103_063721_x", source=spawn) + "\n", encoding="utf-8")
        with patch.object(check_tool_use, "check", return_value=None):
            self.assertEqual(verdict(data, "codex"), "allow")

    # 서브에이전트의 질문 도구는 출력이 없고, 판정 중 예외는 main()이 검사 오류 사유의 deny로 끝낸다.
    def test_permission_request_subagent_question_and_error(self):
        proj = self.project("승인")
        for runner in ("claude", "codex"):
            self.assertIsNone(verdict(self.child(runner, self.FIRST, proj, tool="AskUserQuestion"), runner))
        data = self.child("claude", self.FIRST, proj)
        out = io.StringIO()
        with patch.object(sys, "argv", ["allow_approved_request.py", "--runner", "claude"]), \
             patch.object(sys, "stdin", io.StringIO(json.dumps(data))), patch.object(sys, "stdout", out), \
             patch.object(check_tool_use, "check", side_effect=RuntimeError("x")):
            self.assertEqual(hook.main(), 0)
        decision = json.loads(out.getvalue())["hookSpecificOutput"]["decision"]
        self.assertEqual((decision["behavior"], decision["message"]), ("deny", hook.REASON_CHECK_ERROR))

    # director의 작업 문서 apply_patch는 allow이며 코드 파일이 섞이면 runner 권한 판정을 따른다.
    def test_permission_request_task_doc_before_approval(self):
        proj = self.project("승인 대기")
        rollout = self.root / "rollout-1-M.jsonl"
        rollout.write_text(codex_meta() + "\n" + codex_msg("user", "안 B로 해") + "\n", encoding="utf-8")
        (proj / "hooks").mkdir(exist_ok=True)
        (proj / "hooks" / "a.py").write_text("old\n", encoding="utf-8")

        def patch_of(*files):
            text = "*** Begin Patch\n" + "".join(f"*** Update File: {f}\n@@\n-old\n+new\n" for f in files) + "*** End Patch"
            return verdict({"tool_name": "apply_patch", "tool_input": {"command": text}, "cwd": str(proj),
                                "transcript_path": str(rollout)}, "codex")
        import sub_scope
        with patch.object(sub_scope, "skill_called", return_value=True):
            self.assertEqual(patch_of("log/task/T1/spec.md"), "allow")
            self.assertEqual(patch_of("hooks/a.py"), "allow")
            self.assertEqual(patch_of("log/task/T1/spec.md", "hooks/a.py"), "allow")

    # PreToolUse 검사를 통과한 호출은 모든 역할과 세션에서 allow이고 git 비가역 작업과 질문 도구는 출력 없이 둔다.
    def test_permission_request_session_matrix(self):
        proj = self.project()
        for runner in ("claude", "codex"):
            for role in ("director", "assistant", "researcher", "reviewer", "worker", "documenter"):
                for child in (False, True):
                    for user in ("승인", "다시 검토"):
                        transcript = self.root / "matrix.jsonl"
                        if runner == "codex":
                            source = {"subagent": {"thread_spawn": {"parent_thread_id": "P"}}} if child else "cli"
                            lines = [codex_meta(agent_role=role, source=source), codex_msg("user", user)]
                        else:
                            lines = [claude_line("user", user)]
                        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
                        event = {"agent_type": role, "cwd": str(proj), "transcript_path": str(transcript)}
                        if runner == "claude" and child:
                            event["agent_id"] = "child"
                        calls = [("Read", {"file_path": "a.py"}, "allow"),
                                 ("Bash", {"command": "cat a.py"}, "allow"),
                                 ("Bash", {"command": "rm a.py"}, "allow"),
                                 ("Bash", {"command": "git push"}, None),
                                 ("Write", {"file_path": "a.py"}, "allow"),
                                 ("Edit", {"file_path": "hooks/a.py"}, "allow"),
                                 ("Edit", {"file_path": "log/task/T1/spec.md"}, "allow"),
                                 ("Write", {"file_path": "log/incident/i.md"}, "allow"),
                                 ("AskUserQuestion", {}, None)]
                        for tool, inputs, expected in calls:
                            data = {**event, "tool_name": tool, "tool_input": inputs}
                            with self.subTest(runner=runner, role=role, child=child, user=user, tool=tool, inputs=inputs), \
                                    patch.object(check_tool_use, "check", return_value=None):
                                self.assertEqual(verdict(data, runner), expected)

    # PreToolUse가 차단한 하위 실행의 읽기와 쓰기는 자동 허용하지 않는다.
    def test_permission_request_pretool_denial(self):
        proj = self.project()
        for runner in ("claude", "codex"):
            for tool in ("Read", "Write", "Bash"):
                with patch.object(check_tool_use, "check", return_value="보안 경로 차단"):
                    self.assertEqual(verdict(self.child(runner, self.FIRST, proj, tool=tool), runner), "deny")

    # 하위 실행 자동 허용은 두 runner에서 PermissionRequest allow JSON으로 출력한다.
    def test_permission_request_subagent_allow_output(self):
        proj = self.project()
        for runner in ("claude", "codex"):
            data = self.child(runner, self.FIRST, proj)
            out = io.StringIO()
            with patch.object(sys, "argv", ["allow_approved_request.py", "--runner", runner]), \
                    patch.object(sys, "stdin", io.StringIO(json.dumps(data))), patch.object(sys, "stdout", out), \
                    patch.object(check_tool_use, "check", return_value=None):
                self.assertEqual(hook.main(), 0)
            self.assertEqual(json.loads(out.getvalue()), {"hookSpecificOutput": {
                "hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}})

    # Claude git deny와 Codex git forbidden은 같은 명령 목록을 가진다.
    def test_git_forbidden_matches_claude_deny(self):
        settings = HOOKS.parent / "shared" / "settings"
        denied = json.loads((settings / "claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]
        patterns = {rule[5:-1].rstrip().removesuffix("*").rstrip() for rule in denied if rule.startswith("Bash(git ")}
        forbidden = set()
        for path in (settings / "codex" / "rules").glob("*.rules"):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                    continue
                values = {kw.arg: ast.literal_eval(kw.value) for kw in node.value.keywords}
                if values.get("decision") == "forbidden" and values.get("pattern", [])[:1] == ["git"]:
                    forbidden.add(" ".join(values["pattern"]))
        self.assertTrue(patterns)
        self.assertTrue(forbidden)
        for pattern in patterns:
            self.assertIn(pattern, forbidden)
        raw_patterns = [rule[5:-1] for rule in denied if rule.startswith("Bash(git ")]
        for command in forbidden:
            self.assertTrue(any(fnmatch.fnmatchcase(command + " ", pattern) or fnmatch.fnmatchcase(command, pattern)
                                for pattern in raw_patterns), command)


if __name__ == "__main__":
    unittest.main()
