"""L0 — l'action a-t-elle vraiment eu lieu ? (§8, « jamais d'échec silencieux »)

Home Assistant accepte un appel de service sans rien promettre de son effet.
`light.turn_on` sur une ampoule débranchée est accepté, ne lève rien, et
n'allume rien. Jusqu'ici Luna annonçait « Fait » sur cette seule acceptation :
elle rapportait qu'elle avait **demandé**, pas qu'il s'était **passé** quelque
chose. C'est la définition même de l'échec silencieux que §8 interdit.

Ce module ne parle ni à Home Assistant ni au réseau : il reçoit deux photos de
l'état — avant, après — et rend un verdict. L'arbitre s'occupe de les prendre.
"""

from __future__ import annotations

from enum import StrEnum

#: Ce que Home Assistant répond quand il ne sait pas. `unavailable` : l'appareil
#: ne répond plus. `unknown` : il est là mais n'a pas encore dit son état. Les
#: deux se valent ici — dans les deux cas, Luna ne peut pas confirmer.
ETATS_MUETS = frozenset({"unavailable", "unknown"})

#: `domain.service` → l'état observable attendu ensuite. `None` = l'effet n'est
#: pas observable dans l'état de l'entité, donc rien à vérifier : `scene.turn_on`
#: laisse la scène à un horodatage, pas à « on », et `climate.set_temperature`
#: change une consigne qui ne vit pas dans `state`.
ETAT_ATTENDU: dict[str, str] = {
    "light.turn_on": "on",
    "light.turn_off": "off",
    "switch.turn_on": "on",
    "switch.turn_off": "off",
    "input_boolean.turn_on": "on",
    "input_boolean.turn_off": "off",
}

#: Ces services-là ne visent aucun état fixe : ils demandent un changement.
#: La vérification compare donc l'après à l'avant, pas à une constante.
BASCULES = frozenset({"light.toggle", "switch.toggle", "input_boolean.toggle"})


class Verdict(StrEnum):
    """Ce que l'arbitre a constaté après avoir agi."""

    #: L'état a changé comme demandé.
    FAIT = "fait"
    #: L'appel est passé, l'état n'a pas bougé. L'appareil est joignable et
    #: n'obéit pas — ampoule retirée, interrupteur physique, automatisation qui
    #: rétablit derrière.
    SANS_EFFET = "sans_effet"
    #: L'entité ne dit plus son état. On ne sait pas, et on le dit.
    INJOIGNABLE = "injoignable"
    #: Rien à vérifier : l'effet ne se lit pas dans l'état de l'entité.
    NON_VERIFIABLE = "non_verifiable"


def attendu_de(domaine: str, service: str) -> str | None:
    """L'état visé après ce service, ou `None` s'il n'y en a pas."""
    return ETAT_ATTENDU.get(f"{domaine}.{service}")


def est_une_bascule(domaine: str, service: str) -> bool:
    return f"{domaine}.{service}" in BASCULES


def verifiable(domaine: str, service: str) -> bool:
    """Vaut-il la peine de relire l'état après cet appel ?"""
    return attendu_de(domaine, service) is not None or est_une_bascule(domaine, service)


#: Les deux tournures que rend le jugement, au singulier et au pluriel.
MUET = ("ne répond plus", "ne répondent plus")
FIGE = ("n'a pas changé d'état", "n'ont pas changé d'état")


def juger(
    *,
    attendu: str | None,
    avant: dict[str, str],
    apres: dict[str, str],
) -> tuple[Verdict, str]:
    """Rend le verdict et sa raison, à partir de deux photos de l'état.

    `attendu` vaut `None` pour une bascule : on ne cherche alors pas un état
    précis, seulement un changement. `avant` peut être vide quand l'appel visait
    un état fixe — le relire ne servirait à rien.

    Le détail rendu est écrit pour être lu par le cerveau, pas par un humain :
    il nomme ce qui s'est passé, à charge pour le modèle de le dire joliment.
    """
    if not apres:
        return Verdict.INJOIGNABLE, "aucune entité n'a rendu son état"

    muettes = sorted(cle for cle, etat in apres.items() if etat in ETATS_MUETS)
    if len(muettes) == len(apres):
        return Verdict.INJOIGNABLE, _enumerer(muettes, *MUET)

    parlantes = {cle: etat for cle, etat in apres.items() if etat not in ETATS_MUETS}
    if attendu is not None:
        rebelles = sorted(cle for cle, etat in parlantes.items() if etat != attendu)
    else:
        # Une bascule ne vise pas un état : elle vise un changement.
        rebelles = sorted(
            cle for cle, etat in parlantes.items() if avant.get(cle) == etat
        )

    if not rebelles and not muettes:
        return Verdict.FAIT, ""

    morceaux = []
    if rebelles:
        morceaux.append(_enumerer(rebelles, *FIGE))
    if muettes:
        morceaux.append(_enumerer(muettes, *MUET))
    return Verdict.SANS_EFFET, " et ".join(morceaux)


def _enumerer(entites: list[str], singulier: str, pluriel: str) -> str:
    """« light.salon ne répond plus », « 3 entités ne répondent plus ».

    Au-delà de deux, on compte au lieu de citer : une liste de huit identifiants
    dans une réponse lue à voix haute est inaudible (§7).
    """
    if not entites:
        return ""
    if len(entites) == 1:
        return f"{entites[0]} {singulier}"
    if len(entites) == 2:
        return f"{entites[0]} et {entites[1]} {pluriel}"
    return f"{len(entites)} entités {pluriel}"
