"""Identité du locuteur & niveaux de confiance (Phase 2).

Qui parle → comment Luna s'adapte. Règle de sécurité cardinale : la
reconnaissance vocale ne peut JAMAIS *élever* des droits. Les actions sensibles
(déverrouillage, désarmement) restent réservées à l'interface pour tout le monde,
reconnu ou non — exactement comme avant. L'identité sert à **personnaliser**
(prénom, mémoire propre à chacun) et, pour un **inconnu**, à **restreindre**
(conversation et lecture d'état seulement).

Niveaux (du plus large au plus restreint) :
- **owner**  : Guillaume. Canaux de confiance (écrit/UI, Assist authentifié) et
  voix reconnue comme lui. Tout ce qui existe aujourd'hui.
- **household** : une personne enrôlée autre que le propriétaire (conjointe,
  fils). Domotique courante + sa propre mémoire ; pas les outils d'administration.
- **guest** : voix non reconnue (ou personne encore enrôlée). Conversation et
  lecture d'état uniquement ; aucune action, aucune mémoire personnelle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Sujet mémoire du propriétaire (aligne la Phase 1 : subject == "guillaume").
OWNER_KEY = "guillaume"


@dataclass(frozen=True)
class Speaker:
    key: str            # sujet mémoire : "guillaume", id de profil, ou "" si inconnu
    name: str           # nom affiché ; "" si inconnu
    known: bool         # reconnu (ou canal de confiance) ?
    is_owner: bool      # Guillaume ?
    score: float = 0.0  # similarité de la reconnaissance (0 pour l'écrit/inconnu)

    @property
    def subject(self) -> str | None:
        """Sujet mémoire à utiliser (None pour un invité : aucune mémoire)."""
        return self.key if self.known else None

    @property
    def can_act(self) -> bool:
        """Peut déclencher la domotique courante ? (pas un invité)."""
        return self.known

    @property
    def label(self) -> str:
        return self.name if self.known else "invité"


# Canaux de confiance (écrit dans le cockpit, Assist authentifié par jeton) : on
# les traite comme le propriétaire — la reconnaissance vocale ne concerne que la voix.
OWNER = Speaker(key=OWNER_KEY, name="Guillaume", known=True, is_owner=True, score=1.0)
# Voix non reconnue.
UNKNOWN = Speaker(key="", name="", known=False, is_owner=False, score=0.0)


def cosine(a: list[float], b: list[float]) -> float:
    """Similarité cosinus de deux vecteurs (0 si l'un est nul ou de taille ≠)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def identify(query: list[float], profiles: list[dict], threshold: float) -> Speaker:
    """Rend le meilleur profil au-dessus du seuil, sinon UNKNOWN.

    `profiles` : [{id, name, is_owner, vectors: [[…], …]}]. Le score d'un profil
    est la MEILLEURE similarité parmi ses empreintes (robuste à des enrôlements
    de qualité inégale).
    """
    best: Speaker | None = None
    for prof in profiles:
        vectors = prof.get("vectors") or []
        if not vectors:
            continue
        score = max(cosine(query, v) for v in vectors)
        if score < threshold:
            continue
        if best is None or score > best.score:
            is_owner = bool(prof.get("is_owner"))
            best = Speaker(
                key=OWNER_KEY if is_owner else str(prof.get("id") or ""),
                name=str(prof.get("name") or ""),
                known=True,
                is_owner=is_owner,
                score=score,
            )
    return best or UNKNOWN
