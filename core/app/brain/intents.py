"""Intents locaux : la domotique courante SANS LLM — rapide et hors Internet.

Grammaire française volontairement lisible : des mots-clés sur texte normalisé
(minuscules, sans accents), la résolution des pièces via les areas de Nova, et
toutes les écritures passées au moteur d'actions (ordre direct whitelisté).

Renvoie la réponse à prononcer, ou None si la phrase n'est pas une commande
locale (elle part alors vers le LLM).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from ..actions.engine import RISK_FR, ActionEngine
from ..ha.client import HAClient
from ..ha.protocols import ProtocolBook
from ..norm import normalize
from ..store import Store

log = logging.getLogger("sentinel.intents")

# Vocabulaire (sur texte normalisé, donc sans accents)
_LIGHT_WORDS = {"lumiere", "lumieres", "lampe", "lampes", "eclairage", "plafonnier", "spot", "spots", "led", "leds"}
_COVER_WORDS = {"volet", "volets", "store", "stores"}
_ALL_WORDS = ("tout", "toute la maison", "partout", "toutes les lumieres")

_ON_VERBS = {"allume", "allumer", "allumes", "rallume"}
_OFF_VERBS = {"eteins", "eteindre", "eteint", "coupe"}
_OPEN_VERBS = {"ouvre", "ouvrir", "leve", "remonte", "releve"}
_CLOSE_VERBS = {"ferme", "fermer", "baisse", "descends", "descend"}
_STOP_VERBS = {"stoppe", "stop", "arrete"}

_PROPOSAL_RE = re.compile(
    r"\b(?P<verb>approuve|valide|accepte|refuse|rejette|reporte)\b.*?\bproposition\b\D*(?P<num>\d+)"
)
_LIST_PROPOSALS_RE = re.compile(r"\b(liste|montre|affiche|donne)\b.*\bpropositions?\b|\bpropositions? en attente\b")
_CONFIRM_RE = re.compile(r"^(sentinel )?(je )?confirme$")
_CANCEL_RE = re.compile(r"^(sentinel )?annule( tout)?$|^laisse tomber$")
_TIME_RE = re.compile(r"\bquelle heure\b|\bl'heure\b$")
_DATE_RE = re.compile(r"\bquel jour\b|\bquelle date\b|\bla date\b$")
_TEMP_RE = re.compile(r"\btemperature\b|\bcombien fait[- ]il\b|\bil fait combien\b|\bquel temps fait[- ]il a l'interieur\b")


class LocalIntents:
    def __init__(
        self,
        ha: HAClient | None,
        engine: ActionEngine | None,
        protocols: ProtocolBook,
        store: Store,
        config_path: Path | None = None,
        tz: str = "Europe/Paris",
    ):
        self._ha = ha
        self._engine = engine
        self._protocols = protocols
        self._store = store
        self._tz = tz
        self._aliases: dict[str, str] = {}
        if config_path and config_path.is_file():
            try:
                raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                for alias, target in (raw.get("pieces") or {}).items():
                    self._aliases[normalize(str(alias))] = normalize(str(target))
            except yaml.YAMLError as exc:
                log.error("intents.yml invalide : %s", exc)

    # ── Point d'entrée ───────────────────────────────────────────────────

    async def handle(self, text: str, source: str) -> str | None:
        """Tente de traiter la phrase localement. None = à passer au LLM."""
        norm = normalize(text)
        if not norm:
            return None

        # 1) Confirmation / annulation d'une action sensible en attente
        if _CONFIRM_RE.match(norm):
            if self._engine is None:
                return "Il n'y a rien à confirmer."
            outcome = await self._engine.confirm_pending(source=source)
            return outcome.text
        if _CANCEL_RE.match(norm):
            if self._engine and self._engine.cancel_pending():
                return "D'accord, j'annule."
            return None  # « annule » sans contexte : on laisse le LLM répondre

        # 2) Heure et date (local, gratuit, hors ligne)
        if _TIME_RE.search(norm):
            return self._time_reply()
        if _DATE_RE.search(norm):
            return self._date_reply()

        # 3) Gestion des propositions
        match = _PROPOSAL_RE.search(norm)
        if match:
            return await self._decide_proposal(match, source)
        if _LIST_PROPOSALS_RE.search(norm):
            return await self._list_proposals()

        # Tout le reste exige Nova
        if self._ha is None or self._engine is None:
            return None

        # 4) Protocoles (phrases de déclenchement exactes)
        proto = self._protocols.find_in_text(norm)
        if proto:
            outcome = await self._engine.run_direct(
                "protocol.run", {"name": proto.key}, utterance=text, source=source
            )
            return outcome.text

        if not self._ha.connected:
            if self._domotic_looking(norm):
                return "Nova est injoignable pour l'instant — je ne peux pas piloter la maison."
            return None

        # 5) Température (lecture)
        if _TEMP_RE.search(norm):
            return self._temperature_reply(norm)

        # 6) Volets
        reply = await self._covers(norm, text, source)
        if reply:
            return reply

        # 7) Serrures (verrouille / déverrouille)
        reply = await self._locks(norm, text, source)
        if reply:
            return reply

        # 8) Lumières
        reply = await self._lights(norm, text, source)
        if reply:
            return reply

        return None

    # ── Intents domotiques ───────────────────────────────────────────────

    def _domotic_looking(self, norm: str) -> bool:
        words = set(norm.split())
        verbs = _ON_VERBS | _OFF_VERBS | _OPEN_VERBS | _CLOSE_VERBS | {"verrouille", "deverrouille"}
        return bool(words & verbs) and bool(
            words & (_LIGHT_WORDS | _COVER_WORDS | {"maison", "porte", "alarme"})
        ) or _TEMP_RE.search(norm) is not None

    async def _lights(self, norm: str, text: str, source: str) -> str | None:
        words = set(norm.split())
        on = bool(words & _ON_VERBS)
        off = bool(words & _OFF_VERBS)
        if not (on or off):
            return None
        mentions_light = bool(words & _LIGHT_WORDS)
        wants_all = any(w in norm for w in _ALL_WORDS)

        area = self._find_area(norm)
        if not mentions_light and not wants_all and area is None:
            return None  # « allume la télé » etc. : pas pour nous → LLM

        if wants_all:
            entity_ids = self._ha.entities_by_domain("light")
            where = "de toute la maison"
        elif area:
            area_id, area_name = area
            entity_ids = self._ha.entities_in_area(area_id, "light")
            where = f"« {area_name} »"
        elif mentions_light:
            return "Précise la pièce — par exemple : « allume la lumière du salon »."
        else:
            return None

        if not entity_ids:
            return f"Je ne trouve pas de lumière {where} dans Nova."
        outcome = await self._engine.run_direct(
            "ha.turn_on" if on else "ha.turn_off",
            {"entity_ids": entity_ids},
            utterance=text, source=source,
        )
        return outcome.text

    async def _covers(self, norm: str, text: str, source: str) -> str | None:
        words = set(norm.split())
        if not (words & _COVER_WORDS):
            return None
        if words & _OPEN_VERBS:
            op = "open"
        elif words & _CLOSE_VERBS:
            op = "close"
        elif words & _STOP_VERBS:
            op = "stop"
        else:
            return None

        area = self._find_area(norm)
        if area:
            area_id, area_name = area
            entity_ids = self._ha.entities_in_area(area_id, "cover")
            where = f"« {area_name} »"
        else:
            entity_ids = self._ha.entities_by_domain("cover")
            where = "de la maison"
        if not entity_ids:
            return f"Je ne trouve pas de volet {where} dans Nova."
        outcome = await self._engine.run_direct(
            "ha.cover", {"op": op, "entity_ids": entity_ids}, utterance=text, source=source
        )
        return outcome.text

    async def _locks(self, norm: str, text: str, source: str) -> str | None:
        words = set(norm.split())
        if "verrouille" in words:
            action = "ha.lock"
        elif "deverrouille" in words:
            action = "ha.unlock"
        else:
            return None
        area = self._find_area(norm)
        if area:
            entity_ids = self._ha.entities_in_area(area[0], "lock")
        else:
            entity_ids = self._ha.entities_by_domain("lock")
        if not entity_ids:
            return "Je ne trouve pas de serrure dans Nova."
        outcome = await self._engine.run_direct(
            action, {"entity_ids": entity_ids}, utterance=text, source=source
        )
        return outcome.text

    def _temperature_reply(self, norm: str) -> str:
        area = self._find_area(norm)
        readings: list[tuple[str, float]] = []  # (où, valeur)
        for entity_id, state in self._ha.states_snapshot().items():
            value = self._temperature_of(entity_id, state)
            if value is None:
                continue
            entity_area = self._ha.entity_area(entity_id)
            if area and entity_area != area[0]:
                continue
            readings.append((entity_area or "", value))
        if not readings:
            where = f" dans « {area[1]} »" if area else ""
            return f"Je ne trouve pas de capteur de température{where}."
        avg = sum(v for _, v in readings) / len(readings)
        temp = f"{avg:.1f}".replace(".", ",").removesuffix(",0")
        if area:
            return f"Il fait {temp} degrés dans « {area[1]} »."
        return f"Il fait {temp} degrés en moyenne dans la maison ({len(readings)} capteurs)."

    @staticmethod
    def _temperature_of(entity_id: str, state: dict) -> float | None:
        attrs = state.get("attributes") or {}
        if entity_id.startswith("climate."):
            value = attrs.get("current_temperature")
        elif entity_id.startswith("sensor.") and attrs.get("device_class") == "temperature":
            value = state.get("state")
        else:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # ── Propositions ─────────────────────────────────────────────────────

    async def _decide_proposal(self, match: re.Match, source: str) -> str:
        if self._engine is None:
            return "Le moteur d'actions n'est pas disponible."
        num = int(match.group("num"))
        verb = match.group("verb")
        decision = {"refuse": "reject", "rejette": "reject", "reporte": "defer"}.get(verb, "approve")
        via = "voice" if source == "voice" else "text"
        _, message = await self._engine.decide(num, decision, via=via)
        return message

    async def _list_proposals(self) -> str:
        pending = await self._store.list_proposals("pending")
        deferred = await self._store.list_proposals("deferred")
        items = sorted(pending + deferred, key=lambda p: p["num"])
        if not items:
            return "Aucune proposition en attente."
        parts = [
            f"n°{p['num']} : {p['title']} (risque {RISK_FR.get(p['risk'], p['risk'])})"
            for p in items[:5]
        ]
        extra = f" Et {len(items) - 5} de plus." if len(items) > 5 else ""
        plural = "s" if len(items) > 1 else ""
        return f"{len(items)} proposition{plural} en attente. " + ". ".join(parts) + "." + extra

    # ── Divers ───────────────────────────────────────────────────────────

    def _find_area(self, norm: str) -> tuple[str, str] | None:
        if self._ha is None:
            return None
        found = self._ha.find_area_in_text(norm)
        if found:
            return found
        for alias, target in self._aliases.items():
            if f" {alias} " in f" {norm} ":
                for area_id, name in self._ha.areas().items():
                    if normalize(name) == target:
                        return (area_id, name)
        return None

    def _now(self) -> datetime:
        try:
            return datetime.now(ZoneInfo(self._tz))
        except Exception:
            return datetime.now()

    def _time_reply(self) -> str:
        now = self._now()
        return f"Il est {now.hour} heures {now.minute:02d}."

    def _date_reply(self) -> str:
        now = self._now()
        jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
                "août", "septembre", "octobre", "novembre", "décembre"]
        return f"Nous sommes le {jours[now.weekday()]} {now.day} {mois[now.month - 1]} {now.year}."
