# Sentinel core — construit depuis la racine du dépôt :
#   docker compose build  (le contexte est ".", voir docker-compose.yml)
FROM python:3.12-slim

# openssl sert à générer le certificat auto-signé au premier démarrage
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/sentinel

COPY core/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY core/app ./app
COPY ui ./ui
COPY config ./config
COPY core/entrypoint.sh core/healthcheck.py ./

# Note durcissement : le conteneur tourne en root pour pouvoir écrire dans le
# volume ./data monté par compose (créé root sur Unraid). Il n'expose qu'un port
# LAN et ne monte aucun socket sensible ; passage en utilisateur dédié prévu
# lors de la phase de durcissement (voir docs/ARCHITECTURE.md).
RUN chmod +x entrypoint.sh && mkdir -p /data

EXPOSE 8443
ENTRYPOINT ["./entrypoint.sh"]
