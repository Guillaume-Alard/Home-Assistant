"""Embeddings locaux pour la mémoire (RAG).

Un service d'embeddings tourne EN LOCAL (la RTX 2070 de Guillaume) derrière une
API compatible OpenAI (`/v1/embeddings`) — même dialecte que la vision et les
fournisseurs LLM alternatifs. Sentinel s'en sert pour retrouver les souvenirs
par PERTINENCE (pas seulement par récence) : la requête et les souvenirs sont
transformés en vecteurs, comparés par cosinus.

Aucune donnée ne quitte le réseau local, et rien n'agit sur le monde : c'est de
la récupération de contexte. Non configuré (`base_url` vide) → `available` est
faux et la mémoire retombe simplement sur la récence.
"""

from __future__ import annotations

import logging

try:  # le SDK n'est requis que si les embeddings sont réellement configurés
    import openai
except ImportError:  # pragma: no cover - présent en production (requirements.txt)
    openai = None  # type: ignore[assignment]

log = logging.getLogger("sentinel.embeddings")


class EmbeddingsError(RuntimeError):
    """Échec du service d'embeddings — message en français, montrable."""


class EmbeddingsService:
    """Client d'embeddings local (API compatible OpenAI). Absent/non configuré →
    `available` est faux et le RAG mémoire reste inactif (repli sur la récence)."""

    def __init__(self, settings):
        self._enabled = settings.embeddings_enabled
        self._base_url = settings.embeddings_base_url
        self._model = settings.embeddings_model
        self._api_key = settings.embeddings_api_key
        self._timeout = 30
        self._client = None  # openai.AsyncOpenAI, construit à la demande

    @property
    def available(self) -> bool:
        """Prêt dès qu'un service local est déclaré (URL + modèle) et le SDK présent.
        La clé est OPTIONNELLE : un service local n'authentifie souvent pas."""
        return bool(self._enabled and self._base_url and self._model and openai is not None)

    def _get_client(self):
        if openai is None:
            raise EmbeddingsError("Le paquet Python « openai » n'est pas installé sur Nebula.")
        if not self._base_url or not self._model:
            raise EmbeddingsError(
                "Le service d'embeddings n'est pas configuré (EMBEDDINGS_BASE_URL / EMBEDDINGS_MODEL)."
            )
        if self._client is None:
            self._client = openai.AsyncOpenAI(
                api_key=self._api_key or "sk-local", base_url=self._base_url
            )
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Renvoie un vecteur par texte (même ordre). Lève EmbeddingsError en cas
        d'échec — l'appelant retombe alors sur la récence, sans planter."""
        clean = [str(t or "").strip() for t in texts]
        if not any(clean):
            return [[] for _ in clean]
        client = self._get_client()
        try:
            resp = await client.embeddings.create(
                model=self._model, input=clean, timeout=self._timeout
            )
        except Exception as exc:  # SDK openai → message clair, jamais opaque
            raise _translate(exc) from exc
        data = sorted(getattr(resp, "data", []) or [], key=lambda d: getattr(d, "index", 0))
        vectors = [list(getattr(d, "embedding", []) or []) for d in data]
        if len(vectors) != len(clean):
            raise EmbeddingsError("Le service d'embeddings a renvoyé un nombre de vecteurs inattendu.")
        return vectors


def _translate(exc: Exception) -> EmbeddingsError:
    if isinstance(exc, EmbeddingsError):
        return exc
    if openai is None:  # pragma: no cover
        return EmbeddingsError(f"Embeddings indisponibles : {exc}")
    if isinstance(exc, openai.APIConnectionError):
        return EmbeddingsError(
            "Service d'embeddings local injoignable — vérifie qu'il tourne sur Nebula "
            "(EMBEDDINGS_BASE_URL)."
        )
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", "?")
        return EmbeddingsError(
            f"Le service d'embeddings a renvoyé une erreur ({status}) — vérifie le "
            f"modèle (EMBEDDINGS_MODEL)."
        )
    log.exception("Embeddings : erreur inattendue")
    return EmbeddingsError(f"Embeddings indisponibles : {exc}")
