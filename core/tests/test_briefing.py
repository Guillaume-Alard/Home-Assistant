"""Briefing du matin (Phase 11) : composition à partir des sources locales."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.briefing import BriefingService
from app.store import Store


class _HA:
    connected = True

    def __init__(self, states):
        self._s = states
        self._area = {"light.salon": "a1", "binary_sensor.porte": "a2"}
        self._an = {"a1": "Salon", "a2": "Entrée"}

    def get_state(self, e):
        return self._s.get(e)

    def states_snapshot(self):
        return dict(self._s)

    def entity_area(self, e):
        return self._area.get(e)

    def area_name(self, a):
        return self._an.get(a, a)

    def entities_by_domain(self, d):
        return [e for e in self._s if e.split(".")[0] == d]


class _Health:
    async def audit(self):
        return {"constats": [{"gravite": "attention", "sujet": "Nova", "detail": "2 entités indisponibles"}]}


class _Mail:
    async def summary(self):
        return {"unread_total": 3, "messages": []}


@pytest.fixture()
async def store(tmp_path):
    s = Store(Path(tmp_path) / "b.db")
    await s.open()
    yield s
    await s.close()


async def test_brief_assemble_les_sources(store):
    ha = _HA({
        "weather.maison": {"state": "rainy", "attributes": {"temperature": 6}},
        "binary_sensor.porte": {"state": "on", "attributes": {"friendly_name": "Porte d'entrée", "device_class": "door"}},
        "light.salon": {"state": "on", "attributes": {"friendly_name": "Salon"}},
    })
    due = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    await store.add_reminder(kind="reminder", label="appeler le garage", due_at=due)
    settings = SimpleNamespace(tz="Europe/Paris", weather_entity="")
    brief = await BriefingService(settings, ha, _Health(), store, mail=_Mail()).compose(pending=2)

    assert "pluvieux" in brief and "6°" in brief          # météo
    assert "Porte d'entrée" in brief                      # maison (ouvert)
    assert "3 messages non lus" in brief                  # courriel
    assert "appeler le garage" in brief                   # rappel du jour
    assert "Nova" in brief                                # santé (constat)
    assert "2 proposition" in brief                       # propositions en attente


async def test_brief_degrade_sans_nova(store):
    settings = SimpleNamespace(tz="Europe/Paris", weather_entity="")
    brief = await BriefingService(settings, None, None, store, mail=None).compose(pending=0)
    # Sans météo/maison/mail/santé : le brief reste valide (salutation + rien à signaler)
    assert "Bonjour" in brief or "Bonsoir" in brief
    assert "Rien de particulier" in brief
