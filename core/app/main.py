"""Serveur Sentinel : API HTTP(S), WebSocket temps réel et UI statique (PWA).

Protocole WebSocket (résumé — détail dans docs/ARCHITECTURE.md) :

  Client → serveur (JSON) : chat, audio_start, audio_end, audio_cancel, cancel, ping
  Client → serveur (binaire) : PCM 16 bits mono (entre audio_start et audio_end)
  Serveur → clients (JSON) : hello, status, message, assistant_start,
                             assistant_delta, assistant_end, speak_start,
                             speak_end, notice, error, pong
  Serveur → client d'origine (binaire) : PCM de la voix de Sentinel

Le fil de conversation est unique et partagé : chaque événement de conversation
est diffusé à tous les clients connectés ; seul l'audio de la réponse est envoyé
à l'appareil qui a parlé.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import __version__
from .brain.llm import Brain, LLMUnavailable
from .brain.speech_text import SentenceChunker, markdown_to_speech
from .config import Settings, find_ui_dir
from .store import Store
from .voice.session import CaptureSession
from .voice.wyoming import PiperTTS, VoiceServiceError, WhisperSTT

log = logging.getLogger("sentinel")


class Client:
    """Un appareil connecté (onglet de navigateur, téléphone…)."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.id = uuid.uuid4().hex[:8]
        self.capture = CaptureSession()


class Hub:
    """Registre des clients connectés + diffusion des événements."""

    def __init__(self) -> None:
        self._clients: dict[WebSocket, Client] = {}

    def register(self, ws: WebSocket) -> Client:
        client = Client(ws)
        self._clients[ws] = client
        return client

    def unregister(self, ws: WebSocket) -> None:
        self._clients.pop(ws, None)

    @property
    def count(self) -> int:
        return len(self._clients)

    async def send(self, client: Client, payload: dict) -> None:
        with contextlib.suppress(Exception):  # client parti entre-temps
            await client.ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def send_bytes(self, client: Client, data: bytes) -> None:
        with contextlib.suppress(Exception):
            await client.ws.send_bytes(data)

    async def broadcast(self, payload: dict) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        for client in list(self._clients.values()):
            with contextlib.suppress(Exception):
                await client.ws.send_text(text)


class Sentinel:
    """État applicatif : un seul tour de parole à la fois, interruptible."""

    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.hub = Hub()
        self.brain = Brain(settings)
        self.stt = WhisperSTT(
            settings.whisper_host, settings.whisper_port, settings.wyoming_timeout_seconds
        )
        self.tts = PiperTTS(
            settings.piper_host, settings.piper_port, settings.wyoming_timeout_seconds
        )
        self.state = "idle"
        self._turn_task: asyncio.Task | None = None
        self._turn_lock = asyncio.Lock()

    # ── États diffusés ────────────────────────────────────────────────────

    async def set_state(self, state: str) -> None:
        self.state = state
        await self.hub.broadcast({"type": "status", "state": state})

    # ── Gestion du tour de parole (un seul à la fois) ────────────────────

    async def start_turn(self, coro) -> None:
        """Lance un tour ; s'il y en a un en cours, il est interrompu (barge-in)."""
        async with self._turn_lock:
            await self._cancel_locked()
            self._turn_task = asyncio.create_task(coro)

    async def cancel_turn(self) -> None:
        async with self._turn_lock:
            await self._cancel_locked()

    async def _cancel_locked(self) -> None:
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._turn_task
        self._turn_task = None

    # ── Tours de parole ──────────────────────────────────────────────────

    async def run_voice_turn(self, origin: Client, pcm: bytes, rate: int) -> None:
        """Un tour initié à la voix : transcription puis réponse parlée."""
        await self.set_state("transcribing")
        try:
            text = await self.stt.transcribe(pcm, rate=rate)
        except VoiceServiceError as exc:
            await self.hub.send(origin, {"type": "notice", "text": str(exc)})
            await self.set_state("idle")
            return
        except asyncio.CancelledError:
            await self.set_state("idle")
            return

        if not text:
            await self.hub.send(origin, {"type": "notice", "text": "Je n'ai rien entendu."})
            await self.set_state("idle")
            return

        await self.run_reply_turn(origin, text, source="voice", speak=True)

    async def run_reply_turn(
        self, origin: Client, text: str, source: str, speak: bool
    ) -> None:
        """Un tour complet : message utilisateur → réponse LLM en streaming (+ TTS)."""
        assistant_id = uuid.uuid4().hex[:12]
        parts: list[str] = []
        cancelled = False
        error_text: str | None = None
        speaker: asyncio.Task | None = None
        tts_queue: asyncio.Queue[str | None] = asyncio.Queue()

        try:
            user_msg = await self.store.add_message("user", text, source)
            await self.hub.broadcast({"type": "message", "message": user_msg})
            await self.set_state("thinking")

            history = _build_history(
                await self.store.recent_messages(self.settings.history_window)
            )
            await self.hub.broadcast({"type": "assistant_start", "id": assistant_id})

            if speak:
                speaker = asyncio.create_task(self._speak_worker(origin, tts_queue))

            chunker = SentenceChunker()
            async for delta in self.brain.stream_reply(history):
                parts.append(delta)
                await self.hub.broadcast(
                    {"type": "assistant_delta", "id": assistant_id, "text": delta}
                )
                if speaker:
                    for sentence in chunker.feed(delta):
                        tts_queue.put_nowait(sentence)

            if speaker:
                rest = chunker.flush()
                if rest:
                    tts_queue.put_nowait(rest)
                tts_queue.put_nowait(None)  # fin de flux
                await speaker  # laisser Sentinel finir de parler
                speaker = None

        except asyncio.CancelledError:
            # Interruption volontaire (nouveau message ou bouton) : on garde le partiel.
            cancelled = True
        except LLMUnavailable as exc:
            error_text = str(exc)
        finally:
            if speaker and not speaker.done():
                speaker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await speaker

            full = "".join(parts).strip()
            message = None
            if full:
                with contextlib.suppress(Exception):  # arrêt serveur pendant le tour
                    message = await self.store.add_message("assistant", full, "text")
            await self.hub.broadcast(
                {
                    "type": "assistant_end",
                    "id": assistant_id,
                    "message": message,
                    "cancelled": cancelled,
                }
            )
            if error_text:
                await self.hub.broadcast({"type": "error", "text": error_text})
            await self.set_state("idle")

    async def _speak_worker(
        self, origin: Client, queue: asyncio.Queue[str | None]
    ) -> None:
        """Consomme les phrases au fil de l'eau et streame l'audio Piper au client d'origine."""
        started = False
        try:
            while True:
                sentence = await queue.get()
                if sentence is None:
                    break
                speakable = markdown_to_speech(sentence)
                if not speakable:
                    continue
                try:
                    async for rate, chunk in self.tts.synthesize(speakable):
                        if not started:
                            started = True
                            await self.set_state("speaking")
                            await self.hub.send(
                                origin, {"type": "speak_start", "rate": rate}
                            )
                        await self.hub.send_bytes(origin, chunk)
                except VoiceServiceError as exc:
                    await self.hub.send(origin, {"type": "notice", "text": str(exc)})
                    break  # inutile d'essayer les phrases suivantes
        finally:
            if started:
                await self.hub.send(origin, {"type": "speak_end"})


def _build_history(records: list[dict]) -> list[dict]:
    """Convertit l'historique stocké au format API.

    Contraintes de l'API Messages : premier message de rôle `user`, aucun contenu
    vide, et jamais un `assistant` en dernier (traité comme un prefill → 400 sur
    les modèles récents). Le flux normal les garantit déjà ; on les impose ici
    pour qu'aucune donnée héritée ou imprévue ne casse un tour.
    """
    history = [
        {"role": r["role"], "content": r["content"]}
        for r in records
        if (r.get("content") or "").strip()
    ]
    while history and history[0]["role"] != "user":
        history.pop(0)
    while history and history[-1]["role"] == "assistant":
        log.warning("Historique terminé par un message assistant — retiré avant l'appel API")
        history.pop()
    return history


# ── Application FastAPI ──────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    store = Store(settings.data_dir / "sentinel.db")
    await store.open()
    app.state.sentinel = Sentinel(settings, store)
    log.info(
        "Sentinel %s démarré — modèle %s, effort %s, UI %s",
        __version__, settings.model, settings.effort, settings.ui_dir,
    )
    if not settings.anthropic_api_key:
        log.warning(
            "ANTHROPIC_API_KEY absente : la voix et le chat répondront par une erreur."
        )
    yield
    await app.state.sentinel.cancel_turn()
    await store.close()


app = FastAPI(title="Sentinel", version=__version__, lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "sentinel", "version": __version__}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    sentinel: Sentinel = ws.app.state.sentinel
    await ws.accept()
    client = sentinel.hub.register(ws)
    log.info("Client %s connecté (%d en ligne)", client.id, sentinel.hub.count)

    await sentinel.hub.send(
        client,
        {
            "type": "hello",
            "version": __version__,
            "state": sentinel.state,
            "history": await sentinel.store.recent_messages(50),
        },
    )

    try:
        while True:
            raw = await ws.receive()
            if raw.get("type") == "websocket.disconnect":
                break

            data = raw.get("bytes")
            if data is not None:
                await _on_audio_chunk(sentinel, client, data)
                continue

            text = raw.get("text")
            if not text:
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            await _on_message(sentinel, client, msg)
    except WebSocketDisconnect:
        pass
    finally:
        sentinel.hub.unregister(ws)
        log.info("Client %s déconnecté (%d en ligne)", client.id, sentinel.hub.count)


async def _on_audio_chunk(sentinel: Sentinel, client: Client, data: bytes) -> None:
    if not client.capture.active:
        return  # audio hors capture : ignoré
    if not client.capture.add(data):
        # Durée maximale atteinte : on transcrit ce qui a été capté
        pcm, rate = client.capture.finish()
        await sentinel.hub.send(
            client, {"type": "notice", "text": "Durée maximale atteinte, je traite ce que j'ai entendu."}
        )
        await sentinel.start_turn(sentinel.run_voice_turn(client, pcm, rate))


async def _on_message(sentinel: Sentinel, client: Client, msg: dict) -> None:
    mtype = msg.get("type")

    if mtype == "chat":
        text = str(msg.get("text") or "").strip()
        if text:
            speak = bool(msg.get("speak", False))
            await sentinel.start_turn(
                sentinel.run_reply_turn(client, text, source="text", speak=speak)
            )

    elif mtype == "audio_start":
        # Parler interrompt la réponse en cours (barge-in)
        await sentinel.cancel_turn()
        client.capture.start(rate=int(msg.get("rate", 16000)))
        await sentinel.set_state("listening")

    elif mtype == "audio_end":
        if not client.capture.active:
            return
        pcm, rate = client.capture.finish()
        if not pcm:
            await sentinel.hub.send(client, {"type": "notice", "text": "Je n'ai rien entendu."})
            await sentinel.set_state("idle")
        else:
            await sentinel.start_turn(sentinel.run_voice_turn(client, pcm, rate))

    elif mtype == "audio_cancel":
        client.capture.reset()
        await sentinel.set_state("idle")

    elif mtype == "cancel":
        client.capture.reset()
        await sentinel.cancel_turn()
        await sentinel.set_state("idle")

    elif mtype == "ping":
        await sentinel.hub.send(client, {"type": "pong"})


# L'UI statique en dernier : les routes déclarées avant restent prioritaires.
app.mount("/", StaticFiles(directory=find_ui_dir(), html=True), name="ui")
