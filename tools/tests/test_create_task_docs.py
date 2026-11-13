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
            # 착수는 경과 문서만 생성하고, 나머지 문서는 해당 단계가 기록된 뒤에만 생성한다.
            paths = create(self.root, TASK, "한글 제목", "kickoff")
            self.assertEqual([p.name for p in paths], ["status.md"])
            status = paths[0]
            self.assertIn("한글 제목", status.read_text(encoding="utf-8").split("\n# ", 1)[-1].splitlines()[0])
            self.assertIn(TASK, status.read_text(encoding="utf-8"))
            self.assertIn("- 작업 단계: 착수", status.read_text(encoding="utf-8"))
            self.assertNotIn("- 작업 상태:", status.read_text(encoding="utf-8"))
            status.write_bytes(b"existing\r\nbytes")
            self.assertEqual(create(self.root, TASK, "변경 제목", "kickoff"), [])
            self.assertEqual(status.read_bytes(), b"existing\r\nbytes")
            for stage in ("research", "refute", "review"):
                with self.assertRaises(ValueError):
                    create(self.root, TASK, "제목", stage)
            status.write_bytes("- 작업 단계: 조사\n".encode("utf-8"))
            research = create(self.root, TASK, "조사 제목", "research")[0]
            self.assertIn("# 조사 문서: 조사 제목\n", research.read_text(encoding="utf-8"))
            self.assertEqual(create(self.root, TASK, "조사 제목", "research"), [])
            status.write_bytes("- 작업 단계: 착수\n".encode("utf-8"))
            with self.assertRaises(ValueError):
                create(self.root, TASK, "명세", "review")
            status.write_bytes("- 작업 단계: 조사\n".encode("utf-8"))
            spec = create(self.root, TASK, "명세", "review")[0]
            self.assertIn("# 명세 명세", spec.read_text(encoding="utf-8"))
            self.assertIn(TASK, spec.read_text(encoding="utf-8"))
            self.assertEqual(sub_docs.missing_titles(spec.read_text(encoding="utf-8"), "spec"), [])
            body = sub_spec.apply(sub_docs.section(spec.read_text(encoding="utf-8"), "요구사항 목록"),
                                  [("req", 1, "확정 결과")], {1: "요구"})
            self.assertEqual(sub_spec.cells(body)[1], "- 확정 결과\n--\n- 확정 결과")
            status.write_bytes("- 작업 단계: 조사\n".encode("utf-8"))
            refute = create(self.root, TASK, "반증 제목", "refute")[0]
            self.assertEqual(refute.name, "refute.md")
            self.assertIn("# 반증 문서: 반증 제목\n", refute.read_text(encoding="utf-8"))
            self.assertIn(TASK, refute.read_text(encoding="utf-8"))

    def test_invalid_id_and_title(self):
        for task in ("", "../escape", TASK + "/../escape", "20261106-130000-../escape"):
            with self.subTest(task=task), self.assertRaises(ValueError):
                create(self.root, task, "제목", "kickoff")
        for title in ("", "제목\n## 절"):
            with self.assertRaises(ValueError):
                create(self.root, TASK, title, "review")
        self.assertFalse((self.root / "log").exists())

    def test_external_project_cli_and_readonly_reports(self):
        script = Path(__file__).resolve().parents[1] / "create_task_docs.py"
        command = [sys.executable, "-B", "-X", "utf8", str(script), "--task", TASK,
                   "--title", "외부 프로젝트", "--stage", "kickoff", "--root", str(self.root)]
        subprocess.run(command, cwd=self.root, check=True, capture_output=True, text=True, encoding="utf-8")
        status = self.root / "log" / "task" / TASK / "status.md"
        self.assertIn("# 외부 프로젝트", status.read_text(encoding="utf-8"))
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

    def test_external_directory_link(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside)
            preserved = external / "preserved.txt"
            preserved.write_bytes(b"external bytes\r\n")
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "mklink", "/J", str(self.root / "log"), str(external)],
                               check=True, capture_output=True)
            else:
                (self.root / "log").symlink_to(external, target_is_directory=True)
            try:
                with self.assertRaises(ValueError):
                    create(self.root, TASK, "제목", "kickoff")
                self.assertEqual(list(external.iterdir()), [preserved])
                self.assertEqual(preserved.read_bytes(), b"external bytes\r\n")
            finally:
                if os.name == "nt":
                    os.rmdir(self.root / "log")
                else:
                    (self.root / "log").unlink()

    def test_external_file_symlink(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside)
            preserved = external / "status.md"
            preserved.write_bytes(b"external file\r\n")
            folder = self.root / "log" / "task" / TASK
            folder.mkdir(parents=True)
            try:
                (folder / "status.md").symlink_to(preserved)
            except OSError as error:
                self.skipTest(f"파일 symlink 생성 권한 없음: {error}")
            with self.assertRaises(ValueError):
                create(self.root, TASK, "제목", "kickoff")
            self.assertEqual(preserved.read_bytes(), b"external file\r\n")


if __name__ == "__main__":
    unittest.main()
