"""Fournisseurs LLM alternatifs (multi-LLM).

Claude (Anthropic) reste le cerveau de référence : il est piloté nativement par
le SDK Anthropic dans `llm.py` (meilleur usage des outils, recherche web,
citations, cache de prompt). TOUS les autres fournisseurs — ChatGPT (OpenAI),
Google Gemini, Groq, OpenRouter — parlent le même dialecte « compatible OpenAI »
et passent par l'adaptateur générique ci-dessous : une seule implémentation
couvre les quatre, il suffit d'une clé et d'une URL de base.

Sécurité, inchangée quel que soit le modèle : un fournisseur alternatif a exactement
les mêmes outils que Claude et ne peut rien exécuter de plus. Chaque appel d'outil
repart par le MÊME callback `run_tool` → Toolbox → moteur d'actions : les niveaux
de confiance, la liste blanche et le « propose puis approuve » s'appliquent à
l'identique. La reconnaissance de locuteur ne peut jamais élever un droit, ici non
plus. La recherche web (outil serveur d'Anthropic) n'existe QUE pour Claude.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from .llm import LLMUnavailable, MAX_TOOL_ROUNDS

try:  # le SDK n'est requis que si un fournisseur alternatif est réellement utilisé
    import openai
except ImportError:  # pragma: no cover - présent en production (requirements.txt)
    openai = None  # type: ignore[assignment]

log = logging.getLogger("sentinel.providers")

# Identifiant du cerveau de référence (Claude, piloté nativement dans llm.py).
ANTHROPIC_ID = "claude"


@dataclass(frozen=True)
class Preset:
    """Un fournisseur « compatible OpenAI » préconfiguré (URL + modèle par défaut)."""

    id: str
    label: str
    base_url: str
    default_model: str
    # Attributs de Settings portant la clé et l'éventuel modèle choisi par Guillaume.
    key_attr: str
    model_attr: str
    # Aide affichée dans le cockpit : où récupérer une clé.
    hint: str = ""


# Fournisseurs alternatifs pré-câblés. Ordre STABLE (affiché tel quel dans le cockpit).
# Les modèles par défaut sont un point de départ RAISONNABLE, pas une garantie : les
# catalogues bougent, Guillaume peut choisir le sien via .env (voir .env.example).
PRESETS: tuple[Preset, ...] = (
    Preset(
        id="openai",
        label="ChatGPT (OpenAI)",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        key_attr="openai_api_key",
        model_attr="openai_model",
        hint="platform.openai.com/api-keys",
    ),
    Preset(
        id="gemini",
        label="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        # Google retire vite ses anciens flash (2.0 puis 2.5 → 404 « no longer
        # available »). Son API recommande gemini-3.6-flash. Les catalogues bougent :
        # ce modèle reste éditable dans le cockpit (Paramètres › Moteur).
        default_model="gemini-3.6-flash",
        key_attr="gemini_api_key",
        model_attr="gemini_model",
        hint="aistudio.google.com/apikey",
    ),
    Preset(
        id="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        key_attr="groq_api_key",
        model_attr="groq_model",
        hint="console.groq.com/keys",
    ),
    Preset(
        id="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        default_model="meta-llama/llama-3.3-70b-instruct",
        key_attr="openrouter_api_key",
        model_attr="openrouter_model",
        hint="openrouter.ai/keys",
    ),
)


# Suggestions de modèles par fournisseur, pour la liste déroulante du cockpit.
# Point de départ RAISONNABLE (les catalogues bougent, cf. le retrait des Gemini
# flash) — le champ reste libre : on peut toujours saisir un modèle « perso ».
SUGGESTED_MODELS: dict[str, tuple[str, ...]] = {
    ANTHROPIC_ID: ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"),
    "openai": ("gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o1-mini"),
    "gemini": ("gemini-3.6-flash", "gemini-2.5-pro"),
    "groq": ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
    "openrouter": (
        "meta-llama/llama-3.3-70b-instruct",
        "openai/gpt-4o-mini",
        "google/gemini-2.5-flash",
        "anthropic/claude-sonnet-5",
    ),
}


@dataclass(frozen=True)
class ProviderProfile:
    """Un fournisseur configurable : identité, modèle, accès.

    `kind` = "anthropic" (Claude natif) | "openai" (adaptateur générique). La clé
    n'est JAMAIS exposée à l'UI (voir `public_view`).
    """

    id: str
    label: str
    kind: str
    model: str
    base_url: str | None
    api_key: str
    web_search: bool = False
    hint: str = ""

    @property
    def available(self) -> bool:
        """Utilisable dès qu'une clé est présente (et le SDK openai pour les alternatifs)."""
        if not self.api_key:
            return False
        if self.kind == "openai" and openai is None:
            return False
        return True


def load_profiles(settings, overrides: dict | None = None) -> list[ProviderProfile]:
    """Construit la liste des fournisseurs, en superposant les réglages du cockpit
    à ceux de l'environnement.

    Claude d'abord (cerveau de référence), puis les alternatifs pré-câblés. Un
    fournisseur sans clé apparaît quand même (grisé) : le cockpit indique alors
    comment l'activer. `overrides["providers"][<id>]` = {key?, model?} : une clé
    non vide écrase celle de `.env` ; une clé vide (ou absente) retombe sur
    l'environnement. Idem pour le modèle. Aucune lecture d'environnement ici —
    tout vient de Settings + overrides, pour rester testable.
    """
    ov = (overrides or {}).get("providers", {}) or {}

    def effective(pid: str, env_key: str, env_model: str, default_model: str) -> tuple[str, str]:
        o = ov.get(pid, {}) or {}
        key = str(o.get("key") or "").strip() or str(env_key or "")
        model = str(o.get("model") or "").strip() or str(env_model or "") or default_model
        return key, model

    ckey, cmodel = effective(ANTHROPIC_ID, settings.anthropic_api_key, settings.model, settings.model)
    profiles = [
        ProviderProfile(
            id=ANTHROPIC_ID,
            label="Claude (Anthropic)",
            kind="anthropic",
            model=cmodel,
            base_url=None,
            api_key=ckey,
            web_search=True,
            hint="console.anthropic.com",
        )
    ]
    for preset in PRESETS:
        env_key = str(getattr(settings, preset.key_attr, "") or "")
        env_model = str(getattr(settings, preset.model_attr, "") or "")
        key, model = effective(preset.id, env_key, env_model, preset.default_model)
        profiles.append(
            ProviderProfile(
                id=preset.id,
                label=preset.label,
                kind="openai",
                model=model,
                base_url=preset.base_url,
                api_key=key,
                web_search=False,
                hint=preset.hint,
            )
        )
    return profiles


def resolve_default(profiles: list[ProviderProfile], requested: str) -> str:
    """Fournisseur actif au démarrage : le choix demandé s'il est disponible,
    sinon Claude s'il l'est, sinon le premier fournisseur disponible, sinon Claude."""
    by_id = {p.id: p for p in profiles}
    if requested and (p := by_id.get(requested)) and p.available:
        return requested
    if (claude := by_id.get(ANTHROPIC_ID)) and claude.available:
        return ANTHROPIC_ID
    for p in profiles:
        if p.available:
            return p.id
    return ANTHROPIC_ID


def public_view(profiles: list[ProviderProfile], active_id: str, default_id: str) -> dict:
    """Vue destinée au cockpit : jamais de clé, juste de quoi choisir et afficher."""
    return {
        "active": active_id,
        "default": default_id,
        "providers": [
            {
                "id": p.id,
                "label": p.label,
                "kind": p.kind,
                "model": p.model,
                "suggested": list(SUGGESTED_MODELS.get(p.id, ())),  # liste déroulante (indicatif)
                "configured": bool(p.api_key),  # une clé est posée (jamais la clé elle-même)
                "available": p.available,
                "web_search": p.web_search,
                "hint": p.hint,
            }
            for p in profiles
        ],
    }


def to_openai_tools(specs: list[dict] | None) -> list[dict]:
    """Traduit les déclarations d'outils (format Anthropic) au format OpenAI.

    Anthropic : {name, description, input_schema}. OpenAI :
    {type:"function", function:{name, description, parameters}}. Le schéma JSON
    est identique (`input_schema` → `parameters`) ; les noms d'outils de Sentinel
    sont en ASCII snake_case, acceptés tels quels par les deux API.
    """
    out: list[dict] = []
    for spec in specs or []:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec.get("description", ""),
                    "parameters": spec.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return out


class OpenAICompatProvider:
    """Adaptateur unique pour tout fournisseur à API compatible OpenAI.

    Même boucle d'outils manuelle que Claude, transposée au protocole OpenAI :
    le texte est diffusé au fil de l'eau ; quand le modèle demande des outils, on
    les exécute via `run_tool` (qui passe par la Toolbox et le moteur d'actions),
    puis on renvoie les résultats et la conversation continue — avec le même
    plafond de tours. Aucune écriture directe : cet objet ne connaît que `run_tool`.
    """

    def __init__(self, profile: ProviderProfile):
        self.profile = profile
        self._client = None  # openai.AsyncOpenAI, construit à la demande

    def _get_client(self):
        if openai is None:
            raise LLMUnavailable(
                "Le paquet Python « openai » n'est pas installé sur Nebula "
                "(ajoute-le puis reconstruis l'image sentinel-core)."
            )
        if not self.profile.api_key:
            raise LLMUnavailable(
                f"Aucune clé API n'est configurée pour {self.profile.label}."
            )
        if self._client is None:
            self._client = openai.AsyncOpenAI(
                api_key=self.profile.api_key, base_url=self.profile.base_url
            )
        return self._client

    async def _create(self, client, **kwargs):
        """Ouvre le flux ; replis propres si l'API refuse une option, AVANT tout
        streaming (aucun texte n'a encore été émis) :

        - `max_tokens` → `max_completion_tokens` (OpenAI o1/gpt-5…).
        - `stream_options` (demande d'usage) retiré si l'endpoint ne le connaît pas —
          on perd alors le comptage pour ce fournisseur, jamais la réponse."""
        try:
            return await client.chat.completions.create(stream=True, **kwargs)
        except openai.BadRequestError as exc:
            detail = str(getattr(exc, "message", "") or exc).lower()
            if "stream_options" in kwargs and "stream_options" in detail:
                kwargs.pop("stream_options", None)
                return await self._create(client, **kwargs)
            if "max_tokens" in kwargs and "max_completion_tokens" in detail:
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                return await client.chat.completions.create(stream=True, **kwargs)
            raise

    async def stream(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None,
        system_text: str,
        max_tokens: int,
        notify_activity: Callable[[str], Awaitable[None]],
        run_tool: Callable[[str, dict], Awaitable[tuple[str, bool]]],
        on_usage: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> AsyncIterator[str]:
        client = self._get_client()
        oai_messages: list[dict] = []
        if system_text:
            oai_messages.append({"role": "system", "content": system_text})
        oai_messages += [{"role": m["role"], "content": m["content"]} for m in messages]
        oai_tools = to_openai_tools(tools) or None
        total_in = total_out = 0  # tokens réels, cumulés sur les tours d'outils

        try:
            for round_no in range(MAX_TOOL_ROUNDS):
                kwargs = dict(
                    model=self.profile.model,
                    messages=oai_messages,
                    max_tokens=max_tokens,
                    # Demande le décompte des tokens dans l'ultime fragment du flux
                    # (retiré automatiquement si l'endpoint ne le supporte pas).
                    stream_options={"include_usage": True},
                )
                if oai_tools:
                    kwargs["tools"] = oai_tools
                stream = await self._create(client, **kwargs)

                tool_calls: dict[int, dict] = {}
                async with stream as s:
                    async for chunk in s:
                        cu = getattr(chunk, "usage", None)
                        if cu is not None:  # fragment final « usage » (choices vide)
                            total_in += getattr(cu, "prompt_tokens", 0) or 0
                            total_out += getattr(cu, "completion_tokens", 0) or 0
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        if delta and delta.content:
                            yield delta.content
                        for tc in (getattr(delta, "tool_calls", None) or []):
                            slot = tool_calls.setdefault(
                                tc.index, {"id": "", "name": "", "args": ""}
                            )
                            if tc.id:
                                slot["id"] = tc.id
                            fn = getattr(tc, "function", None)
                            if fn and fn.name:
                                slot["name"] = fn.name
                            if fn and fn.arguments:
                                slot["args"] += fn.arguments

                # Aucun outil demandé → le tour est fini.
                if not tool_calls or not oai_tools:
                    return

                if round_no == MAX_TOOL_ROUNDS - 1:
                    # Budget épuisé : on n'exécute pas des actions dont le modèle
                    # ne verrait jamais le résultat (même garde-fou que Claude).
                    yield (
                        "\n(Je m'arrête là — trop d'étapes d'outils pour une seule "
                        "demande. Rien n'a été exécuté à la dernière étape.)"
                    )
                    return

                ordered: list[dict] = []
                for i, idx in enumerate(sorted(tool_calls)):
                    call = tool_calls[idx]
                    call["id"] = call["id"] or f"call_{i}"
                    ordered.append(call)

                oai_messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": c["id"],
                                "type": "function",
                                "function": {"name": c["name"], "arguments": c["args"] or "{}"},
                            }
                            for c in ordered
                        ],
                    }
                )
                for c in ordered:
                    await notify_activity(c["name"])
                    try:
                        args = json.loads(c["args"] or "{}")
                    except (ValueError, TypeError):
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                    content, _is_error = await run_tool(c["name"], args)
                    oai_messages.append(
                        {"role": "tool", "tool_call_id": c["id"], "content": content or ""}
                    )
        except LLMUnavailable:
            raise
        except Exception as exc:  # SDK openai : on traduit en message clair, jamais opaque
            raise _translate_error(self.profile, exc) from exc
        finally:
            if on_usage is not None and (total_in or total_out):
                await on_usage(total_in, total_out)


# Indices par code HTTP : transforment un « erreur (4xx) » opaque en action concrète.
# La plupart de ces échecs ne sont PAS des bugs de Sentinel mais de la configuration
# du fournisseur (crédit, identifiant de modèle) — désormais réglable dans le cockpit.
_STATUS_HINTS: dict[int, str] = {
    400: "requête refusée — le plus souvent un identifiant de modèle invalide. "
         "Vérifie le modèle dans Paramètres › Moteur.",
    402: "paiement requis — le crédit de ce compte est épuisé. Recharge-le, ou "
         "choisis un modèle gratuit (sur OpenRouter, un id suffixé « :free ») "
         "dans Paramètres › Moteur.",
    404: "modèle introuvable — vérifie l'identifiant exact du modèle dans "
         "Paramètres › Moteur (p.ex. « gemini-2.5-flash », « gpt-4o-mini »).",
    413: "requête trop longue — réduis « Tokens max » ou « Mémoire de "
         "conversation » dans Paramètres › Moteur.",
}


def _error_detail(exc: Exception) -> str:
    """Message d'erreur du fournisseur, quel que soit le format (dict, str, .message)."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"]).strip()
        if isinstance(err, str) and err.strip():
            return err.strip()
        if body.get("message"):
            return str(body["message"]).strip()
    return str(getattr(exc, "message", "") or "").strip()


def _translate_error(profile: ProviderProfile, exc: Exception) -> LLMUnavailable:
    """Erreur d'un fournisseur compatible OpenAI → message français ACTIONNABLE.

    On dit non seulement *quoi* (code + détail brut du fournisseur) mais *comment
    corriger* (indice par code), car ces échecs sont souvent de la config (clé,
    crédit, modèle) que Guillaume peut régler lui-même dans le cockpit."""
    label = profile.label
    if openai is None:  # pragma: no cover - openai importé dès qu'on stream
        return LLMUnavailable(f"{label} : erreur inattendue ({exc}).")
    if isinstance(exc, openai.AuthenticationError):
        return LLMUnavailable(
            f"La clé API de {label} est invalide ou révoquée — repose-la dans "
            f"Paramètres › Moteur."
        )
    if isinstance(exc, openai.PermissionDeniedError):
        return LLMUnavailable(f"{label} refuse l'accès (droits ou quota du compte).")
    if isinstance(exc, openai.RateLimitError):
        return LLMUnavailable(
            f"{label} limite le débit ou le quota est atteint — réessaie dans un moment."
        )
    if isinstance(exc, openai.APIConnectionError):
        return LLMUnavailable(
            f"Impossible de joindre {label} — vérifie l'accès Internet de Nebula."
        )
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", "?")
        hint = _STATUS_HINTS.get(status if isinstance(status, int) else -1, "")
        detail = _error_detail(exc)
        msg = f"{label} a renvoyé une erreur ({status})."
        if hint:
            msg += f" {hint}"
        if detail:
            msg += f" (détail : {detail})"
        return LLMUnavailable(msg)
    log.exception("Erreur %s inattendue", label)
    return LLMUnavailable(f"{label} : erreur inattendue ({exc}).")
