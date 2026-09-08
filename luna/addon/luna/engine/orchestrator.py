"""L2 — l'orchestrateur : un échange, du premier delta au `done`.

Il ne parle jamais directement à Claude, à Home Assistant ni à SQLite : il reçoit
des `Protocol` de L0 par injection (§3.1). C'est ce qui permet de le tester sans
aucun des trois.

**Le point délicat, c'est l'ordre des événements.** Le cerveau produit ses
deltas dans une boucle `async for`, pendant que l'arbitre — appelé *depuis* cette
boucle — doit lui aussi émettre vers la carte. On ne peut pas `yield` depuis un
rappel. D'où la file unique : tout le monde y dépose, l'orchestrateur la vide, et
l'ordre observé par la carte est exactement l'ordre réel.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .. import __version__
from ..kernel.contracts import CerveauProvider, MaisonProvider, MemoireProvider
from ..kernel.errors import ErreurInterne, LunaError
from ..kernel.ids import nouvel_id
from ..kernel.schemas import (
    CerveauDelta,
    CerveauOutilDebut,
    CerveauOutilFin,
    CerveauTermine,
    ContexteRequete,
    EvenementCarte,
    EvtAccepte,
    EvtDelta,
    EvtErreur,
    EvtOutil,
    EvtTermine,
    MessageEnregistre,
    OutilResume,
    ProfilActif,
    Usage,
)
from .arbiter import Arbitre
from .tools import OUTILS

log = logging.getLogger("luna.orchestrateur")

#: Ce qu'on renvoie à Claude comme historique. Au-delà, ça coûte sans servir.
TOURS_HISTORIQUE = 20

NOMS_PROFILS = {
    "guillaume": "Guillaume",
    "clara": "Clara",
    "liam": "Liam",
    "guest": "Invité",
    "unknown": "Inconnu",
}

_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def date_francaise(quand: datetime) -> str:
    return (
        f"{_JOURS[quand.weekday()]} {quand.day} {_MOIS[quand.month - 1]} "
        f"{quand.year}, {quand.hour} h {quand.minute:02d}"
    )


class Orchestrateur:
    def __init__(
        self,
        *,
        cerveau: CerveauProvider,
        maison: MaisonProvider,
        memoire: MemoireProvider,
        arbitre: Arbitre,
        fuseau: str = "Europe/Paris",
    ) -> None:
        self._cerveau = cerveau
        self._maison = maison
        self._memoire = memoire
        self._arbitre = arbitre
        self._fuseau = ZoneInfo(fuseau)
        self._en_cours: dict[str, asyncio.Task[None]] = {}

    # ── luna/chat ────────────────────────────────────────────────────────

    async def converser(
        self,
        texte: str,
        *,
        contexte: ContexteRequete,
        conversation_id: str | None = None,
    ) -> AsyncIterator[EvenementCarte]:
        message_id = nouvel_id("m")
        conversation = conversation_id or await self._memoire.conversation_courante(
            contexte.profile
        )
        yield EvtAccepte(message_id=message_id, conversation_id=conversation)

        await self._memoire.ajouter_message(
            MessageEnregistre(
                id=nouvel_id("m"),
                conversation_id=conversation,
                role="user",
                text=texte,
                ts=self._maintenant(),
                profile=contexte.profile,
                client_id=contexte.client_id,
            )
        )

        file: asyncio.Queue[EvenementCarte | None] = asyncio.Queue()
        tache = asyncio.create_task(
            self._pomper(
                file,
                texte=texte,
                conversation=conversation,
                message_id=message_id,
                contexte=contexte,
            ),
            name=f"luna-echange-{message_id}",
        )
        self._en_cours[message_id] = tache
        try:
            while True:
                evenement = await file.get()
                if evenement is None:
                    return
                yield evenement
        finally:
            self._en_cours.pop(message_id, None)
            if not tache.done():
                tache.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await tache

    async def _pomper(
        self,
        file: asyncio.Queue[EvenementCarte | None],
        *,
        texte: str,
        conversation: str,
        message_id: str,
        contexte: ContexteRequete,
    ) -> None:
        outils_vus: list[OutilResume] = []

        async def emettre(evenement: EvenementCarte) -> None:
            if isinstance(evenement, EvtOutil):
                outils_vus.append(
                    OutilResume(
                        name=evenement.name,
                        label=evenement.label,
                        level=evenement.level,
                        status=evenement.status,
                    )
                )
            await file.put(evenement)

        async def executer_outil(nom: str, entree: dict[str, Any]) -> Any:
            return await self._arbitre.executer(
                nom,
                entree,
                contexte=contexte,
                message_id=message_id,
                emettre=emettre,
            )

        try:
            historique = await self._historique_pour_cerveau(conversation, texte)
            flux = self._cerveau.repondre(
                historique=historique,
                contexte=self._contexte_volatil(contexte),
                outils=OUTILS,
                executer_outil=executer_outil,
            )
            reponse = ""
            usage = Usage()
            async for evenement in flux:
                if isinstance(evenement, CerveauDelta):
                    await file.put(EvtDelta(message_id=message_id, text=evenement.text))
                elif isinstance(evenement, CerveauTermine):
                    reponse, usage = evenement.text, evenement.usage
                elif isinstance(evenement, (CerveauOutilDebut, CerveauOutilFin)):
                    log.debug("cerveau/outil %s", evenement)

            await self._memoire.ajouter_message(
                MessageEnregistre(
                    id=message_id,
                    conversation_id=conversation,
                    role="luna",
                    text=reponse,
                    ts=self._maintenant(),
                    tools=_derniers_statuts(outils_vus),
                )
            )
            await file.put(EvtTermine(message_id=message_id, text=reponse, usage=usage))
        except asyncio.CancelledError:
            log.info("Échange %s annulé", message_id)
            raise
        except LunaError as exc:
            log.warning("Échange %s : %s", message_id, exc.message)
            await file.put(
                EvtErreur(message_id=message_id, code=exc.code, message=exc.message)
            )
        except Exception:
            log.exception("Échange %s : erreur inattendue", message_id)
            interne = ErreurInterne()
            await file.put(
                EvtErreur(
                    message_id=message_id, code=interne.code, message=interne.message
                )
            )
        finally:
            await file.put(None)

    # ── luna/cancel ──────────────────────────────────────────────────────

    async def annuler(self, message_id: str) -> bool:
        tache = self._en_cours.get(message_id)
        if tache is None or tache.done():
            return False
        tache.cancel()
        return True

    # ── luna/history ─────────────────────────────────────────────────────

    async def historique(
        self, conversation_id: str | None, *, contexte: ContexteRequete, limite: int
    ) -> dict[str, Any]:
        identifiant, messages, encore = await self._memoire.historique(
            conversation_id, profil=contexte.profile, limite=limite
        )
        return {
            "conversation_id": identifiant,
            "messages": [m.model_dump(mode="json") for m in messages],
            "has_more": encore,
        }

    # ── luna/info ────────────────────────────────────────────────────────

    async def info(self, contexte: ContexteRequete | None = None) -> dict[str, Any]:
        profil = contexte.profile if contexte else "unknown"
        return {
            "version": __version__,
            "addon": "online" if self._maison.connectee else "degraded",
            "capabilities": ["chat", "ha_control"],
            "profile": ProfilActif(
                id=profil,
                display_name=NOMS_PROFILS.get(profil, profil.capitalize()),
                confidence=1.0 if contexte and contexte.ha_user_id else 0.0,
                signals={"ha_user": 1.0 if contexte and contexte.ha_user_id else 0.0},
            ).model_dump(mode="json"),
            # P3 à P6. La carte grise ce qui est à false plutôt que de le cacher.
            "phases": {
                "voice": True,
                "identity": False,
                "veille": False,
                "guardian": False,
            },
        }

    # ── luna/proposal/decide ─────────────────────────────────────────────

    async def decider(
        self, proposition_id: str, decision: str, *, contexte: ContexteRequete
    ) -> dict[str, Any]:
        return await self._arbitre.decider(proposition_id, decision, contexte=contexte)

    # ── Fabrique de contexte ─────────────────────────────────────────────

    def _maintenant(self) -> datetime:
        return datetime.now(self._fuseau)

    def _contexte_volatil(self, contexte: ContexteRequete) -> str:
        """Le second bloc système. Court, parce qu'il n'est jamais mis en cache."""
        nom = NOMS_PROFILS.get(contexte.profile, contexte.profile)
        lieu = "réseau local" if contexte.local else "accès distant"
        voie = (
            "Demande reçue à la voix : ta réponse sera lue à voix haute."
            if contexte.source == "voix"
            else "Demande reçue à l'écrit."
        )
        return (
            f"Contexte : {date_francaise(self._maintenant())}. "
            f"Tu parles à {nom}. Client : {contexte.client_id} ({lieu}). {voie}"
        )

    async def _historique_pour_cerveau(
        self, conversation: str, texte: str
    ) -> list[dict[str, Any]]:
        _, messages, _ = await self._memoire.historique(
            conversation, profil="", limite=TOURS_HISTORIQUE
        )
        tours: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system" or not message.text.strip():
                continue
            role = "user" if message.role == "user" else "assistant"
            if tours and tours[-1]["role"] == role:
                tours[-1]["content"] += "\n" + message.text
                continue
            tours.append({"role": role, "content": message.text})

        while tours and tours[0]["role"] != "user":
            tours.pop(0)
        if not tours or tours[-1]["role"] != "user" or tours[-1]["content"] != texte:
            tours.append({"role": "user", "content": texte})
        return tours


def _derniers_statuts(outils: list[OutilResume]) -> list[OutilResume]:
    """Un outil apparaît deux fois (running puis done) : on garde le dernier état."""
    par_cle: dict[tuple[str, str], OutilResume] = {}
    for outil in outils:
        par_cle[(outil.name, outil.label)] = outil
    return list(par_cle.values())
