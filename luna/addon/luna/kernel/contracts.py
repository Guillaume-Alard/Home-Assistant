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
    EntreeJournal,
    EtatEntite,
    EvenementCerveau,
    MessageEnregistre,
    Piece,
    ResultatOutil,
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
