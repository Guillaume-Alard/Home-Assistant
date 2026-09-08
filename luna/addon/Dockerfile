# Add-on Luna — le cerveau.
#
# Pas de base image Home Assistant, pas de bashio, pas de s6 : les options sont
# lues directement dans /data/options.json par `kernel/settings.py`, et l'arrêt
# est géré par un gestionnaire de signal Python. Une pièce mobile de moins.
FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Les dépendances d'abord : sur le N95 de Nova, une reconstruction après un
# changement de code ne doit pas réinstaller anthropic et aiohttp.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY luna ./luna

# /data est le seul chemin persistant et sauvegardé par HAOS : la base y vit.
VOLUME ["/data"]
EXPOSE 8099

CMD ["python", "-m", "luna.interfaces"]
