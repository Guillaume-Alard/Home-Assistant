"""Le garde-fou des routines (Phase 8) : une routine ne contient QUE des actions
courantes, jamais rien de sensible.

Une routine est une séquence d'actions déclenchable d'un mot, y compris par une
personne reconnue non-propriétaire. Elle ne doit donc JAMAIS pouvoir déverrouiller
une serrure, désarmer l'alarme ou appeler un service arbitraire : `validate_steps`
refuse tout ce qui sort de la liste blanche `ROUTINE_SAFE_ACTIONS`. C'est le
pendant, pour les routines, de la règle « les actions sensibles restent à l'UI ».
"""

from __future__ import annotations

import hashlib
import json

from ..actions.executors import ONOFF_DOMAINS

# Actions autorisées dans une routine : toutes NON sensibles et exécutables en
# ordre direct (turn_on/off, volets, scènes, consigne de chauffage, notification).
# Volontairement PAS : serrures, alarme, service générique, ni protocol.run (dont
# le risque est dynamique) — hors de portée d'une routine.
ROUTINE_SAFE_ACTIONS = {
    "ha.turn_on", "ha.turn_off", "ha.cover", "ha.scene",
    "ha.climate_set_temperature", "ha.notify",
}
_ENTITY_DOMAINS = {
    "ha.turn_on": ONOFF_DOMAINS, "ha.turn_off": ONOFF_DOMAINS,
    "ha.cover": {"cover"}, "ha.scene": {"scene"}, "ha.climate_set_temperature": {"climate"},
}
_COVER_OPS = {"open", "close", "stop"}
MAX_STEPS = 15


class RoutineError(ValueError):
    """Refus de validation — message français, montrable à l'utilisateur."""


def _entity_ids(params: dict, domains: set[str]) -> list[str]:
    raw = params.get("entity_ids")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise RoutineError("Une étape cible au moins une entité (entity_ids).")
    out = []
    for e in raw:
        if not isinstance(e, str) or "." not in e:
            raise RoutineError(f"Entité invalide : {e!r}.")
        if e.split(".", 1)[0] not in domains:
            raise RoutineError(f"L'entité « {e} » n'est pas du bon type pour cette action.")
        out.append(e)
    return out


def _label(action_id: str, params: dict, names: dict[str, str] | None = None) -> str:
    def nm(ids):
        return ", ".join((names or {}).get(e, e) for e in ids)
    ids = params.get("entity_ids") or []
    if isinstance(ids, str):
        ids = [ids]
    if action_id == "ha.turn_on":
        return f"Allumer {nm(ids)}"
    if action_id == "ha.turn_off":
        return f"Éteindre {nm(ids)}"
    if action_id == "ha.cover":
        verbe = {"open": "Ouvrir", "close": "Fermer", "stop": "Arrêter"}.get(params.get("op"), "Volet")
        return f"{verbe} {nm(ids)}"
    if action_id == "ha.scene":
        return f"Scène {nm(ids)}"
    if action_id == "ha.climate_set_temperature":
        return f"Chauffage {nm(ids)} → {params.get('temperature')}°C"
    if action_id == "ha.notify":
        return f"Notifier : {str(params.get('message') or '')[:40]}"
    return action_id


def validate_step(step: dict, names: dict[str, str] | None = None) -> dict:
    """Valide UNE étape et renvoie sa forme normalisée {action_id, params, label}.
    Lève RoutineError si l'action est hors liste blanche ou mal formée."""
    if not isinstance(step, dict):
        raise RoutineError("Étape invalide (objet attendu).")
    action_id = str(step.get("action_id") or "")
    if action_id not in ROUTINE_SAFE_ACTIONS:
        raise RoutineError(
            f"« {action_id or '?'} » n'est pas autorisé dans une routine "
            "(les actions sensibles et le service générique en sont exclus)."
        )
    params = dict(step.get("params") or {})
    if action_id in _ENTITY_DOMAINS:
        params["entity_ids"] = _entity_ids(params, _ENTITY_DOMAINS[action_id])
    if action_id == "ha.cover" and params.get("op") not in _COVER_OPS:
        raise RoutineError("Opération de volet invalide (open/close/stop).")
    if action_id == "ha.climate_set_temperature":
        try:
            t = float(params.get("temperature"))
        except (TypeError, ValueError):
            raise RoutineError("Température invalide.") from None
        if not 5.0 <= t <= 30.0:
            raise RoutineError("Température hors bornes (5–30 °C).")
        params["temperature"] = t
    if action_id == "ha.notify" and not str(params.get("message") or "").strip():
        raise RoutineError("Une notification a besoin d'un message.")
    return {"action_id": action_id, "params": params, "label": _label(action_id, params, names)}


def validate_steps(steps, names: dict[str, str] | None = None) -> list[dict]:
    if not isinstance(steps, list) or not steps:
        raise RoutineError("Une routine a besoin d'au moins une étape.")
    if len(steps) > MAX_STEPS:
        raise RoutineError(f"Trop d'étapes (max {MAX_STEPS}).")
    return [validate_step(s, names) for s in steps]


def signature(steps: list[dict]) -> str:
    """Empreinte stable d'une routine (pour l'anti-doublon des habitudes) :
    l'ensemble {action + cibles} sans tenir compte de l'ordre."""
    parts = []
    for s in steps:
        params = s.get("params") or {}
        ids = params.get("entity_ids") or []
        if isinstance(ids, str):
            ids = [ids]
        key = s.get("action_id", "") + "|" + params.get("op", "") + "|" + ",".join(sorted(ids))
        parts.append(key)
    blob = json.dumps(sorted(parts), ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
