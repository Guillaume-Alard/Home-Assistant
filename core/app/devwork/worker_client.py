"""Client HTTP vers le worker Claude Code (réseau interne compose).

⚠️ `start_task` (écrit dans l'espace de travail isolé) et `push_branch`
(écrit sur GitHub) sont réservés aux exécuteurs du moteur d'actions —
verrouillé par tests/test_invariant.py. Le reste est de la lecture libre.
"""

from __future__ import annotations

import httpx


class WorkerError(RuntimeError):
    """Erreur du worker — message en français, montrable à l'utilisateur."""


class WorkerClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, timeout: float | None = None, **kwargs):
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise WorkerError(
                "L'atelier de développement (sentinel-worker) est injoignable — "
                "vérifie que le conteneur est démarré."
            ) from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", "")
            except ValueError:
                detail = ""
            raise WorkerError(detail or f"Le worker a refusé la requête ({resp.status_code}).")
        return resp

    # ── Lecture (libre) ──────────────────────────────────────────────────

    async def health(self) -> dict | None:
        try:
            return (await self._request("GET", "/health")).json()
        except WorkerError:
            return None

    async def list_tasks(self) -> list[dict]:
        return (await self._request("GET", "/tasks")).json()

    async def get_task(self, task_id: str) -> dict:
        return (await self._request("GET", f"/tasks/{task_id}")).json()

    async def get_diff(self, task_id: str) -> str:
        return (await self._request("GET", f"/tasks/{task_id}/diff")).text

    async def get_log(self, task_id: str, after: int = 0) -> dict:
        """Journal en direct d'une tâche (lecture incrémentale : after = `next` reçu)."""
        return (await self._request(
            "GET", f"/tasks/{task_id}/log", params={"after": after}
        )).json()

    async def mark_announced(self, task_id: str) -> None:
        await self._request("POST", f"/tasks/{task_id}/announced")

    # ── Écriture (réservée aux exécuteurs du moteur d'actions) ───────────

    async def start_task(self, repo: str, instruction: str) -> dict:
        return (await self._request(
            "POST", "/tasks", json={"repo": repo, "instruction": instruction}
        )).json()

    async def push_branch(self, task_id: str) -> dict:
        # Le push git côté worker peut prendre jusqu'à ~2 min : délai adapté,
        # sinon un push lent serait marqué en échec alors qu'il a réussi.
        return (await self._request("POST", f"/tasks/{task_id}/push", timeout=180.0)).json()
