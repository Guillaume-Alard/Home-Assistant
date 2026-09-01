"""Client WebSocket Nova contre le faux serveur HA (vrai protocole)."""

from __future__ import annotations

import asyncio

from conftest import FAKE_HA_TOKEN

from app.ha.client import HAClient


async def _wait_for(predicate, timeout: float = 5.0):
    for _ in range(int(timeout / 0.05)):
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_connexion_registres_service_et_evenements(fake_ha):
    events: list[dict] = []
    statuses: list[bool] = []

    async def on_event(event):
        events.append(event)

    async def on_status(connected):
        statuses.append(connected)

    client = HAClient(
        f"http://127.0.0.1:{fake_ha.port}", FAKE_HA_TOKEN,
        on_event=on_event, on_status=on_status,
    )
    await client.start()
    try:
        assert await _wait_for(lambda: client.connected), "connexion au faux HA échouée"
        assert statuses == [True]

        # Registres : résolution de pièce en français
        area = client.find_area_in_text("allume la lumiere du salon")
        assert area is not None and area[1] == "Salon"
        assert client.entities_in_area(area[0], "light") == ["light.salon"]
        assert client.friendly_name("light.salon") == "Plafonnier salon"

        # Écriture (chemin exécuteurs) : reçue côté serveur
        await client.call_service("light", "turn_on", target={"entity_id": ["light.salon"]})
        assert fake_ha.calls[-1][:2] == ("light", "turn_on")

        # Événement poussé : cache mis à jour + callback appelé
        fake_ha.push_state_changed(
            "light.salon",
            {"entity_id": "light.salon", "state": "on", "attributes": {"friendly_name": "Plafonnier salon"}},
            {"entity_id": "light.salon", "state": "off", "attributes": {}},
        )
        assert await _wait_for(
            lambda: (client.get_state("light.salon") or {}).get("state") == "on"
        )
        assert any(e.get("event_type") == "state_changed" for e in events)
    finally:
        await client.stop()


async def test_jeton_refuse(fake_ha):
    client = HAClient(f"http://127.0.0.1:{fake_ha.port}", "mauvais-jeton")
    await client.start()
    try:
        await asyncio.sleep(0.6)
        assert client.connected is False
    finally:
        await client.stop()
