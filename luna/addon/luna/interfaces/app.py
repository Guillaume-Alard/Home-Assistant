"""L3 — l'amorçage : c'est ici, et nulle part ailleurs, qu'on assemble.

L'injection de dépendances de §3.1 se joue en une trentaine de lignes : les
providers de L1 sont construits, puis passés au moteur de L2 qui ne connaît
d'eux que les `Protocol` de L0.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from aiohttp import web

from ..engine.arbiter import Arbitre
from ..engine.orchestrator import Orchestrateur
from ..kernel.bus import Bus
from ..kernel.settings import Reglages, charger
from ..providers.claude import CerveauClaude
from ..providers.home import ClientMaison
from ..providers.store import MagasinSQLite
from .http import construire_app
from .relay import Relais

log = logging.getLogger("luna")

#: Fréquence de purge des propositions expirées (§9, 5 minutes de validité).
PERIODE_PURGE = 60.0


class Luna:
    def __init__(self, reglages: Reglages) -> None:
        self.reglages = reglages
        self.bus = Bus()

        self.memoire = MagasinSQLite(reglages.chemin_base)
        self.maison = ClientMaison(reglages.url_ha, reglages.jeton_ha, self.bus)
        self.cerveau = CerveauClaude(
            reglages.anthropic_api_key, reglages.modele, reglages.effort
        )

        self.arbitre = Arbitre(self.maison, self.memoire)
        self.orchestrateur = Orchestrateur(
            cerveau=self.cerveau,
            maison=self.maison,
            memoire=self.memoire,
            arbitre=self.arbitre,
            fuseau=reglages.fuseau,
        )
        self.relais = Relais(
            self.orchestrateur,
            self.bus,
            reglages.relay_secret,
            lambda nom, identifiant: reglages.profil_pour(
                nom=nom, identifiant=identifiant
            ),
        )
        self.app = construire_app(
            orchestrateur=self.orchestrateur,
            relais=self.relais,
            maison=self.maison,
            memoire=self.memoire,
            modele=reglages.modele,
        )
        self._purge: asyncio.Task[None] | None = None

    async def demarrer(self) -> None:
        await self.memoire.demarrer()
        await self.maison.demarrer()
        self._purge = asyncio.create_task(self._boucle_purge(), name="luna-purge")

    async def arreter(self) -> None:
        if self._purge is not None:
            self._purge.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._purge
        await self.maison.fermer()
        await self.memoire.fermer()

    async def _boucle_purge(self) -> None:
        while True:
            await asyncio.sleep(PERIODE_PURGE)
            if purgees := self.arbitre.purger():
                log.debug("%s proposition(s) expirée(s) purgée(s)", purgees)


def configurer_journal(niveau: str) -> None:
    logging.basicConfig(
        level=getattr(logging, niveau.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-20s %(message)s",
        datefmt="%H:%M:%S",
    )


async def servir(reglages: Reglages | None = None) -> None:
    reglages = reglages or charger()
    configurer_journal(reglages.journal)
    log.info("Luna démarre — réglages : %s", reglages.secrets_masques())

    if not reglages.anthropic_api_key:
        log.error("Aucune clé API Anthropic : Luna répondra une erreur à chaque tour.")
    if not reglages.relay_secret:
        log.error("Aucun secret de relais : l'intégration ne pourra pas se connecter.")

    luna = Luna(reglages)
    await luna.demarrer()

    coureur = web.AppRunner(luna.app)
    await coureur.setup()
    site = web.TCPSite(coureur, "0.0.0.0", reglages.relay_port)  # noqa: S104
    await site.start()
    log.info("Relais à l'écoute sur le port %s", reglages.relay_port)

    arret = asyncio.Event()
    boucle = asyncio.get_running_loop()
    for signal_ in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            boucle.add_signal_handler(signal_, arret.set)

    try:
        await arret.wait()
        log.info("Signal d'arrêt reçu.")
    finally:
        await coureur.cleanup()
        await luna.arreter()
        log.info("Luna arrêtée.")
