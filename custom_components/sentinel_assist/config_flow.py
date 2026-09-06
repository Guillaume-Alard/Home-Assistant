"""Écran de configuration : URL de Sentinel + jeton, avec test de connexion."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_BASE_URL, CONF_TOKEN, DEFAULT_BASE_URL, DOMAIN


class SentinelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Un seul écran : on saisit l'URL et le jeton, on vérifie, on enregistre."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL].rstrip("/")
            token = user_input[CONF_TOKEN].strip()
            # Certificat auto-signé de Sentinel → on ne vérifie pas le TLS.
            session = async_get_clientsession(self.hass, verify_ssl=False)
            try:
                async with session.get(
                    f"{base_url}/v1/models",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 401:
                        errors["base"] = "invalid_auth"
                    elif resp.status == 404:
                        # Route absente (sentinel-core pas à jour) OU endpoint
                        # désactivé (jeton non chargé) : les deux renvoient 404.
                        errors["base"] = "endpoint_missing"
                    elif resp.status != 200:
                        errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 — toute erreur réseau = injoignable
                errors["base"] = "cannot_connect"

            if not errors:
                await self.async_set_unique_id(base_url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Sentinel",
                    data={CONF_BASE_URL: base_url, CONF_TOKEN: token},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                vol.Required(CONF_TOKEN): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
