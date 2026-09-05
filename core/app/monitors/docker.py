"""Surveillance Docker de Nebula, via le socket-proxy en lecture seule.

Le proxy (tecnativa/docker-socket-proxy) n'expose que la lecture + le
redémarrage (ALLOW_RESTARTS) : pas d'exec, pas de création, pas de suppression.
⚠️ `restart_container` est une écriture : appelée UNIQUEMENT par les exécuteurs
du moteur d'actions — donc uniquement après une proposition approuvée
(verrouillé par tests/test_invariant.py).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..norm import normalize

log = logging.getLogger("sentinel.docker")


class DockerError(RuntimeError):
    """Erreur Docker — message en français, montrable à l'utilisateur."""


def _demux_logs(data: bytes) -> str:
    """Retire les en-têtes de trames du flux de logs Docker (conteneurs sans TTY)."""
    out: list[bytes] = []
    i = 0
    while i + 8 <= len(data):
        if data[i] in (0, 1, 2) and data[i + 1 : i + 4] == b"\x00\x00\x00":
            size = int.from_bytes(data[i + 4 : i + 8], "big")
            out.append(data[i + 8 : i + 8 + size])
            i += 8 + size
        else:
            return data.decode("utf-8", "replace")  # flux TTY brut, pas de trames
    return b"".join(out).decode("utf-8", "replace")


class DockerMonitor:
    def __init__(self, base_url: str, timeout: float = 8.0):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params) -> httpx.Response:
        try:
            resp = await self._client.get(path, params=params or None)
        except httpx.HTTPError as exc:
            raise DockerError(
                "Le proxy Docker est injoignable — vérifie le conteneur sentinel-dockerproxy."
            ) from exc
        if resp.status_code == 404:
            raise DockerError("Conteneur introuvable.")
        if resp.status_code >= 400:
            raise DockerError(f"Le proxy Docker a refusé la requête ({resp.status_code}).")
        return resp

    # ── Lecture ──────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        try:
            return (await self._client.get("/_ping")).status_code == 200
        except httpx.HTTPError:
            return False

    async def containers(self) -> list[dict]:
        """Tous les conteneurs, normalisés : nom, etat, status, image, ports publiés."""
        raw = (await self._get("/containers/json", all="true")).json()
        out = []
        for c in raw:
            names = c.get("Names") or ["?"]
            ports = sorted({
                f"{p.get('IP', '')}:{p['PublicPort']}→{p.get('PrivatePort')}"
                for p in (c.get("Ports") or [])
                if p.get("PublicPort")
            })
            out.append({
                "id": c.get("Id", "")[:12],
                "nom": names[0].lstrip("/"),
                "etat": c.get("State", "?"),          # running | exited | restarting…
                "status": c.get("Status", ""),        # "Up 3 days (healthy)"…
                "image": c.get("Image", ""),
                "ports_publies": ports,
            })
        return sorted(out, key=lambda c: c["nom"])

    async def memory_usage(self, max_containers: int = 20) -> dict[str, int]:
        """Mémoire (Mo) des conteneurs en marche — {nom: Mo}."""
        running = [c for c in await self.containers() if c["etat"] == "running"]
        semaphore = asyncio.Semaphore(5)

        async def one(c: dict) -> tuple[str, int] | None:
            async with semaphore:
                try:
                    stats = (await self._get(
                        f"/containers/{c['id']}/stats", stream="false", **{"one-shot": "true"}
                    )).json()
                    usage = (stats.get("memory_stats") or {}).get("usage")
                    if usage is None:
                        return None
                    return (c["nom"], round(usage / 1_048_576))
                except DockerError:
                    return None

        results = await asyncio.gather(*(one(c) for c in running[:max_containers]))
        return dict(r for r in results if r)

    async def find(self, name: str) -> dict | None:
        """Retrouve un conteneur par nom (exact, sinon inclusion sans ambiguïté)."""
        wanted = normalize(name)
        containers = await self.containers()
        for c in containers:
            if normalize(c["nom"]) == wanted:
                return c
        matches = [c for c in containers if wanted in normalize(c["nom"])]
        return matches[0] if len(matches) == 1 else None

    async def logs(self, name: str, tail: int = 50) -> str:
        container = await self.find(name)
        if container is None:
            raise DockerError(f"Conteneur introuvable ou ambigu : « {name} ».")
        resp = await self._get(
            f"/containers/{container['id']}/logs",
            stdout="true", stderr="true", tail=str(max(1, min(tail, 300))),
        )
        return _demux_logs(resp.content)

    # ── Écriture (réservée aux exécuteurs du moteur d'actions) ───────────

    async def restart_container(self, name: str) -> str:
        container = await self.find(name)
        if container is None:
            raise DockerError(f"Conteneur introuvable ou ambigu : « {name} ».")
        if container["nom"].startswith("sentinel-core"):
            raise DockerError("Je ne me redémarre pas moi-même — fais-le depuis Unraid.")
        try:
            resp = await self._client.post(f"/containers/{container['id']}/restart", params={"t": 10})
        except httpx.HTTPError as exc:
            raise DockerError("Le proxy Docker est injoignable.") from exc
        if resp.status_code not in (204, 200):
            raise DockerError(f"Redémarrage refusé par le proxy ({resp.status_code}).")
        return f"Conteneur {container['nom']} redémarré."
