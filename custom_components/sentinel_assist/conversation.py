"""L'agent conversationnel : relaie chaque phrase d'Assist vers Sentinel."""

from __future__ import annotations

import logging

import aiohttp
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_BASE_URL, CONF_TOKEN, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SentinelConversationEntity(entry)])


class SentinelConversationEntity(conversation.ConversationEntity):
    """Agent conversationnel qui délègue tout au cerveau de Sentinel."""

    _attr_has_entity_name = True
    _attr_name = "Sentinel"

    def __init__(self, entry: ConfigEntry) -> None:
        self._base_url = entry.data[CONF_BASE_URL]
        self._token = entry.data[CONF_TOKEN]
        self._attr_unique_id = entry.entry_id

    @property
    def supported_languages(self) -> list[str] | str:
        return MATCH_ALL  # Sentinel répond en français quoi qu'il arrive

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        response = intent.IntentResponse(language=user_input.language)
        try:
            session = async_get_clientsession(self.hass, verify_ssl=False)
            async with session.post(
                f"{self._base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "model": "sentinel",
                    "messages": [{"role": "user", "content": user_input.text}],
                },
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
            reply = data["choices"][0]["message"]["content"]
        except Exception as err:  # noqa: BLE001 — on ne casse jamais le pipeline vocal
            _LOGGER.error("Sentinel injoignable : %s", err)
            response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                "Sentinel est injoignable pour l'instant.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        response.async_set_speech(reply)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
