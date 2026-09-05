"""Moniteurs 3A : Docker (via faux proxy), système, agrégateur santé, audit."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import make_ha_stub

from app.config import Settings
from app.monitors.docker import DockerError, DockerMonitor
from app.monitors.health import HealthService
from app.monitors.system import read_system


@pytest.fixture()
async def docker(fake_docker):
    monitor = DockerMonitor(f"http://127.0.0.1:{fake_docker.port}", timeout=5)
    yield monitor
    await monitor.close()


async def test_ping_et_liste(docker):
    assert await docker.ping() is True
    containers = await docker.containers()
    assert [c["nom"] for c in containers] == ["frigate", "plex", "sentinel-core"]
    plex = next(c for c in containers if c["nom"] == "plex")
    assert plex["etat"] == "exited"
    frigate = next(c for c in containers if c["nom"] == "frigate")
    assert frigate["ports_publies"] == ["0.0.0.0:5000→5000"]


async def test_logs_demultiplexes(docker):
    logs = await docker.logs("plex", tail=50)
    assert logs == "ligne info\nligne erreur\n"  # en-têtes de trames retirés

    with pytest.raises(DockerError):
        await docker.logs("inexistant")


async def test_memoire(docker):
    memoire = await docker.memory_usage()
    assert memoire.get("frigate") == 200  # Mo, conteneurs en marche uniquement
    assert "plex" not in memoire


async def test_restart_et_garde_fous(docker, fake_docker):
    result = await docker.restart_container("plex")
    assert "redémarré" in result
    assert fake_docker.restarts == ["plex"]

    with pytest.raises(DockerError):  # jamais soi-même
        await docker.restart_container("sentinel-core")
    assert fake_docker.restarts == ["plex"]


def test_lecture_systeme():
    out = read_system()
    assert out["coeurs"] >= 1
    assert len(out["charge"]) == 3
    assert out["ram"]["totale_mo"] > 0


@pytest.fixture()
async def health(fake_docker, tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    settings = Settings.from_env()
    ha, _calls = make_ha_stub()
    ha._states["light.salon"]["state"] = "unavailable"  # une entité en panne
    docker = DockerMonitor(f"http://127.0.0.1:{fake_docker.port}", timeout=5)
    service = HealthService(settings, ha, docker, atrium=None)
    yield SimpleNamespace(service=service, ha=ha)
    await service.close()


async def test_snapshot_et_resume(health):
    snap = await health.service.snapshot()
    assert snap["nova"]["connectee"] is True
    assert "light.salon" in snap["nova"]["indisponibles"]
    assert snap["docker"]["total"] == 3
    assert {p["nom"] for p in snap["docker"]["problemes"]} == {"plex", "frigate"}

    resume = await health.service.resume_texte()
    assert "Nova : connectée" in resume
    assert "À surveiller" in resume
    assert "Nebula : charge" in resume


async def test_audit_classe_les_constats(health):
    audit = await health.service.audit()
    assert audit["ok"] is False
    gravites = [c["gravite"] for c in audit["constats"]]
    assert gravites == sorted(gravites, key=["critique", "attention", "info"].index)

    sujets = " | ".join(c["sujet"] for c in audit["constats"])
    assert "frigate" in sujets   # unhealthy → critique
    assert "plex" in sujets      # exited → attention
    assert "Surface réseau" in sujets

    frigate = next(c for c in audit["constats"] if "frigate" in c["sujet"])
    assert frigate["gravite"] == "critique"
    assert "redémarrage" in frigate.get("action", "")


async def test_rapport_quotidien(health):
    rapport = await health.service.rapport_quotidien(propositions_en_attente=2)
    assert rapport.startswith("Rapport du ")
    assert "2 proposition(s)" in rapport
    assert "⚠" in rapport  # les constats non-info remontent


async def test_sante_sans_aucun_moniteur(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    service = HealthService(Settings.from_env())
    resume = await service.resume_texte()
    assert "Nebula" in resume  # le système local est toujours lisible