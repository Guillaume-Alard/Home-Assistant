"""L1 — le client Home Assistant, contre un faux serveur WebSocket.

Hypothèse H19 : aucun appel réseau réel, aucune instance de Home Assistant en
CI. Le faux serveur rejoue le protocole WebSocket de HA — poignée de main,
registres, `state_changed`, `call_service` — sur un port local éphémère.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import WSMsgType, web

from luna.kernel.bus import Bus
from luna.kernel.errors import MaisonIndisponible
from luna.kernel.schemas import ActionHA, MaisonConnectee
from luna.providers.home import ClientMaison

ETATS = [
    {
        "entity_id": "light.salon_plafond",
        "state": "off",
        "attributes": {"friendly_name": "Plafond du salon"},
    },
    {
        "entity_id": "light.cuisine",
        "state": "on",
        "attributes": {"friendly_name": "Plafonnier cuisine"},
    },
    {
        "entity_id": "cover.baie_vitree",
        "state": "open",
        "attributes": {"friendly_name": "Baie vitrée", "device_class": "door"},
    },
    {
        "entity_id": "sensor.temperature_sejour",
        "state": "17.5",
        "attributes": {
            "friendly_name": "Température séjour",
            "unit_of_measurement": "°C",
        },
    },
]

PIECES = [
    {"area_id": "sejour", "name": "Séjour"},
    {"area_id": "cuisine", "name": "Cuisine"},
]
APPAREILS = [{"id": "dev-1", "area_id": "sejour"}]
ENTITES = [
    {"entity_id": "light.salon_plafond", "area_id": "sejour", "device_id": None},
    {"entity_id": "light.cuisine", "area_id": "cuisine", "device_id": None},
    {"entity_id": "cover.baie_vitree", "area_id": None, "device_id": "dev-1"},
    {"entity_id": "sensor.temperature_sejour", "area_id": "sejour", "device_id": None},
]


class FauxHomeAssistant:
    """Le protocole WebSocket de HA, réduit à ce dont Luna se sert."""

    def __init__(self, *, jeton_attendu: str = "jeton-valide") -> None:
        self.jeton_attendu = jeton_attendu
        self.services_appeles: list[dict[str, Any]] = []
        self.echec_service: str | None = None
        self._ws: web.WebSocketResponse | None = None
        self.pret = asyncio.Event()

    async def handler(self, requete: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(requete)
        self._ws = ws
        await ws.send_json({"type": "auth_required", "ha_version": "2026.9.0"})

        auth = await ws.receive_json()
        if auth.get("access_token") != self.jeton_attendu:
            await ws.send_json({"type": "auth_invalid"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2026.9.0"})

        async for message in ws:
            if message.type is not WSMsgType.TEXT:
                break
            await self._commande(ws, message.json())
        return ws

    async def _commande(self, ws: web.WebSocketResponse, trame: dict[str, Any]) -> None:
        identifiant, genre = trame.get("id"), trame.get("type")
        resultats = {
            "get_states": ETATS,
            "config/area_registry/list": PIECES,
            "config/device_registry/list": APPAREILS,
            "config/entity_registry/list": ENTITES,
        }
        if genre in resultats:
            await ws.send_json(
                {
                    "id": identifiant,
                    "type": "result",
                    "success": True,
                    "result": resultats[genre],
                }
            )
            if genre == "config/entity_registry/list":
                self.pret.set()
        elif genre == "subscribe_events":
            await ws.send_json(
                {"id": identifiant, "type": "result", "success": True, "result": None}
            )
        elif genre == "call_service":
            self.services_appeles.append(trame)
            if self.echec_service:
                await ws.send_json(
                    {
                        "id": identifiant,
                        "type": "result",
                        "success": False,
                        "error": {"code": "not_found", "message": self.echec_service},
                    }
                )
            else:
                await ws.send_json(
                    {"id": identifiant, "type": "result", "success": True, "result": None}
                )
        else:
            await ws.send_json(
                {
                    "id": identifiant,
                    "type": "result",
                    "success": False,
                    "error": {"message": f"type inconnu : {genre}"},
                }
            )

    async def pousser_etat(self, entity_id: str, etat: str) -> None:
        assert self._ws is not None
        await self._ws.send_json(
            {
                "id": 99,
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "data": {
                        "entity_id": entity_id,
                        "new_state": {
                            "entity_id": entity_id,
                            "state": etat,
                            "attributes": {"friendly_name": entity_id},
                        },
                    },
                },
            }
        )


@pytest.fixture
async def faux_ha():
    """Serveur monté à la main, sur la boucle du test.

    On n'utilise pas `pytest-aiohttp` : il installe ses propres fixtures de
    boucle, et le serveur se retrouve sur une autre boucle que le client — les
    deux s'attendent alors indéfiniment.
    """
    faux = FauxHomeAssistant()
    app = web.Application()
    app.router.add_get("/api/websocket", faux.handler)
    # `shutdown_timeout` court : sans lui, `cleanup()` attend jusqu'à 60 s la
    # fin du handler WebSocket, et un test d'arrêt devient un test de patience.
    coureur = web.AppRunner(app, shutdown_timeout=1.0)
    await coureur.setup()
    site = web.TCPSite(coureur, "127.0.0.1", 0)
    await site.start()
    faux.url = site.name.replace("http://", "ws://") + "/api/websocket"  # type: ignore[attr-defined]
    try:
        yield faux
    finally:
        await coureur.cleanup()


@pytest.fixture
async def client(faux_ha):
    bus = Bus()
    statuts: list[MaisonConnectee] = []
    bus.abonner(MaisonConnectee, lambda e: _noter(statuts, e))
    maison = ClientMaison(faux_ha.url, "jeton-valide", bus)
    await maison.demarrer()
    await asyncio.wait_for(faux_ha.pret.wait(), 5)
    await asyncio.sleep(0.05)
    maison.statuts = statuts  # type: ignore[attr-defined]
    yield maison
    await maison.fermer()


async def _noter(cible: list, evenement) -> None:
    cible.append(evenement)


class TestConnexion:
    async def test_authentification_et_registres(self, client):
        assert client.connectee is True
        assert client.version_ha == "2026.9.0"
        assert client.statuts[0].connectee is True

    async def test_jeton_refuse(self, faux_ha):
        bus = Bus()
        maison = ClientMaison(faux_ha.url, "mauvais-jeton", bus)
        await maison.demarrer()
        await asyncio.sleep(0.2)
        assert maison.connectee is False
        await maison.fermer()


class TestLecture:
    async def test_pieces_et_comptage(self, client):
        pieces = await client.pieces()
        noms = {p.nom: p for p in pieces}
        assert set(noms) == {"Séjour", "Cuisine"}
        assert noms["Séjour"].entites == {"light": 1, "cover": 1, "sensor": 1}

    async def test_lentite_herite_de_la_piece_de_son_appareil(self, client):
        """`cover.baie_vitree` n'a pas d'area propre : elle vient de `dev-1`."""
        assert client.nom_piece("cover.baie_vitree") == "Séjour"

    async def test_filtrage_par_piece_et_domaine(self, client):
        etats = await client.etats(piece="Séjour", domaine="light")
        assert [e.entity_id for e in etats] == ["light.salon_plafond"]

    async def test_unite_et_nom_convivial(self, client):
        etats = await client.etats(domaine="sensor")
        assert etats[0].nom == "Température séjour"
        assert etats[0].unite == "°C"
        assert etats[0].etat == "17.5"

    async def test_piece_inconnue_ne_rend_rien(self, client):
        assert await client.etats(piece="la véranda") == []

    async def test_le_cache_suit_les_changements_detat(self, client, faux_ha):
        avant = await client.etats(domaine="light")
        plafond = next(e for e in avant if e.entity_id == "light.salon_plafond")
        assert plafond.etat == "off"

        await faux_ha.pousser_etat("light.salon_plafond", "on")
        await asyncio.sleep(0.05)

        apres = await client.etats(domaine="light")
        assert next(e for e in apres if e.entity_id == "light.salon_plafond").etat == "on"


class TestResolution:
    async def test_identifiant_exact(self, client):
        assert await client.resoudre("light.cuisine", ("light",)) == ["light.cuisine"]

    async def test_identifiant_du_mauvais_domaine(self, client):
        assert await client.resoudre("cover.baie_vitree", ("light",)) == []

    async def test_par_piece_avec_article_et_accents(self, client):
        assert await client.resoudre("le Séjour", ("light",)) == ["light.salon_plafond"]
        assert await client.resoudre("sejour", ("light",)) == ["light.salon_plafond"]

    async def test_par_nom_convivial(self, client):
        assert await client.resoudre("plafonnier", ("light",)) == ["light.cuisine"]

    async def test_rien_ne_correspond(self, client):
        assert await client.resoudre("la véranda", ("light",)) == []


class TestEcriture:
    async def test_appel_de_service(self, client, faux_ha):
        await client.appeler_service(
            ActionHA(
                domain="light",
                service="turn_on",
                target={"entity_id": ["light.cuisine"]},
                data={"brightness_pct": 40},
            )
        )
        assert len(faux_ha.services_appeles) == 1
        appel = faux_ha.services_appeles[0]
        assert appel["domain"] == "light"
        assert appel["service"] == "turn_on"
        assert appel["target"] == {"entity_id": ["light.cuisine"]}
        assert appel["service_data"] == {"brightness_pct": 40}

    async def test_un_refus_de_ha_remonte_en_erreur_lisible(self, client, faux_ha):
        faux_ha.echec_service = "Entity not found"
        with pytest.raises(MaisonIndisponible) as info:
            await client.appeler_service(
                ActionHA(domain="light", service="turn_on", target={"entity_id": ["x.y"]})
            )
        assert "Entity not found" in info.value.message

    async def test_sans_connexion_lecriture_echoue_proprement(self, faux_ha):
        bus = Bus()
        maison = ClientMaison(faux_ha.url, "jeton-valide", bus)
        with pytest.raises(MaisonIndisponible):
            await maison.appeler_service(ActionHA(domain="light", service="turn_on"))


class TestArret:
    async def test_fermeture_propre(self, faux_ha):
        """L'add-on doit pouvoir s'arrêter : aucune tâche ne doit survivre."""
        maison = ClientMaison(faux_ha.url, "jeton-valide", Bus())
        await maison.demarrer()
        await asyncio.wait_for(faux_ha.pret.wait(), 5)
        await maison.fermer()

        assert maison.connectee is False
        restantes = [
            t
            for t in asyncio.all_tasks()
            if t.get_name().startswith("luna-maison") and not t.done()
        ]
        assert restantes == [], f"tâches encore vivantes : {restantes}"

    async def test_fermeture_sans_demarrage(self, faux_ha):
        maison = ClientMaison(faux_ha.url, "jeton-valide", Bus())
        await maison.fermer()
        assert maison.connectee is False
