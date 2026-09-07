"""Proactivité contextuelle (Phase 7) : les observations que Luna sait faire.

Chaque règle regarde le CONTEXTE (état de la maison + heure + ce que Luna sait)
et renvoie zéro, une ou plusieurs `Suggestion`. Une suggestion n'est jamais une
action : c'est un constat, éventuellement assorti d'une action PROPOSABLE (qui,
sur accord de Guillaume, deviendra une proposition à approuver — jamais exécutée
d'elle-même). Les règles sont des fonctions pures : faciles à tester.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

# Capteurs d'ouverture qui comptent comme « une ouverture »
_OPENING_CLASSES = {"door", "window", "garage_door", "opening", "garage"}


@dataclass
class Suggestion:
    key: str                     # anti-répétition (stable pour une même situation)
    rule: str                    # identifiant de règle (pour « ne plus me suggérer ça »)
    title: str                   # court, pour le cockpit
    detail: str                  # la phrase que Luna dit / affiche
    severity: str = "info"       # info | warning
    category: str = ""           # securite | confort | energie
    action: dict | None = None   # action PROPOSABLE (action_id, params, …) ou None


@dataclass
class ProactiveConfig:
    night_hour: int = 22         # volets/ouvertures « la nuit » à partir de cette heure
    late_hour: int = 23          # lumières « tard » à partir de cette heure
    quiet_start: int = 22        # pas de voix (sauf sécurité) entre quiet_start…
    quiet_end: int = 8           # …et quiet_end
    temp_min: float = 17.0
    temp_max: float = 26.0
    cooldown_minutes: int = 180  # silence par situation identique
    enabled_rules: tuple[str, ...] | None = None  # None = toutes

    def rule_on(self, rule_id: str) -> bool:
        return self.enabled_rules is None or rule_id in self.enabled_rules

    def is_night(self, hour: int) -> bool:
        return hour >= self.night_hour or hour < self.quiet_end

    def in_quiet_hours(self, hour: int) -> bool:
        if self.quiet_start == self.quiet_end:
            return False
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= hour < self.quiet_end
        return hour >= self.quiet_start or hour < self.quiet_end  # fenêtre qui passe minuit


@dataclass
class Context:
    now: datetime
    snapshot: dict[str, dict]
    area_labels: dict[str, str] = field(default_factory=dict)  # entity_id -> nom de pièce
    memory: list[str] = field(default_factory=list)

    @property
    def hour(self) -> int:
        return self.now.hour

    def _domain(self, entity_id: str) -> str:
        return entity_id.split(".", 1)[0]

    def state_of(self, entity_id: str) -> str | None:
        return (self.snapshot.get(entity_id) or {}).get("state")

    def attrs(self, entity_id: str) -> dict:
        return (self.snapshot.get(entity_id) or {}).get("attributes") or {}

    def friendly(self, entity_id: str) -> str:
        return self.attrs(entity_id).get("friendly_name") or entity_id

    def device_class(self, entity_id: str) -> str | None:
        return self.attrs(entity_id).get("device_class")

    def area(self, entity_id: str) -> str:
        return self.area_labels.get(entity_id, "")

    def _in_area(self, entity_id: str) -> str:
        a = self.area(entity_id)
        return f" ({a})" if a else ""

    def by_domain(self, domain: str) -> Iterator[tuple[str, dict]]:
        for entity_id, state in self.snapshot.items():
            if self._domain(entity_id) == domain:
                yield entity_id, state

    def openings_open(self) -> list[str]:
        out = []
        for entity_id, state in self.by_domain("binary_sensor"):
            if (state.get("state") == "on"
                    and (state.get("attributes") or {}).get("device_class") in _OPENING_CLASSES):
                out.append(entity_id)
        return out

    def covers_open(self) -> list[str]:
        return [e for e, s in self.by_domain("cover") if s.get("state") == "open"]

    def lights_on(self) -> list[str]:
        return [e for e, s in self.by_domain("light") if s.get("state") == "on"]

    def media_active(self) -> list[str]:
        return [e for e, s in self.by_domain("media_player") if s.get("state") == "playing"]

    def persons(self) -> list[str]:
        return [e for e, _ in self.by_domain("person")]

    def anyone_home(self) -> bool | None:
        persons = self.persons()
        if not persons:
            return None  # pas d'info de présence
        return any(self.state_of(p) == "home" for p in persons)

    def temperatures(self) -> list[tuple[str, float]]:
        out = []
        for entity_id, state in self.by_domain("sensor"):
            if (state.get("attributes") or {}).get("device_class") != "temperature":
                continue
            try:
                out.append((entity_id, float(state.get("state"))))
            except (TypeError, ValueError):
                continue
        return out


# ── Les règles (fonctions pures : Context + config → suggestions) ────────────

def _areas_phrase(ctx: Context, entity_ids: list[str], limit: int = 3) -> str:
    seen = [a for a in dict.fromkeys(ctx.area(e) for e in entity_ids) if a]
    if not seen:
        return ""
    shown = ", ".join(seen[:limit])
    return shown + ("…" if len(seen) > limit else "")


def rule_ouverture_nuit(ctx: Context, cfg: ProactiveConfig) -> list[Suggestion]:
    """Une porte/fenêtre restée ouverte le soir — signalé (pas d'actionneur)."""
    if not cfg.is_night(ctx.hour):
        return []
    out = []
    for e in ctx.openings_open():
        name = ctx.friendly(e)
        out.append(Suggestion(
            key=f"ouverture_nuit:{e}", rule="ouverture_nuit",
            title=f"{name} ouverte", severity="warning", category="securite",
            detail=f"{name}{ctx._in_area(e)} est restée ouverte, et il est {ctx.hour}h.",
        ))
    return out


def rule_volet_nuit(ctx: Context, cfg: ProactiveConfig) -> list[Suggestion]:
    """Volet / porte de garage encore ouvert le soir — propose de fermer."""
    if not cfg.is_night(ctx.hour):
        return []
    out = []
    for e in ctx.covers_open():
        name = ctx.friendly(e)
        out.append(Suggestion(
            key=f"volet_nuit:{e}", rule="volet_nuit",
            title=f"Fermer {name}", severity="warning", category="securite",
            detail=f"{name}{ctx._in_area(e)} est encore ouvert, et il est {ctx.hour}h. Je prépare une proposition pour le fermer ?",
            action={
                "action_id": "ha.cover", "params": {"op": "close", "entity_ids": [e]},
                "title": f"Fermer {name}", "risk": "medium",
                "rollback": "Tu peux le rouvrir à tout moment (voix ou interface).",
            },
        ))
    return out


def rule_lumiere_tard(ctx: Context, cfg: ProactiveConfig) -> list[Suggestion]:
    """Des lumières encore allumées tard — propose de tout éteindre (agrégé)."""
    if not (ctx.hour >= cfg.late_hour or ctx.hour < cfg.quiet_end):
        return []
    lights = ctx.lights_on()
    if not lights:
        return []
    where = _areas_phrase(ctx, lights)
    n = len(lights)
    lieux = f" ({where})" if where else ""
    return [Suggestion(
        key="lumiere_tard", rule="lumiere_tard",
        title=f"Éteindre {n} lumière{'s' if n > 1 else ''}", severity="info", category="energie",
        detail=f"{n} lumière{'s' if n > 1 else ''} encore allumée{'s' if n > 1 else ''}{lieux}, à {ctx.hour}h. Je les éteins ?",
        action={
            "action_id": "ha.turn_off", "params": {"entity_ids": lights},
            "title": f"Éteindre {n} lumière{'s' if n > 1 else ''}", "risk": "low",
            "rollback": "Tu peux les rallumer à tout moment.",
        },
    )]


def rule_absence_appareils(ctx: Context, cfg: ProactiveConfig) -> list[Suggestion]:
    """Personne à la maison mais des lumières / la musique tournent."""
    if ctx.anyone_home() is not False:  # None (pas d'info) ou True (quelqu'un est là)
        return []
    lights = ctx.lights_on()
    media = ctx.media_active()
    if not lights and not media:
        return []
    quoi = []
    if lights:
        quoi.append(f"{len(lights)} lumière{'s' if len(lights) > 1 else ''}")
    if media:
        quoi.append("de la musique")
    action = None
    if lights:
        action = {
            "action_id": "ha.turn_off", "params": {"entity_ids": lights},
            "title": "Éteindre les lumières (personne à la maison)", "risk": "low",
            "rollback": "Tu peux les rallumer à distance ou en rentrant.",
        }
    return [Suggestion(
        key="absence_appareils", rule="absence_appareils",
        title="Personne à la maison", severity="info", category="energie",
        detail=f"Personne à la maison, mais {' et '.join(quoi)} en marche." + (" J'éteins les lumières ?" if action else ""),
        action=action,
    )]


def rule_fenetre_chauffage(ctx: Context, cfg: ProactiveConfig) -> list[Suggestion]:
    """Une fenêtre ouverte alors que le chauffage tourne dans la même pièce."""
    heating_areas: dict[str, str] = {}
    for e, s in ctx.by_domain("climate"):
        action = (s.get("attributes") or {}).get("hvac_action")
        if s.get("state") == "heat" or action == "heating":
            area = ctx.area(e)
            if area:
                heating_areas[area] = e
    if not heating_areas:
        return []
    out = []
    for e in ctx.openings_open():
        if ctx.device_class(e) != "window":
            continue
        area = ctx.area(e)
        if area in heating_areas:
            out.append(Suggestion(
                key=f"fenetre_chauffage:{area}", rule="fenetre_chauffage",
                title=f"Fenêtre ouverte + chauffage ({area})", severity="info", category="energie",
                detail=f"La fenêtre {area} est ouverte alors que le chauffage y tourne — de l'énergie qui part dehors.",
            ))
    return out


def rule_confort_temperature(ctx: Context, cfg: ProactiveConfig) -> list[Suggestion]:
    """Température inconfortable dans une pièce — simple constat."""
    out = []
    for e, value in ctx.temperatures():
        where = ctx.area(e) or ctx.friendly(e)
        if value > cfg.temp_max:
            out.append(Suggestion(
                key=f"temperature_haute:{e}", rule="temperature",
                title=f"{where} : {value:g}°C", severity="info", category="confort",
                detail=f"Il fait {value:g}°C dans {where.lower()} — un peu chaud.",
            ))
        elif value < cfg.temp_min:
            out.append(Suggestion(
                key=f"temperature_basse:{e}", rule="temperature",
                title=f"{where} : {value:g}°C", severity="info", category="confort",
                detail=f"Il fait {value:g}°C dans {where.lower()} — un peu frais.",
            ))
    return out


# Ordre STABLE (les plus « sécurité » d'abord).
RULES = (
    ("ouverture_nuit", rule_ouverture_nuit),
    ("volet_nuit", rule_volet_nuit),
    ("absence_appareils", rule_absence_appareils),
    ("fenetre_chauffage", rule_fenetre_chauffage),
    ("lumiere_tard", rule_lumiere_tard),
    ("temperature", rule_confort_temperature),
)


def evaluate_all(ctx: Context, cfg: ProactiveConfig, muted: set[str] | None = None) -> list[Suggestion]:
    """Toutes les règles actives (non tues) → suggestions, sécurité d'abord."""
    muted = muted or set()
    out: list[Suggestion] = []
    for rule_id, fn in RULES:
        if not cfg.rule_on(rule_id) or rule_id in muted:
            continue
        try:
            out.extend(fn(ctx, cfg))
        except Exception:  # une règle boguée ne casse pas la veille
            import logging
            logging.getLogger("sentinel.proactive").exception("Règle %s en échec", rule_id)
    # Sécurité avant confort/énergie
    out.sort(key=lambda s: 0 if s.severity == "warning" else 1)
    return out
