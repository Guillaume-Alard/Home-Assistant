"""Santé de Nova (Home Assistant) : lecture pure, aucune écriture ici.

Alimente l'outil LLM `sante_systemes`, l'intent local « comment va la maison »
et l'audit à la demande. On observe l'état de Nova — connexion, entités
indisponibles, mises à jour en attente — pour savoir si HA va mal et proposer
la réparation.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import Settings
from ..ha.client import HAClient

log = logging.getLogger("sentinel.health")

# Domaines dont l'état « unknown/unavailable » est normal (bruit, pas une panne)
_NOISE_DOMAINS = {
    "button", "event", "scene", "tts", "stt", "conversation", "wake_word",
    "input_button", "update",
}

_GRAVITY_ORDER = {"critique": 0, "attention": 1, "info": 2}


class HealthService:
    def __init__(self, settings: Settings, ha: HAClient | None = None):
        self._settings = settings
        self._ha = ha

    async def close(self) -> None:
        return None

    # ── Instantané ───────────────────────────────────────────────────────

    def _nova(self) -> dict:
        if self._ha is None:
            return {"configuree": False}
        if not self._ha.connected:
            return {"configuree": True, "connectee": False}
        states = self._ha.states_snapshot()
        indisponibles = sorted(
            entity_id
            for entity_id, state in states.items()
            if state.get("state") in ("unavailable", "unknown")
            and entity_id.split(".", 1)[0] not in _NOISE_DOMAINS
        )
        mises_a_jour = sorted(
            self._ha.friendly_name(entity_id).removesuffix(" Update").removesuffix(" update")
            for entity_id, state in states.items()
            if entity_id.startswith("update.") and state.get("state") == "on"
        )
        return {
            "configuree": True,
            "connectee": True,
            "version": getattr(self._ha, "ha_version", None),
            "entites": len(states),
            "nb_indisponibles": len(indisponibles),
            "indisponibles": indisponibles[:25],
            "mises_a_jour": mises_a_jour[:15],
        }

    async def snapshot(self) -> dict:
        return {
            "genere_le": self._now().isoformat(timespec="seconds"),
            "nova": self._nova(),
        }

    # ── Résumé prononçable ───────────────────────────────────────────────

    async def resume_texte(self, snap: dict | None = None) -> str:
        if snap is None:
            snap = await self.snapshot()
        nova = snap["nova"]
        if not nova.get("configuree"):
            return "Nova n'est pas configurée."
        if not nova.get("connectee"):
            return "Nova est déconnectée !"
        extra = []
        if nova["nb_indisponibles"]:
            extra.append(f"{nova['nb_indisponibles']} indisponible(s)")
        if nova["mises_a_jour"]:
            extra.append(f"{len(nova['mises_a_jour'])} mise(s) à jour en attente")
        suffix = f", {', '.join(extra)}" if extra else ""
        phrase = f"Nova : connectée, {nova['entites']} entités{suffix}."
        return phrase if extra else f"{phrase} Rien d'anormal."

    # ── Audit déterministe ───────────────────────────────────────────────

    async def audit(self, snap: dict | None = None) -> dict:
        if snap is None:
            snap = await self.snapshot()
        constats: list[dict] = []

        def add(gravite: str, sujet: str, detail: str, action: str | None = None):
            item = {"gravite": gravite, "sujet": sujet, "detail": detail}
            if action:
                item["action"] = action
            constats.append(item)

        nova = snap["nova"]
        if nova.get("configuree"):
            if not nova.get("connectee"):
                add("critique", "Nova", "La connexion à Home Assistant est perdue.",
                    "Vérifier Nova et le jeton HA_TOKEN.")
            else:
                if nova["mises_a_jour"]:
                    add("info", "Mises à jour",
                        "Disponibles : " + ", ".join(nova["mises_a_jour"]),
                        "Analyser les changelogs puis proposer l'installation.")
                if nova["nb_indisponibles"] > 10:
                    add("attention", "Entités Nova",
                        f"{nova['nb_indisponibles']} entités indisponibles — une intégration est peut-être tombée.")
                elif nova["nb_indisponibles"]:
                    add("info", "Entités Nova",
                        "Indisponibles : " + ", ".join(nova["indisponibles"][:8]))

        constats.sort(key=lambda c: _GRAVITY_ORDER.get(c["gravite"], 9))
        return {"genere_le": snap["genere_le"], "ok": not constats, "constats": constats}

    def _now(self) -> datetime:
        try:
            return datetime.now(ZoneInfo(self._settings.tz))
        except Exception:
            return datetime.now()
