"""L1 — normalisation de texte, pour la résolution floue des pièces.

« Le Séjour », « sejour », « SÉJOUR » doivent tomber sur la même area.
"""

from __future__ import annotations

import re
import unicodedata

_NON_ALPHANUM = re.compile(r"[^a-z0-9]+")
_ARTICLES = ("le ", "la ", "les ", "l ", "du ", "de la ", "de ", "des ", "un ", "une ")


def normaliser(texte: str) -> str:
    """Minuscules, sans accents, sans ponctuation, articles de tête retirés."""
    sans_accent = "".join(
        c
        for c in unicodedata.normalize("NFD", texte.lower())
        if unicodedata.category(c) != "Mn"
    )
    propre = _NON_ALPHANUM.sub(" ", sans_accent).strip()
    for article in _ARTICLES:
        if propre.startswith(article):
            propre = propre[len(article) :]
            break
    return " ".join(propre.split())
