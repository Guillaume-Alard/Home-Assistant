"""Normalisation de texte français : minuscules, sans accents, espaces réduits.

Utilisée partout où l'on compare du texte parlé/écrit à des noms (pièces,
protocoles, commandes) — « Salon », « salon » et « sàlon » doivent se retrouver.
"""

from __future__ import annotations

import re
import unicodedata


def normalize(text: str) -> str:
    # L'apostrophe devient un espace : « l'entrée » → « l entree », pour que la
    # pièce « entrée » se retrouve par recherche de mots entiers.
    text = text.lower().replace("’", " ").replace("'", " ").replace("œ", "oe")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 \-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def date_francaise(dt) -> str:
    """« mardi 1 septembre 2026 » — partagé par le cerveau et les intents."""
    return f"{JOURS_FR[dt.weekday()]} {dt.day} {MOIS_FR[dt.month - 1]} {dt.year}"
