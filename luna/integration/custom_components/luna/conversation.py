"""L'agent de conversation : la deuxième porte d'entrée vers le même cerveau.

Décision B1. Tout ce qui parle au pipeline Assist de Home Assistant — le bouton
Assist de l'app Companion, la tablette murale, un satellite plus tard — arrive
ici, et repart par le même op `chat` que la carte Loggia. Il n'y a pas deux
Luna : il y a deux portes.

**Le streaming compte.** `ChatLog.async_add_delta_content_stream` fait descendre
les deltas dans le pipeline au fil de l'eau, ce qui permet à Piper de commencer
à parler avant la fin de la réponse. Sans ça, on attend la phrase entière avant
d'entendre le premier mot.

Ce fichier ne décide rien : ni le niveau d'une action, ni le profil. Il pose la
provenance (`source: "voix"`) et laisse l'add-on trancher.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import ClientRelais, ErreurLuna
from .const import DOMAINE

_LOGGER = logging.getLogger(__name__)

#: Une réponse vocale qui n'arrive pas en une minute n'arrivera jamais utilement.
DELAI_TOUR = 60.0

LANGUES = ["fr"]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    client: ClientRelais = hass.data[DOMAINE][entry.entry_id]
    async_add_entities([AgentLuna(entry, client)])


class AgentLuna(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: ConfigEntry, client: ClientRelais) -> None:
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_agent"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAINE, entry.entry_id)},
            name="Luna",
            manufacturer="Alardware",
            model="Add-on Luna",
        )

    @property
    def supported_languages(self) -> list[str]:
        return LANGUES

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        dit: list[str] = []

        try:
            async for _ in chat_log.async_add_delta_content_stream(
                user_input.agent_id or self.entity_id,
                self._deltas(user_input, dit),
            ):
                pass
        except ErreurLuna as err:
            # Une erreur se dit à voix haute plutôt que de laisser un silence :
            # §8, « jamais d'échec silencieux », vaut aussi au micro.
            _LOGGER.warning("Luna : %s", err.message)
            dit = [err.message]

        reponse = intent.IntentResponse(language=user_input.language)
        reponse.async_set_speech(
            "".join(dit).strip() or "Je n'ai pas de réponse à te donner."
        )
        return conversation.ConversationResult(
            response=reponse,
            conversation_id=chat_log.conversation_id,
            continue_conversation=False,
        )

    async def _deltas(
        self, user_input: conversation.ConversationInput, dit: list[str]
    ) -> AsyncGenerator[conversation.AssistantContentDeltaDict]:
        """Traduit le flux du relais en deltas pour le pipeline.

        Le relais est à rappels, le pipeline veut un générateur : une file fait
        le pont, et le désabonnement est garanti par le `finally`.
        """
        file: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        arreter = await self._client.souscrire(
            "chat",
            {
                "text": user_input.text,
                # Le tour doit rejoindre le fil des cartes ouvertes (§7).
                "diffuser": True,
            },
            self._contexte(user_input),
            file.put_nowait,
        )
        yield {"role": "assistant"}
        try:
            while True:
                trame = await asyncio.wait_for(file.get(), DELAI_TOUR)
                if erreur := trame.get("error"):
                    raise ErreurLuna(
                        erreur.get("code") or "internal",
                        erreur.get("message") or "Erreur inconnue.",
                    )
                genre = trame.get("event")
                if genre == "delta":
                    dit.append(trame["text"])
                    yield {"content": trame["text"]}
                elif genre == "done":
                    return
                elif genre == "error":
                    raise ErreurLuna(
                        trame.get("code") or "internal",
                        trame.get("message") or "Erreur inconnue.",
                    )
        except TimeoutError as err:
            raise ErreurLuna("internal", "Luna a mis trop de temps à répondre.") from err
        finally:
            arreter()

    def _contexte(self, user_input: conversation.ConversationInput) -> dict[str, Any]:
        """Ce que sait Assist de la personne qui parle.

        En P2 l'identité n'existe pas encore : un satellite sans utilisateur
        authentifié n'est pas plus qu'un invité, et l'add-on lui appliquera son
        profil par défaut (H37). C'est P3 qui donnera une vraie voix à un nom.
        """
        utilisateur = getattr(user_input.context, "user_id", None)
        return {
            "ha_user_id": utilisateur,
            "ha_user_name": None,
            "is_admin": False,
            "profile": "unknown",
            "client_id": user_input.device_id or "assist",
            "local": True,
            "source": "voix",
        }
