"""Santé d'Atrium (le dashboard maison) : simple contrôle HTTP + latence."""

from __future__ import annotations

import time

import httpx


class AtriumMonitor:
    def __init__(self, url: str, timeout: float = 5.0):
        self._url = url
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def check(self) -> dict:
        start = time.monotonic()
        try:
            resp = await self._client.get(self._url)
            return {
                "configure": True,
                "ok": resp.status_code < 400,
                "code": resp.status_code,
                "latence_ms": round((time.monotonic() - start) * 1000),
            }
        except httpx.HTTPError as exc:
            return {
                "configure": True,
                "ok": False,
                "code": None,
                "latence_ms": None,
                "erreur": f"{type(exc).__name__}",
            }
