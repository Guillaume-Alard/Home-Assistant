# Add-on Luna — le cerveau.
#
# Pas de base image Home Assistant, pas de bashio, pas de s6 : les options sont
# lues directement dans /data/options.json par `kernel/settings.py`, et l'arrêt
# est géré par un gestionnaire de signal Python. Une pièce mobile de moins.
#
# ⚠️ **Debian, pas Alpine, et ce n'est pas négociable.** `onnxruntime` ne publie
# aucune roue `musllinux` : sur Alpine, pip tenterait de compiler un gros projet
# C++ depuis les sources — des heures sur le N95 de Nova, si tant est que ça
# aboutisse. Ses roues sont en `manylinux_2_28`, ce qui demande une glibc ≥ 2.28 ;
# bookworm fournit la 2.36. Toutes les autres dépendances s'installeraient très
# bien sur Alpine, mais une seule suffit à trancher.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# `libgomp1` est la bibliothèque OpenMP contre laquelle onnxruntime est lié. Une
# roue manylinux ne l'embarque pas : elle la suppose présente sur le système, et
# l'image `slim` ne l'a pas. Sans elle, `import onnxruntime` échoue au
# chargement — et comme cet import est paresseux, Luna démarrerait normalement
# en annonçant simplement que la reconnaissance de voix est indisponible. Une
# panne qui se déguise en fonctionnalité éteinte est le pire des deux mondes.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/*

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
