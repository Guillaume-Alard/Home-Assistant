"""Veilleur contextuel (Phase 7) : Luna observe, puis SUGGÈRE — jamais n'exécute.

Boucle de fond : toutes les `interval` secondes (et, de façon amortie, sur un
changement d'état de Nova), le contexte est évalué par les règles. Une nouvelle
suggestion est rangée (anti-répétition par `key` + silence par situation),
diffusée au cockpit, et — hors heures calmes, ou si c'est une alerte de sécurité
— dite à voix haute.

Une suggestion actionnable ne devient une action qu'en DEUX temps, tous deux
humains : Guillaume dit « oui » → une PROPOSITION est créée (via le moteur
d'actions) → il l'APPROUVE. Ce module ne fait qu'appeler `propose` : il n'exécute
jamais rien lui-même (les actions sensibles restent d'ailleurs barrées à la voix).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from ..actions.engine import ActionEngine
from ..ha.client import HAClient
from .rules import Context, ProactiveConfig, evaluate_all

log = logging.getLogger("sentinel.proactive")


def load_config(path: Path) -> ProactiveConfig:
    """Charge config/proactive.yml (facultatif) par-dessus les valeurs par défaut."""
    cfg = ProactiveConfig()
    if not path.is_file():
        return cfg
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        log.error("proactive.yml invalide : %s — valeurs par défaut", exc)
        return cfg
    if not isinstance(raw, dict):
        return cfg
    rules = raw.get("regles")
    return ProactiveConfig(
        night_hour=int(raw.get("heure_nuit", cfg.night_hour)),
        late_hour=int(raw.get("heure_tard", cfg.late_hour)),
        quiet_start=int(raw.get("silence_debut", cfg.quiet_start)),
        quiet_end=int(raw.get("silence_fin", cfg.quiet_end)),
        temp_min=float(raw.get("temp_min", cfg.temp_min)),
        temp_max=float(raw.get("temp_max", cfg.temp_max)),
        cooldown_minutes=int(raw.get("silence_minutes", cfg.cooldown_minutes)),
        enabled_rules=tuple(str(r) for r in rules) if isinstance(rules, list) else None,
    )


class ProactiveEngine:
    def __init__(
        self,
        settings,
        ha: HAClient,
        action_engine: ActionEngine,
        store,
        say: Callable[[str, bool], Awaitable[None]],   # (texte, parler) → fil + voix
        on_change: Callable[[], Awaitable[None]],       # rafraîchit le tiroir Suggestions
        config: ProactiveConfig | None = None,
        notify: Callable[[str, str], Awaitable[None]] | None = None,  # (titre, détail) → tel.
    ):
        self._settings = settings
        self._ha = ha
        self._engine = action_engine
        self._store = store
        self._say = say
        self._on_change = on_change
        # Notification mobile (Phase 14) : pour les alertes de sécurité seulement.
        # C'est de la COMMUNICATION vers Guillaume — jamais une action sur la maison
        # (le veilleur ne fait toujours qu'observer, dire, et proposer).
        self._notify = notify
        self.config = config or load_config(settings.config_dir / "proactive.yml")
        try:
            self._tz = ZoneInfo(settings.tz)
        except Exception:
            self._tz = None
        self._lock = asyncio.Lock()
        self._last_eval = 0.0  # monotonic, pour amortir les nudges

    # ── Boucle de fond ───────────────────────────────────────────────────

    async def run(self) -> None:
        # Petit délai initial : laisser Nova se connecter et peupler les états.
        await asyncio.sleep(20)
        while True:
            try:
                await self.evaluate()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Évaluation proactive en échec")
            await asyncio.sleep(max(30, self._settings.proactive_interval))

    async def nudge(self) -> None:
        """Réévalue vite après un changement d'état — mais pas plus d'une fois / 20 s."""
        if time.monotonic() - self._last_eval < 20:
            return
        try:
            await self.evaluate()
        except Exception:
            log.exception("Nudge proactif en échec")

    # ── Cœur ─────────────────────────────────────────────────────────────

    def _context(self) -> Context:
        snapshot = self._ha.states_snapshot()
        area_labels = {}
        for entity_id in snapshot:
            area_id = self._ha.entity_area(entity_id)
            if area_id:
                area_labels[entity_id] = self._ha.area_name(area_id)
        return Context(now=datetime.now(self._tz), snapshot=snapshot, area_labels=area_labels)

    async def evaluate(self) -> None:
        if not self._settings.proactive_enabled or self._ha is None or not self._ha.connected:
            return
        async with self._lock:
            self._last_eval = time.monotonic()
            ctx = self._context()
            muted = set(await self._store.list_proactive_mutes())
            suggestions = evaluate_all(ctx, self.config, muted)
            cutoff = (datetime.now(self._tz) - timedelta(minutes=self.config.cooldown_minutes)).isoformat()
            for s in suggestions:
                if await self._store.find_live_proactive(s.key, cutoff):
                    continue  # déjà en cours, reportée, ou trop récente
                await self._surface(s, ctx.hour)

    async def _surface(self, s, hour: int) -> None:
        await self._store.add_proactive(
            key=s.key, rule=s.rule, title=s.title, detail=s.detail,
            severity=s.severity, category=s.category, action=s.action,
        )
        # La voix : une alerte de sécurité parle toujours ; un simple constat se
        # tait pendant les heures calmes (il reste dans le tiroir Suggestions).
        speak = s.severity == "warning" or not self.config.in_quiet_hours(hour)
        log.info("Suggestion proactive [%s/%s] %s", s.severity, s.rule, s.title)
        try:
            await self._say(s.detail, speak)
        except Exception:
            log.exception("Annonce d'une suggestion impossible")
        # Une alerte de sécurité te suit sur le téléphone (hors du cockpit) ; un
        # simple constat de confort/énergie reste au cockpit.
        if self._notify is not None and s.severity == "warning":
            try:
                await self._notify(s.title, s.detail)
            except Exception:
                log.exception("Notification mobile d'une suggestion impossible")
        try:
            await self._on_change()
        except Exception:
            log.exception("Rafraîchissement des suggestions impossible")

    # ── Décisions de Guillaume (cockpit / voix) ──────────────────────────

    async def make_proposal(self, sug_id: str) -> str:
        """« Oui, prépare » : la suggestion devient une PROPOSITION à approuver."""
        s = await self._store.get_proactive(sug_id)
        if s is None:
            return "Suggestion introuvable."
        if s["status"] != "active":
            return "Cette suggestion n'est plus active."
        action = s.get("action")
        if not action or not action.get("action_id"):
            return "Cette suggestion est un simple constat — rien à proposer."
        proposal, message = await self._engine.propose(
            title=str(action.get("title") or s["title"]),
            description=str(action.get("description") or s["detail"]),
            justification="Suggestion proactive de Luna, validée par Guillaume.",
            risk=action.get("risk"),
            rollback=str(action.get("rollback") or ""),
            action_id=str(action["action_id"]),
            params=action.get("params") or {},
            created_by="sentinel (proactif)",
        )
        if proposal is not None:
            await self._store.update_proactive(sug_id, status="acted", proposal_num=proposal["num"])
            await self._on_change()
        return message

    async def snooze(self, sug_id: str, minutes: int = 120) -> None:
        until = (datetime.now(self._tz) + timedelta(minutes=max(5, minutes))).isoformat()
        await self._store.update_proactive(sug_id, status="snoozed", snooze_until=until)
        await self._on_change()

    async def dismiss(self, sug_id: str) -> None:
        await self._store.update_proactive(sug_id, status="dismissed")
        await self._on_change()

    async def mute(self, rule: str) -> None:
        """« Ne plus me suggérer ça » : la règle est tue, ses suggestions actives écartées."""
        if rule:
            await self._store.add_proactive_mute(rule)
            for s in await self._store.list_proactive(("active", "snoozed")):
                if s["rule"] == rule:
                    await self._store.update_proactive(s["id"], status="dismissed")
            await self._on_change()

    async def unmute(self, rule: str) -> None:
        if rule:
            await self._store.remove_proactive_mute(rule)
            await self._on_change()
