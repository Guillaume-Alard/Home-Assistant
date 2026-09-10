"""Scénarios & routines (Phase 8) : garde-fou (jamais de sensible), détection
d'habitudes, exécution via le moteur, et le cycle proposer → activer → déclencher."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from conftest import PROTOCOLS_TEST_YML, make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.config import Settings
from app.ha.protocols import ProtocolBook
from app.routines import RoutineService, RoutineError, find_candidates, validate_steps
from app.routines.runner import RoutineRunner
from app.routines.safety import ROUTINE_SAFE_ACTIONS, signature
from app.store import Store


# ── Garde-fou : une routine ne contient JAMAIS d'action sensible ─────────────

def test_liste_blanche_exclut_le_sensible():
    for sensitive in ("ha.unlock", "ha.alarm_disarm", "ha.alarm_arm", "ha.call_service",
                      "protocol.run", "docker.restart"):
        assert sensitive not in ROUTINE_SAFE_ACTIONS


def test_validate_refuse_le_sensible_et_valide_le_courant():
    with pytest.raises(RoutineError):
        validate_steps([{"action_id": "ha.unlock", "params": {"entity_ids": ["lock.porte"]}}])
    with pytest.raises(RoutineError):
        validate_steps([{"action_id": "ha.call_service", "params": {"domain": "x", "service": "y"}}])
    # Mauvais domaine pour l'action
    with pytest.raises(RoutineError):
        validate_steps([{"action_id": "ha.cover", "params": {"op": "close", "entity_ids": ["light.x"]}}])
    # Cas valide, avec libellé lisible
    steps = validate_steps(
        [{"action_id": "ha.turn_off", "params": {"entity_ids": ["light.salon"]}}],
        {"light.salon": "Plafonnier salon"},
    )
    assert steps[0]["label"] == "Éteindre Plafonnier salon"


def test_signature_ignore_l_ordre():
    a = [{"action_id": "ha.turn_off", "params": {"entity_ids": ["light.salon"]}},
         {"action_id": "ha.cover", "params": {"op": "close", "entity_ids": ["cover.salon"]}}]
    b = list(reversed(a))
    assert signature(validate_steps(a)) == signature(validate_steps(b))


# ── Détection d'habitudes ────────────────────────────────────────────────────

def _journal_rows(days, hour, actions):
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    rows = []
    for d in range(1, days + 1):
        base = (now - timedelta(days=d)).replace(hour=hour, minute=5)
        for i, (action_id, params) in enumerate(actions):
            import json
            rows.append({
                "ts": (base + timedelta(minutes=i * 3)).isoformat(),
                "action_id": action_id, "outcome": "ok",
                "params": json.dumps(params),
            })
    return rows, now


def test_habitude_detectee_sur_plusieurs_jours():
    rows, now = _journal_rows(4, 23, [
        ("ha.cover", {"op": "close", "entity_ids": ["cover.salon"]}),
        ("ha.turn_off", {"entity_ids": ["light.salon"]}),
    ])
    cands = find_candidates(rows, now=now, min_days=3, lookback_days=21)
    assert len(cands) == 1
    c = cands[0]
    assert c.name == "Bonne nuit" and c.days == 4 and len(c.steps) == 2


def test_pas_d_habitude_sous_le_seuil():
    rows, now = _journal_rows(2, 23, [  # 2 jours seulement
        ("ha.cover", {"op": "close", "entity_ids": ["cover.salon"]}),
        ("ha.turn_off", {"entity_ids": ["light.salon"]}),
    ])
    assert find_candidates(rows, now=now, min_days=3) == []


def test_action_isolee_pas_une_routine():
    rows, now = _journal_rows(5, 7, [("ha.turn_on", {"entity_ids": ["light.cuisine"]})])  # une seule action
    assert find_candidates(rows, now=now, min_days=3) == []


# ── Service : propose → active → déclenche (via le moteur) ────────────────────

@pytest.fixture()
async def svc(tmp_path):
    ha, calls = make_ha_stub()
    proto = tmp_path / "protocols.yml"
    proto.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    store = Store(tmp_path / "routines.db")
    await store.open()
    engine = ActionEngine(build_registry(ha, ProtocolBook.load(proto)), store)
    changes: list[int] = []

    async def announce(text, speak):
        pass

    async def on_change():
        changes.append(1)

    service = RoutineService(Settings.from_env(), ha, engine, store, announce=announce, on_change=on_change)
    from types import SimpleNamespace
    yield SimpleNamespace(service=service, store=store, ha=ha, calls=calls, changes=changes)
    await store.close()


async def test_propose_refuse_le_sensible(svc):
    with pytest.raises(RoutineError):
        await svc.service.propose(
            name="Danger", steps=[{"action_id": "ha.unlock", "params": {"entity_ids": ["lock.porte"]}}],
        )
    assert await svc.store.list_routines() == []


async def test_cycle_proposer_activer_declencher(svc):
    routine = await svc.service.propose(
        name="Bonne nuit",
        steps=[
            {"action_id": "ha.turn_off", "params": {"entity_ids": ["light.salon"]}},
            {"action_id": "ha.cover", "params": {"op": "close", "entity_ids": ["cover.salon"]}},
        ],
        description="Fermer + éteindre",
    )
    assert routine["status"] == "proposed"

    # Tant qu'elle n'est pas active, on ne la déclenche pas
    text, ok = await svc.service.run_by_name("Bonne nuit", utterance="x", source="voice")
    assert not ok and "active" in text.lower()
    assert svc.calls == []  # RIEN n'a touché Nova

    # Guillaume active (revue humaine), puis déclenche
    await svc.service.approve(routine["id"])
    text, ok = await svc.service.run_by_name("bonne nuit", utterance="lance", source="voice")
    assert ok
    # Les DEUX étapes sont bien parties vers Nova, via le moteur
    services = {(c[0], c[1]) for c in svc.calls}
    assert ("homeassistant", "turn_off") in services and ("cover", "close_cover") in services
    updated = await svc.store.get_routine(routine["id"])
    assert updated["run_count"] == 1 and updated["last_run_at"]


async def test_scan_habits_propose_puis_dedoublonne(svc):
    # Journal multi-jours inséré directement (ts contrôlés)
    import json
    now = datetime.now(timezone.utc)
    for d in range(1, 5):
        base = (now - timedelta(days=d)).replace(hour=23, minute=5)
        for i, (aid, params) in enumerate([
            ("ha.cover", {"op": "close", "entity_ids": ["cover.salon"]}),
            ("ha.turn_off", {"entity_ids": ["light.salon"]}),
        ]):
            await svc.store._db.execute(
                "INSERT INTO journal (ts, kind, actor, action_id, params, authorization, outcome, detail)"
                " VALUES (?, 'direct', 'guillaume', ?, ?, 'ordre', 'ok', '')",
                ((base + timedelta(minutes=i * 3)).isoformat(), aid, json.dumps(params)),
            )
    await svc.store._db.commit()

    n = await svc.service.scan_habits()
    assert n == 1
    proposed = await svc.store.list_routines("proposed")
    assert len(proposed) == 1 and proposed[0]["source"] == "appris"

    # Rescanner ne reproposera pas la même habitude
    assert await svc.service.scan_habits() == 0
    assert len(await svc.store.list_routines("proposed")) == 1
