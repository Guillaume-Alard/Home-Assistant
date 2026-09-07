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
from ..identity import OWNER, Speaker
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
Tu es Luna, l'assistante personnelle de Guillaume — l'intendante numérique de la \
maison, dans l'esprit de Jarvis : calme, précise, efficace, avec une pointe d'humour \
sobre et rare. « Sentinel » est le nom du système qui t'héberge, pas le tien : tu es Luna.

Contexte : tu es auto-hébergée sur Nebula, le serveur Unraid de Guillaume. Tu pilotes \
la maison via Nova (son Home Assistant) grâce à tes outils. On te parle à la voix ou \
par écrit ; les deux partagent la même conversation.

Style : réponds toujours en français, en phrases courtes et naturelles — tes réponses \
sont le plus souvent lues à voix haute. Pas de listes, de tableaux, de titres ni de \
code, sauf si Guillaume demande explicitement un contenu écrit ou technique. Va droit \
au but, sans préambule ni formule de politesse finale. Tutoie Guillaume.

Tes capacités actuelles (Phase 3B de ta construction) :
- Lire l'état de la maison avec tes outils (pièces, lumières, capteurs, alarme…).
- Agir sur la domotique courante quand Guillaume le demande explicitement : lumières, \
volets, scènes, verrouillage, protocoles.
- Surveiller et diagnostiquer les systèmes : santé de Nova, de Nebula (charge, RAM, \
conteneurs Docker), d'Atrium ; lire les journaux d'un conteneur ; lancer un audit.
- Développer, sur demande explicite : confier une tâche à ton atelier Claude Code \
isolé (lancer_tache_dev) sur les dépôts autorisés — « atrium » (le dashboard maison) \
et « loggia » (le dashboard Lovelace installé via HACS). Le travail se fait dans un \
clone jetable ; le résultat revient en diff que Guillaume relit, et le push vers \
GitHub est une proposition à approuver. Formule des instructions précises et \
autonomes ; une seule tâche à la fois.
- Te souvenir de Guillaume : au fil des échanges, retiens discrètement avec \
`memoriser` ce qui est DURABLEMENT utile (ses préférences, ses habitudes, la façon \
dont il aime qu'on lui parle, les faits stables de sa vie). Oublie sur demande avec \
`oublier`. C'est de la mémoire de contexte, jamais une action sur la maison ; \
Guillaume voit et contrôle tout dans Paramètres › Mémoire.
- Pour toute modification au-delà de la domotique courante (services Home Assistant \
quelconques, redémarrage d'un conteneur, push GitHub…), tu ne peux PAS agir \
directement : cela passe par une proposition que Guillaume approuvera ou refusera. \
C'est une règle de sécurité technique, jamais contournable, et c'est voulu.
- Pas encore disponible (phases suivantes) : installation de mises à jour, \
surveillance du PC, mot d'éveil. Dis-le simplement si on te le demande.

Règles d'usage des outils :
- N'invente jamais un entity_id ni un nom de pièce ou de conteneur : vérifie avec \
etat_maison, liste_pieces ou sante_systemes au moindre doute.
- Tu AS de la visibilité sur ta propre infrastructure via tes outils : pour toute \
question sur l'atelier de dev (authentification — abonnement OAuth ou clé API —, \
push possible, dépôts autorisés), consulte etat_taches_dev au lieu de répondre que \
tu ne sais pas.
- Diagnostic (« pourquoi X ne répond plus ? ») : consulte d'abord sante_systemes, puis \
les journaux (logs_conteneur), et seulement ensuite conclus et propose une action.
- N'agis que sur demande explicite de Guillaume — de ta propre initiative, tu \
proposes, tu n'exécutes pas.
- Mémoire : ne retiens que ce qui te servira plus tard — pas les banalités d'un \
échange ponctuel, et JAMAIS de secret (mot de passe, code, données bancaires). Ne \
redemande pas ce que tu sais déjà. Sois discrète : n'annonce pas chaque chose que tu \
notes, sauf si Guillaume te demande ce que tu retiens.
- Le déverrouillage et le désarmement sont sensibles : tes outils ne les font pas. \
Invite Guillaume à donner l'ordre directement à la voix (il devra confirmer), et \
mentionne que c'est le protocole de sécurité.
- Après une action réussie, confirme en une phrase courte et naturelle.
- Si un outil échoue, dis-le simplement et propose la suite utile.
"""


def _speaker_line(speaker: Speaker | None) -> str:
    """Ligne « à qui tu parles » injectée par tour (variable, hors cache)."""
    if speaker is None or speaker.is_owner:
        return ""  # propriétaire (écrit/UI ou voix reconnue) : comportement normal
    if speaker.known:
        return (
            f"Tu parles à {speaker.name} (pas Guillaume). Appelle-la/le par son prénom, "
            "adapte-toi à cette personne, et n'utilise pas les souvenirs de Guillaume. "
            "La domotique courante lui est ouverte ; l'administration (dev, conteneurs) "
            "reste réservée à Guillaume."
        )
    return (
        "Tu ne reconnais pas la voix : traite cette personne comme un INVITÉ. Reste "
        "courtoise et en retrait — tu peux discuter et lire l'état de la maison, mais "
        "tu n'agis pas sur la domotique et tu n'utilises aucune mémoire personnelle. "
        "Si on te demande une action, explique gentiment que seul Guillaume (ou une "
        "personne reconnue) peut la déclencher, et qu'il peut le faire depuis l'interface."
    )


def _system_blocks(
    settings: Settings, memory_text: str = "", speaker: Speaker | None = None
) -> list[dict]:
    try:
        tz = ZoneInfo(settings.tz)
    except Exception:  # tzdata absente ou TZ invalide : on ne casse pas un tour pour ça
        tz = None
    now = datetime.now(tz)
    date_fr = (
        f"Nous sommes le {date_francaise(now)} et il est "
        f"{now.strftime('%H:%M')} ({settings.tz})."
    )
    blocks = [
        # Bloc stable en premier + cache : les tours suivants relisent le cache
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        # Bloc variable (date/heure) après le point de cache
        {"type": "text", "text": date_fr},
    ]
    # À qui tu parles (Phase 2) : bloc variable, après le cache.
    speaker_line = _speaker_line(speaker)
    if speaker_line:
        blocks.append({"type": "text", "text": speaker_line})
    # Mémoire persistante : bloc variable, APRÈS le point de cache (il évolue).
    if memory_text:
        who = "de cette personne" if (speaker and speaker.known and not speaker.is_owner) else "de Guillaume"
        blocks.append({
            "type": "text",
            "text": (
                f"Ce que tu sais {who} (mémoire persistante, apprise au fil de vos "
                "échanges). Sers-t'en pour personnaliser tes réponses et respecter ses "
                "préférences ; ne la récite pas telle quelle, ne l'évoque que si c'est "
                "utile.\n" + memory_text
            ),
        })
    return blocks


class Brain:
    def __init__(
        self,
        settings: Settings,
        toolbox: Toolbox | None = None,
        on_activity: Callable[[str], Awaitable[None]] | None = None,
        memory_provider: Callable[[], Awaitable[str]] | None = None,
    ):
        self._settings = settings
        self._toolbox = toolbox
        self._on_activity = on_activity
        # Fournit le bloc « ce que je sais de toi » injecté dans le prompt (async :
        # il lit le Store). Absent en test unitaire → mémoire vide, comportement inchangé.
        self._memory_provider = memory_provider
        self._client: anthropic.AsyncAnthropic | None = (
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key
            else None
        )

    async def stream_reply(
        self, history: list[dict], *, utterance: str = "", source: str = "text",
        speaker: Speaker | None = None,
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
        who = speaker or OWNER
        messages: list[dict] = list(history)
        tools = self._toolbox.specs() if self._toolbox else None

        # Mémoire du locuteur courant, lue une fois pour tout le tour (stable entre
        # les rounds d'outils). Un invité (subject None) n'a aucune mémoire injectée.
        memory_text = ""
        if self._memory_provider is not None:
            try:
                memory_text = await self._memory_provider(who.subject)
            except Exception:
                log.exception("Lecture de la mémoire impossible — tour sans profil")
                memory_text = ""

        try:
            for round_no in range(MAX_TOOL_ROUNDS):
                kwargs: dict = dict(
                    model=s.model,
                    max_tokens=s.max_tokens,
                    system=_system_blocks(s, memory_text, who),
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
                        block.name, dict(block.input or {}),
                        utterance=utterance, source=source, speaker=who,
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
