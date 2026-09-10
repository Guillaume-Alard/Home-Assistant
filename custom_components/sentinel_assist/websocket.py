"""Commande WebSocket « sentinel_assist/converse » : conversation en streaming.

La carte Luna s'abonne à cette commande (par `hass.connection`) et reçoit la
réponse de Sentinel **fragment par fragment** — d'où le rendu « mot à mot ». On
ne fait que relayer : la commande ouvre le flux SSE de Sentinel
(`/v1/chat/completions` avec `stream: true`) et repousse chaque fragment vers la
carte. Aucune écriture, aucune décision ici — le cerveau et la sécurité restent
côté Sentinel.
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Enregistre la commande (idempotent au niveau appelant)."""
    websocket_api.async_register_command(hass, ws_converse)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sentinel_assist/converse",
        vol.Required("text"): str,
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_converse(hass: HomeAssistant, connection, msg: dict) -> None:
    entries = hass.data.get(DOMAIN, {}).get("entries", {})
    cfg = entries.get(msg["entry_id"]) if msg.get("entry_id") else None
    if cfg is None and entries:
        cfg = next(iter(entries.values()))  # une seule intégration, en général
    if cfg is None:
        connection.send_error(msg["id"], "not_configured", "Aucune intégration Sentinel configurée.")
        return

    task = hass.async_create_task(_stream(hass, connection, msg, cfg))

    @callback
    def _cancel() -> None:
        task.cancel()

    # On accuse réception de l'abonnement AVANT tout événement : le client résout
    # sa promesse d'abonnement sur ce résultat, puis reçoit les fragments.
    connection.subscriptions[msg["id"]] = _cancel
    connection.send_result(msg["id"])


async def _stream(hass: HomeAssistant, connection, msg: dict, cfg: dict) -> None:
    session = async_get_clientsession(hass, verify_ssl=False)
    morceaux: list[str] = []
    try:
        async with session.post(
            f"{cfg['base']}/v1/chat/completions",
            headers={"Authorization": f"Bearer {cfg['token']}"},
            json={
                "model": "sentinel",
                "stream": True,
                "messages": [{"role": "user", "content": msg["text"]}],
            },
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as resp:
            resp.raise_for_status()
            async for raw in resp.content:
                ligne = raw.decode("utf-8", "ignore").strip()
                if not ligne.startswith("data:"):
                    continue
                charge = ligne[5:].strip()
                if charge == "[DONE]":
                    break
                try:
                    delta = json.loads(charge)["choices"][0]["delta"].get("content")
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    morceaux.append(delta)
                    connection.send_message(
                        websocket_api.event_message(msg["id"], {"delta": delta})
                    )
        connection.send_message(
            websocket_api.event_message(msg["id"], {"done": True, "text": "".join(morceaux)})
        )
    except asyncio.CancelledError:
        raise  # le client s'est désabonné : rien à signaler
    except Exception as err:  # noqa: BLE001 — on referme toujours proprement le flux
        _LOGGER.warning("Streaming Sentinel indisponible : %s", err)
        connection.send_message(
            websocket_api.event_message(
                msg["id"],
                {"error": "Sentinel est injoignable pour l'instant.", "done": True},
            )
        )
    finally:
        connection.subscriptions.pop(msg["id"], None)
