"""Alertes proactives : Sentinel surveille les événements de Nova et réagit.

Règles déclaratives (config/alerts.yml) évaluées sur chaque `state_changed` :
correspondance d'entités (liste ou motif), transition d'état entrante,
condition optionnelle (ex. alarme armée), anti-rebond par entité. Une règle
déclenchée = annonce vocale/visuelle + éventuelle notification téléphone via
le moteur d'actions (chemin « système », risque faible uniquement, journalisé).
"""

from __future__ import annotations

import fnmatch
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..actions.engine import ActionEngine
from .client import HAClient

log = logging.getLogger("sentinel.alerts")

_SEVERITIES = {
    "info": "info", "attention": "warning", "warning": "warning",
    "critique": "critical", "critical": "critical",
}


@dataclass(frozen=True)
class AlertRule:
    id: str
    name: str
    entities: tuple[str, ...]        # entity_id exacts…
    pattern: str | None              # …ou motif glob (binary_sensor.*fumee*)
    to_states: tuple[str, ...]
    from_states: tuple[str, ...]     # vide = n'importe quel état d'origine
    condition_entity: str | None
    condition_states: tuple[str, ...]
    severity: str                    # info | warning | critical
    message: str                     # gabarit : {friendly_name} {state} {entity_id}
    speak: bool
    notify_service: str | None
    notify_title: str
    cooldown: float                  # secondes de silence par (règle, entité)

    def matches_entity(self, entity_id: str) -> bool:
        if entity_id in self.entities:
            return True
        return bool(self.pattern and fnmatch.fnmatch(entity_id, self.pattern))


def load_rules(path: Path) -> list[AlertRule]:
    if not path.is_file():
        log.warning("Aucun fichier d'alertes (%s)", path)
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as exc:
        log.error("alerts.yml invalide : %s", exc)
        return []
    if not isinstance(raw, list):
        log.error("alerts.yml doit être une liste de règles")
        return []

    rules: list[AlertRule] = []
    for i, spec in enumerate(raw):
        if not isinstance(spec, dict):
            continue
        rule_id = str(spec.get("id") or f"regle-{i + 1}")
        severity = _SEVERITIES.get(str(spec.get("gravite", "attention")).lower())
        to_states = spec.get("vers")
        if severity is None or not to_states or not spec.get("message"):
            log.error("Règle « %s » ignorée : gravite/vers/message requis", rule_id)
            continue
        entities = spec.get("entites") or []
        if isinstance(entities, str):
            entities = [entities]
        pattern = spec.get("motif")
        if not entities and not pattern:
            log.error("Règle « %s » ignorée : ni entites ni motif", rule_id)
            continue
        condition = spec.get("condition") or {}
        notify = spec.get("notifier") or {}
        rules.append(AlertRule(
            id=rule_id,
            name=str(spec.get("nom") or rule_id),
            entities=tuple(str(e) for e in entities),
            pattern=str(pattern) if pattern else None,
            to_states=tuple(str(s) for s in (to_states if isinstance(to_states, list) else [to_states])),
            from_states=tuple(str(s) for s in (spec.get("depuis") or [])),
            condition_entity=str(condition["entite"]) if condition.get("entite") else None,
            condition_states=tuple(str(s) for s in (condition.get("etats") or [])),
            severity=severity,
            message=str(spec["message"]),
            speak=bool(spec.get("parler", True)),
            notify_service=str(notify["service"]) if notify.get("service") else None,
            notify_title=str(notify.get("titre") or "Sentinel"),
            cooldown=float(spec.get("silence", 300)),
        ))
    log.info("Règles d'alerte chargées : %s", ", ".join(r.id for r in rules) or "aucune")
    return rules


class AlertEngine:
    def __init__(
        self,
        rules: list[AlertRule],
        ha: HAClient,
        engine: ActionEngine,
        announce: Callable[[str, str, bool], Awaitable[None]],  # (texte, gravité, parler)
    ):
        self._rules = rules
        self._ha = ha
        self._engine = engine
        self._announce = announce
        self._last_fired: dict[tuple[str, str], float] = {}

    async def on_state_changed(self, event: dict) -> None:
        if event.get("event_type") != "state_changed":
            return
        data = event.get("data") or {}
        entity_id = data.get("entity_id") or ""
        new = data.get("new_state") or {}
        old = data.get("old_state") or {}
        new_state = new.get("state")
        old_state = old.get("state")
        if new_state is None:
            return

        for rule in self._rules:
            if not rule.matches_entity(entity_id):
                continue
            # Transition ENTRANTE uniquement (un changement d'attribut ne compte pas)
            if new_state not in rule.to_states or old_state in rule.to_states:
                continue
            if rule.from_states and old_state not in rule.from_states:
                continue
            if rule.condition_entity:
                cond = self._ha.get_state(rule.condition_entity) or {}
                if cond.get("state") not in rule.condition_states:
                    continue
            now = time.monotonic()
            key = (rule.id, entity_id)
            if now - self._last_fired.get(key, -1e12) < rule.cooldown:
                continue
            self._last_fired[key] = now
            await self._fire(rule, entity_id, new_state)

    async def _fire(self, rule: AlertRule, entity_id: str, state: str) -> None:
        friendly = self._ha.friendly_name(entity_id)
        try:
            text = rule.message.format(
                friendly_name=friendly, entity_id=entity_id, state=state
            )
        except (KeyError, IndexError):
            text = rule.message
        log.warning("ALERTE [%s/%s] %s : %s", rule.severity, rule.id, entity_id, text)

        try:
            await self._announce(text, rule.severity, rule.speak)
        except Exception:
            log.exception("Annonce d'alerte impossible")

        if rule.notify_service:
            outcome = await self._engine.run_system(
                "ha.notify",
                {"service": rule.notify_service, "message": text, "title": rule.notify_title},
                authorization=f"règle d'alerte « {rule.id} » (config/alerts.yml)",
            )
            if not outcome.ok:
                log.warning("Notification de l'alerte %s en échec : %s", rule.id, outcome.text)
