"""Les exécuteurs : seul code du projet (avec ha/client.py) autorisé à écrire.

Chaque exécuteur valide strictement ses paramètres (domaines autorisés, bornes)
avant d'appeler Nova — même une proposition approuvée ne peut pas sortir du
cadre déclaré ici. Un test statique (tests/test_invariant.py) garantit qu'aucun
autre module n'appelle `call_service`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
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


# ── Vérification (brique 1) : relecture d'état, LECTURE SEULE ─────────────
#
# Après une écriture, on relit l'état de Nova pour confirmer que l'ordre a bien
# pris effet. Le cache d'états est alimenté par le WebSocket (événements
# state_changed) : on laisse donc quelques instants à l'état pour se propager.
# On confirme au premier état conforme ; sinon on épuise un budget court. Aucune
# de ces fonctions n'écrit — elles n'appellent QUE `get_state` (l'invariant de
# sécurité reste vert).

# États « indisponibles » : ni allumé, ni éteint — on ne peut rien confirmer.
_INDISPO = {"", "unavailable", "unknown", "none"}


def _state_str(state: dict | None) -> str:
    return str((state or {}).get("state") or "").strip().lower()


def _is_on(state: str) -> bool:
    """« Allumé-ish » : tout sauf éteint/veille/indisponible. Couvre aussi les
    media_player (idle/playing/on) qui ne rapportent pas littéralement « on »."""
    return state not in _INDISPO and state not in {"off", "standby"}


async def _await_state(
    ha: HAClient,
    entity_ids: list[str],
    ok: Callable[[dict | None], bool],
    *,
    tries: int = 4,
    delay: float = 0.25,
) -> list[str]:
    """Relit l'état jusqu'à ce que TOUTES les entités satisfassent `ok`, ou
    épuisement du budget (~0,75 s). Renvoie la liste des entités encore non
    conformes (vide = tout est confirmé)."""
    pending = list(entity_ids)
    for attempt in range(tries):
        pending = [e for e in pending if not ok(ha.get_state(e))]
        if not pending:
            return []
        if attempt < tries - 1:
            await asyncio.sleep(delay)
    return pending


def build_registry(
    ha: HAClient | None,
    protocols: "ProtocolBook | None" = None,
) -> ActionRegistry:
    reg = ActionRegistry()

    if ha is None:
        return reg

    # ── Allumer / éteindre (domaines sûrs uniquement) ────────────────────

    async def turn_on(params: dict) -> str:
        ids = _entity_ids(params, ONOFF_DOMAINS)
        await ha.call_service("homeassistant", "turn_on", target={"entity_id": ids})
        return f"Allumé : {_friendly_list(ha, ids)}."

    async def turn_off(params: dict) -> str:
        ids = _entity_ids(params, ONOFF_DOMAINS)
        await ha.call_service("homeassistant", "turn_off", target={"entity_id": ids})
        return f"Éteint : {_friendly_list(ha, ids)}."

    async def verify_turn_on(params: dict) -> tuple[bool | None, str]:
        ids = _entity_ids(params, ONOFF_DOMAINS)
        reste = await _await_state(ha, ids, lambda s: _is_on(_state_str(s)))
        return (True, "") if not reste else (False, f"{_friendly_list(ha, reste)} pas encore allumé côté Nova")

    async def verify_turn_off(params: dict) -> tuple[bool | None, str]:
        ids = _entity_ids(params, ONOFF_DOMAINS)
        reste = await _await_state(ha, ids, lambda s: _state_str(s) == "off")
        return (True, "") if not reste else (False, f"{_friendly_list(ha, reste)} pas encore éteint côté Nova")

    reg.register(ActionSpec("ha.turn_on", "Allumer lumières/prises/ventilateurs", "low", True, turn_on, verify=verify_turn_on))
    reg.register(ActionSpec("ha.turn_off", "Éteindre lumières/prises/ventilateurs", "low", True, turn_off, verify=verify_turn_off))

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

    async def verify_cover(params: dict) -> tuple[bool | None, str]:
        # Un volet met du temps à bouger : on confirme que le mouvement a démarré
        # (ou est déjà arrivé) dans le bon sens. « stop » laisse une position
        # indéterminée → non vérifiable.
        attendu = {"open": {"open", "opening"}, "close": {"closed", "closing"}}.get(params.get("op"))
        if attendu is None:
            return None, ""
        ids = _entity_ids(params, {"cover"})
        reste = await _await_state(ha, ids, lambda s: _state_str(s) in attendu)
        if not reste:
            return True, ""
        sens = "ouvre" if params.get("op") == "open" else "ferme"
        return False, f"{_friendly_list(ha, reste)} ne s'{sens} pas côté Nova"

    reg.register(ActionSpec("ha.cover", "Ouvrir/fermer/stopper des volets", "low", True, cover, verify=verify_cover))

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

    async def verify_climate(params: dict) -> tuple[bool | None, str]:
        try:
            cible = float(params.get("temperature"))
        except (TypeError, ValueError):
            return None, ""
        ids = _entity_ids(params, {"climate"})

        def atteinte(state: dict | None) -> bool:
            cur = ((state or {}).get("attributes") or {}).get("temperature")
            try:
                return cur is not None and abs(float(cur) - cible) < 0.15
            except (TypeError, ValueError):
                return False

        reste = await _await_state(ha, ids, atteinte)
        return (True, "") if not reste else (False, f"consigne non prise en compte sur {_friendly_list(ha, reste)}")

    reg.register(ActionSpec(
        "ha.climate_set_temperature", "Régler une consigne de chauffage (5–30 °C)",
        "medium", True, climate, verify=verify_climate,
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

    async def verify_lock(params: dict) -> tuple[bool | None, str]:
        ids = _entity_ids(params, {"lock"})
        reste = await _await_state(ha, ids, lambda s: _state_str(s) in {"locked", "locking"})
        return (True, "") if not reste else (False, f"{_friendly_list(ha, reste)} pas encore verrouillé côté Nova")

    async def verify_unlock(params: dict) -> tuple[bool | None, str]:
        ids = _entity_ids(params, {"lock"})
        reste = await _await_state(ha, ids, lambda s: _state_str(s) in {"unlocked", "unlocking"})
        return (True, "") if not reste else (False, f"{_friendly_list(ha, reste)} pas encore déverrouillé côté Nova")

    reg.register(ActionSpec("ha.lock", "Verrouiller des serrures", "medium", True, lock, verify=verify_lock))
    reg.register(ActionSpec("ha.unlock", "Déverrouiller des serrures", "sensitive", True, unlock, verify=verify_unlock))

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

    async def verify_alarm_arm(params: dict) -> tuple[bool | None, str]:
        ids = _alarm_targets(params)
        # L'armement passe souvent par un état transitoire « arming » (délai de sortie).
        reste = await _await_state(
            ha, ids, lambda s: _state_str(s) == "arming" or _state_str(s).startswith("armed")
        )
        return (True, "") if not reste else (False, "l'alarme n'est pas encore armée côté Nova")

    async def verify_alarm_disarm(params: dict) -> tuple[bool | None, str]:
        ids = _alarm_targets(params)
        reste = await _await_state(ha, ids, lambda s: _state_str(s) == "disarmed")
        return (True, "") if not reste else (False, "l'alarme n'est pas encore désarmée côté Nova")

    reg.register(ActionSpec("ha.alarm_arm", "Armer l'alarme", "medium", True, alarm_arm, verify=verify_alarm_arm))
    reg.register(ActionSpec("ha.alarm_disarm", "Désarmer l'alarme", "sensitive", True, alarm_disarm, verify=verify_alarm_disarm))

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

    # ── Musique (media_player — courant, jamais sensible) ────────────────

    _MEDIA_SIMPLE = {
        "play": ("media_play", "Lecture"), "pause": ("media_pause", "Pause"),
        "playpause": ("media_play_pause", "Lecture/pause"), "stop": ("media_stop", "Arrêt"),
        "next": ("media_next_track", "Piste suivante"), "previous": ("media_previous_track", "Piste précédente"),
        "volume_up": ("volume_up", "Volume +"), "volume_down": ("volume_down", "Volume −"),
    }

    async def media(params: dict) -> str:
        op = params.get("op")
        ids = _entity_ids(params, {"media_player"})
        who = _friendly_list(ha, ids)
        if op in _MEDIA_SIMPLE:
            service, verbe = _MEDIA_SIMPLE[op]
            await ha.call_service("media_player", service, target={"entity_id": ids})
            return f"{verbe} : {who}."
        if op == "volume":
            try:
                level = float(params.get("level"))
            except (TypeError, ValueError):
                raise ActionError("Niveau de volume invalide.") from None
            level = max(0.0, min(1.0, level))
            await ha.call_service("media_player", "volume_set",
                                  data={"volume_level": level}, target={"entity_id": ids})
            return f"Volume à {round(level * 100)} % : {who}."
        if op in ("mute", "unmute"):
            await ha.call_service("media_player", "volume_mute",
                                  data={"is_volume_muted": op == "mute"}, target={"entity_id": ids})
            return f"{'Son coupé' if op == 'mute' else 'Son rétabli'} : {who}."
        if op == "source":
            source = str(params.get("source") or "").strip()
            if not source:
                raise ActionError("Aucune source précisée.")
            await ha.call_service("media_player", "select_source",
                                  data={"source": source}, target={"entity_id": ids})
            return f"Source « {source} » : {who}."
        if op == "play_media":
            content_id = str(params.get("media_content_id") or "").strip()
            content_type = str(params.get("media_content_type") or "music").strip()
            if not content_id:
                raise ActionError("Contenu à jouer manquant.")
            await ha.call_service("media_player", "play_media", data={
                "media_content_id": content_id, "media_content_type": content_type,
            }, target={"entity_id": ids})
            return f"Lecture lancée : {who}."
        if op == "join":
            members = params.get("group_members") or []
            members = _entity_ids({"entity_ids": members}, {"media_player"})
            await ha.call_service("media_player", "join",
                                  data={"group_members": members}, target={"entity_id": ids})
            return f"Regroupé avec {_friendly_list(ha, members)}."
        if op == "unjoin":
            await ha.call_service("media_player", "unjoin", target={"entity_id": ids})
            return f"Dégroupé : {who}."
        raise ActionError(f"Opération musique inconnue : {op}.")

    reg.register(ActionSpec(
        "ha.media", "Contrôler la musique (lecture, volume, source, multi-pièces)",
        "low", True, media,
    ))

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

    def generic_service_risk(params: dict) -> str:
        """Le service générique porte le risque de sa charge utile : les services
        qui désarment, ouvrent un accès ou exécutent du code sont sensibles —
        leur proposition ne pourra donc pas être approuvée à la voix."""
        domain = str(params.get("domain") or "").lower()
        service = str(params.get("service") or "").lower()
        if domain in {"shell_command", "hassio", "python_script"}:
            return "sensitive"
        if (domain, service) in {
            ("lock", "unlock"), ("lock", "open"),
            ("alarm_control_panel", "alarm_disarm"),
            ("homeassistant", "restart"), ("homeassistant", "stop"),
        }:
            return "sensitive"
        return "medium"

    reg.register(ActionSpec(
        "ha.call_service",
        "Appeler un service Home Assistant quelconque (réservé aux propositions approuvées)",
        "medium", False, generic_service, risk_fn=generic_service_risk,
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
