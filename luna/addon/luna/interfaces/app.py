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
from ..engine.health import Gardienne
from ..engine.identity import MoteurIdentite
from ..engine.nightly import EntretienNocturne
from ..engine.observers import ObservateurCoucher, ObservateurSequences
from ..engine.orchestrator import NOMS_PROFILS, Orchestrateur
from ..engine.scheduler import Ordonnanceur
from ..engine.veille import MoteurVeille
from ..kernel.bus import Bus
from ..kernel.identity import INCONNU
from ..kernel.schemas import ChangementEtat, ContexteRequete, MaisonConnectee
from ..kernel.settings import Reglages, charger
from ..providers.claude import CerveauClaude
from ..providers.home import ClientMaison
from ..providers.store import MagasinSQLite
from ..providers.voiceprint import EmpreinteOnnx
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

        self.empreinte = EmpreinteOnnx(reglages.modele_voix)
        self.identite = MoteurIdentite(
            empreinte=self.empreinte,
            maison=self.maison,
            memoire=self.memoire,
            bus=self.bus,
            profils=reglages.profils_declares,
            capteur_presence=reglages.capteur_presence,
            profil_de_session=self._profil_de_session,
            noms=NOMS_PROFILS,
        )
        self.arbitre = Arbitre(self.maison, self.memoire)
        self.orchestrateur = Orchestrateur(
            cerveau=self.cerveau,
            maison=self.maison,
            memoire=self.memoire,
            arbitre=self.arbitre,
            fuseau=reglages.fuseau,
        )

        # ── Habitudes et veille (P4) ─────────────────────────────────────
        # Le moteur émet vers les cartes par une fonction, pas par un import :
        # L2 ne connaît pas le relais. C'est la même injection que partout.
        self.veille = MoteurVeille(
            regles=reglages.veille,
            memoire=self.memoire,
            maison=self.maison,
            arbitre=self.arbitre,
            emettre=self._diffuser,
            annonce=reglages.annonce,
        )
        self.observateurs = [
            ObservateurCoucher(
                self.memoire,
                entite=reglages.observateurs.coucher,
                profil=reglages.observateurs.coucher_profil,
            ),
            ObservateurSequences(
                self.memoire,
                actif=reglages.observateurs.sequences,
                piece_de=self.maison.nom_piece,
            ),
        ]
        # ── Gardienne de l'installation (P5) ─────────────────────────────
        # Elle ne parle jamais directement aux cartes : elle passe par le
        # moteur de veille, donc par la même sourdine, le même score et les
        # mêmes heures de silence qu'une alerte de capteur (E3).
        self.gardienne = Gardienne(
            maison=self.maison,
            memoire=self.memoire,
            cerveau=self.cerveau,
            reglages=reglages.gardienne,
            signaler=self.veille.signaler,
            effacer=self.veille.lever,
        )
        self.bus.abonner(MaisonConnectee, self.gardienne.sur_connexion)

        self.entretien = EntretienNocturne(
            cerveau=self.cerveau,
            memoire=self.memoire,
            plafond=reglages.evenements_par_entretien,
            actif=reglages.entretien_actif,
        )
        self.ordonnanceur = Ordonnanceur(
            veille=self.veille,
            entretien=self.entretien,
            memoire=self.memoire,
            heure=reglages.moment_entretien(),
            gardienne=self.gardienne,
        )
        # H63 : les observateurs et la veille réagissent au bus. Rien
        # n'interroge la maison en boucle.
        self.bus.abonner(ChangementEtat, self._sur_changement)

        self.relais = Relais(
            self.orchestrateur,
            self.bus,
            reglages.relay_secret,
            self._contexte,
            self.identite,
            self.veille,
            self.gardienne,
        )
        self.app = construire_app(
            orchestrateur=self.orchestrateur,
            relais=self.relais,
            maison=self.maison,
            memoire=self.memoire,
            veille=self.veille,
            modele=reglages.modele,
        )
        self._purge: asyncio.Task[None] | None = None

    async def _diffuser(self, evenement: object) -> None:
        """Pousse un événement de veille vers les cartes ouvertes.

        Le relais est construit après le moteur — d'où le passage par une
        méthode plutôt que par une référence directe.
        """
        await self.relais.diffuser(evenement)

    async def _sur_changement(self, evenement: ChangementEtat) -> None:
        """Un changement d'état, distribué à qui le regarde.

        Un observateur qui lève n'empêche pas les autres de voir passer
        l'événement, et surtout n'empêche pas la veille de sortir son alerte :
        c'est elle qui porte la promesse de §5.
        """
        await self.veille.sur_changement(evenement)
        await self.gardienne.sur_changement(evenement)
        for observateur in self.observateurs:
            try:
                await observateur.sur_changement(evenement)
            except Exception:
                log.exception("Observateur %s", type(observateur).__name__)

    def _profil_de_session(self, contexte: ContexteRequete) -> str | None:
        """Le profil que désigne la session Home Assistant, s'il en désigne un.

        C'est la règle de C1 : quand la réponse est là, on ne calcule rien. Un
        appareil partagé — l'iPad du couloir — tombe dans le `None`, et c'est
        seulement là que la voix sert.
        """
        if self.reglages.utilisateur_connu(
            nom=contexte.ha_user_name, identifiant=contexte.ha_user_id
        ):
            return self.reglages.profil_pour(
                nom=contexte.ha_user_name, identifiant=contexte.ha_user_id
            )
        return None

    def _contexte(self, brut: dict[str, object]) -> ContexteRequete:
        """Le profil actif est décidé **ici**, jamais annoncé par le client.

        Deux sources, dans cet ordre : la session Home Assistant (A6), puis
        l'identité de l'appareil (C6). Ce qu'un client prétend n'entre pas dans
        le calcul.
        """
        contexte = ContexteRequete(**brut)  # type: ignore[arg-type]
        etat = self.identite.etat(contexte)
        profil = (
            etat.profil if etat.profil != INCONNU else self.reglages.profil_par_defaut
        )
        return contexte.model_copy(update={"profile": profil})

    async def demarrer(self) -> None:
        await self.memoire.demarrer()
        await self.maison.demarrer()
        if self.empreinte.disponible:
            log.info("Reconnaissance de voix : %s", self.empreinte.nom)
        else:
            log.info(
                "Reconnaissance de voix inactive — %s", self.empreinte.motif_indisponible
            )
        self._purge = asyncio.create_task(self._boucle_purge(), name="luna-purge")
        # Un ouvrant déjà ouvert au démarrage ne doit pas attendre qu'on le
        # referme pour être signalé.
        await self.veille.amorcer()
        await self.ordonnanceur.demarrer()
        if self.veille.entites_surveillees:
            log.info(
                "Veille : %s capteur(s) surveillé(s)",
                len(self.veille.entites_surveillees),
            )
        else:
            log.info("Veille : aucune règle déclarée — rien à surveiller.")
        if self.reglages.gardienne.active:
            log.info(
                "Gardienne : seuil %s min, Loggia %s, journal système %s",
                self.reglages.gardienne.minutes_avant_panne,
                "oui" if self.reglages.gardienne.loggia else "non",
                "oui" if self.reglages.gardienne.journal_systeme else "non",
            )
        if self.reglages.annonce.enceinte:
            log.info(
                "Annonce vocale : %s (%s), niveaux %s",
                self.reglages.annonce.enceinte,
                self.reglages.annonce.moteur,
                ", ".join(self.reglages.annonce.niveaux),
            )

    async def arreter(self) -> None:
        await self.ordonnanceur.arreter()
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
