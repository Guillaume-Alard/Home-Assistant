"""Connecteur Sentinel : expose Sentinel comme agent conversationnel d'Assist.

Alternative maison à « Extended OpenAI Conversation » — minimale, sans
dépendance tierce, sous notre contrôle. Elle relaie chaque phrase d'Assist vers
l'API compatible OpenAI de Sentinel (`/v1/chat/completions`).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

PLATFORMS = ["conversation"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
