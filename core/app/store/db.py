"""Persistance SQLite : le fil de conversation (voix + écrit confondus).

Une seule conversation « main » en Phase 1 ; la colonne existe déjà pour
permettre des fils séparés plus tard sans migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL DEFAULT 'main',
    role            TEXT NOT NULL,             -- user | assistant
    content         TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'text',  -- text | voice
    created_at      TEXT NOT NULL              -- ISO 8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_messages_conv
    ON messages (conversation_id, created_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def add_message(
        self, role: str, content: str, source: str = "text", conversation_id: str = "main"
    ) -> dict:
        assert self._db is not None, "Store non ouvert"
        record = {
            "id": uuid.uuid4().hex[:12],
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "source": source,
            "created_at": _now_iso(),
        }
        await self._db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, source, created_at)"
            " VALUES (:id, :conversation_id, :role, :content, :source, :created_at)",
            record,
        )
        await self._db.commit()
        return record

    async def recent_messages(self, limit: int = 50, conversation_id: str = "main") -> list[dict]:
        """Les `limit` derniers messages, en ordre chronologique."""
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute(
            "SELECT id, conversation_id, role, content, source, created_at"
            " FROM messages WHERE conversation_id = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (conversation_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in reversed(rows)]
