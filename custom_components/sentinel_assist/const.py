"""Constantes du connecteur Sentinel pour Home Assistant."""

DOMAIN = "sentinel_assist"

CONF_BASE_URL = "base_url"
CONF_TOKEN = "token"

DEFAULT_BASE_URL = "https://192.168.0.212:8443"
REQUEST_TIMEOUT = 60  # secondes — le cerveau peut réfléchir + utiliser des outils

# Carte Lovelace « Luna » (le visage dans Home Assistant) : servie depuis le
# dossier www/ de l'intégration et enregistrée d'office comme module frontend,
# pour apparaître dans le sélecteur de cartes sans configuration manuelle.
CARD_URL_BASE = "/sentinel_assist_www"
CARD_FILENAME = "luna-card.js"
CARD_VERSION = "5"  # bump = purge du cache navigateur (?v=…)
