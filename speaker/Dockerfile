# Service de reconnaissance de locuteur (Phase 2) — Resemblyzer sur CPU.
# torch est installé depuis l'index CPU de PyTorch pour éviter la variante CUDA
# (bien plus lourde) : l'image reste de l'ordre du Go, pas de la dizaine de Go.
FROM python:3.11-slim

WORKDIR /app

# libsndfile + ffmpeg : lecture/rééchantillonnage audio (librosa) ;
# webrtcvad (dépendance de resemblyzer) se compile → build-essential.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# torch CPU d'abord (sinon resemblyzer tire la variante CUDA par défaut).
RUN pip install --no-cache-dir torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV SPEAKER_PORT=10500
EXPOSE 10500

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10500"]
