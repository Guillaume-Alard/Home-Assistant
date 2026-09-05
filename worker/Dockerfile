# Sentinel worker — Claude Code headless dans un conteneur ISOLÉ.
# Il ne connaît ni Nova, ni le socket Docker, ni les jetons de sentinel-core :
# uniquement son espace de travail jetable, l'API Anthropic et GitHub.
FROM node:22-bookworm-slim

ENV PIP_ROOT_USER_ACTION=ignore \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git python3 python3-pip ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @anthropic-ai/claude-code \
 && pip3 install --break-system-packages --no-cache-dir \
      "fastapi>=0.115,<1" "uvicorn>=0.30,<1"

# `claude --dangerously-skip-permissions` (nécessaire en headless) refuse de
# tourner en root : on utilise l'utilisateur `node` (uid 1000) de l'image.
RUN mkdir -p /workspace /opt/worker && chown -R node:node /workspace /opt/worker

COPY worker/server.py worker/streamlog.py /opt/worker/

USER node
WORKDIR /opt/worker
EXPOSE 8400
CMD ["python3", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8400"]
