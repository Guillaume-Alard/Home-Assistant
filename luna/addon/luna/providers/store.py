"""L1 — la mémoire de P1 : SQLite.

Trois tables et pas une de plus (décision A7) : le modèle à faits atomiques de
F4 arrive en P4, sans migration destructive. `action_log` alimentera `events`
le moment venu.

Base unique sur Nova (§4, F4 : « Aucune donnée d'habitude ne quitte la maison »).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from ..kernel.autonomy import Niveau
from ..kernel.ids import nouvel_id
from ..kernel.schemas import (
    ActionHA,
    EntreeJournal,
    MessageEnregistre,
    OutilResume,
)

log = logging.getLogger("luna.store")

VERSION_SCHEMA = 1

#: Au-delà, on ouvre une nouvelle conversation plutôt que de reprendre le fil.
FENETRE_CONVERSATION = timedelta(hours=12)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    profile     TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    last_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,
    text            TEXT NOT NULL,
    ts              TEXT NOT NULL,
    profile         TEXT,
    client_id       TEXT,
    tools_json      TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, ts);

CREATE TABLE IF NOT EXISTS action_log (
    id            TEXT PRIMARY KEY,
    ts            TEXT NOT NULL,
    profile       TEXT NOT NULL,
    ha_user_id    TEXT,
    level         INTEGER NOT NULL,
    domain        TEXT NOT NULL,
    service       TEXT NOT NULL,
    target_json   TEXT NOT NULL,
    data_json     TEXT,
    justification TEXT NOT NULL,
    decision      TEXT NOT NULL,
    executed      INTEGER NOT NULL,
    error         TEXT,
    message_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_action_log_ts ON action_log(ts);
"""


class MagasinSQLite:
    def __init__(self, chemin: Path) -> None:
        self._chemin = chemin
        self._db: aiosqlite.Connection | None = None

    # ── Cycle de vie ─────────────────────────────────────────────────────

    async def demarrer(self) -> None:
        self._chemin.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._chemin)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        await self._appliquer_version()
        await self._db.commit()
        log.info("Base ouverte : %s (schéma v%s)", self._chemin, VERSION_SCHEMA)

    async def fermer(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _appliquer_version(self) -> None:
        curseur = await self._co.execute("SELECT version FROM schema_version")
        ligne = await curseur.fetchone()
        if ligne is None:
            await self._co.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (VERSION_SCHEMA,)
            )
        # P4 branchera ici les migrations numérotées ; en P1 il n'y en a aucune.

    @property
    def _co(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Magasin non démarré — appeler demarrer() d'abord.")
        return self._db

    # ── Conversations et messages ────────────────────────────────────────

    async def conversation_courante(self, profil: str) -> str:
        """Reprend le fil du profil, ou en ouvre un neuf après 12 h de silence."""
        curseur = await self._co.execute(
            "SELECT id, last_at FROM conversations WHERE profile = ? "
            "ORDER BY last_at DESC LIMIT 1",
            (profil,),
        )
        ligne = await curseur.fetchone()
        maintenant = datetime.now().astimezone()
        if ligne is not None:
            dernier = datetime.fromisoformat(ligne["last_at"])
            if maintenant - dernier < FENETRE_CONVERSATION:
                return str(ligne["id"])

        identifiant = nouvel_id("c")
        horodatage = maintenant.isoformat()
        await self._co.execute(
            "INSERT INTO conversations (id, profile, started_at, last_at) "
            "VALUES (?, ?, ?, ?)",
            (identifiant, profil, horodatage, horodatage),
        )
        await self._co.commit()
        return identifiant

    async def ajouter_message(self, message: MessageEnregistre) -> None:
        await self._co.execute(
            "INSERT INTO messages (id, conversation_id, role, text, ts, profile, "
            "client_id, tools_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message.id,
                message.conversation_id,
                message.role,
                message.text,
                message.ts.isoformat(),
                message.profile,
                message.client_id,
                json.dumps([o.model_dump() for o in message.tools], ensure_ascii=False),
            ),
        )
        await self._co.execute(
            "UPDATE conversations SET last_at = ? WHERE id = ?",
            (message.ts.isoformat(), message.conversation_id),
        )
        await self._co.commit()

    async def historique(
        self, conversation_id: str | None, *, profil: str, limite: int
    ) -> tuple[str, list[MessageEnregistre], bool]:
        """Les `limite` derniers messages, rendus dans l'ordre chronologique.

        `conversation_id` absent → la conversation la plus récente du profil.
        C'est ce qui permet à la carte de retrouver son fil après rechargement.
        """
        identifiant = conversation_id or await self.conversation_courante(profil)
        curseur = await self._co.execute(
            "SELECT * FROM messages WHERE conversation_id = ? "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (identifiant, limite + 1),
        )
        lignes = await curseur.fetchall()
        encore = len(lignes) > limite
        messages = [self._vers_message(ligne) for ligne in reversed(lignes[:limite])]
        return identifiant, messages, encore

    @staticmethod
    def _vers_message(ligne: aiosqlite.Row) -> MessageEnregistre:
        outils = [OutilResume(**o) for o in json.loads(ligne["tools_json"] or "[]")]
        return MessageEnregistre(
            id=ligne["id"],
            conversation_id=ligne["conversation_id"],
            role=ligne["role"],
            text=ligne["text"],
            ts=datetime.fromisoformat(ligne["ts"]),
            profile=ligne["profile"],
            client_id=ligne["client_id"],
            tools=outils,
        )

    # ── Journal des actions (§9.1) ───────────────────────────────────────

    async def journaliser(self, entree: EntreeJournal) -> None:
        await self._co.execute(
            "INSERT INTO action_log (id, ts, profile, ha_user_id, level, domain, "
            "service, target_json, data_json, justification, decision, executed, "
            "error, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entree.id,
                entree.ts.isoformat(),
                entree.profile,
                entree.ha_user_id,
                int(entree.level),
                entree.action.domain,
                entree.action.service,
                json.dumps(entree.action.target, ensure_ascii=False),
                json.dumps(entree.action.data, ensure_ascii=False),
                entree.justification,
                entree.decision,
                int(entree.executed),
                entree.error,
                entree.message_id,
            ),
        )
        await self._co.commit()
        log.info(
            "Journal : %s %s niveau %s → %s",
            entree.action.cle,
            entree.profile,
            int(entree.level),
            entree.decision,
        )

    async def derniere_action(self) -> datetime | None:
        curseur = await self._co.execute("SELECT MAX(ts) AS ts FROM action_log")
        ligne = await curseur.fetchone()
        if ligne is None or ligne["ts"] is None:
            return None
        return datetime.fromisoformat(ligne["ts"])

    async def actions(self, limite: int = 50) -> list[EntreeJournal]:
        """Relecture du journal. Sert au débogage et à la recette de P1."""
        curseur = await self._co.execute(
            "SELECT * FROM action_log ORDER BY ts DESC LIMIT ?", (limite,)
        )
        return [
            EntreeJournal(
                id=ligne["id"],
                ts=datetime.fromisoformat(ligne["ts"]),
                profile=ligne["profile"],
                ha_user_id=ligne["ha_user_id"],
                level=Niveau(ligne["level"]),
                action=ActionHA(
                    domain=ligne["domain"],
                    service=ligne["service"],
                    target=json.loads(ligne["target_json"]),
                    data=json.loads(ligne["data_json"] or "{}"),
                ),
                justification=ligne["justification"],
                decision=ligne["decision"],
                executed=bool(ligne["executed"]),
                error=ligne["error"],
                message_id=ligne["message_id"],
            )
            for ligne in await curseur.fetchall()
        ]
