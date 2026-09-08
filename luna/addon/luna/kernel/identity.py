"""L0 — la fusion des signaux d'identité (§6, décision C4).

Pur : ni réseau, ni modèle, ni base. Des vecteurs entrent, une décision sort.
C'est pour ça que ce calcul vit ici et pas dans L2 — il se teste au vecteur
près, ce qui est la seule façon honnête de régler des seuils.

**Ce qui compte, c'est la marge.** La sortie testable de §11 n'est pas « Luna
reconnaît Guillaume » mais « Luna *distingue* Guillaume de Clara ». Deux scores
élevés ne distinguent rien : c'est l'écart entre les deux premiers qui décide.
"""

from __future__ import annotations

import math
from datetime import timedelta

from pydantic import Field

from .schemas import Modele

# ── Présence (§6 : « Le téléphone à la maison indique une probabilité ») ───

PRESENCE_PRESENT = 0.85
PRESENCE_ABSENT = 0.15
#: Aucun capteur déclaré pour ce profil : on ne présume rien.
PRESENCE_INCONNUE = 0.50

#: Part de la présence dans le score final.
#:
#: §6 est explicite : « Le téléphone à la maison indique une *probabilité*, pas
#: une certitude. La voix et le visage **confirment**. » La présence pondère
#: donc la voix, elle ne la remplace pas et surtout **elle ne peut pas
#: l'annuler** : un téléphone oublié dans la voiture ne doit pas rendre son
#: propriétaire méconnaissable. À 0.5, une absence coûte environ 38 % du score,
#: assez pour départager deux voix proches, pas assez pour opposer un veto.
POIDS_PRESENCE = 0.5

# ── Seuils (H48) — points de départ, à régler sur de vraies voix ──────────

#: En dessous, la ressemblance ne compte pas du tout.
SEUIL_COSINUS = 0.55
#: Le meilleur score doit atteindre ça pour qu'on ose décider.
SCORE_MINIMUM = 0.35
#: Et devancer le suivant d'au moins ça.
MARGE_MINIMUM = 0.15

#: Sur un appareil partagé, l'identité retombe à `unknown` après ce silence.
DUREE_IDENTITE = timedelta(minutes=5)

INCONNU = "unknown"


class Candidat(Modele):
    """Un profil en lice, avec ses deux signaux."""

    profil: str
    presence: float = PRESENCE_INCONNUE
    #: Cosinus brut entre l'empreinte entendue et le centroïde du profil.
    cosinus: float = 0.0

    @property
    def voix(self) -> float:
        """Le cosinus ramené sur [0, 1], seuil de C4 déduit."""
        if self.cosinus <= SEUIL_COSINUS:
            return 0.0
        return (self.cosinus - SEUIL_COSINUS) / (1.0 - SEUIL_COSINUS)

    @property
    def score(self) -> float:
        """La voix, pondérée par la présence — jamais bridée par elle.

        Une multiplication simple donnerait à la présence un droit de veto :
        avec `presence = 0.15`, aucun score ne pourrait atteindre le seuil de
        décision, si franche que soit la voix. Ce serait l'inverse de ce que
        dit §6.
        """
        return self.voix * (1.0 - POIDS_PRESENCE + POIDS_PRESENCE * self.presence)


class Decision(Modele):
    """Ce que la fusion conclut, et de quoi comprendre pourquoi."""

    profil: str
    confiance: float
    marge: float
    #: Luna doit-elle poser la question plutôt que de trancher ?
    demander: bool
    scores: dict[str, float] = Field(default_factory=dict)

    @property
    def decide(self) -> bool:
        return self.profil != INCONNU


def similarite(a: list[float], b: list[float]) -> float:
    """Cosinus entre deux empreintes. Rend 0 si l'une est nulle ou mal formée."""
    if not a or not b or len(a) != len(b):
        return 0.0
    produit = sum(x * y for x, y in zip(a, b, strict=True))
    norme_a = math.sqrt(sum(x * x for x in a))
    norme_b = math.sqrt(sum(y * y for y in b))
    if norme_a == 0.0 or norme_b == 0.0:
        return 0.0
    return produit / (norme_a * norme_b)


def centroide(empreintes: list[list[float]]) -> list[float]:
    """Moyenne normalisée d'un ensemble d'empreintes."""
    if not empreintes:
        return []
    dim = len(empreintes[0])
    if any(len(e) != dim for e in empreintes):
        raise ValueError("Empreintes de dimensions différentes.")
    moyenne = [sum(e[i] for e in empreintes) / len(empreintes) for i in range(dim)]
    norme = math.sqrt(sum(x * x for x in moyenne))
    return [x / norme for x in moyenne] if norme else moyenne


def coherence(empreintes: list[list[float]]) -> float:
    """Cohésion interne d'une inscription : 1 = toutes identiques.

    Sert à dire tout de suite qu'une inscription est ratée, plutôt que de le
    découvrir trois semaines plus tard quand Luna se trompe de personne.
    """
    if len(empreintes) < 2:
        return 0.0
    centre = centroide(empreintes)
    return sum(similarite(e, centre) for e in empreintes) / len(empreintes)


def fusionner(candidats: list[Candidat]) -> Decision:
    """La formule de C4, et rien d'autre."""
    scores = {c.profil: c.score for c in candidats}
    if not scores:
        return Decision(profil=INCONNU, confiance=0.0, marge=0.0, demander=False)

    classement = sorted(candidats, key=lambda c: c.score, reverse=True)
    premier = classement[0]
    second = classement[1].score if len(classement) > 1 else 0.0
    marge = premier.score - second
    total = sum(scores.values())
    confiance = premier.score / total if total > 0 else 0.0

    assez = premier.score >= SCORE_MINIMUM and marge >= MARGE_MINIMUM
    if assez:
        return Decision(
            profil=premier.profil,
            confiance=confiance,
            marge=marge,
            demander=False,
            scores=scores,
        )

    # Pas assez sûr. Si quelqu'un se détache quand même, Luna demande plutôt
    # que de deviner — c'est la branche de C4, pas un pis-aller.
    return Decision(
        profil=INCONNU,
        confiance=confiance,
        marge=marge,
        demander=premier.score > 0.0,
        scores=scores,
    )


def presence_depuis_etat(etat: str | None) -> float:
    """`device_tracker` → probabilité de présence."""
    if etat is None:
        return PRESENCE_INCONNUE
    return PRESENCE_PRESENT if etat == "home" else PRESENCE_ABSENT
