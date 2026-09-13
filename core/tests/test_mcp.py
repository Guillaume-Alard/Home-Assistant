"""Brique 3 — MCP (couche d'extension).

Deux niveaux de tests :
  • le CLIENT (`McpManager`) contre un faux serveur MCP « Streamable HTTP » —
    initialize, session, tools/list, tools/call, erreurs, SSE ;
  • la SÉCURITÉ (outils `mcp_outils`/`mcp_appeler`) — le contrat fondateur tenu :
    serveur « lecture » → appel direct ; serveur « proposition » → validation
    avant exécution ; sensible → interface uniquement ; réservé au propriétaire.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from types import SimpleNamespace

import pytest
from conftest import PROTOCOLS_TEST_YML, make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.brain.mcp import McpError, McpManager, McpServer, load_mcp_config
from app.brain.toolbox import Toolbox
from app.ha.protocols import ProtocolBook
from app.identity import OWNER, UNKNOWN, Speaker
from app.store import Store

_HOUSEHOLD = Speaker(key="cam", name="Camille", known=True, is_owner=False, score=0.9)


# ── Faux serveur MCP (Streamable HTTP, JSON-RPC 2.0) ───────────────────────


class FakeMcpServer:
    """Parle le sous-ensemble MCP dont Sentinel a besoin. `sse=True` répond en
    text/event-stream ; sinon en application/json. Enregistre les requêtes."""

    def __init__(self, *, sse: bool = False, tool_error: bool = False):
        self.sse = sse
        self.tool_error = tool_error
        self.requests: list[dict] = []
        self.session_ids: list[str | None] = []
        self.port: int | None = None
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._counter = 0

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(5), "faux serveur MCP non démarré"

    def stop(self):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

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
            session = None
            for line in headers.split("\r\n")[1:]:
                low = line.lower()
                if low.startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
                elif low.startswith("mcp-session-id:"):
                    session = line.split(":", 1)[1].strip()
            while len(body) < length:
                body += await reader.read(4096)
            msg = json.loads(body or b"{}")
            self.requests.append(msg)
            self.session_ids.append(session)
            status, extra_headers, payload = self._route(msg)
            if payload is None:  # notification → 202 sans corps
                writer.write((
                    f"HTTP/1.1 {status} X\r\n{extra_headers}"
                    f"Content-Length: 0\r\nConnection: close\r\n\r\n"
                ).encode())
                await writer.drain()
                return
            if self.sse:
                raw = f"event: message\r\ndata: {json.dumps(payload)}\r\n\r\n".encode()
                ctype = "text/event-stream"
            else:
                raw = json.dumps(payload).encode()
                ctype = "application/json"
            writer.write((
                f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\n"
                f"{extra_headers}Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n"
            ).encode() + raw)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    def _route(self, msg: dict):
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            hdr = "Mcp-Session-Id: sess-123\r\n"
            return 200, hdr, {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "fake-mcp", "version": "1"},
            }}
        if method == "notifications/initialized":
            return 202, "", None
        if method == "tools/list":
            return 200, "", {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "recherche", "description": "Cherche un truc",
                 "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
            ]}}
        if method == "resources/list":
            return 200, "", {"jsonrpc": "2.0", "id": mid, "result": {"resources": [
                {"uri": "mem://notes", "name": "Notes", "description": "Bloc-notes"},
            ]}}
        if method == "resources/read":
            uri = (msg.get("params") or {}).get("uri")
            return 200, "", {"jsonrpc": "2.0", "id": mid, "result": {"contents": [
                {"uri": uri, "mimeType": "text/plain", "text": f"contenu de {uri}"},
            ]}}
        if method == "prompts/list":
            return 200, "", {"jsonrpc": "2.0", "id": mid, "result": {"prompts": [
                {"name": "resume", "description": "Résume un texte", "arguments": []},
            ]}}
        if method == "prompts/get":
            args = (msg.get("params") or {}).get("arguments") or {}
            return 200, "", {"jsonrpc": "2.0", "id": mid, "result": {"messages": [
                {"role": "user", "content": {"type": "text", "text": f"Résume ceci : {args}"}},
            ]}}
        if method == "tools/call":
            params = msg.get("params") or {}
            if self.tool_error:
                return 200, "", {"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "boum côté serveur"}], "isError": True,
                }}
            echo = json.dumps(params.get("arguments") or {}, ensure_ascii=False)
            return 200, "", {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": f"outil {params.get('name')} → {echo}"}],
            }}
        return 200, "", {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "méthode inconnue"}}


@pytest.fixture()
def mcp_server():
    server = FakeMcpServer()
    server.start()
    yield server
    server.stop()


# ── Client McpManager ──────────────────────────────────────────────────────


async def test_catalogue_liste_les_outils(mcp_server):
    mgr = McpManager([McpServer(name="local", url=mcp_server.url, mode="lecture")])
    try:
        catalog = await mgr.catalog()
    finally:
        await mgr.aclose()
    assert set(catalog) == {"local"}
    assert catalog["local"]["mode"] == "lecture"
    assert [t["nom"] for t in catalog["local"]["outils"]] == ["recherche"]


async def test_appel_outil_et_session(mcp_server):
    mgr = McpManager([McpServer(name="local", url=mcp_server.url, mode="lecture")])
    try:
        out = await mgr.call_tool("local", "recherche", {"q": "météo"})
    finally:
        await mgr.aclose()
    assert "outil recherche" in out and "météo" in out
    # La session ouverte à l'initialisation est réutilisée sur les appels suivants.
    methods = [r.get("method") for r in mcp_server.requests]
    assert methods[0] == "initialize" and "tools/call" in methods
    call_idx = methods.index("tools/call")
    assert mcp_server.session_ids[call_idx] == "sess-123"


async def test_outil_en_erreur_leve_mcp_error(mcp_server):
    mcp_server.tool_error = True
    mgr = McpManager([McpServer(name="local", url=mcp_server.url, mode="lecture")])
    try:
        with pytest.raises(McpError):
            await mgr.call_tool("local", "recherche", {})
    finally:
        await mgr.aclose()


async def test_serveur_injoignable_ne_casse_pas_le_catalogue():
    mgr = McpManager([McpServer(name="mort", url="http://127.0.0.1:9/mcp", mode="lecture")])
    try:
        catalog = await mgr.catalog()
        assert "erreur" in catalog["mort"]  # signalé, pas une exception
        with pytest.raises(McpError):
            await mgr.call_tool("mort", "x", {})
    finally:
        await mgr.aclose()


async def test_reponse_sse_est_lue():
    server = FakeMcpServer(sse=True)
    server.start()
    try:
        mgr = McpManager([McpServer(name="local", url=server.url, mode="lecture")])
        out = await mgr.call_tool("local", "recherche", {"q": "x"})
        await mgr.aclose()
        assert "outil recherche" in out
    finally:
        server.stop()


async def test_ressource_et_prompt_http(mcp_server):
    mgr = McpManager([McpServer(name="local", url=mcp_server.url, mode="lecture")])
    try:
        cat = await mgr.catalog()
        assert [r["uri"] for r in cat["local"]["ressources"]] == ["mem://notes"]
        assert [p["nom"] for p in cat["local"]["invites"]] == ["resume"]
        assert "contenu de mem://notes" in await mgr.read_resource("local", "mem://notes")
        assert "Résume ceci" in await mgr.get_prompt("local", "resume", {"x": "y"})
    finally:
        await mgr.aclose()


# Faux serveur MCP « stdio » : un script Python qui parle JSON-RPC sur stdin/stdout.
STDIO_SERVER = r'''
import sys, json
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        send({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":"2025-06-18",
              "capabilities":{"tools":{},"resources":{}},"serverInfo":{"name":"fake-stdio","version":"1"}}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({"jsonrpc":"2.0","id":mid,"result":{"tools":[{"name":"echo","description":"Echo","inputSchema":{"type":"object"}}]}})
    elif method == "tools/call":
        args = (msg.get("params") or {}).get("arguments") or {}
        send({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":"stdio echo "+json.dumps(args, ensure_ascii=False)}]}})
    elif method == "resources/list":
        send({"jsonrpc":"2.0","id":mid,"result":{"resources":[{"uri":"mem://x","name":"X"}]}})
    elif method == "resources/read":
        uri = (msg.get("params") or {}).get("uri")
        send({"jsonrpc":"2.0","id":mid,"result":{"contents":[{"uri":uri,"mimeType":"text/plain","text":"lu:"+str(uri)}]}})
    else:
        send({"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":"inconnu"}})
'''


async def test_stdio_transport(tmp_path):
    script = tmp_path / "fake_stdio_mcp.py"
    script.write_text(STDIO_SERVER, encoding="utf-8")
    mgr = McpManager([McpServer(
        name="loc", transport="stdio", command=sys.executable, args=(str(script),), mode="lecture",
    )])
    try:
        cat = await mgr.catalog()
        assert cat["loc"]["transport"] == "stdio"
        assert [t["nom"] for t in cat["loc"]["outils"]] == ["echo"]
        out = await mgr.call_tool("loc", "echo", {"a": 1})
        assert "stdio echo" in out and '"a"' in out
        assert "lu:mem://x" in await mgr.read_resource("loc", "mem://x")
        # Le serveur stdio n'annonce pas « prompts » → pas d'appel, liste vide.
        assert "invites" not in cat["loc"]
    finally:
        await mgr.aclose()


def test_load_mcp_config_stdio(tmp_path):
    path = tmp_path / "mcp.yml"
    path.write_text(
        "servers:\n"
        "  - nom: local\n"
        "    transport: stdio\n"
        "    commande: mcp-server\n"
        "    args: ['--flag', 'x']\n"
        "  - nom: sans-commande\n"      # stdio sans commande → ignoré
        "    transport: stdio\n"
        "  - nom: http-sans-url\n"       # http sans url → ignoré
        "    transport: http\n",
        encoding="utf-8",
    )
    servers = {s.name: s for s in load_mcp_config(path)}
    assert set(servers) == {"local"}
    assert servers["local"].transport == "stdio"
    assert servers["local"].command == "mcp-server" and servers["local"].args == ("--flag", "x")


def test_load_mcp_config(tmp_path):
    path = tmp_path / "mcp.yml"
    path.write_text(
        "servers:\n"
        "  - nom: lecture-srv\n"
        "    url: http://a/mcp\n"
        "    mode: lecture\n"
        "  - nom: defaut-srv\n"
        "    url: http://b/mcp\n"      # mode omis → proposition/sensible
        "  - nom: moyen-srv\n"
        "    url: http://c/mcp\n"
        "    mode: proposition\n"
        "    risque: moyen\n"
        "  - nom: sans-url\n"          # ignoré (url manquante)
        "  - nom: lecture-srv\n"       # doublon → ignoré
        "    url: http://dup/mcp\n",
        encoding="utf-8",
    )
    servers = {s.name: s for s in load_mcp_config(path)}
    assert set(servers) == {"lecture-srv", "defaut-srv", "moyen-srv"}
    assert servers["lecture-srv"].mode == "lecture"
    assert servers["defaut-srv"].mode == "proposition" and servers["defaut-srv"].risk == "sensitive"
    assert servers["moyen-srv"].risk == "medium"
    assert load_mcp_config(tmp_path / "absent.yml") == []


# ── Sécurité : les outils MCP dans la Toolbox ──────────────────────────────


class FakeMcp:
    """Manager factice : contrôle les serveurs et enregistre les appels réels."""

    def __init__(self, servers: list[McpServer]):
        self._servers = {s.name: s for s in servers}
        self.calls: list[tuple] = []
        self.results: dict[tuple, object] = {}

    @property
    def configured(self):
        return bool(self._servers)

    def servers(self):
        return list(self._servers.values())

    def get(self, name):
        return self._servers.get(name)

    async def catalog(self):
        return {
            n: {"mode": s.mode, "risque": "sensible" if s.risk == "sensitive" else "moyen",
                "outils": [{"nom": "echo", "description": "", "schema": {}}]}
            for n, s in self._servers.items()
        }

    async def call_tool(self, server, tool, arguments=None):
        self.calls.append((server, tool, arguments))
        res = self.results.get((server, tool), f"résultat:{tool}")
        if isinstance(res, Exception):
            raise res
        return res

    async def read_resource(self, server, uri):
        self.calls.append(("read_resource", server, uri))
        return f"contenu:{uri}"

    async def get_prompt(self, server, name, arguments=None):
        self.calls.append(("get_prompt", server, name, arguments))
        return f"prompt:{name}"


@pytest.fixture()
async def mbox(tmp_path):
    ha, calls = make_ha_stub()
    proto_path = tmp_path / "protocols.yml"
    proto_path.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    protocols = ProtocolBook.load(proto_path)
    store = Store(tmp_path / "mcp.db")
    await store.open()
    fake = FakeMcp([
        McpServer(name="lecture", url="http://x/mcp", mode="lecture"),
        McpServer(name="sensible", url="http://y/mcp", mode="proposition", risk="sensitive"),
        McpServer(name="moyen", url="http://z/mcp", mode="proposition", risk="medium"),
    ])
    engine = ActionEngine(build_registry(ha, protocols, mcp=fake), store)
    toolbox = Toolbox(ha, engine, protocols, store, mcp=fake, tz="Europe/Paris")
    yield SimpleNamespace(toolbox=toolbox, engine=engine, store=store, mcp=fake, ha=ha, calls=calls)
    await store.close()


async def _mcp(mbox, args, speaker=OWNER):
    return await mbox.toolbox.run("mcp_appeler", args, utterance="", source="text", speaker=speaker)


async def test_mcp_lecture_appel_direct(mbox):
    content, is_error = await _mcp(mbox, {"serveur": "lecture", "outil": "echo", "arguments": {"a": 1}})
    assert not is_error and "résultat:echo" in content
    assert mbox.mcp.calls == [("lecture", "echo", {"a": 1})]  # exécuté directement
    assert await mbox.store.list_proposals() == []            # aucune proposition


async def test_mcp_proposition_ne_sexecute_pas_sans_validation(mbox):
    content, is_error = await _mcp(mbox, {"serveur": "sensible", "outil": "supprime", "arguments": {}})
    assert not is_error and "n°" in content
    assert mbox.mcp.calls == []  # RIEN n'est parti avant approbation
    pending = await mbox.store.list_proposals("pending")
    assert pending and pending[0]["action_id"] == "mcp.call" and pending[0]["risk"] == "sensitive"


async def test_mcp_proposition_sensible_interface_uniquement(mbox):
    await _mcp(mbox, {"serveur": "sensible", "outil": "supprime", "arguments": {}})
    num = (await mbox.store.list_proposals("pending"))[0]["num"]
    # À la voix : refusé (sensible = interface uniquement), rien n'est exécuté.
    p, msg = await mbox.engine.decide(num, "approve", via="voice")
    assert p["status"] == "pending" and "interface" in msg
    assert mbox.mcp.calls == []
    # Depuis l'interface : exécuté via le moteur → l'appel MCP part enfin.
    p, _ = await mbox.engine.decide(num, "approve", via="ui")
    assert p["status"] == "done"
    assert mbox.mcp.calls == [("sensible", "supprime", {})]


async def test_mcp_proposition_moyen_approuvable_a_la_voix(mbox):
    await _mcp(mbox, {"serveur": "moyen", "outil": "ranger", "arguments": {"x": 2}})
    num = (await mbox.store.list_proposals("pending"))[0]["num"]
    p, _ = await mbox.engine.decide(num, "approve", via="voice")
    assert p["status"] == "done"
    assert mbox.mcp.calls == [("moyen", "ranger", {"x": 2})]


async def test_mcp_reserve_au_proprietaire(mbox):
    for who in (UNKNOWN, _HOUSEHOLD):
        content, _ = await _mcp(mbox, {"serveur": "lecture", "outil": "echo"}, speaker=who)
        assert "réservé à guillaume" in content.lower()
        out, _ = await mbox.toolbox.run("mcp_outils", {}, utterance="", source="voice", speaker=who)
        assert "réservé à guillaume" in out.lower()
    assert mbox.mcp.calls == []  # aucun appel MCP pour un non-propriétaire


async def test_mcp_serveur_inconnu(mbox):
    content, is_error = await _mcp(mbox, {"serveur": "fantome", "outil": "echo"})
    assert is_error and "inconnu" in content.lower()


async def test_mcp_ressource_lecture_directe(mbox):
    content, is_error = await mbox.toolbox.run(
        "mcp_ressource", {"serveur": "lecture", "uri": "mem://x"},
        utterance="", source="text", speaker=OWNER,
    )
    assert not is_error and "contenu:mem://x" in content
    # Réservé au propriétaire.
    c2, _ = await mbox.toolbox.run(
        "mcp_ressource", {"serveur": "lecture", "uri": "mem://x"},
        utterance="", source="text", speaker=UNKNOWN,
    )
    assert "réservé à guillaume" in c2.lower()


async def test_mcp_prompt_lecture(mbox):
    content, is_error = await mbox.toolbox.run(
        "mcp_prompt", {"serveur": "lecture", "nom": "resume", "arguments": {"x": 1}},
        utterance="", source="text", speaker=OWNER,
    )
    assert not is_error and "prompt:resume" in content


async def test_mcp_absent_si_non_configure(tmp_path):
    ha, _ = make_ha_stub()
    proto_path = tmp_path / "p.yml"
    proto_path.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    protocols = ProtocolBook.load(proto_path)
    store = Store(tmp_path / "nomcp.db")
    await store.open()
    try:
        toolbox = Toolbox(ha, None, protocols, store, mcp=None, tz="Europe/Paris")
        names = [s["name"] for s in toolbox.specs()]
        assert "mcp_outils" not in names and "mcp_appeler" not in names
        content, is_error = await toolbox.run("mcp_outils", {}, utterance="", source="text", speaker=OWNER)
        assert is_error and "Aucun service MCP" in content
    finally:
        await store.close()
