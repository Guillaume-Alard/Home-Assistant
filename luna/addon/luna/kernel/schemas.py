"""L0 — les schémas partagés.

Deux familles distinctes, à ne pas confondre :

* **Événements de carte** (`Evt*`) — le contrat public de docs/P1-CONTRATS.md §4.
  Leur `model_dump(mode="json")` produit littéralement le JSON documenté.
* **Événements de cerveau** (`Cerveau*`) — interne, entre le provider Claude (L1)
  et l'orchestrateur (L2). Le cerveau ignore tout des propositions et des
  niveaux d'autonomie.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .autonomy import Niveau


class Modele(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ── Contexte et identité ─────────────────────────────────────────────────


class ContexteRequete(Modele):
    """Posé par l'intégration à partir de `connection.user`, jamais par la carte.

    C'est ce qui empêche un client de se déclarer administrateur.
    """

    ha_user_id: str | None = None
    ha_user_name: str | None = None
    is_admin: bool = False
    profile: str = "unknown"
    client_id: str = "inconnu"
    local: bool = True
    #: D'où vient la demande. Change la **longueur** de la réponse, jamais les
    #: droits : le niveau d'une action reste décidé par le registre de L0 (§9.2).
    source: Literal["texte", "voix"] = "texte"
    #: L'appareil, au sens de C6 : l'identité vit par appareil, pas globalement.
    device: str | None = None


class ProfilActif(Modele):
    id: str
    display_name: str
    confidence: float = 1.0
    #: P1 ne connaît que `ha_user`. P3 ajoutera `presence` et `voice`, P6 `face`.
    signals: dict[str, float] = Field(default_factory=dict)


# ── Home Assistant ───────────────────────────────────────────────────────


class Piece(Modele):
    area_id: str
    nom: str
    entites: dict[str, int] = Field(default_factory=dict)  # domaine → nombre


class EtatEntite(Modele):
    entity_id: str
    nom: str
    etat: str
    piece: str | None = None
    device_class: str | None = None
    unite: str | None = None


class ActionHA(Modele):
    """Un appel de service concret, déjà résolu. C'est ce que l'arbitre note."""

    domain: str
    service: str
    target: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def cle(self) -> str:
        return f"{self.domain}.{self.service}"


# ── Propositions (§9) ────────────────────────────────────────────────────


class Proposition(Modele):
    """Ce que la carte reçoit. Rien de plus : pas de contexte, pas de profil."""

    id: str
    level: int
    title: str
    why: str
    actions: list[ActionHA]
    expires_at: datetime


class PropositionEnAttente(Modele):
    """Ce que l'add-on garde en mémoire, le temps de la décision."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    proposition: Proposition
    contexte: ContexteRequete
    message_id: str | None = None


# ── Événements de carte (contrat §4) ─────────────────────────────────────


class OutilResume(Modele):
    name: str
    label: str
    level: int
    status: Literal["running", "done", "error"]


class Usage(Modele):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class EvtAccepte(Modele):
    event: Literal["accepted"] = "accepted"
    message_id: str
    conversation_id: str


class EvtDelta(Modele):
    event: Literal["delta"] = "delta"
    message_id: str
    text: str


class EvtOutil(Modele):
    event: Literal["tool"] = "tool"
    message_id: str
    name: str
    label: str
    level: int
    status: Literal["running", "done", "error"]


class EvtProposition(Modele):
    event: Literal["proposal"] = "proposal"
    message_id: str
    proposal: Proposition


class EvtTermine(Modele):
    event: Literal["done"] = "done"
    message_id: str
    text: str
    usage: Usage


class EvtErreur(Modele):
    event: Literal["error"] = "error"
    message_id: str | None = None
    code: str
    message: str


class EvtStatut(Modele):
    event: Literal["status"] = "status"
    addon: Literal["online", "offline", "degraded"]
    detail: str | None = None


class MessageDiffuse(Modele):
    """Un message poussé aux cartes ouvertes, sans qu'elles l'aient demandé."""

    id: str
    role: Literal["user", "luna"]
    text: str
    ts: datetime
    conversation_id: str
    #: La carte doit-elle le lire à voix haute ? Non pour un tour déjà parlé
    #: par un satellite : Home Assistant s'en est chargé.
    speak: bool = False


class EvtMessage(Modele):
    """§7 : « à l'écrit et à l'oral, indifféremment ». C'est par là qu'un tour
    de parole traité hors de la carte rejoint le même fil."""

    event: Literal["message"] = "message"
    message: MessageDiffuse


class EvtIdentite(Modele):
    """[P3] Documenté maintenant, jamais émis avant la phase 3."""

    event: Literal["identity"] = "identity"
    profile: ProfilActif


class EvtAlerte(Modele):
    """[P4] Documenté maintenant, jamais émis avant la phase 4."""

    event: Literal["alert"] = "alert"
    alert: dict[str, Any]


EvenementCarte = (
    EvtAccepte
    | EvtDelta
    | EvtOutil
    | EvtProposition
    | EvtTermine
    | EvtErreur
    | EvtStatut
    | EvtMessage
    | EvtIdentite
    | EvtAlerte
)

#: Après l'un de ces deux, plus rien n'arrive sur la souscription (§4).
EVENEMENTS_TERMINAUX = (EvtTermine, EvtErreur)


# ── Événements de cerveau (L1 → L2) ──────────────────────────────────────


class CerveauDelta(Modele):
    text: str


class CerveauOutilDebut(Modele):
    name: str
    entree: dict[str, Any]


class CerveauOutilFin(Modele):
    name: str
    ok: bool


class CerveauTermine(Modele):
    text: str
    usage: Usage


EvenementCerveau = CerveauDelta | CerveauOutilDebut | CerveauOutilFin | CerveauTermine


class ResultatOutil(Modele):
    """Ce que l'arbitre renvoie au cerveau après avoir exécuté — ou refusé."""

    contenu: str
    erreur: bool = False


# ── Identité (P3) ────────────────────────────────────────────────────────


class EmpreinteVocale(Modele):
    """Un vecteur, jamais l'audio dont il vient (H50).

    `model` accompagne l'empreinte : changer de modèle rend les anciennes
    incomparables, et il vaut mieux les invalider que produire des
    ressemblances silencieusement fausses.
    """

    id: str
    profile: str
    vector: list[float]
    model: str
    source: Literal["enrolment", "confirmed"]
    created_at: datetime

    @property
    def dim(self) -> int:
        return len(self.vector)


class EtatIdentite(Modele):
    """Ce que Luna croit savoir d'un appareil, et jusqu'à quand."""

    device: str
    profil: str
    confiance: float
    signals: dict[str, float] = Field(default_factory=dict)
    expires_at: datetime | None = None
    #: Vrai quand l'utilisateur Home Assistant suffit : rien à deviner, rien
    #: à périmer (C6).
    ancre_sur_session: bool = False


class EntreeIdentite(Modele):
    """§C.3 : chaque décision et son score, pour régler les seuils sur des
    données plutôt qu'au jugé."""

    id: str
    ts: datetime
    device: str
    decided: str
    confidence: float
    margin: float
    signals: dict[str, dict[str, float]] = Field(default_factory=dict)
    asked: bool = False


# ── Persistance ──────────────────────────────────────────────────────────


class MessageEnregistre(Modele):
    id: str
    conversation_id: str
    role: Literal["user", "luna", "system"]
    text: str
    ts: datetime
    profile: str | None = None
    client_id: str | None = None
    tools: list[OutilResume] = Field(default_factory=list)


class EntreeJournal(Modele):
    """§9.1 : « journalisée avec sa justification, acceptée ou refusée »."""

    id: str
    ts: datetime
    profile: str
    ha_user_id: str | None
    level: Niveau
    action: ActionHA
    justification: str
    decision: Literal["accepted", "rejected", "expired", "refused_v1", "not_allowed"]
    executed: bool
    error: str | None = None
    message_id: str | None = None


# ── Événements de bus (interne, montant) ─────────────────────────────────


class MaisonConnectee(Modele):
    """Publié par le client HA à chaque changement de connectivité."""

    connectee: bool
    detail: str | None = None
