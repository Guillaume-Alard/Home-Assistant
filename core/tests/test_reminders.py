"""Minuteurs & rappels (Phase 10) : calcul d'échéance et déclenchement local."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.reminders import ReminderScheduler, compute_due_iso
from app.store import Store

PARIS = ZoneInfo("Europe/Paris")


# ── Calcul de l'échéance ─────────────────────────────────────────────────────

def test_compute_due_relatif_et_absolu():
    assert compute_due_iso(tz=PARIS, minutes=10) is not None
    assert compute_due_iso(tz=PARIS, dans_minutes=20) is not None
    # Une heure absolue dans le futur (naïve → interprétée en heure locale)
    future = (datetime.now(PARIS) + timedelta(hours=2)).replace(tzinfo=None).isoformat()
    assert compute_due_iso(tz=PARIS, a=future) is not None


def test_compute_due_refuse_passe_et_vide():
    assert compute_due_iso(tz=PARIS) is None                 # rien
    assert compute_due_iso(tz=PARIS, minutes=0) is None      # durée nulle
    past = (datetime.now(PARIS) - timedelta(hours=1)).isoformat()
    assert compute_due_iso(tz=PARIS, a=past) is None         # déjà passé
    assert compute_due_iso(tz=PARIS, a="pas une date") is None


# ── Déclenchement (tick) ─────────────────────────────────────────────────────

@pytest.fixture()
async def sched(tmp_path):
    store = Store(tmp_path / "rem.db")
    await store.open()
    said, fired, changes = [], [], []

    async def announce(text, speak):
        said.append((text, speak))

    async def on_change():
        changes.append(1)

    async def on_fire(r):
        fired.append(r)

    scheduler = ReminderScheduler(
        SimpleNamespace(reminders_enabled=True), store,
        announce=announce, on_change=on_change, on_fire=on_fire,
    )
    yield SimpleNamespace(s=scheduler, store=store, said=said, fired=fired, changes=changes)
    await store.close()


async def test_tick_declenche_les_echus_seulement(sched):
    now = datetime.now(timezone.utc)
    past = (now - timedelta(seconds=5)).isoformat(timespec="milliseconds")
    future = (now + timedelta(hours=1)).isoformat(timespec="milliseconds")
    await sched.store.add_reminder(kind="timer", label="pâtes", due_at=past)
    await sched.store.add_reminder(kind="reminder", label="plus tard", due_at=future)

    await sched.s.tick()

    # Le minuteur échu a sonné (voix + carillon) ; le futur reste actif
    assert sched.fired and sched.fired[0]["label"] == "pâtes"
    assert any("Minuteur terminé" in t and "pâtes" in t for t, _ in sched.said)
    actifs = await sched.store.list_reminders("active")
    assert [r["label"] for r in actifs] == ["plus tard"]

    # Un second tick ne re-déclenche pas ce qui a déjà sonné
    sched.fired.clear()
    await sched.s.tick()
    assert sched.fired == []
