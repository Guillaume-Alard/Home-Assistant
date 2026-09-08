"""L0 — la confiance d'un fait, et sa décroissance (D6 de P4).

§4 du cahier des charges : « Chaque catégorie de fait porte un taux de
décroissance : une habitude de coucher se périme plus vite qu'une préférence de
température. » Voici le combien, écrit en clair et testable au jour près.

    confiance = maturité × fraîcheur

      maturité  = 1 − 0,5 ^ (observations / 2)
      fraîcheur = 0,5 ^ (jours depuis la dernière observation / demi-vie)

La maturité dit « je l'ai vu assez souvent pour y croire », la fraîcheur dit
« je l'ai vu assez récemment pour que ce soit encore vrai ». Les deux sont
nécessaires : vingt couchers observés il y a six mois ne décrivent plus
personne, et un seul coucher observé hier ne décrit pas une habitude.

Sous `SEUIL_UTILE`, un fait **n'est pas supprimé** — §4 l'interdit — il cesse
simplement d'être proposé. C'est la différence entre oublier et se taire.

Aucune dépendance : comme la fusion d'identité de P3, ce calcul se teste sans
base de données, sans maison et sans horloge réelle.
"""

from __future__ import annotations

from datetime import datetime, timedelta

#: Demi-vies, en jours, par catégorie de fait (§4, D6).
DEMI_VIES: dict[str, float] = {
    "heure_de_coucher": 30.0,  # une habitude se déplace
    "sequence_recurrente": 45.0,
    "preference_eclairage": 90.0,
    "preference_temperature": 180.0,  # une préférence dure
    "fait_declare": 365.0,  # « Clara est allergique aux chats »
}

#: Une catégorie inconnue se périme le plus vite. Comme le niveau 3 par défaut
#: de `autonomy.py`, l'oubli échoue du côté sûr : ce qu'on ne sait pas nommer
#: cesse d'être proposé le plus tôt possible.
DEMI_VIE_PAR_DEFAUT = 30.0

#: Sous ce seuil, le fait existe toujours mais ne sert plus à rien (D6).
SEUIL_UTILE = 0.25

#: Le seuil pour **s'appuyer** sur une habitude — proposer, justifier, rappeler.
#: Plus haut que `SEUIL_UTILE`, et volontairement : une seule observation donne
#: déjà 0,29 de confiance, ce qui suffit à ne pas oublier un fait mais pas à en
#: tirer un rappel. Trois soirs identiques (0,65) commencent à décrire quelqu'un ;
#: un seul décrit une soirée.
SEUIL_PROPOSITION = 0.5

#: Nombre d'observations pour lequel la maturité vaut la moitié de son maximum.
#: 2 : deux soirs identiques valent déjà 0,50, trois valent 0,65.
ECHELLE_MATURITE = 2.0


def demi_vie(categorie: str) -> float:
    return DEMI_VIES.get(categorie, DEMI_VIE_PAR_DEFAUT)


def maturite(observations: int) -> float:
    """« Je l'ai vu assez souvent. » 1 obs : 0,29 · 3 : 0,65 · 10 : 0,97."""
    if observations <= 0:
        return 0.0
    return 1.0 - 0.5 ** (observations / ECHELLE_MATURITE)


def fraicheur(jours: float, categorie: str) -> float:
    """« Je l'ai vu assez récemment. » Une demi-vie écoulée divise par deux."""
    if jours <= 0:
        return 1.0
    return 0.5 ** (jours / demi_vie(categorie))


def jours_ecoules(depuis: datetime, maintenant: datetime) -> float:
    """En jours fractionnaires. Négatif ramené à zéro : une observation datée
    dans le futur — horloge qui recule, import — ne vaut pas mieux que maintenant.
    """
    ecart: timedelta = maintenant - depuis
    return max(0.0, ecart.total_seconds() / 86400.0)


def confiance(
    *, observations: int, derniere: datetime, categorie: str, maintenant: datetime
) -> float:
    """La confiance d'un fait à un instant donné, dans [0, 1]."""
    return maturite(observations) * fraicheur(
        jours_ecoules(derniere, maintenant), categorie
    )


def encore_utile(valeur: float) -> bool:
    """Un fait sous le seuil n'est pas faux : il n'est plus assez sûr pour
    qu'on s'en serve. §4 interdit de le supprimer, pas de se taire."""
    return valeur >= SEUIL_UTILE


def assez_sur_pour_proposer(valeur: float) -> bool:
    """Luna a-t-elle le droit de bâtir un rappel là-dessus ?"""
    return valeur >= SEUIL_PROPOSITION
