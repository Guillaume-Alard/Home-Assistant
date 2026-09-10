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
        default_model="gemini-2.0-flash",
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


def load_profiles(settings) -> list[ProviderProfile]:
    """Construit la liste des fournisseurs à partir de la configuration.

    Claude d'abord (cerveau de référence), puis les alternatifs pré-câblés. Un
    fournisseur sans clé apparaît quand même (grisé) : le cockpit indique alors
    comment l'activer. Rien ici ne lit l'environnement — tout vient de Settings,
    pour rester testable.
    """
    profiles = [
        ProviderProfile(
            id=ANTHROPIC_ID,
            label="Claude (Anthropic)",
            kind="anthropic",
            model=settings.model,
            base_url=None,
            api_key=settings.anthropic_api_key,
            web_search=True,
            hint="console.anthropic.com",
        )
    ]
    for preset in PRESETS:
        api_key = str(getattr(settings, preset.key_attr, "") or "")
        model = str(getattr(settings, preset.model_attr, "") or "") or preset.default_model
        profiles.append(
            ProviderProfile(
                id=preset.id,
                label=preset.label,
                kind="openai",
                model=model,
                base_url=preset.base_url,
                api_key=api_key,
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
        """Ouvre le flux ; repli max_tokens → max_completion_tokens si l'API l'exige.

        Certains modèles récents (OpenAI o1/gpt-5…) refusent `max_tokens` et
        réclament `max_completion_tokens`. On bascule proprement AVANT tout
        streaming (aucun texte n'a encore été émis)."""
        try:
            return await client.chat.completions.create(stream=True, **kwargs)
        except openai.BadRequestError as exc:
            detail = str(getattr(exc, "message", "") or exc).lower()
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
        settings,
        notify_activity: Callable[[str], Awaitable[None]],
        run_tool: Callable[[str, dict], Awaitable[tuple[str, bool]]],
    ) -> AsyncIterator[str]:
        client = self._get_client()
        oai_messages: list[dict] = []
        if system_text:
            oai_messages.append({"role": "system", "content": system_text})
        oai_messages += [{"role": m["role"], "content": m["content"]} for m in messages]
        oai_tools = to_openai_tools(tools) or None

        try:
            for round_no in range(MAX_TOOL_ROUNDS):
                kwargs = dict(
                    model=self.profile.model,
                    messages=oai_messages,
                    max_tokens=settings.max_tokens,
                )
                if oai_tools:
                    kwargs["tools"] = oai_tools
                stream = await self._create(client, **kwargs)

                tool_calls: dict[int, dict] = {}
                async with stream as s:
                    async for chunk in s:
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


def _translate_error(profile: ProviderProfile, exc: Exception) -> LLMUnavailable:
    """Erreur d'un fournisseur compatible OpenAI → message français, sans rien cacher."""
    label = profile.label
    if openai is None:  # pragma: no cover - openai importé dès qu'on stream
        return LLMUnavailable(f"{label} : erreur inattendue ({exc}).")
    if isinstance(exc, openai.AuthenticationError):
        return LLMUnavailable(f"La clé API de {label} est invalide ou révoquée.")
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
        detail = ""
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                detail = str(err.get("message") or "").strip()
        base = f"{label} a renvoyé une erreur ({status})"
        return LLMUnavailable(f"{base} : {detail}" if detail else f"{base}.")
    log.exception("Erreur %s inattendue", label)
    return LLMUnavailable(f"{label} : erreur inattendue ({exc}).")
