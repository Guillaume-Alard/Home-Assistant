"""Agenda Google en LECTURE SEULE (Phase 12).

Même motif que le courriel (Phase 3) : OAuth2 avec la portée `calendar.readonly`
— Sentinel ne peut que LIRE tes événements, aucune création ni modification n'est
possible (la portée l'interdit côté Google). Appels REST directs via httpx, jeton
d'accès de courte durée mis en cache. Réutilise le même client Google (client_id /
secret) que Gmail ; seul le jeton de rafraîchissement diffère (portée agenda).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import httpx

log = logging.getLogger("sentinel.agenda")

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API = "https://www.googleapis.com/calendar/v3"


class CalendarError(RuntimeError):
    """Erreur d'accès à l'agenda — message (français) montré à l'utilisateur."""


class CalendarClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        tz,
        calendar_id: str = "primary",
        max_results: int = 10,
        timeout: int = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._tz = tz
        self._calendar_id = calendar_id or "primary"
        self._max = max(1, min(max_results, 25))
        self._timeout = timeout
        self._transport = transport
        self._access_token = ""
        self._expiry = 0.0

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def _token(self) -> str:
        if self._access_token and time.time() < self._expiry - 60:
            return self._access_token
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }
        try:
            async with self._http() as http:
                resp = await http.post(_TOKEN_URL, data=data)
        except (httpx.HTTPError, OSError) as exc:
            raise CalendarError(f"Impossible de joindre Google ({exc}).") from exc
        if resp.status_code != 200:
            raise CalendarError(
                "L'autorisation Agenda a été refusée (jeton expiré ou révoqué). "
                "Refais l'autorisation — voir docs/AGENDA.md."
            )
        payload = resp.json()
        self._access_token = str(payload.get("access_token") or "")
        self._expiry = time.time() + float(payload.get("expires_in") or 0)
        if not self._access_token:
            raise CalendarError("Google n'a pas renvoyé de jeton d'accès.")
        return self._access_token

    async def events(self, time_min: datetime, time_max: datetime, *, max_results: int | None = None) -> list[dict]:
        token = await self._token()
        headers = {"authorization": f"Bearer {token}"}
        params = {
            "singleEvents": "true", "orderBy": "startTime",
            "timeMin": time_min.isoformat(), "timeMax": time_max.isoformat(),
            "maxResults": str(max_results or self._max),
        }
        try:
            async with self._http() as http:
                resp = await http.get(
                    f"{_API}/calendars/{self._calendar_id}/events", params=params, headers=headers
                )
        except (httpx.HTTPError, OSError) as exc:
            raise CalendarError(f"Agenda injoignable ({exc}).") from exc
        if resp.status_code != 200:
            raise CalendarError(f"L'agenda a répondu {resp.status_code}.")
        return [_parse_event(e, self._tz) for e in (resp.json().get("items") or [])]

    async def today(self) -> list[dict]:
        now = datetime.now(self._tz)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return await self.events(start, end, max_results=25)

    async def upcoming(self, days: int = 7) -> list[dict]:
        now = datetime.now(self._tz)
        return await self.events(now, now + timedelta(days=max(1, days)))

    # ── Écriture (Phase 13) — UNIQUEMENT via une proposition approuvée ────────
    # Le jeton doit porter la portée `calendar.events` (réautorisation avec
    # `--write`, voir docs/AGENDA.md) ; un jeton lecture seule est refusé par
    # Google (403), ce qui remonte ici en CalendarError explicite.

    async def create_event(
        self,
        *,
        summary: str,
        start: str,
        end: str | None = None,
        all_day: bool = False,
        location: str = "",
        description: str = "",
    ) -> dict:
        summary = (summary or "").strip()
        if not summary:
            raise CalendarError("Titre de l'événement manquant.")
        body: dict = {"summary": summary}
        if location.strip():
            body["location"] = location.strip()
        if description.strip():
            body["description"] = description.strip()
        body.update(_event_times(start, end, all_day, _tz_name(self._tz)))

        token = await self._token()
        headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
        try:
            async with self._http() as http:
                resp = await http.post(
                    f"{_API}/calendars/{self._calendar_id}/events", json=body, headers=headers
                )
        except (httpx.HTTPError, OSError) as exc:
            raise CalendarError(f"Agenda injoignable ({exc}).") from exc
        if resp.status_code in (401, 403):
            raise CalendarError(
                "Création refusée : le jeton Agenda est en lecture seule. Réautorise avec "
                "l'accès en écriture (`python agenda/authorize.py --write`) puis mets GCAL_WRITE=1 "
                "— voir docs/AGENDA.md."
            )
        if resp.status_code not in (200, 201):
            raise CalendarError(f"L'agenda a refusé la création ({resp.status_code}).")
        return _parse_event(resp.json(), self._tz)


def _parse_event(item: dict, tz) -> dict:
    start = item.get("start") or {}
    end = item.get("end") or {}
    all_day = "date" in start
    start_dt = _parse_dt(start, tz)
    return {
        "summary": item.get("summary") or "(sans titre)",
        "all_day": all_day,
        "start": start.get("dateTime") or start.get("date") or "",
        "end": end.get("dateTime") or end.get("date") or "",
        "location": item.get("location") or "",
        # Heure locale lisible pour l'UI / le briefing
        "when": _fmt_when(start_dt, all_day) if start_dt else "",
        "start_ts": start_dt.isoformat() if start_dt else "",
    }


def _tz_name(tz) -> str:
    return getattr(tz, "key", None) or "UTC"


def _event_times(start: str, end: str | None, all_day: bool, tz_name: str) -> dict:
    """Construit les blocs start/end du corps d'événement Google.

    Événement daté : { {dateTime, timeZone}, … } ; fin par défaut = début + 1 h.
    Journée entière : { {date}, … } ; fin exclusive par défaut = lendemain.
    """
    start = (start or "").strip()
    if not start:
        raise CalendarError("Date/heure de début manquante.")
    if all_day:
        sd = start[:10]
        try:
            base = datetime.strptime(sd, "%Y-%m-%d")
        except ValueError as exc:
            raise CalendarError("Date de début illisible (attendu AAAA-MM-JJ).") from exc
        ed = (end or "")[:10] or (base + timedelta(days=1)).strftime("%Y-%m-%d")
        return {"start": {"date": sd}, "end": {"date": ed}}
    if not end:
        try:
            end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
        except ValueError as exc:
            raise CalendarError("Heure de début illisible (format ISO attendu).") from exc
    return {
        "start": {"dateTime": start, "timeZone": tz_name},
        "end": {"dateTime": end, "timeZone": tz_name},
    }


def _parse_dt(node: dict, tz) -> datetime | None:
    raw = node.get("dateTime") or node.get("date")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:  # date « toute la journée » → minuit local
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz) if tz else dt


def _fmt_when(dt: datetime, all_day: bool) -> str:
    if all_day:
        return "toute la journée"
    return f"{dt.hour}h{dt.minute:02d}" if dt.minute else f"{dt.hour}h"
