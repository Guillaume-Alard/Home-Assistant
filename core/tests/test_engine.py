"""Le moteur « propose puis approuve » : refus techniques, confirmation, propositions."""

from __future__ import annotations

import time

import pytest

from app.actions.engine import ActionEngine
from app.actions.registry import ActionError, ActionRegistry, ActionSpec
from app.store import Store


class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def executor(self, action_id: str, result: str = "fait"):
        async def run(params: dict) -> str:
            self.calls.append((action_id, params))
            return result
        return run


@pytest.fixture()
async def env(tmp_path):
    store = Store(tmp_path / "engine.db")
    await store.open()
    rec = Recorder()
    reg = ActionRegistry()
    reg.register(ActionSpec("test.low", "action banale", "low", True, rec.executor("test.low", "Lumière allumée.")))
    reg.register(ActionSpec("test.sensitive", "action sensible", "sensitive", True, rec.executor("test.sensitive", "Alarme désarmée.")))
    reg.register(ActionSpec("test.proposal_only", "réservée aux propositions", "medium", False, rec.executor("test.proposal_only", "Service appelé.")))

    async def failing(params):
        raise ActionError("Ça a cassé.")
    reg.register(ActionSpec("test.failing", "échoue toujours", "low", True, failing))

    changes: list[tuple[str, dict]] = []

    async def on_change(kind, proposal):
        changes.append((kind, proposal))

    engine = ActionEngine(reg, store, on_proposal_change=on_change)
    yield type("Env", (), {"store": store, "rec": rec, "engine": engine, "changes": changes})()
    await store.close()


async def test_ordre_direct_whiteliste(env):
    outcome = await env.engine.run_direct(
        "test.low", {"x": 1}, utterance="allume la lumière", source="voice"
    )
    assert outcome.ok and outcome.text == "Lumière allumée."
    assert env.rec.calls == [("test.low", {"x": 1})]
    journal = await env.store.list_journal()
    assert journal[0]["outcome"] == "ok"
    assert "allume la lumière" in journal[0]["authorization"]


async def test_refus_action_inconnue_et_hors_liste(env):
    unknown = await env.engine.run_direct("test.nexiste_pas", {}, utterance="x", source="text")
    assert unknown.status == "refused"

    blocked = await env.engine.run_direct("test.proposal_only", {}, utterance="x", source="text")
    assert blocked.status == "refused"
    assert "proposition" in blocked.text
    assert env.rec.calls == []  # rien n'a été exécuté

    journal = await env.store.list_journal()
    assert all(e["outcome"] == "refused" for e in journal[:2])


async def test_sensible_double_confirmation(env):
    ask = await env.engine.run_direct("test.sensitive", {}, utterance="désarme l'alarme", source="voice")
    assert ask.status == "needs_confirmation"
    assert env.rec.calls == []  # pas d'exécution avant confirmation

    done = await env.engine.confirm_pending(source="voice")
    assert done.ok and done.text == "Alarme désarmée."
    assert env.rec.calls == [("test.sensitive", {})]

    # Une seconde confirmation ne rejoue rien
    again = await env.engine.confirm_pending(source="voice")
    assert again.status == "refused"


async def test_confirmation_expiree_et_annulation(env):
    await env.engine.run_direct("test.sensitive", {}, utterance="désarme", source="voice")
    env.engine._pending_confirm.expires_at = time.monotonic() - 1
    expired = await env.engine.confirm_pending(source="voice")
    assert expired.status == "refused" and "expiré" in expired.text
    assert env.rec.calls == []

    await env.engine.run_direct("test.sensitive", {}, utterance="désarme", source="voice")
    assert env.engine.cancel_pending() is True
    cancelled = await env.engine.confirm_pending(source="voice")
    assert cancelled.status == "refused"
    assert env.rec.calls == []


async def test_proposition_cycle_complet(env):
    proposal, msg = await env.engine.propose(
        title="Redémarrer un service", justification="test", risk="medium",
        action_id="test.proposal_only", params={"service": "x"},
    )
    assert proposal["status"] == "pending" and "n°" in msg
    assert env.rec.calls == []  # une proposition ne s'exécute pas à la création
    assert env.changes[0][0] == "new"

    rejected, msg = await env.engine.decide(proposal["num"], "reject", via="ui")
    assert rejected["status"] == "rejected"
    assert env.rec.calls == []  # refusée → jamais exécutée

    # Une proposition déjà décidée ne se rejoue pas
    _, msg = await env.engine.decide(proposal["num"], "approve", via="ui")
    assert "déjà" in msg
    assert env.rec.calls == []


async def test_proposition_approuvee_est_executee(env):
    proposal, _ = await env.engine.propose(
        title="Appeler un service", justification="test", risk="low",
        action_id="test.proposal_only", params={"y": 2},
    )
    updated, msg = await env.engine.decide(proposal["num"], "approve", via="ui")
    assert updated["status"] == "done"
    assert updated["result"] == "Service appelé."
    assert env.rec.calls == [("test.proposal_only", {"y": 2})]
    assert "exécutée" in msg


async def test_proposition_sensible_pas_approuvable_a_la_voix(env):
    proposal, _ = await env.engine.propose(
        title="Ouvrir un accès", justification="test", risk="sensible-typo",  # risque invalide → retombe sur le registre
        action_id="test.sensitive", params={},
    )
    assert proposal["risk"] == "sensitive"  # jamais plus faible que le registre

    p, msg = await env.engine.decide(proposal["num"], "approve", via="voice")
    assert p["status"] == "pending" and "interface" in msg
    assert env.rec.calls == []

    p, _ = await env.engine.decide(proposal["num"], "approve", via="ui")
    assert p["status"] == "done"


async def test_proposition_reportee_reste_decidable(env):
    proposal, _ = await env.engine.propose(
        title="Plus tard", justification="test", action_id="test.proposal_only",
    )
    p, _ = await env.engine.decide(proposal["num"], "defer", via="ui")
    assert p["status"] == "deferred"
    p, _ = await env.engine.decide(proposal["num"], "approve", via="ui")
    assert p["status"] == "done"


async def test_echec_execution_journalise(env):
    outcome = await env.engine.run_direct("test.failing", {}, utterance="x", source="text")
    assert outcome.status == "failed" and outcome.text == "Ça a cassé."
    journal = await env.store.list_journal()
    assert journal[0]["outcome"] == "failed"


async def test_chemin_systeme_risque_faible_uniquement(env):
    ok = await env.engine.run_system("test.low", {}, authorization="règle « test »")
    assert ok.ok

    blocked = await env.engine.run_system("test.sensitive", {}, authorization="règle « x »")
    assert blocked.status == "refused"
    assert [c[0] for c in env.rec.calls] == ["test.low"]
