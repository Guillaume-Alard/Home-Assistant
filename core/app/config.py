"""Configuration de Sentinel, lue depuis les variables d'environnement.

En production, docker compose injecte le fichier `.env` (voir `.env.example`).
En développement local, exporter les variables ou sourcer `.env` avant lancement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def _find_dir(name: str) -> Path:
    # Conteneur : /opt/sentinel/app/config.py → /opt/sentinel/<name>
    # Dépôt     : core/app/config.py         → <racine>/<name>
    here = Path(__file__).resolve()
    for candidate in (here.parents[1] / name, here.parents[2] / name):
        if candidate.is_dir():
            return candidate
    return here.parents[1] / name


def find_ui_dir() -> Path:
    return _find_dir("ui")


@dataclass(frozen=True)
class Settings:
    # LLM
    anthropic_api_key: str
    model: str
    max_tokens: int
    effort: str
    history_window: int

    # Voix (services Wyoming)
    whisper_host: str
    whisper_port: int
    piper_host: str
    piper_port: int
    # Affichage seul (le modèle STT et la voix TTS sont passés aux conteneurs
    # Wyoming ; on les reçoit ici uniquement pour l'afficher dans les Paramètres)
    whisper_model: str
    piper_voice: str
    # Mot d'éveil (Phase 5A) — WAKE_HOST vide = désactivé
    wake_host: str
    wake_port: int
    wake_model: str

    # Reconnaissance de locuteur (Phase 2) — SPEAKER_HOST vide = désactivée.
    # Le service extrait une empreinte vocale ; core compare aux profils enrôlés.
    speaker_host: str
    speaker_port: int
    speaker_threshold: float

    # Voix de Luna (clonage local) — TTS_ENGINE=cloned pour l'activer ; sinon Piper.
    # Le serveur clone est appelé via une API compatible OpenAI (/v1/audio/speech).
    # Repli automatique sur Piper si le serveur est indisponible.
    tts_engine: str
    cloned_tts_url: str
    cloned_tts_voice: str
    cloned_tts_model: str
    cloned_tts_rate: int

    # Réseau / stockage — le TLS lui-même est géré hors application
    # (entrypoint.sh + healthcheck.py lisent SENTINEL_TLS directement)
    data_dir: Path
    ui_dir: Path
    config_dir: Path
    tz: str
    log_level: str

    # Home Assistant (Nova) — vide = fonctionnalités domotiques désactivées
    ha_url: str = ""
    ha_token: str = ""

    # Agent conversationnel Assist (Phase 5B) — vide = endpoint /v1 désactivé
    assist_token: str = ""

    # Mémoire persistante (Phase 1) — SENTINEL_MEMORY=off coupe l'injection du profil
    # dans le prompt (les souvenirs restent stockés) ; memory_window borne le nombre
    # de souvenirs les plus récents injectés à chaque tour.
    memory_enabled: bool = True
    memory_window: int = 60

    # Recherche web (Phase 4) — outil natif Anthropic (web_search), citations
    # intégrées. Contrôlée : plafond d'usages par tour ; réservée aux personnes
    # reconnues (owner + maisonnée). SENTINEL_WEB_SEARCH=off pour la désactiver.
    web_search_enabled: bool = True
    web_search_max_uses: int = 5

    # Auto-amélioration encadrée (Phase 6) — Luna PROPOSE des diffs sur son propre
    # code / sa config, relus et appliqués par Guillaume ; jamais d'exécution
    # automatique, jamais de garde-fou de sécurité modifiable. SENTINEL_SELF_IMPROVE=off
    # retire les outils correspondants (les propositions existantes restent lisibles).
    self_improve_enabled: bool = True

    # Proactivité contextuelle (Phase 7) — Luna observe l'état de la maison + l'heure
    # + ce qu'elle sait de toi, et te SUGGÈRE (jamais n'exécute) : au mieux une
    # proposition à valider. SENTINEL_PROACTIVE=off la coupe entièrement.
    proactive_enabled: bool = True
    proactive_interval: int = 150  # secondes entre deux évaluations de fond

    # Scénarios & routines (Phase 8) — Luna PROPOSE des routines (séquences
    # d'actions nommées) depuis tes habitudes ou la conversation ; tu les ACTIVES
    # puis les déclenches. Jamais d'action sensible dans une routine. Off = plus
    # de détection d'habitudes ni d'outils de routine (les routines actives restent).
    routines_enabled: bool = True
    routine_habit_min_days: int = 3    # nb de jours distincts pour qu'une habitude compte
    routine_habit_lookback: int = 21   # fenêtre d'analyse (jours)

    # Musique multi-pièces (Phase 9) — pilotage des media_player de Nova (Spotify,
    # enceintes…) via leurs services standard. Aucun compte en plus. Off = outils
    # et tuile musique retirés. Préréglages : config/media.yml.
    music_enabled: bool = True

    # Minuteurs & rappels vocaux (Phase 10) — 100% local. Off = outils retirés.
    reminders_enabled: bool = True

    # Notifications mobiles (Phase 14) — Luna te joint sur ton téléphone via l'app
    # Home Assistant. `notify_service` = le service Nova (« mobile_app_xxx »), vide
    # = désactivé. Les bascules disent QUOI pousser : rappels qui sonnent, alertes
    # de sécurité. Communication seule — jamais de pilotage.
    notify_service: str = ""
    notify_reminders: bool = True
    notify_alerts: bool = True

    # Garde-fous
    max_utterance_seconds: int = 60
    wyoming_timeout_seconds: int = 120

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("SENTINEL_DATA_DIR", "./data")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)

        effort = os.environ.get("SENTINEL_EFFORT", "low").strip().lower()
        if effort not in ("low", "medium", "high"):
            effort = "low"

        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
            model=os.environ.get("SENTINEL_MODEL", "claude-sonnet-5").strip(),
            max_tokens=_int(os.environ.get("SENTINEL_MAX_TOKENS"), 1024),
            effort=effort,
            history_window=_int(os.environ.get("SENTINEL_HISTORY_WINDOW"), 30),
            whisper_host=os.environ.get("WHISPER_HOST", "sentinel-whisper"),
            whisper_port=_int(os.environ.get("WHISPER_PORT"), 10300),
            piper_host=os.environ.get("PIPER_HOST", "sentinel-piper"),
            piper_port=_int(os.environ.get("PIPER_PORT"), 10200),
            whisper_model=os.environ.get("WHISPER_MODEL", "small-int8").strip() or "small-int8",
            piper_voice=os.environ.get("PIPER_VOICE", "fr_FR-siwis-medium").strip() or "fr_FR-siwis-medium",
            wake_host=os.environ.get("WAKE_HOST", "").strip(),
            wake_port=_int(os.environ.get("WAKE_PORT"), 10400),
            wake_model=os.environ.get("WAKEWORD_MODEL", "hey_jarvis").strip() or "hey_jarvis",
            speaker_host=os.environ.get("SPEAKER_HOST", "").strip(),
            speaker_port=_int(os.environ.get("SPEAKER_PORT"), 10500),
            speaker_threshold=_float(os.environ.get("SPEAKER_THRESHOLD"), 0.75),
            tts_engine=(os.environ.get("TTS_ENGINE", "piper").strip().lower() or "piper")
            if os.environ.get("TTS_ENGINE", "piper").strip().lower() in ("piper", "cloned")
            else "piper",
            cloned_tts_url=os.environ.get("CLONED_TTS_URL", "").strip().rstrip("/"),
            cloned_tts_voice=os.environ.get("CLONED_TTS_VOICE", "luna").strip() or "luna",
            cloned_tts_model=os.environ.get("CLONED_TTS_MODEL", "tts-1").strip() or "tts-1",
            cloned_tts_rate=_int(os.environ.get("CLONED_TTS_RATE"), 24000),
            data_dir=data_dir,
            ui_dir=find_ui_dir(),
            config_dir=Path(os.environ.get("SENTINEL_CONFIG_DIR", "")) if os.environ.get("SENTINEL_CONFIG_DIR") else _find_dir("config"),
            tz=os.environ.get("TZ", "Europe/Paris"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            ha_url=os.environ.get("HA_URL", "").strip().rstrip("/"),
            ha_token=os.environ.get("HA_TOKEN", "").strip(),
            assist_token=os.environ.get("SENTINEL_ASSIST_TOKEN", "").strip(),
            memory_enabled=os.environ.get("SENTINEL_MEMORY", "on").strip().lower()
            not in ("off", "0", "false", "no", "non"),
            memory_window=_int(os.environ.get("SENTINEL_MEMORY_WINDOW"), 60),
            web_search_enabled=os.environ.get("SENTINEL_WEB_SEARCH", "on").strip().lower()
            not in ("off", "0", "false", "no", "non"),
            web_search_max_uses=_int(os.environ.get("SENTINEL_WEB_SEARCH_MAX"), 5),
            self_improve_enabled=os.environ.get("SENTINEL_SELF_IMPROVE", "on").strip().lower()
            not in ("off", "0", "false", "no", "non"),
            proactive_enabled=os.environ.get("SENTINEL_PROACTIVE", "on").strip().lower()
            not in ("off", "0", "false", "no", "non"),
            proactive_interval=_int(os.environ.get("SENTINEL_PROACTIVE_INTERVAL"), 150),
            routines_enabled=os.environ.get("SENTINEL_ROUTINES", "on").strip().lower()
            not in ("off", "0", "false", "no", "non"),
            routine_habit_min_days=_int(os.environ.get("SENTINEL_ROUTINE_MIN_DAYS"), 3),
            routine_habit_lookback=_int(os.environ.get("SENTINEL_ROUTINE_LOOKBACK"), 21),
            music_enabled=os.environ.get("SENTINEL_MUSIC", "on").strip().lower()
            not in ("off", "0", "false", "no", "non"),
            reminders_enabled=os.environ.get("SENTINEL_REMINDERS", "on").strip().lower()
            not in ("off", "0", "false", "no", "non"),
            notify_service=os.environ.get("SENTINEL_NOTIFY_SERVICE", "").strip(),
            notify_reminders=os.environ.get("SENTINEL_NOTIFY_REMINDERS", "on").strip().lower()
            not in ("off", "0", "false", "no", "non"),
            notify_alerts=os.environ.get("SENTINEL_NOTIFY_ALERTS", "on").strip().lower()
            not in ("off", "0", "false", "no", "non"),
        )

    @property
    def notify_enabled(self) -> bool:
        # Notifications mobiles actives dès qu'un service Nova est déclaré.
        return bool(self.notify_service)
