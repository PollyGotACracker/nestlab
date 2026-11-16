"""단계별 문서 생성과 경로 및 기존 파일 보존을 검증한다."""
import sys
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from create_task_docs import create
import sub_docs
import sub_spec

TASK = "20261106-130000-example"


class CreateTaskDocs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    # 문서 종류와 파일 이름은 스킬 설치 없이 같은 독립 양식 원본을 읽는다.
    def test_document_templates_match_independent_sources(self):
        templates = Path(__file__).resolve().parents[2] / "shared" / "templates" / "workflow"
        with patch.object(sub_docs, "SKILLS", self.root / "missing-skills"):
            for kind, name in {"state": "status", "input": "spec", "research": "research", "refute": "refute"}.items():
                with self.subTest(kind=kind, name=name):
                    expected = (templates / f"log-{name}.md").read_text(encoding="utf-8").rstrip("\n")
                    self.assertEqual(sub_docs.template_text(kind), expected)
                    self.assertEqual(sub_docs.template_text(name), expected)

    def test_stages_and_existing_bytes_without_skills(self):
        with patch.object(sub_docs, "SKILLS", self.root / "missing-skills"):
            # 계획 단계는 경과, 조사, 반증, 명세 문서를 한 번에 생성하고 색인의 현재 진행 작업으로 선택한다.
            paths = create(self.root, TASK, "한글 제목", "plan")
            self.assertEqual([p.name for p in paths], ["status.md", "research.md", "refute.md", "spec.md"])
            status, research, refute, spec = paths
            text = status.read_text(encoding="utf-8")
            self.assertIn("# 경과 문서: 한글 제목\n", text)
            self.assertRegex(text, rf"- 작업 ID: {TASK}\n- 생성 시각: \d{{4}}\.\d\d\.\d\d \d\d:\d\d:\d\d\n- 완료 시각: 없음\n- 작업 단계: 계획")
            self.assertNotIn("- 작업 상태:", text)
            self.assertIn("# 조사 문서\n", research.read_text(encoding="utf-8"))
            self.assertIn("# 반증 문서", refute.read_text(encoding="utf-8"))
            self.assertIn("# 명세 문서", spec.read_text(encoding="utf-8"))
            self.assertEqual(sub_docs.missing_titles(spec.read_text(encoding="utf-8"), "spec"), [])
            body = sub_spec.apply(sub_docs.section(spec.read_text(encoding="utf-8"), "요구사항 목록"),
                                  [("problem", 1, "- 문제 1. 확정 문제", "2026-11-16 10:00:00"), ("req", 1, "확정 결과")], {1: "요구"})
            self.assertEqual(sub_spec.cells(body)[1], "2026-11-16 10:00:00\n--\n확정 문제\n--\n- 확정 결과\n--\n")
            self.assertIn("### 1. 요구\n", body)
            self.assertNotIn("## 문제 정의", text)
            index = (self.root / ".agent-logs/task/index.md").read_text(encoding="utf-8")
            self.assertIn(f"## 현재 진행 작업\n\n<!-- <작업 링크>, <작업 단계> -->\n\n- [경과 문서: 한글 제목]({TASK}/status.md), 계획", index)

            # 다시 생성하면 기존 문서의 바이트를 보존하고, plan 밖 단계 이름은 거부한다.
            status.write_bytes(b"existing\r\nbytes")
            self.assertEqual(create(self.root, TASK, "변경 제목", "plan"), [])
            self.assertEqual(status.read_bytes(), b"existing\r\nbytes")
            for stage in ("research", "refute", "review", "implement"):
                with self.subTest(stage=stage), self.assertRaises(ValueError):
                    create(self.root, TASK, "제목", stage)

    def test_invalid_id_and_title(self):
        for task in ("", "../escape", TASK + "/../escape", "20261106-130000-../escape"):
            with self.subTest(task=task), self.assertRaises(ValueError):
                create(self.root, task, "제목", "plan")
        for title in ("", "제목\n## 절"):
            with self.assertRaises(ValueError):
                create(self.root, TASK, title, "review")
        self.assertFalse((self.root / ".agent-logs").exists())

    def test_external_project_cli_and_readonly_reports(self):
        script = Path(__file__).resolve().parents[1] / "create_task_docs.py"
        command = [sys.executable, "-B", "-X", "utf8", str(script), "--task", TASK,
                   "--title", "외부 프로젝트", "--stage", "plan", "--root", str(self.root)]
        subprocess.run(command, cwd=self.root, check=True, capture_output=True, text=True, encoding="utf-8")
        status = self.root / ".agent-logs" / "task" / TASK / "status.md"
        self.assertIn("# 경과 문서: 외부 프로젝트", status.read_text(encoding="utf-8"))
        status.write_bytes(b"existing bytes\r\n")
        subprocess.run(command, cwd=self.root, check=True, capture_output=True)
        self.assertEqual(status.read_bytes(), b"existing bytes\r\n")
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        for name in sub_docs.RESPONSE_TEMPLATES:
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(script), "--print-template", name],
                                    cwd=self.root, check=True, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.stdout, sub_docs.template_text(name) + "\n")
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")), before)
        self.assertFalse((self.root / "shared").exists())

    # 작업 ID를 주면 통합 구조 명세의 폐기되지 않은 항목마다 제목, 목표, 기대 결과, 한계, 잠재 위험 원문만 출력하고, 다른 양식은 거부한다.
    def test_render_approval_from_task_docs(self):
        script = Path(__file__).resolve().parents[1] / "create_task_docs.py"
        folder = self.root / ".agent-logs" / "task" / TASK
        folder.mkdir(parents=True)
        (folder / "status.md").write_text("# 작업\n\n## 원인 분석\n", encoding="utf-8")
        item = ("### {n}. {name}\n\n#### 사용자 확인 시각\n\n2026-11-16 10:00:00\n\n#### 문제\n\n문제 원문이다.\n\n"
                "#### 목표\n\n- 목표 원문이다.\n\n#### 기대 결과\n\n- 결과 원문이다.\n  - 하위 결과이다.\n\n"
                "#### 한계\n\n- 없음\n\n#### 잠재 위험\n\n- 위험 원문이다. (출처: https://example.com/a)\n\n"
                "#### 검증\n\n- 검증 원문이다. (정적)\n\n#### Test Case\n\n- 차단 동작: 없음\n\n")
        (folder / "spec.md").write_text("## 요구사항 목록\n\n<!-- 주석 -->\n\n" + item.format(n=1, name="첫 요구")
                                        + item.format(n=2, name="폐기") + "## 작업 계획\n", encoding="utf-8")
        run = lambda form: subprocess.run([sys.executable, "-B", "-X", "utf8", str(script), "--print-template", form,
                                           "--task", TASK], cwd=self.root, capture_output=True, text=True, encoding="utf-8")
        out = run("approval-implement").stdout
        self.assertEqual(out.strip(), "\n".join([
            "# 구현 승인 요청", "", "## 작업 문서", "", f"- [작업 경과 문서](.agent-logs/task/{TASK}/status.md)",
            f"- [작업 명세 문서](.agent-logs/task/{TASK}/spec.md)", "", "## 요구사항", "", "### 1. 첫 요구", "",
            "#### 목표", "", "- 목표 원문이다.", "", "#### 기대 결과", "", "- 결과 원문이다.", "  - 하위 결과이다.", "",
            "#### 한계", "", "- 없음", "", "#### 잠재 위험", "", "- 위험 원문이다. (출처: https://example.com/a)"]))
        self.assertNotEqual(run("question-choice").returncode, 0)

    # 기존 구조(`- 칸:` 줄) 명세도 같은 표시 칸을 내어 쓰고, 없는 칸은 기록 없음으로 출력한다.
    def test_render_approval_from_legacy_spec(self):
        folder = self.root / ".agent-logs" / "task" / TASK
        folder.mkdir(parents=True)
        (folder / "status.md").write_text("# 작업\n\n## 문제 정의\n\n- 문제 1. 첫 문제이다.\n", encoding="utf-8")
        (folder / "spec.md").write_text("## 요구사항 목록\n\n### 1. 첫 요구\n\n- 문제: 문제 1\n- 목표:\n  - 목표이다.\n"
                                        "- 결과:\n  - 결과이다.\n- 기대 결과:\n  - 기준이다.\n\n## 작업 계획\n", encoding="utf-8")
        from create_task_docs import render
        out = render("approval-implement", self.root, TASK)
        self.assertIn("### 1. 첫 요구\n\n#### 목표\n\n- 목표이다.\n\n#### 결과\n\n- 결과이다.\n\n#### 한계\n\n- 기록 없음", out)
        self.assertNotIn("기준이다.", out)

    def test_external_directory_link(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside)
            preserved = external / "preserved.txt"
            preserved.write_bytes(b"external bytes\r\n")
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "mklink", "/J", str(self.root / ".agent-logs"), str(external)],
                               check=True, capture_output=True)
            else:
                (self.root / ".agent-logs").symlink_to(external, target_is_directory=True)
            try:
                with self.assertRaises(ValueError):
                    create(self.root, TASK, "제목", "plan")
                self.assertEqual(list(external.iterdir()), [preserved])
                self.assertEqual(preserved.read_bytes(), b"external bytes\r\n")
            finally:
                if os.name == "nt":
                    os.rmdir(self.root / ".agent-logs")
                else:
                    (self.root / ".agent-logs").unlink()

    def test_external_file_symlink(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside)
            preserved = external / "status.md"
            preserved.write_bytes(b"external file\r\n")
            folder = self.root / ".agent-logs" / "task" / TASK
            folder.mkdir(parents=True)
            try:
                (folder / "status.md").symlink_to(preserved)
            except OSError as error:
                self.skipTest(f"파일 symlink 생성 권한 없음: {error}")
            with self.assertRaises(ValueError):
                create(self.root, TASK, "제목", "plan")
            self.assertEqual(preserved.read_bytes(), b"external file\r\n")


if __name__ == "__main__":
    unittest.main()
