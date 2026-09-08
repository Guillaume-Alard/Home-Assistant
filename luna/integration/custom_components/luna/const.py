"""Constantes de l'intégration Luna."""

from __future__ import annotations

DOMAINE = "luna"

CONF_HOTE = "hote"
CONF_PORT = "port"
CONF_SECRET = "secret"

#: Nom d'hôte de l'add-on sur le réseau des add-ons du Supervisor (H7).
#: Le formulaire de configuration permet de le changer si Nova le résout
#: autrement — c'est le repli prévu dès le départ.
DEFAUT_HOTE = "local-luna"
DEFAUT_PORT = 8099

EN_TETE_SECRET = "X-Luna-Secret"

#: Reconnexion : 1 s, puis doublement jusqu'à 30 s.
BACKOFF_MIN = 1.0
BACKOFF_MAX = 30.0
DELAI_REQUETE = 30.0

SIGNAL_STATUT = f"{DOMAINE}_statut"
