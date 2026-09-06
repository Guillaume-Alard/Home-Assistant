"""Constantes du connecteur Sentinel pour Home Assistant."""

DOMAIN = "sentinel_assist"

CONF_BASE_URL = "base_url"
CONF_TOKEN = "token"

DEFAULT_BASE_URL = "https://192.168.0.251:8443"
REQUEST_TIMEOUT = 60  # secondes — le cerveau peut réfléchir + utiliser des outils
