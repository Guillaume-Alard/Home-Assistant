"""Détection d'habitudes (Phase 8) : lire le JOURNAL des actions exécutées et y
repérer des scénarios récurrents — les mêmes actions, groupées, à la même heure,
sur plusieurs jours. Chaque motif fort devient un *candidat* de routine que Luna
PROPOSE (jamais n'active).

Fonction pure sur les lignes de journal : facile à tester. On ne considère que
des actions courantes basées sur des entités (allumer/éteindre/volets/scènes),
volontairement : ce sont celles qui composent un « scénario » reproductible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from .safety import signature, validate_steps

# Actions « scénarisables » (sous-ensemble stable de la liste blanche des routines)
_HABIT_ACTIONS = {"ha.turn_on", "ha.turn_off", "ha.cover", "ha.scene"}
_SESSION_GAP_MIN = 30   # deux actions à moins de 30 min = même « session »
_MIN_STEPS = 2          # une routine, c'est au moins deux actions


@dataclass
class RoutineCandidate:
    name: str
    description: str
    steps: list[dict]
    signature: str
    hour: int
    days: int
    keys: frozenset = field(default_factory=frozenset)


def _name_for_hour(hour: int) -> str:
    if 5 <= hour < 11:
        return "Bon matin"
    if 17 <= hour < 22:
        return "Soirée"
    if hour >= 22 or hour < 5:
        return "Bonne nuit"
    return "Routine de journée"


def _event_key(action_id: str, params: dict) -> tuple | None:
    ids = params.get("entity_ids")
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list) or not ids:
        return None
    op = params.get("op") or ""
    return (action_id, op, tuple(sorted(str(e) for e in ids)))


def _key_to_step(key: tuple) -> dict:
    action_id, op, ids = key
    params: dict = {"entity_ids": list(ids)}
    if op:
        params["op"] = op
    return {"action_id": action_id, "params": params}


def find_candidates(
    rows: list[dict], *, now: datetime, min_days: int = 3, lookback_days: int = 21,
) -> list[RoutineCandidate]:
    """Journal → candidats de routine (motifs ≥ min_days jours, ≥ 2 actions)."""
    tz = now.tzinfo
    # 1) Événements exploitables : action courante exécutée, datée, dans la fenêtre.
    events: list[tuple] = []  # (date, minutes_since_midnight, hour, key)
    for r in rows:
        if r.get("outcome") != "ok" or r.get("action_id") not in _HABIT_ACTIONS:
            continue
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except (TypeError, ValueError):
            continue
        if ts.tzinfo is not None and tz is not None:
            ts = ts.astimezone(tz)
        if (now - ts).days > lookback_days or ts > now:
            continue
        try:
            params = json.loads(r.get("params") or "{}")
        except (TypeError, ValueError):
            continue
        key = _event_key(str(r["action_id"]), params)
        if key is None:
            continue
        events.append((ts.date(), ts.hour * 60 + ts.minute, ts.hour, key))

    if not events:
        return []

    # 2) Regrouper en sessions par jour : actions rapprochées dans le temps
    #    (≤ 30 min) → une même session, datée, avec son heure et son jeu de clés.
    from collections import defaultdict

    events.sort(key=lambda e: (e[0], e[1]))
    day_sessions: list[tuple] = []  # (date, hour, frozenset(keys))
    cur_day = cur_last = None
    cur_keys: set = set()
    cur_hour = 0
    for date, minute, hour, key in events:
        if cur_day == date and cur_last is not None and minute - cur_last <= _SESSION_GAP_MIN:
            cur_keys.add(key)
            cur_last = minute
        else:
            if cur_keys:
                day_sessions.append((cur_day, cur_hour, frozenset(cur_keys)))
            cur_day, cur_last, cur_keys, cur_hour = date, minute, {key}, hour
    if cur_keys:
        day_sessions.append((cur_day, cur_hour, frozenset(cur_keys)))

    # 3) Par heure voisine (±1), garder les clés présentes sur ≥ min_days jours.
    candidates: list[RoutineCandidate] = []
    seen_sig: set[str] = set()
    for center in range(0, 24):
        # jours distincts, comptage de clés et heures réelles dans la fenêtre ±1
        key_days: dict[tuple, set] = defaultdict(set)
        days_in_window: set = set()
        hours_seen: list[int] = []
        for date, hour, keys in day_sessions:
            if min(abs(hour - center), 24 - abs(hour - center)) <= 1:
                days_in_window.add(date)
                hours_seen.append(hour)
                for k in keys:
                    key_days[k].add(date)
        if len(days_in_window) < min_days:
            continue
        common = [k for k, days in key_days.items() if len(days) >= min_days]
        if len(common) < _MIN_STEPS:
            continue
        steps = validate_steps([_key_to_step(k) for k in sorted(common)])
        sig = signature(steps)
        if sig in seen_sig:
            continue
        seen_sig.add(sig)
        n_days = max(len(key_days[k]) for k in common)
        rep_hour = max(set(hours_seen), key=hours_seen.count)  # heure la plus fréquente
        candidates.append(RoutineCandidate(
            name=_name_for_hour(rep_hour),
            description=f"Tu fais souvent ça vers {rep_hour}h ({n_days} jours observés).",
            steps=steps, signature=sig, hour=rep_hour, days=n_days, keys=frozenset(common),
        ))
    # Les motifs les plus réguliers d'abord
    candidates.sort(key=lambda c: c.days, reverse=True)
    return candidates
