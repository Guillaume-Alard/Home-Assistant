"""Les exécuteurs : seul code du projet (avec ha/client.py) autorisé à écrire.

Chaque exécuteur valide strictement ses paramètres (domaines autorisés, bornes)
avant d'appeler Nova — même une proposition approuvée ne peut pas sortir du
cadre déclaré ici. Un test statique (tests/test_invariant.py) garantit qu'aucun
autre module n'appelle `call_service`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..ha.client import HAClient
from .registry import ActionError, ActionRegistry, ActionSpec

if TYPE_CHECKING:  # uniquement pour les annotations — pas d'import circulaire
    from ..ha.protocols import ProtocolBook

log = logging.getLogger("sentinel.actions")

# Domaines dont l'allumage/extinction est considéré sans risque
ONOFF_DOMAINS = {"light", "switch", "fan", "media_player", "humidifier", "input_boolean"}


def _entity_ids(params: dict, allowed_domains: set[str] | None = None) -> list[str]:
    raw = params.get("entity_ids")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw or not all(isinstance(e, str) and "." in e for e in raw):
        raise ActionError("Paramètre entity_ids manquant ou invalide.")
    if allowed_domains is not None:
        for entity_id in raw:
            domain = entity_id.split(".", 1)[0]
            if domain not in allowed_domains:
                raise ActionError(
                    f"Le domaine « {domain} » n'est pas autorisé pour cette action ({entity_id})."
                )
    return raw


def _friendly_list(ha: HAClient, entity_ids: list[str]) -> str:
    return ", ".join(ha.friendly_name(e) for e in entity_ids)


def build_registry(ha: HAClient, protocols: "ProtocolBook | None" = None) -> ActionRegistry:
    reg = ActionRegistry()

    # ── Allumer / éteindre (domaines sûrs uniquement) ────────────────────

    async def turn_on(params: dict) -> str:
        ids = _entity_ids(params, ONOFF_DOMAINS)
        await ha.call_service("homeassistant", "turn_on", target={"entity_id": ids})
        return f"Allumé : {_friendly_list(ha, ids)}."

    async def turn_off(params: dict) -> str:
        ids = _entity_ids(params, ONOFF_DOMAINS)
        await ha.call_service("homeassistant", "turn_off", target={"entity_id": ids})
        return f"Éteint : {_friendly_list(ha, ids)}."

    reg.register(ActionSpec("ha.turn_on", "Allumer lumières/prises/ventilateurs", "low", True, turn_on))
    reg.register(ActionSpec("ha.turn_off", "Éteindre lumières/prises/ventilateurs", "low", True, turn_off))

    # ── Volets ───────────────────────────────────────────────────────────

    async def cover(params: dict) -> str:
        op = params.get("op")
        services = {"open": "open_cover", "close": "close_cover", "stop": "stop_cover"}
        if op not in services:
            raise ActionError("Opération de volet invalide (open/close/stop).")
        ids = _entity_ids(params, {"cover"})
        await ha.call_service("cover", services[op], target={"entity_id": ids})
        verbe = {"open": "Ouverture", "close": "Fermeture", "stop": "Arrêt"}[op]
        return f"{verbe} : {_friendly_list(ha, ids)}."

    reg.register(ActionSpec("ha.cover", "Ouvrir/fermer/stopper des volets", "low", True, cover))

    # ── Scènes ───────────────────────────────────────────────────────────

    async def scene(params: dict) -> str:
        ids = _entity_ids(params, {"scene"})
        await ha.call_service("scene", "turn_on", target={"entity_id": ids})
        return f"Scène activée : {_friendly_list(ha, ids)}."

    reg.register(ActionSpec("ha.scene", "Activer une scène", "low", True, scene))

    # ── Chauffage ────────────────────────────────────────────────────────

    async def climate(params: dict) -> str:
        ids = _entity_ids(params, {"climate"})
        try:
            temp = float(params.get("temperature"))
        except (TypeError, ValueError):
            raise ActionError("Température invalide.") from None
        if not 5.0 <= temp <= 30.0:
            raise ActionError("Température hors bornes (5–30 °C).")
        await ha.call_service(
            "climate", "set_temperature", data={"temperature": temp}, target={"entity_id": ids}
        )
        return f"Consigne réglée à {temp:g} °C : {_friendly_list(ha, ids)}."

    reg.register(ActionSpec(
        "ha.climate_set_temperature", "Régler une consigne de chauffage (5–30 °C)",
        "medium", True, climate,
    ))

    # ── Serrures ─────────────────────────────────────────────────────────

    async def lock(params: dict) -> str:
        ids = _entity_ids(params, {"lock"})
        await ha.call_service("lock", "lock", target={"entity_id": ids})
        return f"Verrouillé : {_friendly_list(ha, ids)}."

    async def unlock(params: dict) -> str:
        ids = _entity_ids(params, {"lock"})
        await ha.call_service("lock", "unlock", target={"entity_id": ids})
        return f"Déverrouillé : {_friendly_list(ha, ids)}."

    reg.register(ActionSpec("ha.lock", "Verrouiller des serrures", "medium", True, lock))
    reg.register(ActionSpec("ha.unlock", "Déverrouiller des serrures", "sensitive", True, unlock))

    # ── Alarme ───────────────────────────────────────────────────────────

    def _alarm_targets(params: dict) -> list[str]:
        ids = params.get("entity_ids") or ha.entities_by_domain("alarm_control_panel")
        if not ids:
            raise ActionError("Aucune alarme trouvée dans Nova.")
        return _entity_ids({"entity_ids": ids}, {"alarm_control_panel"})

    async def alarm_arm(params: dict) -> str:
        mode = params.get("mode", "away")
        services = {"away": "alarm_arm_away", "home": "alarm_arm_home", "night": "alarm_arm_night"}
        if mode not in services:
            raise ActionError("Mode d'armement invalide (away/home/night).")
        ids = _alarm_targets(params)
        await ha.call_service("alarm_control_panel", services[mode], target={"entity_id": ids})
        return f"Alarme armée (mode {mode})."

    async def alarm_disarm(params: dict) -> str:
        ids = _alarm_targets(params)
        data = {"code": params["code"]} if params.get("code") else None
        await ha.call_service("alarm_control_panel", "alarm_disarm", data=data, target={"entity_id": ids})
        return "Alarme désarmée."

    reg.register(ActionSpec("ha.alarm_arm", "Armer l'alarme", "medium", True, alarm_arm))
    reg.register(ActionSpec("ha.alarm_disarm", "Désarmer l'alarme", "sensitive", True, alarm_disarm))

    # ── Notifications ────────────────────────────────────────────────────

    async def notify(params: dict) -> str:
        service = str(params.get("service") or "notify")
        service = service.removeprefix("notify.")
        if not service.replace("_", "").isalnum():
            raise ActionError("Service de notification invalide.")
        message = str(params.get("message") or "").strip()
        if not message:
            raise ActionError("Message de notification vide.")
        data: dict = {"message": message}
        if params.get("title"):
            data["title"] = str(params["title"])
        await ha.call_service("notify", service, data=data)
        return f"Notification envoyée ({service})."

    reg.register(ActionSpec("ha.notify", "Envoyer une notification", "low", True, notify))

    # ── Service générique (propositions uniquement) ──────────────────────

    async def generic_service(params: dict) -> str:
        domain = str(params.get("domain") or "").strip()
        service = str(params.get("service") or "").strip()
        if not domain or not service:
            raise ActionError("Domaine ou service manquant.")
        await ha.call_service(
            domain, service,
            data=params.get("data") or None,
            target=params.get("target") or None,
        )
        return f"Service {domain}.{service} appelé."

    reg.register(ActionSpec(
        "ha.call_service",
        "Appeler un service Home Assistant quelconque (réservé aux propositions approuvées)",
        "medium", False, generic_service,
    ))

    # ── Protocoles ───────────────────────────────────────────────────────

    if protocols is not None:

        async def run_protocol(params: dict) -> str:
            name = str(params.get("name") or "")
            proto = protocols.get(name)
            if proto is None:
                raise ActionError(f"Protocole inconnu : « {name} ».")
            errors: list[str] = []
            done = 0
            for step in proto.steps:
                domain, _, service = step.service.partition(".")
                try:
                    await ha.call_service(domain, service, data=step.data, target=step.target)
                    done += 1
                except Exception as exc:  # une étape ratée n'arrête pas le protocole
                    log.warning("Protocole %s : étape %s en échec : %s", name, step.service, exc)
                    errors.append(f"{step.service} ({exc})")
                await asyncio.sleep(0.2)  # laisse Nova respirer entre les étapes
            if done == 0 and errors:
                raise ActionError(f"Protocole {proto.display} : toutes les étapes ont échoué.")
            summary = proto.announce or f"Protocole {proto.display} exécuté."
            if errors:
                summary += f" Attention, {len(errors)} étape(s) en échec : {', '.join(errors)}."
            return summary

        def protocol_risk(params: dict) -> str:
            proto = protocols.get(str(params.get("name") or ""))
            return proto.risk if proto else "sensitive"

        reg.register(ActionSpec(
            "protocol.run", "Déclencher un protocole (séquence d'actions nommée)",
            "medium", True, run_protocol, risk_fn=protocol_risk,
        ))

    return reg
