"""Mémoire persistante (Phase 1) : mise en forme du profil pour le prompt.

Les souvenirs (préférences, habitudes, style, faits) sont stockés par le `Store`
et injectés comme CONTEXTE dans le prompt système — après le point de cache,
donc jamais figés. C'est de l'enrichissement pur : aucune action sur le monde
réel n'en découle. La transparence et le contrôle restent à Guillaume
(Paramètres › Mémoire : il voit, ajoute et supprime tout).
"""

from __future__ import annotations

import json
import math

# Catégories reconnues, dans l'ordre d'affichage, avec leur libellé.
CATEGORY_LABELS = {
    "preference": "Préférences",
    "habitude": "Habitudes",
    "style": "Style de langage souhaité",
    "fait": "À savoir",
}
DEFAULT_CATEGORY = "fait"

# Variantes courantes tolérées en entrée (accents, pluriels, synonymes).
_ALIASES = {
    "préférence": "preference",
    "préférences": "preference",
    "preferences": "preference",
    "habitudes": "habitude",
    "style de langage": "style",
    "langage": "style",
    "ton": "style",
    "faits": "fait",
    "contexte": "fait",
    "info": "fait",
    "information": "fait",
}


def normalize_category(value: str | None) -> str:
    """Ramène une catégorie libre vers l'une des catégories connues."""
    cat = (value or "").strip().lower()
    cat = _ALIASES.get(cat, cat)
    return cat if cat in CATEGORY_LABELS else DEFAULT_CATEGORY


# Niveaux de mémoire (brique 4), dans l'ordre d'injection. « utilisateur » est le
# PROFIL STABLE (qui est Guillaume) ; les autres organisent le reste du contexte.
SCOPE_LABELS = {
    "utilisateur": "Profil de Guillaume",
    "maison": "La maison",
    "projet": "Projets en cours",
    "conversation": "Contexte récent",
}
SCOPE_ORDER = ("utilisateur", "maison", "projet", "conversation")
DEFAULT_SCOPE = "utilisateur"
# Le contexte « conversation » est passager : on n'en injecte que les plus récents.
CONVERSATION_INJECT_MAX = 8

_SCOPE_ALIASES = {
    "user": "utilisateur", "profil": "utilisateur", "profile": "utilisateur",
    "moi": "utilisateur", "guillaume": "utilisateur", "perso": "utilisateur",
    "home": "maison", "domotique": "maison", "logement": "maison",
    "projets": "projet", "travail": "projet", "boulot": "projet",
    "work": "projet", "project": "projet",
    "session": "conversation", "recent": "conversation", "récent": "conversation",
    "passager": "conversation", "éphémère": "conversation", "ephemere": "conversation",
}


def normalize_scope(value: str | None) -> str:
    """Ramène un niveau libre vers l'un des niveaux connus (défaut : utilisateur)."""
    scope = (value or "").strip().lower()
    scope = _SCOPE_ALIASES.get(scope, scope)
    return scope if scope in SCOPE_LABELS else DEFAULT_SCOPE


def format_profile(memories: list[dict]) -> str:
    """Rend un bloc lisible « ce que je sais », groupé par NIVEAU puis catégorie.

    Le profil stable (niveau « utilisateur ») vient en premier ; le contexte
    « conversation », passager, est borné aux plus récents. Renvoie une chaîne
    vide si aucun souvenir exploitable — le bloc n'est alors pas injecté du tout.
    """
    by_scope: dict[str, list[dict]] = {}
    for m in memories or []:
        if not str(m.get("content") or "").strip():
            continue
        by_scope.setdefault(normalize_scope(m.get("scope")), []).append(m)

    sections: list[str] = []
    for scope in SCOPE_ORDER:
        items = by_scope.get(scope)
        if not items:
            continue
        if scope == "conversation" and len(items) > CONVERSATION_INJECT_MAX:
            items = items[-CONVERSATION_INJECT_MAX:]  # ne garder que les plus récents
        block = _format_scope(scope, items)
        if block:
            sections.append(block)
    return "\n\n".join(sections)


# ── RAG mémoire : récupération par pertinence sémantique ──────────────────

def _mem_vector(m: dict) -> list[float] | None:
    """Vecteur d'embedding d'un souvenir (stocké en JSON), ou None."""
    v = m.get("embedding")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, list) and v else None


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rank_by_similarity(query_vec: list[float] | None, memories: list[dict], top_k: int) -> list[dict]:
    """Les `top_k` souvenirs les plus proches de la requête (cosinus). Ignore ceux
    sans vecteur ou de dimension différente (vecteur périmé)."""
    if not query_vec or top_k <= 0:
        return []
    dim = len(query_vec)
    scored = []
    for m in memories:
        vec = _mem_vector(m)
        if vec is None or len(vec) != dim:
            continue
        scored.append((_cosine(query_vec, vec), m))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [m for _, m in scored[:top_k]]


def select_context(
    memories: list[dict], query_vec: list[float] | None = None, *, top_k: int = 6, recent: int = 60
) -> list[dict]:
    """Choisit les souvenirs à injecter. Sans vecteur de requête (RAG inactif) :
    les plus récents (comportement d'origine). Avec : le PROFIL STABLE (niveau
    « utilisateur ») + les `top_k` souvenirs les plus PERTINENTS des autres
    niveaux — pour que le profil ne soit jamais évincé et qu'un souvenir ancien
    mais pertinent remonte."""
    mems = [m for m in memories if str(m.get("content") or "").strip()]
    if not query_vec:
        return mems[-recent:] if recent and recent > 0 else mems
    profile = [m for m in mems if normalize_scope(m.get("scope")) == "utilisateur"]
    if recent and recent > 0:
        profile = profile[-recent:]
    selected = list(profile)
    kept = {m.get("id") for m in profile}
    for m in rank_by_similarity(query_vec, mems, top_k):
        if m.get("id") not in kept:
            selected.append(m)
            kept.add(m.get("id"))
    return selected


def _format_scope(scope: str, items: list[dict]) -> str:
    """Un niveau : son en-tête, puis les souvenirs groupés par catégorie."""
    by_cat: dict[str, list[str]] = {}
    for m in items:
        content = str(m.get("content") or "").strip()
        if content:
            by_cat.setdefault(normalize_category(m.get("category")), []).append(content)
    if not by_cat:
        return ""
    lines = [f"{SCOPE_LABELS[scope]} :"]
    for cat, label in CATEGORY_LABELS.items():
        items_cat = by_cat.get(cat)
        if not items_cat:
            continue
        lines.append(f"{label} :")
        lines.extend(f"- {item}" for item in items_cat)
    return "\n".join(lines)
