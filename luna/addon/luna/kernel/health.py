"""L0 — la santé de l'installation : seuils, groupement, clés d'incident (P5).

Ce module ne sait pas ce qu'est Home Assistant. Il sait qu'une chose peut être
tombée depuis un certain temps, qu'un paquet de choses tombées ensemble ne fait
qu'une nouvelle, et qu'une gardienne qui parle trop n'est plus écoutée.

**Le bruit est le sujet de la phase** (E4). Une maison a trois cents entités ;
après un redémarrage elles sont toutes indisponibles. Une gardienne qui émet
une alerte par entité rend le tiroir inutilisable en une heure — et alors
l'alerte qui comptait vraiment se perd avec les autres. Les cinq règles ci-
dessous sont là pour ça, et elles se testent à la minute près.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

Famille = Literal["entite", "integration", "automatisation", "loggia"]

#: H65 — seul `unavailable` est une panne. `unknown` est un état légitime : un
#: capteur qui n'a pas encore de valeur n'est pas cassé, et la moitié d'une
#: maison passe par là après chaque redémarrage.
ETAT_PANNE = "unavailable"

#: H66 — durée d'indisponibilité continue avant de compter. En dessous, c'est
#: un hoquet : un Zigbee qui bat de l'aile trente secondes n'intéresse personne.
SEUIL_PANNE = timedelta(minutes=30)

#: H67 — après une (re)connexion à Home Assistant, on ne signale rien. Un
#: redémarrage rend tout indisponible pendant quelques minutes ; sans cette
#: grâce, chaque redémarrage produirait une avalanche.
GRACE = timedelta(minutes=10)

#: H68 — au-delà, l'alerte nomme l'intégration et compte les entités au lieu de
#: les énumérer. « 12 entités de Zigbee2MQTT sont indisponibles » est un
#: diagnostic ; douze alertes n'en sont pas un.
SEUIL_GROUPEMENT = 3

#: Les états d'une intégration qui méritent qu'on en parle. `not_loaded` n'y est
#: pas : une intégration désactivée à la main n'est pas une panne.
ETATS_INTEGRATION_CASSEE = frozenset(
    {"setup_error", "setup_retry", "migration_error", "failed_unload"}
)

#: H72 — plafond d'appels au modèle par jour, toutes anomalies confondues.
DIAGNOSTICS_PAR_JOUR = 10

CATEGORIE = "installation"


def cle_incident(famille: Famille, sujet: str) -> str:
    """`installation|<famille>|<sujet>`.

    C'est aussi la clé de suggestion de §12 : mettre en sourdine un capteur qui
    tombe toutes les semaines ne met pas en sourdine toute la surveillance.
    """
    return f"{CATEGORIE}|{famille}|{sujet}"


def dans_la_grace(depuis_connexion: timedelta) -> bool:
    return depuis_connexion < GRACE


def assez_longtemps(depuis: datetime, maintenant: datetime, seuil: timedelta) -> bool:
    """Une panne compte-t-elle, à cet instant ?"""
    return maintenant - depuis >= seuil


@dataclass(frozen=True)
class Panne:
    """Une entité tombée, avec ce qu'il faut pour la regrouper."""

    entity_id: str
    depuis: datetime
    #: L'entrée de configuration dont elle vient. Vide = on ne sait pas, et on
    #: ne regroupe alors rien : mieux vaut trois lignes justes qu'un
    #: regroupement inventé.
    entree: str = ""
    integration: str = ""
    nom: str = ""


@dataclass(frozen=True)
class Groupe:
    """Ce qui deviendra une alerte : une entité seule, ou tout un hub."""

    famille: Famille
    sujet: str
    depuis: datetime
    entites: tuple[str, ...] = field(default_factory=tuple)
    integration: str = ""
    nom: str = ""

    @property
    def cle(self) -> str:
        return cle_incident(self.famille, self.sujet)

    @property
    def groupe(self) -> bool:
        return len(self.entites) > 1


def regrouper(pannes: list[Panne]) -> list[Groupe]:
    """Des entités tombées → les alertes qu'on veut vraiment lire (H68).

    Les entités d'une même entrée de configuration se regroupent dès qu'elles
    dépassent `SEUIL_GROUPEMENT` : c'est un hub tombé, pas douze appareils
    cassés le même jour. En dessous du seuil elles restent nommées une par une,
    parce que « le capteur de la buanderie » est plus utile que « une entité de
    Zigbee2MQTT ».

    L'horodatage d'un groupe est celui de la **première** panne : c'est le
    moment où le hub est tombé.
    """
    par_entree: dict[str, list[Panne]] = {}
    seules: list[Panne] = []
    for panne in pannes:
        if panne.entree:
            par_entree.setdefault(panne.entree, []).append(panne)
        else:
            seules.append(panne)

    groupes: list[Groupe] = []
    for entree, lot in par_entree.items():
        if len(lot) > SEUIL_GROUPEMENT:
            groupes.append(
                Groupe(
                    famille="integration",
                    sujet=entree,
                    depuis=min(p.depuis for p in lot),
                    entites=tuple(sorted(p.entity_id for p in lot)),
                    integration=lot[0].integration,
                    nom=lot[0].integration,
                )
            )
        else:
            seules.extend(lot)

    for panne in seules:
        groupes.append(
            Groupe(
                famille="entite",
                sujet=panne.entity_id,
                depuis=panne.depuis,
                entites=(panne.entity_id,),
                integration=panne.integration,
                nom=panne.nom or panne.entity_id,
            )
        )
    return sorted(groupes, key=lambda g: (g.depuis, g.sujet))


def duree_lisible(depuis: datetime, maintenant: datetime) -> str:
    """« depuis 40 minutes », « depuis 3 jours ». Sans horloge, pas de rapport.

    Approximatif à dessein : personne n'a besoin de « 2 jours 7 heures 12
    minutes » pour comprendre qu'un capteur est mort depuis un moment.
    """
    ecart = maintenant - depuis
    minutes = int(ecart.total_seconds() // 60)
    if minutes < 60:
        return f"depuis {max(minutes, 1)} minutes"
    heures = minutes // 60
    if heures < 24:
        return f"depuis {heures} heure{'s' if heures > 1 else ''}"
    jours = heures // 24
    return f"depuis {jours} jour{'s' if jours > 1 else ''}"


def resume_de_secours(groupe: Groupe, maintenant: datetime) -> tuple[str, str]:
    """Le titre et la raison d'une anomalie, **sans le modèle**.

    Le modèle améliore la formulation ; il n'est jamais nécessaire. Une clé API
    épuisée, une panne réseau, le plafond quotidien atteint : l'alerte sort
    quand même, en français correct. C'est ce qui distingue une gardienne d'une
    fonctionnalité qui dépend d'un service tiers.
    """
    quand = duree_lisible(groupe.depuis, maintenant)
    if groupe.famille == "integration" and groupe.entites:
        nom = groupe.nom or groupe.sujet
        return (
            f"{nom} ne répond plus.",
            f"{len(groupe.entites)} entités sont indisponibles {quand}.",
        )
    if groupe.famille == "integration":
        nom = groupe.nom or groupe.sujet
        return (f"{nom} n'arrive pas à démarrer.", f"En erreur {quand}.")
    if groupe.famille == "automatisation":
        return (f"{groupe.nom or groupe.sujet} est en erreur.", f"Signalé {quand}.")
    if groupe.famille == "loggia":
        return (
            "Une carte de Loggia pointe dans le vide.",
            f"{groupe.sujet} n'existe plus.",
        )
    return (f"{groupe.nom or groupe.sujet} ne répond plus.", f"Indisponible {quand}.")
