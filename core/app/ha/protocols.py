"""Protocoles : des séquences d'actions nommées, déclenchables à la voix.

Ce module ne fait que CHARGER et DÉCRIRE les protocoles (config/protocols.yml).
Leur exécution est un exécuteur du moteur d'actions (`actions/executors.py`) —
comme toute écriture. Ajouter un protocole = éditer le YAML, redémarrer le
conteneur, zéro code (docs/PROTOCOLES.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..norm import normalize

log = logging.getLogger("sentinel.protocols")

_RISK_ALIASES = {
    "low": "low", "faible": "low",
    "medium": "medium", "moyen": "medium",
    "sensitive": "sensitive", "sensible": "sensitive",
}


@dataclass(frozen=True)
class ProtocolStep:
    service: str            # "domaine.service", ex. "cover.close_cover"
    data: dict | None
    target: dict | None     # cibles natives HA : entity_id / area_id / device_id


@dataclass(frozen=True)
class Protocol:
    key: str                # clé YAML (normalisée)
    display: str            # nom affiché / prononcé
    risk: str               # low | medium | sensitive
    announce: str           # phrase dite à l'exécution
    phrases: tuple[str, ...]  # déclencheurs vocaux (normalisés)
    steps: tuple[ProtocolStep, ...]


class ProtocolBook:
    def __init__(self, protocols: list[Protocol]):
        self._by_key = {p.key: p for p in protocols}

    @classmethod
    def load(cls, path: Path) -> "ProtocolBook":
        if not path.is_file():
            log.warning("Aucun fichier de protocoles (%s)", path)
            return cls([])
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            log.error("protocols.yml invalide : %s", exc)
            return cls([])
        if not isinstance(raw, dict):
            log.error("protocols.yml doit être un dictionnaire {clé: protocole}")
            return cls([])

        protocols: list[Protocol] = []
        for key, spec in raw.items():
            proto = cls._parse(str(key), spec)
            if proto:
                protocols.append(proto)
        log.info("Protocoles chargés : %s", ", ".join(p.display for p in protocols) or "aucun")
        return cls(protocols)

    @staticmethod
    def _parse(key: str, spec) -> Protocol | None:
        if not isinstance(spec, dict):
            log.error("Protocole « %s » ignoré : format invalide", key)
            return None
        display = str(spec.get("nom") or key).strip()
        risk = _RISK_ALIASES.get(str(spec.get("risque", "medium")).lower())
        if risk is None:
            log.error("Protocole « %s » ignoré : risque inconnu (faible/moyen/sensible)", key)
            return None

        steps: list[ProtocolStep] = []
        for i, action in enumerate(spec.get("actions") or []):
            if not isinstance(action, dict) or "." not in str(action.get("service", "")):
                log.error("Protocole « %s » : action %d invalide (champ service requis)", key, i + 1)
                return None
            target = action.get("target")
            if isinstance(target, dict):
                # entity_id: "x" → ["x"] (HA accepte les deux, on normalise)
                for field in ("entity_id", "area_id", "device_id"):
                    if isinstance(target.get(field), str):
                        target[field] = [target[field]]
            steps.append(ProtocolStep(
                service=str(action["service"]),
                data=action.get("data") or None,
                target=target or None,
            ))
        if not steps:
            log.error("Protocole « %s » ignoré : aucune action", key)
            return None

        norm_key = normalize(key)
        phrases = {normalize(p) for p in (spec.get("phrases") or []) if str(p).strip()}
        # Déclencheurs implicites : « protocole X », « mode X »
        phrases.update({f"protocole {normalize(display)}", f"mode {normalize(display)}",
                        f"protocole {norm_key}"})
        return Protocol(
            key=norm_key,
            display=display,
            risk=risk,
            announce=str(spec.get("annonce") or f"Protocole {display} exécuté.").strip(),
            phrases=tuple(sorted(phrases)),
            steps=tuple(steps),
        )

    # ── Recherche ────────────────────────────────────────────────────────

    def get(self, name: str) -> Protocol | None:
        norm = normalize(name)
        if norm in self._by_key:
            return self._by_key[norm]
        for proto in self._by_key.values():
            if normalize(proto.display) == norm:
                return proto
        return None

    def find_in_text(self, text: str) -> Protocol | None:
        """Trouve un protocole dont une phrase de déclenchement figure dans le texte."""
        phrase = f" {normalize(text)} "
        best: Protocol | None = None
        best_len = 0
        for proto in self._by_key.values():
            for trigger in proto.phrases:
                if f" {trigger} " in phrase and len(trigger) > best_len:
                    best, best_len = proto, len(trigger)
        return best

    def all(self) -> list[Protocol]:
        return sorted(self._by_key.values(), key=lambda p: p.display)
