#!/bin/sh
# Démarrage de sentinel-core : certificat TLS puis serveur.
set -e

DATA_DIR="${SENTINEL_DATA_DIR:-/data}"
CERT_DIR="$DATA_DIR/certs"
# --http h11 : parseur HTTP en Python pur (implémentation de référence). Le
# parseur par défaut de uvicorn[standard], httptools, rejette la requête
# d'upgrade WebSocket au niveau HTTP (« Invalid HTTP request received ») avec
# certaines combinaisons récentes uvicorn/httptools : le HTTP simple passe mais
# /ws ne se connecte jamais. h11 gère l'upgrade de façon fiable et nous met à
# l'abri de cette classe de régressions liée aux versions de httptools.
ARGS="--host 0.0.0.0 --port 8443 --http h11"

# Toutes les valeurs « vraies » usuelles activent le TLS (on/true/yes/1),
# même interprétation que healthcheck.py — ne pas diverger.
TLS=$(printf '%s' "${SENTINEL_TLS:-on}" | tr '[:upper:]' '[:lower:]')
case "$TLS" in on|true|yes|1) TLS=on ;; *) TLS=off ;; esac

if [ "$TLS" = "on" ]; then
  if [ ! -f "$CERT_DIR/sentinel.crt" ] || [ ! -f "$CERT_DIR/sentinel.key" ]; then
    echo "[sentinel] Aucun certificat trouvé — génération d'un certificat auto-signé (10 ans) dans $CERT_DIR"
    echo "[sentinel] Pour un certificat de confiance (PWA iPhone, avertissement supprimé), voir le README (mkcert)."
    mkdir -p "$CERT_DIR"
    # SAN de base + adresses fournies par l'utilisateur (SENTINEL_TLS_SANS).
    # IMPORTANT : ajoute-y l'IP LAN réelle (ex. IP:192.168.0.212), sinon les
    # clients stricts (iOS) refusent le WebSocket sécurisé — l'adresse ne
    # correspond pas au certificat.
    SAN="DNS:sentinel,DNS:sentinel.local,DNS:nebula,DNS:nebula.local,DNS:localhost,IP:127.0.0.1"
    if [ -n "${SENTINEL_TLS_SANS:-}" ]; then
      SAN="$SAN,$SENTINEL_TLS_SANS"
      echo "[sentinel] SAN supplémentaires : $SENTINEL_TLS_SANS"
    fi
    openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
      -keyout "$CERT_DIR/sentinel.key" -out "$CERT_DIR/sentinel.crt" \
      -subj "/CN=sentinel" \
      -addext "subjectAltName=$SAN" \
      >/dev/null 2>&1
  fi
  ARGS="$ARGS --ssl-certfile $CERT_DIR/sentinel.crt --ssl-keyfile $CERT_DIR/sentinel.key"
else
  echo "[sentinel] SENTINEL_TLS=off — HTTP simple. Le micro du navigateur ne fonctionnera"
  echo "[sentinel] qu'à travers un reverse proxy HTTPS (contexte sécurisé obligatoire)."
fi

exec python -m uvicorn app.main:app $ARGS
