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


class DockerStub:
    """Moniteur Docker minimal pour les tests d'outils (lecture + restart tracé)."""

    def __init__(self):
        self.restarts: list[str] = []

    async def restart_container(self, name: str) -> str:
        self.restarts.append(name)
        return f"Conteneur {name} redémarré."

    async def logs(self, name: str, tail: int = 50) -> str:
        return f"[{name}] ligne de log 1\n[{name}] ligne de log 2"


@pytest.fixture()
async def box(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    from app.config import Settings
    from app.monitors.health import HealthService

    ha, calls = make_ha_stub()
    proto_path = tmp_path / "protocols.yml"
    proto_path.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    protocols = ProtocolBook.load(proto_path)
    store = Store(tmp_path / "toolbox.db")
    await store.open()
    docker = DockerStub()
    engine = ActionEngine(build_registry(ha, protocols, docker), store)
    health = HealthService(Settings.from_env(), ha)
    toolbox = Toolbox(ha, engine, protocols, store, health=health, docker=docker)
    yield SimpleNamespace(
        ha=ha, calls=calls, toolbox=toolbox, store=store, docker=docker, engine=engine
    )
    await store.close()


async def _run(box, name, args):
    return await box.toolbox.run(name, args, utterance="demande de test", source="text")


async def test_specs_stables_et_completes(box):
    specs = box.toolbox.specs()
    names = [s["name"] for s in specs]
    assert names == [
        "etat_maison", "details_entite", "action_domotique", "lancer_protocole",
        "creer_proposition", "lister_propositions", "liste_pieces",
        "sante_systemes", "logs_conteneur", "audit_systemes", "redemarrer_conteneur",
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


async def test_sante_et_logs(box):
    content, is_error = await _run(box, "sante_systemes", {})
    assert not is_error and '"nova"' in content

    content, is_error = await _run(box, "logs_conteneur", {"nom": "plex", "lignes": 10})
    assert not is_error and "ligne de log" in content


async def test_redemarrage_conteneur_est_une_proposition(box):
    content, is_error = await _run(box, "redemarrer_conteneur", {
        "nom": "plex", "justification": "Le conteneur ne répond plus.",
    })
    assert not is_error and "n°" in content
    assert box.docker.restarts == []  # RIEN n'a redémarré : proposition seulement

    pending = (await box.store.list_proposals("pending"))[0]
    assert pending["action_id"] == "docker.restart"
    assert pending["params"] == {"name": "plex"}

    # L'approbation exécute réellement le redémarrage, journalisé
    updated, msg = await box.engine.decide(pending["num"], "approve", via="ui")
    assert updated["status"] == "done"
    assert box.docker.restarts == ["plex"]


async def test_proposition_de_service_sensible_escaladee(box):
    """Un lock.unlock enveloppé dans une proposition « moyenne » devient sensible :
    impossible de l'approuver à la voix — le contournement est fermé."""
    content, is_error = await _run(box, "creer_proposition", {
        "titre": "Ouvrir la porte",
        "justification": "test",
        "risque": "moyen",  # le LLM minimise — le moteur ne le croit pas
        "action": {"domain": "lock", "service": "unlock",
                   "target": {"entity_id": ["lock.entree"]}},
    })
    assert not is_error and "sensible" in content

    pending = (await box.store.list_proposals("pending"))[0]
    assert pending["risk"] == "sensitive"

    from app.actions.engine import ActionEngine  # l'engine du fixture
    engine = box.toolbox._engine
    _, msg = await engine.decide(pending["num"], "approve", via="voice")
    assert "interface" in msg
    assert box.calls == []
