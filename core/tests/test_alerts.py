"""Alertes proactives : déclenchement, condition, anti-rebond, notification."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.ha.alerts import AlertEngine, load_rules
from app.store import Store

ALERTS_YML = """\
- id: test
  nom: "Test"
  entites: [input_boolean.test_sentinel]
  vers: ["on"]
  gravite: info
  message: "Test : {friendly_name} activé."
  silence: 60

- id: intrusion
  motif: "binary_sensor.*porte*"
  vers: ["on"]
  condition: { entite: alarm_control_panel.maison, etats: [armed_away] }
  gravite: critique
  message: "Intrusion : {friendly_name} !"
  notifier: { service: notify.telephone, titre: "Sentinel" }
  silence: 30

- id: invalide-sans-message
  vers: ["on"]
  gravite: info
"""


def _evt(entity_id: str, new: str, old: str | None):
    return {
        "event_type": "state_changed",
        "data": {
            "entity_id": entity_id,
            "new_state": {"entity_id": entity_id, "state": new, "attributes": {}},
            "old_state": None if old is None else {"entity_id": entity_id, "state": old, "attributes": {}},
        },
    }


@pytest.fixture()
async def env(tmp_path):
    ha, calls = make_ha_stub()
    store = Store(tmp_path / "alerts.db")
    await store.open()
    engine = ActionEngine(build_registry(ha), store)
    announced: list[tuple[str, str, bool]] = []

    async def announce(text, severity, speak):
        announced.append((text, severity, speak))

    rules = load_rules(_write(tmp_path))
    alerts = AlertEngine(rules, ha, engine, announce)
    yield SimpleNamespace(
        ha=ha, calls=calls, alerts=alerts, announced=announced, store=store, rules=rules
    )
    await store.close()


def _write(tmp_path):
    path = tmp_path / "alerts.yml"
    path.write_text(ALERTS_YML, encoding="utf-8")
    return path


async def test_chargement_regles(env):
    assert [r.id for r in env.rules] == ["test", "intrusion"]  # l'invalide est écartée


async def test_declenchement_et_antirebond(env):
    await env.alerts.on_state_changed(_evt("input_boolean.test_sentinel", "on", "off"))
    assert env.announced == [("Test : Test Sentinel activé.", "info", True)]

    # Retombe puis remonte pendant le silence : pas de seconde annonce
    await env.alerts.on_state_changed(_evt("input_boolean.test_sentinel", "off", "on"))
    await env.alerts.on_state_changed(_evt("input_boolean.test_sentinel", "on", "off"))
    assert len(env.announced) == 1


async def test_changement_d_attribut_ignore(env):
    # old == new (mise à jour d'attribut) : pas une transition entrante
    await env.alerts.on_state_changed(_evt("input_boolean.test_sentinel", "on", "on"))
    assert env.announced == []


async def test_condition_alarme(env):
    event = _evt("binary_sensor.porte_entree", "on", "off")

    # Alarme désarmée → rien
    await env.alerts.on_state_changed(event)
    assert env.announced == []

    # Alarme armée → alerte critique + notification via le moteur (journalisée)
    env.ha._states["alarm_control_panel.maison"]["state"] = "armed_away"
    await env.alerts.on_state_changed(event)
    assert env.announced and env.announced[0][1] == "critical"
    assert env.calls[-1][:2] == ("notify", "telephone")
    assert env.calls[-1][2]["message"].startswith("Intrusion")

    journal = await env.store.list_journal()
    assert journal[0]["kind"] == "system"
    assert "règle d'alerte" in journal[0]["authorization"]


async def test_entite_hors_regle_ignoree(env):
    await env.alerts.on_state_changed(_evt("light.salon", "on", "off"))
    assert env.announced == []
