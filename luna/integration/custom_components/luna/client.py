"""Le client du relais : intégration → add-on.

Contrat : luna/docs/P1-CONTRATS.md §5. Une connexion WebSocket unique vers
l'add-on, tenue en tâche de fond, avec reconnexion à backoff.

L'intégration ne relit ni ne réécrit ce qui transite : elle transpose les
identifiants et transmet. Toute la logique vit dans l'add-on — c'est ce qui
permet à ce fichier de rester un nerf, pas un second cerveau.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .const import (
    BACKOFF_MAX,
    BACKOFF_MIN,
    DELAI_REQUETE,
    EN_TETE_SECRET,
)

_LOGGER = logging.getLogger(__name__)

RappelEvenement = Callable[[dict[str, Any]], None]
RappelStatut = Callable[[bool], Awaitable[None]]


class ErreurLuna(Exception):
    """Une erreur de l'add-on, avec son code intact.

    Le code compte : la carte s'en sert pour choisir quoi afficher (§8). Le
    réécrire en « addon_offline » ferait passer une capacité de phase future
    pour une panne.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LunaIndisponible(ErreurLuna):
    """L'add-on ne répond pas."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            "addon_offline",
            message
            or "Luna est hors ligne. Vérifie que l'add-on tourne dans "
            "Paramètres → Modules complémentaires.",
        )


class ClientRelais:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        hote: str,
        port: int,
        secret: str,
        *,
        sur_statut: RappelStatut | None = None,
    ) -> None:
        self._session = session
        self._url = f"ws://{hote}:{port}/relay"
        self._secret = secret
        self._sur_statut = sur_statut

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._tache: asyncio.Task[None] | None = None
        self._ferme = False
        self._id = 0
        self._attentes: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._flux: dict[int, RappelEvenement] = {}
        self._connexion_etablie = asyncio.Event()

        self.connecte = False

    # ── Cycle de vie ─────────────────────────────────────────────────────

    async def demarrer(self) -> None:
        self._ferme = False
        self._tache = asyncio.create_task(self._boucle())

    async def fermer(self) -> None:
        self._ferme = True
        tache, self._tache = self._tache, None
        if tache is not None:
            tache.cancel()
            await asyncio.wait({tache}, timeout=2.0)
        ws, self._ws = self._ws, None
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.close(), 2.0)
        await self._signaler(False)

    async def _boucle(self) -> None:
        backoff = BACKOFF_MIN
        while not self._ferme:
            try:
                self._ws = await self._session.ws_connect(
                    self._url, headers={EN_TETE_SECRET: self._secret}, heartbeat=30
                )
                self._id = 0
                backoff = BACKOFF_MIN
                await self._signaler(True)
                _LOGGER.info("Relais Luna connecté : %s", self._url)
                await self._recevoir()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Relais Luna injoignable (%s)", err)
            finally:
                await self._signaler(False)
                self._reveiller(LunaIndisponible())
            if self._ferme:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def attendre_connexion(self) -> None:
        """Attend que le relais réponde. **Sans limite de temps** : c'est à
        l'appelant de poser la sienne, avec `asyncio.timeout`.

        Utilisé par le formulaire de configuration : on ne valide pas une entrée
        sans avoir vraiment joint l'add-on.
        """
        await self._connexion_etablie.wait()

    async def _signaler(self, connecte: bool) -> None:
        if connecte:
            self._connexion_etablie.set()
        else:
            self._connexion_etablie.clear()
        if self.connecte == connecte:
            return
        self.connecte = connecte
        if self._sur_statut is not None:
            await self._sur_statut(connecte)

    async def _recevoir(self) -> None:
        assert self._ws is not None
        async for message in self._ws:
            if message.type is not aiohttp.WSMsgType.TEXT:
                break
            trame = message.json()
            identifiant = trame.get("id")
            if "result" in trame or "error" in trame:
                self._resoudre(identifiant, trame)
            elif rappel := self._flux.get(identifiant):
                rappel(trame)
        raise LunaIndisponible("Le relais s'est refermé.")

    def _resoudre(self, identifiant: int, trame: dict[str, Any]) -> None:
        future = self._attentes.pop(identifiant, None)
        if future is None or future.done():
            # Une erreur ponctuelle qui n'attend personne, c'est un flux refusé
            # avant d'avoir commencé : elle doit remonter à la carte. Un
            # `result` orphelin, lui, n'est qu'un accusé et ne se transmet pas.
            if "error" in trame and (rappel := self._flux.get(identifiant)):
                rappel(trame)
            return
        if erreur := trame.get("error"):
            future.set_exception(
                ErreurLuna(
                    erreur.get("code") or "internal",
                    erreur.get("message") or "Erreur inconnue.",
                )
            )
        else:
            future.set_result(trame.get("result") or {})

    def _reveiller(self, erreur: Exception) -> None:
        for future in self._attentes.values():
            if not future.done():
                future.set_exception(erreur)
        self._attentes.clear()
        for rappel in list(self._flux.values()):
            rappel(
                {
                    "event": "error",
                    "code": "addon_offline",
                    "message": str(LunaIndisponible()),
                }
            )
        self._flux.clear()

    # ── Émission ─────────────────────────────────────────────────────────

    def _prochain_id(self) -> int:
        self._id += 1
        return self._id

    async def _envoyer(
        self, identifiant: int, op: str, charge: dict[str, Any], contexte: dict[str, Any]
    ) -> None:
        if self._ws is None or self._ws.closed:
            raise LunaIndisponible()
        await self._ws.send_json(
            {"id": identifiant, "op": op, "payload": charge, "context": contexte}
        )

    async def demander(
        self, op: str, charge: dict[str, Any], contexte: dict[str, Any]
    ) -> dict[str, Any]:
        """Opération ponctuelle : une trame, une réponse."""
        identifiant = self._prochain_id()
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._attentes[identifiant] = future
        try:
            await self._envoyer(identifiant, op, charge, contexte)
            return await asyncio.wait_for(future, DELAI_REQUETE)
        except TimeoutError as err:
            raise LunaIndisponible("Luna n'a pas répondu dans les temps.") from err
        finally:
            self._attentes.pop(identifiant, None)

    async def souscrire(
        self,
        op: str,
        charge: dict[str, Any],
        contexte: dict[str, Any],
        rappel: RappelEvenement,
    ) -> Callable[[], None]:
        """Flux : une trame, puis des événements jusqu'au terminal.

        Rend la fonction de désabonnement. **L'appeler** : sans ça, le flux
        continue de tourner côté add-on et la souscription fuit.
        """
        identifiant = self._prochain_id()
        self._flux[identifiant] = rappel
        try:
            await self._envoyer(identifiant, op, charge, contexte)
        except Exception:
            self._flux.pop(identifiant, None)
            raise

        def arreter() -> None:
            self._flux.pop(identifiant, None)
            if self._ws is not None and not self._ws.closed:
                asyncio.create_task(  # noqa: RUF006
                    self._envoyer(
                        self._prochain_id(),
                        "stop",
                        {"stream_id": identifiant},
                        contexte,
                    )
                )

        return arreter
