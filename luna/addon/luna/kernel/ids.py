"""L0 — identifiants.

Triables dans le temps (préfixe horodaté en base 36) et non devinables (suffixe
aléatoire). Un identifiant porte son type : `c_` conversation, `m_` message,
`p_` proposition, `a_` action journalisée.
"""

from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(n: int) -> str:
    if n == 0:
        return "0"
    chiffres = []
    while n:
        n, reste = divmod(n, 36)
        chiffres.append(_ALPHABET[reste])
    return "".join(reversed(chiffres))


def nouvel_id(prefixe: str) -> str:
    """`c_` + horodatage en millisecondes + 6 caractères aléatoires."""
    return f"{prefixe}_{_base36(int(time.time() * 1000))}{secrets.token_hex(3)}"
