"""L3 — le relais interne, consommé par l'intégration Home Assistant.

Contrat : docs/P1-CONTRATS.md §5. WebSocket sur `/relay`, secret partagé en
en-tête, trames `{id, op, payload, context}`.

**Le `context` fait foi et vient de l'intégration**, qui le construit à partir de
`connection.user`. La carte ne peut ni le fournir ni l'influencer : c'est ce qui
empêche un client de se déclarer administrateur.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiohttp import WSMsgType, web

from ..engine.orchestrator import Orchestrateur
from ..kernel.bus import Bus
from ..kernel.errors import LunaError, PasEncoreImplemente
from ..kernel.ids import nouvel_id
from ..kernel.schemas import (
    ContexteRequete,
    EvtMessage,
    EvtStatut,
    MaisonConnectee,
    MessageDiffuse,
)

log = logging.getLogger("luna.relais")

EN_TETE_SECRET = "X-Luna-Secret"  # noqa: S105 — un nom d'en-tête, pas un secret
FERMETURE_NON_AUTORISE = 4401

#: Documentées en P1, vivantes plus tard (§12). Jamais un silence (§8).
OPS_FUTURES = {
    "identity": "Cette capacité arrive en phase 3.",
    "alerts_feedback": "Cette capacité arrive en phase 4.",
    "patterns": "Cette capacité arrive en phase 4.",
    "suggestions": "Cette capacité arrive en phase 4.",
    "identity_face": "Cette capacité arrive en phase 6.",
}


#: Résout un utilisateur Home Assistant en profil Luna (décision A6).
ResolveurProfil = Callable[[str | None, str | None], str]


class Relais:
    def __init__(
        self,
        orchestrateur: Orchestrateur,
        bus: Bus,
        secret: str,
        resoudre_profil: ResolveurProfil,
    ) -> None:
        self._orchestrateur = orchestrateur
        self._bus = bus
        self._secret = secret
        self._resoudre_profil = resoudre_profil
        self._connexions: set[Connexion] = set()
        bus.abonner(MaisonConnectee, self._sur_maison)

    async def diffuser(self, evenement: Any) -> None:
        """Pousse un événement à toutes les cartes abonnées au feed."""
        for connexion in list(self._connexions):
            await connexion.diffuser(evenement)

    async def _sur_maison(self, evenement: MaisonConnectee) -> None:
        statut = EvtStatut(
            addon="online" if evenement.connectee else "degraded",
            detail=None if evenement.connectee else "Home Assistant injoignable",
        )
        await self.diffuser(statut)

    async def handler(self, requete: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(requete)

        fourni = requete.headers.get(EN_TETE_SECRET, "")
        if not self._secret or not hmac.compare_digest(fourni, self._secret):
            log.warning("Relais : secret invalide depuis %s", requete.remote)
            await ws.close(code=FERMETURE_NON_AUTORISE)
            return ws

        connexion = Connexion(
            ws, self._orchestrateur, self._resoudre_profil, self.diffuser
        )
        self._connexions.add(connexion)
        log.info("Relais : intégration connectée (%s)", requete.remote)
        try:
            await connexion.servir()
        finally:
            self._connexions.discard(connexion)
            await connexion.arreter_tout()
            log.info("Relais : intégration déconnectée")
        return ws


class Connexion:
    def __init__(
        self,
        ws: web.WebSocketResponse,
        orchestrateur: Orchestrateur,
        resoudre_profil: ResolveurProfil,
        diffuser_a_tous: Callable[[Any], Awaitable[None]],
    ) -> None:
        self._ws = ws
        self._orchestrateur = orchestrateur
        self._resoudre_profil = resoudre_profil
        self._diffuser_a_tous = diffuser_a_tous
        self._verrou = asyncio.Lock()
        self._flux: dict[int, asyncio.Task[None]] = {}
        self._abonnes_feed: set[int] = set()

    # ── Émission ─────────────────────────────────────────────────────────

    async def _envoyer(self, charge: dict[str, Any]) -> None:
        if self._ws.closed:
            return
        async with self._verrou:
            with contextlib.suppress(ConnectionResetError):
                await self._ws.send_json(charge)

    async def diffuser(self, evenement: Any) -> None:
        for identifiant in list(self._abonnes_feed):
            await self._envoyer({"id": identifiant, **evenement.model_dump(mode="json")})

    # ── Réception ────────────────────────────────────────────────────────

    async def servir(self) -> None:
        async for message in self._ws:
            if message.type is not WSMsgType.TEXT:
                break
            try:
                trame = message.json()
            except ValueError:
                continue
            asyncio.create_task(self._traiter(trame))  # noqa: RUF006

    async def _traiter(self, trame: dict[str, Any]) -> None:
        identifiant = int(trame.get("id", 0))
        op = str(trame.get("op") or "")
        charge = trame.get("payload") or {}
        contexte = self._contexte(trame.get("context") or {})
        try:
            await self._router(identifiant, op, charge, contexte)
        except LunaError as exc:
            await self._envoyer({"id": identifiant, "error": exc.charge_utile()})
        except Exception:
            log.exception("Relais : op %s a échoué", op)
            await self._envoyer(
                {
                    "id": identifiant,
                    "error": {
                        "code": "internal",
                        "message": "Quelque chose a cassé de mon côté.",
                    },
                }
            )

    def _contexte(self, brut: dict[str, Any]) -> ContexteRequete:
        """Le profil est résolu **ici**, pas côté intégration.

        La correspondance utilisateur HA → profil Luna vit dans les options de
        l'add-on (décision A6). Une seule source de vérité, et un client qui
        se déclarerait « guillaume » n'y gagne rien.
        """
        contexte = ContexteRequete(**brut)
        return contexte.model_copy(
            update={
                "profile": self._resoudre_profil(
                    contexte.ha_user_name, contexte.ha_user_id
                )
            }
        )

    async def _router(
        self, identifiant: int, op: str, charge: dict[str, Any], contexte: ContexteRequete
    ) -> None:
        if op == "info":
            await self._resultat(identifiant, await self._orchestrateur.info(contexte))
        elif op == "history":
            await self._resultat(
                identifiant,
                await self._orchestrateur.historique(
                    charge.get("conversation_id"),
                    contexte=contexte,
                    limite=int(charge.get("limit") or 50),
                ),
            )
        elif op == "cancel":
            annule = await self._orchestrateur.annuler(str(charge.get("message_id")))
            await self._resultat(identifiant, {"cancelled": annule})
        elif op == "decide":
            await self._resultat(
                identifiant,
                await self._orchestrateur.decider(
                    str(charge.get("proposal_id")),
                    str(charge.get("decision")),
                    contexte=contexte,
                ),
            )
        elif op == "chat":
            self._flux[identifiant] = asyncio.create_task(
                self._converser(identifiant, charge, contexte),
                name=f"luna-relais-{identifiant}",
            )
        elif op == "feed":
            self._abonnes_feed.add(identifiant)
            await self._envoyer(
                {
                    "id": identifiant,
                    **EvtStatut(
                        addon=(await self._orchestrateur.info(contexte))["addon"]
                    ).model_dump(mode="json"),
                }
            )
        elif op == "stop":
            await self._arreter(int(charge.get("stream_id") or identifiant))
            await self._resultat(identifiant, {"stopped": True})
        elif op in OPS_FUTURES:
            raise PasEncoreImplemente(OPS_FUTURES[op])
        else:
            raise LunaError(f"Opération inconnue : {op}.")

    async def _resultat(self, identifiant: int, resultat: dict[str, Any]) -> None:
        await self._envoyer({"id": identifiant, "result": resultat})

    async def _converser(
        self, identifiant: int, charge: dict[str, Any], contexte: ContexteRequete
    ) -> None:
        texte = str(charge.get("text") or "")
        # Un échange né hors d'une carte — l'agent de conversation d'Assist —
        # doit rejoindre le fil des cartes ouvertes. §7 : « à l'écrit et à
        # l'oral, indifféremment ». C'est le transport qui sait d'où ça vient,
        # pas l'orchestrateur : la décision se prend donc ici.
        diffuser = bool(charge.get("diffuser"))
        try:
            flux = self._orchestrateur.converser(
                texte,
                contexte=contexte,
                conversation_id=charge.get("conversation_id"),
            )
            async for evenement in flux:
                await self._envoyer(
                    {"id": identifiant, **evenement.model_dump(mode="json")}
                )
                if diffuser:
                    await self._diffuser_tour(evenement, texte)
        except asyncio.CancelledError:
            raise
        finally:
            self._flux.pop(identifiant, None)

    async def _diffuser_tour(self, evenement: Any, question: str) -> None:
        """Traduit un échange d'Assist en messages pour les cartes ouvertes."""
        genre = getattr(evenement, "event", None)
        if genre == "proposal":
            # Une proposition née d'un tour vocal doit pouvoir être acceptée :
            # on ne clique pas dans un haut-parleur. Sans ça elle expirerait en
            # cinq minutes sans que personne n'ait pu la voir.
            await self._diffuser_a_tous(evenement)
            return
        if genre == "accepted":
            role, texte, identifiant = "user", question, nouvel_id("m")
        elif genre == "done":
            role, texte, identifiant = "luna", evenement.text, evenement.message_id
        else:
            return
        if not texte.strip():
            return
        await self._diffuser_a_tous(
            EvtMessage(
                message=MessageDiffuse(
                    id=identifiant,
                    role=role,  # type: ignore[arg-type]
                    text=texte,
                    ts=datetime.now().astimezone(),
                    conversation_id=getattr(evenement, "conversation_id", "")
                    or getattr(evenement, "message_id", ""),
                    # Home Assistant a déjà parlé : la carte ne doit pas répéter.
                    speak=False,
                )
            )
        )

    async def _arreter(self, identifiant: int) -> None:
        self._abonnes_feed.discard(identifiant)
        tache = self._flux.pop(identifiant, None)
        if tache is not None and not tache.done():
            tache.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tache

    async def arreter_tout(self) -> None:
        for identifiant in list(self._flux):
            await self._arreter(identifiant)
        self._abonnes_feed.clear()
