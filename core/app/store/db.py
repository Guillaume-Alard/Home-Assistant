"""Persistance SQLite : le fil de conversation (voix + écrit confondus).

Une seule conversation « main » en Phase 1 ; la colonne existe déjà pour
permettre des fils séparés plus tard sans migration.
"""

from __future__ import annotations

import json
import re
import unicodedata
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

-- Mémoire persistante (Phase 1) : ce que Luna apprend de l'utilisateur pour
-- personnaliser ses réponses. ENRICHISSEMENT DE CONTEXTE UNIQUEMENT — aucune
-- action sur le monde réel n'en découle jamais. `subject` est prêt pour la
-- reconnaissance de locuteur (Phase 2) sans migration.
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    subject     TEXT NOT NULL DEFAULT 'guillaume',
    category    TEXT NOT NULL DEFAULT 'fait',   -- preference | habitude | style | fait
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'luna',    -- luna | manuel
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_subject
    ON memories (subject, created_at);

-- Reconnaissance de locuteur (Phase 2) : profils vocaux (Guillaume, conjointe,
-- fils…) et leurs empreintes d'enrôlement. L'empreinte est un vecteur (JSON) ;
-- la reconnaissance NE PEUT JAMAIS élever les droits (les actions sensibles
-- restent réservées à l'interface pour tout le monde) — elle personnalise et,
-- pour un inconnu, restreint.
CREATE TABLE IF NOT EXISTS speakers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    is_owner    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS speaker_samples (
    id          TEXT PRIMARY KEY,
    speaker_id  TEXT NOT NULL,
    vector      TEXT NOT NULL,              -- JSON : liste de flottants (empreinte)
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_speaker_samples
    ON speaker_samples (speaker_id);

-- Pages web (Phase 5) : Luna RÉDIGE une copie de travail (html) ; la version
-- EN LIGNE (published_html) n'existe qu'après publication PAR Guillaume dans le
-- cockpit — revue humaine avant toute publication, jamais contournée.
CREATE TABLE IF NOT EXISTS pages (
    id             TEXT PRIMARY KEY,
    slug           TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    html           TEXT NOT NULL DEFAULT '',   -- copie de travail (brouillon)
    published_html TEXT,                        -- version en ligne (NULL = non publiée)
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    published_at   TEXT
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


def slugify(text: str) -> str:
    """Titre → identifiant d'URL : sans accents, minuscules, tirets."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or "page")[:60]


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

    # ── Mémoire persistante (Phase 1) ────────────────────────────────────

    async def add_memory(
        self,
        content: str,
        *,
        category: str = "fait",
        subject: str = "guillaume",
        source: str = "luna",
    ) -> dict:
        assert self._db is not None, "Store non ouvert"
        now = _now_iso()
        record = {
            "id": uuid.uuid4().hex[:12],
            "subject": subject,
            "category": category,
            "content": content,
            "source": source,
            "created_at": now,
            "updated_at": now,
        }
        await self._db.execute(
            "INSERT INTO memories (id, subject, category, content, source, created_at, updated_at)"
            " VALUES (:id, :subject, :category, :content, :source, :created_at, :updated_at)",
            record,
        )
        await self._db.commit()
        return record

    async def list_memories(self, subject: str | None = None, limit: int = 200) -> list[dict]:
        """Les `limit` souvenirs les plus récents, en ordre chronologique."""
        assert self._db is not None, "Store non ouvert"
        if subject:
            cursor = await self._db.execute(
                "SELECT * FROM memories WHERE subject = ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (subject, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM memories ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in reversed(await cursor.fetchall())]

    async def get_memory(self, mem_id: str) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("SELECT * FROM memories WHERE id = ?", (mem_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_memory(self, mem_id: str, **fields) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        fields = {k: v for k, v in fields.items() if k in ("content", "category", "subject")}
        if fields:
            fields["updated_at"] = _now_iso()
            keys = ", ".join(f"{k} = ?" for k in fields)
            await self._db.execute(
                f"UPDATE memories SET {keys} WHERE id = ?", (*fields.values(), mem_id)
            )
            await self._db.commit()
        return await self.get_memory(mem_id)

    async def delete_memory(self, mem_id: str) -> bool:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    # ── Profils vocaux (Phase 2) ─────────────────────────────────────────

    async def add_speaker(self, name: str, *, is_owner: bool = False) -> dict:
        assert self._db is not None, "Store non ouvert"
        record = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "is_owner": 1 if is_owner else 0,
            "created_at": _now_iso(),
        }
        await self._db.execute(
            "INSERT INTO speakers (id, name, is_owner, created_at)"
            " VALUES (:id, :name, :is_owner, :created_at)",
            record,
        )
        await self._db.commit()
        return record

    async def get_speaker(self, speaker_id: str) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("SELECT * FROM speakers WHERE id = ?", (speaker_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_speakers(self) -> list[dict]:
        """Les profils vocaux avec leur nombre d'empreintes enrôlées."""
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute(
            "SELECT s.id, s.name, s.is_owner, s.created_at,"
            " (SELECT COUNT(*) FROM speaker_samples e WHERE e.speaker_id = s.id) AS samples"
            " FROM speakers s ORDER BY s.is_owner DESC, s.created_at"
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def delete_speaker(self, speaker_id: str) -> bool:
        assert self._db is not None, "Store non ouvert"
        await self._db.execute("DELETE FROM speaker_samples WHERE speaker_id = ?", (speaker_id,))
        cursor = await self._db.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def add_speaker_sample(self, speaker_id: str, vector: list[float]) -> dict:
        assert self._db is not None, "Store non ouvert"
        record = {
            "id": uuid.uuid4().hex[:12],
            "speaker_id": speaker_id,
            "vector": json.dumps(vector),
            "created_at": _now_iso(),
        }
        await self._db.execute(
            "INSERT INTO speaker_samples (id, speaker_id, vector, created_at)"
            " VALUES (:id, :speaker_id, :vector, :created_at)",
            record,
        )
        await self._db.commit()
        return record

    async def speaker_profiles(self) -> list[dict]:
        """Profils prêts pour la reconnaissance : {id, name, is_owner, vectors[]}."""
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute(
            "SELECT s.id, s.name, s.is_owner, e.vector"
            " FROM speakers s JOIN speaker_samples e ON e.speaker_id = s.id"
        )
        by_id: dict[str, dict] = {}
        for row in await cursor.fetchall():
            prof = by_id.setdefault(
                row["id"],
                {"id": row["id"], "name": row["name"], "is_owner": bool(row["is_owner"]), "vectors": []},
            )
            try:
                prof["vectors"].append(json.loads(row["vector"]))
            except (ValueError, TypeError):
                continue
        return list(by_id.values())

    # ── Pages web (Phase 5) ──────────────────────────────────────────────

    async def _unique_slug(self, base: str) -> str:
        slug = base or "page"
        n = 2
        while True:
            cursor = await self._db.execute("SELECT 1 FROM pages WHERE slug = ?", (slug,))
            if await cursor.fetchone() is None:
                return slug
            slug = f"{base}-{n}"
            n += 1

    async def add_page(self, *, title: str, html: str = "", slug: str = "") -> dict:
        assert self._db is not None, "Store non ouvert"
        slug = await self._unique_slug(slug or slugify(title))
        now = _now_iso()
        record = {
            "id": uuid.uuid4().hex[:12], "slug": slug, "title": title,
            "html": html, "published_html": None,
            "created_at": now, "updated_at": now, "published_at": None,
        }
        await self._db.execute(
            "INSERT INTO pages (id, slug, title, html, published_html, created_at, updated_at, published_at)"
            " VALUES (:id, :slug, :title, :html, :published_html, :created_at, :updated_at, :published_at)",
            record,
        )
        await self._db.commit()
        return record

    async def get_page(self, page_id: str) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("SELECT * FROM pages WHERE id = ?", (page_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_published_page(self, slug: str) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute(
            "SELECT * FROM pages WHERE slug = ? AND published_html IS NOT NULL", (slug,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_pages(self) -> list[dict]:
        """Métadonnées des pages (sans le HTML) : id, slug, titre, état, dates."""
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute(
            "SELECT id, slug, title, created_at, updated_at, published_at,"
            " published_html IS NOT NULL AS published,"
            " (published_html IS NOT NULL AND html != published_html) AS dirty"
            " FROM pages ORDER BY updated_at DESC"
        )
        return [
            {**dict(r), "published": bool(r["published"]), "dirty": bool(r["dirty"])}
            for r in await cursor.fetchall()
        ]

    async def update_page(self, page_id: str, **fields) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        fields = {k: v for k, v in fields.items() if k in ("title", "html")}
        if fields:
            fields["updated_at"] = _now_iso()
            keys = ", ".join(f"{k} = ?" for k in fields)
            await self._db.execute(
                f"UPDATE pages SET {keys} WHERE id = ?", (*fields.values(), page_id)
            )
            await self._db.commit()
        return await self.get_page(page_id)

    async def publish_page(self, page_id: str) -> dict | None:
        """Met la copie de travail EN LIGNE (action déclenchée par Guillaume)."""
        assert self._db is not None, "Store non ouvert"
        await self._db.execute(
            "UPDATE pages SET published_html = html, published_at = ? WHERE id = ?",
            (_now_iso(), page_id),
        )
        await self._db.commit()
        return await self.get_page(page_id)

    async def unpublish_page(self, page_id: str) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        await self._db.execute(
            "UPDATE pages SET published_html = NULL, published_at = NULL WHERE id = ?", (page_id,)
        )
        await self._db.commit()
        return await self.get_page(page_id)

    async def delete_page(self, page_id: str) -> bool:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("DELETE FROM pages WHERE id = ?", (page_id,))
        await self._db.commit()
        return cursor.rowcount > 0
