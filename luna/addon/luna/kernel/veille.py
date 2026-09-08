"""L0 — les règles de la veille : quand se taire, quand se répéter, quoi croire.

Trois décisions, toutes prises ici, toutes pures :

* **Les heures de silence** (H58). Un rappel de coucher a le droit de parler à
  23 h ; une ampoule oubliée dans le garage, non. Seul le niveau `critical`
  traverse.
* **La répétition** (H57). Un capteur qui s'agite dix fois en une heure produit
  une alerte, pas dix. C'est la différence entre une veille et un harcèlement.
* **Le score de suggestion** (§12, C.2). Accepter renforce, refuser affaiblit,
  trois refus valent un « ne plus me le dire ».

Le compteur porte sur la **clé** — `prédicat|profil|entité` — et pas sur
l'occurrence : refuser trois fois « éteins le séjour le soir » met la règle en
sourdine, pas trois soirées distinctes.

Aucune dépendance : ces règles se testent à l'heure près sans base ni maison.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

Niveau = Literal["info", "warning", "critical"]

#: H58 — les heures pendant lesquelles Luna se tait.
SILENCE_DEBUT = time(22, 30)
SILENCE_FIN = time(7, 0)

#: H57 — délai minimal entre deux émissions d'une même alerte.
REPETITION_MINIMALE = timedelta(hours=4)

#: §12 — la boucle de feedback, chiffrée.
SCORE_INITIAL = 0.5
GAIN_ACCEPTATION = 0.15
PERTE_REFUS = 0.25
SEUIL_REMONTEE = 0.2
REFUS_AVANT_SOURDINE = 3
DUREE_SOURDINE = timedelta(days=30)

#: H59 — un fait à relire que personne ne tranche finit par disparaître de la
#: file. Sans ça, la file de relecture devient un cimetière que plus personne
#: n'ouvre — et une file qu'on n'ouvre plus ne protège plus rien.
EXPIRATION_RELECTURE = timedelta(days=14)


def cle_suggestion(predicat: str, profil: str | None, entite: str | None) -> str:
    """`prédicat|profil|entité`. Le compteur de §12 porte là-dessus."""
    return f"{predicat}|{profil or ''}|{entite or ''}"


def dans_les_heures_de_silence(quand: datetime) -> bool:
    """Vrai entre 22 h 30 et 7 h. L'intervalle passe minuit, d'où le `or`."""
    heure = quand.time()
    return heure >= SILENCE_DEBUT or heure < SILENCE_FIN


def peut_parler(niveau: Niveau, quand: datetime, *, silence: bool = True) -> bool:
    """`silence=False` sur une règle qui a le droit de réveiller (le coucher).

    Une alerte `critical` traverse toujours : c'est la seule chose que §5
    justifie de dire à 3 h du matin.
    """
    if niveau == "critical":
        return True
    if not silence:
        return True
    return not dans_les_heures_de_silence(quand)


def peut_repeter(derniere: datetime | None, maintenant: datetime) -> bool:
    """H57 : quatre heures entre deux émissions d'une même alerte."""
    if derniere is None:
        return True
    return maintenant - derniere >= REPETITION_MINIMALE


@dataclass(frozen=True)
class Score:
    """L'état d'apprentissage d'une clé de suggestion (§12)."""

    score: float = SCORE_INITIAL
    refus: int = 0
    sourdine_jusqua: datetime | None = None

    def remonte(self, maintenant: datetime) -> bool:
        """La suggestion a-t-elle encore le droit d'apparaître ?"""
        if self.sourdine_jusqua is not None and self.sourdine_jusqua > maintenant:
            return False
        return self.score >= SEUIL_REMONTEE


def appliquer_retour(score: Score, action: str, maintenant: datetime) -> Score:
    """Applique un retour utilisateur. Rend un nouveau `Score` — jamais en place.

    `accepted` remet le compteur de refus à zéro : trois refus *consécutifs*
    valent une sourdine, pas trois refus dans toute une vie.

    Un refus qui fait passer le score **sous le plancher** vaut lui aussi une
    sourdine, et le score repart au plancher. Sans ça, §12 se contredit : deux
    refus depuis 0,5 amènent le score à 0,0, la règle cesse d'apparaître, plus
    personne ne peut donc l'accepter — et la « mise en sourdine de trente
    jours » devient une mort définitive qu'aucune des deux règles ne voulait.
    Ici les deux chemins mènent au même endroit : trente jours de silence, puis
    une nouvelle chance, à l'essai.
    """
    if action == "accepted":
        return Score(
            score=min(1.0, score.score + GAIN_ACCEPTATION),
            refus=0,
            sourdine_jusqua=score.sourdine_jusqua,
        )
    if action == "rejected":
        refus = score.refus + 1
        valeur = max(0.0, score.score - PERTE_REFUS)
        if refus >= REFUS_AVANT_SOURDINE or valeur < SEUIL_REMONTEE:
            return Score(
                score=SEUIL_REMONTEE,
                refus=0,
                sourdine_jusqua=maintenant + DUREE_SOURDINE,
            )
        return Score(score=valeur, refus=refus, sourdine_jusqua=score.sourdine_jusqua)
    if action == "muted":
        return Score(
            score=score.score, refus=0, sourdine_jusqua=maintenant + DUREE_SOURDINE
        )
    return score
