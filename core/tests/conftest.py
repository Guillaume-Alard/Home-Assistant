"""Fixtures : faux serveurs Wyoming (whisper/piper) sur ports éphémères.

Ils parlent le vrai protocole Wyoming (via la bibliothèque `wyoming`), dans un
thread dédié avec sa propre boucle asyncio — le serveur Sentinel sous test s'y
connecte comme aux vrais conteneurs.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # core/ → import app

from wyoming.asr import Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import async_read_event, async_write_event
from wyoming.tts import Synthesize

FAKE_TRANSCRIPT = "allume la lumière du salon"
FAKE_TTS_CHUNKS = [b"\x00\x01" * 512, b"\x02\x03" * 512]


class FakeWyoming:
    def __init__(self) -> None:
        self.whisper_port: int | None = None
        self.piper_port: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(5), "faux serveurs Wyoming non démarrés"

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start_servers())
        self._loop.run_forever()

    async def _start_servers(self) -> None:
        whisper = await asyncio.start_server(self._handle_whisper, "127.0.0.1", 0)
        piper = await asyncio.start_server(self._handle_piper, "127.0.0.1", 0)
        self.whisper_port = whisper.sockets[0].getsockname()[1]
        self.piper_port = piper.sockets[0].getsockname()[1]
        self._ready.set()

    async def _handle_whisper(self, reader, writer) -> None:
        try:
            while True:
                event = await async_read_event(reader)
                if event is None:
                    return
                if AudioStop.is_type(event.type):
                    await async_write_event(Transcript(text=FAKE_TRANSCRIPT).event(), writer)
                    await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def _handle_piper(self, reader, writer) -> None:
        try:
            while True:
                event = await async_read_event(reader)
                if event is None:
                    return
                if Synthesize.is_type(event.type):
                    await async_write_event(
                        AudioStart(rate=22050, width=2, channels=1).event(), writer
                    )
                    for chunk in FAKE_TTS_CHUNKS:
                        await async_write_event(
                            AudioChunk(audio=chunk, rate=22050, width=2, channels=1).event(),
                            writer,
                        )
                    await async_write_event(AudioStop().event(), writer)
                    await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


@pytest.fixture(scope="session")
def fake_wyoming() -> FakeWyoming:
    server = FakeWyoming()
    server.start()
    yield server
    server.stop()


# ── Faux Home Assistant (protocole WebSocket réel) ──────────────────────

import json as _json

import websockets as _websockets

FAKE_HA_TOKEN = "jeton-de-test"

DEFAULT_AREAS = {"area_salon": "Salon", "area_chambre": "Chambre"}
DEFAULT_ENTITY_AREA = {
    "light.salon": "area_salon",
    "light.chambre": "area_chambre",
    "cover.salon": "area_salon",
    "sensor.temp_salon": "area_salon",
}


def default_states() -> dict[str, dict]:
    return {
        "light.salon": {"entity_id": "light.salon", "state": "off",
                        "attributes": {"friendly_name": "Plafonnier salon"}},
        "light.chambre": {"entity_id": "light.chambre", "state": "off",
                          "attributes": {"friendly_name": "Lampe chambre"}},
        "cover.salon": {"entity_id": "cover.salon", "state": "closed",
                        "attributes": {"friendly_name": "Volet salon"}},
        "sensor.temp_salon": {"entity_id": "sensor.temp_salon", "state": "21.5",
                              "attributes": {"friendly_name": "Température salon",
                                             "device_class": "temperature"}},
        "lock.entree": {"entity_id": "lock.entree", "state": "locked",
                        "attributes": {"friendly_name": "Porte d'entrée"}},
        "alarm_control_panel.maison": {"entity_id": "alarm_control_panel.maison",
                                       "state": "disarmed",
                                       "attributes": {"friendly_name": "Alarme"}},
        "input_boolean.test_sentinel": {"entity_id": "input_boolean.test_sentinel",
                                        "state": "off",
                                        "attributes": {"friendly_name": "Test Sentinel"}},
        # Volontairement SANS pièce assignée (cas réel : capteur oublié hors zone)
        "binary_sensor.capteur_porte_entree": {"entity_id": "binary_sensor.capteur_porte_entree",
                                               "state": "off",
                                               "attributes": {"friendly_name": "Capteur porte d'entrée",
                                                              "device_class": "door"}},
    }


class FakeHA:
    """Faux serveur Home Assistant : auth, états, registres, services, événements."""

    def __init__(self):
        self.token = FAKE_HA_TOKEN
        self.states = default_states()
        self.areas = dict(DEFAULT_AREAS)
        self.entity_area = dict(DEFAULT_ENTITY_AREA)
        self.calls: list[tuple] = []  # (domain, service, data, target)
        self.port: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._clients: set = set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(5), "faux serveur HA non démarré"

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start_server())
        self._loop.run_forever()

    async def _start_server(self) -> None:
        server = await _websockets.serve(self._handler, "127.0.0.1", 0)
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()

    async def _handler(self, ws) -> None:
        try:
            await ws.send(_json.dumps({"type": "auth_required"}))
            msg = _json.loads(await ws.recv())
            if msg.get("access_token") != self.token:
                await ws.send(_json.dumps({"type": "auth_invalid", "message": "bad token"}))
                return
            await ws.send(_json.dumps({"type": "auth_ok"}))
            self._clients.add(ws)
            async for raw in ws:
                m = _json.loads(raw)
                mid, mtype = m.get("id"), m.get("type")
                if mtype == "ping":
                    await ws.send(_json.dumps({"id": mid, "type": "pong"}))
                    continue
                result = None
                if mtype == "get_states":
                    result = list(self.states.values())
                elif mtype == "config/area_registry/list":
                    result = [{"area_id": k, "name": v} for k, v in self.areas.items()]
                elif mtype == "config/entity_registry/list":
                    result = [
                        {"entity_id": e, "area_id": a, "device_id": None}
                        for e, a in self.entity_area.items()
                    ]
                elif mtype == "config/device_registry/list":
                    result = []
                elif mtype == "call_service":
                    self.calls.append((
                        m.get("domain"), m.get("service"),
                        m.get("service_data"), m.get("target"),
                    ))
                    result = {}
                await ws.send(_json.dumps(
                    {"id": mid, "type": "result", "success": True, "result": result}
                ))
        except Exception:
            pass
        finally:
            self._clients.discard(ws)

    def push_state_changed(self, entity_id: str, new_state: dict, old_state: dict | None) -> None:
        """Injecte un événement state_changed (thread-safe)."""
        assert self._loop is not None
        self.states[entity_id] = new_state
        event = {
            "id": 1, "type": "event",
            "event": {
                "event_type": "state_changed",
                "data": {"entity_id": entity_id, "new_state": new_state, "old_state": old_state},
            },
        }

        async def _send() -> None:
            for ws in list(self._clients):
                try:
                    await ws.send(_json.dumps(event))
                except Exception:
                    pass

        asyncio.run_coroutine_threadsafe(_send(), self._loop)


@pytest.fixture()
def fake_ha() -> FakeHA:
    server = FakeHA()
    server.start()
    yield server
    server.stop()


def make_ha_stub():
    """HAClient peuplé sans réseau : caches remplis, call_service enregistré.

    Pour les tests d'intents/outils/alertes qui n'ont pas besoin du vrai
    protocole WebSocket (couvert par test_ha_client.py).
    """
    from app.ha.client import HAClient

    ha = HAClient("http://test-local", "jeton")
    ha.connected = True
    ha._states = default_states()
    ha._areas = dict(DEFAULT_AREAS)
    ha._entity_area = dict(DEFAULT_ENTITY_AREA)

    calls: list[tuple] = []

    async def record(domain, service, data=None, target=None):
        calls.append((domain, service, data, target))

    ha.call_service = record  # écritures capturées au lieu de partir sur le réseau
    return ha, calls


# ── Faux proxy Docker (HTTP minimal, mêmes chemins que l'API Docker) ─────

_FRAME1 = b"\x01\x00\x00\x00" + (11).to_bytes(4, "big") + b"ligne info\n"
_FRAME2 = b"\x02\x00\x00\x00" + (13).to_bytes(4, "big") + b"ligne erreur\n"


class FakeDockerProxy:
    def __init__(self):
        self.port: int | None = None
        self.restarts: list[str] = []
        self.containers = [
            {"Id": "aaa111aaa111", "Names": ["/plex"], "State": "exited",
             "Status": "Exited (1) 2 hours ago", "Image": "plex:latest", "Ports": []},
            {"Id": "bbb222bbb222", "Names": ["/frigate"], "State": "running",
             "Status": "Up 5 days (unhealthy)", "Image": "frigate",
             "Ports": [{"IP": "0.0.0.0", "PublicPort": 5000, "PrivatePort": 5000}]},
            {"Id": "ccc333ccc333", "Names": ["/sentinel-core"], "State": "running",
             "Status": "Up 2 days", "Image": "sentinel",
             "Ports": [{"IP": "0.0.0.0", "PublicPort": 8443, "PrivatePort": 8443}]},
        ]
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(5), "faux proxy Docker non démarré"

    def stop(self):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start_server())
        self._loop.run_forever()

    async def _start_server(self):
        server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()

    async def _handle(self, reader, writer):
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                data += chunk
            method, target, _ = data.split(b"\r\n", 1)[0].decode().split(" ", 2)
            status, ctype, body = self._route(method, target.split("?", 1)[0])
            head = (
                f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            )
            writer.write(head.encode() + body)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    def _find(self, cid: str):
        return next((c for c in self.containers if c["Id"].startswith(cid)), None)

    def _route(self, method: str, path: str):
        if path == "/_ping":
            return 200, "text/plain", b"OK"
        if path == "/containers/json":
            return 200, "application/json", _json.dumps(self.containers).encode()
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "containers":
            container = self._find(parts[1])
            if container is None:
                return 404, "text/plain", b"no such container"
            if parts[2] == "stats":
                return 200, "application/json", _json.dumps(
                    {"memory_stats": {"usage": 200 * 1_048_576}}
                ).encode()
            if parts[2] == "logs":
                return 200, "application/octet-stream", _FRAME1 + _FRAME2
            if parts[2] == "restart" and method == "POST":
                self.restarts.append(container["Names"][0].lstrip("/"))
                return 204, "text/plain", b""
        return 404, "text/plain", b"not found"


@pytest.fixture()
def fake_docker() -> FakeDockerProxy:
    server = FakeDockerProxy()
    server.start()
    yield server
    server.stop()


# ── Faux worker de développement (HTTP minimal, même API que worker/server.py) ──


class FakeWorker:
    def __init__(self):
        self.port: int | None = None
        self.tasks: dict[str, dict] = {}
        self.pushes: list[str] = []
        self.push_possible = True
        self._counter = 0
        self._loop = None
        self._thread = None
        self._ready = threading.Event()

    # Pilotage depuis les tests
    def finish_task(self, task_id: str, files: list[str], summary: str = "Travail terminé.") -> None:
        self.tasks[task_id].update(status="done", files_changed=files, summary=summary)

    def fail_task(self, task_id: str, error: str) -> None:
        self.tasks[task_id].update(status="failed", error=error)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(5), "faux worker non démarré"

    def stop(self):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start_server())
        self._loop.run_forever()

    async def _start_server(self):
        server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()

    async def _handle(self, reader, writer):
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = await reader.read(4096)
                if not chunk:
                    return
                data += chunk
            head, _, body = data.partition(b"\r\n\r\n")
            headers = head.decode(errors="replace")
            length = 0
            for line in headers.split("\r\n")[1:]:
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
            while len(body) < length:
                body += await reader.read(4096)
            method, target, _ = headers.split("\r\n", 1)[0].split(" ", 2)
            status, payload = self._route(method, target.split("?", 1)[0], body)
            raw = payload if isinstance(payload, bytes) else _json.dumps(payload).encode()
            ctype = "text/plain" if isinstance(payload, bytes) else "application/json"
            writer.write((
                f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\n"
                f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n"
            ).encode() + raw)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    def _route(self, method: str, path: str, body: bytes):
        if path == "/health":
            return 200, {"status": "ok", "repos": ["atrium", "loggia"], "busy": False,
                         "auth": "clé API", "push_possible": self.push_possible}
        if path == "/tasks" and method == "POST":
            req = _json.loads(body or b"{}")
            alias = str(req.get("repo", "")).rsplit("/", 1)[-1].removesuffix(".git").lower()
            if alias not in ("atrium", "loggia"):
                return 403, {"detail": f"Dépôt hors liste blanche : « {req.get('repo')} »."}
            self._counter += 1
            task_id = f"t{self._counter}"
            self.tasks[task_id] = {
                "id": task_id, "repo": alias, "instruction": req.get("instruction", ""),
                "status": "queued", "branch": f"sentinel/{task_id}",
                "created_at": "2026-09-01T10:00:00", "announced": False, "pushed": False,
            }
            return 200, self.tasks[task_id]
        if path == "/tasks" and method == "GET":
            return 200, list(self.tasks.values())
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "tasks":
            task = self.tasks.get(parts[1])
            if task is None:
                return 404, {"detail": "Tâche inconnue."}
            if len(parts) == 2:
                return 200, task
            if parts[2] == "diff":
                return 200, b"diff --git a/x b/x\n+correctif"
            if parts[2] == "announced" and method == "POST":
                task["announced"] = True
                return 200, {"ok": True}
            if parts[2] == "push" and method == "POST":
                if task.get("status") != "done" or not task.get("files_changed"):
                    return 409, {"detail": "Rien à pousser."}
                self.pushes.append(task["id"])
                task["pushed"] = True
                return 200, {"ok": True, "branch": task["branch"],
                             "compare_url": f"https://github.com/Alardware/{task['repo']}/compare/{task['branch']}"}
        return 404, {"detail": "not found"}


@pytest.fixture()
def fake_worker() -> FakeWorker:
    server = FakeWorker()
    server.start()
    yield server
    server.stop()


PROTOCOLS_TEST_YML = """\
test:
  nom: "Test"
  risque: faible
  annonce: "Protocole de test exécuté."
  actions:
    - service: persistent_notification.create
      data: { title: "Sentinel", message: "ok" }

verrou:
  nom: "Verrou"
  risque: sensible
  annonce: "Verrouillage général effectué."
  actions:
    - service: lock.lock
      target: { entity_id: lock.entree }
"""
