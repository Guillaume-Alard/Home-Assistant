"""Serveur Sentinel : API HTTP(S), WebSocket temps réel et UI statique (PWA).

Protocole WebSocket (résumé — détail dans docs/ARCHITECTURE.md) :

  Client → serveur (JSON) : chat, audio_start, audio_end, audio_cancel, cancel,
                            proposal_decision, ping — et les requêtes de lecture
                            des panneaux : dev_tasks, dev_log, dev_diff, sante,
                            historique
  Client → serveur (binaire) : PCM 16 bits mono (entre audio_start et audio_end)
  Serveur → clients (JSON) : hello, status, message, assistant_start,
                             assistant_delta, assistant_end, speak_start,
                             speak_end, notice, error, alert, ha_status,
                             activity, proposal_new, proposal_update,
                             dev_status, pong — et les réponses de panneaux
                             (dev_tasks, dev_log, dev_diff, sante, historique,
                             au seul client demandeur)
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
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import __version__
from .actions.engine import ActionEngine
from .actions.executors import build_registry
from .brain.intents import LocalIntents
from .brain.llm import Brain, LLMUnavailable
from .brain.speech_text import SentenceChunker, markdown_to_speech
from .brain.toolbox import Toolbox
from .config import Settings, find_ui_dir
from .devwork import DevWatcher, WorkerClient, WorkerError
from .ha.alerts import AlertEngine, load_rules
from .ha.client import HAClient
from .ha.protocols import ProtocolBook
from .monitors import AtriumMonitor, DockerMonitor, HealthService
from .store import Store
from .voice.session import CaptureSession
from .voice.wyoming import PiperTTS, VoiceServiceError, WhisperSTT

log = logging.getLogger("sentinel")


class Client:
    """Un appareil connecté (onglet de navigateur, téléphone…)."""

    def __init__(self, ws: WebSocket, max_utterance_seconds: int = 60):
        self.ws = ws
        self.id = uuid.uuid4().hex[:8]
        self.capture = CaptureSession(max_seconds=max_utterance_seconds)


class Hub:
    """Registre des clients connectés + diffusion des événements."""

    # Un client gelé (Wi-Fi coupé sans fermeture TCP) ne doit jamais figer un
    # tour de parole : au-delà de ce délai on ferme sa connexion, il se
    # reconnectera tout seul.
    SEND_TIMEOUT = 5.0

    def __init__(self) -> None:
        self._clients: dict[WebSocket, Client] = {}

    def register(self, ws: WebSocket, max_utterance_seconds: int = 60) -> Client:
        client = Client(ws, max_utterance_seconds)
        self._clients[ws] = client
        return client

    def unregister(self, ws: WebSocket) -> None:
        self._clients.pop(ws, None)

    @property
    def count(self) -> int:
        return len(self._clients)

    def clients(self) -> list[Client]:
        return list(self._clients.values())

    async def _safe_send(
        self, client: Client, *, text: str | None = None, data: bytes | None = None
    ) -> None:
        try:
            async with asyncio.timeout(self.SEND_TIMEOUT):
                if text is not None:
                    await client.ws.send_text(text)
                elif data is not None:
                    await client.ws.send_bytes(data)
        except TimeoutError:
            log.warning("Client %s ne répond plus — fermeture de sa connexion", client.id)
            with contextlib.suppress(Exception):
                await client.ws.close()
        except Exception:
            pass  # client parti entre-temps

    async def send(self, client: Client, payload: dict) -> None:
        await self._safe_send(client, text=json.dumps(payload, ensure_ascii=False))

    async def send_bytes(self, client: Client, data: bytes) -> None:
        await self._safe_send(client, data=data)

    async def broadcast(self, payload: dict) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        clients = list(self._clients.values())
        if clients:
            # En parallèle : un client lent ne retarde pas les autres
            await asyncio.gather(*(self._safe_send(c, text=text) for c in clients))


class Sentinel:
    """État applicatif : un seul tour de parole à la fois, interruptible."""

    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.hub = Hub()
        self.stt = WhisperSTT(
            settings.whisper_host, settings.whisper_port, settings.wyoming_timeout_seconds
        )
        self.tts = PiperTTS(
            settings.piper_host, settings.piper_port, settings.wyoming_timeout_seconds
        )
        self.state = "idle"
        self._turn_task: asyncio.Task | None = None
        self._turn_lock = asyncio.Lock()
        self._announce_lock = asyncio.Lock()

        # ── Domotique & surveillance — chaque brique se dégrade proprement ──
        self.protocols = ProtocolBook.load(settings.config_dir / "protocols.yml")
        self.ha: HAClient | None = None
        self.engine: ActionEngine | None = None
        self.alerts: AlertEngine | None = None
        if settings.ha_url and settings.ha_token:
            self.ha = HAClient(
                settings.ha_url, settings.ha_token,
                on_event=self._on_ha_event, on_status=self._on_ha_status,
            )
        else:
            log.warning("HA_URL/HA_TOKEN absents : domotique désactivée (conversation seule).")

        self._docker = (
            DockerMonitor(settings.docker_proxy_url, settings.docker_restart_url or None)
            if settings.docker_proxy_url
            else None
        )
        atrium = AtriumMonitor(settings.atrium_url) if settings.atrium_url else None
        self.health = HealthService(settings, self.ha, self._docker, atrium)
        self._worker = WorkerClient(settings.worker_url) if settings.worker_url else None

        if self.ha or self._docker or self._worker:
            registry = build_registry(self.ha, self.protocols, self._docker, self._worker)
            self.engine = ActionEngine(registry, store, on_proposal_change=self._on_proposal_change)
        if self.ha and self.engine:
            self.alerts = AlertEngine(
                load_rules(settings.config_dir / "alerts.yml"), self.ha, self.engine, self.announce
            )

        toolbox = Toolbox(
            self.ha, self.engine, self.protocols, store,
            health=self.health, docker=self._docker, worker=self._worker,
        )
        self.intents = LocalIntents(
            self.ha, self.engine, self.protocols, store,
            settings.config_dir / "intents.yml", settings.tz, health=self.health,
        )
        self.brain = Brain(settings, toolbox, on_activity=self._on_activity)
        self._report_task: asyncio.Task | None = None
        self._devwatch_task: asyncio.Task | None = None
        self._dev_running: dict | None = None  # tâche de dev en cours (cache pour hello)
        self._bg: set[asyncio.Task] = set()  # références fortes (le GC peut sinon tuer une tâche)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # ── Ponts vers l'UI ──────────────────────────────────────────────────

    async def _on_ha_event(self, event: dict) -> None:
        if self.alerts:
            await self.alerts.on_state_changed(event)

    async def _on_ha_status(self, connected: bool) -> None:
        await self.hub.broadcast({"type": "ha_status", "connected": connected})

    async def _on_activity(self, label: str) -> None:
        await self.hub.broadcast({"type": "activity", "text": label})

    async def _on_proposal_change(self, change: str, proposal: dict) -> None:
        kind = "proposal_new" if change == "new" else "proposal_update"
        await self.hub.broadcast({"type": kind, "proposal": proposal})

    # ── Annonces proactives (alertes, à tous les appareils) ──────────────

    async def announce(self, text: str, severity: str = "info", speak: bool = True) -> None:
        """Sentinel prend la parole de lui-même : fil + bannière + voix partout."""
        message = await self.store.add_message("assistant", text, "alert")
        await self.hub.broadcast({"type": "alert", "level": severity, "text": text})
        await self.hub.broadcast({"type": "message", "message": message})
        if speak:
            self._spawn(self._speak_announcement(text, severity))

    # ── Rapport quotidien ────────────────────────────────────────────────

    def start_daily_report(self) -> None:
        hhmm = self.settings.daily_report
        if not hhmm:
            return
        try:
            hour, minute = (int(x) for x in hhmm.split(":", 1))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError
        except ValueError:
            log.warning("SENTINEL_DAILY_REPORT invalide (%r) — rapport désactivé", hhmm)
            return
        self._report_task = asyncio.create_task(self._daily_report_loop(hour, minute))
        log.info("Rapport quotidien planifié à %02d:%02d (%s)", hour, minute, self.settings.tz)

    async def _daily_report_loop(self, hour: int, minute: int) -> None:
        # Sondage à la minute plutôt que sleep-until : insensible aux changements
        # d'heure (DST) et aux dérives d'horloge.
        try:
            tz = ZoneInfo(self.settings.tz)
        except Exception:
            tz = None
        last_fired_on = None
        while True:
            await asyncio.sleep(30)
            now = datetime.now(tz)
            if now.hour != hour or now.minute != minute or last_fired_on == now.date():
                continue
            last_fired_on = now.date()
            try:
                pending = await self.store.list_proposals("pending")
                deferred = await self.store.list_proposals("deferred")
                text = await self.health.rapport_quotidien(len(pending) + len(deferred))
                await self.announce(text, "info", speak=False)
            except Exception:
                log.exception("Rapport quotidien en échec")

    def start_dev_watcher(self) -> None:
        if self._worker is None:
            return
        watcher = DevWatcher(
            self._worker, self.engine, self.announce,
            on_running_change=self._on_dev_running,
        )
        self._devwatch_task = asyncio.create_task(watcher.run())
        log.info("Veilleur des tâches de développement actif (%s)", self.settings.worker_url)

    async def _on_dev_running(self, running: dict | None) -> None:
        """Pastille « atelier au travail » de l'UI, mise à jour par le veilleur."""
        self._dev_running = (
            {"id": running.get("id"), "repo": running.get("repo")} if running else None
        )
        await self.hub.broadcast({"type": "dev_status", "running": self._dev_running})

    async def stop_background(self) -> None:
        for task in (self._report_task, self._devwatch_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._report_task = None
        self._devwatch_task = None
        await self.health.close()
        if self._worker:
            await self._worker.close()

    async def _speak_announcement(self, text: str, severity: str) -> None:
        async with self._announce_lock:  # une annonce vocale à la fois
            if severity == "critical":
                await self.cancel_turn()  # une alerte critique coupe la parole
            else:
                task = self._turn_task
                if task and not task.done():
                    await asyncio.wait({task}, timeout=30)  # laisser finir le tour
                    if not task.done():
                        # Toujours occupé : on ne superpose pas deux voix — le
                        # texte est déjà dans le fil et la bannière.
                        log.warning("Annonce vocale sautée (tour encore en cours) : %s", text)
                        return
            speakable = markdown_to_speech(text)
            if not speakable:
                return
            clients = self.hub.clients()
            if not clients:
                return
            await self.set_state("speaking")
            started = False
            try:
                async for rate, chunk in self.tts.synthesize(speakable):
                    if not started:
                        started = True
                        for c in clients:
                            await self.hub.send(c, {"type": "speak_start", "rate": rate})
                    for c in clients:
                        await self.hub.send_bytes(c, chunk)
            except VoiceServiceError as exc:
                log.warning("Annonce vocale impossible : %s", exc)
            finally:
                if started:
                    for c in clients:
                        await self.hub.send(c, {"type": "speak_end"})
                # Ne pas écraser l'état d'un tour démarré pendant l'annonce
                if not (self._turn_task and not self._turn_task.done()):
                    await self.set_state("idle")

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
        except Exception:
            # Quoi qu'il arrive, on ne laisse jamais l'état bloqué sur « transcription »
            log.exception("Échec inattendu de la transcription")
            await self.hub.send(
                origin,
                {"type": "notice", "text": "La transcription a échoué — détail dans les journaux du serveur."},
            )
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

            # Intents locaux d'abord : domotique courante sans LLM, hors Internet
            intent_reply = await self.intents.handle(text, source)
            if intent_reply is not None:
                stream = _single_reply(intent_reply)
            else:
                history = _build_history(
                    await self.store.recent_messages(self.settings.history_window)
                )
                stream = self.brain.stream_reply(history, utterance=text, source=source)

            await self.hub.broadcast({"type": "assistant_start", "id": assistant_id})

            if speak:
                speaker = asyncio.create_task(self._speak_worker(origin, tts_queue))

            chunker = SentenceChunker()
            async for delta in stream:
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
        except Exception:
            log.exception("Échec inattendu du tour de parole")
            error_text = "Une erreur interne est survenue — détail dans les journaux du serveur."
        finally:
            if speaker and not speaker.done():
                speaker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await speaker

            full = "".join(parts).strip()
            message = None
            if full:
                with contextlib.suppress(Exception):  # arrêt serveur pendant le tour
                    message = await self.store.add_message(
                        "assistant", full, "voice" if speak else "text"
                    )
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


async def _single_reply(text: str):
    """Réponse d'intent local, servie dans le même pipeline que le LLM."""
    yield text


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
    sentinel = Sentinel(settings, store)
    app.state.sentinel = sentinel
    if sentinel.ha:
        await sentinel.ha.start()
    sentinel.start_daily_report()
    sentinel.start_dev_watcher()
    log.info(
        "Sentinel %s démarré — modèle %s, effort %s, UI %s",
        __version__, settings.model, settings.effort, settings.ui_dir,
    )
    if not settings.anthropic_api_key:
        log.warning(
            "ANTHROPIC_API_KEY absente : la voix et le chat répondront par une erreur."
        )
    yield
    await sentinel.cancel_turn()
    await sentinel.stop_background()
    if sentinel.ha:
        await sentinel.ha.stop()
    await store.close()


app = FastAPI(title="Sentinel", version=__version__, lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "sentinel", "version": __version__}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    sentinel: Sentinel = ws.app.state.sentinel
    await ws.accept()
    client = sentinel.hub.register(ws, sentinel.settings.max_utterance_seconds)
    log.info("Client %s connecté (%d en ligne)", client.id, sentinel.hub.count)

    pending = await sentinel.store.list_proposals("pending")
    deferred = await sentinel.store.list_proposals("deferred")
    await sentinel.hub.send(
        client,
        {
            "type": "hello",
            "version": __version__,
            "state": sentinel.state,
            "history": await sentinel.store.recent_messages(50),
            "ha_connected": bool(sentinel.ha and sentinel.ha.connected),
            "ha_configured": sentinel.ha is not None,
            "dev_configured": sentinel._worker is not None,
            "dev_running": sentinel._dev_running,
            "proposals": sorted(pending + deferred, key=lambda p: p["num"]),
            "protocols": [
                {"nom": p.display, "risque": p.risk} for p in sentinel.protocols.all()
            ],
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
            try:
                await _on_message(sentinel, client, msg)
            except Exception:
                # Un message ne doit jamais coûter sa connexion au client
                log.exception("Traitement d'un message WS en échec (%s)", msg.get("type"))
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

    elif mtype == "proposal_decision":
        if sentinel.engine is None:
            await sentinel.hub.send(
                client, {"type": "notice", "text": "Le moteur d'actions n'est pas disponible."}
            )
            return
        decision = str(msg.get("decision") or "")
        if decision not in ("approve", "reject", "defer"):
            return
        try:
            num = int(msg.get("id"))
        except (TypeError, ValueError):
            return
        _, message = await sentinel.engine.decide(num, decision, via="ui")
        await sentinel.hub.send(client, {"type": "notice", "text": message})

    elif mtype == "dev_tasks":
        await _reply_dev_tasks(sentinel, client)

    elif mtype == "dev_log":
        await _reply_dev_log(sentinel, client, msg)

    elif mtype == "dev_diff":
        await _reply_dev_diff(sentinel, client, msg)

    elif mtype == "sante":
        await _reply_sante(sentinel, client)

    elif mtype == "historique":
        await _reply_historique(sentinel, client)

    elif mtype == "ping":
        await sentinel.hub.send(client, {"type": "pong"})


# ── Requêtes de lecture des panneaux (réponse au seul client demandeur) ──

_WORKER_ABSENT = "L'atelier de développement n'est pas configuré (WORKER_URL)."


async def _reply_dev_tasks(sentinel: Sentinel, client: Client) -> None:
    if sentinel._worker is None:
        await sentinel.hub.send(client, {"type": "dev_tasks", "error": _WORKER_ABSENT})
        return
    try:
        tasks, health = await asyncio.gather(
            sentinel._worker.list_tasks(), sentinel._worker.health()
        )
    except WorkerError as exc:
        await sentinel.hub.send(client, {"type": "dev_tasks", "error": str(exc)})
        return
    health = health or {}
    await sentinel.hub.send(client, {
        "type": "dev_tasks",
        "tasks": tasks,
        "atelier": {
            "auth": health.get("auth"),
            "push_possible": bool(health.get("push_possible")),
            "repos": health.get("repos") or [],
        },
    })


async def _reply_dev_log(sentinel: Sentinel, client: Client, msg: dict) -> None:
    task_id = str(msg.get("id") or "")
    if sentinel._worker is None or not task_id:
        return
    try:
        after = max(0, int(msg.get("after") or 0))
    except (TypeError, ValueError):
        after = 0
    try:
        data = await sentinel._worker.get_log(task_id, after)
    except WorkerError as exc:
        await sentinel.hub.send(
            client, {"type": "dev_log", "id": task_id, "error": str(exc)}
        )
        return
    await sentinel.hub.send(client, {"type": "dev_log", "id": task_id, **data})


async def _reply_dev_diff(sentinel: Sentinel, client: Client, msg: dict) -> None:
    task_id = str(msg.get("id") or "")
    if sentinel._worker is None or not task_id:
        return
    try:
        diff = await sentinel._worker.get_diff(task_id)
    except WorkerError as exc:
        await sentinel.hub.send(
            client, {"type": "dev_diff", "id": task_id, "error": str(exc)}
        )
        return
    await sentinel.hub.send(client, {"type": "dev_diff", "id": task_id, "diff": diff})


async def _reply_sante(sentinel: Sentinel, client: Client) -> None:
    try:
        snap = await sentinel.health.snapshot()
    except Exception:
        log.exception("Instantané santé impossible")
        await sentinel.hub.send(
            client,
            {"type": "sante", "error": "Impossible de collecter l'état des systèmes."},
        )
        return
    await sentinel.hub.send(client, {"type": "sante", "data": snap})


async def _reply_historique(sentinel: Sentinel, client: Client) -> None:
    journal = await sentinel.store.list_journal(80)
    proposals = [
        p for p in await sentinel.store.list_proposals(limit=60)
        if p["status"] not in ("pending", "deferred")
    ]
    await sentinel.hub.send(
        client, {"type": "historique", "journal": journal, "proposals": proposals[:40]}
    )


# L'UI statique en dernier : les routes déclarées avant restent prioritaires.
app.mount("/", StaticFiles(directory=find_ui_dir(), html=True), name="ui")
