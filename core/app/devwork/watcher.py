"""Veilleur des tâches de développement : annonce les fins et propose le push.

Boucle de fond côté core : quand une tâche du worker se termine, Guillaume est
prévenu dans le fil, et — s'il y a des modifications — une proposition
« pousser la branche sur GitHub » est créée. Le push lui-même reste une action
du moteur (`dev.push`, propositions uniquement).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ..actions.engine import ActionEngine
from .worker_client import WorkerClient, WorkerError

log = logging.getLogger("sentinel.devwork")


class DevWatcher:
    def __init__(
        self,
        worker: WorkerClient,
        engine: ActionEngine | None,
        announce: Callable[..., Awaitable[None]],  # (texte, gravité, speak=)
        interval: float = 15.0,
    ):
        self._worker = worker
        self._engine = engine
        self._announce = announce
        self._interval = interval

    async def run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.debug("Veilleur dev : worker injoignable", exc_info=True)
            await asyncio.sleep(self._interval)

    async def _tick(self) -> None:
        try:
            tasks = await self._worker.list_tasks()
        except WorkerError:
            return
        for summary in tasks:
            if summary.get("announced") or summary.get("status") not in ("done", "failed"):
                continue
            task = await self._worker.get_task(summary["id"])
            await self._handle_finished(task)
            await self._worker.mark_announced(task["id"])

    async def _handle_finished(self, task: dict) -> None:
        task_id, repo = task["id"], task["repo"]
        if task["status"] == "failed":
            await self._announce(
                f"La tâche de développement {task_id} sur « {repo} » a échoué : "
                f"{task.get('error') or 'raison inconnue'}",
                "warning", speak=False,
            )
            return

        files = task.get("files_changed") or []
        resume = (task.get("summary") or "").strip()
        if len(resume) > 400:
            resume = resume[:400] + "…"

        if not files:
            await self._announce(
                f"Tâche de développement {task_id} sur « {repo} » terminée — "
                f"aucune modification de fichier. {resume}",
                "info", speak=False,
            )
            return

        text = (
            f"Tâche de développement {task_id} sur « {repo} » terminée : "
            f"{len(files)} fichier(s) modifié(s) sur la branche {task['branch']}. {resume}"
        )
        if self._engine is not None:
            proposal, message = await self._engine.propose(
                title=f"Pousser la branche {task['branch']} de {repo} sur GitHub",
                description=(
                    f"Fichiers modifiés : {', '.join(files[:12])}"
                    + ("…" if len(files) > 12 else "")
                ),
                justification=resume or task["instruction"],
                risk="medium",
                rollback="La branche distante pourra être supprimée sur GitHub — "
                         "aucun impact sur la branche principale avant merge.",
                action_id="dev.push",
                params={"task_id": task_id},
                created_by="sentinel (worker)",
            )
            if proposal is not None:
                text += f" {message}"
        await self._announce(text, "info", speak=False)
