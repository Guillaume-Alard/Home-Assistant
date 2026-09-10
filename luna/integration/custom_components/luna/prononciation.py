"""Ce que Luna écrit n'est pas toujours ce qui se dit.

Le texte parlé est **exactement** le texte écrit : la carte le passe à
`luna/speak`, et l'agent de conversation le passe au pipeline. Tout ce que le
modèle écrit et que le français ne lit pas arrive donc tel quel dans le
synthétiseur — un identifiant d'entité devient « light point salon », un nombre
décimal devient « dix-neuf point cinq », une astérisque devient « astérisque ».

Ce module ne corrige pas la cause — c'est le rôle du prompt, qui demande du
français — mais il en attrape ce qui passe. Les deux se complètent : une
consigne de prompt n'est jamais une garantie, et §8 veut du déterministe là où
ça compte.

**Ce qu'il produit est correct à l'écrit comme à l'oral.** Ce n'est pas une
coquetterie : Home Assistant n'a qu'un texte, affiché *et* lu. Un « dix-neuf
virgule cinq » écrit en toutes lettres serait bizarre à l'écran ; « 19,5 » est
juste des deux côtés.
"""

from __future__ import annotations

import re
from collections.abc import Callable

#: `19.5` → `19,5`. La virgule décimale est la forme française à l'écrit, et
#: c'est elle qu'espeak lit « virgule ». Bornée par des chiffres des deux côtés :
#: sans ça, la fin d'une phrase suivie d'un nombre y passerait.
_DECIMALE = re.compile(r"(?<=\d)\.(?=\d)")

#: `light.salon_plafond`. Un domaine Home Assistant est en minuscules, l'objet
#: aussi. On ne remplace que si l'entité existe vraiment — inventer un nom pour
#: un mot qui ressemble à un identifiant ferait plus de dégâts que le point.
_ENTITE = re.compile(r"\b([a-z][a-z0-9_]*)\.([a-z0-9_]+)\b")

#: `[texte](url)` → `texte`. L'URL n'a rien à faire dans une phrase parlée.
_LIEN = re.compile(r"\[([^\]]+)\]\(([^)]*)\)")

#: `**gras**`, `*italique*`, `_souligné_`, `` `code` ``.
_EMPHASE = re.compile(r"(\*{1,3}|_{1,3}|`+)(.+?)\1", re.DOTALL)

#: `## Titre` en début de ligne.
_TITRE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)

#: `- item`, `* item`, `• item`, `1. item` en début de ligne.
_PUCE = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+", re.MULTILINE)

#: Ce qui resterait de balisage dépareillé après les passes ci-dessus.
_RESTES = re.compile(r"[*`#]+")

#: Une ponctuation de fin collée au mot suivant : espeak la lit alors comme un
#: mot. « Fait.Ensuite » n'est pas une phrase, c'est deux.
#:
#: La majuscule qui suit n'est pas un détail, c'est **tout le discriminant** :
#: sans elle, la règle coupe aussi `nova.local` et `light.inexistante` en deux,
#: c'est-à-dire précisément les identifiants qu'on n'a pas su résoudre et qu'il
#: valait mieux laisser intacts. Une phrase française reprend par une capitale ;
#: un identifiant Home Assistant est en minuscules.
_COLLEE = re.compile(r"([.!?;:,])(?=[A-ZÀ-ÖØ-Þ])")

_ESPACES = re.compile(r"[ \t]{2,}")


def pour_la_voix(texte: str, nom_de: Callable[[str], str | None] | None = None) -> str:
    """Rend le texte lisible à voix haute sans le rendre faux à l'écrit.

    `nom_de` résout un `entity_id` en nom convivial et rend `None` quand
    l'entité n'existe pas — c'est ce qui distingue un vrai identifiant d'un mot
    qui lui ressemble.
    """
    if not texte:
        return texte

    resolu = _LIEN.sub(r"\1", texte)
    resolu = _TITRE.sub("", resolu)
    resolu = _PUCE.sub("", resolu)
    # Deux passes : `**gras _et_ italique**` s'imbrique.
    for _ in range(2):
        resolu = _EMPHASE.sub(r"\2", resolu)
    resolu = _RESTES.sub("", resolu)

    if nom_de is not None:
        resolu = _ENTITE.sub(lambda trouve: _nommer(trouve, nom_de), resolu)

    resolu = _DECIMALE.sub(",", resolu)
    resolu = _COLLEE.sub(r"\1 ", resolu)
    resolu = _ESPACES.sub(" ", resolu)
    return "\n".join(ligne.rstrip() for ligne in resolu.splitlines()).strip()


def nom_dans(hass) -> Callable[[str], str | None]:
    """Le résolveur adossé au registre d'états de Home Assistant.

    Séparé de `pour_la_voix` pour que celle-ci reste une fonction pure, donc
    éprouvable sans monter une instance.
    """

    def nom_de(entity_id: str) -> str | None:
        etat = hass.states.get(entity_id)
        return etat.name if etat is not None else None

    return nom_de


def _nommer(trouve: re.Match[str], nom_de: Callable[[str], str | None]) -> str:
    identifiant = trouve.group(0)
    return nom_de(identifiant) or identifiant
