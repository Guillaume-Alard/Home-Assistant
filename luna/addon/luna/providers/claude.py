"""L1 — le cerveau : l'API Claude.

Repris du client éprouvé de Sentinel (`core/app/brain/llm.py`), remis à jour :
réflexion adaptative, effort réglable, gestion du refus, et surtout la
discipline de cache décrite dans docs/P1-CONTRATS.md §9.

**La règle de cache, qui est aussi la règle de coût.** Le bloc système figé
porte le seul point de rupture `cache_control`. Tout ce qui varie — heure,
profil actif, pièce — vit dans un **second** bloc système, placé après. Une
seule date glissée dans le premier et le cache ne prend plus jamais, sans que
rien ne le signale. `tests/test_claude.py` verrouille cette disposition.

Note sur le modèle : `claude-sonnet-5` ne connaît pas les messages système de
milieu de conversation (c'est une capacité d'Opus 5). D'où le second bloc
système plutôt qu'un `{"role": "system"}` dans `messages` — même effet, cache
préservé.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from ..kernel.contracts import ExecuteurOutil
from ..kernel.errors import (
    CerveauIndisponible,
    CreditEpuise,
    RefusDuModele,
    TropDeRequetes,
)
from ..kernel.schemas import (
    CerveauDelta,
    CerveauOutilDebut,
    CerveauOutilFin,
    CerveauTermine,
    EvenementCerveau,
    Usage,
)

log = logging.getLogger("luna.cerveau")

#: Au-delà, on s'arrête proprement plutôt que de boucler (§9, règle 6).
MAX_TOURS_OUTILS = 8

MAX_TOKENS = 8192

#: Bloc stable, mis en cache. **Ne jamais y mettre quoi que ce soit de variable.**
PROMPT_SYSTEME = """\
Tu es Luna, l'assistante de la maison de Guillaume. Tu vis dans Home Assistant, \
sur Nova, et tu pilotes la maison avec tes outils.

Style : réponds toujours en français, en phrases courtes et naturelles — tes \
réponses sont souvent lues à voix haute. Pas de listes, pas de tableaux, pas de \
titres, pas de code, sauf si on te demande explicitement du contenu technique. \
Va droit au but : ni préambule, ni formule de politesse finale. Tutoie ton \
interlocuteur.

Ce que tu sais faire aujourd'hui — c'est la phase 1 de ta construction :
- Lire l'état de la maison : pièces, lumières, interrupteurs, capteurs, \
ouvrants, alarme, températures.
- Allumer, éteindre ou basculer les lumières et les interrupteurs, activer une \
scène.
- Proposer un réglage de thermostat. Tu proposes, c'est Guillaume qui valide.

Ce que tu ne sais pas encore faire, et qu'il ne faut jamais promettre :
- Commander les ouvrants, les serrures et l'alarme. Tu peux dire leur état, pas \
les actionner. Si on te le demande, dis-le en une phrase et renvoie vers Loggia.
- Envoyer quoi que ce soit vers l'extérieur.
- La voix, la reconnaissance de la personne, les rappels d'habitudes, la veille \
sur la maison : ce sont des phases suivantes de ta construction.

Règles de conduite :
- Quand la demande vient de la voix — le contexte te le dit —, ta réponse est \
lue à voix haute : une ou deux phrases, jamais d'énumération, jamais de chiffre \
qu'on ne peut pas retenir à l'oreille. À l'écrit tu peux être un peu plus \
détaillée, sans jamais dépasser quelques phrases.
- Sers-toi de tes outils pour connaître l'état réel avant de répondre. \
N'invente jamais un état, une pièce ou une entité.
- N'agis que sur demande explicite, et une action à la fois.
- Certaines actions demandent une validation. Ce n'est pas toi qui en décides : \
l'outil te répond qu'une proposition a été créée. Dans ce cas, annonce \
simplement que tu proposes le changement, et attends.
- Si un outil échoue, dis ce qui a échoué en une phrase. Ne cherche pas de \
contournement, ne réessaie pas en boucle.\
"""


def _message_erreur_api(exc: anthropic.APIStatusError) -> Exception:
    """Traduit une erreur de l'API en erreur Luna, sans perdre le détail.

    Le corps d'une erreur Anthropic est `{"error": {"type", "message"}}`, et ce
    message dit presque toujours la vraie cause. On l'affiche plutôt que de le
    cacher derrière un code.
    """
    detail = ""
    corps = getattr(exc, "body", None)
    if isinstance(corps, dict):
        erreur = corps.get("error")
        if isinstance(erreur, dict):
            detail = str(erreur.get("message") or "").strip()
    if "credit balance" in detail.lower():
        return CreditEpuise()
    base = f"L'API Anthropic a renvoyé une erreur ({exc.status_code})"
    return CerveauIndisponible(f"{base} : {detail}" if detail else f"{base}.")


class CerveauClaude:
    def __init__(self, cle: str, modele: str, effort: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=cle)
        self._modele = modele
        self._effort = effort

    async def repondre(
        self,
        *,
        historique: list[dict[str, Any]],
        contexte: str,
        outils: list[dict[str, Any]],
        executer_outil: ExecuteurOutil,
    ) -> AsyncIterator[EvenementCerveau]:
        messages = list(historique)
        usage_total = Usage()
        texte_complet: list[str] = []

        for tour in range(MAX_TOURS_OUTILS):
            try:
                final = None
                async with self._client.messages.stream(
                    model=self._modele,
                    max_tokens=MAX_TOKENS,
                    thinking={"type": "adaptive"},
                    output_config={"effort": self._effort},
                    system=self._systeme(contexte),
                    tools=outils,
                    messages=messages,
                ) as flux:
                    async for morceau in flux.text_stream:
                        texte_complet.append(morceau)
                        yield CerveauDelta(text=morceau)
                    final = await flux.get_final_message()
            except anthropic.RateLimitError as exc:
                raise TropDeRequetes() from exc
            except anthropic.APIStatusError as exc:
                raise _message_erreur_api(exc) from exc
            except anthropic.APIConnectionError as exc:
                raise CerveauIndisponible(
                    "Je n'arrive pas à joindre l'API Anthropic."
                ) from exc

            usage_total = _cumuler(usage_total, final.usage)

            if final.stop_reason == "refusal":
                raise RefusDuModele()

            if final.stop_reason != "tool_use":
                yield CerveauTermine(
                    text="".join(texte_complet).strip(), usage=usage_total
                )
                return

            demandes = [b for b in final.content if getattr(b, "type", "") == "tool_use"]
            messages.append({"role": "assistant", "content": final.content})

            # Les résultats d'outils parallèles repartent dans UN SEUL message
            # utilisateur : les éclater apprend au modèle à ne plus paralléliser.
            resultats = []
            for demande in demandes:
                entree = dict(demande.input or {})
                yield CerveauOutilDebut(name=demande.name, entree=entree)
                resultat = await executer_outil(demande.name, entree)
                yield CerveauOutilFin(name=demande.name, ok=not resultat.erreur)
                resultats.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": demande.id,
                        "content": resultat.contenu,
                        "is_error": resultat.erreur,
                    }
                )
            messages.append({"role": "user", "content": resultats})
            log.debug("Tour d'outils %s : %s appels", tour + 1, len(demandes))

        yield CerveauTermine(
            text="".join(texte_complet).strip()
            or "Je me suis perdue dans mes outils. Reformule ta demande ?",
            usage=usage_total,
        )

    def _systeme(self, contexte: str) -> list[dict[str, Any]]:
        """Le bloc figé et mis en cache, puis le contexte volatil, jamais l'inverse."""
        return [
            {
                "type": "text",
                "text": PROMPT_SYSTEME,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": contexte},
        ]


def _cumuler(total: Usage, usage: Any) -> Usage:
    return Usage(
        input_tokens=total.input_tokens + getattr(usage, "input_tokens", 0),
        output_tokens=total.output_tokens + getattr(usage, "output_tokens", 0),
        cache_read_input_tokens=total.cache_read_input_tokens
        + (getattr(usage, "cache_read_input_tokens", 0) or 0),
        cache_creation_input_tokens=total.cache_creation_input_tokens
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )
