"""Clients Wyoming pour les services vocaux (whisper = STT, piper = TTS).

Le protocole Wyoming est celui de l'écosystème Assist de Home Assistant :
les mêmes conteneurs pourront être partagés avec Nova et des satellites.
Une connexion TCP est ouverte par requête — les services sont sur le réseau
interne Docker, le coût est négligeable et cela évite tout état partagé.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize


class VoiceServiceError(RuntimeError):
    """Erreur d'un service vocal — le message (en français) est montré à l'utilisateur."""


_CHUNK_BYTES = 8192  # ~256 ms de PCM 16 kHz mono 16 bits par événement


class _WyomingService:
    def __init__(self, host: str, port: int, label: str, timeout: int = 120):
        self._host = host
        self._port = port
        self._label = label
        self._timeout = timeout

    async def _connect(self) -> AsyncTcpClient:
        client = AsyncTcpClient(self._host, self._port)
        try:
            await client.connect()
        except OSError as exc:
            raise VoiceServiceError(
                f"{self._label} est injoignable ({self._host}:{self._port}) — "
                "vérifie que le conteneur est démarré."
            ) from exc
        return client


class WhisperSTT(_WyomingService):
    def __init__(self, host: str, port: int, timeout: int = 120, language: str = "fr"):
        super().__init__(host, port, "Le service de transcription (whisper)", timeout)
        self._language = language

    async def transcribe(
        self, pcm: bytes, rate: int = 16000, width: int = 2, channels: int = 1
    ) -> str:
        client = await self._connect()
        try:
            async with asyncio.timeout(self._timeout):
                await client.write_event(Transcribe(language=self._language).event())
                await client.write_event(
                    AudioStart(rate=rate, width=width, channels=channels).event()
                )
                for i in range(0, len(pcm), _CHUNK_BYTES):
                    await client.write_event(
                        AudioChunk(
                            audio=pcm[i : i + _CHUNK_BYTES],
                            rate=rate,
                            width=width,
                            channels=channels,
                        ).event()
                    )
                await client.write_event(AudioStop().event())

                while True:
                    event = await client.read_event()
                    if event is None:
                        raise VoiceServiceError(f"{self._label} a fermé la connexion.")
                    if Transcript.is_type(event.type):
                        return (Transcript.from_event(event).text or "").strip()
        except TimeoutError as exc:
            raise VoiceServiceError(f"{self._label} n'a pas répondu à temps.") from exc
        finally:
            await client.disconnect()


class PiperTTS(_WyomingService):
    def __init__(self, host: str, port: int, timeout: int = 120):
        super().__init__(host, port, "Le service de synthèse vocale (piper)", timeout)

    async def synthesize(self, text: str) -> AsyncIterator[tuple[int, bytes]]:
        """Synthétise `text` et produit des couples (fréquence, PCM 16 bits mono)."""
        client = await self._connect()
        try:
            async with asyncio.timeout(self._timeout):
                await client.write_event(Synthesize(text=text).event())
                while True:
                    event = await client.read_event()
                    if event is None:
                        raise VoiceServiceError(f"{self._label} a fermé la connexion.")
                    if AudioChunk.is_type(event.type):
                        chunk = AudioChunk.from_event(event)
                        yield chunk.rate, chunk.audio
                    elif AudioStop.is_type(event.type):
                        return
        except TimeoutError as exc:
            raise VoiceServiceError(f"{self._label} n'a pas répondu à temps.") from exc
        finally:
            await client.disconnect()
