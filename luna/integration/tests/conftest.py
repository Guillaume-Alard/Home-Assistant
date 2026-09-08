"""Harnais Home Assistant pour l'intégration Luna.

`pytest-homeassistant-custom-component` démarre une vraie instance de Home
Assistant en mémoire : les commandes WebSocket, le formulaire de configuration
et le `binary_sensor` sont exercés par le vrai code de HA, pas par des doublures.

Le relais de l'add-on, lui, est simulé — c'est le contrat §5 qu'on vérifie ici,
pas le cerveau, déjà couvert par les 151 tests de l'add-on.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import WSMsgType, web

from custom_components.luna.const import EN_TETE_SECRET

pytest_plugins = "pytest_homeassistant_custom_component"

SECRET = "secret-de-test"


@pytest.fixture(autouse=True)
def activer_integrations_perso(enable_custom_integrations):
    """Sans ça, Home Assistant ignore `custom_components/`."""
    return


class FauxRelais:
    """Le contrat §5 : trames {id, op, payload, context}, réponses et flux."""

    def __init__(self) -> None:
        self.recu: list[dict[str, Any]] = []
        self.reponses: dict[str, Any] = {
            "info": {
                "version": "0.1.0",
                "addon": "online",
                "capabilities": ["chat", "ha_control"],
                "profile": {
                    "id": "guillaume",
                    "display_name": "Guillaume",
                    "confidence": 1.0,
                    "signals": {"ha_user": 1.0},
                },
                "phases": {
                    "voice": False,
                    "identity": False,
                    "veille": False,
                    "guardian": False,
                },
            },
            "history": {"conversation_id": "c_1", "messages": [], "has_more": False},
            "decide": {"executed": True, "results": []},
        }
        self.erreurs: dict[str, dict[str, str]] = {
            "patterns": {"code": "not_implemented", "message": "Phase 4."},
        }
        self.evenements_chat = [
            {"event": "accepted", "message_id": "m_1", "conversation_id": "c_1"},
            {"event": "delta", "message_id": "m_1", "text": "Bonjour."},
            {
                "event": "done",
                "message_id": "m_1",
                "text": "Bonjour.",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        ]
        self.refuser_secret = False

    async def handler(self, requete: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(requete)
        if self.refuser_secret or requete.headers.get(EN_TETE_SECRET) != SECRET:
            await ws.close(code=4401)
            return ws
        async for message in ws:
            if message.type is not WSMsgType.TEXT:
                break
            await self._traiter(ws, message.json())
        return ws

    async def _traiter(self, ws: web.WebSocketResponse, trame: dict[str, Any]) -> None:
        self.recu.append(trame)
        identifiant, op = trame.get("id"), trame.get("op")
        if op in self.erreurs:
            await ws.send_json({"id": identifiant, "error": self.erreurs[op]})
        elif op == "chat":
            # Pas d'accusé : le premier événement (`accepted`) en tient lieu,
            # comme dans le vrai relais.
            for evenement in self.evenements_chat:
                await ws.send_json({"id": identifiant, **evenement})
        elif op == "feed":
            await ws.send_json(
                {"id": identifiant, "event": "status", "addon": "online", "detail": None}
            )
        elif op == "stop":
            await ws.send_json({"id": identifiant, "result": {"stopped": True}})
        else:
            await ws.send_json({"id": identifiant, "result": self.reponses.get(op, {})})

    def dernier(self, op: str) -> dict[str, Any]:
        return next(t for t in reversed(self.recu) if t.get("op") == op)


@pytest.fixture
async def faux_relais(socket_enabled):
    """`socket_enabled` : le harnais HA coupe les sockets par défaut, et notre
    faux relais est un vrai serveur TCP sur la boucle locale."""
    relais = FauxRelais()
    app = web.Application()
    app.router.add_get("/relay", relais.handler)
    coureur = web.AppRunner(app, shutdown_timeout=1.0)
    await coureur.setup()
    site = web.TCPSite(coureur, "127.0.0.1", 0)
    await site.start()
    # `site.name` rend le port *demandé* (0) et pas celui réellement attribué ;
    # `AppRunner.addresses` donne l'adresse effective.
    hote, port = coureur.addresses[0][:2]
    relais.hote, relais.port = str(hote), int(port)  # type: ignore[attr-defined]
    try:
        yield relais
    finally:
        await coureur.cleanup()


@pytest.fixture
async def entree(hass, faux_relais):
    """Une intégration Luna installée et connectée au faux relais."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.luna.const import (
        CONF_HOTE,
        CONF_PORT,
        CONF_SECRET,
        DOMAINE,
    )

    entree = MockConfigEntry(
        domain=DOMAINE,
        data={
            CONF_HOTE: faux_relais.hote,
            CONF_PORT: faux_relais.port,
            CONF_SECRET: SECRET,
        },
        unique_id=DOMAINE,
        title="Luna",
    )
    entree.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entree.entry_id)
    await hass.async_block_till_done()
    for _ in range(50):
        if hass.data[DOMAINE][entree.entry_id].connecte:
            break
        await asyncio.sleep(0.02)
    return entree
