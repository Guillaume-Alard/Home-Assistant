"""Client WebSocket vers Nova (Home Assistant).

Connexion unique maintenue en tâche de fond : authentification par jeton longue
durée, cache d'états tenu à jour par `state_changed`, registres (pièces,
entités, appareils) pour résoudre « le salon » → entités, reconnexion avec
backoff. Les événements et les changements de statut sont poussés via callbacks.

⚠️ Écritures : `call_service` ne doit être appelé QUE par les exécuteurs du
moteur d'actions (`app/actions/`) — c'est l'invariant de sécurité du projet,
verrouillé par un test statique (tests/test_invariant.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable

import websockets

from ..norm import normalize

log = logging.getLogger("sentinel.ha")


class HAError(RuntimeError):
    """Erreur Home Assistant — message en français, montrable à l'utilisateur."""


def _ws_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    return url + "/api/websocket"


class HAClient:
    COMMAND_TIMEOUT = 10.0

    def __init__(
        self,
        url: str,
        token: str,
        *,
        on_event: Callable[[dict], Awaitable[None]] | None = None,
        on_status: Callable[[bool], Awaitable[None]] | None = None,
    ):
        self._url = _ws_url(url)
        self._token = token
        self._on_event = on_event
        self._on_status = on_status

        self._task: asyncio.Task | None = None
        self._ws = None
        self._closing = False
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}

        self.connected = False
        self.ha_version: str | None = None
        self._states: dict[str, dict] = {}
        self._areas: dict[str, str] = {}          # area_id → nom
        self._entity_area: dict[str, str] = {}    # entity_id → area_id

    # ── Cycle de vie ─────────────────────────────────────────────────────

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="ha-client")

    async def stop(self) -> None:
        self._closing = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def _run(self) -> None:
        backoff = 1.0
        while not self._closing:
            try:
                async with websockets.connect(self._url, max_size=2**23) as ws:
                    await self._handshake(ws)
                    self._ws = ws
                    reader = asyncio.create_task(self._reader(ws), name="ha-reader")
                    try:
                        await self._bootstrap()
                        self.connected = True
                        backoff = 1.0
                        log.info(
                            "Nova connectée — %d entités, %d pièces",
                            len(self._states), len(self._areas),
                        )
                        await self._notify_status(True)
                        await reader  # jusqu'à la coupure de connexion
                    finally:
                        reader.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await reader
            except asyncio.CancelledError:
                raise
            except HAError as exc:
                log.error("Connexion Nova refusée : %s", exc)
                backoff = 30.0  # jeton invalide : inutile d'insister vite
            except Exception as exc:
                log.warning("Connexion Nova perdue (%s) — nouvel essai dans %.0f s", exc, backoff)
            finally:
                was_connected = self.connected
                self.connected = False
                self._ws = None
                self._fail_pending(HAError("Nova s'est déconnectée pendant la commande."))
                if was_connected:
                    await self._notify_status(False)
            if not self._closing:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _handshake(self, ws) -> None:
        async with asyncio.timeout(10):
            first = json.loads(await ws.recv())
            if first.get("type") != "auth_required":
                raise HAError("Nova a répondu de façon inattendue à la connexion.")
            await ws.send(json.dumps({"type": "auth", "access_token": self._token}))
            resp = json.loads(await ws.recv())
        if resp.get("type") != "auth_ok":
            raise HAError("Nova a refusé le jeton d'accès — vérifie HA_TOKEN dans .env.")

    async def _bootstrap(self) -> None:
        states = await self._send_wait({"type": "get_states"})
        self._states = {s["entity_id"]: s for s in states}

        try:
            config = await self._send_wait({"type": "get_config"})
            self.ha_version = (config or {}).get("version")
        except HAError:
            self.ha_version = None

        # Registres : nécessitent un jeton d'utilisateur administrateur ; en cas
        # de refus on continue sans résolution de pièces (mode dégradé).
        try:
            areas = await self._send_wait({"type": "config/area_registry/list"})
            entities = await self._send_wait({"type": "config/entity_registry/list"})
            devices = await self._send_wait({"type": "config/device_registry/list"})
            self._areas = {a["area_id"]: a["name"] for a in areas}
            device_area = {d["id"]: d.get("area_id") for d in devices}
            self._entity_area = {}
            for e in entities:
                area = e.get("area_id") or device_area.get(e.get("device_id") or "")
                if area:
                    self._entity_area[e["entity_id"]] = area
        except HAError as exc:
            log.warning("Registres inaccessibles (%s) — pièces non résolues", exc)

        await self._send_wait({"type": "subscribe_events", "event_type": "state_changed"})

    async def _reader(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype in ("result", "pong"):
            fut = self._pending.pop(msg.get("id"), None)
            if fut is None or fut.done():
                return
            if mtype == "pong" or msg.get("success"):
                fut.set_result(msg.get("result"))
            else:
                err = (msg.get("error") or {}).get("message", "erreur inconnue")
                fut.set_exception(HAError(f"Nova a refusé la commande : {err}"))
        elif mtype == "event":
            event = msg.get("event") or {}
            if event.get("event_type") == "state_changed":
                data = event.get("data") or {}
                entity_id = data.get("entity_id")
                new_state = data.get("new_state")
                if entity_id:
                    if new_state is None:
                        self._states.pop(entity_id, None)
                    else:
                        self._states[entity_id] = new_state
            if self._on_event:
                asyncio.get_running_loop().create_task(self._safe_event(event))

    async def _safe_event(self, event: dict) -> None:
        try:
            await self._on_event(event)
        except Exception:
            log.exception("Erreur dans le traitement d'un événement Nova")

    def _fail_pending(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def _notify_status(self, connected: bool) -> None:
        if self._on_status:
            try:
                await self._on_status(connected)
            except Exception:
                log.exception("Erreur dans le callback de statut Nova")

    async def _send_wait(self, payload: dict, timeout: float | None = None):
        ws = self._ws
        if ws is None:
            raise HAError("Nova est injoignable pour l'instant.")
        self._msg_id += 1
        mid = self._msg_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        try:
            await ws.send(json.dumps({"id": mid, **payload}))
            return await asyncio.wait_for(fut, timeout or self.COMMAND_TIMEOUT)
        except TimeoutError as exc:
            raise HAError("Nova n'a pas répondu à temps.") from exc
        finally:
            self._pending.pop(mid, None)

    # ── Écriture (réservée aux exécuteurs du moteur d'actions) ───────────

    async def call_service(
        self, domain: str, service: str, data: dict | None = None, target: dict | None = None
    ) -> None:
        if not self.connected:
            raise HAError("Nova est injoignable pour l'instant.")
        payload: dict = {"type": "call_service", "domain": domain, "service": service}
        if data:
            payload["service_data"] = data
        if target:
            payload["target"] = target
        await self._send_wait(payload)

    # ── Lecture (libre) ──────────────────────────────────────────────────

    def get_state(self, entity_id: str) -> dict | None:
        return self._states.get(entity_id)

    def states_snapshot(self) -> dict[str, dict]:
        return dict(self._states)

    def friendly_name(self, entity_id: str) -> str:
        state = self._states.get(entity_id) or {}
        return (state.get("attributes") or {}).get("friendly_name") or entity_id

    def areas(self) -> dict[str, str]:
        """{area_id: nom} des pièces déclarées dans Nova."""
        return dict(self._areas)

    def area_name(self, area_id: str) -> str:
        return self._areas.get(area_id, area_id)

    def entity_area(self, entity_id: str) -> str | None:
        return self._entity_area.get(entity_id)

    def find_area_in_text(self, text: str) -> tuple[str, str] | None:
        """Cherche une pièce de Nova citée dans une phrase (match le plus long).

        « allume la lumière du salon » → ("area_salon", "Salon")
        """
        phrase = f" {normalize(text)} "
        best: tuple[str, str] | None = None
        best_len = 0
        for area_id, name in self._areas.items():
            norm = normalize(name)
            if norm and f" {norm} " in phrase and len(norm) > best_len:
                best = (area_id, name)
                best_len = len(norm)
        return best

    def entities_in_area(self, area_id: str, domain: str | None = None) -> list[str]:
        out = []
        for entity_id, area in self._entity_area.items():
            if area != area_id or entity_id not in self._states:
                continue
            if domain and not entity_id.startswith(domain + "."):
                continue
            out.append(entity_id)
        return sorted(out)

    def entities_by_domain(self, domain: str) -> list[str]:
        return sorted(e for e in self._states if e.startswith(domain + "."))
