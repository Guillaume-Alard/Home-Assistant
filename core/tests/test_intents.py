"""Intents locaux : grammaire FR, résolution des pièces, chemin moteur complet."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import PROTOCOLS_TEST_YML, make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.brain.intents import LocalIntents
from app.ha.protocols import ProtocolBook
from app.store import Store


@pytest.fixture()
async def home(tmp_path):
    ha, calls = make_ha_stub()
    proto_path = tmp_path / "protocols.yml"
    proto_path.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    protocols = ProtocolBook.load(proto_path)
    store = Store(tmp_path / "intents.db")
    await store.open()
    engine = ActionEngine(build_registry(ha, protocols), store)
    intents = LocalIntents(ha, engine, protocols, store, None, "Europe/Paris")
    yield SimpleNamespace(
        ha=ha, calls=calls, engine=engine, intents=intents, store=store
    )
    await store.close()


async def test_allumer_lumiere_du_salon(home):
    reply = await home.intents.handle("Allume la lumière du salon", "voice")
    assert reply == "Allumé : Plafonnier salon."
    assert home.calls == [
        ("homeassistant", "turn_on", None, {"entity_id": ["light.salon"]})
    ]


async def test_eteindre_tout(home):
    reply = await home.intents.handle("Éteins tout", "voice")
    assert reply.startswith("Éteint :")
    domain, service, _, target = home.calls[0]
    assert (domain, service) == ("homeassistant", "turn_off")
    assert set(target["entity_id"]) == {"light.salon", "light.chambre"}


async def test_lumiere_sans_piece_demande_precision(home):
    reply = await home.intents.handle("allume la lumière", "voice")
    assert "Précise la pièce" in reply
    assert home.calls == []


async def test_piece_sans_lumiere(home):
    home.ha._entity_area.pop("light.chambre")
    reply = await home.intents.handle("allume la lumière de la chambre", "voice")
    assert "Je ne trouve pas de lumière" in reply
    assert home.calls == []


async def test_fermer_les_volets(home):
    reply = await home.intents.handle("Ferme les volets", "voice")
    assert reply.startswith("Fermeture :")
    assert home.calls[0][:2] == ("cover", "close_cover")
    assert home.calls[0][3] == {"entity_id": ["cover.salon"]}


async def test_temperature_du_salon(home):
    reply = await home.intents.handle("Quelle est la température du salon ?", "voice")
    assert reply == "Il fait 21,5 degrés dans « Salon »."
    assert home.calls == []  # lecture pure


async def test_temperature_piece_sans_capteur(home):
    reply = await home.intents.handle("il fait combien dans la chambre", "voice")
    assert "Je ne trouve pas de capteur" in reply


async def test_protocole_simple(home):
    reply = await home.intents.handle("Sentinel, protocole test", "voice")
    assert reply == "Protocole de test exécuté."
    assert home.calls[0][:2] == ("persistent_notification", "create")


async def test_protocole_sensible_exige_confirmation(home):
    ask = await home.intents.handle("protocole verrou", "voice")
    assert "confirme" in ask.lower()
    assert home.calls == []  # rien avant la confirmation

    done = await home.intents.handle("confirme", "voice")
    assert done == "Verrouillage général effectué."
    assert home.calls[0][:2] == ("lock", "lock")


async def test_deverrouillage_sensible_puis_annulation(home):
    ask = await home.intents.handle("déverrouille la porte", "voice")
    assert "sensible" in ask.lower()
    cancel = await home.intents.handle("annule", "voice")
    assert cancel == "D'accord, j'annule."
    again = await home.intents.handle("confirme", "voice")
    assert "aucune action" in again.lower()
    assert home.calls == []


async def test_heure_locale_sans_reseau(home):
    home.ha.connected = False  # même hors ligne
    reply = await home.intents.handle("quelle heure est-il ?", "voice")
    assert reply.startswith("Il est ")


async def test_nova_deconnectee(home):
    home.ha.connected = False
    reply = await home.intents.handle("allume la lumière du salon", "voice")
    assert "injoignable" in reply


async def test_propositions_a_la_voix(home):
    proposal, _ = await home.engine.propose(
        title="Tester", justification="x", risk="low",
        action_id="ha.call_service",
        params={"domain": "persistent_notification", "service": "create", "data": {"message": "y"}},
    )
    listing = await home.intents.handle("liste les propositions", "voice")
    assert f"n°{proposal['num']}" in listing

    decided = await home.intents.handle(f"approuve la proposition {proposal['num']}", "voice")
    assert "exécutée" in decided
    assert home.calls[-1][:2] == ("persistent_notification", "create")


async def test_proposition_sensible_refusee_a_la_voix(home):
    proposal, _ = await home.engine.propose(
        title="Déverrouiller", justification="x", risk="sensitive",
        action_id="ha.unlock", params={"entity_ids": ["lock.entree"]},
    )
    reply = await home.intents.handle(f"approuve la proposition {proposal['num']}", "voice")
    assert "interface" in reply
    assert home.calls == []


async def test_phrase_ordinaire_part_au_llm(home):
    assert await home.intents.handle("Raconte-moi une histoire", "voice") is None
    assert await home.intents.handle("allume la télévision de mamie", "voice") is None


async def test_verbe_et_piece_sans_mot_lumiere(home):
    # « allume le salon » = lumières ; « coupe la musique dans le salon » = LLM
    reply = await home.intents.handle("allume le salon", "voice")
    assert reply == "Allumé : Plafonnier salon."

    assert await home.intents.handle("coupe la musique dans le salon", "voice") is None
    assert len(home.calls) == 1  # seul le premier ordre a agi


async def test_la_piece_prime_sur_toutes(home):
    reply = await home.intents.handle("Éteins toutes les lumières de la chambre", "voice")
    assert reply == "Éteint : Lampe chambre."
    assert home.calls[0][3] == {"entity_id": ["light.chambre"]}


async def test_regler_la_temperature_part_au_llm(home):
    assert await home.intents.handle("Mets la température du salon à 21", "voice") is None
    assert await home.intents.handle("baisse la température de la chambre", "voice") is None
    assert await home.intents.handle("quelle température fait-il dehors ?", "voice") is None
    assert home.calls == []
