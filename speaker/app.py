"""Service de reconnaissance de locuteur — extraction d'empreinte vocale.

Rôle unique : PCM (16 bits mono) → empreinte vocale (vecteur). Rien d'autre.
Toute la logique d'identité, de profils et de droits vit dans sentinel-core (là
où sont la base et les garde-fous de sécurité) : ce service est apatride et
« bête ». Empreinte via Resemblyzer (d-vector 256, CPU) — léger et local.

Protocole (appelé par core) :
  POST /embed?rate=16000&width=2&channels=1
       corps = PCM brut s16le
       → 200 {"embedding": [floats], "dim": 256}
       → 422 si l'audio est trop court / silencieux pour une empreinte fiable
  GET  /health → {"status": "ok"}
"""

from __future__ import annotations

import logging

import numpy as np
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from resemblyzer import VoiceEncoder, preprocess_wav

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("sentinel.speaker")

# Chargé une fois au démarrage (CPU). ~ quelques secondes, puis quasi instantané.
_encoder = VoiceEncoder("cpu")

app = FastAPI(title="Sentinel Speaker", version="1.0")

# En deçà, l'empreinte n'est pas fiable (Resemblyzer conseille ~1,6 s ; on tolère
# un peu moins mais on refuse le quasi-silence).
_MIN_SECONDS = 0.6


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "sentinel-speaker", "dim": 256}


@app.post("/embed")
async def embed(request: Request) -> Response:
    rate = int(request.query_params.get("rate", 16000))
    width = int(request.query_params.get("width", 2))
    channels = int(request.query_params.get("channels", 1))
    pcm = await request.body()
    if not pcm or width != 2:
        return JSONResponse({"detail": "PCM 16 bits attendu."}, status_code=422)

    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:  # démixage vers mono
        usable = (samples.size // channels) * channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)

    try:
        # Rééchantillonne vers 16 kHz, coupe les silences et normalise le volume.
        wav = preprocess_wav(samples, source_sr=rate)
    except Exception as exc:  # audio inexploitable
        log.warning("Prétraitement impossible : %s", exc)
        return JSONResponse({"detail": "Audio inexploitable."}, status_code=422)

    if wav.size < int(_MIN_SECONDS * 16000):
        return JSONResponse({"detail": "Audio trop court ou silencieux."}, status_code=422)

    embedding = _encoder.embed_utterance(wav)
    return JSONResponse({"embedding": [float(x) for x in embedding], "dim": int(embedding.size)})
