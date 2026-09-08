"""`binary_sensor.luna_en_ligne` — l'état du cerveau, lisible sans aller-retour.

La carte Loggia le lit dans `hass.states` pour décider du mode dégradé (§8) :
c'est instantané, alors qu'un `luna/info` coûte un aller-retour et échoue
justement quand l'add-on est absent.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import ClientRelais
from .const import DOMAINE, SIGNAL_STATUT


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    client: ClientRelais = hass.data[DOMAINE][entry.entry_id]
    async_add_entities([CapteurEnLigne(entry, client)])


class CapteurEnLigne(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "En ligne"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, client: ClientRelais) -> None:
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_en_ligne"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAINE, entry.entry_id)},
            name="Luna",
            manufacturer="Alardware",
            model="Add-on Luna",
        )

    @property
    def is_on(self) -> bool:
        return self._client.connecte

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_STATUT}_{self._entry.entry_id}",
                self._sur_statut,
            )
        )

    @callback
    def _sur_statut(self, _connecte: bool) -> None:
        self.async_write_ha_state()
