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

-- Auto-amélioration encadrée (Phase 6) : Luna PROPOSE une évolution de son
-- propre code / sa config sous forme de DIFF. Rien ne s'applique jamais tout
-- seul — c'est une proposition, relue et appliquée par Guillaume (revue humaine
-- avant exécution, jamais contournée). Le diff est passé au crible de la
-- politique (selfmod/policy.py) AVANT d'arriver ici : aucun garde-fou de
-- sécurité, aucun secret. `status` : pending | accepted | rejected.
CREATE TABLE IF NOT EXISTS suggestions (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL DEFAULT 'code',    -- code | config
    title       TEXT NOT NULL,
    rationale   TEXT NOT NULL DEFAULT '',        -- pourquoi (motivation)
    target      TEXT NOT NULL DEFAULT '',        -- fichier(s) / réglage visé
    diff        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL,
    decided_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_suggestions_status
    ON suggestions (status, created_at);

-- Proactivité contextuelle (Phase 7) : Luna observe (état maison + heure + ce
-- qu'elle sait) et SUGGÈRE — jamais n'exécute. Une suggestion actionnable peut,
-- sur ton accord, devenir une PROPOSITION (moteur d'actions), elle-même à
-- approuver. `key` sert à l'anti-répétition ; `rule` au « ne plus me suggérer ça ».
-- status : active | snoozed | dismissed | acted.
CREATE TABLE IF NOT EXISTS proactive (
    id           TEXT PRIMARY KEY,
    key          TEXT NOT NULL,
    rule         TEXT NOT NULL,
    title        TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT '',
    severity     TEXT NOT NULL DEFAULT 'info',
    category     TEXT NOT NULL DEFAULT '',
    action       TEXT,                        -- JSON de l'action proposable (NULL = simple constat)
    status       TEXT NOT NULL DEFAULT 'active',
    proposal_num INTEGER,                      -- n° de la proposition créée (si « acted »)
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    snooze_until TEXT
);
CREATE INDEX IF NOT EXISTS idx_proactive_key ON proactive (key, created_at);
CREATE INDEX IF NOT EXISTS idx_proactive_status ON proactive (status, created_at);

-- Règles que Guillaume a demandé de taire (« ne plus me suggérer ça »).
CREATE TABLE IF NOT EXISTS proactive_mutes (
    rule       TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

-- Scénarios & routines (Phase 8) : des séquences d'actions NOMMÉES et
-- RÉUTILISABLES (ex. « Bonne nuit »). Luna les PROPOSE (depuis tes habitudes ou
-- la conversation) ; Guillaume les ACTIVE (revue humaine), puis les déclenche
-- quand il veut. Une routine ne contient JAMAIS d'action sensible (verrouillé à
-- la création). `steps` : JSON [{action_id, params, label}]. `signature` sert à
-- l'anti-doublon des routines apprises. status : proposed | active | rejected.
CREATE TABLE IF NOT EXISTS routines (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    steps       TEXT NOT NULL DEFAULT '[]',
    signature   TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'manuel',   -- appris | manuel | llm
    status      TEXT NOT NULL DEFAULT 'proposed',
    run_count   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    last_run_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_routines_status ON routines (status, created_at);

-- Minuteurs & rappels vocaux (Phase 10) : 100% local. Un minuteur (« pâtes, 10
-- min ») ou un rappel daté (« sortir le plat, dans 20 min ») ; à l'échéance,
-- Luna carillonne et le dit. `due_at` en UTC ISO (comparable). kind : timer |
-- reminder. status : active | fired | cancelled.
CREATE TABLE IF NOT EXISTS reminders (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL DEFAULT 'reminder',
    label      TEXT NOT NULL DEFAULT '',
    due_at     TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    subject    TEXT NOT NULL DEFAULT 'guillaume',
    created_at TEXT NOT NULL,
    fired_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_active ON reminders (status, due_at);
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

    # ── Auto-amélioration : propositions d'évolution (Phase 6) ───────────────

    async def add_suggestion(
        self, *, kind: str, title: str, rationale: str = "", target: str = "", diff: str = "",
    ) -> dict:
        assert self._db is not None, "Store non ouvert"
        record = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind if kind in ("code", "config") else "code",
            "title": title,
            "rationale": rationale,
            "target": target,
            "diff": diff,
            "status": "pending",
            "created_at": _now_iso(),
            "decided_at": None,
        }
        await self._db.execute(
            "INSERT INTO suggestions (id, kind, title, rationale, target, diff, status, created_at, decided_at)"
            " VALUES (:id, :kind, :title, :rationale, :target, :diff, :status, :created_at, :decided_at)",
            record,
        )
        await self._db.commit()
        return record

    async def get_suggestion(self, sug_id: str) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("SELECT * FROM suggestions WHERE id = ?", (sug_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_suggestions(self, status: str | None = None, limit: int = 100) -> list[dict]:
        """Métadonnées (SANS le diff) : plus récentes d'abord."""
        assert self._db is not None, "Store non ouvert"
        cols = "id, kind, title, target, status, created_at, decided_at"
        if status:
            cursor = await self._db.execute(
                f"SELECT {cols} FROM suggestions WHERE status = ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = await self._db.execute(
                f"SELECT {cols} FROM suggestions ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in await cursor.fetchall()]

    async def decide_suggestion(self, sug_id: str, status: str) -> dict | None:
        """Guillaume accepte / rejette une proposition (revue humaine)."""
        assert self._db is not None, "Store non ouvert"
        if status not in ("accepted", "rejected", "pending"):
            return await self.get_suggestion(sug_id)
        await self._db.execute(
            "UPDATE suggestions SET status = ?, decided_at = ? WHERE id = ?",
            (status, _now_iso() if status != "pending" else None, sug_id),
        )
        await self._db.commit()
        return await self.get_suggestion(sug_id)

    async def delete_suggestion(self, sug_id: str) -> bool:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("DELETE FROM suggestions WHERE id = ?", (sug_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    # ── Proactivité contextuelle (Phase 7) ───────────────────────────────────

    @staticmethod
    def _proactive_row(row) -> dict:
        d = dict(row)
        d["action"] = json.loads(d["action"]) if d.get("action") else None
        return d

    async def add_proactive(
        self, *, key: str, rule: str, title: str, detail: str = "",
        severity: str = "info", category: str = "", action: dict | None = None,
    ) -> dict:
        assert self._db is not None, "Store non ouvert"
        now = _now_iso()
        record = {
            "id": uuid.uuid4().hex[:12], "key": key, "rule": rule, "title": title,
            "detail": detail, "severity": severity, "category": category,
            "action": json.dumps(action, ensure_ascii=False) if action else None,
            "status": "active", "proposal_num": None,
            "created_at": now, "updated_at": now, "snooze_until": None,
        }
        await self._db.execute(
            "INSERT INTO proactive (id, key, rule, title, detail, severity, category, action,"
            " status, proposal_num, created_at, updated_at, snooze_until)"
            " VALUES (:id, :key, :rule, :title, :detail, :severity, :category, :action,"
            " :status, :proposal_num, :created_at, :updated_at, :snooze_until)",
            record,
        )
        await self._db.commit()
        record["action"] = action
        return record

    async def get_proactive(self, sug_id: str) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("SELECT * FROM proactive WHERE id = ?", (sug_id,))
        row = await cursor.fetchone()
        return self._proactive_row(row) if row else None

    async def list_proactive(self, statuses: tuple[str, ...] = ("active", "snoozed"), limit: int = 50) -> list[dict]:
        """Suggestions visibles au cockpit (actives + reportées non expirées), récentes d'abord."""
        assert self._db is not None, "Store non ouvert"
        placeholders = ",".join("?" for _ in statuses)
        cursor = await self._db.execute(
            f"SELECT * FROM proactive WHERE status IN ({placeholders})"
            " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (*statuses, limit),
        )
        rows = [self._proactive_row(r) for r in await cursor.fetchall()]
        # Une suggestion « reportée » dont l'heure est passée redevient invisible ici.
        now = _now_iso()
        return [r for r in rows if not (r["status"] == "snoozed" and (r["snooze_until"] or "") <= now)]

    async def find_live_proactive(self, key: str, cutoff_iso: str) -> dict | None:
        """Anti-répétition : une suggestion pour cette `key` encore « vivante »
        (active, reportée non expirée, ou trop récente) supprime un doublon."""
        assert self._db is not None, "Store non ouvert"
        now = _now_iso()
        cursor = await self._db.execute(
            "SELECT * FROM proactive WHERE key = ? AND ("
            " status = 'active'"
            " OR (status = 'snoozed' AND snooze_until > ?)"
            " OR (status IN ('dismissed','acted') AND created_at > ?)"
            ") ORDER BY created_at DESC LIMIT 1",
            (key, now, cutoff_iso),
        )
        row = await cursor.fetchone()
        return self._proactive_row(row) if row else None

    async def update_proactive(self, sug_id: str, **fields) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        allowed = {"status", "proposal_num", "snooze_until"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return await self.get_proactive(sug_id)
        sets["updated_at"] = _now_iso()
        assignments = ", ".join(f"{k} = ?" for k in sets)
        await self._db.execute(
            f"UPDATE proactive SET {assignments} WHERE id = ?", (*sets.values(), sug_id)
        )
        await self._db.commit()
        return await self.get_proactive(sug_id)

    async def add_proactive_mute(self, rule: str) -> None:
        assert self._db is not None, "Store non ouvert"
        await self._db.execute(
            "INSERT OR IGNORE INTO proactive_mutes (rule, created_at) VALUES (?, ?)",
            (rule, _now_iso()),
        )
        await self._db.commit()

    async def remove_proactive_mute(self, rule: str) -> None:
        assert self._db is not None, "Store non ouvert"
        await self._db.execute("DELETE FROM proactive_mutes WHERE rule = ?", (rule,))
        await self._db.commit()

    async def list_proactive_mutes(self) -> list[str]:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("SELECT rule FROM proactive_mutes ORDER BY created_at")
        return [r["rule"] for r in await cursor.fetchall()]

    # ── Scénarios & routines (Phase 8) ───────────────────────────────────────

    @staticmethod
    def _routine_row(row) -> dict:
        d = dict(row)
        d["steps"] = json.loads(d.get("steps") or "[]")
        return d

    async def add_routine(
        self, *, name: str, description: str = "", steps: list[dict] | None = None,
        signature: str = "", source: str = "manuel", status: str = "proposed",
    ) -> dict:
        assert self._db is not None, "Store non ouvert"
        now = _now_iso()
        record = {
            "id": uuid.uuid4().hex[:12], "name": name, "slug": slugify(name),
            "description": description, "steps": json.dumps(steps or [], ensure_ascii=False),
            "signature": signature, "source": source, "status": status,
            "run_count": 0, "created_at": now, "updated_at": now, "last_run_at": None,
        }
        await self._db.execute(
            "INSERT INTO routines (id, name, slug, description, steps, signature, source,"
            " status, run_count, created_at, updated_at, last_run_at)"
            " VALUES (:id, :name, :slug, :description, :steps, :signature, :source,"
            " :status, :run_count, :created_at, :updated_at, :last_run_at)",
            record,
        )
        await self._db.commit()
        return self._routine_row(record)

    async def get_routine(self, routine_id: str) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("SELECT * FROM routines WHERE id = ?", (routine_id,))
        row = await cursor.fetchone()
        return self._routine_row(row) if row else None

    async def list_routines(self, status: str | None = None, limit: int = 100) -> list[dict]:
        assert self._db is not None, "Store non ouvert"
        if status:
            cursor = await self._db.execute(
                "SELECT * FROM routines WHERE status = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM routines ORDER BY status, created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            )
        return [self._routine_row(r) for r in await cursor.fetchall()]

    async def find_active_routine(self, name: str) -> dict | None:
        """Routine ACTIVE dont le nom (insensible casse/accents) correspond — pour le déclenchement."""
        target = slugify(name)
        for r in await self.list_routines("active"):
            if r["slug"] == target or slugify(r["name"]) == target:
                return r
        return None

    async def signature_exists(self, signature: str, statuses: tuple[str, ...] = ("proposed", "active")) -> bool:
        assert self._db is not None, "Store non ouvert"
        if not signature:
            return False
        placeholders = ",".join("?" for _ in statuses)
        cursor = await self._db.execute(
            f"SELECT 1 FROM routines WHERE signature = ? AND status IN ({placeholders}) LIMIT 1",
            (signature, *statuses),
        )
        return await cursor.fetchone() is not None

    async def update_routine(self, routine_id: str, **fields) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        allowed = {"name", "description", "status", "steps"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if "steps" in sets:
            sets["steps"] = json.dumps(sets["steps"], ensure_ascii=False)
        if "name" in sets:
            sets["slug"] = slugify(sets["name"])
        if not sets:
            return await self.get_routine(routine_id)
        sets["updated_at"] = _now_iso()
        assignments = ", ".join(f"{k} = ?" for k in sets)
        await self._db.execute(
            f"UPDATE routines SET {assignments} WHERE id = ?", (*sets.values(), routine_id)
        )
        await self._db.commit()
        return await self.get_routine(routine_id)

    async def routine_ran(self, routine_id: str) -> None:
        assert self._db is not None, "Store non ouvert"
        await self._db.execute(
            "UPDATE routines SET run_count = run_count + 1, last_run_at = ? WHERE id = ?",
            (_now_iso(), routine_id),
        )
        await self._db.commit()

    async def delete_routine(self, routine_id: str) -> bool:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    # ── Minuteurs & rappels (Phase 10) ───────────────────────────────────────

    async def add_reminder(
        self, *, kind: str, label: str, due_at: str, subject: str = "guillaume",
    ) -> dict:
        assert self._db is not None, "Store non ouvert"
        record = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind if kind in ("timer", "reminder") else "reminder",
            "label": label, "due_at": due_at, "status": "active",
            "subject": subject or "guillaume", "created_at": _now_iso(), "fired_at": None,
        }
        await self._db.execute(
            "INSERT INTO reminders (id, kind, label, due_at, status, subject, created_at, fired_at)"
            " VALUES (:id, :kind, :label, :due_at, :status, :subject, :created_at, :fired_at)",
            record,
        )
        await self._db.commit()
        return record

    async def get_reminder(self, reminder_id: str) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_reminders(self, status: str = "active", limit: int = 100) -> list[dict]:
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute(
            "SELECT * FROM reminders WHERE status = ? ORDER BY due_at LIMIT ?", (status, limit)
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def due_reminders(self) -> list[dict]:
        """Rappels actifs dont l'échéance est passée (UTC)."""
        assert self._db is not None, "Store non ouvert"
        cursor = await self._db.execute(
            "SELECT * FROM reminders WHERE status = 'active' AND due_at <= ? ORDER BY due_at",
            (_now_iso(),),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def set_reminder_status(self, reminder_id: str, status: str) -> dict | None:
        assert self._db is not None, "Store non ouvert"
        fired = _now_iso() if status == "fired" else None
        await self._db.execute(
            "UPDATE reminders SET status = ?, fired_at = ? WHERE id = ?",
            (status, fired, reminder_id),
        )
        await self._db.commit()
        return await self.get_reminder(reminder_id)
