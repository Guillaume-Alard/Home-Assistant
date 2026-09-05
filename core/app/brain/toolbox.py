"""Les outils exposés au LLM.

Lecture : libre (états, pièces, propositions). Écriture : chaque outil d'action
ne fait qu'invoquer le moteur (`ActionEngine`) — jamais Nova directement. Le
LLM ne peut donc rien exécuter hors liste blanche : au mieux, il crée une
proposition que Guillaume approuvera. Ajouter un outil : docs/OUTILS-LLM.md.
"""

from __future__ import annotations

import json
import logging

from ..actions.engine import RISK_FR, STATUS_FR, ActionEngine
from ..ha.client import HAClient
from ..ha.protocols import ProtocolBook
from ..monitors.docker import DockerError, DockerMonitor
from ..monitors.health import HealthService
from ..store import Store

log = logging.getLogger("sentinel.toolbox")

_RISK_FROM_FR = {"faible": "low", "moyen": "medium", "sensible": "sensitive"}

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
    "sante_systemes": "ausculte les systèmes…",
    "logs_conteneur": "lit des journaux…",
    "audit_systemes": "audite les systèmes…",
    "redemarrer_conteneur": "rédige une proposition…",
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
        docker: DockerMonitor | None = None,
    ):
        self._ha = ha
        self._engine = engine
        self._protocols = protocols
        self._store = store
        self._health = health
        self._docker = docker

    _NOVA_ABSENTE = "Nova (Home Assistant) n'est pas configurée ou pas joignable."
    _MOTEUR_ABSENT = "Le moteur d'actions n'est pas disponible (Nova/Docker non configurés)."

    # ── Déclarations (ordre STABLE : le cache de prompt en dépend) ───────

    def specs(self) -> list[dict]:
        protocol_names = [p.display for p in self._protocols.all()] or ["(aucun protocole configuré)"]
        return [
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
                    "Instantané de santé des systèmes : Nova (entités indisponibles, mises à "
                    "jour), Nebula (charge, RAM, conteneurs Docker, mémoire par conteneur), "
                    "Atrium. À consulter AVANT tout diagnostic (« pourquoi X ne répond plus ? »)."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "logs_conteneur",
                "description": (
                    "Lit les dernières lignes de journal d'un conteneur Docker de Nebula "
                    "(lecture seule) — pour diagnostiquer un service en panne."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "nom": {"type": "string", "description": "Nom (ou partie du nom) du conteneur"},
                        "lignes": {"type": "integer", "description": "Nombre de lignes (défaut 50, max 300)"},
                    },
                    "required": ["nom"],
                },
            },
            {
                "name": "audit_systemes",
                "description": (
                    "Audit de sécurité et de performance : constats classés par gravité "
                    "(critique/attention/info) avec actions suggérées. Présente les constats à "
                    "Guillaume et crée des propositions pour les actions qu'il souhaite."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "redemarrer_conteneur",
                "description": (
                    "Crée une PROPOSITION de redémarrage d'un conteneur Docker (rien ne "
                    "s'exécute avant l'approbation de Guillaume). À utiliser après diagnostic."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "nom": {"type": "string"},
                        "justification": {"type": "string", "description": "Pourquoi ce redémarrage"},
                    },
                    "required": ["nom", "justification"],
                },
            },
        ]

    # ── Exécution ────────────────────────────────────────────────────────

    async def run(self, name: str, args: dict, *, utterance: str, source: str) -> tuple[str, bool]:
        """Exécute un outil. Renvoie (contenu, is_error)."""
        try:
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                return f"Outil inconnu : {name}", True
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
            return "Aucun moniteur n'est configuré.", True
        return _compact(await self._health.snapshot())[:6000], False

    async def _tool_audit_systemes(self, args, _utt, _src):
        if self._health is None:
            return "Aucun moniteur n'est configuré.", True
        return _compact(await self._health.audit())[:6000], False

    async def _tool_logs_conteneur(self, args, _utt, _src):
        if self._docker is None:
            return "La surveillance Docker n'est pas configurée (DOCKER_PROXY_URL).", True
        nom = str(args.get("nom") or "").strip()
        if not nom:
            return "Précise le nom du conteneur.", True
        try:
            lignes = int(args.get("lignes") or 50)
        except (TypeError, ValueError):
            lignes = 50
        try:
            logs = await self._docker.logs(nom, tail=lignes)
        except DockerError as exc:
            return str(exc), True
        return (logs[-4000:] or "(journal vide)"), False

    # Écriture (via le moteur uniquement) ─────────────────────────────────

    async def _tool_redemarrer_conteneur(self, args, _utt, _src):
        if self._docker is None:
            return "La surveillance Docker n'est pas configurée (DOCKER_PROXY_URL).", True
        if self._engine is None:
            return self._MOTEUR_ABSENT, True
        nom = str(args.get("nom") or "").strip()
        if not nom:
            return "Précise le nom du conteneur.", True
        proposal, message = await self._engine.propose(
            title=f"Redémarrer le conteneur {nom}",
            description=f"docker restart {nom} (via le proxy, arrêt propre en 10 s)",
            justification=str(args.get("justification") or ""),
            risk="medium",
            rollback="Le conteneur redémarre avec sa configuration actuelle ; "
                     "aucun changement persistant.",
            action_id="docker.restart",
            params={"name": nom},
            created_by="sentinel (LLM)",
        )
        return message, proposal is None

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
