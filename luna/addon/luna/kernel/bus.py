"""L0 — le bus d'événements (§3.1, « communication montante »).

Une couche basse ne rappelle **jamais** une couche haute par import : elle
publie. Le bus est typé sur la classe pydantic de l'événement — s'abonner à une
classe qui n'existe pas ne compile pas, et publier un événement auquel personne
ne s'est abonné ne casse rien.

Pub/sub asyncio en mémoire, sans persistance : contrainte N95 (H16). Un
événement publié pendant que l'add-on redémarre est perdu, et c'est assumé —
rien de critique ne transite par ici.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel

log = logging.getLogger("luna.bus")

E = TypeVar("E", bound=BaseModel)

Abonne = Callable[[BaseModel], Awaitable[None]]


class Bus:
    def __init__(self) -> None:
        self._abonnes: dict[type[BaseModel], list[Abonne]] = {}

    def abonner(
        self, type_evenement: type[E], abonne: Callable[[E], Awaitable[None]]
    ) -> Callable[[], None]:
        """Retourne la fonction de désabonnement. L'appeler, sinon ça fuit."""
        liste = self._abonnes.setdefault(type_evenement, [])
        liste.append(abonne)  # type: ignore[arg-type]

        def desabonner() -> None:
            with_liste = self._abonnes.get(type_evenement)
            if with_liste and abonne in with_liste:
                with_liste.remove(abonne)  # type: ignore[arg-type]

        return desabonner

    async def publier(self, evenement: BaseModel) -> None:
        """Livre à tous les abonnés du type exact.

        Un abonné qui lève n'empêche pas les autres d'être servis : le bus ne
        doit jamais faire tomber celui qui publie.
        """
        abonnes = list(self._abonnes.get(type(evenement), ()))
        if not abonnes:
            return
        resultats = await asyncio.gather(
            *(abonne(evenement) for abonne in abonnes), return_exceptions=True
        )
        for resultat in resultats:
            if isinstance(resultat, BaseException):
                log.exception(
                    "Un abonné à %s a levé", type(evenement).__name__, exc_info=resultat
                )

    def nombre_abonnes(self, type_evenement: type[BaseModel]) -> int:
        return len(self._abonnes.get(type_evenement, ()))
