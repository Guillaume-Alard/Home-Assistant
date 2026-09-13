"""Mémoire persistante (Phase 1) : mise en forme du profil pour le prompt.

Les souvenirs (préférences, habitudes, style, faits) sont stockés par le `Store`
et injectés comme CONTEXTE dans le prompt système — après le point de cache,
donc jamais figés. C'est de l'enrichissement pur : aucune action sur le monde
réel n'en découle. La transparence et le contrôle restent à Guillaume
(Paramètres › Mémoire : il voit, ajoute et supprime tout).
"""

from __future__ import annotations

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
