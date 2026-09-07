"""Le briefing du matin (Phase 11) : un point clair et court, à heure fixe ou à
la demande.

Luna rassemble ce qu'elle sait déjà — la météo (via l'entité `weather` de Nova),
l'état de la maison, tes courriels non lus, tes rappels du jour, la santé des
systèmes — et en fait un brief naturel. Tout est LOCAL sauf le courriel (déjà
configuré) : aucune nouvelle mise en place, aucune dépendance externe.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .mail import MailError
from .norm import date_francaise
from .proactive.rules import Context

log = logging.getLogger("sentinel.briefing")

# Conditions météo Home Assistant → français
_WEATHER_FR = {
    "sunny": "ensoleillé", "clear-night": "ciel dégagé", "partlycloudy": "partiellement nuageux",
    "cloudy": "nuageux", "rainy": "pluvieux", "pouring": "forte pluie",
    "lightning": "orageux", "lightning-rainy": "orages et pluie",
    "snowy": "neigeux", "snowy-rainy": "neige et pluie", "fog": "brumeux",
    "hail": "grêle", "windy": "venteux", "windy-variant": "venteux",
    "exceptional": "conditions exceptionnelles",
}


class BriefingService:
    def __init__(self, settings, ha, health, store, mail=None, calendar=None):
        self._settings = settings
        self._ha = ha
        self._health = health
        self._store = store
        self._mail = mail
        self._calendar = calendar

    def _tz(self):
        try:
            return ZoneInfo(self._settings.tz)
        except Exception:
            return None

    def _greeting(self, now: datetime) -> str:
        h = now.hour
        mot = "Bonjour" if 5 <= h < 18 else "Bonsoir"
        return f"{mot}. Nous sommes le {date_francaise(now)}."

    # ── Sources ──────────────────────────────────────────────────────────

    def _weather_line(self) -> str | None:
        if self._ha is None or not self._ha.connected:
            return None
        entity = self._settings.weather_entity or next(
            iter(self._ha.entities_by_domain("weather")), ""
        )
        state = self._ha.get_state(entity) if entity else None
        if not state:
            return None
        cond = _WEATHER_FR.get(state.get("state"), state.get("state") or "")
        attrs = state.get("attributes") or {}
        temp = attrs.get("temperature")
        bits = [f"Dehors : {cond}" if cond else "Dehors"]
        if temp is not None:
            bits.append(f"{round(float(temp))}°")
        return ", ".join(bits) + "."

    def _house_line(self) -> str | None:
        if self._ha is None or not self._ha.connected:
            return None
        snapshot = self._ha.states_snapshot()
        labels = {}
        for e in snapshot:
            area_id = self._ha.entity_area(e)
            if area_id:
                labels[e] = self._ha.area_name(area_id)
        ctx = Context(now=datetime.now(self._tz()), snapshot=snapshot, area_labels=labels)
        openings = ctx.openings_open()
        covers = ctx.covers_open()
        lights = ctx.lights_on()
        parts = []
        if openings or covers:
            noms = [ctx.friendly(e) for e in (openings + covers)][:4]
            parts.append("ouvert : " + ", ".join(noms))
        else:
            parts.append("tout est fermé")
        if lights:
            parts.append(f"{len(lights)} lumière{'s' if len(lights) > 1 else ''} allumée{'s' if len(lights) > 1 else ''}")
        return "Maison : " + ", ".join(parts) + "."

    async def _mail_line(self) -> str | None:
        if self._mail is None:
            return None
        try:
            data = await self._mail.summary()
        except MailError:
            return None
        n = data.get("unread_total", 0)
        if not n:
            return "Pas de nouveau courriel."
        return f"{n} message{'s' if n > 1 else ''} non lu{'s' if n > 1 else ''}."

    async def _agenda_line(self) -> str | None:
        if self._calendar is None:
            return None
        try:
            events = await self._calendar.today()
        except Exception:
            log.debug("Agenda indisponible pour le briefing", exc_info=True)
            return None
        if not events:
            return "Agenda : rien de prévu aujourd'hui."
        bits = [f"{e['summary']} ({e['when']})" if e["when"] else e["summary"] for e in events[:4]]
        return "Agenda : " + " ; ".join(bits) + "."

    async def _reminders_line(self, now: datetime) -> str | None:
        try:
            items = await self._store.list_reminders("active")
        except Exception:
            return None
        today = now.date()
        due_today = []
        for r in items:
            try:
                local = datetime.fromisoformat(r["due_at"]).astimezone(now.tzinfo)
            except (ValueError, KeyError):
                continue
            if local.date() == today and r["kind"] == "reminder":
                t = f"{local.hour}h{local.minute:02d}" if local.minute else f"{local.hour}h"
                due_today.append(f"{r['label']} ({t})")
        if not due_today:
            return None
        return "Aujourd'hui : " + " ; ".join(due_today[:5]) + "."

    async def _systems_lines(self, pending: int) -> list[str]:
        out = []
        if self._health is not None:
            try:
                audit = await self._health.audit()
                warns = [c for c in audit.get("constats", []) if c.get("gravite") != "info"][:3]
                out.extend(f"⚠ {c['sujet']} : {c['detail']}" for c in warns)
            except Exception:
                log.debug("Audit indisponible pour le briefing", exc_info=True)
        if pending:
            out.append(f"{pending} proposition{'s' if pending > 1 else ''} en attente de ta décision (panneau ▤).")
        return out

    # ── Composition ──────────────────────────────────────────────────────

    async def compose(self, *, pending: int = 0, include_systems: bool = True, greeting: bool = True) -> str:
        now = datetime.now(self._tz())
        parts: list[str] = []
        if greeting:
            parts.append(self._greeting(now))
        for line in (self._weather_line(), self._house_line()):
            if line:
                parts.append(line)
        for coro in (self._agenda_line(), self._mail_line(), self._reminders_line(now)):
            line = await coro
            if line:
                parts.append(line)
        if include_systems:
            parts.extend(await self._systems_lines(pending))
        if len(parts) <= (1 if greeting else 0):
            parts.append("Rien de particulier à signaler.")
        return "\n".join(parts)
