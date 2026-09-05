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

    # Surveillance (Phase 3A) — vides = moniteurs correspondants désactivés
    docker_proxy_url: str = ""
    docker_restart_url: str = ""
    atrium_url: str = ""
    daily_report: str = ""            # "HH:MM" heure locale, vide = pas de rapport
    container_mem_mo: int = 1500      # seuil mémoire signalé dans les audits

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
            data_dir=data_dir,
            ui_dir=find_ui_dir(),
            config_dir=Path(os.environ.get("SENTINEL_CONFIG_DIR", "")) if os.environ.get("SENTINEL_CONFIG_DIR") else _find_dir("config"),
            tz=os.environ.get("TZ", "Europe/Paris"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            ha_url=os.environ.get("HA_URL", "").strip().rstrip("/"),
            ha_token=os.environ.get("HA_TOKEN", "").strip(),
            docker_proxy_url=os.environ.get("DOCKER_PROXY_URL", "").strip().rstrip("/"),
            docker_restart_url=os.environ.get("DOCKER_RESTART_URL", "").strip().rstrip("/"),
            atrium_url=os.environ.get("ATRIUM_URL", "").strip().rstrip("/"),
            daily_report=os.environ.get("SENTINEL_DAILY_REPORT", "").strip(),
            container_mem_mo=_int(os.environ.get("SENTINEL_CONTAINER_MEM_MO"), 1500),
        )
