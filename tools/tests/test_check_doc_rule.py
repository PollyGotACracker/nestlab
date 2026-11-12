"""직접 원본 참조 허용과 누락 경로 및 placeholder 구분을 확인한다."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / "check_doc_rule"))
import sub_rules
import sub_paths

# 같은 이름의 다른 도구 모듈(sub_config)과 충돌하지 않도록 캐시에서 뺀다.
for _name in ("sub_config", "sub_paths", "sub_sections", "sub_rules"):
    sys.modules.pop(_name, None)
sys.path.remove(str(TOOLS / "check_doc_rule"))

class ReferencePathTest(unittest.TestCase):
    def test_existing_assets_missing_paths_and_placeholders(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            assets = ("shared/settings/local.json", "shared/templates/workflow/approval.md",
                      "shared/templates/workflow/approval-research.md")
            for asset in assets:
                path = root / asset
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# 원본\n", encoding="utf-8")
            source = root / "AGENTS.md"
            source.write_text("## 규칙\n" + "\n".join(f"- `{asset}`" for asset in assets)
                              + "\n- `shared/missing.md`\n- `<archive_root>/note.md`\n", encoding="utf-8")
            with mock.patch.object(sub_rules, "ROOT", root), mock.patch.object(sub_paths, "ROOT", root):
                found = sub_rules.check_file("AGENTS.md", {"shared"}, {})
            self.assertEqual([(kind, value) for _, _, kind, value in found],
                             [(sub_rules.KIND_PATH, "shared/missing.md")])


class PreToolUseHook(unittest.TestCase):
    """--hook 입력의 쓰기 뒤 파일 내용을 검사해 위반이면 deny하고 파일은 바꾸지 않는다. (요구사항 25)"""

    # --hook 모드를 실행해 (출력 JSON 또는 None)을 반환한다.
    def run_hook(self, data):
        import json
        import subprocess
        out = subprocess.run([sys.executable, "-B", str(TOOLS / "check_doc_rule.py"), "--hook"], input=json.dumps(data),
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout
        return json.loads(out) if out.strip() else None

    # Edit의 쓰기 뒤 내용에 부정형 위반이 생기면 deny하고, 위반 없는 Edit과 검사 대상이 아닌 파일은 출력이 없다.
    def test_edit_and_patch(self):
        target = TOOLS.parent / "AGENTS.md"
        before = target.read_bytes()
        title = "# 저장소 공통 지침"
        edit = lambda new: {"tool_name": "Edit", "cwd": str(TOOLS.parent),
                            "tool_input": {"file_path": str(target), "old_string": title, "new_string": new}}
        denied = self.run_hook(edit(title + "\n\n- 이렇게 하지 마십시오."))
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("검사4 부정형", denied["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIsNone(self.run_hook(edit(title + " 개정")))
        other = {"tool_name": "Write", "cwd": str(TOOLS.parent),
                 "tool_input": {"file_path": str(TOOLS.parent / "notes-unrelated.txt"), "content": "하지 마"}}
        self.assertIsNone(self.run_hook(other))
        patch = ("*** Begin Patch\n*** Update File: AGENTS.md\n@@\n-" + title + "\n+" + title + "\n+\n+- 이렇게 하지 마십시오.\n*** End Patch")
        denied = self.run_hook({"tool_name": "apply_patch", "cwd": str(TOOLS.parent), "tool_input": {"input": patch}})
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
