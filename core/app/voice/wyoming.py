"""Clients Wyoming pour les services vocaux (whisper = STT, piper = TTS).

Le protocole Wyoming est celui de l'écosystème Assist de Home Assistant :
les mêmes conteneurs pourront être partagés avec Nova et des satellites.
Une connexion TCP est ouverte par requête — les services sont sur le réseau
interne Docker, le coût est négligeable et cela évite tout état partagé.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize
from wyoming.wake import Detection

log = logging.getLogger("sentinel.voice")


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
        except OSError as exc:  # connexion coupée en plein échange (redémarrage du conteneur…)
            raise VoiceServiceError(f"{self._label} — connexion interrompue.") from exc
        finally:
            await client.disconnect()


class WakeWordDetector(_WyomingService):
    """Détection du mot d'éveil (openWakeWord) — flux continu, pas requête/réponse.

    `open()` établit une session : l'appelant y pousse le PCM du micro au fil de
    l'eau ; à la détection, le callback est invoqué UNE fois puis la session ne
    sert plus (l'appareil bascule en écoute normale et ré-ouvrira une session).
    """

    def __init__(self, host: str, port: int, timeout: int = 120):
        super().__init__(host, port, "Le service du mot d'éveil (openwakeword)", timeout)

    async def open(
        self, rate: int, on_detection: Callable[[str], Awaitable[None]]
    ) -> "WakeStream":
        client = await self._connect()
        try:
            await client.write_event(AudioStart(rate=rate, width=2, channels=1).event())
        except OSError as exc:
            await client.disconnect()
            raise VoiceServiceError(f"{self._label} — connexion interrompue.") from exc
        return WakeStream(client, self._label, on_detection)


class WakeStream:
    def __init__(
        self, client: AsyncTcpClient, label: str, on_detection: Callable[[str], Awaitable[None]]
    ):
        self._client = client
        self._label = label
        self._on_detection = on_detection
        self._closed = False
        self._reader = asyncio.create_task(self._read_loop(), name="wake-reader")

    @property
    def closed(self) -> bool:
        """Vrai dès que la session ne peut plus servir (détection, coupure ou arrêt)."""
        return self._closed

    async def _read_loop(self) -> None:
        # Une détection (ou une coupure) met fin à la session : la boucle sort
        # d'elle-même et ferme sa connexion dans le `finally`. On ne s'auto-annule
        # JAMAIS — le callback tourne dans ce task, l'annuler couperait son envoi.
        try:
            while True:
                event = await self._client.read_event()
                if event is None:
                    return
                if Detection.is_type(event.type):
                    name = Detection.from_event(event).name or ""
                    try:
                        await self._on_detection(name)
                    except Exception:
                        log.exception("Callback de détection du mot d'éveil en échec")
                    return
        except asyncio.CancelledError:
            raise
        except OSError:
            return  # connexion coupée : send() le signalera aussi à l'appelant
        finally:
            self._closed = True
            with contextlib.suppress(Exception):
                await self._client.disconnect()

    async def send(self, pcm: bytes, rate: int) -> None:
        if self._closed:
            return
        try:
            await self._client.write_event(
                AudioChunk(audio=pcm, rate=rate, width=2, channels=1).event()
            )
        except OSError as exc:
            await self.close()
            raise VoiceServiceError(f"{self._label} — connexion interrompue.") from exc

    async def close(self) -> None:
        """Arrêt externe (wake_stop, déconnexion, nouvelle session). Sûr même si
        le lecteur s'est déjà terminé tout seul après une détection."""
        self._closed = True
        if asyncio.current_task() is not self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        with contextlib.suppress(Exception):
            await self._client.disconnect()


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
        except OSError as exc:
            raise VoiceServiceError(f"{self._label} — connexion interrompue.") from exc
        finally:
            await client.disconnect()
