"""Phase 3B : atelier de développement — client, moteur, veilleur, outils."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.devwork.watcher import DevWatcher
from app.devwork.worker_client import WorkerClient, WorkerError
from app.store import Store


@pytest.fixture()
async def env(fake_worker, tmp_path):
    client = WorkerClient(f"http://127.0.0.1:{fake_worker.port}", timeout=5)
    store = Store(tmp_path / "dev.db")
    await store.open()
    engine = ActionEngine(build_registry(None, None, None, worker=client), store)
    announced: list[tuple] = []

    async def announce(text, severity, speak=True):
        announced.append((text, severity, speak))

    watcher = DevWatcher(client, engine, announce)
    yield SimpleNamespace(
        client=client, store=store, engine=engine, watcher=watcher,
        announced=announced, fake=fake_worker,
    )
    await client.close()
    await store.close()


async def test_client_et_liste_blanche(env):
    task = await env.client.start_task("loggia", "Corrige la carte météo")
    assert task["repo"] == "loggia" and task["branch"].startswith("sentinel/")
    assert (await env.client.get_task(task["id"]))["instruction"] == "Corrige la carte météo"

    with pytest.raises(WorkerError, match="liste blanche"):
        await env.client.start_task("https://github.com/evil/repo", "pwn")


async def test_journal_en_direct_incremental(env):
    task = await env.client.start_task("loggia", "x")
    env.fake.add_log(task["id"], "Clone de loggia…", "▸ modifie README.md")

    data = await env.client.get_log(task["id"])
    assert [entry["line"] for entry in data["lines"]] == ["Clone de loggia…", "▸ modifie README.md"]
    assert data["next"] == 2 and data["status"] == "queued"

    # Lecture incrémentale : seules les nouvelles lignes reviennent
    env.fake.finish_task(task["id"], ["README.md"])
    data2 = await env.client.get_log(task["id"], after=data["next"])
    assert len(data2["lines"]) == 1 and "✔" in data2["lines"][0]["line"]
    assert data2["status"] == "done"


async def test_lancement_par_ordre_direct_journalise(env):
    outcome = await env.engine.run_direct(
        "dev.task", {"repo": "atrium", "instruction": "Ajoute un footer"},
        utterance="corrige le footer d'atrium", source="text (via LLM)",
    )
    assert outcome.ok and "lancée" in outcome.text
    journal = await env.store.list_journal()
    assert journal[0]["action_id"] == "dev.task"
    assert "corrige le footer" in journal[0]["authorization"]


async def test_push_impossible_en_ordre_direct(env):
    task = await env.client.start_task("loggia", "x")
    env.fake.finish_task(task["id"], ["carte.yaml"])

    outcome = await env.engine.run_direct(
        "dev.push", {"task_id": task["id"]}, utterance="pousse", source="text"
    )
    assert outcome.status == "refused"  # refus technique : propositions uniquement
    assert env.fake.pushes == []


async def test_push_via_proposition_approuvee(env):
    task = await env.client.start_task("loggia", "x")
    env.fake.finish_task(task["id"], ["carte.yaml"])

    proposal, _ = await env.engine.propose(
        title="Pousser la branche", justification="test", risk="medium",
        action_id="dev.push", params={"task_id": task["id"]},
    )
    updated, message = await env.engine.decide(proposal["num"], "approve", via="ui")
    assert updated["status"] == "done"
    assert "compare" in updated["result"]
    assert env.fake.pushes == [task["id"]]


async def test_veilleur_pastille_atelier(env, monkeypatch):
    """La pastille ⚒ suit la tâche en cours — et s'éteint si le worker disparaît."""
    seen: list[str | None] = []

    async def rec(running):
        seen.append(running["id"] if running else None)

    async def announce(text, severity, speak=True):
        pass

    watcher = DevWatcher(env.client, env.engine, announce, on_running_change=rec)

    await watcher._tick()
    assert seen == []  # rien ne tourne, rien à signaler

    task = await env.client.start_task("loggia", "x")
    await watcher._tick()
    await watcher._tick()  # pas de changement → pas de re-notification
    assert seen == [task["id"]]

    env.fake.finish_task(task["id"], ["a.yaml"])
    await watcher._tick()
    assert seen == [task["id"], None]

    # Worker injoignable pendant une « tâche en cours » : pastille éteinte
    task2 = await env.client.start_task("loggia", "y")
    await watcher._tick()
    assert seen[-1] == task2["id"]

    async def boom():
        raise WorkerError("injoignable")

    monkeypatch.setattr(env.client, "list_tasks", boom)
    await watcher._tick()
    assert seen[-1] is None


async def test_reponse_worker_illisible(env, monkeypatch):
    """Un worker qui répond du non-JSON donne une WorkerError propre, pas un crash."""
    from types import SimpleNamespace as NS

    async def bad_request(method, path, timeout=None, **kwargs):
        return NS(json=lambda: (_ for _ in ()).throw(ValueError("bad")), status_code=200)

    monkeypatch.setattr(env.client, "_request", bad_request)
    with pytest.raises(WorkerError, match="illisible"):
        await env.client.list_tasks()


async def test_veilleur_annonce_et_propose(env):
    task = await env.client.start_task("loggia", "Corrige la carte météo")
    await env.watcher._tick()
    assert env.announced == []  # rien tant que la tâche tourne

    env.fake.finish_task(task["id"], ["ui/carte.yaml", "README.md"], "J'ai corrigé la carte.")
    await env.watcher._tick()

    assert len(env.announced) == 1
    text, severity, speak = env.announced[0]
    assert "terminée" in text and "2 fichier(s)" in text and speak is False

    pending = await env.store.list_proposals("pending")
    assert pending and pending[0]["action_id"] == "dev.push"
    assert pending[0]["params"] == {"task_id": task["id"]}

    await env.watcher._tick()
    assert len(env.announced) == 1  # pas de double annonce


async def test_veilleur_sans_github_token(env):
    env.fake.push_possible = False
    task = await env.client.start_task("loggia", "x")
    env.fake.finish_task(task["id"], ["a.yaml"])
    await env.watcher._tick()

    # Annonce avec la marche à suivre, mais AUCUNE proposition morte-née
    assert "GITHUB_TOKEN" in env.announced[0][0]
    assert await env.store.list_proposals("pending") == []


async def test_veilleur_echec_sans_proposition(env):
    task = await env.client.start_task("atrium", "x")
    env.fake.fail_task(task["id"], "npm introuvable")
    await env.watcher._tick()

    assert env.announced[0][1] == "warning"
    assert "npm introuvable" in env.announced[0][0]
    assert await env.store.list_proposals("pending") == []


async def test_outils_llm_dev(env, tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    from app.brain.toolbox import Toolbox
    from app.ha.protocols import ProtocolBook

    toolbox = Toolbox(None, env.engine, ProtocolBook([]), env.store, worker=env.client)

    content, is_error = await toolbox.run(
        "lancer_tache_dev", {"depot": "loggia", "instruction": "Répare le thème sombre"},
        utterance="répare le thème sombre de loggia", source="text",
    )
    assert not is_error and "lancée" in content

    content, is_error = await toolbox.run("etat_taches_dev", {}, utterance="", source="text")
    assert not is_error and "loggia" in content

    task_id = list(env.fake.tasks)[-1]
    content, is_error = await toolbox.run(
        "lire_diff_dev", {"id": task_id}, utterance="", source="text"
    )
    assert not is_error and "correctif" in content
