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


def format_profile(memories: list[dict]) -> str:
    """Rend un bloc lisible « ce que je sais de toi », groupé par catégorie.

    Renvoie une chaîne vide si aucun souvenir exploitable — le bloc n'est alors
    pas injecté du tout dans le prompt.
    """
    by_cat: dict[str, list[str]] = {}
    for m in memories or []:
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        by_cat.setdefault(normalize_category(m.get("category")), []).append(content)

    lines: list[str] = []
    for cat, label in CATEGORY_LABELS.items():
        items = by_cat.get(cat)
        if not items:
            continue
        lines.append(f"{label} :")
        lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)
