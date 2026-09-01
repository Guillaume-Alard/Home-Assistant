"""Outils LLM : lecture libre, écriture uniquement à travers le moteur."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from conftest import PROTOCOLS_TEST_YML, make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.brain.toolbox import Toolbox
from app.ha.protocols import ProtocolBook
from app.store import Store


@pytest.fixture()
async def box(tmp_path):
    ha, calls = make_ha_stub()
    proto_path = tmp_path / "protocols.yml"
    proto_path.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    protocols = ProtocolBook.load(proto_path)
    store = Store(tmp_path / "toolbox.db")
    await store.open()
    engine = ActionEngine(build_registry(ha, protocols), store)
    toolbox = Toolbox(ha, engine, protocols, store)
    yield SimpleNamespace(ha=ha, calls=calls, toolbox=toolbox, store=store)
    await store.close()


async def _run(box, name, args):
    return await box.toolbox.run(name, args, utterance="demande de test", source="text")


async def test_specs_stables_et_completes(box):
    specs = box.toolbox.specs()
    names = [s["name"] for s in specs]
    assert names == [
        "etat_maison", "details_entite", "action_domotique", "lancer_protocole",
        "creer_proposition", "lister_propositions", "liste_pieces",
    ]
    assert all(s["description"] for s in specs)


async def test_etat_maison_par_zone(box):
    content, is_error = await _run(box, "etat_maison", {"zone": "salon"})
    assert not is_error
    data = json.loads(content)
    assert data["piece"] == "Salon"
    assert "light.salon" in data["entites"]


async def test_etat_maison_zone_inconnue(box):
    content, is_error = await _run(box, "etat_maison", {"zone": "grenier"})
    assert is_error
    assert "Salon" in content  # il suggère les pièces existantes


async def test_action_domotique_via_moteur(box):
    content, is_error = await _run(
        box, "action_domotique", {"operation": "allumer", "zone": "salon"}
    )
    assert not is_error and content == "Allumé : Plafonnier salon."
    assert box.calls == [("homeassistant", "turn_on", None, {"entity_id": ["light.salon"]})]

    journal = await box.store.list_journal()
    assert "via LLM" in journal[0]["authorization"] or "via LLM" in journal[0]["actor"]


async def test_le_deverrouillage_est_hors_de_portee_du_llm(box):
    content, is_error = await _run(
        box, "action_domotique", {"operation": "deverrouiller", "entity_ids": ["lock.entree"]}
    )
    assert is_error and "inconnue" in content.lower()
    assert box.calls == []


async def test_creer_proposition(box):
    content, is_error = await _run(box, "creer_proposition", {
        "titre": "Purger la base",
        "justification": "Elle grossit",
        "risque": "moyen",
        "rollback": "Aucun impact",
        "action": {"domain": "recorder", "service": "purge", "data": {"keep_days": 30}},
    })
    assert not is_error and "n°" in content
    assert box.calls == []  # rien d'exécuté à la création

    pending = await box.store.list_proposals("pending")
    assert pending[0]["title"] == "Purger la base"
    assert pending[0]["action_id"] == "ha.call_service"
    assert pending[0]["params"]["domain"] == "recorder"


async def test_lancer_protocole_inconnu(box):
    content, is_error = await _run(box, "lancer_protocole", {"nom": "apocalypse"})
    assert is_error and "Test" in content  # liste les protocoles réels


async def test_lancer_protocole_sensible_transmet_la_confirmation(box):
    content, is_error = await _run(box, "lancer_protocole", {"nom": "verrou"})
    assert not is_error  # needs_confirmation n'est pas une erreur : consigne à relayer
    assert "confirme" in content.lower()
    assert box.calls == []


async def test_outil_inconnu(box):
    content, is_error = await box.toolbox.run("hacker_le_pentagone", {}, utterance="", source="text")
    assert is_error
