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


@pytest.fixture(autouse=True)
async def socle(hass):
    """Le composant `homeassistant` doit exister avant `conversation`.

    Dans une vraie installation il est toujours là ; dans le harnais, il faut le
    demander — sinon `conversation` échoue sur `exposed_entities`, et Luna avec
    lui puisqu'elle en dépend depuis P2.
    """
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})


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
            "identity": {
                "profile": {
                    "id": "guillaume",
                    "display_name": "Guillaume",
                    "confidence": 0.82,
                    "signals": {"voice": 0.91},
                },
                "expires_at": None,
                "enrolled": ["guillaume"],
                "voice_needed": True,
                "voice_available": True,
            },
            "identity_voice": {
                "profile": "guillaume",
                "confidence": 0.82,
                "margin": 0.31,
                "asked": False,
            },
            "identity_confirm": {
                "profile": "guillaume",
                "confidence": 1.0,
                "expires_at": None,
            },
            "enroll_start": {"session": "e_1", "phrases": ["une", "deux"]},
            "enroll_sample": {"accepted": True, "quality": "ok", "remaining": 4},
            "enroll_finish": {
                "profile": "guillaume",
                "samples": 5,
                "coherence": 0.91,
            },
            "identity_forget": {"removed": 5},
            # ── Habitudes et veille (P4) ─────────────────────────────────
            "suggestions": {
                "suggestions": [
                    {
                        "id": "al_1",
                        "key": "ouvrant||binary_sensor.luna_ouvrant_oublie",
                        "title": "Un ouvrant est resté ouvert et il est tard.",
                        "why": "La baie vitrée du séjour est ouverte.",
                        "score": 0.5,
                        "level": 0,
                        "actions": [],
                    }
                ]
            },
            "patterns": {
                "patterns": [
                    {
                        "id": "f_1",
                        "predicate": "heure_de_coucher",
                        "value": "23:20",
                        "entity_id": None,
                        "confidence": 0.74,
                        "observations": 23,
                        "last_seen": "2026-09-07T23:18:00+02:00",
                        "status": "active",
                    }
                ]
            },
            "alerts_feedback": {"ok": True, "score": 0.25, "muted_until": None},
            "alerts_act": {"executed": True, "results": []},
            "facts": {
                "facts": [
                    {
                        "id": "f_2",
                        "predicate": "preference_eclairage",
                        "value": "couloir tamisé le soir",
                        "profile": "guillaume",
                        "why": "Tu me l'as dit le 6 septembre.",
                        "created_at": "2026-09-07T03:30:00+02:00",
                    }
                ]
            },
            "facts_decide": {"status": "active"},
        }
        self.erreurs: dict[str, dict[str, str]] = {
            "identity_face": {"code": "not_implemented", "message": "Phase 6."},
        }
        #: Tout ce qui est arrivé jusqu'ici. Sert à prouver qu'un refus s'est
        #: fait **avant** le relais, pas après.
        self.audio_recu: list[str] = []
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
        if audio := (trame.get("payload") or {}).get("audio"):
            self.audio_recu.append(audio)
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
