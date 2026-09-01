"""Persistance SQLite : le fil de conversation (voix + écrit confondus).

Une seule conversation « main » en Phase 1 ; la colonne existe déjà pour
permettre des fils séparés plus tard sans migration.
"""

from __future__ import annotations

import json
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
    source          TEXT NOT NULL DEFAULT 'text',  -- text | voice | alert
    created_at      TEXT NOT NULL              -- ISO 8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_messages_conv
    ON messages (conversation_id, created_at);

-- File des propositions du moteur « propose puis approuve ».
-- num est court et stable : « Sentinel, approuve la proposition 3 ».
CREATE TABLE IF NOT EXISTS proposals (
    num           INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    justification TEXT NOT NULL DEFAULT '',
    risk          TEXT NOT NULL DEFAULT 'medium',  -- low | medium | sensitive
    rollback      TEXT NOT NULL DEFAULT '',
    action_id     TEXT NOT NULL,
    params        TEXT NOT NULL DEFAULT '{}',      -- JSON
    status        TEXT NOT NULL DEFAULT 'pending',
    created_by    TEXT NOT NULL DEFAULT 'sentinel',
    created_at    TEXT NOT NULL,
    decided_at    TEXT,
    decided_via   TEXT,                            -- ui | voice | text
    executed_at   TEXT,
    result        TEXT,
    error         TEXT
);

-- Journal append-only : qui, quoi, quand, avec quelle autorisation, résultat.
CREATE TABLE IF NOT EXISTS journal (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    kind          TEXT NOT NULL,      -- direct | proposal | alert | system
    actor         TEXT NOT NULL,
    action_id     TEXT NOT NULL,
    params        TEXT NOT NULL DEFAULT '{}',
    authorization TEXT NOT NULL,
    outcome       TEXT NOT NULL,      -- ok | refused | failed | needs_confirmation | created | decided
    detail        TEXT NOT NULL DEFAULT ''
);
"""


def _proposal_dict(row) -> dict:
    p = dict(row)
    try:
        p["params"] = json.loads(p.get("params") or "{}")
    except ValueError:
        p["params"] = {}
    return p


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

    # ── Propositions ─────────────────────────────────────────────────────

    async def add_proposal(
        self,
        *,
        title: str,
        description: str = "",
        justification: str = "",
        risk: str = "medium",
        rollback: str = "",
        action_id: str,
        params: dict | None = None,
        created_by: str = "sentinel",
    ) -> dict:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute(
            "INSERT INTO proposals (title, description, justification, risk, rollback,"
            " action_id, params, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                title, description, justification, risk, rollback,
                action_id, json.dumps(params or {}, ensure_ascii=False),
                created_by, _now_iso(),
            ),
        )
        await self._db.commit()
        return await self.get_proposal(cursor.lastrowid)

    async def get_proposal(self, num: int) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("SELECT * FROM proposals WHERE num = ?", (num,))
        row = await cursor.fetchone()
        return _proposal_dict(row) if row else None

    async def list_proposals(self, status: str | None = None, limit: int = 50) -> list[dict]:
        """Les propositions, plus récentes d'abord (filtrées par statut si donné)."""
        assert self._db is not None, "Store non ouvert"
        if status:
            cursor = await self._db.execute(
                "SELECT * FROM proposals WHERE status = ? ORDER BY num DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM proposals ORDER BY num DESC LIMIT ?", (limit,)
            )
        return [_proposal_dict(r) for r in await cursor.fetchall()]

    async def update_proposal(self, num: int, **fields) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        if fields:
            if "params" in fields and isinstance(fields["params"], dict):
                fields["params"] = json.dumps(fields["params"], ensure_ascii=False)
            keys = ", ".join(f"{k} = ?" for k in fields)
            await self._db.execute(
                f"UPDATE proposals SET {keys} WHERE num = ?", (*fields.values(), num)
            )
            await self._db.commit()
        return await self.get_proposal(num)

    # ── Journal (append-only) ────────────────────────────────────────────

    async def add_journal(
        self,
        *,
        kind: str,
        actor: str,
        action_id: str,
        params: dict | None = None,
        authorization: str,
        outcome: str,
        detail: str = "",
    ) -> None:
        assert self._db is not None, "Store non ouvert"
        await self._db.execute(
            "INSERT INTO journal (ts, kind, actor, action_id, params, authorization, outcome, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now_iso(), kind, actor, action_id,
                json.dumps(params or {}, ensure_ascii=False),
                authorization, outcome, detail,
            ),
        )
        await self._db.commit()

    async def list_journal(self, limit: int = 100) -> list[dict]:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute(
            "SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cursor.fetchall()]
