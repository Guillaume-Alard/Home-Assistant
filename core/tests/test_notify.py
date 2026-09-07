"""Notifications mobiles (Phase 14) : Luna te joint sur le téléphone.

La poussée est de la COMMUNICATION (service notify de Nova), jamais du pilotage :
elle passe par le moteur d'actions (chemin système, risque faible), donc par
l'exécuteur — l'invariant reste intact. Ici on prouve le chemin et les garde-fous.
"""

from __future__ import annotations

import pytest
from conftest import make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.notify import Notifier
from app.store import Store


@pytest.fixture()
async def engine_ha(tmp_path):
    ha, calls = make_ha_stub()
    store = Store(tmp_path / "n.db")
    await store.open()
    engine = ActionEngine(build_registry(ha), store)
    yield engine, calls, store
    await store.close()


async def test_push_passe_par_le_service_notify(engine_ha):
    engine, calls, store = engine_ha
    notifier = Notifier(engine, "mobile_app_pixel")
    assert notifier.enabled
    assert await notifier.push("Porte ouverte la nuit", title="⚠️ Sécurité") is True
    # Un seul appel, vers le service notify — aucun pilotage de la maison.
    assert calls == [("notify", "mobile_app_pixel",
                      {"message": "Porte ouverte la nuit", "title": "⚠️ Sécurité"}, None)]
    # La poussée est journalisée (chemin système du moteur).
    journal = await store.list_journal()
    assert any(e["action_id"] == "ha.notify" for e in journal)


async def test_push_sans_titre(engine_ha):
    engine, calls, _ = engine_ha
    await Notifier(engine, "mobile_app_pixel").push("Rappel")
    assert calls == [("notify", "mobile_app_pixel", {"message": "Rappel"}, None)]


async def test_push_sans_service_ne_fait_rien(engine_ha):
    engine, calls, _ = engine_ha
    notifier = Notifier(engine, "")
    assert notifier.enabled is False
    assert await notifier.push("x") is False
    assert calls == []


async def test_push_sans_moteur_ne_fait_rien():
    notifier = Notifier(None, "mobile_app_pixel")
    assert notifier.enabled is False
    assert await notifier.push("x") is False


async def test_push_message_vide_ignore(engine_ha):
    engine, calls, _ = engine_ha
    assert await Notifier(engine, "mobile_app_pixel").push("   ") is False
    assert calls == []


async def test_bascules_par_defaut():
    n = Notifier(object(), "svc")
    assert n.reminders and n.alerts and not n.briefing


# ── La proactivité pousse les alertes de sécurité, pas les simples constats ───

async def test_proactif_pousse_les_alertes_de_securite(tmp_path):
    from app.config import Settings
    from app.proactive.engine import ProactiveEngine
    from app.proactive.rules import ProactiveConfig, Suggestion

    ha, _calls = make_ha_stub()
    store = Store(tmp_path / "p.db")
    await store.open()
    engine = ActionEngine(build_registry(ha), store)
    pushed: list[tuple[str, str]] = []

    async def notify(title, detail):
        pushed.append((title, detail))

    async def _noop(*a):
        pass

    pe = ProactiveEngine(Settings.from_env(), ha, engine, store,
                         say=_noop, on_change=_noop, config=ProactiveConfig(), notify=notify)
    # Alerte de sécurité (warning) → poussée sur le téléphone.
    await pe._surface(Suggestion(key="k1", rule="ouverture_nuit", title="Porte ouverte",
                                 detail="La porte d'entrée est ouverte.", severity="warning",
                                 category="securite"), hour=2)
    # Simple constat (info) → reste au cockpit, aucune poussée.
    await pe._surface(Suggestion(key="k2", rule="lumiere_tard", title="Lumières",
                                 detail="Des lumières sont allumées.", severity="info",
                                 category="energie"), hour=2)
    assert pushed == [("Porte ouverte", "La porte d'entrée est ouverte.")]
    await store.close()


def test_config_notify(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("SENTINEL_NOTIFY_SERVICE", "mobile_app_pixel")
    monkeypatch.setenv("SENTINEL_NOTIFY_BRIEFING", "1")
    monkeypatch.setenv("SENTINEL_NOTIFY_ALERTS", "off")
    s = Settings.from_env()
    assert s.notify_enabled and s.notify_service == "mobile_app_pixel"
    assert s.notify_reminders is True and s.notify_alerts is False and s.notify_briefing is True
