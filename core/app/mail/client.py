"""Client Gmail en LECTURE SEULE (Phase 3).

OAuth2 (portée `gmail.readonly`) : Sentinel ne peut que LIRE — aucun envoi, aucune
suppression, aucune modification n'est possible, la portée l'interdit côté Google.
Appels REST directs via httpx (pas de grosse dépendance google-api). Le jeton de
rafraîchissement s'obtient une fois (voir mail/authorize.py et docs/EMAIL.md) ; il
sert à obtenir des jetons d'accès de courte durée, mis en cache jusqu'à expiration.

Renvoie un résumé STRUCTURÉ des non-lus (expéditeur, objet, date, aperçu,
importance) — jamais le corps complet, jamais de pièces jointes.
"""

from __future__ import annotations

import logging
import time
from email.utils import parseaddr

import httpx

log = logging.getLogger("sentinel.mail")

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API = "https://gmail.googleapis.com/gmail/v1/users/me"


class MailError(RuntimeError):
    """Erreur d'accès au courriel — message (en français) montré à l'utilisateur."""


class GmailClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        max_results: int = 10,
        timeout: int = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._max = max(1, min(max_results, 25))
        self._timeout = timeout
        self._transport = transport  # injecté par les tests (httpx.MockTransport)
        self._access_token = ""
        self._expiry = 0.0

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def _token(self) -> str:
        # Jeton d'accès mis en cache jusqu'à ~1 min avant expiration.
        if self._access_token and time.time() < self._expiry - 60:
            return self._access_token
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }
        try:
            async with self._http() as http:
                resp = await http.post(_TOKEN_URL, data=data)
        except (httpx.HTTPError, OSError) as exc:
            raise MailError(f"Impossible de joindre Google ({exc}).") from exc
        if resp.status_code != 200:
            raise MailError(
                "L'autorisation Gmail a été refusée (jeton expiré ou révoqué). "
                "Refais l'autorisation — voir docs/EMAIL.md."
            )
        payload = resp.json()
        self._access_token = str(payload.get("access_token") or "")
        self._expiry = time.time() + float(payload.get("expires_in") or 0)
        if not self._access_token:
            raise MailError("Google n'a pas renvoyé de jeton d'accès.")
        return self._access_token

    async def summary(self) -> dict:
        """Résumé structuré des messages non lus de la boîte de réception."""
        token = await self._token()
        headers = {"authorization": f"Bearer {token}"}
        params = {"q": "is:unread in:inbox", "maxResults": str(self._max)}
        try:
            async with self._http() as http:
                listing = await http.get(f"{_API}/messages", params=params, headers=headers)
                if listing.status_code != 200:
                    raise MailError(f"Gmail a répondu {listing.status_code} à la liste des messages.")
                data = listing.json()
                ids = [m.get("id") for m in (data.get("messages") or []) if m.get("id")]
                total = int(data.get("resultSizeEstimate") or len(ids))

                messages = []
                for mid in ids:
                    detail = await http.get(
                        f"{_API}/messages/{mid}",
                        params={
                            "format": "metadata",
                            "metadataHeaders": ["From", "Subject", "Date"],
                        },
                        headers=headers,
                    )
                    if detail.status_code != 200:
                        continue
                    messages.append(_parse_message(detail.json()))
        except (httpx.HTTPError, OSError) as exc:
            raise MailError(f"Gmail injoignable ({exc}).") from exc
        return {"unread_total": total, "messages": messages}


def _parse_message(msg: dict) -> dict:
    headers = {h.get("name", "").lower(): h.get("value", "") for h in (msg.get("payload") or {}).get("headers", [])}
    name, email_addr = parseaddr(headers.get("from", ""))
    return {
        "from_name": name or email_addr or "?",
        "from_email": email_addr,
        "subject": headers.get("subject", "(sans objet)"),
        "date": headers.get("date", ""),
        "snippet": (msg.get("snippet") or "").strip(),
        "important": "IMPORTANT" in (msg.get("labelIds") or []),
    }
