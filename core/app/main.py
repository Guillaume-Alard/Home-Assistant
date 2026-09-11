"""Serveur Sentinel : API HTTP(S), WebSocket temps réel et UI statique (PWA).

Protocole WebSocket (résumé — détail dans docs/ARCHITECTURE.md) :

  Client → serveur (JSON) : chat, audio_start, audio_end, audio_cancel, cancel,
                            proposal_decision, wake_start, wake_stop, ping —
                            les requêtes de lecture des panneaux (dev_tasks,
                            dev_log, dev_diff, sante, historique, mail, memoires,
                            speakers, pages), la gestion de la mémoire (memoire_add,
                            memoire_delete), des profils vocaux (speaker_add,
                            speaker_delete, speaker_enroll_start/end), des pages
                            web (page_get, page_publish, page_unpublish, page_delete),
                            des propositions d'évolution (evolutions, evolution_get,
                            evolution_accept, evolution_reject, evolution_delete) et
                            des fournisseurs LLM (llm_providers, llm_select)
  Client → serveur (binaire) : PCM 16 bits mono (tour de parole, veille, ou
                               enrôlement d'une empreinte vocale)
  Serveur → clients (JSON) : hello, status, message, assistant_start,
                             assistant_delta, assistant_end, speak_start,
                             speak_end, notice, error, alert, ha_status,
                             activity, proposal_new, proposal_update,
                             dev_status, wake, wake_error, sources, pong — les réponses de
                             panneaux (dev_tasks, dev_log, dev_diff, sante,
                             historique, au seul client demandeur), memoires et
                             speakers (rediffusés à tous après un changement),
                             speaker (locuteur reconnu), enroll_result, llm
                             (fournisseur LLM actif, rediffusé après une bascule)
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
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .actions.engine import ActionEngine
from .actions.executors import build_registry
from .notify import Notifier
from .brain.intents import LocalIntents
from .brain.llm import Brain, LLMUnavailable
from .brain.memory import format_profile, normalize_category
from .brain.speech_text import SentenceChunker, markdown_to_speech
from .brain.toolbox import Toolbox
from .config import Settings, find_ui_dir
from .identity import OWNER, Speaker, identify
from .ha.alerts import AlertEngine, load_rules
from .ha.client import HAClient
from .ha.media import load_config as load_media_config, snapshot as media_snapshot
from .ha.protocols import ProtocolBook
from .monitors import HealthService
from .proactive import ProactiveEngine
from .reminders import ReminderScheduler
from .routines import RoutineService
from .selfmod import SelfSource, summarize as summarize_diff
from .store import Store
from .voice.session import CaptureSession
from .voice.wyoming import (
    ClonedTTS,
    FallbackTTS,
    PiperTTS,
    SpeakerEmbedder,
    VoiceServiceError,
    WakeStream,
    WakeWordDetector,
    WhisperSTT,
)

log = logging.getLogger("sentinel")


class Client:
    """Un appareil connecté (onglet de navigateur, téléphone…)."""

    def __init__(self, ws: WebSocket, max_utterance_seconds: int = 60):
        self.ws = ws
        self.id = uuid.uuid4().hex[:8]
        self.capture = CaptureSession(max_seconds=max_utterance_seconds)
        self.wake: WakeStream | None = None  # veille au mot d'éveil (Phase 5A)
        self.wake_rate = 16000
        # Enrôlement d'une empreinte vocale (Phase 2) — capture courte dédiée.
        self.enroll = CaptureSession(max_seconds=15)
        self.enroll_id: str | None = None


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
        piper = PiperTTS(
            settings.piper_host, settings.piper_port, settings.wyoming_timeout_seconds
        )
        # Voix de Luna : clonage local (repli automatique sur Piper) ou Piper seul.
        if settings.tts_engine == "cloned" and settings.cloned_tts_url:
            self.tts = FallbackTTS(
                ClonedTTS(
                    settings.cloned_tts_url,
                    settings.cloned_tts_voice,
                    settings.cloned_tts_model,
                    settings.cloned_tts_rate,
                    settings.wyoming_timeout_seconds,
                ),
                piper,
            )
        else:
            self.tts = piper
        self.wake_detector: WakeWordDetector | None = (
            WakeWordDetector(settings.wake_host, settings.wake_port)
            if settings.wake_host
            else None
        )
        # Reconnaissance de locuteur (Phase 2) — désactivée si SPEAKER_HOST vide.
        self.speaker_embedder: SpeakerEmbedder | None = (
            SpeakerEmbedder(
                settings.speaker_host, settings.speaker_port, settings.wyoming_timeout_seconds
            )
            if settings.speaker_host
            else None
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

        # Santé de Nova : version, entités indisponibles, mises à jour, hors-ligne.
        self.health = HealthService(settings, self.ha)

        if self.ha:
            registry = build_registry(self.ha, self.protocols)
            self.engine = ActionEngine(registry, store, on_proposal_change=self._on_proposal_change)
        # Notifications mobiles (Phase 14) : Luna te joint sur ton téléphone via le
        # service notify de Nova. Communication seule, jamais de pilotage. Inactif
        # sans moteur/service ; la poussée passe alors en silence (renvoie False).
        self.notifier = Notifier(
            self.engine, settings.notify_service,
            reminders=settings.notify_reminders, alerts=settings.notify_alerts,
        )
        if self.ha and self.engine:
            self.alerts = AlertEngine(
                load_rules(settings.config_dir / "alerts.yml"), self.ha, self.engine, self.announce
            )
        # Proactivité contextuelle (Phase 7) : observe et SUGGÈRE, jamais n'exécute.
        self.proactive: ProactiveEngine | None = None
        if self.ha and self.engine and settings.proactive_enabled:
            self.proactive = ProactiveEngine(
                settings, self.ha, self.engine, store,
                say=self.say_proactive, on_change=self._broadcast_proactive,
                notify=self._notify_alert,
            )
        # Scénarios & routines (Phase 8) : Luna propose, Guillaume active puis déclenche.
        self.routines: RoutineService | None = None
        if self.ha and self.engine and settings.routines_enabled:
            self.routines = RoutineService(
                settings, self.ha, self.engine, store,
                announce=self.say_proactive, on_change=self._broadcast_routines,
            )
        # Musique multi-pièces (Phase 9) : pilotage des media_player de Nova.
        self.media_cfg = (
            load_media_config(settings.config_dir / "media.yml")
            if self.ha and settings.music_enabled else None
        )
        self._media_last = 0.0  # anti-rafale des diffusions d'état média
        # Minuteurs & rappels (Phase 10) : 100% local, indépendant de Nova.
        self.reminders: ReminderScheduler | None = (
            ReminderScheduler(
                settings, store, announce=self.say_proactive,
                on_change=self._broadcast_reminders, on_fire=self._on_reminder_fired,
            )
            if settings.reminders_enabled else None
        )

        # Auto-amélioration encadrée (Phase 6) : lecteur SEULE lecture de son propre
        # code (app/ + ui/), pour que Luna rédige des diffs justes.
        self.source = SelfSource(Path(__file__).resolve().parent, settings.ui_dir)
        toolbox = Toolbox(
            self.ha, self.engine, self.protocols, store,
            health=self.health, source=self.source, self_improve=settings.self_improve_enabled,
            routines=self.routines, media=self.media_cfg,
            reminders=settings.reminders_enabled, tz=settings.tz,
            on_memory_change=self._broadcast_memoires,
            on_suggestions_change=self._broadcast_evolutions,
            on_reminders_change=self._broadcast_reminders,
        )
        self.intents = LocalIntents(
            self.ha, self.engine, self.protocols, store,
            settings.config_dir / "intents.yml", settings.tz, health=self.health,
        )
        self.brain = Brain(
            settings, toolbox,
            on_activity=self._on_activity, memory_provider=self._memory_context,
            on_sources=self._broadcast_sources, on_usage=self._record_usage,
        )
        self._proactive_task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()  # références fortes (le GC peut sinon tuer une tâche)
        self._llm_cfg: dict = {}  # réglages LLM du cockpit (clés/modèles/params) — cf. restore_llm_config

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # ── Ponts vers l'UI ──────────────────────────────────────────────────

    async def _on_ha_event(self, event: dict) -> None:
        if self.alerts:
            await self.alerts.on_state_changed(event)
        # Un changement d'état peut créer une situation à suggérer (porte ouverte…) :
        # on réévalue, de façon amortie (au plus une fois toutes les 20 s).
        if self.proactive and event.get("event_type") == "state_changed":
            self._spawn(self.proactive.nudge())
        # Musique : rafraîchit la tuile lecteur quand un media_player change
        # (amorti : au plus une diffusion toutes les 1,5 s — la position défile vite).
        if self.media_cfg and event.get("event_type") == "state_changed":
            entity_id = (event.get("data") or {}).get("entity_id") or ""
            if entity_id.startswith("media_player."):
                now = time.monotonic()
                if now - self._media_last >= 1.5:
                    self._media_last = now
                    self._spawn(self._broadcast_media())

    async def _on_ha_status(self, connected: bool) -> None:
        await self.hub.broadcast({"type": "ha_status", "connected": connected})

    async def _on_activity(self, label: str) -> None:
        await self.hub.broadcast({"type": "activity", "text": label})

    async def _broadcast_sources(self, sources: list[dict]) -> None:
        """Sources web citées par Luna (Phase 4) — rattachées au dernier message."""
        await self.hub.broadcast({"type": "sources", "sources": sources})

    async def _record_usage(self, provider: str, model: str, in_tok: int, out_tok: int) -> None:
        """Comptabilise les tokens d'un tour LLM dans le Store (best-effort)."""
        with contextlib.suppress(Exception):
            await self.store.add_usage(provider, model, in_tok, out_tok)

    # ── Fournisseurs LLM (multi-LLM) ─────────────────────────────────────

    def _llm_payload(self) -> dict:
        """État des fournisseurs LLM pour le cockpit (jamais de clé)."""
        return {"type": "llm", **self.brain.providers_public()}

    async def _broadcast_llm(self) -> None:
        await self.hub.broadcast(self._llm_payload())

    async def restore_llm_config(self) -> None:
        """Recharge les réglages LLM du cockpit (clés, modèles, params, actif) et
        les applique. Migre l'ancien réglage `llm_provider` (fournisseur actif seul)."""
        cfg: dict = {}
        try:
            raw = await self.store.get_setting("llm_config")
            if raw:
                cfg = json.loads(raw)
            else:
                legacy = await self.store.get_setting("llm_provider")
                if legacy:
                    cfg = {"active": legacy}
        except Exception:
            log.exception("Lecture des réglages LLM persistants impossible")
            cfg = {}
        self._llm_cfg = cfg if isinstance(cfg, dict) else {}
        self.brain.apply_config(self._llm_cfg)

    async def _persist_llm(self) -> None:
        """Enregistre la config LLM (contient des clés API — jamais renvoyée à l'UI)."""
        with contextlib.suppress(Exception):
            await self.store.set_setting("llm_config", json.dumps(self._llm_cfg, ensure_ascii=False))

    async def _apply_llm(self) -> None:
        """Applique la config LLM courante, la persiste, et rediffuse l'état."""
        self.brain.apply_config(self._llm_cfg)
        await self._persist_llm()
        await self._broadcast_llm()

    # ── Consommation & crédit (Paramètres › Consommation) ────────────────────

    async def build_usage_payload(self, days: int = 30) -> dict:
        """Résumé de consommation (tokens comptés localement) + coût ESTIMÉ + solde
        restant, réel quand le fournisseur l'expose (OpenRouter), honnête sinon."""
        from .brain.pricing import estimate_usd

        since = (datetime.now(timezone.utc).date() - timedelta(days=max(1, days) - 1)).isoformat()
        try:
            rows = await self.store.usage_rows(since)
        except Exception:
            log.exception("Lecture de la consommation impossible")
            rows = []

        labels = {p["id"]: p["label"] for p in self.brain.providers_public()["providers"]}
        items, tot_in, tot_out, tot_cost, cost_complete = [], 0, 0, 0.0, True
        for r in rows:
            est = estimate_usd(r["model"], r["input_tokens"], r["output_tokens"])
            items.append({
                "provider": r["provider"], "label": labels.get(r["provider"], r["provider"]),
                "model": r["model"], "turns": r["turns"],
                "input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"],
                "cost_usd": est,
            })
            tot_in += r["input_tokens"] or 0
            tot_out += r["output_tokens"] or 0
            if est is None:
                cost_complete = False
            else:
                tot_cost += est
        return {
            "type": "llm_usage", "period_days": days, "since": since, "rows": items,
            "totals": {
                "input_tokens": tot_in, "output_tokens": tot_out,
                "cost_usd": round(tot_cost, 4), "cost_complete": cost_complete,
            },
            "credits": await self._fetch_credits(),
        }

    async def _fetch_credits(self) -> list[dict]:
        """Solde par fournisseur configuré. RÉEL pour OpenRouter (API) ; pour les
        autres, l'API n'expose pas de solde → note honnête « à vérifier sur la
        console ». Best-effort : un échec réseau n'empêche jamais l'affichage."""
        from .brain.pricing import FREE_PROVIDERS

        out: list[dict] = []
        for p in self.brain.providers_public()["providers"]:
            pid, label = p["id"], p["label"]
            if not p.get("configured"):
                continue
            if pid == "openrouter":
                out.append({"provider": pid, "label": label, **await self._openrouter_credit()})
            elif pid in FREE_PROVIDERS:
                out.append({"provider": pid, "label": label, "kind": "free",
                            "note": "Gratuit à ce jour — pas de solde à suivre."})
            elif pid == "claude":
                out.append({"provider": pid, "label": label, "kind": "unavailable",
                            "note": "L'API Anthropic n'expose pas le solde restant — "
                                    "à vérifier sur console.anthropic.com."})
            else:
                out.append({"provider": pid, "label": label, "kind": "unavailable",
                            "note": "Solde non exposé par l'API — à vérifier sur la "
                                    "console du fournisseur."})
        return out

    async def _openrouter_credit(self) -> dict:
        """Solde OpenRouter réel via son API (crédits - usage). Best-effort."""
        key = self.brain.provider_key("openrouter")
        if not key:
            return {"kind": "unavailable", "note": "Clé absente."}
        try:
            async with httpx.AsyncClient(timeout=6.0) as cx:
                resp = await cx.get(
                    "https://openrouter.ai/api/v1/credits",
                    headers={"Authorization": f"Bearer {key}"},
                )
            resp.raise_for_status()
            data = resp.json().get("data", {}) or {}
            total = float(data.get("total_credits", 0) or 0)
            used = float(data.get("total_usage", 0) or 0)
            return {"kind": "balance", "currency": "USD",
                    "remaining": round(total - used, 4),
                    "used": round(used, 4), "total": round(total, 4)}
        except Exception:
            log.warning("Solde OpenRouter injoignable", exc_info=True)
            return {"kind": "unavailable", "note": "Solde OpenRouter injoignable pour l'instant."}

    async def _on_proposal_change(self, change: str, proposal: dict) -> None:
        kind = "proposal_new" if change == "new" else "proposal_update"
        await self.hub.broadcast({"type": kind, "proposal": proposal})

    # ── Mémoire persistante (Phase 1) ────────────────────────────────────

    async def _memory_context(self, subject: str | None) -> str:
        """Bloc « ce que je sais de toi » du locuteur courant (vide si invité/coupée)."""
        if not self.settings.memory_enabled or not subject:
            return ""
        try:
            mems = await self.store.list_memories(subject=subject, limit=self.settings.memory_window)
        except Exception:
            log.exception("Lecture de la mémoire impossible")
            return ""
        return format_profile(mems)

    async def _memoires_payload(self) -> dict:
        # Paramètres › Mémoire montre la mémoire de Guillaume (les profils de la
        # maisonnée gèrent la leur à la voix — pas de surveillance croisée).
        mems = await self.store.list_memories(subject="guillaume", limit=500)
        return {"type": "memoires", "enabled": self.settings.memory_enabled, "memories": mems}

    async def _broadcast_memoires(self, subject: str | None = None) -> None:
        """Rafraîchit Paramètres › Mémoire (mémoire de Guillaume) sur tous les appareils."""
        if subject not in (None, "guillaume"):
            return  # un changement dans un autre profil n'affecte pas le panneau
        await self.hub.broadcast(await self._memoires_payload())

    # ── Reconnaissance de locuteur (Phase 2) ─────────────────────────────

    async def identify_speaker(self, pcm: bytes, rate: int) -> Speaker:
        """Qui parle ? Empreinte du PCM → meilleur profil au-dessus du seuil.

        Reconnaissance désactivée (pas de service) → propriétaire (comportement
        d'avant, aucune restriction). Service actif mais voix non reconnue, ou
        service en panne → invité (prudent). La reconnaissance ne peut jamais
        ÉLEVER un droit : au mieux elle confirme le propriétaire.
        """
        if self.speaker_embedder is None:
            return OWNER
        try:
            profiles = await self.store.speaker_profiles()
            if not profiles:
                return OWNER  # personne n'est encore enrôlé : ne bloque pas la maison
            vector = await self.speaker_embedder.embed(pcm, rate=rate)
        except VoiceServiceError as exc:
            log.warning("Reconnaissance de locuteur indisponible : %s", exc)
            return OWNER  # panne du service : on ne verrouille pas Guillaume dehors
        except Exception:
            log.exception("Reconnaissance de locuteur en échec")
            return OWNER
        return identify(vector, profiles, self.settings.speaker_threshold)

    async def _speakers_payload(self) -> dict:
        return {
            "type": "speakers",
            "enabled": self.speaker_embedder is not None,
            "speakers": await self.store.list_speakers(),
        }

    async def _broadcast_speakers(self) -> None:
        await self.hub.broadcast(await self._speakers_payload())

    # ── Auto-amélioration encadrée (Phase 6) ─────────────────────────────

    async def _evolutions_payload(self) -> dict:
        return {
            "type": "evolutions",
            "enabled": self.settings.self_improve_enabled,
            "suggestions": await self.store.list_suggestions(),
        }

    async def _broadcast_evolutions(self) -> None:
        """Rafraîchit Paramètres › Évolutions quand Luna propose une auto-amélioration."""
        await self.hub.broadcast(await self._evolutions_payload())

    # ── Proactivité contextuelle (Phase 7) ───────────────────────────────

    async def say_proactive(self, text: str, speak: bool = True) -> None:
        """Luna prend la parole d'elle-même pour un constat/suggestion (fil + voix,
        SANS bannière d'alerte — c'est une suggestion, pas une alarme)."""
        message = await self.store.add_message("assistant", text, "proactive")
        await self.hub.broadcast({"type": "message", "message": message})
        if speak:
            self._spawn(self._speak_announcement(text, "info"))

    async def _proactive_payload(self) -> dict:
        return {
            "type": "proactive",
            "enabled": self.proactive is not None,
            "suggestions": await self.store.list_proactive(),
            "muted": await self.store.list_proactive_mutes(),
        }

    async def _broadcast_proactive(self) -> None:
        await self.hub.broadcast(await self._proactive_payload())

    # ── Scénarios & routines (Phase 8) ───────────────────────────────────

    async def _routines_payload(self) -> dict:
        return {
            "type": "routines",
            "enabled": self.routines is not None,
            "routines": await self.store.list_routines(),
        }

    async def _broadcast_routines(self) -> None:
        await self.hub.broadcast(await self._routines_payload())

    # ── Musique multi-pièces (Phase 9) ───────────────────────────────────

    def _media_payload(self) -> dict:
        players = media_snapshot(self.ha, live_only=True) if self.ha else []
        return {"type": "media", "enabled": self.media_cfg is not None, "players": players}

    async def _broadcast_media(self) -> None:
        await self.hub.broadcast(self._media_payload())

    # ── Minuteurs & rappels (Phase 10) ───────────────────────────────────

    async def _reminders_payload(self) -> dict:
        return {
            "type": "reminders",
            "enabled": self.reminders is not None,
            "reminders": await self.store.list_reminders("active"),
        }

    async def _broadcast_reminders(self) -> None:
        await self.hub.broadcast(await self._reminders_payload())

    async def _on_reminder_fired(self, reminder: dict) -> None:
        """Carillon + petit signal pour l'UI quand un minuteur/rappel sonne."""
        kind = reminder.get("kind")
        label = reminder.get("label") or ""
        await self.hub.broadcast({"type": "reminder_fired", "kind": kind, "label": label})
        # Notification mobile (Phase 14) : le rappel te suit hors du cockpit.
        if self.notifier.reminders:
            icon = "⏱" if kind == "timer" else "⏰"
            titre = "Minuteur terminé" if kind == "timer" else "Rappel"
            self._spawn(self.notifier.push(label or "C'est l'heure.", title=f"{icon} {titre}"))

    # ── Annonces proactives (alertes, à tous les appareils) ──────────────

    async def announce(self, text: str, severity: str = "info", speak: bool = True) -> None:
        """Sentinel prend la parole de lui-même : fil + bannière + voix partout."""
        message = await self.store.add_message("assistant", text, "alert")
        await self.hub.broadcast({"type": "alert", "level": severity, "text": text})
        await self.hub.broadcast({"type": "message", "message": message})
        if speak:
            self._spawn(self._speak_announcement(text, severity))
        # Notification mobile (Phase 14) : les vraies alertes (avertissement/critique)
        # te suivent sur le téléphone ; une simple info reste au cockpit.
        if self.notifier.alerts and severity in ("warning", "critical"):
            self._spawn(self.notifier.push(text, title="Sentinel"))

    async def _notify_alert(self, title: str, message: str) -> None:
        """Poussée téléphone d'une alerte proactive (Phase 14) — jamais bloquante."""
        if self.notifier.alerts:
            self._spawn(self.notifier.push(message, title=f"⚠️ {title}"))

    def start_proactive(self) -> None:
        if self.proactive is None:
            return
        self._proactive_task = asyncio.create_task(self.proactive.run())
        log.info("Veilleur proactif actif (intervalle %ss)", self.settings.proactive_interval)

    async def stop_background(self) -> None:
        task = self._proactive_task
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._proactive_task = None
        if self.routines:
            await self.routines.stop()
        if self.reminders:
            await self.reminders.stop()
        await self.health.close()

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

        # Qui parle ? (Phase 2) — personnalise et, si inconnu, restreint.
        speaker = await self.identify_speaker(pcm, rate)
        await self.hub.broadcast({"type": "speaker", **_speaker_event(speaker)})
        await self.run_reply_turn(origin, text, source="voice", speak=True, speaker=speaker)

    async def run_reply_turn(
        self, origin: Client, text: str, source: str, speak: bool,
        speaker: Speaker | None = None,
    ) -> None:
        """Un tour complet : message utilisateur → réponse LLM en streaming (+ TTS)."""
        who = speaker or OWNER  # écrit/UI : propriétaire (la voix seule identifie)
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

            # Intents locaux d'abord : domotique courante sans LLM, hors Internet.
            # Un invité (who.can_act == False) ne déclenche aucune action locale.
            intent_reply = await self.intents.handle(text, source, can_act=who.can_act)
            if intent_reply is not None:
                stream = _single_reply(intent_reply)
            else:
                history = _build_history(
                    await self.store.recent_messages(self.brain.history_window)
                )
                stream = self.brain.stream_reply(
                    history, utterance=text, source=source, speaker=who
                )

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

    async def run_assist_reply(self, text: str) -> str:
        """Un tour venu d'Assist (agent conversationnel de Nova / app HA).

        Même cerveau, même fil, même sécurité que le chat écrit — mais requête/
        réponse HTTP (pas de TTS : Nova gère la voix). Le tour PASSE par la même
        machinerie de tour unique (verrou + barge-in) que le WebSocket : il ne
        peut donc jamais se superposer à un tour web ni à un autre tour Assist
        (sinon deux flux corrompraient le fil partagé, et un outil pourrait
        s'exécuter deux fois). Renvoie le texte final (que Nova prononcera).
        """
        holder: dict[str, str] = {}

        async def _turn() -> None:
            holder["text"] = await self._assist_body(text)

        async with self._turn_lock:
            await self._cancel_locked()
            task = asyncio.create_task(_turn(), name="assist-turn")
            self._turn_task = task
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise  # notre propre annulation (client HTTP parti), pas le tour
        return holder.get("text") or "Désolé, ma réponse a été interrompue."

    async def run_assist_stream(self, text: str):
        """Variante streaming d'un tour Assist : rend les fragments au fil de l'eau.

        Même machinerie de tour unique (verrou + barge-in) que `run_assist_reply` —
        c'est le même `_assist_body` qui tourne, ici avec un callback qui pousse
        chaque fragment dans une file que ce générateur draine. Un tour non
        streamé (intent local) rend son texte en un seul fragment. En cas d'erreur
        sans aucun fragment produit, le message d'erreur est émis en fin de flux.
        """
        queue: asyncio.Queue = asyncio.Queue()
        done = object()
        holder: dict[str, str] = {}

        def _on_delta(delta: str) -> None:
            queue.put_nowait(delta)

        async def _turn() -> None:
            try:
                holder["text"] = await self._assist_body(text, on_delta=_on_delta)
            finally:
                queue.put_nowait(done)

        async with self._turn_lock:
            await self._cancel_locked()
            task = asyncio.create_task(_turn(), name="assist-stream")
            self._turn_task = task

        emitted = False
        try:
            while True:
                item = await queue.get()
                if item is done:
                    break
                emitted = True
                yield item
        finally:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        # Erreur (ou repli) sans aucun fragment : on rend quand même le texte final.
        if not emitted and holder.get("text"):
            yield holder["text"]

    async def _assist_body(self, text: str, on_delta=None) -> str:
        source = "assist"
        assistant_id = uuid.uuid4().hex[:12]
        parts: list[str] = []
        error_text: str | None = None
        cancelled = False
        try:
            user_msg = await self.store.add_message("user", text, source)
            await self.hub.broadcast({"type": "message", "message": user_msg})
            intent_reply = await self.intents.handle(text, source)
            if intent_reply is not None:
                stream = _single_reply(intent_reply)
            else:
                history = _build_history(
                    await self.store.recent_messages(self.brain.history_window)
                )
                stream = self.brain.stream_reply(history, utterance=text, source=source)
            await self.hub.broadcast({"type": "assistant_start", "id": assistant_id})
            async for delta in stream:
                parts.append(delta)
                await self.hub.broadcast(
                    {"type": "assistant_delta", "id": assistant_id, "text": delta}
                )
                if on_delta is not None:
                    on_delta(delta)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except LLMUnavailable as exc:
            error_text = str(exc)
        except Exception:
            log.exception("Échec inattendu d'un tour Assist")
            error_text = "Une erreur interne est survenue — détail dans les journaux du serveur."
        finally:
            full = "".join(parts).strip()
            message = None
            if full:
                with contextlib.suppress(Exception):
                    message = await self.store.add_message("assistant", full, source)
            await self.hub.broadcast(
                {"type": "assistant_end", "id": assistant_id,
                 "message": message, "cancelled": cancelled}
            )
            if error_text:
                await self.hub.broadcast({"type": "error", "text": error_text})
        return full or error_text or "Je n'ai rien à répondre."

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


def _speaker_event(speaker: Speaker) -> dict:
    """Champs du locuteur reconnu, diffusés à l'UI (indicateur « qui parle »)."""
    return {
        "key": speaker.key,
        "name": speaker.name,
        "label": speaker.label,
        "known": speaker.known,
        "is_owner": speaker.is_owner,
        "score": round(speaker.score, 3),
    }


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
    await sentinel.restore_llm_config()
    if sentinel.ha:
        await sentinel.ha.start()
    sentinel.start_proactive()
    if sentinel.routines:
        sentinel.routines.start_scanner()
    if sentinel.reminders:
        sentinel.reminders.start()
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


# ── Agent conversationnel Assist : API compatible OpenAI (Phase 5B) ──────────
#
# Permet à Nova (via l'intégration HACS « Extended OpenAI Conversation ») et donc
# à l'app HA et aux satellites Assist de parler au cerveau de Sentinel. Protégé
# par un jeton porteur ; désactivé si SENTINEL_ASSIST_TOKEN est vide.


def _assist_guard(request: Request) -> JSONResponse | None:
    sentinel: Sentinel = request.app.state.sentinel
    token = sentinel.settings.assist_token
    if not token:
        return JSONResponse({"error": {"message": "Agent Assist désactivé (SENTINEL_ASSIST_TOKEN)."}},
                            status_code=404)
    header = request.headers.get("authorization", "")
    presented = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not presented or not secrets.compare_digest(presented, token):
        return JSONResponse({"error": {"message": "Jeton Assist invalide."}}, status_code=401)
    return None


@app.get("/v1/models")
async def assist_models(request: Request):
    denied = _assist_guard(request)
    if denied is not None:
        return denied
    model = request.app.state.sentinel.settings.model
    return {"object": "list", "data": [
        {"id": model, "object": "model", "created": 0, "owned_by": "sentinel"},
        {"id": "sentinel", "object": "model", "created": 0, "owned_by": "sentinel"},
    ]}


@app.post("/v1/chat/completions")
async def assist_chat(request: Request):
    denied = _assist_guard(request)
    if denied is not None:
        return denied
    sentinel: Sentinel = request.app.state.sentinel
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "Corps JSON invalide."}}, status_code=400)

    messages = body.get("messages") if isinstance(body, dict) else None
    text = ""
    if isinstance(messages, list):
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, list):  # format à blocs éventuel
                    content = " ".join(
                        str(b.get("text") or "") for b in content if isinstance(b, dict)
                    )
                text = str(content or "").strip()
                break
    if not text:
        return JSONResponse(
            {"error": {"message": "Aucun message utilisateur."}}, status_code=400
        )

    model = str(body.get("model") or sentinel.settings.model)

    # Streaming (SSE, compatible OpenAI) : la réponse arrive fragment par fragment.
    # C'est ce que consomme la carte Luna (via l'intégration) pour le rendu « mot
    # à mot ». Sans `stream`, on garde la réponse en un seul bloc (rétrocompat).
    if body.get("stream"):
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"

        async def _sse():
            base = {"id": cid, "object": "chat.completion.chunk",
                    "created": int(time.time()), "model": model}
            # Premier chunk : le rôle, comme le fait l'API OpenAI.
            first = {**base, "choices": [{"index": 0, "delta": {"role": "assistant"},
                                          "finish_reason": None}]}
            yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
            try:
                async for delta in sentinel.run_assist_stream(text):
                    chunk = {**base, "choices": [{"index": 0, "delta": {"content": delta},
                                                  "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            except Exception:  # le flux ne doit jamais planter à mi-course sans clore
                log.exception("Flux Assist interrompu")
            end = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(end, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            _sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    reply = await sentinel.run_assist_reply(text)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": reply},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


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
            "wake_available": sentinel.wake_detector is not None,
            "wake_word": sentinel.settings.wake_model.replace("_", " "),
            "proposals": sorted(pending + deferred, key=lambda p: p["num"]),
            "protocols": [
                {"nom": p.display, "risque": p.risk} for p in sentinel.protocols.all()
            ],
            # Infos moteur et capacités (affichage seul) pour la page Paramètres.
            "engine": {
                "model": sentinel.settings.model,
                "effort": sentinel.settings.effort,
                "max_tokens": sentinel.settings.max_tokens,
                "whisper_model": sentinel.settings.whisper_model,
                "piper_voice": sentinel.settings.piper_voice,
                "tts_engine": sentinel.settings.tts_engine,
                "cloned_tts_voice": sentinel.settings.cloned_tts_voice,
                "wake_model": sentinel.settings.wake_model,
                "tz": sentinel.settings.tz,
            },
            "config": {
                "ha": bool(sentinel.settings.ha_url),
                "assist": bool(sentinel.settings.assist_token),
                "anthropic": bool(sentinel.settings.anthropic_api_key),
                "memory": sentinel.settings.memory_enabled,
                "speaker": sentinel.speaker_embedder is not None,
                "web_search": sentinel.settings.web_search_enabled and bool(sentinel.settings.anthropic_api_key),
                "self_improve": sentinel.settings.self_improve_enabled,
                "proactive": sentinel.proactive is not None,
                "routines": sentinel.routines is not None,
                "music": sentinel.media_cfg is not None,
                "reminders": sentinel.reminders is not None,
                "notify": sentinel.notifier.enabled,
            },
            # Profils vocaux (Phase 2) pour la page Paramètres › Profils vocaux
            "speakers": await sentinel.store.list_speakers(),
            # Suggestions proactives en cours (Phase 7) — tiroir Suggestions
            "proactive": await sentinel.store.list_proactive(),
            "proactive_muted": await sentinel.store.list_proactive_mutes(),
            # Routines (Phase 8) — Paramètres › Routines
            "routines": await sentinel.store.list_routines(),
            # Musique (Phase 9) — tuile lecteur (lecteurs actifs à l'instant)
            "media": sentinel._media_payload()["players"],
            # Minuteurs & rappels (Phase 10) — actifs
            "reminders": await sentinel.store.list_reminders("active"),
            # Fournisseurs LLM (multi-LLM) — Paramètres › Connexions : modèle actif + bascule
            "llm": sentinel.brain.providers_public(),
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
        with contextlib.suppress(Exception):
            await _stop_wake(client)
        log.info("Client %s déconnecté (%d en ligne)", client.id, sentinel.hub.count)


async def _on_audio_chunk(sentinel: Sentinel, client: Client, data: bytes) -> None:
    # Enrôlement d'une empreinte vocale (Phase 2) : l'audio va au tampon dédié.
    if client.enroll_id is not None and client.enroll.active:
        if not client.enroll.add(data):
            await _speaker_enroll_finish(sentinel, client)  # durée max : on prend ce qu'on a
        return

    if client.capture.active:
        if not client.capture.add(data):
            # Durée maximale atteinte : on transcrit ce qui a été capté
            pcm, rate = client.capture.finish()
            await sentinel.hub.send(
                client,
                {"type": "notice", "text": "Durée maximale atteinte, je traite ce que j'ai entendu."},
            )
            await sentinel.start_turn(sentinel.run_voice_turn(client, pcm, rate))
        return

    # Hors capture : l'audio alimente la veille au mot d'éveil, s'il y en a une
    stream = client.wake
    if stream is None:
        return
    if stream.closed:
        # Le service a coupé la session sans détection (redémarrage du conteneur…) :
        # send() se tairait, donc on signale ici pour que l'UI ré-arme.
        client.wake = None
        await sentinel.hub.send(
            client,
            {"type": "wake_error", "text": "La veille au mot d'éveil a été interrompue."},
        )
        return
    try:
        await stream.send(data, client.wake_rate)
    except VoiceServiceError as exc:
        client.wake = None
        await sentinel.hub.send(client, {"type": "wake_error", "text": str(exc)})


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

    elif mtype == "wake_start":
        await _wake_start(sentinel, client, msg)

    elif mtype == "wake_stop":
        await _stop_wake(client)

    elif mtype == "sante":
        await _reply_sante(sentinel, client)

    elif mtype == "historique":
        await _reply_historique(sentinel, client)

    elif mtype == "memoires":
        await sentinel.hub.send(client, await sentinel._memoires_payload())

    elif mtype == "memoire_add":
        await _memoire_add(sentinel, msg)

    elif mtype == "memoire_delete":
        await _memoire_delete(sentinel, msg)

    elif mtype == "speakers":
        await sentinel.hub.send(client, await sentinel._speakers_payload())

    elif mtype == "speaker_add":
        await _speaker_add(sentinel, client, msg)

    elif mtype == "speaker_delete":
        await _speaker_delete(sentinel, msg)

    elif mtype == "speaker_enroll_start":
        await _speaker_enroll_start(sentinel, client, msg)

    elif mtype == "speaker_enroll_end":
        await _speaker_enroll_finish(sentinel, client)

    elif mtype == "evolutions":
        await sentinel.hub.send(client, await sentinel._evolutions_payload())

    elif mtype == "evolution_get":
        await _evolution_get(sentinel, client, msg)

    elif mtype == "evolution_accept":
        await _evolution_action(sentinel, msg, "accepted")

    elif mtype == "evolution_reject":
        await _evolution_action(sentinel, msg, "rejected")

    elif mtype == "evolution_delete":
        await _evolution_action(sentinel, msg, "delete")

    elif mtype == "proactive":
        await sentinel.hub.send(client, await sentinel._proactive_payload())

    elif mtype in ("proactive_make_proposal", "proactive_snooze", "proactive_dismiss",
                   "proactive_mute", "proactive_unmute"):
        await _proactive_action(sentinel, client, msg, mtype)

    elif mtype == "routines":
        await sentinel.hub.send(client, await sentinel._routines_payload())

    elif mtype in ("routine_approve", "routine_reject", "routine_delete",
                   "routine_run", "routine_rename"):
        await _routine_action(sentinel, client, msg, mtype)

    elif mtype == "media":
        await sentinel.hub.send(client, sentinel._media_payload())

    elif mtype == "media_control":
        await _media_control(sentinel, client, msg)

    elif mtype == "reminders":
        await sentinel.hub.send(client, await sentinel._reminders_payload())

    elif mtype == "reminder_cancel":
        rid = str(msg.get("id") or "").strip()
        if rid and (r := await sentinel.store.get_reminder(rid)) and r["status"] == "active":
            await sentinel.store.set_reminder_status(rid, "cancelled")
            await sentinel._broadcast_reminders()

    elif mtype == "notify_test":
        # Notification mobile de test (Phase 14) : vérifier le réglage depuis le cockpit.
        ok = await sentinel.notifier.push(
            "Notification de test — si tu la reçois, c'est bon. 🌙", title="Sentinel"
        )
        await sentinel.hub.send(client, {"type": "notify_test", "ok": ok})

    elif mtype == "llm_providers":
        await sentinel.hub.send(client, sentinel._llm_payload())

    elif mtype == "llm_select":
        await _llm_select(sentinel, client, msg)

    elif mtype == "llm_set_key":
        await _llm_set_key(sentinel, client, msg)

    elif mtype == "llm_set_model":
        await _llm_set_model(sentinel, client, msg)

    elif mtype == "llm_set_params":
        await _llm_set_params(sentinel, client, msg)

    elif mtype == "llm_usage":
        await sentinel.hub.send(client, await sentinel.build_usage_payload())

    elif mtype == "ping":
        await sentinel.hub.send(client, {"type": "pong"})


# ── Fournisseurs LLM : bascule à chaud (Paramètres › Connexions) ─────────────
#
# Choisir le modèle actif est une action du COCKPIT (canal du propriétaire). La
# bascule est purement une préférence de génération : elle ne change RIEN aux
# garde-fous — quel que soit le modèle, tout appel d'outil repasse par la Toolbox
# et le moteur d'actions (niveaux de confiance, « propose puis approuve »). Le
# choix est persisté pour survivre à un redémarrage.


async def _llm_select(sentinel: Sentinel, client: Client, msg: dict) -> None:
    provider_id = str(msg.get("id") or "").strip()
    if not provider_id:
        return
    # Bascule à chaud : `set_provider` valide la disponibilité et flippe l'actif
    # sans reconstruire les clients. On persiste l'actif dans la config LLM pour
    # qu'il survive à un redémarrage (via restore_llm_config → apply_config).
    if sentinel.brain.set_provider(provider_id):
        sentinel._llm_cfg["active"] = provider_id
        await sentinel._persist_llm()
        await sentinel._broadcast_llm()
        label = next(
            (p["label"] for p in sentinel.brain.providers_public()["providers"]
             if p["id"] == provider_id),
            provider_id,
        )
        await sentinel.hub.send(client, {"type": "notice", "text": f"Modèle actif : {label}."})
    else:
        await sentinel.hub.send(
            client,
            {"type": "notice",
             "text": "Ce fournisseur n'est pas disponible (clé API manquante ?)."},
        )


# ── Réglages LLM éditables (Paramètres › Modèles) ────────────────────────────
#
# Le propriétaire connecte les fournisseurs et règle la génération DEPUIS le
# cockpit : coller une clé API, choisir le modèle par fournisseur, régler effort
# / max_tokens / fenêtre d'historique — le tout à chaud, sans .env ni redémarrage.
# Les clés sont stockées côté serveur (Store) et JAMAIS renvoyées à l'UI : la vue
# publique n'expose qu'un booléen « configuré ». Ces réglages ne touchent en rien
# les garde-fous : quel que soit le modèle, tout appel d'outil repasse par la
# Toolbox et le moteur « propose puis approuve ».


async def _llm_set_key(sentinel: Sentinel, client: Client, msg: dict) -> None:
    """Colle (ou efface) la clé API d'un fournisseur. Vide ⇒ retour à la valeur
    d'environnement (.env), le cas échéant. La clé n'est jamais rediffusée."""
    provider_id = str(msg.get("id") or "").strip()
    if not provider_id:
        return
    key = str(msg.get("key") or "").strip()
    providers = sentinel._llm_cfg.setdefault("providers", {})
    entry = providers.setdefault(provider_id, {})
    if key:
        entry["key"] = key
    else:
        entry.pop("key", None)  # vide ⇒ on retombe sur l'environnement
        if not entry:
            providers.pop(provider_id, None)
    await sentinel._apply_llm()
    await sentinel.hub.send(
        client,
        {"type": "notice",
         "text": ("Clé enregistrée." if key else "Clé effacée (retour à l'environnement).")},
    )


async def _llm_set_model(sentinel: Sentinel, client: Client, msg: dict) -> None:
    """Choisit le modèle d'un fournisseur. Vide ⇒ modèle par défaut du fournisseur."""
    provider_id = str(msg.get("id") or "").strip()
    if not provider_id:
        return
    model = str(msg.get("model") or "").strip()
    providers = sentinel._llm_cfg.setdefault("providers", {})
    entry = providers.setdefault(provider_id, {})
    if model:
        entry["model"] = model
    else:
        entry.pop("model", None)
        if not entry:
            providers.pop(provider_id, None)
    await sentinel._apply_llm()


async def _llm_set_params(sentinel: Sentinel, client: Client, msg: dict) -> None:
    """Règle effort / max_tokens / fenêtre d'historique (à chaud). Les valeurs sont
    bornées côté Brain (`apply_config`) ; ici on ne fait qu'écrire ce qui est fourni."""
    params = sentinel._llm_cfg.setdefault("params", {})
    if "effort" in msg:
        params["effort"] = str(msg.get("effort") or "").strip()
    if "max_tokens" in msg:
        params["max_tokens"] = msg.get("max_tokens")
    if "history_window" in msg:
        params["history_window"] = msg.get("history_window")
    await sentinel._apply_llm()


# ── Veille au mot d'éveil (Phase 5A) ─────────────────────────────────────


async def _wake_start(sentinel: Sentinel, client: Client, msg: dict) -> None:
    if sentinel.wake_detector is None:
        await sentinel.hub.send(
            client,
            {"type": "wake_error", "text": "Le mot d'éveil n'est pas configuré (WAKE_HOST)."},
        )
        return
    await _stop_wake(client)  # une session remplace la précédente
    try:
        client.wake_rate = int(msg.get("rate", 16000))
    except (TypeError, ValueError):
        client.wake_rate = 16000

    async def on_detection(name: str) -> None:
        # Ce callback tourne DANS le task lecteur de la session : on se contente
        # de la détacher (le lecteur ferme lui-même sa connexion en sortant) et
        # d'annoncer. Surtout pas de close() ici — cela s'auto-annulerait.
        client.wake = None
        log.info("Mot d'éveil détecté (%s) par le client %s", name or "?", client.id)
        await sentinel.hub.send(client, {"type": "wake", "name": name})

    try:
        client.wake = await sentinel.wake_detector.open(client.wake_rate, on_detection)
    except VoiceServiceError as exc:
        await sentinel.hub.send(client, {"type": "wake_error", "text": str(exc)})


async def _stop_wake(client: Client) -> None:
    stream = client.wake
    client.wake = None
    if stream is not None:
        await stream.close()


# ── Requêtes de lecture des panneaux (réponse au seul client demandeur) ──


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


# ── Mémoire : ajout/suppression manuels par Guillaume (Paramètres › Mémoire) ──
#
# La mémoire est un simple enrichissement de contexte : rien n'agit sur le monde
# réel, donc pas de passage par le moteur « propose puis approuve ». Guillaume
# garde le contrôle direct (il voit, ajoute et supprime), ce qui EST le garde-fou
# pour cette capacité. Après chaque changement, la liste est rediffusée à tous.


async def _memoire_add(sentinel: Sentinel, msg: dict) -> None:
    content = str(msg.get("content") or "").strip()[:500]
    if not content:
        return
    category = normalize_category(msg.get("category"))
    await sentinel.store.add_memory(
        content, category=category, subject="guillaume", source="manuel"
    )
    await sentinel._broadcast_memoires()


async def _memoire_delete(sentinel: Sentinel, msg: dict) -> None:
    mem_id = str(msg.get("id") or "").strip()
    if not mem_id:
        return
    await sentinel.store.delete_memory(mem_id)
    await sentinel._broadcast_memoires()


# ── Auto-amélioration : revue des propositions d'évolution (cockpit) ──────────
#
# Luna PROPOSE (outil proposer_evolution, déjà filtré par selfmod/policy.py) ;
# accepter/rejeter/supprimer sont des actions du COCKPIT (canal du propriétaire) —
# la revue humaine avant toute application, jamais contournée. « Accepter »
# n'exécute RIEN : c'est la décision de Guillaume, qui applique ensuite le diff
# lui-même (git/déploiement). Aucun code n'applique un diff automatiquement.


async def _evolution_get(sentinel: Sentinel, client: Client, msg: dict) -> None:
    sug = await sentinel.store.get_suggestion(str(msg.get("id") or "").strip())
    if sug is None:
        return
    stats = summarize_diff(sug.get("diff") or "")
    await sentinel.hub.send(client, {
        "type": "evolution", "id": sug["id"], "kind": sug["kind"],
        "title": sug["title"], "rationale": sug["rationale"], "target": sug["target"],
        "status": sug["status"], "diff": sug["diff"],
        "added": stats["added"], "removed": stats["removed"], "paths": stats["paths"],
    })


async def _evolution_action(sentinel: Sentinel, msg: dict, action: str) -> None:
    sug_id = str(msg.get("id") or "").strip()
    if not sug_id:
        return
    if action == "delete":
        await sentinel.store.delete_suggestion(sug_id)
    else:  # accepted | rejected — décision humaine, n'applique aucun code
        await sentinel.store.decide_suggestion(sug_id, action)
    await sentinel._broadcast_evolutions()


# ── Proactivité : décisions du cockpit sur les suggestions (Phase 7) ──────────
#
# « Préparer la proposition » ne fait que créer une PROPOSITION (moteur d'actions),
# que Guillaume approuve ensuite — deux gestes humains, aucune exécution auto. Les
# autres actions (plus tard / ignorer / ne plus suggérer) ne font que ranger.


async def _proactive_action(sentinel: Sentinel, client: Client, msg: dict, mtype: str) -> None:
    if sentinel.proactive is None:
        return
    if mtype in ("proactive_mute", "proactive_unmute"):
        rule = str(msg.get("rule") or "").strip()
        if mtype == "proactive_mute":
            await sentinel.proactive.mute(rule)
        else:
            await sentinel.proactive.unmute(rule)
        return
    sug_id = str(msg.get("id") or "").strip()
    if not sug_id:
        return
    if mtype == "proactive_make_proposal":
        message = await sentinel.proactive.make_proposal(sug_id)
        await sentinel.hub.send(client, {"type": "notice", "text": message})
    elif mtype == "proactive_snooze":
        await sentinel.proactive.snooze(sug_id)
    elif mtype == "proactive_dismiss":
        await sentinel.proactive.dismiss(sug_id)


# ── Scénarios & routines : revue et déclenchement (cockpit) ──────────────────
#
# Luna PROPOSE (outil proposer_routine, ou détection d'habitude) ; activer /
# rejeter / renommer / supprimer sont des actions du COCKPIT (revue humaine avant
# qu'une routine puisse se déclencher). Lancer exécute des actions COURANTES
# uniquement (aucune sensible ne peut être dans une routine, cf. routines/safety.py).


async def _routine_action(sentinel: Sentinel, client: Client, msg: dict, mtype: str) -> None:
    if sentinel.routines is None:
        return
    routine_id = str(msg.get("id") or "").strip()
    if not routine_id:
        return
    if mtype == "routine_approve":
        await sentinel.routines.approve(routine_id)
    elif mtype == "routine_reject":
        await sentinel.routines.reject(routine_id)
    elif mtype == "routine_delete":
        await sentinel.routines.delete(routine_id)
    elif mtype == "routine_rename":
        await sentinel.routines.rename(routine_id, str(msg.get("name") or ""))
    elif mtype == "routine_run":
        routine = await sentinel.store.get_routine(routine_id)
        if routine is None or routine["status"] != "active":
            await sentinel.hub.send(client, {"type": "notice", "text": "Cette routine n'est pas active."})
            return
        text, _ = await sentinel.routines.run(routine, utterance="depuis l'interface", source="ui")
        await sentinel.hub.send(client, {"type": "notice", "text": text})


# ── Musique : contrôle du lecteur depuis le cockpit (Phase 9) ────────────────
#
# Chaque commande passe par le MOTEUR (run_direct → ha.media), comme la musique à
# la voix. Actions courantes, non sensibles ; l'état est rediffusé aussitôt.


async def _media_control(sentinel: Sentinel, client: Client, msg: dict) -> None:
    if sentinel.media_cfg is None or sentinel.engine is None:
        return
    op = str(msg.get("op") or "")
    ids = [e for e in (msg.get("entity_ids") or []) if isinstance(e, str)]
    if not op or not ids:
        return
    params: dict = {"op": op, "entity_ids": ids}
    if op == "volume":
        try:
            params["level"] = max(0.0, min(1.0, float(msg.get("level"))))
        except (TypeError, ValueError):
            return
    elif op == "source":
        params["source"] = str(msg.get("source") or "")
    elif op == "join":
        params["group_members"] = [e for e in (msg.get("group_members") or []) if isinstance(e, str)]
    outcome = await sentinel.engine.run_direct(
        "ha.media", params, utterance="depuis l'interface", source="ui"
    )
    if not outcome.ok:
        await sentinel.hub.send(client, {"type": "notice", "text": outcome.text})
    await sentinel._broadcast_media()


# ── Profils vocaux : création, suppression, enrôlement (Paramètres › Profils) ──
#
# Géré depuis le cockpit (canal de confiance, comme les décisions sensibles). Le
# service ne fait que « audio → empreinte » ; core stocke et compare. La
# reconnaissance ne débloque jamais rien de sensible : voir docs/PHASE2-LOCUTEUR.md.


async def _speaker_add(sentinel: Sentinel, client: Client, msg: dict) -> None:
    name = str(msg.get("name") or "").strip()[:60]
    if not name:
        return
    await sentinel.store.add_speaker(name, is_owner=bool(msg.get("is_owner")))
    await sentinel._broadcast_speakers()


async def _speaker_delete(sentinel: Sentinel, msg: dict) -> None:
    speaker_id = str(msg.get("id") or "").strip()
    if not speaker_id:
        return
    await sentinel.store.delete_speaker(speaker_id)
    await sentinel._broadcast_speakers()


async def _speaker_enroll_start(sentinel: Sentinel, client: Client, msg: dict) -> None:
    speaker_id = str(msg.get("id") or "").strip()
    if sentinel.speaker_embedder is None:
        await sentinel.hub.send(
            client,
            {"type": "enroll_result", "ok": False,
             "text": "La reconnaissance de locuteur n'est pas activée (SPEAKER_HOST)."},
        )
        return
    if not speaker_id or await sentinel.store.get_speaker(speaker_id) is None:
        await sentinel.hub.send(
            client, {"type": "enroll_result", "ok": False, "text": "Profil inconnu."}
        )
        return
    try:
        rate = int(msg.get("rate", 16000))
    except (TypeError, ValueError):
        rate = 16000
    client.enroll_id = speaker_id
    client.enroll.start(rate=rate)


async def _speaker_enroll_finish(sentinel: Sentinel, client: Client) -> None:
    speaker_id = client.enroll_id
    client.enroll_id = None
    if speaker_id is None:
        return
    pcm, rate = client.enroll.finish()
    if sentinel.speaker_embedder is None or not pcm:
        await sentinel.hub.send(
            client, {"type": "enroll_result", "ok": False, "text": "Échantillon vide."}
        )
        return
    try:
        vector = await sentinel.speaker_embedder.embed(pcm, rate=rate)
        await sentinel.store.add_speaker_sample(speaker_id, vector)
    except VoiceServiceError as exc:
        await sentinel.hub.send(client, {"type": "enroll_result", "ok": False, "text": str(exc)})
        return
    except Exception:
        log.exception("Enrôlement d'empreinte en échec")
        await sentinel.hub.send(
            client, {"type": "enroll_result", "ok": False, "text": "Enrôlement impossible."}
        )
        return
    await sentinel.hub.send(client, {"type": "enroll_result", "ok": True, "text": "Échantillon enregistré."})
    await sentinel._broadcast_speakers()


# L'UI statique en dernier : les routes déclarées avant restent prioritaires.
app.mount("/", StaticFiles(directory=find_ui_dir(), html=True), name="ui")
