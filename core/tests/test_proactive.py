"""Proactivité contextuelle (Phase 7) : les règles observent bien, et le veilleur
SUGGÈRE sans jamais exécuter — au mieux il crée une proposition à approuver."""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import PROTOCOLS_TEST_YML, make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.config import Settings
from app.ha.protocols import ProtocolBook
from app.proactive import ProactiveEngine
from app.proactive.rules import Context, ProactiveConfig, evaluate_all
from app.store import Store

NIGHT = datetime(2026, 9, 7, 23, 15)
DAY = datetime(2026, 9, 7, 14, 0)

_SNAP = {
    "cover.garage": {"state": "open", "attributes": {"friendly_name": "Porte de garage", "device_class": "garage"}},
    "binary_sensor.porte": {"state": "on", "attributes": {"friendly_name": "Porte d'entrée", "device_class": "door"}},
    "binary_sensor.fenetre_salon": {"state": "on", "attributes": {"friendly_name": "Fenêtre salon", "device_class": "window"}},
    "light.salon": {"state": "on", "attributes": {"friendly_name": "Plafonnier salon"}},
    "climate.salon": {"state": "heat", "attributes": {"friendly_name": "Chauffage salon", "hvac_action": "heating"}},
    "person.g": {"state": "home", "attributes": {}},
    "sensor.chambre": {"state": "27", "attributes": {"friendly_name": "Temp chambre", "device_class": "temperature"}},
}
_AREAS = {"light.salon": "Salon", "climate.salon": "Salon", "binary_sensor.fenetre_salon": "Salon",
          "cover.garage": "Garage", "binary_sensor.porte": "Entrée", "sensor.chambre": "Chambre"}


def _ctx(now, snap=None):
    return Context(now=now, snapshot=snap or _SNAP, area_labels=_AREAS)


# ── Règles (contextes déterministes) ─────────────────────────────────────────

def test_regles_de_nuit_se_declenchent_le_soir():
    rules = {s.rule for s in evaluate_all(_ctx(NIGHT), ProactiveConfig())}
    assert {"ouverture_nuit", "volet_nuit", "fenetre_chauffage", "temperature"} <= rules
    # volet_nuit porte une action de fermeture ; les alertes sécurité d'abord
    subs = evaluate_all(_ctx(NIGHT), ProactiveConfig())
    volet = next(s for s in subs if s.rule == "volet_nuit")
    assert volet.severity == "warning" and volet.action["action_id"] == "ha.cover"
    assert subs[0].severity == "warning"


def test_regles_de_nuit_muettes_le_jour():
    rules = {s.rule for s in evaluate_all(_ctx(DAY), ProactiveConfig())}
    assert "volet_nuit" not in rules and "ouverture_nuit" not in rules
    # fenêtre+chauffage et température ne sont pas liées à l'heure
    assert "fenetre_chauffage" in rules and "temperature" in rules


def test_absence_appareils_selon_presence():
    home = {**_SNAP, "person.g": {"state": "home", "attributes": {}}}
    away = {**_SNAP, "person.g": {"state": "not_home", "attributes": {}}}
    assert not any(s.rule == "absence_appareils" for s in evaluate_all(_ctx(DAY, home), ProactiveConfig()))
    away_rules = evaluate_all(_ctx(DAY, away), ProactiveConfig())
    absence = next(s for s in away_rules if s.rule == "absence_appareils")
    assert absence.action["action_id"] == "ha.turn_off" and "light.salon" in absence.action["params"]["entity_ids"]


def test_lumiere_tard_agrege_et_propose():
    subs = evaluate_all(_ctx(NIGHT), ProactiveConfig())
    lum = next(s for s in subs if s.rule == "lumiere_tard")
    assert lum.action["action_id"] == "ha.turn_off" and lum.severity == "info"


def test_mute_retire_une_regle():
    rules = {s.rule for s in evaluate_all(_ctx(NIGHT), ProactiveConfig(), muted={"volet_nuit"})}
    assert "volet_nuit" not in rules and "ouverture_nuit" in rules


def test_heures_calmes_et_liste_de_regles():
    cfg = ProactiveConfig(quiet_start=22, quiet_end=8)
    assert cfg.in_quiet_hours(23) and cfg.in_quiet_hours(3) and not cfg.in_quiet_hours(14)
    only = ProactiveConfig(enabled_rules=("temperature",))
    assert {s.rule for s in evaluate_all(_ctx(NIGHT), only)} == {"temperature"}


# ── Veilleur (surface, anti-répétition, escalade en proposition) ─────────────

@pytest.fixture()
async def eng(tmp_path):
    ha, calls = make_ha_stub()
    # Scénario NON lié à l'heure : personne à la maison + une lumière allumée.
    ha._states = {
        "person.g": {"state": "not_home", "attributes": {"friendly_name": "Guillaume"}},
        "light.salon": {"state": "on", "attributes": {"friendly_name": "Plafonnier salon"}},
    }
    ha._areas = {"area_salon": "Salon"}
    ha._entity_area = {"light.salon": "area_salon"}

    proto = tmp_path / "protocols.yml"
    proto.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    store = Store(tmp_path / "proactive.db")
    await store.open()
    engine = ActionEngine(build_registry(ha, ProtocolBook.load(proto), None), store)

    said: list[tuple[str, bool]] = []
    changes: list[int] = []

    async def say(text, speak):
        said.append((text, speak))

    async def on_change():
        changes.append(1)

    pe = ProactiveEngine(
        Settings.from_env(), ha, engine, store,
        say=say, on_change=on_change, config=ProactiveConfig(),
    )
    from types import SimpleNamespace
    yield SimpleNamespace(pe=pe, store=store, ha=ha, said=said, changes=changes, calls=calls)
    await store.close()


async def test_veilleur_surface_une_suggestion(eng):
    await eng.pe.evaluate()
    subs = await eng.store.list_proactive()
    absence = [s for s in subs if s["rule"] == "absence_appareils"]
    assert absence and absence[0]["status"] == "active"
    assert eng.said and eng.changes  # Luna a parlé + le cockpit a été rafraîchi


async def test_veilleur_anti_repetition(eng):
    await eng.pe.evaluate()
    await eng.pe.evaluate()  # même situation : pas de doublon
    absence = [s for s in await eng.store.list_proactive() if s["rule"] == "absence_appareils"]
    assert len(absence) == 1


async def test_suggestion_devient_proposition_jamais_executee(eng):
    await eng.pe.evaluate()
    sug = next(s for s in await eng.store.list_proactive() if s["rule"] == "absence_appareils")
    message = await eng.pe.make_proposal(sug["id"])
    assert "proposition" in message.lower()
    # Une PROPOSITION a été créée (à approuver), RIEN n'a été envoyé à Nova
    assert len(await eng.store.list_proposals("pending")) == 1
    assert eng.calls == []
    assert (await eng.store.get_proactive(sug["id"]))["status"] == "acted"


async def test_ne_plus_me_suggerer_ca(eng):
    await eng.pe.evaluate()
    await eng.pe.mute("absence_appareils")
    # la règle tue est écartée des suggestions actives…
    assert not [s for s in await eng.store.list_proactive() if s["rule"] == "absence_appareils"]
    # …et ne réapparaît pas à la prochaine évaluation
    await eng.pe.evaluate()
    assert not [s for s in await eng.store.list_proactive() if s["rule"] == "absence_appareils"]
    assert "absence_appareils" in await eng.store.list_proactive_mutes()


async def test_snooze_et_dismiss(eng):
    await eng.pe.evaluate()
    sug = next(s for s in await eng.store.list_proactive() if s["rule"] == "absence_appareils")
    await eng.pe.snooze(sug["id"], minutes=120)
    assert not await eng.store.list_proactive(("active",))  # plus visible en actif
    await eng.pe.dismiss(sug["id"])
    assert (await eng.store.get_proactive(sug["id"]))["status"] == "dismissed"
