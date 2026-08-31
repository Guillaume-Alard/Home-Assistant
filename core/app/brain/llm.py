"""Le cerveau LLM : conversation en streaming avec Claude (API Anthropic).

Phase 1 : conversation pure, sans outils. Les outils (Home Assistant, Docker…)
arrivent en Phase 2+ et passeront tous par le moteur d'actions.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from ..config import Settings

log = logging.getLogger("sentinel.brain")


class LLMUnavailable(RuntimeError):
    """Erreur LLM — le message (en français) est montré tel quel à l'utilisateur."""


def _api_error_message(status_code: int, body: object) -> str:
    """Traduit une erreur API en message utilisateur, sans perdre le détail.

    Le corps d'une erreur Anthropic est {"error": {"type": …, "message": …}} ;
    ce message dit presque toujours la vraie cause (crédit épuisé, requête
    invalide…) — on l'affiche plutôt que de le cacher derrière un code.
    """
    detail = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message") or "").strip()

    if "credit balance" in detail.lower():
        return (
            "Le crédit de la clé API Anthropic est épuisé — recharge le compte sur "
            "console.anthropic.com (Plans & Billing) puis réessaie."
        )
    base = f"L'API Anthropic a renvoyé une erreur ({status_code})"
    return f"{base} : {detail}" if detail else f"{base}."


# Bloc stable, mis en cache côté API (cache_control) : ne rien y mettre de variable.
SYSTEM_PROMPT = """\
Tu es Sentinel, l'assistant personnel de Guillaume — un majordome numérique dans \
l'esprit de Jarvis : calme, précis, efficace, avec une pointe d'humour sobre et rare.

Contexte : tu es auto-hébergé sur Nebula, le serveur Unraid de Guillaume, chez lui. \
On te parle à la voix ou par écrit, depuis un navigateur ; voix et écrit partagent \
la même conversation.

Règles :
- Réponds toujours en français.
- Tes réponses sont le plus souvent lues à voix haute : fais des phrases courtes et \
naturelles. Pas de listes, de tableaux, de titres ni de code, sauf si Guillaume \
demande explicitement un contenu écrit ou technique.
- Va droit au but : l'essentiel d'abord, pas de préambule ni de conclusion de politesse.
- Sois honnête sur tes capacités actuelles : tu es en Phase 1 de ta construction. \
Tu ne peux pas encore agir sur la maison ni sur les serveurs — le pilotage de la \
domotique (via Nova, le Home Assistant de Guillaume), les protocoles comme \
« Forteresse » et la surveillance des systèmes arrivent dans les phases suivantes. \
Si on te demande une action de ce type, dis-le simplement et réponds au mieux \
avec tes connaissances.
- Tutoie Guillaume.
"""


def _system_blocks(settings: Settings) -> list[dict]:
    try:
        tz = ZoneInfo(settings.tz)
    except Exception:  # tzdata absente ou TZ invalide : on ne casse pas un tour pour ça
        tz = None
    now = datetime.now(tz)
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    mois = [
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ]
    date_fr = (
        f"Nous sommes le {jours[now.weekday()]} {now.day} {mois[now.month - 1]} "
        f"{now.year} et il est {now.strftime('%H:%M')} ({settings.tz})."
    )
    return [
        # Bloc stable en premier + cache : les tours suivants relisent le cache
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        # Bloc variable (date/heure) après le point de cache
        {"type": "text", "text": date_fr},
    ]


class Brain:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: anthropic.AsyncAnthropic | None = (
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key
            else None
        )

    async def stream_reply(self, history: list[dict]) -> AsyncIterator[str]:
        """Produit la réponse de Sentinel en streaming (deltas de texte).

        `history` : [{"role": "user"|"assistant", "content": str}, …],
        premier message de rôle `user`, dernier = message courant.
        """
        if self._client is None:
            raise LLMUnavailable(
                "Aucune clé API Anthropic n'est configurée : renseigne "
                "ANTHROPIC_API_KEY dans le fichier .env puis redémarre Sentinel."
            )
        s = self._settings
        try:
            async with self._client.messages.stream(
                model=s.model,
                max_tokens=s.max_tokens,
                system=_system_blocks(s),
                output_config={"effort": s.effort},
                messages=history,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except anthropic.AuthenticationError as exc:
            log.error("Authentification API refusée : %s", exc)
            raise LLMUnavailable(
                "La clé API Anthropic est invalide ou révoquée (ANTHROPIC_API_KEY)."
            ) from exc
        except anthropic.RateLimitError as exc:
            log.warning("Limite de débit API atteinte : %s", exc)
            raise LLMUnavailable(
                "L'API Anthropic limite le débit pour l'instant — réessaie dans un moment."
            ) from exc
        except anthropic.APIStatusError as exc:
            body = getattr(exc, "body", None)
            log.error("Erreur API Anthropic %s — corps : %r", exc.status_code, body or exc)
            raise LLMUnavailable(_api_error_message(exc.status_code, body)) from exc
        except anthropic.APIConnectionError as exc:
            log.error("API Anthropic injoignable : %s", exc)
            raise LLMUnavailable(
                "Impossible de joindre l'API Anthropic — vérifie l'accès Internet de Nebula."
            ) from exc
