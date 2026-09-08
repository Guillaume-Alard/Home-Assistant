"""L0 — l'échelle d'autonomie (§9.1 du cahier des charges).

Le point important n'est pas la table, c'est **qui la consulte**. Le niveau
d'une action est décidé ici, à partir du couple `domaine.service` déjà résolu.
Le modèle de langage n'est jamais consulté et ne voit même pas la notion : il
demande un outil, l'arbitre traduit, cette table tranche (§9.2).

Un service absent de la table vaut 3 — donc proposition. Un oubli échoue du
côté sûr.
"""

from __future__ import annotations

from enum import IntEnum


class Niveau(IntEnum):
    """Les six niveaux de §9.1."""

    REPONDRE = 0
    LIRE = 1
    CONFORT = 2
    PERSISTANT = 3
    CONFIGURATION = 4
    INTERDIT_V1 = 5


#: « Le niveau par défaut d'une action non classée est 3. » (§9.1)
NIVEAU_PAR_DEFAUT = Niveau.PERSISTANT

#: Clé `domaine.service`, ou `domaine.*` pour tout un domaine.
REGISTRE: dict[str, Niveau] = {
    # ── 2 — confort réversible ───────────────────────────────────────────
    "light.turn_on": Niveau.CONFORT,
    "light.turn_off": Niveau.CONFORT,
    "light.toggle": Niveau.CONFORT,
    "switch.turn_on": Niveau.CONFORT,
    "switch.turn_off": Niveau.CONFORT,
    "switch.toggle": Niveau.CONFORT,
    "scene.turn_on": Niveau.CONFORT,
    "media_player.media_play": Niveau.CONFORT,
    "media_player.media_pause": Niveau.CONFORT,
    "media_player.media_stop": Niveau.CONFORT,
    "media_player.volume_set": Niveau.CONFORT,
    "script.turn_on": Niveau.CONFORT,
    # ── 3 — état persistant ──────────────────────────────────────────────
    "climate.set_temperature": Niveau.PERSISTANT,
    "climate.set_hvac_mode": Niveau.PERSISTANT,
    "climate.set_preset_mode": Niveau.PERSISTANT,
    "input_number.set_value": Niveau.PERSISTANT,
    "input_datetime.set_datetime": Niveau.PERSISTANT,
    "input_boolean.turn_on": Niveau.PERSISTANT,
    "input_boolean.turn_off": Niveau.PERSISTANT,
    # ── 4 — configuration de HA ou de Loggia ─────────────────────────────
    "automation.turn_on": Niveau.CONFIGURATION,
    "automation.turn_off": Niveau.CONFIGURATION,
    "automation.reload": Niveau.CONFIGURATION,
    "lovelace.save_config": Niveau.CONFIGURATION,
    "homeassistant.restart": Niveau.CONFIGURATION,
    # ── 5 — HORS PÉRIMÈTRE v1 (§9, F3) ───────────────────────────────────
    # Ces services ne sont pas non plus déclarés à Claude : deuxième barrière,
    # pas la première.
    "cover.*": Niveau.INTERDIT_V1,
    "lock.*": Niveau.INTERDIT_V1,
    "alarm_control_panel.*": Niveau.INTERDIT_V1,
    "notify.*": Niveau.INTERDIT_V1,
}

#: Ce que Luna refuse d'expliquer autrement qu'en nommant la limite v1.
MOTIF_V1 = {
    "cover": "les ouvrants",
    "lock": "les serrures",
    "alarm_control_panel": "l'alarme",
    "notify": "l'envoi de messages vers l'extérieur",
}


def niveau_de(domaine: str, service: str) -> Niveau:
    """Niveau d'un service concret. Jamais `None` : l'inconnu vaut 3."""
    exact = REGISTRE.get(f"{domaine}.{service}")
    if exact is not None:
        return exact
    joker = REGISTRE.get(f"{domaine}.*")
    if joker is not None:
        return joker
    return NIVEAU_PAR_DEFAUT


def doit_etre_journalise(niveau: Niveau) -> bool:
    """§9.1 : « Toute action de niveau ≥ 3 est journalisée. »"""
    return niveau >= Niveau.PERSISTANT


def libelle_v1(domaine: str) -> str:
    """Comment Luna nomme ce qu'elle ne peut pas faire en v1."""
    return MOTIF_V1.get(domaine, f"le domaine « {domaine} »")
