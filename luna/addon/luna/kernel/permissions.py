"""L0 — profils et scopes (§3 F3, §6 du cahier des charges).

Trois niveaux d'accès. `critique` n'apparaît nulle part : il est hors périmètre
v1 par §3, et un scope qui n'existe pas ne peut pas être accordé par erreur.
"""

from __future__ import annotations

from .autonomy import Niveau

PROFILS = ("guillaume", "clara", "liam", "guest", "unknown")

CONFORT = "confort"
PERSONNEL = "personnel"

SCOPES: dict[str, frozenset[str]] = {
    "guillaume": frozenset({CONFORT, PERSONNEL}),
    "clara": frozenset({CONFORT, PERSONNEL}),
    "liam": frozenset({CONFORT}),
    "guest": frozenset({CONFORT}),
    # Personne d'identifié : Luna répond et lit, mais ne déclenche rien.
    "unknown": frozenset(),
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
