"""Sentinel worker : exécute des tâches de développement Claude Code, isolées.

Chaque tâche : clone jetable d'un dépôt de la LISTE BLANCHE (DEV_REPOS) →
branche `sentinel/<id>` → `claude -p` en headless → commit + diff. Le résultat
(résumé, diff) est consultable par sentinel-core ; le push vers GitHub n'a lieu
que sur appel explicite de /push — côté Sentinel, uniquement via une
proposition approuvée par Guillaume.

Une seule tâche à la fois, persistance simple dans /workspace/tasks.json.
Ce service n'est joignable que sur le réseau interne compose.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s worker: %(message)s")
log = logging.getLogger("worker")

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/workspace"))
TASKS_FILE = WORKSPACE / "tasks.json"
TASK_TIMEOUT = int(os.environ.get("DEV_TASK_TIMEOUT", "1800"))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
DIFF_MAX_BYTES = 500_000
KEEP_TASKS = 30

_SECRETS = [s for s in (
    GITHUB_TOKEN,
    os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip(),
    os.environ.get("ANTHROPIC_API_KEY", "").strip(),
) if s]


def _scrub(text: str) -> str:
    """Aucun secret ne doit fuiter dans un résumé, une erreur ou un diff."""
    for secret in _SECRETS:
        text = text.replace(secret, "•••")
    return text


def _parse_repos() -> dict[str, str]:
    """DEV_REPOS → {alias: url} — l'alias est le nom du dépôt (« loggia »)."""
    raw = os.environ.get("DEV_REPOS", "")
    repos: dict[str, str] = {}
    for url in raw.replace(",", " ").split():
        url = url.strip().rstrip("/")
        if not url.startswith("https://"):
            continue
        alias = url.rsplit("/", 1)[-1].removesuffix(".git").lower()
        repos[alias] = url
    return repos


REPOS = _parse_repos()

app = FastAPI(title="sentinel-worker")

_tasks: dict[str, dict] = {}
_busy_lock = asyncio.Lock()


def _save() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    TASKS_FILE.write_text(json.dumps(_tasks, ensure_ascii=False, indent=1), encoding="utf-8")


def _load() -> None:
    global _tasks
    if TASKS_FILE.is_file():
        try:
            _tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        except ValueError:
            _tasks = {}
    # Un redémarrage en pleine tâche → l'exécution est perdue, on l'acte
    for task in _tasks.values():
        if task["status"] in ("queued", "running"):
            task["status"] = "failed"
            task["error"] = "Le worker a redémarré pendant la tâche."
    _save()


_load()


class TaskRequest(BaseModel):
    repo: str          # alias (« loggia ») ou URL de la liste blanche
    instruction: str


def _resolve_repo(ref: str) -> tuple[str, str]:
    ref = ref.strip().rstrip("/")
    alias = ref.rsplit("/", 1)[-1].removesuffix(".git").lower()
    if alias in REPOS and (not ref.startswith("http") or REPOS[alias] == ref):
        return alias, REPOS[alias]
    raise HTTPException(
        403,
        f"Dépôt hors liste blanche : « {ref} ». Autorisés : {', '.join(sorted(REPOS)) or 'aucun'} "
        "(variable DEV_REPOS).",
    )


async def _run_cmd(cwd: Path, *args: str, timeout: int = 120, env: dict | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, **(env or {})},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise RuntimeError(f"Commande trop longue : {args[0]}")
    text = _scrub(out.decode("utf-8", "replace"))
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} a échoué : {text[-800:]}")
    return text


def _task_public(task: dict, with_summary: bool = True) -> dict:
    fields = ["id", "repo", "instruction", "status", "branch", "files_changed",
              "created_at", "finished_at", "announced", "pushed"]
    out = {k: task.get(k) for k in fields}
    if with_summary:
        out["summary"] = task.get("summary")
        out["error"] = task.get("error")
    return out


# ── API ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        auth = "abonnement (jeton OAuth)"
    elif os.environ.get("ANTHROPIC_API_KEY", "").strip():
        auth = "clé API"
    else:
        auth = "absente"
    return {
        "status": "ok",
        "repos": sorted(REPOS),
        "busy": _busy_lock.locked(),
        "auth": auth,
        "push_possible": bool(GITHUB_TOKEN),
    }


@app.post("/tasks")
async def create_task(req: TaskRequest) -> dict:
    if not req.instruction.strip():
        raise HTTPException(422, "Instruction vide.")
    if _busy_lock.locked():
        raise HTTPException(409, "Une tâche est déjà en cours — une seule à la fois.")
    alias, url = _resolve_repo(req.repo)
    task_id = uuid.uuid4().hex[:8]
    _tasks[task_id] = {
        "id": task_id, "repo": alias, "url": url,
        "instruction": req.instruction.strip()[:4000],
        "status": "queued", "branch": f"sentinel/{task_id}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "announced": False, "pushed": False,
    }
    _save()
    asyncio.get_running_loop().create_task(_run_task(task_id))
    return _task_public(_tasks[task_id])


@app.get("/tasks")
async def list_tasks() -> list[dict]:
    tasks = sorted(_tasks.values(), key=lambda t: t["created_at"], reverse=True)
    return [_task_public(t, with_summary=False) for t in tasks[:KEEP_TASKS]]


@app.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(404, "Tâche inconnue.")
    return _task_public(task)


@app.get("/tasks/{task_id}/diff", response_class=PlainTextResponse)
async def get_diff(task_id: str) -> str:
    if task_id not in _tasks:
        raise HTTPException(404, "Tâche inconnue.")
    diff_file = WORKSPACE / task_id / "diff.patch"
    if not diff_file.is_file():
        return "(aucun diff)"
    return diff_file.read_text(encoding="utf-8", errors="replace")[:DIFF_MAX_BYTES]


@app.post("/tasks/{task_id}/announced")
async def mark_announced(task_id: str) -> dict:
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(404, "Tâche inconnue.")
    task["announced"] = True
    _save()
    return {"ok": True}


@app.post("/tasks/{task_id}/push")
async def push_task(task_id: str) -> dict:
    """Pousse la branche sur GitHub. Appelé UNIQUEMENT par l'exécuteur du moteur
    d'actions de sentinel-core, donc après une proposition approuvée."""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(404, "Tâche inconnue.")
    if task["status"] != "done":
        raise HTTPException(409, f"La tâche n'est pas terminée (statut : {task['status']}).")
    if not task.get("files_changed"):
        raise HTTPException(409, "Aucune modification à pousser.")
    if not GITHUB_TOKEN:
        raise HTTPException(
            409, "Aucun GITHUB_TOKEN configuré — ajoute-le dans .env pour pouvoir pousser."
        )
    repo_dir = WORKSPACE / task_id / "repo"
    if not repo_dir.is_dir():
        raise HTTPException(409, "L'espace de travail de la tâche a été nettoyé.")
    push_url = task["url"].replace("https://", f"https://x-access-token:{GITHUB_TOKEN}@") + ".git"
    await _run_cmd(repo_dir, "git", "push", push_url, f"{task['branch']}:{task['branch']}")
    task["pushed"] = True
    _save()
    return {"ok": True, "branch": task["branch"],
            "compare_url": f"{task['url']}/compare/{task['branch']}"}


# ── Exécution d'une tâche ────────────────────────────────────────────────

async def _run_task(task_id: str) -> None:
    task = _tasks[task_id]
    async with _busy_lock:
        task["status"] = "running"
        _save()
        task_dir = WORKSPACE / task_id
        repo_dir = task_dir / "repo"
        try:
            _prune_old_tasks()
            task_dir.mkdir(parents=True, exist_ok=True)
            log.info("Tâche %s : clone de %s", task_id, task["url"])
            await _run_cmd(task_dir, "git", "clone", "--depth", "50", task["url"], "repo",
                           timeout=300)
            base = (await _run_cmd(repo_dir, "git", "rev-parse", "HEAD")).strip()
            await _run_cmd(repo_dir, "git", "checkout", "-b", task["branch"])

            # HOME dédié : configuration git/claude isolée par tâche
            env = {"HOME": str(task_dir)}
            await _run_cmd(repo_dir, "git", "config", "--global", "user.name", "Sentinel", env=env)
            await _run_cmd(repo_dir, "git", "config", "--global", "user.email",
                           "sentinel@nebula.local", env=env)

            prompt = (
                f"Tu travailles dans un clone jetable du dépôt « {task['repo']} », "
                f"sur la branche {task['branch']}.\n"
                f"Objectif : {task['instruction']}\n\n"
                "Contraintes : modifie uniquement les fichiers de ce dépôt ; ne pousse rien "
                "(aucun push) ; ne touche pas à la configuration git. Termine ta réponse par "
                "un court résumé en français de ce que tu as fait et pourquoi."
            )
            log.info("Tâche %s : lancement de Claude Code", task_id)
            output = await _run_cmd(
                repo_dir, "claude", "-p", prompt,
                "--output-format", "json", "--dangerously-skip-permissions",
                timeout=TASK_TIMEOUT, env=env,
            )
            summary = _claude_summary(output)

            # Commit de tout ce qui a changé (Claude committe parfois lui-même, pas toujours)
            await _run_cmd(repo_dir, "git", "add", "-A")
            status = await _run_cmd(repo_dir, "git", "status", "--porcelain")
            if status.strip():
                title = task["instruction"].splitlines()[0][:70]
                await _run_cmd(repo_dir, "git", "commit", "-m", f"Sentinel : {title}", env=env)

            files = (await _run_cmd(
                repo_dir, "git", "diff", "--name-only", f"{base}..HEAD"
            )).split()
            diff = await _run_cmd(repo_dir, "git", "diff", f"{base}..HEAD")
            (task_dir / "diff.patch").write_text(diff[:DIFF_MAX_BYTES], encoding="utf-8")

            task["files_changed"] = files[:100]
            task["summary"] = summary[:3000] if summary else "(pas de résumé)"
            if not files:
                task["summary"] += "\n(Aucun fichier modifié.)"
            task["status"] = "done"
            log.info("Tâche %s : terminée, %d fichier(s) modifié(s)", task_id, len(files))
        except Exception as exc:
            log.exception("Tâche %s en échec", task_id)
            task["status"] = "failed"
            task["error"] = _scrub(str(exc))[:1500]
        finally:
            task["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _save()


def _claude_summary(output: str) -> str:
    """Sortie `--output-format json` : on veut le champ result, sinon la fin brute."""
    try:
        data = json.loads(output)
        if isinstance(data, dict) and data.get("result"):
            return str(data["result"])
    except ValueError:
        pass
    return output[-2000:]


def _prune_old_tasks() -> None:
    """Garde les KEEP_TASKS dernières tâches ; supprime les espaces de travail au-delà."""
    done = sorted(
        (t for t in _tasks.values() if t["status"] in ("done", "failed")),
        key=lambda t: t["created_at"],
    )
    for task in done[:-KEEP_TASKS] if len(done) > KEEP_TASKS else []:
        shutil.rmtree(WORKSPACE / task["id"], ignore_errors=True)
        _tasks.pop(task["id"], None)
