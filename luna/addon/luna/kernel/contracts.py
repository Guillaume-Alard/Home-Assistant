"""L0 — les contrats.

Des `Protocol`, pas des classes de base : L2 dépend de ces formes, jamais des
implémentations de L1 (§3.1, « Reçoit les providers par injection »). C'est ce
qui permet à l'orchestrateur d'être testé sans Claude, sans Home Assistant et
sans base de données.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

from .schemas import (
    ActionHA,
    EmpreinteVocale,
    EnregistrementJournal,
    EntreeConfig,
    EntreeIdentite,
    EntreeJournal,
    EtatEntite,
    EvenementCerveau,
    EvenementJournal,
    Fait,
    Incident,
    MessageEnregistre,
    Piece,
    ResultatOutil,
    ScoreSuggestion,
)

#: Le rappel que l'orchestrateur injecte dans le cerveau pour exécuter un outil.
#: Le cerveau ne sait pas ce qu'il y a derrière — arbitre, niveaux, refus.
ExecuteurOutil = Callable[[str, dict[str, Any]], Awaitable[ResultatOutil]]


class CerveauProvider(Protocol):
    """L'API Claude. Streaming, boucle d'outils, plafond de tours."""

    async def repondre(
        self,
        *,
        historique: list[dict[str, Any]],
        contexte: str,
        outils: list[dict[str, Any]],
        executer_outil: ExecuteurOutil,
    ) -> AsyncIterator[EvenementCerveau]: ...

    async def extraire_faits(self, extraits: str) -> list[dict[str, str]]:
        """[P4] L'entretien nocturne : des extraits d'échanges → des faits typés.

        Le seul appel de Luna qui ne vienne pas d'une conversation, et le seul
        qui n'ait aucun outil réel à sa disposition (D3).
        """
        ...

    async def diagnostiquer(self, anomalie: str) -> str:
        """[P5] Une anomalie structurée → une phrase que Guillaume peut lire.

        **Du texte, et rien que du texte** (H71). Le modèle lit ici des chaînes
        que Luna n'a pas écrites — noms d'appareils, messages d'exception,
        titres de cartes. Une phrase injectée dans l'une d'elles peut au pire
        produire une phrase bizarre dans le tiroir ; elle ne peut pas produire
        un appel de service, parce que la sortie n'est jamais autre chose qu'un
        texte affiché.
        """
        ...


class MaisonProvider(Protocol):
    """Home Assistant. Lecture libre ; écriture réservée à l'arbitre."""

    @property
    def connectee(self) -> bool: ...

    async def pieces(self) -> list[Piece]: ...

    async def etats(
        self, *, piece: str | None = None, domaine: str | None = None
    ) -> list[EtatEntite]: ...

    async def resoudre(self, cible: str, domaines: tuple[str, ...]) -> list[str]:
        """« le salon » → les entity_id correspondants."""
        ...

    async def appeler_service(self, action: ActionHA) -> None:
        """⚠️ Réservé à l'arbitre. Vérifié par tests/test_invariants.py."""
        ...

    # ── Gardienne de l'installation (P5) — lecture seule ─────────────────
    #
    # Aucune de ces méthodes n'écrit quoi que ce soit. C'est la moitié
    # structurelle du refus de E1 : le contrat ne propose même pas d'écrire
    # dans la configuration de Home Assistant.

    async def entrees_config(self) -> list[EntreeConfig]:
        """Les intégrations et leur état (`config_entries/get`)."""
        ...

    async def journal_systeme(self) -> list[EnregistrementJournal] | None:
        """`system_log/list`. Demande des droits d'administrateur (H64).

        `None` = je n'ai pas pu lire. Liste vide = rien à signaler. La
        distinction n'est pas cosmétique : une installation saine a un journal
        vide, et la confondre avec un refus d'accès ferait dire à Luna « tout
        va bien » là où elle devrait dire « je n'ai pas regardé » (§8).
        """
        ...

    async def config_loggia(self, url_path: str = "") -> dict[str, Any]:
        """La configuration brute d'un dashboard (`lovelace/config`)."""
        ...

    def integration_de(self, entity_id: str) -> tuple[str, str]:
        """`(entry_id, domaine)` de l'entité, depuis le registre. `("", "")` si
        elle n'appartient à aucune entrée de configuration."""
        ...

    def nom_entite(self, entity_id: str) -> str: ...

    def entites_connues(self) -> set[str]: ...


class EmpreinteProvider(Protocol):
    """Le modèle d'empreinte de locuteur (C2).

    Remplaçable par construction : L2 ne connaît que cette forme, et le nom du
    modèle voyage avec chaque empreinte pour qu'en changer les invalide.
    """

    @property
    def disponible(self) -> bool:
        """Faux si le modèle n'a pas pu être chargé. Luna démarre quand même."""
        ...

    @property
    def nom(self) -> str: ...

    def encoder(self, pcm: bytes) -> list[float]:
        """PCM 16 bits, 16 kHz, mono → empreinte normalisée."""
        ...


class MemoireProvider(Protocol):
    """SQLite. Conversations, messages, journal des actions (§11)."""

    async def demarrer(self) -> None: ...

    async def fermer(self) -> None: ...

    async def conversation_courante(self, profil: str) -> str: ...

    async def ajouter_message(self, message: MessageEnregistre) -> None: ...

    async def historique(
        self, conversation_id: str | None, *, profil: str, limite: int
    ) -> tuple[str, list[MessageEnregistre], bool]: ...

    async def journaliser(self, entree: EntreeJournal) -> None: ...

    async def derniere_action(self) -> datetime | None: ...

    # ── Identité (P3) ────────────────────────────────────────────────────

    async def ajouter_empreinte(self, empreinte: EmpreinteVocale) -> None: ...

    async def empreintes(self, modele: str) -> list[EmpreinteVocale]: ...

    async def oublier_empreintes(self, profil: str) -> int: ...

    async def journaliser_identite(self, entree: EntreeIdentite) -> None: ...

    # ── Habitudes et veille (P4) ─────────────────────────────────────────

    async def enregistrer_evenement(self, evenement: EvenementJournal) -> None:
        """§4, D7 : le journal immuable. `action_log` y est aussi recopié."""
        ...

    async def evenements_depuis(
        self, depuis: datetime | None, *, limite: int
    ) -> list[EvenementJournal]: ...

    async def observer_fait(
        self, fait: Fait, *, event_id: str | None = None
    ) -> tuple[Fait, bool]:
        """Crée le fait, ou renforce celui qui existe déjà.

        §4 : « renforcement sans duplication ». Le second membre du couple dit
        s'il s'agit d'une création — c'est ce qui distingue un fait neuf d'une
        habitude qui se confirme.
        """
        ...

    async def renforcer_fait(
        self, fait_id: str, *, valeur: str, quand: datetime, event_id: str | None = None
    ) -> Fait | None:
        """Renforce un fait dont la valeur *dérive* sans changer de nature.

        Une heure de coucher qui glisse de 23 h 18 à 23 h 22 reste la même
        habitude : la moyenne bouge, le compteur d'observations monte, rien
        n'est supplanté. C'est `observer_fait` qui tranche entre dérive et
        contradiction — ici on applique la dérive.
        """
        ...

    async def faits(
        self, *, statut: str | None = None, profil: str | None = None
    ) -> list[Fait]: ...

    async def fait(self, fait_id: str) -> Fait | None: ...

    async def trancher_fait(self, fait_id: str, *, accepte: bool) -> Fait | None:
        """Sort un fait de la file de relecture (D2). Jamais de suppression."""
        ...

    async def supplanter_fait(self, ancien_id: str, nouveau_id: str) -> None:
        """H62 : `superseded` + une relation `supersedes`. Jamais d'effacement."""
        ...

    async def expirer_relectures(self, avant: datetime) -> int:
        """H59 : une file qu'on n'ouvre plus ne protège plus rien."""
        ...

    async def score_suggestion(self, cle: str) -> ScoreSuggestion | None: ...

    async def enregistrer_score(self, score: ScoreSuggestion) -> None: ...

    # ── Gardienne (P5) ───────────────────────────────────────────────────

    async def ouvrir_incident(self, incident: Incident) -> Incident:
        """Ouvre l'incident, ou rend celui qui est déjà ouvert sous cette clé.

        C'est ce qui permet de dire « depuis mardi » plutôt que « depuis le
        dernier redémarrage de l'add-on » (E8).
        """
        ...

    async def fermer_incident(self, cle: str, quand: datetime) -> Incident | None: ...

    async def incidents_ouverts(self) -> list[Incident]: ...
