"""Musique multi-pièces (Phase 9) : lecture et résolution des lecteurs média.

Sentinel pilote la musique via les entités `media_player` de Nova (Spotify,
Sonos, Chromecast… peu importe l'intégration) et leurs services standard. Aucun
compte ni jeton en plus : tout passe par Nova, sur le LAN. Ce module ne fait que
LIRE (instantané des lecteurs) et RÉSOUDRE (pièce → lecteur) + charger d'éventuels
« préréglages » (config/media.yml). Les écritures sont un exécuteur du moteur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..norm import normalize

log = logging.getLogger("sentinel.media")

# États d'un lecteur considéré « disponible » (à montrer / piloter)
_LIVE = {"playing", "paused", "idle", "on", "buffering"}


@dataclass(frozen=True)
class MediaConfig:
    # Préréglages nommés : « jazz » → {source: …} ou {content_id, content_type}
    presets: dict[str, dict] = field(default_factory=dict)
    default_room: str = ""   # pièce par défaut si non précisée


def load_config(path: Path) -> MediaConfig:
    if not path.is_file():
        return MediaConfig()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        log.error("media.yml invalide : %s — préréglages ignorés", exc)
        return MediaConfig()
    if not isinstance(raw, dict):
        return MediaConfig()
    presets = {}
    for name, spec in (raw.get("presets") or {}).items():
        if isinstance(spec, dict):
            presets[normalize(str(name))] = spec
    return MediaConfig(presets=presets, default_room=str(raw.get("piece_defaut") or "").strip())


def _volume_pct(attrs: dict) -> int | None:
    v = attrs.get("volume_level")
    try:
        return round(float(v) * 100)
    except (TypeError, ValueError):
        return None


def player_view(ha, entity_id: str) -> dict | None:
    """Vue compacte d'un lecteur (pour l'UI / etat_musique)."""
    state = ha.get_state(entity_id)
    if state is None:
        return None
    attrs = state.get("attributes") or {}
    area_id = ha.entity_area(entity_id)
    return {
        "entity_id": entity_id,
        "nom": attrs.get("friendly_name") or entity_id,
        "piece": ha.area_name(area_id) if area_id else None,
        "etat": state.get("state"),
        "joue": state.get("state") == "playing",
        "volume": _volume_pct(attrs),
        "coupe": bool(attrs.get("is_volume_muted")),
        "titre": attrs.get("media_title"),
        "artiste": attrs.get("media_artist"),
        "source": attrs.get("source"),
        "sources": attrs.get("source_list") or [],
    }


def snapshot(ha, *, live_only: bool = True) -> list[dict]:
    """Tous les lecteurs (ou seulement ceux allumés), pièce d'abord."""
    out = []
    for entity_id in ha.entities_by_domain("media_player"):
        view = player_view(ha, entity_id)
        if view is None:
            continue
        if live_only and view["etat"] not in _LIVE:
            continue
        out.append(view)
    out.sort(key=lambda v: (v["piece"] or "~", v["nom"]))
    return out


def resolve_players(ha, *, zone: str = "", entity_ids=None, default_room: str = "") -> list[str]:
    """Cible → liste d'entity_id media_player. Priorité : entity_ids > zone >
    lecteur(s) en cours de lecture > pièce par défaut."""
    if entity_ids:
        return [e for e in entity_ids if isinstance(e, str) and e.startswith("media_player.")]
    if zone:
        found = ha.find_area_in_text(zone)
        if found:
            return ha.entities_in_area(found[0], "media_player")
        return []
    # Sans pièce : ce qui joue déjà (ex. « monte le son »)
    playing = [e for e in ha.entities_by_domain("media_player")
               if (ha.get_state(e) or {}).get("state") == "playing"]
    if playing:
        return playing
    if default_room:
        found = ha.find_area_in_text(default_room)
        if found:
            return ha.entities_in_area(found[0], "media_player")
    return []
