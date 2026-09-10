"""Connecteur Sentinel : expose Sentinel comme agent conversationnel d'Assist,
et sert la carte Lovelace « Luna » (le visage de Luna dans Home Assistant).

Alternative maison à « Extended OpenAI Conversation » — minimale, sans
dépendance tierce, sous notre contrôle. Elle relaie chaque phrase d'Assist vers
l'API compatible OpenAI de Sentinel (`/v1/chat/completions`).

La carte est un simple fichier statique servi depuis `www/` et déclaré comme
module frontend : elle apparaît alors dans le sélecteur de cartes de Lovelace,
sans que Guillaume ait à enregistrer une ressource à la main.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import websocket as ws
from .const import (
    CARD_FILENAME,
    CARD_URL_BASE,
    CARD_VERSION,
    CONF_BASE_URL,
    CONF_TOKEN,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["conversation"]

_CARD_REGISTERED = "card_registered"
_WS_REGISTERED = "ws_registered"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    store = hass.data.setdefault(DOMAIN, {})
    # Coordonnées de Sentinel, à disposition de la commande WebSocket de streaming.
    store.setdefault("entries", {})[entry.entry_id] = {
        "base": entry.data[CONF_BASE_URL],
        "token": entry.data[CONF_TOKEN],
    }
    if not store.get(_WS_REGISTERED):
        ws.async_register(hass)
        store[_WS_REGISTERED] = True
    await _register_card(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # La ressource frontend et la commande WS restent enregistrées pour la session
    # (les retirer casserait une autre entrée éventuelle) ; on oublie juste les
    # coordonnées de cette entrée pour ne plus router vers un Sentinel disparu.
    hass.data.get(DOMAIN, {}).get("entries", {}).pop(entry.entry_id, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _register_card(hass: HomeAssistant) -> None:
    """Sert www/ et déclare la carte comme module frontend (une seule fois)."""
    store = hass.data.setdefault(DOMAIN, {})
    if store.get(_CARD_REGISTERED):
        return

    www = Path(__file__).parent / "www"
    if not (www / CARD_FILENAME).is_file():
        _LOGGER.warning("Carte Luna introuvable (%s) — carte non servie", www / CARD_FILENAME)
        return

    # Enregistrement du chemin statique : API async récente si dispo, repli sync.
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL_BASE, str(www), cache_headers=False)]
        )
    except ImportError:
        # Home Assistant plus ancien : API synchrone (thread-safe ici, au démarrage).
        hass.http.register_static_path(CARD_URL_BASE, str(www), cache_headers=False)

    url = f"{CARD_URL_BASE}/{CARD_FILENAME}?v={CARD_VERSION}"
    try:
        from homeassistant.components.frontend import add_extra_module_url

        add_extra_module_url(hass, url)
    except ImportError:  # très ancienne version : script classique
        from homeassistant.components.frontend import add_extra_js_url

        add_extra_js_url(hass, url)

    store[_CARD_REGISTERED] = True
    _LOGGER.info("Carte Luna enregistrée (%s)", url)
