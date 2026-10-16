"""config.json의 chat_channels 형식과 최상위 키 검사를 확인한다."""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


DISCORD_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_ROOT / "bot"))
config_module = importlib.import_module("config")


# config.example.json을 바탕으로 채널 설정만 바꾼 원본 dict를 만든다.
def example_raw(**overrides) -> dict:
    raw = json.loads((DISCORD_ROOT / "config.example.json").read_text(encoding="utf-8"))
    raw["allowed_user_ids"] = ["7"]
    raw["allowed_guild_ids"] = ["8"]
    raw["request_channels"] = ["1"]
    raw["chat_channels"] = []
    raw.pop("archive_forum_id", None)
    raw.update(overrides)
    return raw


class ConfigChannelTests(unittest.TestCase):
    def load(self, raw: dict):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            return config_module.Config.load(path)

    def test_id_items_and_per_day_default(self) -> None:
        cfg = self.load(example_raw(chat_channels=[
            "10", 11, {"id": 12}, {"id": "13", "per_day": 2, "topic": "야구"},
        ]))
        self.assertEqual(cfg.chat_channels, {10, 11, 12, 13})
        self.assertEqual(cfg.chat_per_day, {10: 0, 11: 0, 12: 0, 13: 2})
        self.assertEqual(cfg.chat_topics, {13: "야구"})
        self.assertTrue(cfg.is_agent_channel(10))

    def test_channel_ids_reject_non_string_or_integer_types(self) -> None:
        for value in (10.9, 10.0, True, False, None, [], {}):
            for item in (value, {"id": value}):
                with self.subTest(item=item), self.assertRaises(ValueError):
                    self.load(example_raw(chat_channels=[item]))

    def test_duplicate_ids_are_merged(self) -> None:
        cfg = self.load(example_raw(chat_channels=[
            {"id": "10", "per_day": 3}, 10, {"id": "10", "topic": "음악"},
        ]))
        self.assertEqual(cfg.chat_channels, {10})
        self.assertEqual(cfg.chat_per_day, {10: 3})
        self.assertEqual(cfg.chat_topics, {10: "음악"})

    def test_only_chat_channels_means_no_request_channels(self) -> None:
        raw = example_raw(chat_channels=["10"])
        del raw["request_channels"]
        cfg = self.load(raw)
        self.assertEqual(cfg.request_channels, set())
        self.assertEqual(cfg.chat_channels, {10})

    def test_unknown_top_level_keys_are_rejected(self) -> None:
        raw = example_raw(zeta=1, alpha=[])
        with self.assertRaises(ValueError) as caught:
            self.load(raw)
        self.assertEqual(
            str(caught.exception),
            "알 수 없는 설정 키: alpha, zeta. 채팅 채널은 chat_channels에 적는다.",
        )
        legacy = example_raw()
        legacy["auto" + "_chat_channels"] = []
        with self.assertRaisesRegex(ValueError, "알 수 없는 설정 키: .*chat_channels에 적는다"):
            self.load(legacy)

    def test_overlap_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "request_channels와 chat_channels에 같은 채널이 있습니다: 1"):
            self.load(example_raw(chat_channels=["1"]))

    def test_archive_forum_only_in_chat_is_rejected(self) -> None:
        raw = example_raw(archive_forum_id="20", chat_channels=["20"])
        with self.assertRaises(ValueError):
            self.load(raw)
        raw = example_raw(archive_forum_id="20", request_channels=["20"], chat_channels=[{"id": "20"}])
        with self.assertRaisesRegex(ValueError, "chat_channels"):
            self.load(raw)

    def test_empty_channels_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "request_channels나 chat_channels에 Discord 채널을"):
            self.load(example_raw(request_channels=[], chat_channels=[]))

    def test_invalid_items_are_rejected(self) -> None:
        for item, pattern in (
            ({"per_day": 1}, "chat_channels의 항목에 id가 없습니다"),
            ({"id": "10", "per_day": -1}, "per_day는 0 이상"),
            ({"id": "10", "topic": " "}, "topic이 비어 있습니다"),
        ):
            with self.subTest(item=item), self.assertRaisesRegex(ValueError, pattern):
                self.load(example_raw(chat_channels=[item]))

    def test_auto_conversations_per_day_is_rejected(self) -> None:
        raw = example_raw()
        raw["chat"]["auto_conversations_per_day"] = 2
        with self.assertRaisesRegex(ValueError, "chat_channels의 각 항목에 per_day로 적으세요"):
            self.load(raw)


if __name__ == "__main__":
    unittest.main()
