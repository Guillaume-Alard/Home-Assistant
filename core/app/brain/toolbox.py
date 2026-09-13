"""Les outils exposés au LLM.

Lecture : libre (états, pièces, propositions). Écriture : chaque outil d'action
ne fait qu'invoquer le moteur (`ActionEngine`) — jamais Nova directement. Le
LLM ne peut donc rien exécuter hors liste blanche : au mieux, il crée une
proposition que Guillaume approuvera. Ajouter un outil : docs/OUTILS-LLM.md.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from ..actions.engine import RISK_FR, STATUS_FR, ActionEngine
from ..identity import OWNER, Speaker
from ..reminders import compute_due_iso
from ..routines import RoutineError, RoutineService
from ..selfmod import SelfSource, evaluate as evaluate_diff, summarize as summarize_diff
from .mcp import McpError, McpManager
from .vision import VisionError, VisionService
from ..ha.client import HAClient, HAError
from ..ha import media as media_lib
from ..ha.media import MediaConfig
from ..ha.protocols import ProtocolBook
from ..monitors.health import HealthService
from ..store import Store

log = logging.getLogger("sentinel.toolbox")

_RISK_FROM_FR = {"faible": "low", "moyen": "medium", "sensible": "sensitive"}

# Vocabulaire d'action des routines (Phase 8) → (action_id, paramètres implicites).
# Volontairement limité aux actions COURANTES — même liste blanche que la sécurité.
_ROUTINE_OP = {
    "allumer": ("ha.turn_on", {}),
    "eteindre": ("ha.turn_off", {}),
    "ouvrir_volets": ("ha.cover", {"op": "open"}),
    "fermer_volets": ("ha.cover", {"op": "close"}),
    "stopper_volets": ("ha.cover", {"op": "stop"}),
    "scene": ("ha.scene", {}),
    "chauffage": ("ha.climate_set_temperature", {}),
}

# Domaines montrés dans les résumés d'état (le reste = bruit pour la conversation)
_SUMMARY_DOMAINS = (
    "light", "switch", "cover", "climate", "lock", "alarm_control_panel",
    "media_player", "binary_sensor", "sensor", "person", "scene",
)

# Libellés d'activité affichés dans l'UI pendant l'usage d'un outil
ACTIVITY_LABELS = {
    "etat_maison": "consulte Nova…",
    "liste_pieces": "consulte Nova…",
    "details_entite": "consulte Nova…",
    "action_domotique": "agit sur la maison…",
    "lancer_protocole": "lance un protocole…",
    "creer_proposition": "rédige une proposition…",
    "lister_propositions": "relit ses propositions…",
    "chercher_entites": "consulte Nova…",
    "sante_systemes": "ausculte Nova…",
    "audit_systemes": "audite Nova…",
    "memoriser": "note quelque chose…",
    "lister_souvenirs": "relit ce qu'elle sait…",
    "oublier": "met à jour sa mémoire…",
    "lire_mon_code": "relit son propre code…",
    "proposer_evolution": "prépare une évolution d'elle-même…",
    "lister_evolutions": "relit ses évolutions proposées…",
    "proposer_routine": "imagine une routine…",
    "lancer_routine": "déclenche une routine…",
    "lister_routines": "relit tes routines…",
    "musique": "règle la musique…",
    "etat_musique": "écoute ce qui joue…",
    "minuteur": "lance un minuteur…",
    "rappel": "note un rappel…",
    "lister_rappels": "relit tes rappels…",
    "annuler_rappel": "annule un rappel…",
    "regarder": "regarde la caméra…",
    "mcp_outils": "liste les extensions MCP…",
    "mcp_appeler": "appelle une extension MCP…",
}


def _compact(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class Toolbox:
    def __init__(
        self,
        ha: HAClient | None,
        engine: ActionEngine | None,
        protocols: ProtocolBook,
        store: Store,
        health: HealthService | None = None,
        source: SelfSource | None = None,
        self_improve: bool = True,
        routines: RoutineService | None = None,
        media: MediaConfig | None = None,
        reminders: bool = False,
        vision: VisionService | None = None,
        mcp: McpManager | None = None,
        tz: str = "Europe/Paris",
        on_memory_change: Callable[[str], Awaitable[None]] | None = None,
        on_suggestions_change: Callable[[], Awaitable[None]] | None = None,
        on_reminders_change: Callable[[], Awaitable[None]] | None = None,
    ):
        self._ha = ha
        self._engine = engine
        self._protocols = protocols
        self._store = store
        self._health = health
        # Lecteur (SEULE lecture) du propre code de Luna, pour rédiger des diffs
        # justes (Phase 6). `self_improve` retire les outils d'auto-amélioration.
        self._source = source
        self._self_improve = self_improve
        # Scénarios & routines (Phase 8). Absent = fonction désactivée.
        self._routines = routines
        # Musique multi-pièces (Phase 9). Absent = fonction désactivée.
        self._media = media
        # Minuteurs & rappels (Phase 10). self._reminders = disponibilité de l'outil.
        self._reminders = reminders
        # Vision en lecture (brique agentique) — service local optionnel. Absent
        # ou non configuré = pas d'outil « regarder ».
        self._vision = vision
        # MCP (couche d'extension) — serveurs externes optionnels. Absent = pas
        # d'outils MCP. Réservé au propriétaire.
        self._mcp = mcp
        self._tz = tz
        self._on_reminders_change = on_reminders_change
        # Notifie l'UI (rafraîchit Paramètres › Mémoire) quand Luna retient/oublie
        # quelque chose. Optionnel : absent en test unitaire.
        self._on_memory_change = on_memory_change
        # Rafraîchit Paramètres › Évolutions quand Luna propose une auto-amélioration.
        self._on_suggestions_change = on_suggestions_change

    _NOVA_ABSENTE = "Nova (Home Assistant) n'est pas configurée ou pas joignable."
    _MOTEUR_ABSENT = "Le moteur d'actions n'est pas disponible (Nova non configurée)."

    # ── Déclarations (ordre STABLE : le cache de prompt en dépend) ───────

    def specs(self) -> list[dict]:
        protocol_names = [p.display for p in self._protocols.all()] or ["(aucun protocole configuré)"]
        specs: list[dict] = [
            {
                "name": "etat_maison",
                "description": (
                    "Lit l'état de la maison via Nova (Home Assistant). Sans argument : aperçu "
                    "par pièce. Avec `zone` : le détail des entités de cette pièce. "
                    "Toujours vérifier ici avant d'agir — ne jamais inventer d'entity_id."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "zone": {"type": "string", "description": "Nom de la pièce (ex. « salon »)"},
                        "domaine": {"type": "string", "description": "Filtre optionnel : light, cover, sensor…"},
                    },
                },
            },
            {
                "name": "details_entite",
                "description": "Détail complet d'une entité Nova (état + attributs).",
                "input_schema": {
                    "type": "object",
                    "properties": {"entity_id": {"type": "string"}},
                    "required": ["entity_id"],
                },
            },
            {
                "name": "action_domotique",
                "description": (
                    "Exécute une commande domotique courante demandée EXPLICITEMENT par Guillaume "
                    "(jamais de ta propre initiative). Cible : `zone` (pièce) ou `entity_ids` précis. "
                    "Le déverrouillage et le désarmement ne sont pas disponibles ici (sensibles)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": [
                                "allumer", "eteindre", "ouvrir_volets", "fermer_volets",
                                "stopper_volets", "verrouiller", "scene",
                            ],
                        },
                        "zone": {"type": "string", "description": "Pièce ciblée (ex. « salon »)"},
                        "entity_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["operation"],
                },
            },
            {
                "name": "lancer_protocole",
                "description": (
                    "Déclenche un protocole (séquence d'actions nommée) demandé par Guillaume. "
                    f"Protocoles disponibles : {', '.join(protocol_names)}."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"nom": {"type": "string"}},
                    "required": ["nom"],
                },
            },
            {
                "name": "creer_proposition",
                "description": (
                    "Crée une proposition d'action à faire approuver par Guillaume — l'UNIQUE moyen "
                    "d'aller au-delà de la domotique courante (service Home Assistant quelconque). "
                    "Rien ne s'exécute avant approbation explicite."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "titre": {"type": "string"},
                        "description": {"type": "string"},
                        "justification": {"type": "string"},
                        "risque": {"type": "string", "enum": ["faible", "moyen", "sensible"]},
                        "rollback": {"type": "string", "description": "Comment revenir en arrière"},
                        "action": {
                            "type": "object",
                            "description": "Service HA à appeler après approbation",
                            "properties": {
                                "domain": {"type": "string"},
                                "service": {"type": "string"},
                                "data": {"type": "object"},
                                "target": {"type": "object"},
                            },
                            "required": ["domain", "service"],
                        },
                    },
                    "required": ["titre", "justification", "risque", "action"],
                },
            },
            {
                "name": "lister_propositions",
                "description": "Liste les propositions (statut optionnel : pending, done, rejected…).",
                "input_schema": {
                    "type": "object",
                    "properties": {"statut": {"type": "string"}},
                },
            },
            {
                "name": "liste_pieces",
                "description": "Liste les pièces (areas) de Nova et ce qu'elles contiennent.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "chercher_entites",
                "description": (
                    "Recherche des entités Nova par mots-clés (nom affiché, identifiant ou "
                    "device_class, insensible aux accents), Y COMPRIS les entités sans pièce "
                    "assignée. À utiliser quand etat_maison ne montre pas un capteur ou un "
                    "appareil qui devrait exister — mots-clés courts (ex. « porte »)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "recherche": {"type": "string"},
                        "domaine": {"type": "string", "description": "Filtre optionnel : binary_sensor, sensor, light…"},
                    },
                    "required": ["recherche"],
                },
            },
            {
                "name": "sante_systemes",
                "description": (
                    "Instantané de santé de Nova (Home Assistant) : connexion, entités "
                    "indisponibles, mises à jour en attente. À consulter AVANT tout diagnostic "
                    "(« pourquoi X ne répond plus ? »)."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "audit_systemes",
                "description": (
                    "Audit de Nova : constats classés par gravité "
                    "(critique/attention/info) avec actions suggérées. Présente les constats à "
                    "Guillaume et crée des propositions pour les actions qu'il souhaite."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "memoriser",
                "description": (
                    "Mémorise durablement une information utile sur Guillaume pour "
                    "personnaliser tes futures réponses : une préférence, une habitude, "
                    "la façon dont il aime qu'on lui parle, ou un fait stable de sa vie. "
                    "Enrichissement de contexte UNIQUEMENT — n'agit jamais sur la maison. "
                    "Ne mémorise pas de banalités ni rien de sensible (mots de passe, "
                    "codes, données bancaires). Guillaume voit et peut supprimer chaque "
                    "souvenir dans Paramètres › Mémoire."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "contenu": {
                            "type": "string",
                            "description": "Le fait à retenir, court et clair, à la 3e personne "
                            "(ex. « Préfère des réponses très courtes »).",
                        },
                        "categorie": {
                            "type": "string",
                            "enum": ["preference", "habitude", "style", "fait"],
                            "description": "preference | habitude | style (de langage) | fait",
                        },
                    },
                    "required": ["contenu"],
                },
            },
            {
                "name": "lister_souvenirs",
                "description": (
                    "Liste ce que tu as retenu de Guillaume, avec l'identifiant de chaque "
                    "souvenir (utile pour en oublier un précis)."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "oublier",
                "description": (
                    "Oublie (supprime définitivement) un souvenir précis, sur demande de "
                    "Guillaume. Donne son `id` — consulte lister_souvenirs si tu ne l'as pas."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            },
        ]
        # Auto-amélioration encadrée (Phase 6) — outils réservés au propriétaire,
        # retirés si SENTINEL_SELF_IMPROVE=off. Ordre STABLE (cache de prompt).
        if self._self_improve:
            specs += [
                {
                    "name": "lire_mon_code",
                    "description": (
                        "Lit (EN SEULE LECTURE) ton propre code source, pour préparer une "
                        "proposition d'évolution juste. Sans `chemin` : la liste de tes "
                        "fichiers. Avec `chemin` (ex. « core/app/brain/toolbox.py ») : le "
                        "contenu du fichier. Tu ne lis jamais .env ni aucun secret."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "chemin": {"type": "string", "description": "Fichier de app/ ou ui/ (optionnel)."},
                        },
                    },
                },
                {
                    "name": "proposer_evolution",
                    "description": (
                        "PROPOSE une évolution de ton propre code ou de ta configuration, sous "
                        "forme d'un DIFF unifié soumis à la validation de Guillaume. Tu "
                        "n'appliques JAMAIS rien toi-même : c'est une proposition qu'il relit "
                        "puis applique. Interdit (refusé d'office) : toucher un garde-fou de "
                        "sécurité (niveaux de confiance, moteur d'actions, isolation de "
                        "l'atelier, secrets) ou introduire un secret. Une évolution « config » "
                        "ne modifie que .env.example, une clé de réglage. Relis d'abord le "
                        "code concerné avec lire_mon_code pour un diff exact."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": ["code", "config"], "description": "code | config"},
                            "titre": {"type": "string"},
                            "motivation": {"type": "string", "description": "Pourquoi cette évolution est utile."},
                            "cible": {"type": "string", "description": "Fichier(s) ou réglage visé."},
                            "diff": {"type": "string", "description": "Diff unifié (en-têtes --- / +++)."},
                        },
                        "required": ["titre", "motivation", "diff"],
                    },
                },
                {
                    "name": "lister_evolutions",
                    "description": (
                        "Liste tes propositions d'évolution et leur statut (en attente, "
                        "acceptée, rejetée). Guillaume les relit dans Paramètres › Évolutions."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {"statut": {"type": "string", "description": "pending | accepted | rejected"}},
                    },
                },
            ]
        # Scénarios & routines (Phase 8) — présents si le service est actif.
        if self._routines is not None:
            specs += [
                {
                    "name": "proposer_routine",
                    "description": (
                        "PROPOSE une routine : une séquence d'actions courantes nommée et "
                        "réutilisable (ex. « Bonne nuit » = fermer les volets + éteindre le "
                        "salon). Guillaume l'ACTIVE ensuite dans l'interface (elle ne se "
                        "déclenche pas avant). Une routine ne peut contenir QUE des actions "
                        "courantes — jamais de déverrouillage ni de désarmement. Vérifie les "
                        "entity_ids avec etat_maison d'abord."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "nom": {"type": "string", "description": "Nom court (ex. « Bonne nuit »)."},
                            "description": {"type": "string"},
                            "etapes": {
                                "type": "array",
                                "description": "Les actions, dans l'ordre.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "action": {
                                            "type": "string",
                                            "enum": ["allumer", "eteindre", "ouvrir_volets",
                                                     "fermer_volets", "stopper_volets", "scene", "chauffage"],
                                        },
                                        "entity_ids": {"type": "array", "items": {"type": "string"}},
                                        "temperature": {"type": "number", "description": "Pour « chauffage » (5–30)."},
                                    },
                                    "required": ["action", "entity_ids"],
                                },
                            },
                        },
                        "required": ["nom", "etapes"],
                    },
                },
                {
                    "name": "lancer_routine",
                    "description": (
                        "Déclenche une routine ACTIVE, sur demande explicite (ex. « lance "
                        "Bonne nuit »). N'exécute que des actions courantes."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {"nom": {"type": "string"}},
                        "required": ["nom"],
                    },
                },
                {
                    "name": "lister_routines",
                    "description": "Liste les routines (actives et proposées à activer), avec leurs étapes.",
                    "input_schema": {"type": "object", "properties": {}},
                },
            ]
        # Musique multi-pièces (Phase 9) — présente si des lecteurs existent.
        if self._media is not None:
            preset_names = list(self._media.presets.keys())
            preset_hint = f" Préréglages : {', '.join(preset_names)}." if preset_names else ""
            specs += [
                {
                    "name": "etat_musique",
                    "description": (
                        "Ce qui joue dans la maison : lecteurs allumés, pièce, titre/artiste, "
                        "volume, source. À consulter avant d'agir (« qu'est-ce qui joue ? »)."
                    ),
                    "input_schema": {"type": "object", "properties": {}},
                },
                {
                    "name": "musique",
                    "description": (
                        "Pilote la musique sur les lecteurs de Nova (Spotify, enceintes…), "
                        "demandé par une personne reconnue. Cible : `zone` (pièce) ou "
                        "`entity_ids` ; sans cible, agit sur ce qui joue déjà. « jouer » avec "
                        "`contenu` lance un préréglage ou une source." + preset_hint +
                        " « transferer » regroupe la pièce en cours avec `cible` (multi-pièces)."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": ["jouer", "pause", "stop", "suivant", "precedent",
                                         "volume", "monter_volume", "baisser_volume",
                                         "couper_son", "remettre_son", "source", "transferer"],
                            },
                            "zone": {"type": "string", "description": "Pièce ciblée (ex. « salon »)."},
                            "entity_ids": {"type": "array", "items": {"type": "string"}},
                            "niveau": {"type": "integer", "description": "Volume 0–100 (pour « volume »)."},
                            "contenu": {"type": "string", "description": "Préréglage ou contenu à jouer (pour « jouer »)."},
                            "source": {"type": "string", "description": "Nom de source (pour « source »)."},
                            "cible": {"type": "string", "description": "Pièce de destination (pour « transferer »)."},
                        },
                        "required": ["operation"],
                    },
                },
            ]
        # Minuteurs & rappels (Phase 10) — 100% local.
        if self._reminders:
            specs += [
                {
                    "name": "minuteur",
                    "description": (
                        "Lance un minuteur (compte à rebours) — ex. « minuteur 10 minutes pour "
                        "les pâtes ». À l'échéance, tu carillonnes et l'annonces. Donne la durée "
                        "en minutes et/ou secondes."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "minutes": {"type": "number"},
                            "secondes": {"type": "number"},
                            "libelle": {"type": "string", "description": "Ce que c'est (ex. « pâtes »)."},
                        },
                    },
                },
                {
                    "name": "rappel",
                    "description": (
                        "Programme un rappel daté — ex. « rappelle-moi dans 20 min de sortir le "
                        "plat », « à 18h d'appeler le garage ». Donne le `libelle` (quoi) et QUAND : "
                        "soit `dans_minutes` (relatif), soit `a` (date/heure absolue au format ISO, "
                        "que TU calcules à partir de la date du jour, ex. « 2026-09-07T18:00 »)."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "libelle": {"type": "string", "description": "Ce dont il faut se souvenir."},
                            "dans_minutes": {"type": "number", "description": "Délai en minutes (relatif)."},
                            "a": {"type": "string", "description": "Date/heure ISO absolue (ex. « 2026-09-07T18:00 »)."},
                        },
                        "required": ["libelle"],
                    },
                },
                {
                    "name": "lister_rappels",
                    "description": "Liste les minuteurs et rappels en cours (avec leur échéance et leur id).",
                    "input_schema": {"type": "object", "properties": {}},
                },
                {
                    "name": "annuler_rappel",
                    "description": "Annule un minuteur ou un rappel. Donne son `id` (via lister_rappels).",
                    "input_schema": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                    },
                },
            ]
        # Vision en lecture — présente si un service de vision local est configuré
        # ET Nova joignable (les caméras viennent de Nova). C'est un CAPTEUR :
        # décrire ce qu'on voit, jamais agir dessus.
        if self._vision is not None and self._vision.available and self._ha is not None:
            specs.append({
                "name": "regarder",
                "description": (
                    "REGARDE une caméra de Nova et décris ce qui est visible — c'est une "
                    "OBSERVATION, jamais une action. Cible : `zone` (pièce) ou `camera` "
                    "(entity_id précis) ; si une seule caméra existe, elle est prise par "
                    "défaut. `question` oriente le regard (« y a-t-il quelqu'un ? »). Pour "
                    "AGIR sur ce que tu vois, crée une proposition — regarder n'exécute rien."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "zone": {"type": "string", "description": "Pièce de la caméra (ex. « entrée »)."},
                        "camera": {"type": "string", "description": "entity_id précis (ex. « camera.entree »)."},
                        "question": {"type": "string", "description": "Ce que tu cherches à voir (optionnel)."},
                    },
                },
            })
        # MCP (couche d'extension) — outils réservés au propriétaire, présents si
        # au moins un serveur est déclaré dans config/mcp.yml.
        if self._mcp is not None and self._mcp.configured:
            specs += [
                {
                    "name": "mcp_outils",
                    "description": (
                        "Liste les services MCP branchés et leurs outils (nom, description, "
                        "schéma d'arguments, mode). Consulte-le AVANT d'appeler un outil MCP "
                        "pour connaître les arguments attendus. Réservé à Guillaume."
                    ),
                    "input_schema": {"type": "object", "properties": {}},
                },
                {
                    "name": "mcp_appeler",
                    "description": (
                        "Appelle un outil d'un service MCP. Un serveur « lecture » répond "
                        "directement ; un serveur « proposition » crée une PROPOSITION que "
                        "Guillaume approuve avant exécution (rien d'externe ne part à l'aveugle). "
                        "Donne `serveur`, `outil` et `arguments` (selon le schéma vu dans "
                        "mcp_outils). Réservé à Guillaume."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "serveur": {"type": "string"},
                            "outil": {"type": "string"},
                            "arguments": {"type": "object", "description": "Arguments de l'outil (selon son schéma)."},
                        },
                        "required": ["serveur", "outil"],
                    },
                },
            ]
        return specs

    # ── Exécution ────────────────────────────────────────────────────────

    # Niveau requis par outil (Phase 2). Absent = public : lecture d'état et
    # conversation, ouvertes à tous, invité compris. « known » = personne reconnue
    # (propriétaire ou maisonnée) : domotique courante et mémoire. « owner » =
    # Guillaume seul : administration (dev, conteneurs, audit, journaux).
    # La reconnaissance ne peut JAMAIS élever un droit : les actions sensibles
    # (déverrouillage, désarmement) restent barrées au moteur pour tout le monde.
    _TOOL_LEVEL = {
        "action_domotique": "known", "lancer_protocole": "known",
        "creer_proposition": "known", "lister_propositions": "known",
        "memoriser": "known", "lister_souvenirs": "known", "oublier": "known",
        "sante_systemes": "owner", "audit_systemes": "owner",
        # Auto-amélioration (Phase 6) : proposer des changements sur soi-même =
        # administration, réservée à Guillaume. La reconnaissance n'élève rien.
        "lire_mon_code": "owner", "proposer_evolution": "owner", "lister_evolutions": "owner",
        # Routines (Phase 8) : proposer = owner ; déclencher/lister = personne
        # reconnue (une routine ne contient jamais d'action sensible).
        "proposer_routine": "owner", "lancer_routine": "known", "lister_routines": "known",
        # Musique (Phase 9) : piloter = personne reconnue (courant, non sensible) ;
        # lire ce qui joue = public. (etat_musique absent → public.)
        "musique": "known",
        # Minuteurs & rappels (Phase 10) : personne reconnue (usage courant).
        "minuteur": "known", "rappel": "known",
        "lister_rappels": "known", "annuler_rappel": "known",
        # Vision : regarder une caméra touche à l'intimité → personne reconnue
        # seulement (jamais un invité). La reconnaissance n'élève aucun droit :
        # c'est une lecture, pas une action.
        "regarder": "known",
        # MCP : brancher/piloter un service externe = administration → Guillaume seul.
        "mcp_outils": "owner", "mcp_appeler": "owner",
    }
    _MEMORY_TOOLS = ("memoriser", "lister_souvenirs", "oublier")

    async def run(
        self, name: str, args: dict, *, utterance: str, source: str,
        speaker: Speaker | None = None,
    ) -> tuple[str, bool]:
        """Exécute un outil. Renvoie (contenu, is_error)."""
        who = speaker or OWNER  # canaux sans voix (écrit/UI/Assist) = propriétaire
        level = self._TOOL_LEVEL.get(name)
        if level == "owner" and not who.is_owner:
            return "C'est réservé à Guillaume (administration de Sentinel).", False
        if level == "known" and not who.can_act:
            return (
                "Je ne peux pas faire ça pour une personne que je ne reconnais pas. "
                "Seul Guillaume — ou quelqu'un que je reconnais — peut le demander, "
                "au besoin depuis l'interface.", False
            )
        try:
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                return f"Outil inconnu : {name}", True
            if name in self._MEMORY_TOOLS:
                return await handler(args, who)
            return await handler(args, utterance, source)
        except Exception:
            log.exception("Outil %s en échec", name)
            return "Erreur interne de l'outil — détail dans les journaux du serveur.", True

    # Lecture ─────────────────────────────────────────────────────────────

    async def _tool_etat_maison(self, args, _utt, _src):
        if self._ha is None or not self._ha.connected:
            return self._NOVA_ABSENTE, True
        zone = str(args.get("zone") or "").strip()
        domaine = str(args.get("domaine") or "").strip() or None
        if zone:
            found = self._ha.find_area_in_text(zone)
            if not found:
                names = ", ".join(sorted(self._ha.areas().values())) or "aucune pièce déclarée"
                return f"Pièce inconnue : « {zone} ». Pièces de Nova : {names}.", True
            area_id, area_name = found
            entities = self._ha.entities_in_area(area_id, domaine)
            detail = {
                e: self._state_line(e) for e in entities
                if e.split(".", 1)[0] in _SUMMARY_DOMAINS
            }
            return _compact({"piece": area_name, "entites": detail})[:6000], False
        return self._overview(domaine), False

    def _overview(self, domaine: str | None) -> str:
        by_area: dict[str, dict] = {}
        snapshot = self._ha.states_snapshot()
        for entity_id, state in snapshot.items():
            domain = entity_id.split(".", 1)[0]
            if domaine and domain != domaine:
                continue
            if domain not in _SUMMARY_DOMAINS:
                continue
            area_id = self._ha.entity_area(entity_id)
            area_name = self._ha.area_name(area_id) if area_id else "(hors pièce)"
            bucket = by_area.setdefault(area_name, {"lumieres_allumees": 0, "lumieres": 0, "volets": [], "notable": {}})
            value = state.get("state")
            if domain == "light":
                bucket["lumieres"] += 1
                if value == "on":
                    bucket["lumieres_allumees"] += 1
            elif domain == "cover":
                bucket["volets"].append(value)
            elif domain in ("climate", "lock", "alarm_control_panel", "media_player", "person"):
                bucket["notable"][entity_id] = self._state_line(entity_id)
            elif domain == "binary_sensor" and (state.get("attributes") or {}).get(
                "device_class"
            ) in ("door", "window", "opening", "garage_door"):
                name = (state.get("attributes") or {}).get("friendly_name") or entity_id
                bucket["notable"][entity_id] = f"{name} : {'ouvert' if value == 'on' else 'fermé'}"
            elif domain == "sensor" and (state.get("attributes") or {}).get("device_class") == "temperature":
                bucket["notable"][entity_id] = f"{value} °C"
        return _compact(by_area)[:6000]

    def _state_line(self, entity_id: str) -> str:
        state = self._ha.get_state(entity_id) or {}
        value = state.get("state", "inconnu")
        attrs = state.get("attributes") or {}
        extras = []
        if attrs.get("current_temperature") is not None:
            extras.append(f"{attrs['current_temperature']} °C")
        if attrs.get("temperature") is not None:
            extras.append(f"consigne {attrs['temperature']} °C")
        name = attrs.get("friendly_name") or entity_id
        suffix = f" ({', '.join(extras)})" if extras else ""
        return f"{name} : {value}{suffix}"

    async def _tool_details_entite(self, args, _utt, _src):
        if self._ha is None:
            return self._NOVA_ABSENTE, True
        entity_id = str(args.get("entity_id") or "")
        state = self._ha.get_state(entity_id)
        if state is None:
            return f"Entité inconnue : {entity_id}.", True
        attrs = {
            k: v for k, v in (state.get("attributes") or {}).items()
            if not isinstance(v, (list, dict)) or k in ("hvac_modes",)
        }
        return _compact({
            "entity_id": entity_id,
            "etat": state.get("state"),
            "attributs": attrs,
            "piece": self._ha.area_name(self._ha.entity_area(entity_id) or "") or None,
            "depuis": state.get("last_changed"),
        })[:4000], False

    async def _tool_regarder(self, args, _utt, _src):
        """Vision en LECTURE : instantané d'une caméra de Nova + description. Ne
        déclenche jamais d'action — pour agir, Luna crée une proposition."""
        if self._vision is None or not self._vision.available:
            return (
                "La vision locale n'est pas configurée — déclare le service sur Nebula "
                "(.env : VISION_BASE_URL et VISION_MODEL).", True
            )
        if self._ha is None or not self._ha.connected:
            return self._NOVA_ABSENTE, True
        camera = str(args.get("camera") or "").strip()
        zone = str(args.get("zone") or "").strip()
        question = str(args.get("question") or "").strip() or None
        if camera and not camera.startswith("camera."):
            return f"« {camera} » n'est pas une caméra (attendu : camera.xxx).", True
        if not camera:
            if zone:
                found = self._ha.find_area_in_text(zone)
                if not found:
                    names = ", ".join(sorted(self._ha.areas().values())) or "aucune pièce déclarée"
                    return f"Pièce inconnue : « {zone} ». Pièces de Nova : {names}.", True
                cams = self._ha.entities_in_area(found[0], "camera")
                if not cams:
                    return f"Aucune caméra dans « {found[1]} ».", True
            else:
                cams = self._ha.entities_by_domain("camera")
                if not cams:
                    return "Nova ne déclare aucune caméra.", True
            if len(cams) > 1:
                listing = ", ".join(f"{self._ha.friendly_name(c)} ({c})" for c in cams)
                return f"Plusieurs caméras possibles : {listing}. Précise `zone` ou `camera`.", True
            camera = cams[0]
        try:
            image, ctype = await self._ha.camera_snapshot(camera)
            description = await self._vision.describe(image, question, content_type=ctype)
        except (HAError, VisionError) as exc:
            return str(exc), True
        return _compact({
            "camera": self._ha.friendly_name(camera),
            "entity_id": camera,
            "observation": description,
        })[:4000], False

    async def _tool_mcp_outils(self, _args, _utt, _src):
        if self._mcp is None or not self._mcp.configured:
            return "Aucun service MCP n'est configuré (config/mcp.yml).", True
        return _compact(await self._mcp.catalog())[:6000], False

    async def _tool_mcp_appeler(self, args, _utt, _src):
        """Appelle un outil MCP. Serveur « lecture » → direct ; « proposition » →
        une proposition à valider (jamais d'effet externe à l'aveugle)."""
        if self._mcp is None or not self._mcp.configured:
            return "Aucun service MCP n'est configuré (config/mcp.yml).", True
        server_name = str(args.get("serveur") or "").strip()
        tool = str(args.get("outil") or "").strip()
        arguments = args.get("arguments") or {}
        if not isinstance(arguments, dict):
            return "Les arguments MCP doivent être un objet.", True
        server = self._mcp.get(server_name)
        if server is None:
            dispo = ", ".join(s.name for s in self._mcp.servers()) or "aucun"
            return f"Serveur MCP inconnu : « {server_name} ». Disponibles : {dispo}.", True
        if not tool:
            return "Précise l'outil MCP à appeler (voir mcp_outils).", True
        if server.mode == "lecture":
            try:
                result = await self._mcp.call_tool(server_name, tool, arguments)
            except McpError as exc:
                return str(exc), True
            return _compact({"serveur": server_name, "outil": tool, "resultat": result})[:6000], False
        # Mode « proposition » : rien ne part sans l'accord de Guillaume.
        if self._engine is None:
            return (
                "Ce service MCP exige une validation, mais le moteur de propositions "
                "n'est pas disponible (Nova non configurée).", True
            )
        proposal, message = await self._engine.propose(
            title=f"MCP · {server_name}.{tool}",
            description=f"Appel de l'outil MCP « {tool} » sur le service « {server_name} ».",
            justification=str(args.get("justification") or ""),
            risk=server.risk,
            action_id="mcp.call",
            params={"server": server_name, "tool": tool, "arguments": arguments},
            created_by="sentinel (LLM)",
        )
        return message, proposal is None

    async def _tool_liste_pieces(self, args, _utt, _src):
        if self._ha is None or not self._ha.connected:
            return self._NOVA_ABSENTE, True
        out = {}
        for area_id, name in self._ha.areas().items():
            entities = self._ha.entities_in_area(area_id)
            counts: dict[str, int] = {}
            for e in entities:
                counts[e.split(".", 1)[0]] = counts.get(e.split(".", 1)[0], 0) + 1
            out[name] = counts
        return _compact(out)[:4000], False

    async def _tool_lister_propositions(self, args, _utt, _src):
        status = str(args.get("statut") or "").strip() or None
        items = await self._store.list_proposals(status, limit=15)
        return _compact([
            {
                "num": p["num"], "titre": p["title"], "risque": RISK_FR.get(p["risk"], p["risk"]),
                "statut": STATUS_FR.get(p["status"], p["status"]), "resultat": p.get("result"),
            }
            for p in items
        ]), False

    async def _tool_chercher_entites(self, args, _utt, _src):
        if self._ha is None or not self._ha.connected:
            return self._NOVA_ABSENTE, True
        from ..norm import normalize

        query_words = normalize(str(args.get("recherche") or "")).split()
        if not query_words:
            return "Donne au moins un mot-clé de recherche.", True
        domaine = str(args.get("domaine") or "").strip() or None

        results = []
        for entity_id, state in sorted(self._ha.states_snapshot().items()):
            domain = entity_id.split(".", 1)[0]
            if domaine and domain != domaine:
                continue
            attrs = state.get("attributes") or {}
            haystack = normalize(
                f"{entity_id} {attrs.get('friendly_name') or ''} {attrs.get('device_class') or ''}"
            )
            if not all(word in haystack for word in query_words):
                continue
            area_id = self._ha.entity_area(entity_id)
            results.append({
                "entity_id": entity_id,
                "nom": attrs.get("friendly_name") or entity_id,
                "etat": state.get("state"),
                "device_class": attrs.get("device_class"),
                "piece": self._ha.area_name(area_id) if area_id else None,
            })
            if len(results) >= 25:
                break
        if not results:
            return f"Aucune entité ne correspond à « {' '.join(query_words)} ».", False
        return _compact(results), False

    async def _tool_sante_systemes(self, args, _utt, _src):
        if self._health is None:
            return "La santé de Nova n'est pas disponible.", True
        return _compact(await self._health.snapshot())[:6000], False

    async def _tool_audit_systemes(self, args, _utt, _src):
        if self._health is None:
            return "La santé de Nova n'est pas disponible.", True
        return _compact(await self._health.audit())[:6000], False

    # Écriture (via le moteur uniquement) ─────────────────────────────────

    async def _tool_action_domotique(self, args, utterance, source):
        if self._ha is None or self._engine is None:
            return self._NOVA_ABSENTE, True
        operation = str(args.get("operation") or "")
        entity_ids = args.get("entity_ids") or []
        zone = str(args.get("zone") or "").strip()

        domain_for = {
            "allumer": "light", "eteindre": "light",
            "ouvrir_volets": "cover", "fermer_volets": "cover", "stopper_volets": "cover",
            "verrouiller": "lock", "scene": "scene",
        }
        if operation not in domain_for:
            return f"Opération inconnue : {operation}.", True

        if not entity_ids and zone:
            found = self._ha.find_area_in_text(zone)
            if not found:
                return f"Pièce inconnue : « {zone} » — vérifie avec liste_pieces.", True
            entity_ids = self._ha.entities_in_area(found[0], domain_for[operation])
            if not entity_ids:
                return f"Aucune entité {domain_for[operation]} dans « {found[1]} ».", True
        if not entity_ids:
            return "Précise une zone ou des entity_ids.", True

        mapping = {
            "allumer": ("ha.turn_on", {"entity_ids": entity_ids}),
            "eteindre": ("ha.turn_off", {"entity_ids": entity_ids}),
            "ouvrir_volets": ("ha.cover", {"op": "open", "entity_ids": entity_ids}),
            "fermer_volets": ("ha.cover", {"op": "close", "entity_ids": entity_ids}),
            "stopper_volets": ("ha.cover", {"op": "stop", "entity_ids": entity_ids}),
            "verrouiller": ("ha.lock", {"entity_ids": entity_ids}),
            "scene": ("ha.scene", {"entity_ids": entity_ids}),
        }
        action_id, params = mapping[operation]
        outcome = await self._engine.run_direct(
            action_id, params, utterance=utterance, source=f"{source} (via LLM)"
        )
        return outcome.text, not outcome.ok

    async def _tool_lancer_protocole(self, args, utterance, source):
        if self._engine is None:
            return self._MOTEUR_ABSENT, True
        nom = str(args.get("nom") or "")
        if self._protocols.get(nom) is None:
            names = ", ".join(p.display for p in self._protocols.all()) or "aucun"
            return f"Protocole inconnu : « {nom} ». Disponibles : {names}.", True
        outcome = await self._engine.run_direct(
            "protocol.run", {"name": nom}, utterance=utterance, source=f"{source} (via LLM)"
        )
        # needs_confirmation n'est pas une erreur : le LLM doit relayer la consigne
        return outcome.text, outcome.status in ("refused", "failed")

    async def _tool_creer_proposition(self, args, _utt, _src):
        if self._engine is None:
            return self._MOTEUR_ABSENT, True
        action = args.get("action") or {}
        if not action.get("domain") or not action.get("service"):
            return "L'action proposée doit préciser domain et service.", True
        proposal, message = await self._engine.propose(
            title=str(args.get("titre") or "Proposition"),
            description=str(args.get("description") or ""),
            justification=str(args.get("justification") or ""),
            risk=_RISK_FROM_FR.get(str(args.get("risque") or "moyen"), "medium"),
            rollback=str(args.get("rollback") or ""),
            action_id="ha.call_service",
            params={
                "domain": str(action["domain"]),
                "service": str(action["service"]),
                "data": action.get("data") or {},
                "target": action.get("target") or {},
            },
            created_by="sentinel (LLM)",
        )
        return message, proposal is None

    # Mémoire (enrichissement de contexte — JAMAIS le moteur d'actions) ────

    async def _notify_memory_change(self, subject: str) -> None:
        if self._on_memory_change is not None:
            try:
                await self._on_memory_change(subject)
            except Exception:
                log.exception("Notification de changement de mémoire impossible")

    async def _tool_memoriser(self, args, who: Speaker):
        from ..norm import normalize
        from .memory import normalize_category

        subject = who.subject or "guillaume"
        contenu = str(args.get("contenu") or "").strip()[:500]
        if not contenu:
            return "Précise ce que je dois retenir.", True
        category = normalize_category(args.get("categorie"))
        # Anti-doublon : on ne réécrit pas ce qu'on sait déjà (comparaison sans accents/casse)
        target = normalize(contenu)
        for m in await self._store.list_memories(subject=subject, limit=200):
            if normalize(m.get("content") or "") == target:
                return "C'est déjà noté.", False
        await self._store.add_memory(
            contenu, category=category, subject=subject, source="luna"
        )
        await self._notify_memory_change(subject)
        return "C'est noté.", False

    async def _tool_lister_souvenirs(self, _args, who: Speaker):
        subject = who.subject or "guillaume"
        mems = await self._store.list_memories(subject=subject, limit=200)
        if not mems:
            return "Je n'ai encore rien retenu de particulier.", False
        return _compact([
            {"id": m["id"], "categorie": m["category"], "contenu": m["content"]}
            for m in mems
        ]), False

    async def _tool_oublier(self, args, who: Speaker):
        subject = who.subject or "guillaume"
        mem_id = str(args.get("id") or "").strip()
        if not mem_id:
            return "Précise l'identifiant du souvenir à oublier.", True
        # On ne supprime qu'un souvenir DU locuteur courant (on ne touche pas à
        # la mémoire d'un autre profil via un identifiant deviné).
        mem = await self._store.get_memory(mem_id)
        if mem is None or mem.get("subject") != subject:
            return "Je n'ai pas trouvé ce souvenir.", True
        await self._store.delete_memory(mem_id)
        await self._notify_memory_change(subject)
        return "C'est oublié.", False

    # Auto-amélioration encadrée (Phase 6 — Luna PROPOSE ; Guillaume applique) ──
    #
    # Luna ne modifie jamais rien elle-même : `proposer_evolution` passe le diff au
    # crible de la politique (selfmod/policy.py) — un garde-fou de sécurité ou un
    # secret est refusé d'office — puis le range comme proposition à relire. Aucun
    # code n'écrit sur le disque ni ne lance de processus ici.

    async def _notify_suggestions_change(self) -> None:
        if self._on_suggestions_change is not None:
            try:
                await self._on_suggestions_change()
            except Exception:
                log.exception("Notification de changement d'évolutions impossible")

    async def _tool_lire_mon_code(self, args, _utt, _src):
        if self._source is None:
            return "La lecture de mon code n'est pas disponible.", True
        chemin = str(args.get("chemin") or "").strip()
        if not chemin:
            return _compact({"fichiers": self._source.listing()})[:6000], False
        content, is_error = self._source.read(chemin)
        if is_error:
            return content, True
        return f"# {chemin}\n{content}", False

    async def _tool_proposer_evolution(self, args, _utt, _src):
        titre = str(args.get("titre") or "").strip()[:160]
        diff = str(args.get("diff") or "")
        kind = str(args.get("kind") or "code").strip().lower()
        if kind not in ("code", "config"):
            kind = "code"
        if not titre or not diff.strip():
            return "Donne un titre et un diff unifié.", True
        # Le garde-fou : la politique décide si cette proposition est même recevable.
        verdict = evaluate_diff(diff, kind=kind)
        if not verdict.allowed:
            # Refus de politique : ce n'est pas une erreur d'outil, Luna le relaie.
            return f"Je ne peux pas proposer ça. {verdict.reason}", False
        stats = summarize_diff(diff)
        sug = await self._store.add_suggestion(
            kind=kind, title=titre,
            rationale=str(args.get("motivation") or "").strip()[:1000],
            target=str(args.get("cible") or ", ".join(stats["paths"]))[:300],
            diff=diff,
        )
        await self._notify_suggestions_change()
        return _compact({
            "id": sug["id"], "fichiers": stats["paths"],
            "lignes": {"+": stats["added"], "-": stats["removed"]},
            "etat": "proposé — à relire puis appliquer par Guillaume (Paramètres › Évolutions)",
        }), False

    async def _tool_lister_evolutions(self, args, _utt, _src):
        status = str(args.get("statut") or "").strip() or None
        items = await self._store.list_suggestions(status)
        if not items:
            return "Aucune proposition d'évolution pour l'instant.", False
        return _compact([
            {"id": s["id"], "kind": s["kind"], "titre": s["title"],
             "cible": s["target"], "statut": s["status"]}
            for s in items
        ]), False

    # Scénarios & routines (Phase 8 — Luna PROPOSE ; Guillaume ACTIVE puis déclenche) ─

    async def _tool_proposer_routine(self, args, _utt, _src):
        if self._routines is None:
            return "Les routines ne sont pas activées.", True
        nom = str(args.get("nom") or "").strip()
        etapes = args.get("etapes") or []
        if not nom or not isinstance(etapes, list) or not etapes:
            return "Donne un nom et au moins une étape.", True
        steps = []
        for e in etapes:
            if not isinstance(e, dict):
                continue
            action = str(e.get("action") or "")
            if action not in _ROUTINE_OP:
                return f"Action de routine inconnue : « {action} ».", False
            action_id, extra = _ROUTINE_OP[action]
            params = {"entity_ids": e.get("entity_ids") or [], **extra}
            if action == "chauffage" and e.get("temperature") is not None:
                params["temperature"] = e.get("temperature")
            steps.append({"action_id": action_id, "params": params})
        try:
            routine = await self._routines.propose(
                name=nom, steps=steps, description=str(args.get("description") or ""), source="llm",
            )
        except RoutineError as exc:
            # Refus de validation (ex. action sensible) : Luna le relaie calmement.
            return f"Je ne peux pas créer cette routine. {exc}", False
        return _compact({
            "id": routine["id"], "nom": routine["name"],
            "etapes": [s["label"] for s in routine["steps"]],
            "etat": "proposée — à activer par Guillaume (Paramètres › Routines)",
        }), False

    async def _tool_lancer_routine(self, args, utterance, source):
        if self._routines is None:
            return "Les routines ne sont pas activées.", True
        nom = str(args.get("nom") or "").strip()
        if not nom:
            return "Quelle routine veux-tu lancer ?", True
        text, ok = await self._routines.run_by_name(nom, utterance=utterance, source=source)
        return text, not ok

    async def _tool_lister_routines(self, _args, _utt, _src):
        if self._routines is None:
            return "Les routines ne sont pas activées.", True
        routines = await self._store.list_routines()
        if not routines:
            return "Aucune routine pour l'instant.", False
        return _compact([
            {"nom": r["name"], "etat": r["status"],
             "etapes": [s.get("label") for s in r["steps"]]}
            for r in routines
        ]), False

    # Musique multi-pièces (Phase 9 — pilotage via les media_player de Nova) ──

    async def _tool_etat_musique(self, _args, _utt, _src):
        if self._ha is None or not self._ha.connected:
            return self._NOVA_ABSENTE, True
        players = media_lib.snapshot(self._ha, live_only=True)
        if not players:
            return "Rien ne joue pour l'instant.", False
        return _compact([
            {"nom": p["nom"], "piece": p["piece"], "etat": p["etat"],
             "titre": p["titre"], "artiste": p["artiste"],
             "volume": p["volume"], "source": p["source"]}
            for p in players
        ])[:4000], False

    _MEDIA_SIMPLE_OPS = {
        "pause": "pause", "stop": "stop", "suivant": "next", "precedent": "previous",
        "monter_volume": "volume_up", "baisser_volume": "volume_down",
        "couper_son": "mute", "remettre_son": "unmute",
    }

    async def _tool_musique(self, args, utterance, source):
        if self._ha is None or self._engine is None or self._media is None:
            return self._NOVA_ABSENTE, True
        from ..norm import normalize

        operation = str(args.get("operation") or "")
        zone = str(args.get("zone") or "").strip()
        entity_ids = args.get("entity_ids") or []
        players = media_lib.resolve_players(
            self._ha, zone=zone, entity_ids=entity_ids,
            default_room=self._media.default_room, default_player=self._media.default_player,
        )
        if not players:
            return ("Je ne sais pas sur quel lecteur agir — précise une pièce "
                    "(ex. « dans le salon »), ou lance d'abord la musique.", False)

        params: dict = {"entity_ids": players}
        if operation in self._MEDIA_SIMPLE_OPS:
            params["op"] = self._MEDIA_SIMPLE_OPS[operation]
        elif operation == "volume":
            try:
                niveau = int(args.get("niveau"))
            except (TypeError, ValueError):
                return "À quel volume ? Donne un niveau entre 0 et 100.", False
            params.update(op="volume", level=max(0, min(100, niveau)) / 100)
        elif operation == "source":
            src = str(args.get("source") or "").strip()
            if not src:
                return "Quelle source ?", False
            params.update(op="source", source=src)
        elif operation == "jouer":
            contenu = str(args.get("contenu") or "").strip()
            preset = self._media.presets.get(normalize(contenu)) if contenu else None
            if preset and preset.get("content_id"):
                params.update(op="play_media", media_content_id=preset["content_id"],
                              media_content_type=preset.get("content_type") or "music")
            elif preset and preset.get("source"):
                params.update(op="source", source=preset["source"])
            elif args.get("source"):
                params.update(op="source", source=str(args["source"]))
            elif contenu:
                # Passe-plat (URI Spotify, Music Assistant…) : au pire, Nova refuse proprement.
                params.update(op="play_media", media_content_id=contenu, media_content_type="music")
            else:
                params["op"] = "play"  # reprendre la lecture
        elif operation == "transferer":
            cible = str(args.get("cible") or "").strip()
            targets = media_lib.resolve_players(self._ha, zone=cible) if cible else []
            if not targets:
                return "Vers quelle pièce transférer ? Précise-la.", False
            params.update(op="join", group_members=targets)
        else:
            return f"Opération musique inconnue : {operation}.", True

        outcome = await self._engine.run_direct(
            "ha.media", params, utterance=utterance, source=f"{source} (via LLM)"
        )
        return outcome.text, not outcome.ok

    # Minuteurs & rappels (Phase 10 — 100% local) ─────────────────────────

    def _reminder_tz(self):
        from zoneinfo import ZoneInfo
        try:
            return ZoneInfo(self._tz)
        except Exception:
            return None

    def _fmt_when(self, due_iso: str) -> str:
        from datetime import datetime
        tz = self._reminder_tz()
        try:
            local = datetime.fromisoformat(due_iso).astimezone(tz)
        except ValueError:
            return "bientôt"
        now = datetime.now(tz)
        t = f"{local.hour}h{local.minute:02d}" if local.minute else f"{local.hour}h"
        days = (local.date() - now.date()).days
        if days == 0:
            return f"à {t}"
        if days == 1:
            return f"demain à {t}"
        return f"le {local.day:02d}/{local.month:02d} à {t}"

    async def _notify_reminders_change(self) -> None:
        if self._on_reminders_change is not None:
            try:
                await self._on_reminders_change()
            except Exception:
                log.exception("Notification de changement de rappels impossible")

    async def _tool_minuteur(self, args, _utt, _src):
        minutes = args.get("minutes")
        secondes = args.get("secondes")
        due = compute_due_iso(tz=self._reminder_tz(), minutes=minutes, seconds=secondes)
        if due is None:
            return "Donne-moi une durée (ex. 10 minutes).", False
        libelle = str(args.get("libelle") or "").strip()[:120]
        await self._store.add_reminder(kind="timer", label=libelle, due_at=due)
        await self._notify_reminders_change()
        total = int((float(minutes or 0) * 60) + float(secondes or 0))
        m, s = divmod(total, 60)
        dur = (f"{m} min" + (f" {s} s" if s else "")) if m else f"{s} s"
        return f"C'est parti — minuteur de {dur}{f' pour « {libelle} »' if libelle else ''}.", False

    async def _tool_rappel(self, args, _utt, _src):
        libelle = str(args.get("libelle") or "").strip()[:200]
        if not libelle:
            return "De quoi veux-tu que je te rappelle ?", False
        due = compute_due_iso(
            tz=self._reminder_tz(), dans_minutes=args.get("dans_minutes"), a=args.get("a")
        )
        if due is None:
            return "Précise quand (ex. « dans 20 minutes » ou « à 18h »).", False
        await self._store.add_reminder(kind="reminder", label=libelle, due_at=due)
        await self._notify_reminders_change()
        return f"C'est noté. Je te le rappellerai {self._fmt_when(due)} : {libelle}.", False

    async def _tool_lister_rappels(self, _args, _utt, _src):
        items = await self._store.list_reminders("active")
        if not items:
            return "Aucun minuteur ni rappel en cours.", False
        return _compact([
            {"id": r["id"], "type": "minuteur" if r["kind"] == "timer" else "rappel",
             "libelle": r["label"], "echeance": self._fmt_when(r["due_at"])}
            for r in items
        ]), False

    async def _tool_annuler_rappel(self, args, _utt, _src):
        rid = str(args.get("id") or "").strip()
        r = await self._store.get_reminder(rid)
        if r is None or r["status"] != "active":
            return "Je n'ai pas trouvé ce rappel actif.", False
        await self._store.set_reminder_status(rid, "cancelled")
        await self._notify_reminders_change()
        return "C'est annulé.", False
