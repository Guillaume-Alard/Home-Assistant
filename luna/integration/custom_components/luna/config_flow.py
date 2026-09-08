"""Formulaire de configuration : où joindre l'add-on, et avec quel secret."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import ClientRelais, ErreurLuna
from .const import CONF_HOTE, CONF_PORT, CONF_SECRET, DEFAUT_HOTE, DEFAUT_PORT, DOMAINE

_LOGGER = logging.getLogger(__name__)

#: On ne bloque pas le formulaire plus longtemps que ça.
DELAI_VERIFICATION = 10

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOTE, default=DEFAUT_HOTE): str,
        vol.Required(CONF_PORT, default=DEFAUT_PORT): int,
        vol.Required(CONF_SECRET): str,
    }
)


class LunaConfigFlow(ConfigFlow, domain=DOMAINE):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        erreurs: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(DOMAINE)
            self._abort_if_unique_id_configured()
            erreur = await self._verifier(user_input)
            if erreur is None:
                return self.async_create_entry(title="Luna", data=user_input)
            erreurs["base"] = erreur

        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=erreurs)

    async def _verifier(self, saisie: dict[str, Any]) -> str | None:
        """Ouvre vraiment le relais avant d'enregistrer.

        Une entrée qui s'enregistre sans avoir jamais joint l'add-on donne un
        `binary_sensor` éteint et aucune explication — exactement l'échec
        silencieux que §8 interdit.
        """
        client = ClientRelais(
            async_get_clientsession(self.hass),
            saisie[CONF_HOTE],
            saisie[CONF_PORT],
            saisie[CONF_SECRET],
        )
        try:
            await client.demarrer()
            async with asyncio.timeout(DELAI_VERIFICATION):
                await client.attendre_connexion()
            await client.demander("info", {}, {"client_id": "config_flow"})
        except (ErreurLuna, TimeoutError):
            return "injoignable"
        except Exception:
            _LOGGER.exception("Luna : vérification du relais impossible")
            return "inconnu"
        finally:
            await client.fermer()
        return None
