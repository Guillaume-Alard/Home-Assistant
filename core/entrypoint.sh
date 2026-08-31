#!/bin/sh
# Démarrage de sentinel-core : certificat TLS puis serveur.
set -e

DATA_DIR="${SENTINEL_DATA_DIR:-/data}"
CERT_DIR="$DATA_DIR/certs"
ARGS="--host 0.0.0.0 --port 8443"

# Toutes les valeurs « vraies » usuelles activent le TLS (on/true/yes/1),
# même interprétation que healthcheck.py — ne pas diverger.
TLS=$(printf '%s' "${SENTINEL_TLS:-on}" | tr '[:upper:]' '[:lower:]')
case "$TLS" in on|true|yes|1) TLS=on ;; *) TLS=off ;; esac

if [ "$TLS" = "on" ]; then
  if [ ! -f "$CERT_DIR/sentinel.crt" ] || [ ! -f "$CERT_DIR/sentinel.key" ]; then
    echo "[sentinel] Aucun certificat trouvé — génération d'un certificat auto-signé (10 ans) dans $CERT_DIR"
    echo "[sentinel] Pour un certificat de confiance (PWA installable sans avertissement), voir le README (mkcert)."
    mkdir -p "$CERT_DIR"
    openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
      -keyout "$CERT_DIR/sentinel.key" -out "$CERT_DIR/sentinel.crt" \
      -subj "/CN=sentinel" \
      -addext "subjectAltName=DNS:sentinel,DNS:sentinel.local,DNS:nebula,DNS:nebula.local,DNS:localhost,IP:127.0.0.1" \
      >/dev/null 2>&1
  fi
  ARGS="$ARGS --ssl-certfile $CERT_DIR/sentinel.crt --ssl-keyfile $CERT_DIR/sentinel.key"
else
  echo "[sentinel] SENTINEL_TLS=off — HTTP simple. Le micro du navigateur ne fonctionnera"
  echo "[sentinel] qu'à travers un reverse proxy HTTPS (contexte sécurisé obligatoire)."
fi

exec python -m uvicorn app.main:app $ARGS
