"""Normalisation de texte français : minuscules, sans accents, espaces réduits.

Utilisée partout où l'on compare du texte parlé/écrit à des noms (pièces,
protocoles, commandes) — « Salon », « salon » et « sàlon » doivent se retrouver.
"""

from __future__ import annotations

import re
import unicodedata


def normalize(text: str) -> str:
    text = text.lower().replace("’", "'").replace("œ", "oe")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9' \-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()
