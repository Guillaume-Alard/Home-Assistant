"""L0 — profils et scopes (§3 F3, §6 du cahier des charges).

Trois niveaux d'accès. `critique` n'apparaît nulle part : il est hors périmètre
v1 par §3, et un scope qui n'existe pas ne peut pas être accordé par erreur.
"""

from __future__ import annotations

from .autonomy import Niveau

PROFILS = ("guillaume", "clara", "liam", "guest", "unknown")

#: Les deux seuls profils par défaut qui n'empruntent l'identité de personne.
#: `conversation_courante()` reprend le fil du profil, et le contexte lui livre
#: ses faits : mettre ici quelqu'un de la maison donne son fil, ses faits et son
#: scope à toute personne non identifiée — l'iPad du couloir, un satellite
#: vocal. C'est un réglage, donc une erreur possible, donc un avertissement au
#: démarrage (§8 : « Jamais d'échec silencieux »).
SANS_IDENTITE = frozenset({"guest", "unknown"})

#: La maison agissant d'elle-même (P4). Ce n'est le profil de personne : il
#: n'apparaît donc pas dans `PROFILS`, ne peut pas être choisi comme profil par
#: défaut, et n'est jamais résolu à partir d'un utilisateur Home Assistant.
MAISON = "maison"

CONFORT = "confort"
PERSONNEL = "personnel"

SCOPES: dict[str, frozenset[str]] = {
    "guillaume": frozenset({CONFORT, PERSONNEL}),
    "clara": frozenset({CONFORT, PERSONNEL}),
    "liam": frozenset({CONFORT}),
    "guest": frozenset({CONFORT}),
    # Personne d'identifié : Luna répond et lit, mais ne déclenche rien.
    "unknown": frozenset(),
    # La veille, quand elle agit sans que personne le lui ait demandé : les
    # droits d'un invité, jamais plus. Annoncer une alerte est du confort ;
    # tout ce qui dépasse le confort passera par une proposition, donc par un
    # humain — y compris déclenché par la maison elle-même.
    MAISON: frozenset({CONFORT}),
}


def scopes_de(profil: str) -> frozenset[str]:
    return SCOPES.get(profil, frozenset())


def peut_agir_seule(profil: str, niveau: Niveau) -> bool:
    """§9.1, niveau 2 : « Libre si le profil actif l'autorise, sinon proposition ».

    Les niveaux 0 et 1 sont libres pour tout le monde. Les niveaux 3 et 4
    exigent une validation explicite quel que soit le profil — y compris
    Guillaume. Le niveau 5 n'arrive jamais ici : il est refusé plus tôt.
    """
    if niveau <= Niveau.LIRE:
        return True
    if niveau == Niveau.CONFORT:
        return CONFORT in scopes_de(profil)
    return False


def peut_valider(niveau: Niveau, *, est_admin: bool) -> bool:
    """Qui a le droit d'accepter une proposition.

    Le niveau 4 touche à la configuration de Home Assistant : réservé aux
    administrateurs HA. Le niveau 3 est ouvert à tout utilisateur authentifié.
    """
    if niveau >= Niveau.CONFIGURATION:
        return est_admin
    return True
