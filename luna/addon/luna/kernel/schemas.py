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


class Alerte(Modele):
    """[P4] Ce que la carte reçoit dans son tiroir. Aucun contexte, aucun profil.

    `actions` est la liste des actes que le bouton « Agir » proposera. Ils ne
    court-circuitent rien : ils repassent par l'arbitre, avec leur niveau (D8).
    """

    id: str
    key: str
    level: Literal["info", "warning", "critical"]
    category: str
    title: str
    why: str
    entity_id: str | None = None
    ts: datetime
    actions: list[ActionHA] = Field(default_factory=list)


class EvtAlerte(Modele):
    """[P4] Une alerte de veille arrive dans le tiroir."""

    event: Literal["alert"] = "alert"
    alert: Alerte


class EvtAlerteEffacee(Modele):
    """[P4] La condition a cessé : le tiroir retire la ligne tout seul.

    C'est ce qui distingue une veille d'une boîte de réception : une alerte qui
    n'a plus lieu d'être disparaît sans que personne ait à la ranger.
    """

    event: Literal["alert_cleared"] = "alert_cleared"
    alert_id: str


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
    | EvtAlerteEffacee
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


# ── Habitudes et veille (P4) ─────────────────────────────────────────────


class Fait(Modele):
    """§4 : un fait atomique, jamais supprimé.

    `status` porte tout le sens de D2 : un fait mesuré par un observateur naît
    `active`, un fait déduit d'une conversation naît `needs_review` et attend
    une relecture. Un fait contredit passe `superseded`, un fait refusé à la
    relecture passe `rejected` — jamais effacé, ni l'un ni l'autre (§4).

    `rejected` est distinct de `superseded` à dessein : c'est lui qui permet de
    ne pas reposer la même question la nuit suivante. Les confondre coûterait
    exactement ce que §4 cherche à éviter — une mémoire qui radote.
    """

    id: str
    predicate: str
    value: str
    profile: str | None = None
    entity_id: str | None = None
    #: Porte la demi-vie de D6.
    category: str
    status: Literal["active", "superseded", "needs_review", "rejected"]
    source: Literal["observateur", "modele"]
    created_at: datetime
    last_seen_at: datetime
    observations: int = 1
    #: Pourquoi Luna le croit. Montré tel quel dans la file de relecture.
    why: str = ""

    @property
    def cle(self) -> str:
        return f"{self.predicate}|{self.profile or ''}|{self.entity_id or ''}"


class EvenementJournal(Modele):
    """§4 : « journal immuable de tout ce qui arrive » (D7).

    `action_log` reste, et alimente aussi cette table : rien n'a été migré, rien
    n'a été détruit.
    """

    id: str
    ts: datetime
    kind: Literal["message", "state", "action", "alert", "identity"]
    profile: str | None = None
    entity_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ScoreSuggestion(Modele):
    """§12 : ce que Luna a appris du goût de la maison pour une règle."""

    cle: str
    score: float = 0.5
    rejections: int = 0
    muted_until: datetime | None = None
    updated_at: datetime


class Suggestion(Modele):
    """§12, `GET /suggestions` : ce que Luna propose, sans l'avoir fait."""

    id: str
    key: str
    title: str
    why: str
    score: float
    level: int
    actions: list[ActionHA] = Field(default_factory=list)


class ChangementEtat(Modele):
    """Publié par le client HA à chaque `state_changed` (H63).

    Les observateurs réagissent à cet événement ; rien n'interroge la maison en
    boucle. C'est la seule façon tenable sur un N95 qui fait déjà tourner
    Whisper et une empreinte de locuteur.
    """

    entity_id: str
    ancien: str | None
    nouveau: str | None
    ts: datetime


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
