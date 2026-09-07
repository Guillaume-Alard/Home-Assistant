"""Scénarios & routines (Phase 8) : des séquences d'actions nommées, PROPOSÉES
par Luna (depuis tes habitudes ou la conversation), ACTIVÉES par Guillaume, puis
déclenchées d'un mot. Jamais d'action sensible dans une routine ; l'exécution
passe par le moteur d'actions, jamais par Nova en direct.
"""

from __future__ import annotations

from .analyzer import RoutineCandidate, find_candidates
from .runner import RoutineRunner
from .safety import ROUTINE_SAFE_ACTIONS, RoutineError, signature, validate_steps
from .service import RoutineService

__all__ = [
    "RoutineService", "RoutineRunner", "RoutineCandidate", "find_candidates",
    "ROUTINE_SAFE_ACTIONS", "RoutineError", "signature", "validate_steps",
]
