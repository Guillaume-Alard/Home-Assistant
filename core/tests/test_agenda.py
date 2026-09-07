"""Agenda Google en lecture seule (Phase 12) : client OAuth + parsing, sans réseau."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import httpx
import pytest

from app.agenda import CalendarClient, CalendarError

PARIS = ZoneInfo("Europe/Paris")


def _client(handler, **kw):
    return CalendarClient("id", "secret", "refresh", tz=PARIS,
                          transport=httpx.MockTransport(handler), **kw)


def _ok_handler(calls=None):
    def handler(request):
        if calls is not None:
            calls.append(str(request.url))
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, json={"items": [
            {"summary": "Dentiste",
             "start": {"dateTime": "2026-09-07T14:00:00+02:00"},
             "end": {"dateTime": "2026-09-07T14:30:00+02:00"}, "location": "12 rue des Lilas"},
            {"summary": "Anniversaire", "start": {"date": "2026-09-07"}, "end": {"date": "2026-09-08"}},
        ]})
    return handler


async def test_today_parse_les_evenements():
    events = await _client(_ok_handler()).today()
    assert [e["summary"] for e in events] == ["Dentiste", "Anniversaire"]
    d = events[0]
    assert d["when"] == "14h" and not d["all_day"] and d["location"] == "12 rue des Lilas"
    assert events[1]["all_day"] and events[1]["when"] == "toute la journée"


async def test_upcoming_borne_la_periode():
    calls: list[str] = []
    await _client(_ok_handler(calls)).upcoming(3)
    ev_url = next(u for u in calls if "/events" in u)
    assert "singleEvents=true" in ev_url and "orderBy=startTime" in ev_url
    assert "timeMin=" in ev_url and "timeMax=" in ev_url


async def test_jeton_mis_en_cache():
    calls: list[str] = []
    c = _client(_ok_handler(calls))
    await c.today()
    await c.today()
    # Un seul échange de jeton (mis en cache), deux appels d'événements
    assert sum("oauth2" in u for u in calls) == 1
    assert sum("/events" in u for u in calls) == 2


async def test_erreur_autorisation():
    def handler(request):
        if "oauth2" in str(request.url):
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"items": []})
    with pytest.raises(CalendarError):
        await _client(handler).today()


async def test_erreur_api():
    def handler(request):
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(403, json={})
    with pytest.raises(CalendarError):
        await _client(handler).today()


# ── Écriture (Phase 13) — création d'événement, jamais sans proposition ───────

def _writer(captured=None, status=200):
    import json as _json

    def handler(request):
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if captured is not None:
            captured.append(_json.loads(request.content))
        if status != 200:
            return httpx.Response(status, json={})
        body = _json.loads(request.content)
        return httpx.Response(200, json=body)  # Google renvoie l'événement créé
    return handler


async def test_create_event_date_heure():
    cap: list = []
    ev = await _client(_writer(cap)).create_event(
        summary="Dentiste", start="2026-09-08T14:00:00", location="12 rue des Lilas")
    body = cap[0]
    assert body["summary"] == "Dentiste" and body["location"] == "12 rue des Lilas"
    assert body["start"] == {"dateTime": "2026-09-08T14:00:00", "timeZone": "Europe/Paris"}
    assert body["end"]["dateTime"] == "2026-09-08T15:00:00"  # fin par défaut = +1 h
    assert ev["summary"] == "Dentiste" and ev["when"] == "14h" and not ev["all_day"]


async def test_create_event_toute_la_journee():
    cap: list = []
    await _client(_writer(cap)).create_event(summary="Congés", start="2026-09-08", all_day=True)
    body = cap[0]
    assert body["start"] == {"date": "2026-09-08"}
    assert body["end"] == {"date": "2026-09-09"}  # fin exclusive = lendemain


async def test_create_event_refuse_en_lecture_seule():
    # Jeton de portée lecture seule → Google répond 403 → CalendarError explicite.
    with pytest.raises(CalendarError):
        await _client(_writer(status=403)).create_event(
            summary="X", start="2026-09-08T14:00:00")


async def test_create_event_titre_obligatoire():
    with pytest.raises(CalendarError):
        await _client(_writer()).create_event(summary="  ", start="2026-09-08T14:00:00")
