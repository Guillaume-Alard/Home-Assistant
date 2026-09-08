"""L2 — l'ordonnanceur : trois rendez-vous, et pas un de plus.

* **L'entretien nocturne**, une fois par jour à l'heure dite (D3), suivi de la
  relecture de Loggia (P5).
* **L'expiration des relectures**, dans la foulée (H59).
* **Le réexamen des alertes différées**, toutes les cinq minutes : c'est lui
  qui sort à 7 h l'ampoule oubliée à 3 h (H58).
* **Le tour de la gardienne**, au même battement : les entités tombées depuis
  assez longtemps, les intégrations en erreur, le journal des automatisations
  (P5). Un battement de cinq minutes suffit pour un seuil de trente.

Une horloge, pas une scrutation. H63 interdit d'interroger la maison en boucle ;
regarder l'heure ne coûte rien et ne réveille personne. Ce battement ne parle
ni à Home Assistant ni à SQLite tant qu'il n'a rien à faire.

L'ordonnanceur ne laisse **jamais** une exception remonter : un entretien raté
ne doit pas emporter la boucle, sinon la première nuit sans réseau arrête la
veille jusqu'au prochain redémarrage — et personne ne s'en apercevrait.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta

from ..kernel.contracts import MemoireProvider
from ..kernel.veille import EXPIRATION_RELECTURE
from .health import Gardienne
from .nightly import EntretienNocturne
from .veille import MoteurVeille

log = logging.getLogger("luna.ordonnanceur")

#: Le battement. Cinq minutes suffisent : le seul rendez-vous à l'heure fixe est
#: nocturne, et une alerte différée n'est pas à cinq minutes près.
BATTEMENT = timedelta(minutes=5)


class Ordonnanceur:
    def __init__(
        self,
        *,
        veille: MoteurVeille,
        entretien: EntretienNocturne,
        memoire: MemoireProvider,
        heure: tuple[int, int],
        gardienne: Gardienne | None = None,
        horloge: Callable[[], datetime] | None = None,
        battement: timedelta = BATTEMENT,
    ) -> None:
        self._veille = veille
        self._entretien = entretien
        self._memoire = memoire
        self._gardienne = gardienne
        self._heure, self._minute = heure
        self._maintenant = horloge or (lambda: datetime.now().astimezone())
        self._battement = battement
        self._tache: asyncio.Task[None] | None = None
        #: Le jour du dernier entretien. Sans lui, un battement de cinq minutes
        #: relancerait l'entretien à chaque tour jusqu'à minuit.
        #:
        #: Démarrer l'add-on à 14 h ne doit pas déclencher l'entretien de la
        #: nuit : si le rendez-vous du jour est déjà passé au démarrage, il est
        #: tenu pour honoré, et le prochain aura lieu demain à l'heure dite.
        depart = self._maintenant()
        self._dernier_jour: date | None = (
            depart.date() if depart >= self._rendez_vous(depart) else None
        )

    async def demarrer(self) -> None:
        if self._tache is None:
            self._tache = asyncio.create_task(self._boucle(), name="luna-ordonnanceur")

    async def arreter(self) -> None:
        if self._tache is None:
            return
        self._tache.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._tache
        self._tache = None

    async def _boucle(self) -> None:
        while True:
            await asyncio.sleep(self._battement.total_seconds())
            await self.battre()

    async def battre(self) -> None:
        """Un battement. Public : c'est ce que les tests appellent, sans dormir."""
        maintenant = self._maintenant()
        try:
            await self._veille.reexaminer()
        except Exception:
            log.exception("Réexamen des alertes")

        # Chaque famille est isolée : une gardienne qui trébuche ne doit pas
        # emporter l'entretien nocturne, ni l'inverse.
        if self._gardienne is not None:
            try:
                await self._gardienne.battre()
            except Exception:
                log.exception("Tour de la gardienne")

        if not self._est_l_heure(maintenant):
            return
        self._dernier_jour = maintenant.date()
        try:
            await self._memoire.expirer_relectures(maintenant - EXPIRATION_RELECTURE)
            await self._entretien.passer(maintenant)
        except Exception:
            log.exception("Entretien nocturne")
        if self._gardienne is not None:
            try:
                await self._gardienne.relire_loggia()
            except Exception:
                log.exception("Relecture de Loggia")

    def _rendez_vous(self, jour: datetime) -> datetime:
        return jour.replace(
            hour=self._heure, minute=self._minute, second=0, microsecond=0
        )

    def _est_l_heure(self, maintenant: datetime) -> bool:
        """Vrai une seule fois par jour, au premier battement après l'heure dite.

        C'est `_dernier_jour` qui garantit l'unicité : n'importe quel battement
        postérieur au rendez-vous ferait l'affaire, un seul l'obtiendra. Le
        battement peut donc être aussi lâche qu'on veut sans rater une nuit.
        """
        if self._dernier_jour == maintenant.date():
            return False
        return maintenant >= self._rendez_vous(maintenant)
