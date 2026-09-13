"""Vision en lecture : Luna REGARDE, elle n'agit pas.

Le modèle de vision tourne EN LOCAL (la RTX 2070 de Guillaume) derrière une API
compatible OpenAI — même dialecte que les fournisseurs LLM alternatifs et la voix
clonée. Sentinel lui envoie un instantané de caméra (lu depuis Nova, en lecture
seule) et récupère une DESCRIPTION en français.

C'est un capteur, pas un bras : la vision ne déclenche jamais d'action. Pour agir
sur ce qu'elle voit, Luna passe par le moteur « propose puis approuve ». Aucune
image ne quitte le réseau local — l'URL du service est celle de Nebula.
"""

from __future__ import annotations

import base64
import logging

try:  # le SDK n'est requis que si la vision locale est réellement configurée
    import openai
except ImportError:  # pragma: no cover - présent en production (requirements.txt)
    openai = None  # type: ignore[assignment]

log = logging.getLogger("sentinel.vision")

# Consigne stable : décrire le visible, en français, sans rien inventer, sans agir.
_SYSTEM = (
    "Tu es les yeux de Luna, l'assistante de la maison. On te donne l'image d'une "
    "caméra ou d'un écran. Décris en français, en une à trois phrases courtes et "
    "factuelles, ce qui est réellement visible : personnes, animaux, objets, texte "
    "lisible, état des lieux (porte ouverte/fermée, lumière allumée…). N'invente "
    "rien ; si un détail est incertain ou illisible, dis-le franchement. Tu "
    "OBSERVES seulement — tu ne proposes et ne déclenches aucune action."
)


class VisionError(RuntimeError):
    """Échec de la vision — message en français, montrable à l'utilisateur."""


class VisionService:
    """Client de vision local (API compatible OpenAI). Absent/non configuré →
    `available` est faux et l'outil `regarder` n'apparaît pas."""

    def __init__(self, settings):
        self._enabled = settings.vision_enabled
        self._base_url = settings.vision_base_url
        self._model = settings.vision_model
        self._api_key = settings.vision_api_key
        self._max_tokens = settings.vision_max_tokens
        self._timeout = settings.vision_timeout
        self._client = None  # openai.AsyncOpenAI, construit à la demande

    @property
    def available(self) -> bool:
        """Prête dès qu'un service local est déclaré (URL + modèle) et le SDK openai
        présent. La clé est OPTIONNELLE : un service local n'authentifie souvent pas."""
        return bool(self._enabled and self._base_url and self._model and openai is not None)

    def _get_client(self):
        if openai is None:
            raise VisionError(
                "Le paquet Python « openai » n'est pas installé sur Nebula "
                "(ajoute-le puis reconstruis l'image sentinel-core)."
            )
        if not self._base_url or not self._model:
            raise VisionError(
                "La vision locale n'est pas configurée (VISION_BASE_URL / VISION_MODEL)."
            )
        if self._client is None:
            # « sk-local » : jeton factice accepté par les serveurs locaux sans auth.
            self._client = openai.AsyncOpenAI(
                api_key=self._api_key or "sk-local", base_url=self._base_url
            )
        return self._client

    async def describe(
        self, image: bytes, question: str | None = None, *, content_type: str = "image/jpeg"
    ) -> str:
        """Renvoie une description française de l'image. `question` oriente le regard
        (« y a-t-il quelqu'un ? », « quelle heure indique l'horloge ? »)."""
        client = self._get_client()
        encoded = base64.b64encode(image).decode("ascii")
        data_url = f"data:{content_type};base64,{encoded}"
        user_text = (question or "").strip() or "Décris ce que tu vois sur cette image."
        try:
            resp = await client.chat.completions.create(
                model=self._model,
                max_tokens=self._max_tokens,
                timeout=self._timeout,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
            )
        except Exception as exc:  # SDK openai → message français clair, jamais opaque
            raise _translate(exc) from exc
        text = ""
        if getattr(resp, "choices", None):
            text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise VisionError("Le service de vision n'a renvoyé aucune description.")
        return text


def _translate(exc: Exception) -> VisionError:
    """Erreur du service de vision → message actionnable (souvent de la config :
    service éteint, modèle inconnu)."""
    if isinstance(exc, VisionError):
        return exc
    if openai is None:  # pragma: no cover
        return VisionError(f"Vision indisponible : {exc}")
    if isinstance(exc, openai.APIConnectionError):
        return VisionError(
            "Service de vision local injoignable — vérifie qu'il tourne sur Nebula "
            "(VISION_BASE_URL)."
        )
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", "?")
        return VisionError(
            f"Le service de vision a renvoyé une erreur ({status}) — vérifie le "
            f"modèle (VISION_MODEL) et que l'image est acceptée."
        )
    log.exception("Vision : erreur inattendue")
    return VisionError(f"Vision indisponible : {exc}")
