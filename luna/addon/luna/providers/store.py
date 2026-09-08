"""L1 — la mémoire : SQLite.

Le schéma a grandi par ajouts successifs, jamais par destruction : P1 a posé
conversations, messages et `action_log` ; P3 les empreintes et le journal
d'identité ; P4 les faits, leurs observations, leurs relations, le journal
immuable et les scores de suggestion (D7) ; P5 les incidents d'installation.

`action_log` **reste** et est désormais recopié dans `events` : §9.1 impose le
premier, §4 impose le second, les deux vivent ensemble. Rien à migrer.

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
    EmpreinteVocale,
    EntreeIdentite,
    EntreeJournal,
    EvenementJournal,
    Fait,
    Incident,
    MessageEnregistre,
    OutilResume,
    ScoreSuggestion,
)
from .voiceprint import depaqueter, empaqueter

log = logging.getLogger("luna.store")

VERSION_SCHEMA = 4

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

-- ── Identité (P3) ───────────────────────────────────────────────────────
-- L'empreinte, jamais l'audio (H50). `model` accompagne le vecteur : changer
-- de modèle rend les anciennes incomparables, mieux vaut les invalider que
-- produire des ressemblances silencieusement fausses.
CREATE TABLE IF NOT EXISTS voice_prints (
    id         TEXT PRIMARY KEY,
    profile    TEXT NOT NULL,
    vector     BLOB NOT NULL,
    dim        INTEGER NOT NULL,
    model      TEXT NOT NULL,
    source     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prints_profile ON voice_prints(profile, model);

-- Chaque décision et son score : c'est là-dedans qu'on règle les seuils de
-- H48, sur de vraies voix plutôt qu'au jugé.
CREATE TABLE IF NOT EXISTS identity_log (
    id           TEXT PRIMARY KEY,
    ts           TEXT NOT NULL,
    device       TEXT NOT NULL,
    decided      TEXT NOT NULL,
    confidence   REAL NOT NULL,
    margin       REAL NOT NULL,
    signals_json TEXT NOT NULL,
    asked        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_identity_log_ts ON identity_log(ts);

-- ── Habitudes et veille (P4) ────────────────────────────────────────────
-- §4 : « journal immuable de tout ce qui arrive ».
CREATE TABLE IF NOT EXISTS events (
    id        TEXT PRIMARY KEY,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    profile   TEXT,
    entity_id TEXT,
    payload   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts   ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind, ts);

-- §4 : les faits atomiques. Jamais supprimés — au pire `superseded`.
CREATE TABLE IF NOT EXISTS facts (
    id           TEXT PRIMARY KEY,
    predicate    TEXT NOT NULL,
    value        TEXT NOT NULL,
    profile      TEXT,
    entity_id    TEXT,
    category     TEXT NOT NULL,
    status       TEXT NOT NULL,
    source       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    observations INTEGER NOT NULL DEFAULT 1,
    why          TEXT NOT NULL DEFAULT ''
);
-- C'est cet index, et pas la discipline de l'appelant, qui applique
-- « un fait ré-observé est renforcé, jamais dupliqué » (§4).
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_cle
    ON facts(predicate, IFNULL(profile,''), IFNULL(entity_id,''))
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_facts_statut ON facts(status, last_seen_at);

-- §4 : « renforcement sans duplication » — chaque renfort laisse sa trace.
CREATE TABLE IF NOT EXISTS fact_observations (
    id       TEXT PRIMARY KEY,
    fact_id  TEXT NOT NULL REFERENCES facts(id),
    ts       TEXT NOT NULL,
    source   TEXT NOT NULL,
    event_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_fact_obs ON fact_observations(fact_id, ts);

-- §4 : supersedes | contradicts | supports
CREATE TABLE IF NOT EXISTS fact_relations (
    id      TEXT PRIMARY KEY,
    de_id   TEXT NOT NULL REFERENCES facts(id),
    vers_id TEXT NOT NULL REFERENCES facts(id),
    genre   TEXT NOT NULL,
    ts      TEXT NOT NULL
);

-- §12 : la boucle de feedback, par clé de suggestion et pas par occurrence.
CREATE TABLE IF NOT EXISTS suggestion_scores (
    cle         TEXT PRIMARY KEY,
    score       REAL NOT NULL DEFAULT 0.5,
    rejections  INTEGER NOT NULL DEFAULT 0,
    muted_until TEXT,
    updated_at  TEXT NOT NULL
);

-- ── Gardienne de l'installation (P5) ────────────────────────────────────
-- Une anomalie n'est pas un fait au sens de §4 : ce n'est pas une vérité
-- durable sur la maisonnée, c'est un épisode. Elle a un début, une fin, et
-- elle doit survivre à un redémarrage de l'add-on — sinon Luna dirait
-- « depuis 2 minutes » d'une panne vieille de trois jours.
CREATE TABLE IF NOT EXISTS health_incidents (
    id        TEXT PRIMARY KEY,
    cle       TEXT NOT NULL,
    famille   TEXT NOT NULL,
    sujet     TEXT NOT NULL,
    ouvert_le TEXT NOT NULL,
    ferme_le  TEXT,
    details   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_cle ON health_incidents(cle, ouvert_le);
-- Un seul incident ouvert par clé : c'est l'index qui l'applique, pas la
-- discipline de l'appelant.
CREATE UNIQUE INDEX IF NOT EXISTS idx_incidents_ouvert
    ON health_incidents(cle) WHERE ferme_le IS NULL;
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
            return
        # Les tables sont créées en `IF NOT EXISTS` à chaque démarrage : passer
        # de la v1 à la v2 n'a donc rien détruit ni rien perdu. Une migration
        # qui transformerait des données existantes viendrait ici, numérotée.
        if int(ligne["version"]) < VERSION_SCHEMA:
            log.info("Schéma migré de v%s à v%s", ligne["version"], VERSION_SCHEMA)
            await self._co.execute(
                "UPDATE schema_version SET version = ?", (VERSION_SCHEMA,)
            )

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
        # D7 : le journal immuable reçoit aussi les messages. C'est la matière
        # de l'entretien nocturne — le seul endroit d'où partent des extraits
        # de conversation vers l'API (H60).
        await self.enregistrer_evenement(
            EvenementJournal(
                id=nouvel_id("e"),
                ts=message.ts,
                kind="message",
                profile=message.profile,
                entity_id=None,
                payload={
                    "role": message.role,
                    "text": message.text,
                    "conversation_id": message.conversation_id,
                },
            )
        )

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
        # D7 : `action_log` reste (§9.1) et alimente aussi `events` (§4). Les
        # deux journaux coexistent, aucun n'a été migré dans l'autre.
        await self.enregistrer_evenement(
            EvenementJournal(
                id=nouvel_id("e"),
                ts=entree.ts,
                kind="action",
                profile=entree.profile,
                entity_id=None,
                payload={
                    "service": entree.action.cle,
                    "target": entree.action.target,
                    "level": int(entree.level),
                    "decision": entree.decision,
                    "executed": entree.executed,
                    "justification": entree.justification,
                },
            )
        )
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

    # ── Identité (P3) ────────────────────────────────────────────────────

    async def ajouter_empreinte(self, empreinte: EmpreinteVocale) -> None:
        await self._co.execute(
            "INSERT INTO voice_prints (id, profile, vector, dim, model, source, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                empreinte.id,
                empreinte.profile,
                empaqueter(empreinte.vector),
                empreinte.dim,
                empreinte.model,
                empreinte.source,
                empreinte.created_at.isoformat(),
            ),
        )
        await self._co.commit()

    async def empreintes(self, modele: str) -> list[EmpreinteVocale]:
        """Toutes les empreintes du modèle courant.

        Filtrer sur le modèle est essentiel : comparer un vecteur d'un modèle
        à celui d'un autre donne un nombre, mais pas une ressemblance.
        """
        curseur = await self._co.execute(
            "SELECT * FROM voice_prints WHERE model = ? ORDER BY created_at",
            (modele,),
        )
        return [
            EmpreinteVocale(
                id=ligne["id"],
                profile=ligne["profile"],
                vector=depaqueter(ligne["vector"]),
                model=ligne["model"],
                source=ligne["source"],
                created_at=datetime.fromisoformat(ligne["created_at"]),
            )
            for ligne in await curseur.fetchall()
        ]

    async def oublier_empreintes(self, profil: str) -> int:
        curseur = await self._co.execute(
            "DELETE FROM voice_prints WHERE profile = ?", (profil,)
        )
        await self._co.commit()
        log.info("Empreintes de %s effacées : %s", profil, curseur.rowcount)
        return int(curseur.rowcount or 0)

    async def journaliser_identite(self, entree: EntreeIdentite) -> None:
        await self._co.execute(
            "INSERT INTO identity_log (id, ts, device, decided, confidence, "
            "margin, signals_json, asked) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entree.id,
                entree.ts.isoformat(),
                entree.device,
                entree.decided,
                entree.confidence,
                entree.margin,
                json.dumps(entree.signals, ensure_ascii=False),
                int(entree.asked),
            ),
        )
        await self._co.commit()

    async def decisions_identite(self, limite: int = 50) -> list[EntreeIdentite]:
        curseur = await self._co.execute(
            "SELECT * FROM identity_log ORDER BY ts DESC LIMIT ?", (limite,)
        )
        return [
            EntreeIdentite(
                id=ligne["id"],
                ts=datetime.fromisoformat(ligne["ts"]),
                device=ligne["device"],
                decided=ligne["decided"],
                confidence=ligne["confidence"],
                margin=ligne["margin"],
                signals=json.loads(ligne["signals_json"]),
                asked=bool(ligne["asked"]),
            )
            for ligne in await curseur.fetchall()
        ]

    # ── Habitudes et veille (P4) ─────────────────────────────────────────

    async def enregistrer_evenement(self, evenement: EvenementJournal) -> None:
        """§4, D7 : le journal immuable. Rien n'en sort jamais que par lecture."""
        await self._co.execute(
            "INSERT INTO events (id, ts, kind, profile, entity_id, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                evenement.id,
                evenement.ts.isoformat(),
                evenement.kind,
                evenement.profile,
                evenement.entity_id,
                json.dumps(evenement.payload, ensure_ascii=False),
            ),
        )
        await self._co.commit()

    async def evenements_depuis(
        self, depuis: datetime | None, *, limite: int
    ) -> list[EvenementJournal]:
        """Les `limite` événements les plus récents, rendus chronologiquement.

        `depuis` borne l'entretien nocturne à ce qui est neuf (D3) ; la limite
        le borne tout court (H56).
        """
        if depuis is None:
            curseur = await self._co.execute(
                "SELECT * FROM events ORDER BY ts DESC, id DESC LIMIT ?", (limite,)
            )
        else:
            curseur = await self._co.execute(
                "SELECT * FROM events WHERE ts > ? ORDER BY ts DESC, id DESC LIMIT ?",
                (depuis.isoformat(), limite),
            )
        return [
            self._vers_evenement(ligne) for ligne in reversed(await curseur.fetchall())
        ]

    @staticmethod
    def _vers_evenement(ligne: aiosqlite.Row) -> EvenementJournal:
        return EvenementJournal(
            id=ligne["id"],
            ts=datetime.fromisoformat(ligne["ts"]),
            kind=ligne["kind"],
            profile=ligne["profile"],
            entity_id=ligne["entity_id"],
            payload=json.loads(ligne["payload"]),
        )

    # ── Faits ────────────────────────────────────────────────────────────

    async def observer_fait(
        self, fait: Fait, *, event_id: str | None = None
    ) -> tuple[Fait, bool]:
        """Crée le fait, ou renforce celui qui existe (§4, « sans duplication »).

        Trois cas, et un seul écrit une ligne neuve dans `facts` :

        * **même clé, même valeur** → `observations + 1`, `last_seen_at` avancé.
          C'est un vingtième soir de coucher, pas un vingtième fait.
        * **même clé, valeur différente** → l'ancien passe `superseded`, une
          relation `supersedes` est écrite, le neuf devient actif (H62).
        * **rien de comparable** → création.

        Un fait à relire (`needs_review`) ne supplante rien : il attend. Et il
        n'est pas recréé si un jumeau attend déjà, ou si Guillaume l'a refusé —
        sinon l'entretien nocturne repose la même question toutes les nuits.
        """
        if fait.status == "needs_review":
            return await self._deposer_relecture(fait, event_id)

        courant = await self._fait_actif(fait.predicate, fait.profile, fait.entity_id)
        if courant is None:
            await self._inserer_fait(fait)
            await self._noter_observation(
                fait.id, fait.last_seen_at, fait.source, event_id
            )
            return fait, True

        if courant.value == fait.value:
            renforce = courant.model_copy(
                update={
                    "observations": courant.observations + 1,
                    "last_seen_at": fait.last_seen_at,
                }
            )
            await self._co.execute(
                "UPDATE facts SET observations = ?, last_seen_at = ? WHERE id = ?",
                (renforce.observations, renforce.last_seen_at.isoformat(), courant.id),
            )
            await self._noter_observation(
                courant.id, fait.last_seen_at, fait.source, event_id
            )
            await self._co.commit()
            return renforce, False

        # L'ordre compte : l'index unique partiel n'admet qu'un seul fait
        # `active` par clé. Insérer avant de démettre le précédent le fait
        # échouer — c'est exactement ce que l'index est là pour garantir.
        await self._demettre(courant.id)
        await self._inserer_fait(fait)
        await self._lier(fait.id, courant.id, "supersedes", fait.last_seen_at)
        await self._noter_observation(fait.id, fait.last_seen_at, fait.source, event_id)
        return fait, True

    async def _deposer_relecture(
        self, fait: Fait, event_id: str | None
    ) -> tuple[Fait, bool]:
        curseur = await self._co.execute(
            "SELECT * FROM facts WHERE predicate = ? AND IFNULL(profile,'') = ? "
            "AND IFNULL(entity_id,'') = ? AND value = ? "
            "AND status IN ('needs_review', 'rejected') "
            "ORDER BY created_at DESC LIMIT 1",
            (fait.predicate, fait.profile or "", fait.entity_id or "", fait.value),
        )
        if (ligne := await curseur.fetchone()) is not None:
            return self._vers_fait(ligne), False
        await self._inserer_fait(fait)
        await self._noter_observation(fait.id, fait.last_seen_at, fait.source, event_id)
        return fait, True

    async def _fait_actif(
        self, predicat: str, profil: str | None, entite: str | None
    ) -> Fait | None:
        curseur = await self._co.execute(
            "SELECT * FROM facts WHERE predicate = ? AND IFNULL(profile,'') = ? "
            "AND IFNULL(entity_id,'') = ? AND status = 'active'",
            (predicat, profil or "", entite or ""),
        )
        ligne = await curseur.fetchone()
        return self._vers_fait(ligne) if ligne is not None else None

    async def _inserer_fait(self, fait: Fait) -> None:
        await self._co.execute(
            "INSERT INTO facts (id, predicate, value, profile, entity_id, category, "
            "status, source, created_at, last_seen_at, observations, why) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fait.id,
                fait.predicate,
                fait.value,
                fait.profile,
                fait.entity_id,
                fait.category,
                fait.status,
                fait.source,
                fait.created_at.isoformat(),
                fait.last_seen_at.isoformat(),
                fait.observations,
                fait.why,
            ),
        )
        await self._co.commit()

    async def _noter_observation(
        self, fait_id: str, quand: datetime, source: str, event_id: str | None
    ) -> None:
        await self._co.execute(
            "INSERT INTO fact_observations (id, fact_id, ts, source, event_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (nouvel_id("o"), fait_id, quand.isoformat(), source, event_id),
        )
        await self._co.commit()

    async def _demettre(self, fait_id: str) -> None:
        await self._co.execute(
            "UPDATE facts SET status = 'superseded' WHERE id = ?", (fait_id,)
        )
        await self._co.commit()

    async def _lier(self, de_id: str, vers_id: str, genre: str, quand: datetime) -> None:
        await self._co.execute(
            "INSERT INTO fact_relations (id, de_id, vers_id, genre, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (nouvel_id("r"), de_id, vers_id, genre, quand.isoformat()),
        )
        await self._co.commit()

    async def supplanter_fait(self, ancien_id: str, nouveau_id: str) -> None:
        quand = datetime.now().astimezone()
        await self._demettre(ancien_id)
        await self._lier(nouveau_id, ancien_id, "supersedes", quand)

    async def renforcer_fait(
        self, fait_id: str, *, valeur: str, quand: datetime, event_id: str | None = None
    ) -> Fait | None:
        """Dérive de valeur, sans supplantation. Voir le contrat de L0."""
        fait = await self.fait(fait_id)
        if fait is None:
            return None
        await self._co.execute(
            "UPDATE facts SET value = ?, last_seen_at = ?, "
            "observations = observations + 1 WHERE id = ?",
            (valeur, quand.isoformat(), fait_id),
        )
        await self._noter_observation(fait_id, quand, fait.source, event_id)
        await self._co.commit()
        return fait.model_copy(
            update={
                "value": valeur,
                "last_seen_at": quand,
                "observations": fait.observations + 1,
            }
        )

    async def faits(
        self, *, statut: str | None = None, profil: str | None = None
    ) -> list[Fait]:
        requete = "SELECT * FROM facts"
        clauses: list[str] = []
        parametres: list[object] = []
        if statut:
            clauses.append("status = ?")
            parametres.append(statut)
        if profil:
            clauses.append("profile = ?")
            parametres.append(profil)
        if clauses:
            requete += " WHERE " + " AND ".join(clauses)
        requete += " ORDER BY last_seen_at DESC"
        curseur = await self._co.execute(requete, tuple(parametres))
        return [self._vers_fait(ligne) for ligne in await curseur.fetchall()]

    async def fait(self, fait_id: str) -> Fait | None:
        curseur = await self._co.execute("SELECT * FROM facts WHERE id = ?", (fait_id,))
        ligne = await curseur.fetchone()
        return self._vers_fait(ligne) if ligne is not None else None

    async def trancher_fait(self, fait_id: str, *, accepte: bool) -> Fait | None:
        """Sort un fait de la file de relecture (D2).

        Accepté, il entre en vigueur — et supplante celui qu'il contredit, s'il
        y en a un. Refusé, il devient `rejected` : ni actif, ni en attente, et
        surtout **pas supprimé** (§4). C'est ce statut qui empêche l'entretien
        de reposer la même question la nuit suivante.
        """
        fait = await self.fait(fait_id)
        if fait is None or fait.status != "needs_review":
            return None
        if not accepte:
            await self._co.execute(
                "UPDATE facts SET status = 'rejected' WHERE id = ?", (fait_id,)
            )
            await self._co.commit()
            return fait.model_copy(update={"status": "rejected"})

        courant = await self._fait_actif(fait.predicate, fait.profile, fait.entity_id)
        if courant is not None:
            await self.supplanter_fait(courant.id, fait.id)
        await self._co.execute(
            "UPDATE facts SET status = 'active' WHERE id = ?", (fait_id,)
        )
        await self._co.commit()
        return fait.model_copy(update={"status": "active"})

    async def expirer_relectures(self, avant: datetime) -> int:
        """H59 : une file qu'on n'ouvre plus ne protège plus rien."""
        curseur = await self._co.execute(
            "UPDATE facts SET status = 'rejected' "
            "WHERE status = 'needs_review' AND created_at < ?",
            (avant.isoformat(),),
        )
        await self._co.commit()
        expires = int(curseur.rowcount or 0)
        if expires:
            log.info("%s fait(s) à relire expiré(s)", expires)
        return expires

    @staticmethod
    def _vers_fait(ligne: aiosqlite.Row) -> Fait:
        return Fait(
            id=ligne["id"],
            predicate=ligne["predicate"],
            value=ligne["value"],
            profile=ligne["profile"],
            entity_id=ligne["entity_id"],
            category=ligne["category"],
            status=ligne["status"],
            source=ligne["source"],
            created_at=datetime.fromisoformat(ligne["created_at"]),
            last_seen_at=datetime.fromisoformat(ligne["last_seen_at"]),
            observations=int(ligne["observations"]),
            why=ligne["why"] or "",
        )

    # ── Scores de suggestion (§12) ───────────────────────────────────────

    async def score_suggestion(self, cle: str) -> ScoreSuggestion | None:
        curseur = await self._co.execute(
            "SELECT * FROM suggestion_scores WHERE cle = ?", (cle,)
        )
        ligne = await curseur.fetchone()
        if ligne is None:
            return None
        return ScoreSuggestion(
            cle=ligne["cle"],
            score=float(ligne["score"]),
            rejections=int(ligne["rejections"]),
            muted_until=datetime.fromisoformat(ligne["muted_until"])
            if ligne["muted_until"]
            else None,
            updated_at=datetime.fromisoformat(ligne["updated_at"]),
        )

    async def enregistrer_score(self, score: ScoreSuggestion) -> None:
        await self._co.execute(
            "INSERT INTO suggestion_scores (cle, score, rejections, muted_until, "
            "updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(cle) DO UPDATE SET "
            "score = excluded.score, rejections = excluded.rejections, "
            "muted_until = excluded.muted_until, updated_at = excluded.updated_at",
            (
                score.cle,
                score.score,
                score.rejections,
                score.muted_until.isoformat() if score.muted_until else None,
                score.updated_at.isoformat(),
            ),
        )
        await self._co.commit()

    # ── Incidents d'installation (P5) ────────────────────────────────────

    async def ouvrir_incident(self, incident: Incident) -> Incident:
        """Ouvre l'incident, ou rend celui qui est déjà ouvert sous cette clé.

        Le second cas est le plus important : après un redémarrage de l'add-on,
        une panne toujours en cours doit garder sa date d'ouverture. C'est ce
        qui permet de dire « depuis mardi » (E8).
        """
        if (courant := await self._incident_ouvert(incident.cle)) is not None:
            return courant
        await self._co.execute(
            "INSERT INTO health_incidents (id, cle, famille, sujet, ouvert_le, "
            "ferme_le, details) VALUES (?, ?, ?, ?, ?, NULL, ?)",
            (
                incident.id,
                incident.cle,
                incident.famille,
                incident.sujet,
                incident.ouvert_le.isoformat(),
                json.dumps(incident.details, ensure_ascii=False),
            ),
        )
        await self._co.commit()
        log.info("Incident ouvert : %s", incident.cle)
        return incident

    async def fermer_incident(self, cle: str, quand: datetime) -> Incident | None:
        incident = await self._incident_ouvert(cle)
        if incident is None:
            return None
        await self._co.execute(
            "UPDATE health_incidents SET ferme_le = ? WHERE id = ?",
            (quand.isoformat(), incident.id),
        )
        await self._co.commit()
        log.info("Incident refermé : %s", cle)
        return incident.model_copy(update={"ferme_le": quand})

    async def incidents_ouverts(self) -> list[Incident]:
        curseur = await self._co.execute(
            "SELECT * FROM health_incidents WHERE ferme_le IS NULL ORDER BY ouvert_le"
        )
        return [self._vers_incident(ligne) for ligne in await curseur.fetchall()]

    async def _incident_ouvert(self, cle: str) -> Incident | None:
        curseur = await self._co.execute(
            "SELECT * FROM health_incidents WHERE cle = ? AND ferme_le IS NULL",
            (cle,),
        )
        ligne = await curseur.fetchone()
        return self._vers_incident(ligne) if ligne is not None else None

    @staticmethod
    def _vers_incident(ligne: aiosqlite.Row) -> Incident:
        return Incident(
            id=ligne["id"],
            cle=ligne["cle"],
            famille=ligne["famille"],
            sujet=ligne["sujet"],
            ouvert_le=datetime.fromisoformat(ligne["ouvert_le"]),
            ferme_le=datetime.fromisoformat(ligne["ferme_le"])
            if ligne["ferme_le"]
            else None,
            details=json.loads(ligne["details"]),
        )
