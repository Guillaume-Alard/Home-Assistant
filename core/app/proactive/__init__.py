"""Proactivité contextuelle (Phase 7) : Luna observe l'état de la maison, l'heure
et ce qu'elle sait, puis SUGGÈRE — jamais n'exécute. Une suggestion actionnable
ne devient une action qu'en deux temps humains : accord → proposition → approbation.
"""

from __future__ import annotations

from .engine import ProactiveEngine, load_config
from .rules import Context, ProactiveConfig, Suggestion, evaluate_all

__all__ = ["ProactiveEngine", "load_config", "Context", "ProactiveConfig", "Suggestion", "evaluate_all"]
