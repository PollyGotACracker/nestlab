"""지식 절차 원본 로딩과 고정 commit의 조회 경계를 검증한다."""

from __future__ import annotations

import base64
import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


DISCORD_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_ROOT / "bot"))
reader_module = importlib.import_module("archive_reader")
bot = importlib.import_module("bot")
config_module = importlib.import_module("config")
archive_tools_module = importlib.import_module("archive_tools")
COMMIT = "a" * 40
TREE = "b" * 40


class ArchiveReaderTests(unittest.IsolatedAsyncioTestCase):
    # 가짜 저장소 항목과 읽기 객체를 준비한다.
    def setUp(self):
        self.entries = []
        self.blob_counter = 0
        self.blobs = {}
        self.truncated = False
        self.commit_exists = True
        self.current_commit = COMMIT
        self.current_tree = TREE
        self.commits = []
        self.branch_sha = AsyncMock(return_value=COMMIT)
        self.get_json = AsyncMock(side_effect=self.respond)
        self.reader = reader_module.ArchiveReader(self.get_json, self.branch_sha)

    # GitHub API 경로별 가짜 응답을 반환한다.
    async def respond(self, path, params=None):
        if path == f"/git/commits/{self.current_commit}":
            return {"sha": self.current_commit, "tree": {"sha": self.current_tree}} if self.commit_exists else None
        if path == f"/git/trees/{self.current_tree}":
            return {"tree": self.entries, "truncated": self.truncated}
        if path.startswith("/git/blobs/"):
            return self.blobs.get(path.rsplit("/", 1)[-1])
        if path == "/commits":
            return self.commits
        raise AssertionError(f"unexpected request: {path}")

    # 가짜 저장소에 파일 항목을 추가하고 항목을 반환한다.
    def add_file(self, path, text, mode="100644"):
        self.blob_counter += 1
        sha = f"{self.blob_counter:040x}"
        raw = text.encode("utf-8") if isinstance(text, str) else text
        self.entries.append({"path": path, "sha": sha, "type": "blob", "mode": mode, "size": len(raw)})
        self.blobs[sha] = {"sha": sha, "encoding": "base64", "content": base64.b64encode(raw).decode("ascii")}
        return self.entries[-1]

    # 같은 커밋의 지침과 작업 스키마 두 원본을 함께 읽는지 확인한다.
    async def test_workflow_loads_both_complete_sources_at_same_commit(self):
        self.add_file("AGENTS.md", "원칙\n전체 내용")
        self.add_file("schema/query.md", "조회 원본")
        result = await self.reader.workflow_open("query")
        self.branch_sha.assert_awaited_once_with("master")
        self.assertEqual(result["commit_sha"], COMMIT)
        self.assertEqual(result["base_directory"], ".")
        self.assertEqual([x["content"] for x in result["sources"]], ["원칙\n전체 내용", "조회 원본"])
        self.assertEqual(result["sources"][1]["blob_sha"], self.entries[1]["sha"])
        self.get_json.assert_any_await(f"/git/commits/{COMMIT}")
        self.get_json.assert_any_await(f"/git/trees/{TREE}", params={"recursive": "1"})

    # 알 수 없는 작업과 없는 원본이 명시적 오류가 되는지 확인한다.
    async def test_operation_and_missing_sources_fail_explicitly(self):
        for operation in ("other", "../ingest", "", None):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                await self.reader.workflow_open(operation)
        self.branch_sha.assert_not_awaited()
        with self.assertRaisesRegex(ValueError, "AGENTS.md, schema/lint.md"):
            await self.reader.workflow_open("lint")
        self.truncated = True
        with self.assertRaisesRegex(ValueError, "tree가 잘려"):
            await self.reader.workflow_open("lint")

    # 작업마다 최신 원본을 다시 읽는지 확인한다.
    async def test_each_operation_and_reload_use_live_source(self):
        self.add_file("AGENTS.md", "원칙")
        for operation in ("ingest", "query", "lint"):
            entry = self.add_file(f"schema/{operation}.md", operation)
            result = await self.reader.workflow_open(operation, "docs/7")
            self.assertEqual(result["sources"][1]["content"], operation)
            self.current_commit = f"{100 + len(self.entries):040x}"
            self.current_tree = f"{200 + len(self.entries):040x}"
            self.branch_sha.return_value = self.current_commit
            updated = self.add_file(entry["path"], "updated")
            self.entries.remove(entry)
            again = await self.reader.workflow_open(operation, "docs/7")
            self.assertEqual(again["sources"][1]["content"], "updated")
            self.assertEqual(again["commit_sha"], self.current_commit)
            self.assertEqual(again["sources"][1]["blob_sha"], updated["sha"])
        self.assertEqual(self.branch_sha.await_count, 6)

    # 잘못된 브랜치 이름과 커밋이 아닌 ref가 실패하는지 확인한다.
    async def test_unknown_branch_and_noncommit_ref_fail(self):
        for branch in ("../master", "/master", "docs//7", "docs/%2e", "master?ref=x", "", None):
            with self.subTest(branch=branch), self.assertRaises(ValueError):
                await self.reader.workflow_open("query", branch)
        self.branch_sha.assert_not_awaited()
        self.branch_sha.return_value = None
        with self.assertRaisesRegex(ValueError, "branch"):
            await self.reader.workflow_open("query", "missing")
        for ref in ("master", "a" * 39, "../master", COMMIT + "?x", None):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                await self.reader.list_files("wiki", ref)
        self.commit_exists = False
        with self.assertRaisesRegex(ValueError, "commit"):
            await self.reader.list_files("wiki", COMMIT)

    # 아카이브 경로 경계를 벗어난 경로가 거부되는지 확인한다.
    async def test_archive_path_boundary(self):
        for path in ("/wiki", "wiki/../other", "wiki/./a", "wiki\\a", "wiki/%2e%2e",
                     "wiki/%252e", "wiki//a", "wiki/", "wiki2", "C:/wiki", "wiki/\x00",
                     "archive/wiki/a.md", "AGENTS.md/a", ".github/a", "", None):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.reader.list_files(path, COMMIT)
        self.get_json.assert_not_awaited()

    # 루트 원본과 지식 폴더별 경로가 허용되는지 확인한다.
    async def test_root_sources_and_each_knowledge_directory_are_allowed(self):
        for path in ("AGENTS.md", "schema/query.md", "raw/source.md", "wiki/page.md"):
            entry = self.add_file(path, "source")
            self.assertEqual((await self.reader.read_entry(entry))["path"], path)
        for directory in ("schema", "raw", "wiki"):
            self.assertEqual((await self.reader.list_files(directory, COMMIT))["known_files"], 1)

    # 파일 목록 쪽이 커밋을 유지하고 범위 밖 경로를 제외하는지 확인한다.
    async def test_file_pages_preserve_commit_and_exclude_other_paths(self):
        self.add_file("outside.md", "outside")
        self.add_file("archive-other/a.md", "outside")
        self.add_file("wiki/a.md", "a")
        self.add_file("wiki/b.md", "b")
        result = await self.reader.list_files("wiki", COMMIT, per_page=1)
        self.assertEqual(result["known_files"], 2)
        self.assertEqual(result["next_page"], 2)
        self.assertTrue(result["truncated"])
        second = await self.reader.list_files("wiki", COMMIT, page=2, per_page=1)
        self.assertEqual(second["entries"][0]["path"], "wiki/b.md")
        self.assertEqual(second["commit_sha"], result["commit_sha"])
        self.truncated = True
        self.assertTrue((await self.reader.list_files("wiki", COMMIT))["tree_truncated"])

    # 검색 결과가 행 번호와 한도, 파일 범위를 보고하는지 확인한다.
    async def test_search_reports_lines_limits_and_file_scope(self):
        self.add_file("raw/source.md", "needle")
        self.add_file("wiki/a.md", "first\nNeedle one\nneedle two")
        self.add_file("wiki/b.md", "needle three")
        result = await self.reader.search("needle", COMMIT, per_page=1, limit=1)
        self.assertEqual(result["matches"], [{"path": "wiki/a.md", "line": 2,
                                            "text": "Needle one", "text_truncated": False}])
        self.assertEqual(result["next_page"], 2)
        self.assertTrue(result["matches_truncated"])
        self.assertFalse(result["complete"])
        second = await self.reader.search("needle", COMMIT, page=2, per_page=1)
        self.assertEqual(second["matches"][0]["text"], "needle three")
        self.assertFalse(second["complete"])
        self.assertTrue((await self.reader.search("needle", COMMIT))["complete"])

    # 검색이 이진 파일, 심볼릭 링크, 큰 파일, 없는 blob을 표시하는지 확인한다.
    async def test_search_marks_binary_symlink_oversized_and_missing_blobs(self):
        self.add_file("wiki/binary", b"\xff\x00")
        self.add_file("wiki/link", "../../secret", mode="120000")
        oversized = self.add_file("wiki/huge", "small")
        oversized["size"] = reader_module.MAX_FILE_BYTES + 1
        missing = self.add_file("wiki/missing", "missing")
        del self.blobs[missing["sha"]]
        result = await self.reader.search("secret", COMMIT)
        self.assertEqual(len(result["skipped_files"]), 4)
        self.assertTrue(result["truncated"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["matches"], [])

    # 긴 일치 행과 없는 범위가 결과에 명시되는지 확인한다.
    async def test_long_matching_line_and_missing_scope_are_explicit(self):
        result = await self.reader.search("needle", COMMIT)
        self.assertFalse(result["scope_found"])
        self.assertFalse(result["complete"])
        self.add_file("wiki/long", "a" * 3000 + "needle" + "b" * 3000)
        result = await self.reader.search("needle", COMMIT)
        self.assertIn("needle", result["matches"][0]["text"])
        self.assertTrue(result["matches"][0]["text_truncated"])
        self.assertLessEqual(len(result["matches"][0]["text"]), 2000)

    # 잘못된 쪽 번호와 검색 한도가 실패하는지 확인한다.
    async def test_invalid_page_and_search_limits(self):
        for kwargs in ({"page": 0}, {"per_page": 101}, {"page": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await self.reader.list_files("wiki", COMMIT, **kwargs)
        for kwargs in ({"per_page": 21}, {"limit": 101}, {"limit": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await self.reader.search("x", COMMIT, **kwargs)
        with self.assertRaises(ValueError):
            await self.reader.search("", COMMIT)

    # 이력 조회가 질의를 전달하고 커밋 메시지 첫 줄을 반환하는지 확인한다.
    async def test_history_passes_query_and_returns_first_message_lines(self):
        self.commits = [
            {"sha": "c" * 40, "commit": {"author": {"date": "2026-09-02T00:00:00Z"},
                                         "committer": {"date": "2026-09-03T00:00:00Z"},
                                         "message": "ingest: 새 자료\r\n\n본문"}},
            {"sha": "d" * 40, "commit": {"committer": {"date": "2026-09-01T00:00:00Z"}, "message": "lint"}},
        ]
        result = await self.reader.history("wiki", COMMIT.upper(), per_page=2)
        self.get_json.assert_awaited_once_with(
            "/commits", params={"sha": COMMIT, "path": "wiki", "page": 1, "per_page": 2})
        self.assertEqual(result["commits"], [
            {"sha": "c" * 40, "date": "2026-09-02T00:00:00Z", "message": "ingest: 새 자료"},
            {"sha": "d" * 40, "date": "2026-09-01T00:00:00Z", "message": "lint"},
        ])
        self.assertEqual(result["next_page"], 2)
        self.assertFalse(result["complete"])
        result = await self.reader.history("wiki", "docs/7")
        self.assertEqual(self.get_json.call_args.kwargs["params"]["sha"], "docs/7")
        self.assertIsNone(result["next_page"])
        self.assertTrue(result["complete"])

    # 이력 조회가 잘못된 입력과 없는 ref를 거부하는지 확인한다.
    async def test_history_rejects_invalid_input_and_missing_ref(self):
        for path in ("/archive", "../other", "archive\\wiki", "%2e%2e", "archive2", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.reader.history(path, COMMIT)
        for ref in ("../master", "master?ref=x", "", None, "a b"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                await self.reader.history("wiki", ref)
        for kwargs in ({"page": 0}, {"per_page": 101}, {"per_page": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await self.reader.history("wiki", COMMIT, **kwargs)
        self.get_json.assert_not_awaited()
        self.commits = None
        with self.assertRaisesRegex(ValueError, "ref"):
            await self.reader.history("wiki", "missing")


class ArchiveToolTests(unittest.IsolatedAsyncioTestCase):
    # 아카이브 도구가 아카이브 스레드의 documenter에게만 붙는지 확인한다.
    async def test_tools_only_attach_to_documenter_in_archive_thread(self):
        # 역할과 스레드, 설정 조합마다 턴을 실행한다.
        for role, in_thread, configured in (("documenter", True, True), ("documenter", False, True),
                                            ("worker", True, True), ("documenter", True, False)):
            with self.subTest(role=role, in_thread=in_thread, configured=configured):
                channel = SimpleNamespace(id=123, name="test", guild=SimpleNamespace(name="test"),
                                          typing=Mock(return_value=AsyncMock()))
                runtime = SimpleNamespace(
                    ready=SimpleNamespace(wait=AsyncMock()), render_chat_history=Mock(return_value=""),
                    chat_last_message={}, chat_last_user_id={}, chat_pending={}, archive_workflow=Mock(),
                    config=SimpleNamespace(archive_repository=Mock() if configured else None,
                                           chat_model_for=Mock(return_value=("test", "low")), chat_channels=set(),
                                           chat=SimpleNamespace(sdk_max_turns=1)),
                )
                client = SimpleNamespace(
                    runtime=runtime, _refresh_request_prompt=Mock(), get_channel=Mock(return_value=channel),
                    _react_target_label=Mock(return_value="없음"), _request_prompt="test",
                    agent=SimpleNamespace(name="test", role=role, tools=[]),
                )

                # 정지 응답 하나를 내는 가짜 질의이다.
                async def fake_query(**kwargs):
                    yield SimpleNamespace(result="[[next:stop]]", subtype="success", num_turns=1)

                # 봇 의존성을 대체하고 턴을 실행한다.
                with (
                    patch.object(bot, "query", fake_query),
                    patch.object(bot, "ResultMessage", SimpleNamespace),
                    patch.object(bot, "ClaudeAgentOptions", Mock()) as options,
                    patch.object(bot, "is_archive_thread", return_value=in_thread),
                    patch.object(bot, "build_archive_server", return_value="server") as server,
                    patch.object(bot, "read_hooks", return_value={}),
                    patch.object(bot, "build_server_channels_server", return_value=None),
                    patch.object(bot, "ROLE_TOOL_BUILDERS", {}),
                ):
                    await bot.AgentBot._handle_chat_turn(
                        client, 123, turn_index=1, turn_limit=1, autonomous=False,
                    )
                exposed = role == "documenter" and in_thread and configured
                self.assertEqual(server.called, exposed)
                for name in bot.ARCHIVE_TOOL_NAMES:
                    self.assertEqual(name in options.call_args.kwargs["allowed_tools"], exposed)

    # 등록된 도구 목록과 오류 표시를 확인한다.
    async def test_registered_tools_and_error_flag(self):
        # 도구 등록 데코레이터를 대신하는 가짜 함수이다.
        def decorator(name, description, schema):
            return lambda handler: handler

        # 등록된 도구 이름과 오류 표시를 확인한다.
        cfg = config_module.ArchiveRepositoryConfig(owner="example", repo="nodebase", token="")
        with patch.object(archive_tools_module, "tool", decorator), patch.object(archive_tools_module, "create_sdk_mcp_server", side_effect=lambda **kw: kw):
            server = bot.build_archive_server(cfg, Mock(), 1, 2)
        tools = {handler.__name__: handler for handler in server["tools"]}
        for name in ("archive_workflow_open", "archive_list", "archive_search", "archive_history"):
            self.assertIn(name, tools)
            self.assertIn("mcp__archive__" + name, bot.ARCHIVE_TOOL_NAMES)
        result = await tools["archive_workflow_open"]({"operation": "invalid"})
        self.assertTrue(result["isError"])
        self.assertIn("operation", result["content"][0]["text"])
        result = await tools["archive_history"]({"path": "outside.md", "ref": COMMIT})
        self.assertTrue(result["isError"])
        self.assertIn("path", result["content"][0]["text"])

    # 기존 파일 읽기가 경로를 인코딩하고 ref 인자를 유지하는지 확인한다.
    async def test_existing_file_read_encodes_path_and_keeps_ref_parameter(self):
        response = AsyncMock()
        response.status = 200
        response.raise_for_status = Mock()
        response.json.return_value = {"content": base64.b64encode(b"body").decode(), "sha": "blob"}
        context = AsyncMock()
        context.__aenter__.return_value = response
        session = Mock()
        session.get.return_value = context
        cfg = config_module.ArchiveRepositoryConfig(owner="example", repo="nodebase", token="")
        result = await bot.github_get_file(session, cfg, "wiki/a?# 한글.md", COMMIT)
        self.assertEqual(result, ("body", "blob"))
        self.assertTrue(session.get.call_args.args[0].endswith("/wiki/a%3F%23%20%ED%95%9C%EA%B8%80.md"))
        self.assertEqual(session.get.call_args.kwargs["params"], {"ref": COMMIT})


if __name__ == "__main__":
    unittest.main()
