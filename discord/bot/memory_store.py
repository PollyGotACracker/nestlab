"""서버 단위 장기기억과 캐릭터별 persona 기억을 SQLite 파일에 보관한다.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

# 기억 종류. archive 및 explicit은 서버 장기기억, persona_*는 캐릭터별 기억이다.
KIND_ARCHIVE = "archive"
KIND_EXPLICIT = "explicit"
KIND_PERSONA_EVENT = "persona_event"
KIND_PERSONA_SETTING = "persona_setting"
LONG_TERM_KINDS = (KIND_ARCHIVE, KIND_EXPLICIT)
PERSONA_KINDS = (KIND_PERSONA_EVENT, KIND_PERSONA_SETTING)
SOURCE_CHECK_KINDS = (KIND_EXPLICIT, *PERSONA_KINDS)

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    agent TEXT,
    content TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    channel_id INTEGER,
    thread_id INTEGER,
    uploaded INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_guild_kind ON memories(guild_id, kind);
CREATE TABLE IF NOT EXISTS memory_sources (
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL,
    channel_id INTEGER
);
CREATE INDEX IF NOT EXISTS memory_sources_message ON memory_sources(message_id);
CREATE INDEX IF NOT EXISTS memory_sources_memory ON memory_sources(memory_id);
CREATE TABLE IF NOT EXISTS attachment_sources (
    message_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL
);
"""

# 검색어에서 떼어 보는 한국어 조사. 남는 말이 두 글자 이상일 때만 뗀다.
PARTICLES = ("에서", "으로", "한테", "에게", "까지", "부터", "은", "는", "이", "가", "을", "를", "에", "의", "도", "로", "와", "과")
MAX_TERMS = 20


# 검색 문장을 소문자 낱말 목록으로 나눈다. 조사를 뗀 형태도 함께 넣는다.
def search_terms(text: str) -> list[str]:
    # 소문자 단어를 뽑고 조사를 뗀 후보를 함께 모은다.
    terms: list[str] = []
    for word in re.findall(r"[0-9A-Za-z가-힣_]{2,}", text.lower()):
        candidates = [word]
        for particle in PARTICLES:
            if word.endswith(particle) and len(word) - len(particle) >= 2:
                candidates.append(word[: -len(particle)])
                break
        for candidate in candidates:
            if candidate not in terms:
                terms.append(candidate)

    # 검색어 수 한도만큼 반환한다.
    return terms[:MAX_TERMS]


class MemoryStore:
    # 연결을 받아 외래 키를 켜고 스키마를 만든다.
    def __init__(self, conn: sqlite3.Connection) -> None:
        # 연결을 설정하고 스키마를 만든다.
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    # 파일 경로의 SQLite DB를 연다.
    @classmethod
    def open(cls, path: Path | str) -> "MemoryStore":
        return cls(sqlite3.connect(str(path)))

    # DB 연결을 닫는다.
    def close(self) -> None:
        self.conn.close()

    # 내부 추가 함수. 트랜잭션은 호출한 공개 함수가 맡는다.
    def _insert(
        self, guild_id: int, kind: str, content: str, *, agent: str | None, author: str,
        channel_id: int | None, thread_id: int | None, sources: Iterable[tuple[int, int | None]],
    ) -> int:
        # 기억 행을 추가한다.
        now = time.time()
        cursor = self.conn.execute(
            "INSERT INTO memories (guild_id, kind, agent, content, author, channel_id, thread_id,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, kind, agent, content, author, channel_id, thread_id, now, now),
        )
        memory_id = int(cursor.lastrowid)

        # 출처 메시지를 연결한다.
        self.conn.executemany(
            "INSERT INTO memory_sources (memory_id, message_id, channel_id) VALUES (?, ?, ?)",
            [(memory_id, message_id, source_channel) for message_id, source_channel in dict(sources).items()],
        )
        return memory_id

    # 기억 하나를 트랜잭션 안에서 추가하고 기억 ID를 반환한다.
    def add(
        self, guild_id: int, kind: str, content: str, *, agent: str | None = None, author: str = "",
        channel_id: int | None = None, thread_id: int | None = None,
        sources: Iterable[tuple[int, int | None]] = (),
    ) -> int:
        # 한 트랜잭션 안에서 기억을 추가한다.
        with self.conn:
            return self._insert(
                guild_id, kind, content, agent=agent, author=author,
                channel_id=channel_id, thread_id=thread_id, sources=sources,
            )

    # archive 메시지 ID에 연결된 기억을 갱신하거나 새로 추가한다.
    def _upsert_archive(
        self, guild_id: int, message_id: int, channel_id: int | None, thread_id: int | None,
        author: str, content: str,
    ) -> int:
        # 같은 메시지의 아카이브 기억이 있는지 찾는다.
        row = self.conn.execute(
            "SELECT m.id FROM memories m JOIN memory_sources s ON s.memory_id = m.id"
            " WHERE m.kind = ? AND s.message_id = ?",
            (KIND_ARCHIVE, message_id),
        ).fetchone()

        # 없으면 새로 넣고, 있으면 내용을 갱신한다.
        if row is None:
            return self._insert(
                guild_id, KIND_ARCHIVE, content, agent=None, author=author,
                channel_id=channel_id, thread_id=thread_id, sources=[(message_id, thread_id)],
            )
        self.conn.execute(
            "UPDATE memories SET guild_id = ?, content = ?, author = ?, channel_id = ?, thread_id = ?,"
            " updated_at = ? WHERE id = ?",
            (guild_id, content, author, channel_id, thread_id, time.time(), row["id"]),
        )
        return int(row["id"])

    # archive 메시지 하나를 메시지 ID 기준으로 추가하거나 갱신한다.
    def upsert_archive(
        self, guild_id: int, message_id: int, channel_id: int | None, thread_id: int | None,
        author: str, content: str,
    ) -> int:
        with self.conn:
            return self._upsert_archive(guild_id, message_id, channel_id, thread_id, author, content)

    # 스레드 하나의 archive 메시지를 한 트랜잭션으로 반영한다. skip_ids의 메시지는 넣지 않는다.
    def sync_archive_thread(
        self, guild_id: int, channel_id: int | None, thread_id: int,
        records: Iterable[tuple[int, str, str]], skip_ids: set[int],
    ) -> int:
        # 한 트랜잭션에서 건너뛸 메시지를 뺀 기록을 반영하고 개수를 센다.
        count = 0
        # 한 트랜잭션 안에서 건너뛸 메시지를 뺀 기록을 반영한다.
        with self.conn:
            for message_id, author, content in records:
                if message_id in skip_ids:
                    continue
                self._upsert_archive(guild_id, message_id, channel_id, thread_id, author, content)
                count += 1
        return count

    # archive 기억에 연결된 메시지 ID 전체를 반환한다.
    def archive_message_ids(self) -> set[int]:
        # archive 기억의 출처 메시지 ID를 모두 조회한다.
        rows = self.conn.execute(
            "SELECT s.message_id FROM memories m JOIN memory_sources s ON s.memory_id = m.id WHERE m.kind = ?",
            (KIND_ARCHIVE,),
        ).fetchall()
        return {int(row["message_id"]) for row in rows}

    # 기억 ID 목록을 지우고 지운 행을 반환한다.
    def _delete_ids(self, memory_ids: list[int]) -> list[dict[str, Any]]:
        # 지울 id가 없으면 빈 목록을 반환한다.
        if not memory_ids:
            return []

        # 지울 행을 읽어 둔다.
        marks = ",".join("?" * len(memory_ids))
        rows = [dict(row) for row in self.conn.execute(
            f"SELECT * FROM memories WHERE id IN ({marks})", memory_ids,
        ).fetchall()]

        # 출처와 기억 행을 지운다.
        with self.conn:
            self.conn.execute(f"DELETE FROM memory_sources WHERE memory_id IN ({marks})", memory_ids)
            self.conn.execute(f"DELETE FROM memories WHERE id IN ({marks})", memory_ids)
        return rows

    # 메시지 ID에 연결된 기억 ID를 종류 조건으로 찾는다.
    def _memory_ids_for_messages(self, message_ids: Iterable[int], kinds: tuple[str, ...] | None) -> list[int]:
        # id 목록을 정리하고 비었으면 빈 목록을 반환한다.
        ids = sorted(set(message_ids))
        if not ids:
            return []

        # 메시지 id에 연결된 기억 id를 찾는 질의를 만든다.
        marks = ",".join("?" * len(ids))
        sql = (
            f"SELECT DISTINCT m.id FROM memories m JOIN memory_sources s ON s.memory_id = m.id"
            f" WHERE s.message_id IN ({marks})"
        )
        params: list[Any] = list(ids)

        # 종류가 주어지면 종류로 거른다.
        if kinds:
            sql += f" AND m.kind IN ({','.join('?' * len(kinds))})"
            params += list(kinds)
        return [int(row["id"]) for row in self.conn.execute(sql, params).fetchall()]

    # 원본 메시지 중 하나라도 삭제된 기억을 지우고 지운 행을 돌려준다.
    def delete_by_messages(self, message_ids: Iterable[int]) -> list[dict[str, Any]]:
        return self._delete_ids(self._memory_ids_for_messages(message_ids, None))

    # 동기화에서 찾지 못한 archive 기억만 지운다.
    def delete_archive_messages(self, message_ids: Iterable[int]) -> list[dict[str, Any]]:
        return self._delete_ids(self._memory_ids_for_messages(message_ids, (KIND_ARCHIVE,)))

    # 스레드 및 채널이 삭제되면 그 안의 메시지를 원본으로 둔 기억을 모두 지운다.
    def delete_by_channel(self, channel_id: int) -> list[dict[str, Any]]:
        # 채널과 관련된 기억 ID를 찾아 삭제한다.
        rows = self.conn.execute(
            "SELECT DISTINCT m.id FROM memories m LEFT JOIN memory_sources s ON s.memory_id = m.id"
            " WHERE s.channel_id = ? OR m.thread_id = ? OR m.channel_id = ?",
            (channel_id, channel_id, channel_id),
        ).fetchall()
        return self._delete_ids([int(row["id"]) for row in rows])

    # 기억 ID로 기억 하나를 읽는다.
    def get(self, memory_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return dict(row) if row else None

    # 낱말이 많이 맞는 순, 같으면 최신 순으로 기억을 찾는다. require_match면 맞는 낱말이 없는 기억은 뺀다.
    def search(
        self, guild_id: int, kinds: tuple[str, ...], terms: list[str], limit: int, *,
        agent: str | None = None, require_match: bool = True,
    ) -> list[dict[str, Any]]:
        # 서버와 종류, 캐릭터로 거르는 질의를 만든다.
        sql = f"SELECT * FROM memories WHERE guild_id = ? AND kind IN ({','.join('?' * len(kinds))})"
        params: list[Any] = [guild_id, *kinds]
        if agent is not None:
            sql += " AND agent = ?"
            params.append(agent)

        # 검색어 일치가 필요하면 검색어 조건을 더한다.
        if require_match:
            if not terms:
                return []
            sql += " AND (" + " OR ".join("instr(lower(content), ?) > 0" for _ in terms) + ")"
            params += terms

        # 일치한 검색어 수로 점수를 매겨 정렬하고 한도만큼 반환한다.
        rows = [dict(row) for row in self.conn.execute(sql, params).fetchall()]
        for row in rows:
            text = row["content"].lower()
            row["score"] = sum(1 for term in terms if term in text)
        rows.sort(key=lambda row: (row["score"], row["updated_at"], row["id"]), reverse=True)
        return rows[:limit]

    # 한 캐릭터의 서버별 persona 기억을 최신 순으로 돌려준다.
    def list_persona(self, guild_id: int, agent: str, kinds: tuple[str, ...] = PERSONA_KINDS) -> list[dict[str, Any]]:
        # 에이전트의 캐릭터 기억을 최신순으로 조회한다.
        rows = self.conn.execute(
            f"SELECT * FROM memories WHERE guild_id = ? AND agent = ? AND kind IN ({','.join('?' * len(kinds))})"
            " ORDER BY updated_at DESC, id DESC",
            (guild_id, agent, *kinds),
        ).fetchall()
        return [dict(row) for row in rows]

    # 저장소 MEMORY.md에 반영된 한 캐릭터의 기억(모든 서버)을 오래된 순으로 돌려준다.
    def uploaded_persona(self, agent: str) -> list[dict[str, Any]]:
        # 에이전트가 업로드한 캐릭터 기억을 생성순으로 조회한다.
        rows = self.conn.execute(
            f"SELECT * FROM memories WHERE agent = ? AND uploaded = 1 AND kind IN ({','.join('?' * len(PERSONA_KINDS))})"
            " ORDER BY created_at, id",
            (agent, *PERSONA_KINDS),
        ).fetchall()
        return [dict(row) for row in rows]

    # 저장소에 올린 기억을 업로드 완료로 표시한다.
    def mark_uploaded(self, memory_ids: Iterable[int]) -> None:
        # 지정한 기억을 업로드 완료로 표시한다.
        ids = list(memory_ids)
        if not ids:
            return
        with self.conn:
            self.conn.execute(
                f"UPDATE memories SET uploaded = 1 WHERE id IN ({','.join('?' * len(ids))})", ids,
            )

    # 시작 시 원본 확인 대상(explicit 및 persona 기억)의 (메시지 ID, 채널 ID) 목록.
    def check_sources(self) -> list[tuple[int, int | None]]:
        # 출처 확인 대상 기억의 메시지와 채널 ID를 조회한다.
        rows = self.conn.execute(
            "SELECT DISTINCT s.message_id, s.channel_id FROM memories m JOIN memory_sources s ON s.memory_id = m.id"
            f" WHERE m.kind IN ({','.join('?' * len(SOURCE_CHECK_KINDS))})",
            SOURCE_CHECK_KINDS,
        ).fetchall()
        return [(int(row["message_id"]), row["channel_id"]) for row in rows]

    # 첨부 파일이 있는 메시지와 채널을 기록한다.
    def add_attachment_source(self, message_id: int, channel_id: int) -> None:
        # 첨부 출처를 저장한다.
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO attachment_sources (message_id, channel_id) VALUES (?, ?)",
                (message_id, channel_id),
            )

    # 첨부 원본 기록에서 메시지 ID 목록을 지운다.
    def remove_attachment_sources(self, message_ids: Iterable[int]) -> None:
        # 지정한 메시지의 첨부 출처를 삭제한다.
        ids = list(message_ids)
        if not ids:
            return
        with self.conn:
            self.conn.execute(
                f"DELETE FROM attachment_sources WHERE message_id IN ({','.join('?' * len(ids))})", ids,
            )

    # 첨부 원본 기록을 전체 또는 채널 기준으로 반환한다.
    def attachment_sources(self, channel_id: int | None = None) -> list[tuple[int, int]]:
        # 채널 조건에 맞는 첨부 출처를 조회한다.
        if channel_id is None:
            rows = self.conn.execute("SELECT message_id, channel_id FROM attachment_sources").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT message_id, channel_id FROM attachment_sources WHERE channel_id = ?", (channel_id,),
            ).fetchall()
        return [(int(row["message_id"]), int(row["channel_id"])) for row in rows]
