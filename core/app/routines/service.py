"""Le service des routines (Phase 8) : propose, active, déclenche.

Luna PROPOSE des routines (depuis tes habitudes via l'analyse du journal, ou
depuis la conversation) ; Guillaume les ACTIVE dans le cockpit (revue humaine) ;
ensuite elles se déclenchent d'un mot. Le service valide toujours les étapes
(aucune action sensible) et exécute via le moteur d'actions — jamais Nova en direct.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from ..actions.engine import ActionEngine
from ..ha.client import HAClient
from .analyzer import find_candidates
from .runner import RoutineRunner
from .safety import RoutineError, signature, validate_steps

log = logging.getLogger("sentinel.routines")


class RoutineService:
    def __init__(
        self,
        settings,
        ha: HAClient | None,
        engine: ActionEngine,
        store,
        announce: Callable[[str, bool], Awaitable[None]],
        on_change: Callable[[], Awaitable[None]],
    ):
        self._settings = settings
        self._ha = ha
        self._engine = engine
        self._store = store
        self._announce = announce
        self._on_change = on_change
        self._runner = RoutineRunner(engine)
        self._scan_task: asyncio.Task | None = None
        self._last_scan = 0.0

    def _names(self) -> dict[str, str]:
        if self._ha is None:
            return {}
        return {e: self._ha.friendly_name(e) for e in self._ha.states_snapshot()}

    # ── Proposition (outil LLM ou habitude) ──────────────────────────────

    async def propose(
        self, *, name: str, steps: list[dict], description: str = "", source: str = "llm",
    ) -> dict:
        """Valide et range une routine PROPOSÉE (à activer par Guillaume). Lève RoutineError."""
        name = (name or "").strip()[:60]
        if not name:
            raise RoutineError("Donne un nom à la routine.")
        clean = validate_steps(steps, self._names())  # refuse toute action sensible
        sig = signature(clean)
        routine = await self._store.add_routine(
            name=name, description=description[:300], steps=clean,
            signature=sig, source=source, status="proposed",
        )
        await self._on_change()
        return routine

    # ── Détection d'habitudes ────────────────────────────────────────────

    def start_scanner(self) -> None:
        if not self._settings.routines_enabled or self._ha is None:
            return
        self._scan_task = asyncio.create_task(self._scan_loop())

    async def stop(self) -> None:
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None

    async def _scan_loop(self) -> None:
        await asyncio.sleep(90)  # laisser le système démarrer
        while True:
            try:
                await self.scan_habits()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Analyse d'habitudes en échec")
            await asyncio.sleep(3600)  # une passe par heure

    async def scan_habits(self) -> int:
        """Journal → nouveaux candidats proposés. Renvoie le nombre proposé."""
        if not self._settings.routines_enabled or self._ha is None:
            return 0
        from datetime import datetime
        from zoneinfo import ZoneInfo

        try:
            tz = ZoneInfo(self._settings.tz)
        except Exception:
            tz = None
        rows = await self._store.list_journal(1000)
        candidates = find_candidates(
            rows, now=datetime.now(tz),
            min_days=self._settings.routine_habit_min_days,
            lookback_days=self._settings.routine_habit_lookback,
        )
        proposed = 0
        for c in candidates:
            if await self._store.signature_exists(c.signature, ("proposed", "active", "rejected")):
                continue  # déjà proposée/active/écartée : on n'insiste pas
            try:
                clean = validate_steps(c.steps, self._names())
            except RoutineError:
                continue
            routine = await self._store.add_routine(
                name=c.name, description=c.description, steps=clean,
                signature=c.signature, source="appris", status="proposed",
            )
            proposed += 1
            await self._announce(
                f"J'ai remarqué une habitude : {c.description.lower()} "
                f"Je te propose d'en faire une routine « {routine['name']} » "
                "(à activer dans Paramètres › Routines).",
                False,
            )
            log.info("Routine apprise proposée : %s (%s jours)", routine["name"], c.days)
        if proposed:
            await self._on_change()
        return proposed

    # ── Décisions du cockpit ─────────────────────────────────────────────

    async def approve(self, routine_id: str) -> dict | None:
        r = await self._store.update_routine(routine_id, status="active")
        await self._on_change()
        return r

    async def reject(self, routine_id: str) -> None:
        await self._store.update_routine(routine_id, status="rejected")
        await self._on_change()

    async def delete(self, routine_id: str) -> None:
        await self._store.delete_routine(routine_id)
        await self._on_change()

    async def rename(self, routine_id: str, name: str) -> None:
        name = (name or "").strip()[:60]
        if name:
            await self._store.update_routine(routine_id, name=name)
            await self._on_change()

    # ── Déclenchement ────────────────────────────────────────────────────

    async def run(self, routine: dict, *, utterance: str, source: str) -> tuple[str, bool]:
        text, ok = await self._runner.run(routine, utterance=utterance, source=source)
        if ok:
            await self._store.routine_ran(routine["id"])
            await self._on_change()
        return text, ok

    async def run_by_name(self, name: str, *, utterance: str, source: str) -> tuple[str, bool]:
        routine = await self._store.find_active_routine(name)
        if routine is None:
            actives = await self._store.list_routines("active")
            noms = ", ".join(r["name"] for r in actives) or "aucune routine active"
            return f"Je ne connais pas de routine active « {name} ». Disponibles : {noms}.", False
        return await self.run(routine, utterance=utterance, source=source)
