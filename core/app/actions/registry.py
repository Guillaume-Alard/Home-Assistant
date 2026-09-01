"""Registre des actions : le catalogue exhaustif de ce que Sentinel PEUT écrire.

Une action qui n'est pas déclarée ici n'existe pas pour le moteur — c'est la
liste blanche. Chaque action porte son niveau de risque et dit si elle est
exécutable sur ordre direct (« allume la lumière ») ou réservée aux
propositions approuvées.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


class ActionError(RuntimeError):
    """Échec ou refus d'un exécuteur — message en français, montrable."""


RISK_LEVELS = ("low", "medium", "sensitive")


@dataclass(frozen=True)
class ActionSpec:
    id: str
    description: str
    risk: str                          # low | medium | sensitive
    direct: bool                       # exécutable sur ordre direct (liste blanche)
    executor: Callable[[dict], Awaitable[str]]  # renvoie un résumé en français
    # Risque dépendant des paramètres (ex. protocole) ; défaut : risque statique
    risk_fn: Callable[[dict], str] | None = field(default=None)

    def risk_for(self, params: dict) -> str:
        if self.risk_fn is not None:
            risk = self.risk_fn(params)
            return risk if risk in RISK_LEVELS else "sensitive"
        return self.risk


class ActionRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ActionSpec] = {}

    def register(self, spec: ActionSpec) -> None:
        assert spec.id not in self._specs, f"action en double : {spec.id}"
        assert spec.risk in RISK_LEVELS, f"risque inconnu : {spec.risk}"
        self._specs[spec.id] = spec

    def get(self, action_id: str) -> ActionSpec | None:
        return self._specs.get(action_id)

    def all(self) -> list[ActionSpec]:
        return list(self._specs.values())
