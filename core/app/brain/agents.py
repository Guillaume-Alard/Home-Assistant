"""Sous-agents spécialisés (vrai multi-agent).

Sentinel (l'orchestrateur, `brain/llm.py`) peut DÉLÉGUER une tâche à un sous-agent
spécialisé. Un agent = un rôle : un prompt système + un SOUS-ENSEMBLE des outils
existants + un modèle optionnel.

Sécurité — le principe fondateur tient, même à travers la délégation :
  • un sous-agent tourne avec la MÊME identité (`who`) que la demande d'origine :
    la reconnaissance de locuteur n'élève jamais un droit ;
  • il ne voit QUE les outils de son périmètre (sous-ensemble de la Toolbox) —
    jamais une capacité nouvelle ;
  • tout appel d'outil repart par la Toolbox et le MOTEUR « propose puis approuve »
    (aucune écriture directe ; l'invariant statique reste vrai) ;
  • pas de délégation récursive : un sous-agent n'a jamais l'outil `deleguer`.

Ajouter un agent = une entrée dans AGENTS. Rien d'autre à toucher.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    id: str
    label: str
    description: str            # ce que fait l'agent (lu par l'orchestrateur pour router)
    system: str                # prompt système du rôle
    tools: tuple[str, ...] = ()  # outils autorisés (sous-ensemble de la Toolbox)
    web_search: bool = False   # accès à la recherche web native (Claude, si reconnu)
    provider: str = ""         # id fournisseur optionnel ; sinon le fournisseur actif


# Outils de LECTURE communs (jamais d'écriture ici — l'écriture passe par le moteur).
_READ = ("etat_maison", "details_entite", "liste_pieces", "chercher_entites")

# Registre des agents. Ajouter un agent = une entrée ici (rien d'autre à toucher).
AGENTS: dict[str, AgentSpec] = {
    "research": AgentSpec(
        id="research",
        label="Research",
        description=(
            "cherche, compare et vérifie des informations sur le web, avec sources ; "
            "lecture seule, n'agit jamais sur la maison"
        ),
        system=(
            "Tu es Research, le chercheur de Sentinel. Tu trouves, compares et VÉRIFIES "
            "des informations, en CITANT toujours tes sources (le média / le site). Tu ne "
            "fais que LIRE : aucune action sur la maison, aucune proposition. Structure ta "
            "réponse : la ou les solutions, leurs avantages/inconvénients, ta recommandation, "
            "et un niveau de confiance. Reste concis et honnête sur ce qui est incertain."
        ),
        tools=_READ,
        web_search=True,
    ),
    "home": AgentSpec(
        id="home",
        label="Home",
        description=(
            "spécialiste domotique (Home Assistant) : diagnostique l'état de la maison "
            "et agit sur la domotique courante ; le reste passe par une proposition"
        ),
        system=(
            "Tu es Home, le spécialiste domotique de Sentinel (Home Assistant / Nova). Tu "
            "DIAGNOSTIQUES (états d'entités, capteurs, disponibilité) via tes lectures, et tu "
            "AGIS sur la domotique COURANTE quand c'est demandé : lumières, volets, scènes, "
            "chauffage, musique, protocoles. Vérifie toujours les entity_ids avec etat_maison "
            "AVANT d'agir, et ne prétends jamais qu'une action a réussi sans confirmation. "
            "Pour tout ce qui sort de la domotique courante — ou toute action sensible — passe "
            "par une PROPOSITION (creer_proposition) : tu n'exécutes jamais une action sensible "
            "toi-même. Sois concret et concis."
        ),
        # Écritures possibles, mais UNIQUEMENT via ces outils → le moteur « propose
        # puis approuve » s'applique (sensible = interface, comme partout).
        tools=_READ + ("action_domotique", "lancer_protocole", "etat_musique", "musique", "creer_proposition"),
    ),
    "developer": AgentSpec(
        id="developer",
        label="Developer",
        description=(
            "relit le propre code de Luna et PROPOSE des évolutions (diffs) que "
            "Guillaume valide ; n'applique jamais rien lui-même"
        ),
        system=(
            "Tu es Developer, le développeur de Sentinel. Tu peux RELIRE le propre code de "
            "Luna (lire_mon_code) et PROPOSER une évolution sous forme de diff "
            "(proposer_evolution) que Guillaume relit et applique — tu n'appliques JAMAIS "
            "rien toi-même, et tu ne touches jamais un garde-fou de sécurité ni un secret "
            "(refusé d'office par la politique). Analyse d'abord, propose un diff juste et "
            "minimal, et explique le pourquoi. Rigoureux et concis."
        ),
        # Auto-amélioration : lecture + PROPOSITION de diff (jamais d'application).
        tools=("lire_mon_code", "proposer_evolution", "lister_evolutions"),
    ),
    "infra": AgentSpec(
        id="infra",
        label="Infra",
        description=(
            "diagnostique la santé des systèmes (conteneurs, ressources, Nova) et "
            "PROPOSE une remédiation ; n'exécute rien lui-même"
        ),
        system=(
            "Tu es Infra, l'administrateur système de Sentinel (Unraid, Docker, Nova). Tu "
            "DIAGNOSTIQUES la santé des systèmes (sante_systemes, audit_systemes) : "
            "conteneurs, ressources, entités indisponibles, mises à jour. Tu n'exécutes rien "
            "toi-même : une remédiation (redémarrer un conteneur, recharger une intégration…) "
            "passe par une PROPOSITION que Guillaume valide. Donne un diagnostic clair, puis, "
            "si besoin, propose l'action avec sa justification."
        ),
        tools=("sante_systemes", "audit_systemes", "creer_proposition"),
    ),
    "vision": AgentSpec(
        id="vision",
        label="Vision",
        description=(
            "regarde une caméra de Nova et décrit ce qui est visible ; lecture seule, "
            "n'agit jamais sur la maison"
        ),
        system=(
            "Tu es Vision, les yeux de Sentinel. Tu REGARDES une caméra de Nova (regarder) et "
            "décris ce qui est visible, FACTUELLEMENT, sans rien inventer. C'est une "
            "OBSERVATION, jamais une action : pour intervenir sur ce que tu vois, tu le "
            "signales — cela passe par une proposition. Tu peux corréler avec l'état des "
            "entités (etat_maison, details_entite)."
        ),
        tools=_READ + ("regarder",),
    ),
}


# Outils qui AGISSENT sur le réel via le moteur, vs. qui ne font que PROPOSER.
# Sert à qualifier la « posture » d'un agent pour le cockpit (pur affichage).
_ACT_TOOLS = {"action_domotique", "lancer_protocole", "musique", "lancer_routine"}
_PROPOSE_TOOLS = {"creer_proposition", "proposer_evolution", "proposer_routine"}


def agent_posture(spec: AgentSpec) -> str:
    """Posture d'écriture d'un agent : « act » (via le moteur), « propose » ou « read ».

    Reflète exactement la colonne « Écrit ? » de la doc — dérivée des outils, jamais
    déclarée à part (une entrée de registre ne peut pas mentir sur ses droits)."""
    tools = set(spec.tools)
    if tools & _ACT_TOOLS:
        return "act"
    if tools & _PROPOSE_TOOLS:
        return "propose"
    return "read"


def agent_roster() -> list[dict]:
    """Roster des agents pour le cockpit (id, libellé, rôle, posture, web).

    Purement descriptif : rien ici n'ouvre un droit — la posture est CALCULÉE à
    partir des outils réels du rôle."""
    return [
        {"id": a.id, "label": a.label, "description": a.description,
         "posture": agent_posture(a), "web": a.web_search}
        for a in AGENTS.values()
    ]


def agent_labels() -> str:
    """Ligne « agents disponibles » pour le prompt de l'orchestrateur."""
    return " ; ".join(f"{a.id} — {a.description}" for a in AGENTS.values())


def filter_specs(specs: list[dict], allowed: tuple[str, ...]) -> list[dict]:
    """Restreint une liste de déclarations d'outils au périmètre autorisé d'un agent."""
    allow = set(allowed)
    return [s for s in specs if s.get("name") in allow]
