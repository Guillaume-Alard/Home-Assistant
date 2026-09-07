"""Minuteurs & rappels vocaux (Phase 10) : 100% local, rien ne quitte Nebula.

Un minuteur (« pâtes, 10 minutes ») ou un rappel daté (« sortir le plat, dans
20 minutes » ; « appeler le garage, à 18h ») est rangé avec une échéance ; une
petite boucle de fond réveille Luna à l'heure dite : elle carillonne et le dit.
Tout est stocké dans la base locale (survit à un redémarrage).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("sentinel.reminders")

_MAX_HORIZON_DAYS = 400  # garde-fou : pas d'échéance absurde


def compute_due_iso(
    *, tz: ZoneInfo | None,
    minutes: float | None = None, seconds: float | None = None,
    dans_minutes: float | None = None, a: str | None = None,
) -> str | None:
    """Calcule l'échéance en UTC ISO (comparable au stockage), ou None si invalide
    ou déjà passée. `a` est une date/heure absolue (ISO, ex. « 2026-09-07T18:00 »)."""
    now = datetime.now(timezone.utc)
    target: datetime | None = None
    if a:
        try:
            parsed = datetime.fromisoformat(str(a).strip())
        except ValueError:
            return None
        if parsed.tzinfo is None:  # heure locale sous-entendue
            parsed = parsed.replace(tzinfo=tz or timezone.utc)
        target = parsed.astimezone(timezone.utc)
    else:
        delta = 0.0
        if dans_minutes is not None:
            delta += float(dans_minutes) * 60
        if minutes is not None:
            delta += float(minutes) * 60
        if seconds is not None:
            delta += float(seconds)
        if delta <= 0:
            return None
        target = now + timedelta(seconds=delta)
    if target <= now or target > now + timedelta(days=_MAX_HORIZON_DAYS):
        return None
    return target.isoformat(timespec="milliseconds")


class ReminderScheduler:
    def __init__(
        self,
        settings,
        store,
        announce: Callable[[str, bool], Awaitable[None]],   # (texte, parler)
        on_change: Callable[[], Awaitable[None]],            # rafraîchit le tiroir
        on_fire: Callable[[dict], Awaitable[None]],          # carillon UI
    ):
        self._settings = settings
        self._store = store
        self._announce = announce
        self._on_change = on_change
        self._on_fire = on_fire
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._settings.reminders_enabled:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Boucle des rappels en échec")
            await asyncio.sleep(5)

    async def tick(self) -> None:
        """Déclenche les rappels échus. Isolable pour les tests."""
        due = await self._store.due_reminders()
        for r in due:
            await self._store.set_reminder_status(r["id"], "fired")
            label = (r.get("label") or "").strip()
            if r["kind"] == "timer":
                text = f"⏰ Minuteur terminé{f' — {label}' if label else ''}."
            else:
                text = f"⏰ Rappel{f' : {label}' if label else ''}."
            try:
                await self._announce(text, True)   # un rappel demandé se dit toujours
                await self._on_fire(r)
            except Exception:
                log.exception("Déclenchement du rappel %s impossible", r["id"])
        if due:
            await self._on_change()
