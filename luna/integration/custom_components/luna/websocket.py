"""Les commandes `luna/*` exposées à la carte Loggia.

Contrat : luna/docs/P1-CONTRATS.md §4.

**Le point important est le contexte.** Il est construit ici, à partir de
`connection.user` — l'utilisateur Home Assistant réellement authentifié derrière
la connexion WebSocket. La carte ne le fournit pas et ne peut pas l'influencer :
c'est ce qui empêche un client de se déclarer administrateur. L'add-on résout
ensuite le profil Luna à partir de ce nom (décision A6).
"""

from __future__ import annotations

import logging
from ipaddress import ip_address
from typing import Any

import voluptuous as vol
from homeassistant.components import assist_pipeline, http, tts, websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import network as reseau
from homeassistant.util import network as util_reseau

from .client import ClientRelais, ErreurLuna
from .const import DOMAINE, TAILLE_AUDIO_MAX

_LOGGER = logging.getLogger(__name__)

CLE_ENREGISTRE = f"{DOMAINE}_commandes_enregistrees"

#: Ces événements ferment la souscription : après, plus rien n'arrive (§4).
EVENEMENTS_TERMINAUX = ("done", "error")

#: Ce que la carte joint à **toutes** ses commandes, sans exception.
#:
#: `luna-card.js` construit chaque message avec `client_id` et `device` — le
#: second est l'identifiant d'appareil de C6, celui qui fait vivre l'identité par
#: appareil et non globalement. Une commande qui ne les déclare pas voit son
#: message rejeté par Home Assistant **avant** d'atteindre l'intégration, et la
#: carte affiche « not a valid option at "device" ».
#:
#: Les déclarer ici plutôt qu'à chaque commande n'est pas qu'une économie de
#: lignes : c'est ce qui empêche d'en oublier une. `tests/test_integration.py`
#: vérifie que les 22 commandes portent bien l'enveloppe.
ENVELOPPE_CARTE: dict = {
    vol.Optional("client_id"): str,
    vol.Optional("device"): str,
}


def enregistrer_commandes(hass: HomeAssistant) -> None:
    """Une seule fois par démarrage, même si l'entrée est rechargée."""
    if hass.data.get(CLE_ENREGISTRE):
        return
    hass.data[CLE_ENREGISTRE] = True
    for commande in (
        ws_info,
        ws_chat,
        ws_cancel,
        ws_history,
        ws_feed,
        ws_proposal_decide,
        ws_speak,
        ws_identity_voice,
        ws_identity_confirm,
        ws_enroll_start,
        ws_enroll_sample,
        ws_enroll_finish,
        ws_identity_forget,
        ws_identity,
        ws_alerts_feedback,
        ws_alerts_act,
        ws_patterns,
        ws_suggestions,
        ws_facts,
        ws_facts_decide,
        ws_health,
        ws_identity_face,
    ):
        websocket_api.async_register_command(hass, commande)


@callback
def est_local(hass: HomeAssistant) -> bool:
    """La requête vient-elle du réseau de la maison ?

    §6 : « La biométrie n'est active que sur le réseau local. Aucune frame
    caméra ne transite par le relais Nabu Casa. » Deux cas à écarter : le relais
    Nabu Casa, et un accès direct depuis internet.

    Sans requête courante — l'agent de conversation appelle hors contexte HTTP —
    on répond « local » : ce chemin ne fait de toute façon aucune biométrie.
    """
    if reseau.is_cloud_connection(hass):
        return False
    requete = http.current_request.get()
    if requete is None or not requete.remote:
        return True
    try:
        return util_reseau.is_local(ip_address(requete.remote))
    except ValueError:
        return False


@callback
def _contexte(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> dict[str, Any]:
    utilisateur = connection.user
    return {
        "ha_user_id": utilisateur.id,
        "ha_user_name": utilisateur.name,
        "is_admin": utilisateur.is_admin,
        # Le profil est résolu par l'add-on (A6, puis C6) ; ce qu'on envoie ici
        # n'est qu'une valeur de repli, jamais une affirmation.
        "profile": "unknown",
        "client_id": msg.get("client_id") or "loggia",
        "local": est_local(hass),
        # C6 : l'identité vit par appareil, pas globalement.
        "device": msg.get("device"),
    }


@callback
def _refus_distant(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> bool:
    """Refuse la biométrie à distance, **avant** de relayer quoi que ce soit.

    C'est tout l'intérêt de faire le contrôle ici plutôt que dans l'add-on : à
    l'intérieur, l'audio aurait déjà traversé le relais Nabu Casa que §6
    interdit. Ici, il ne part pas.
    """
    if est_local(hass):
        return False
    connection.send_error(
        msg["id"],
        "remote_biometrics",
        "Je ne reconnais les voix que sur le réseau de la maison. "
        "À distance, passe par le PIN de Loggia.",
    )
    return True


@callback
def _audio_valide(
    connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> bool:
    if len(msg.get("audio") or "") > TAILLE_AUDIO_MAX:
        connection.send_error(
            msg["id"], "audio_too_short", "Cet extrait est trop long pour moi."
        )
        return False
    return True


def _client(hass: HomeAssistant) -> ClientRelais:
    from . import client_actif

    return client_actif(hass)


async def _ponctuelle(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    op: str,
    charge: dict[str, Any],
) -> None:
    """Relaie une opération ponctuelle, en traduisant les pannes en messages."""
    try:
        resultat = await _client(hass).demander(
            op, charge, _contexte(hass, connection, msg)
        )
    except ErreurLuna as err:
        connection.send_error(msg["id"], err.code, err.message)
        return
    except Exception as err:  # jamais d'échec silencieux (§8)
        _LOGGER.exception("Luna : %s a échoué", op)
        connection.send_error(msg["id"], "internal", str(err))
        return
    connection.send_result(msg["id"], resultat)


def _flux(op: str):
    """Fabrique un gestionnaire de souscription pour `chat` et `feed`."""

    async def gestionnaire(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
        charge: dict[str, Any],
    ) -> None:
        identifiant = msg["id"]

        @callback
        def sur_evenement(trame: dict[str, Any]) -> None:
            if erreur := trame.get("error"):
                connection.send_message(
                    websocket_api.event_message(
                        identifiant,
                        {
                            "event": "error",
                            "code": erreur.get("code", "internal"),
                            "message": erreur.get("message", ""),
                        },
                    )
                )
                return
            charge_evt = {c: v for c, v in trame.items() if c != "id"}
            connection.send_message(websocket_api.event_message(identifiant, charge_evt))
            if charge_evt.get("event") in EVENEMENTS_TERMINAUX:
                _fermer(identifiant)

        @callback
        def _fermer(cle: int) -> None:
            if arreter := connection.subscriptions.pop(cle, None):
                arreter()

        try:
            arreter = await _client(hass).souscrire(
                op, charge, _contexte(hass, connection, msg), sur_evenement
            )
        except ErreurLuna as err:
            connection.send_error(identifiant, err.code, err.message)
            return

        connection.subscriptions[identifiant] = arreter
        connection.send_result(identifiant)

    return gestionnaire


# ── P1 ───────────────────────────────────────────────────────────────────


@websocket_api.websocket_command({**ENVELOPPE_CARTE, vol.Required("type"): "luna/info"})
@websocket_api.async_response
async def ws_info(hass, connection, msg) -> None:
    await _ponctuelle(hass, connection, msg, "info", {})


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/chat",
        vol.Required("text"): str,
        vol.Optional("conversation_id"): str,
    }
)
@websocket_api.async_response
async def ws_chat(hass, connection, msg) -> None:
    charge = {"text": msg["text"]}
    if identifiant := msg.get("conversation_id"):
        charge["conversation_id"] = identifiant
    await _flux("chat")(hass, connection, msg, charge)


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/cancel",
        vol.Required("message_id"): str,
    }
)
@websocket_api.async_response
async def ws_cancel(hass, connection, msg) -> None:
    await _ponctuelle(hass, connection, msg, "cancel", {"message_id": msg["message_id"]})


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/history",
        vol.Optional("conversation_id"): str,
        vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=200)),
    }
)
@websocket_api.async_response
async def ws_history(hass, connection, msg) -> None:
    charge: dict[str, Any] = {"limit": msg["limit"]}
    if identifiant := msg.get("conversation_id"):
        charge["conversation_id"] = identifiant
    await _ponctuelle(hass, connection, msg, "history", charge)


@websocket_api.websocket_command({**ENVELOPPE_CARTE, vol.Required("type"): "luna/feed"})
@websocket_api.async_response
async def ws_feed(hass, connection, msg) -> None:
    await _flux("feed")(hass, connection, msg, {})


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/proposal/decide",
        vol.Required("proposal_id"): str,
        vol.Required("decision"): vol.In(("accept", "reject")),
    }
)
@websocket_api.async_response
async def ws_proposal_decide(hass, connection, msg) -> None:
    await _ponctuelle(
        hass,
        connection,
        msg,
        "decide",
        {"proposal_id": msg["proposal_id"], "decision": msg["decision"]},
    )


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/speak",
        vol.Required("text"): str,
    }
)
@websocket_api.async_response
async def ws_speak(hass, connection, msg) -> None:
    """Synthétise une réponse et rend une URL de **même origine**.

    Décision B3 : c'est ici que se fait le travail, en Python, plutôt que dans
    la carte. Elle n'a donc ni `fetch()` à faire (§3) ni jeton à manipuler —
    elle pose l'URL dans un élément audio et c'est tout.

    La voix est celle du pipeline Assist préféré : Luna parle pareil qu'on
    l'écoute dans Loggia ou depuis un satellite.
    """
    moteur, langue, voix = _voix_du_pipeline(hass)
    if not moteur:
        connection.send_error(
            msg["id"],
            "tts_unavailable",
            "Aucune voix n'est configurée dans Home Assistant. "
            "Installe l'add-on Piper et ajoute-le à un pipeline Assist.",
        )
        return

    texte = msg["text"].strip()
    if not texte:
        connection.send_error(msg["id"], "internal", "Rien à dire.")
        return

    try:
        flux = tts.async_create_stream(
            hass,
            moteur,
            language=langue,
            options={"voice": voix} if voix else None,
        )
        flux.async_set_message(texte)
    except Exception as err:  # jamais d'échec silencieux (§8)
        _LOGGER.exception("Luna : synthèse impossible")
        connection.send_error(msg["id"], "tts_unavailable", str(err))
        return

    connection.send_result(msg["id"], {"url": flux.url, "engine": moteur})


@callback
def _voix_du_pipeline(hass: HomeAssistant) -> tuple[str | None, str | None, str | None]:
    """Moteur, langue et voix du pipeline Assist préféré.

    Si aucun pipeline n'est configuré, on retombe sur le moteur TTS par défaut
    de Home Assistant — mieux vaut une voix générique que pas de voix.
    """
    try:
        pipeline = assist_pipeline.async_get_pipeline(hass)
    except Exception:  # noqa: BLE001 — aucun pipeline configuré, ce n'est pas grave
        pipeline = None
    if pipeline is not None and pipeline.tts_engine:
        return pipeline.tts_engine, pipeline.tts_language, pipeline.tts_voice
    return tts.async_resolve_engine(hass, None), None, None


# ── Documentées en P1, vivantes plus tard (§12) ──────────────────────────
# Elles répondent `not_implemented`, jamais un silence ni un 404 : la carte
# grise le bouton correspondant au lieu de le cacher (§8).


@websocket_api.websocket_command(
    {**ENVELOPPE_CARTE, vol.Required("type"): "luna/identity"}
)
@websocket_api.async_response
async def ws_identity(hass, connection, msg) -> None:
    await _ponctuelle(hass, connection, msg, "identity", {})


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/identity/voice",
        vol.Required("audio"): str,
    }
)
@websocket_api.async_response
async def ws_identity_voice(hass, connection, msg) -> None:
    if _refus_distant(hass, connection, msg) or not _audio_valide(connection, msg):
        return
    await _ponctuelle(hass, connection, msg, "identity_voice", {"audio": msg["audio"]})


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/identity/confirm",
        vol.Required("profile"): str,
        vol.Required("accept"): bool,
    }
)
@websocket_api.async_response
async def ws_identity_confirm(hass, connection, msg) -> None:
    await _ponctuelle(
        hass,
        connection,
        msg,
        "identity_confirm",
        {"profile": msg["profile"], "accept": msg["accept"]},
    )


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/identity/enroll/start",
        vol.Required("profile"): str,
    }
)
@websocket_api.async_response
async def ws_enroll_start(hass, connection, msg) -> None:
    if _refus_distant(hass, connection, msg):
        return
    await _ponctuelle(hass, connection, msg, "enroll_start", {"profile": msg["profile"]})


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/identity/enroll/sample",
        vol.Required("session"): str,
        vol.Required("index"): int,
        vol.Required("audio"): str,
    }
)
@websocket_api.async_response
async def ws_enroll_sample(hass, connection, msg) -> None:
    if _refus_distant(hass, connection, msg) or not _audio_valide(connection, msg):
        return
    await _ponctuelle(
        hass,
        connection,
        msg,
        "enroll_sample",
        {"session": msg["session"], "index": msg["index"], "audio": msg["audio"]},
    )


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/identity/enroll/finish",
        vol.Required("session"): str,
    }
)
@websocket_api.async_response
async def ws_enroll_finish(hass, connection, msg) -> None:
    if _refus_distant(hass, connection, msg):
        return
    await _ponctuelle(hass, connection, msg, "enroll_finish", {"session": msg["session"]})


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/identity/forget",
        vol.Required("profile"): str,
    }
)
@websocket_api.async_response
async def ws_identity_forget(hass, connection, msg) -> None:
    """Effacer une empreinte n'est pas de la biométrie : ça marche à distance."""
    await _ponctuelle(
        hass, connection, msg, "identity_forget", {"profile": msg["profile"]}
    )


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/alerts/feedback",
        vol.Required("suggestion_id"): str,
        vol.Required("action"): vol.In(("accepted", "rejected", "muted")),
    }
)
@websocket_api.async_response
async def ws_alerts_feedback(hass, connection, msg) -> None:
    await _ponctuelle(
        hass,
        connection,
        msg,
        "alerts_feedback",
        {"suggestion_id": msg["suggestion_id"], "action": msg["action"]},
    )


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/alerts/act",
        vol.Required("suggestion_id"): str,
    }
)
@websocket_api.async_response
async def ws_alerts_act(hass, connection, msg) -> None:
    """Le bouton « Agir » d'une alerte (D8).

    Le contexte part d'ici, construit à partir de `connection.user` : c'est lui
    que l'arbitre lira pour décider si le profil a le droit d'agir seul. La
    carte ne peut ni le fournir ni l'influencer.
    """
    await _ponctuelle(
        hass, connection, msg, "alerts_act", {"suggestion_id": msg["suggestion_id"]}
    )


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/patterns",
        vol.Optional("profile"): str,
    }
)
@websocket_api.async_response
async def ws_patterns(hass, connection, msg) -> None:
    await _ponctuelle(hass, connection, msg, "patterns", {"profile": msg.get("profile")})


@websocket_api.websocket_command({**ENVELOPPE_CARTE, vol.Required("type"): "luna/facts"})
@websocket_api.async_response
async def ws_facts(hass, connection, msg) -> None:
    """La file de relecture : ce que le modèle propose et que personne n'a
    tranché (D2)."""
    await _ponctuelle(hass, connection, msg, "facts", {})


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/facts/decide",
        vol.Required("fact_id"): str,
        vol.Required("decision"): vol.In(("accept", "reject")),
    }
)
@websocket_api.async_response
async def ws_facts_decide(hass, connection, msg) -> None:
    await _ponctuelle(
        hass,
        connection,
        msg,
        "facts_decide",
        {"fact_id": msg["fact_id"], "decision": msg["decision"]},
    )


@websocket_api.websocket_command(
    {**ENVELOPPE_CARTE, vol.Required("type"): "luna/suggestions"}
)
@websocket_api.async_response
async def ws_suggestions(hass, connection, msg) -> None:
    await _ponctuelle(hass, connection, msg, "suggestions", {})


@websocket_api.websocket_command({**ENVELOPPE_CARTE, vol.Required("type"): "luna/health"})
@websocket_api.async_response
async def ws_health(hass, connection, msg) -> None:
    """L'état de l'installation, vu par la gardienne (P5).

    Lecture seule de bout en bout : l'add-on ne propose aucune opération qui
    écrirait dans la configuration de Home Assistant, et un test statique
    refuse toute ligne qui en nommerait une.
    """
    await _ponctuelle(hass, connection, msg, "health", {})


@websocket_api.websocket_command(
    {
        **ENVELOPPE_CARTE,
        vol.Required("type"): "luna/identity/face",
        vol.Required("image"): str,
    }
)
@websocket_api.async_response
async def ws_identity_face(hass, connection, msg) -> None:
    await _ponctuelle(hass, connection, msg, "identity_face", {"image": msg["image"]})
