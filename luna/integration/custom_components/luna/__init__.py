"""Luna — l'intégration.

Le nerf, pas le cerveau. Elle fait exactement trois choses :

1. tenir une connexion au relais de l'add-on ;
2. enregistrer les commandes WebSocket `luna/*` que la carte Loggia appelle ;
3. exposer `binary_sensor.luna_en_ligne`, pour que la carte détecte le mode
   dégradé sans le moindre aller-retour.

Aucune logique métier ici. Si une règle de §9 se retrouve un jour dans ce
fichier, c'est que quelque chose a dérivé.

Pourquoi cet artefact existe (décision A1) : une carte Lovelace ne peut parler
qu'à Home Assistant. Pour qu'un message `luna/…` existe sur l'API WebSocket de
HA, il faut appeler `websocket_api.async_register_command` **dans le processus
Python de Home Assistant** — ce qu'un add-on, conteneur séparé, ne peut pas
faire.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .client import ClientRelais
from .const import CONF_HOTE, CONF_PORT, CONF_SECRET, DOMAINE, SIGNAL_STATUT
from .websocket import enregistrer_commandes

_LOGGER = logging.getLogger(__name__)

PLATEFORMES = [Platform.BINARY_SENSOR, Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    async def sur_statut(connecte: bool) -> None:
        async_dispatcher_send(hass, f"{SIGNAL_STATUT}_{entry.entry_id}", connecte)

    client = ClientRelais(
        async_get_clientsession(hass),
        entry.data[CONF_HOTE],
        entry.data[CONF_PORT],
        entry.data[CONF_SECRET],
        sur_statut=sur_statut,
    )
    await client.demarrer()

    hass.data.setdefault(DOMAINE, {})[entry.entry_id] = client
    enregistrer_commandes(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATEFORMES)
    _LOGGER.info("Luna : relais vers %s:%s", entry.data[CONF_HOTE], entry.data[CONF_PORT])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    decharge = await hass.config_entries.async_unload_platforms(entry, PLATEFORMES)
    if decharge:
        client: ClientRelais = hass.data[DOMAINE].pop(entry.entry_id)
        await client.fermer()
    return decharge


def client_actif(hass: HomeAssistant) -> ClientRelais:
    """Le client de la seule entrée configurée.

    Luna n'a qu'un cerveau : une seule entrée de configuration a du sens.
    """
    clients = list(hass.data.get(DOMAINE, {}).values())
    if not clients:
        raise ConfigEntryNotReady("L'intégration Luna n'est pas configurée.")
    return clients[0]
