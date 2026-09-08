"""L2 — les outils exposés à Claude.

Six outils, volontairement peu nombreux : chacun coûte du contexte à chaque tour
et ajoute une surface d'erreur.

**Ce qui n'est pas là compte autant que ce qui y est.** `cover.*`, `lock.*`,
`alarm_control_panel.*` et `notify.*` ne sont pas déclarés : Claude ne peut pas
les demander (décision A2). Le niveau 5 du registre d'autonomie est la deuxième
barrière, pas la première.

L'ordre de cette liste est **stable et significatif** : l'ordre de rendu est
`tools` → `system` → `messages`, donc un outil qui change de place invalide tout
le préfixe mis en cache. Ajouter un outil se fait à la fin.
"""

from __future__ import annotations

from typing import Any

ACTIONS = ("allumer", "eteindre", "basculer")

#: Le service Home Assistant derrière chaque action, par domaine.
SERVICES = {
    "allumer": "turn_on",
    "eteindre": "turn_off",
    "basculer": "toggle",
}


def _objet(proprietes: dict[str, Any], requis: list[str]) -> dict[str, Any]:
    """Schéma strict : `additionalProperties: false` et tout est requis.

    Les paramètres optionnels sont déclarés nullables plutôt qu'absents — c'est
    ce qu'exige `strict: true`, et ça évite les entrées à moitié remplies.
    """
    return {
        "type": "object",
        "properties": proprietes,
        "required": requis,
        "additionalProperties": False,
    }


OUTILS: list[dict[str, Any]] = [
    {
        "name": "lister_pieces",
        "description": (
            "Liste les pièces de la maison et le nombre d'entités par domaine "
            "dans chacune. À utiliser quand tu ne sais pas quelles pièces "
            "existent, ou pour vérifier un nom de pièce avant d'agir."
        ),
        "input_schema": _objet({}, []),
        "strict": True,
    },
    {
        "name": "etat_maison",
        "description": (
            "Lit l'état réel des entités. Filtre par pièce et par domaine "
            "(light, switch, sensor, binary_sensor, cover, lock, "
            "alarm_control_panel, climate, media_player). Sans filtre, renvoie "
            "un résumé de toute la maison. Utilise-le avant de répondre sur "
            "l'état de quoi que ce soit : n'invente jamais."
        ),
        "input_schema": _objet(
            {
                "piece": {
                    "type": ["string", "null"],
                    "description": "Nom de la pièce, ou null pour toute la maison.",
                },
                "domaine": {
                    "type": ["string", "null"],
                    "description": "Domaine Home Assistant, ou null pour tous.",
                },
            },
            ["piece", "domaine"],
        ),
        "strict": True,
    },
    {
        "name": "commander_lumiere",
        "description": (
            "Allume, éteint ou bascule une ou plusieurs lumières. La cible peut "
            "être une pièce (« le salon ») ou un identifiant d'entité précis."
        ),
        "input_schema": _objet(
            {
                "cible": {"type": "string", "description": "Pièce ou entity_id."},
                "action": {"type": "string", "enum": list(ACTIONS)},
                "luminosite": {
                    "type": ["integer", "null"],
                    "description": "Pourcentage de 1 à 100, ou null.",
                },
            },
            ["cible", "action", "luminosite"],
        ),
        "strict": True,
    },
    {
        "name": "commander_interrupteur",
        "description": (
            "Allume, éteint ou bascule une prise ou un interrupteur. Même "
            "logique de cible que commander_lumiere."
        ),
        "input_schema": _objet(
            {
                "cible": {"type": "string"},
                "action": {"type": "string", "enum": list(ACTIONS)},
            },
            ["cible", "action"],
        ),
        "strict": True,
    },
    {
        "name": "activer_scene",
        "description": "Active une scène Home Assistant par son nom.",
        "input_schema": _objet({"nom": {"type": "string"}}, ["nom"]),
        "strict": True,
    },
    {
        "name": "regler_thermostat",
        "description": (
            "Propose un réglage de température. Cet outil ne change jamais rien "
            "tout seul : il crée une proposition que Guillaume accepte ou "
            "refuse. Annonce-le comme une proposition, pas comme un fait."
        ),
        "input_schema": _objet(
            {
                "cible": {"type": "string", "description": "Pièce ou entity_id."},
                "temperature": {"type": "number"},
                "raison": {
                    "type": "string",
                    "description": (
                        "Une phrase disant pourquoi, montrée telle quelle à "
                        "Guillaume au moment de valider."
                    ),
                },
            },
            ["cible", "temperature", "raison"],
        ),
        "strict": True,
    },
]

NOMS_OUTILS = tuple(o["name"] for o in OUTILS)
