"""Exécution d'une routine : chaque étape passe par le MOTEUR d'actions.

Le runner n'écrit jamais vers Nova directement : il appelle `engine.run_direct`
étape par étape. Comme une routine ne contient que des actions courantes (non
sensibles, validées à la création), chacune s'exécute immédiatement. Une étape
ratée n'arrête pas la routine ; le résultat est résumé.
"""

from __future__ import annotations

import asyncio
import logging

from ..actions.engine import ActionEngine

log = logging.getLogger("sentinel.routines")


class RoutineRunner:
    def __init__(self, engine: ActionEngine):
        self._engine = engine

    async def run(self, routine: dict, *, utterance: str, source: str) -> tuple[str, bool]:
        steps = routine.get("steps") or []
        if not steps:
            return "Cette routine est vide.", False
        done, errors = 0, []
        for step in steps:
            label = step.get("label") or step.get("action_id")
            outcome = await self._engine.run_direct(
                str(step.get("action_id") or ""), dict(step.get("params") or {}),
                utterance=f"routine « {routine['name']} » : {utterance}",
                source=f"{source} (routine)",
            )
            if outcome.status == "ok":
                done += 1
            else:
                log.warning("Routine %s : étape « %s » — %s", routine["name"], label, outcome.text)
                errors.append(f"{label} ({outcome.text})")
            await asyncio.sleep(0.2)  # laisse Nova respirer entre les étapes
        if done == 0:
            return f"Routine « {routine['name']} » : aucune étape n'a abouti.", False
        summary = f"Routine « {routine['name']} » lancée ({done}/{len(steps)} étapes)."
        if errors:
            summary += " En échec : " + "; ".join(errors)
        return summary, True
