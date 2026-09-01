"""Le cerveau LLM : conversation en streaming avec Claude, outillée (Phase 2).

Boucle d'outils manuelle : le texte est diffusé au fil de l'eau (UI + voix) ;
quand Claude demande des outils, la Toolbox les exécute — la lecture est libre,
toute écriture passe par le moteur d'actions — puis la conversation continue,
avec un plafond de tours pour ne jamais boucler.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from ..config import Settings
from ..norm import date_francaise
from .toolbox import ACTIVITY_LABELS, Toolbox

log = logging.getLogger("sentinel.brain")

MAX_TOOL_ROUNDS = 6


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
Tu es Sentinel, l'assistant personnel de Guillaume — le majordome numérique de la \
maison, dans l'esprit de Jarvis : calme, précis, efficace, avec une pointe d'humour \
sobre et rare.

Contexte : tu es auto-hébergé sur Nebula, le serveur Unraid de Guillaume. Tu pilotes \
la maison via Nova (son Home Assistant) grâce à tes outils. On te parle à la voix ou \
par écrit ; les deux partagent la même conversation.

Style : réponds toujours en français, en phrases courtes et naturelles — tes réponses \
sont le plus souvent lues à voix haute. Pas de listes, de tableaux, de titres ni de \
code, sauf si Guillaume demande explicitement un contenu écrit ou technique. Va droit \
au but, sans préambule ni formule de politesse finale. Tutoie Guillaume.

Tes capacités actuelles (Phase 2 de ta construction) :
- Lire l'état de la maison avec tes outils (pièces, lumières, capteurs, alarme…).
- Agir sur la domotique courante quand Guillaume le demande explicitement : lumières, \
volets, scènes, verrouillage, protocoles.
- Pour toute autre modification (n'importe quel service Home Assistant, réglages…), \
tu ne peux PAS agir directement : crée une proposition avec creer_proposition, que \
Guillaume approuvera ou refusera. C'est une règle de sécurité technique, jamais \
contournable, et c'est voulu.
- Pas encore disponible (phases suivantes) : surveillance des serveurs (Nebula, \
Atrium, PC), aide au développement, mises à jour. Dis-le simplement si on te le \
demande.

Règles d'usage des outils :
- N'invente jamais un entity_id ni un nom de pièce : vérifie avec etat_maison ou \
liste_pieces au moindre doute.
- N'agis que sur demande explicite de Guillaume — de ta propre initiative, tu \
proposes, tu n'exécutes pas.
- Le déverrouillage et le désarmement sont sensibles : tes outils ne les font pas. \
Invite Guillaume à donner l'ordre directement à la voix (il devra confirmer), et \
mentionne que c'est le protocole de sécurité.
- Après une action réussie, confirme en une phrase courte et naturelle.
- Si un outil échoue, dis-le simplement et propose la suite utile.
"""


def _system_blocks(settings: Settings) -> list[dict]:
    try:
        tz = ZoneInfo(settings.tz)
    except Exception:  # tzdata absente ou TZ invalide : on ne casse pas un tour pour ça
        tz = None
    now = datetime.now(tz)
    date_fr = (
        f"Nous sommes le {date_francaise(now)} et il est "
        f"{now.strftime('%H:%M')} ({settings.tz})."
    )
    return [
        # Bloc stable en premier + cache : les tours suivants relisent le cache
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        # Bloc variable (date/heure) après le point de cache
        {"type": "text", "text": date_fr},
    ]


class Brain:
    def __init__(
        self,
        settings: Settings,
        toolbox: Toolbox | None = None,
        on_activity: Callable[[str], Awaitable[None]] | None = None,
    ):
        self._settings = settings
        self._toolbox = toolbox
        self._on_activity = on_activity
        self._client: anthropic.AsyncAnthropic | None = (
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key
            else None
        )

    async def stream_reply(
        self, history: list[dict], *, utterance: str = "", source: str = "text"
    ) -> AsyncIterator[str]:
        """Produit la réponse de Sentinel en streaming, outils compris.

        `history` : [{"role": "user"|"assistant", "content": …}], premier rôle
        `user`, dernier = message courant. `utterance` (la phrase d'origine) est
        transmise au moteur d'actions pour journaliser l'autorisation.
        """
        if self._client is None:
            raise LLMUnavailable(
                "Aucune clé API Anthropic n'est configurée : renseigne "
                "ANTHROPIC_API_KEY dans le fichier .env puis redémarre Sentinel."
            )
        s = self._settings
        messages: list[dict] = list(history)
        tools = self._toolbox.specs() if self._toolbox else None

        try:
            for round_no in range(MAX_TOOL_ROUNDS):
                kwargs: dict = dict(
                    model=s.model,
                    max_tokens=s.max_tokens,
                    system=_system_blocks(s),
                    output_config={"effort": s.effort},
                    messages=messages,
                )
                if tools:
                    kwargs["tools"] = tools

                async with self._client.messages.stream(**kwargs) as stream:
                    async for text in stream.text_stream:
                        yield text
                    final = await stream.get_final_message()

                if final.stop_reason != "tool_use" or not self._toolbox:
                    return

                if round_no == MAX_TOOL_ROUNDS - 1:
                    # Budget épuisé : on n'exécute PAS des actions dont le modèle
                    # ne verrait jamais le résultat.
                    yield (
                        "\n(Je m'arrête là — trop d'étapes d'outils pour une seule "
                        "demande. Rien n'a été exécuté à la dernière étape.)"
                    )
                    return

                # Tour d'outils : exécuter puis renvoyer les résultats
                messages.append({"role": "assistant", "content": final.content})
                results = []
                for block in final.content:
                    if block.type != "tool_use":
                        continue
                    await self._notify_activity(block.name)
                    content, is_error = await self._toolbox.run(
                        block.name, dict(block.input or {}), utterance=utterance, source=source
                    )
                    item: dict = {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                    }
                    if is_error:
                        item["is_error"] = True
                    results.append(item)
                messages.append({"role": "user", "content": results})

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

    async def _notify_activity(self, tool_name: str) -> None:
        if self._on_activity:
            try:
                await self._on_activity(ACTIVITY_LABELS.get(tool_name, "utilise un outil…"))
            except Exception:
                log.exception("Notification d'activité impossible")
