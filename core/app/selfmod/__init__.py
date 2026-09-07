"""Auto-amélioration encadrée (Phase 6) : Luna PROPOSE des diffs sur son propre
code / sa configuration ; la revue humaine et l'application restent à Guillaume.

Ce paquet n'applique jamais rien : `policy` évalue un diff, `source` lit (en
seule lecture) le code source. Aucune écriture, aucun processus lancé."""

from __future__ import annotations

from .policy import Verdict, evaluate, is_protected_path, summarize
from .source import SelfSource

__all__ = ["Verdict", "evaluate", "is_protected_path", "summarize", "SelfSource"]
