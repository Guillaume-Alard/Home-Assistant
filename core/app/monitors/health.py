"""Agrégateur de santé : Nova, Nebula (système + Docker), Atrium.

Fournit le même socle à quatre consommateurs : l'outil LLM `sante_systemes`,
l'intent local « comment vont les systèmes », l'audit à la demande et le
rapport quotidien. Lecture pure — aucune écriture ici.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import Settings
from ..ha.client import HAClient
from ..norm import date_francaise
from .atrium import AtriumMonitor
from .docker import DockerError, DockerMonitor
from .system import read_system

log = logging.getLogger("sentinel.health")

# Domaines dont l'état « unknown/unavailable » est normal (bruit, pas une panne)
_NOISE_DOMAINS = {
    "button", "event", "scene", "tts", "stt", "conversation", "wake_word",
    "input_button", "update",
}

_GRAVITY_ORDER = {"critique": 0, "attention": 1, "info": 2}


def _fr(value: float) -> str:
    return f"{value:g}".replace(".", ",")


class HealthService:
    def __init__(
        self,
        settings: Settings,
        ha: HAClient | None = None,
        docker: DockerMonitor | None = None,
        atrium: AtriumMonitor | None = None,
    ):
        self._settings = settings
        self._ha = ha
        self._docker = docker
        self._atrium = atrium

    async def close(self) -> None:
        if self._docker:
            await self._docker.close()
        if self._atrium:
            await self._atrium.close()

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

    async def _docker_section(self) -> dict:
        assert self._docker is not None
        try:
            containers = await self._docker.containers()
        except DockerError as exc:
            return {"erreur": str(exc)}
        problemes = [
            {"nom": c["nom"], "etat": c["etat"], "status": c["status"]}
            for c in containers
            if c["etat"] != "running" or "unhealthy" in c["status"].lower()
        ]
        ports = sorted({p for c in containers for p in c["ports_publies"]})
        try:
            memoire = await self._docker.memory_usage()
        except DockerError:
            memoire = {}
        top_memoire = dict(sorted(memoire.items(), key=lambda kv: -kv[1])[:5])
        return {
            "total": len(containers),
            "en_marche": sum(1 for c in containers if c["etat"] == "running"),
            "problemes": problemes[:10],
            "top_memoire_mo": top_memoire,
            "ports_publies": ports[:25],
        }

    async def snapshot(self) -> dict:
        data: dict = {"genere_le": self._now().isoformat(timespec="seconds")}
        data["nova"] = self._nova()
        data["systeme"] = read_system()
        if self._docker:
            data["docker"] = await self._docker_section()
        if self._atrium:
            data["atrium"] = await self._atrium.check()
        return data

    # ── Résumé prononçable ───────────────────────────────────────────────

    async def resume_texte(self) -> str:
        snap = await self.snapshot()
        parts: list[str] = []
        souci = False

        nova = snap["nova"]
        if nova.get("configuree"):
            if not nova.get("connectee"):
                parts.append("Nova est déconnectée !")
                souci = True
            else:
                extra = []
                if nova["nb_indisponibles"]:
                    extra.append(f"{nova['nb_indisponibles']} indisponible(s)")
                if nova["mises_a_jour"]:
                    extra.append(f"{len(nova['mises_a_jour'])} mise(s) à jour en attente")
                suffix = f", {', '.join(extra)}" if extra else ""
                parts.append(f"Nova : connectée, {nova['entites']} entités{suffix}.")
                souci = souci or bool(extra)

        systeme = snap["systeme"]
        if systeme.get("charge") and systeme.get("ram"):
            parts.append(
                f"Nebula : charge {_fr(systeme['charge'][0])} sur {systeme['coeurs']} cœurs, "
                f"mémoire utilisée à {systeme['ram']['utilisee_pct']} %."
            )

        docker = snap.get("docker")
        if docker:
            if docker.get("erreur"):
                parts.append(f"Docker : {docker['erreur']}")
                souci = True
            else:
                phrase = f"Docker : {docker['en_marche']} conteneurs en marche sur {docker['total']}."
                if docker["problemes"]:
                    noms = ", ".join(f"{p['nom']} ({p['etat']})" for p in docker["problemes"][:3])
                    phrase += f" À surveiller : {noms}."
                    souci = True
                parts.append(phrase)

        atrium = snap.get("atrium")
        if atrium:
            if atrium.get("ok"):
                parts.append(f"Atrium : en ligne ({atrium['latence_ms']} ms).")
            else:
                parts.append("Atrium : injoignable !")
                souci = True

        if not parts:
            return "Aucun moniteur n'est configuré pour l'instant."
        if not souci:
            parts.append("Rien d'anormal.")
        return " ".join(parts)

    # ── Audit déterministe ───────────────────────────────────────────────

    async def audit(self) -> dict:
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

        systeme = snap["systeme"]
        if systeme.get("charge") and systeme["charge"][0] > (systeme.get("coeurs") or 1):
            add("attention", "Charge CPU",
                f"Charge {_fr(systeme['charge'][0])} pour {systeme['coeurs']} cœurs.")
        ram = systeme.get("ram") or {}
        if ram.get("utilisee_pct", 0) >= 90:
            add("attention", "Mémoire Nebula", f"RAM utilisée à {ram['utilisee_pct']} %.")

        docker = snap.get("docker")
        if docker and not docker.get("erreur"):
            for p in docker["problemes"]:
                gravite = "critique" if p["etat"] in ("restarting",) or "unhealthy" in p["status"].lower() else "attention"
                add(gravite, f"Conteneur {p['nom']}", f"État : {p['etat']} ({p['status']}).",
                    f"Proposer un redémarrage de {p['nom']}.")
            seuil = self._settings.container_mem_mo
            for nom, mo in (docker.get("top_memoire_mo") or {}).items():
                if mo >= seuil:
                    add("attention", f"Conteneur {nom}", f"Mémoire : {mo} Mo (seuil {seuil} Mo).")
            if docker.get("ports_publies"):
                add("info", "Surface réseau",
                    f"{len(docker['ports_publies'])} port(s) publié(s) : "
                    + ", ".join(docker["ports_publies"][:12]))
        elif docker:
            add("attention", "Docker", docker["erreur"])

        atrium = snap.get("atrium")
        if atrium and not atrium.get("ok"):
            add("critique", "Atrium", "Le healthcheck HTTP échoue.",
                "Vérifier le conteneur d'Atrium et ses logs.")

        constats.sort(key=lambda c: _GRAVITY_ORDER.get(c["gravite"], 9))
        return {"genere_le": snap["genere_le"], "ok": not constats, "constats": constats}

    # ── Rapport quotidien ────────────────────────────────────────────────

    async def rapport_quotidien(self, propositions_en_attente: int) -> str:
        resume = await self.resume_texte()
        audit = await self.audit()
        lines = [f"Rapport du {date_francaise(self._now())}.", resume]
        importants = [c for c in audit["constats"] if c["gravite"] != "info"]
        for c in importants[:5]:
            lines.append(f"⚠ {c['sujet']} : {c['detail']}")
        if propositions_en_attente:
            lines.append(
                f"{propositions_en_attente} proposition(s) en attente de ta décision (panneau ▤)."
            )
        return "\n".join(lines)

    def _now(self) -> datetime:
        try:
            return datetime.now(ZoneInfo(self._settings.tz))
        except Exception:
            return datetime.now()
