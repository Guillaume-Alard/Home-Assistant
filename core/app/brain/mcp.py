"""MCP — couche d'extension : brancher des services externes sans toucher au cœur.

Sentinel est CLIENT MCP. Il se connecte à des serveurs MCP déclarés dans
`config/mcp.yml` (transport « Streamable HTTP »), découvre leurs outils, et les
expose à Luna — SOUS le même contrat de sécurité que tout le reste :

  • un serveur « lecture » : ses outils s'appellent directement (requête, lecture) ;
  • un serveur « proposition » (défaut) : tout appel devient une PROPOSITION que
    Guillaume approuve avant exécution — rien d'externe ne s'exécute à l'aveugle.

Réservé au propriétaire (brancher un service, c'est de l'administration). La
reconnaissance de locuteur n'élève jamais ce droit.

Implémentation volontairement minimale et défensive : requête/réponse JSON-RPC
2.0 sur un unique endpoint HTTP (POST), gestion de la session (`Mcp-Session-Id`)
et lecture des réponses `application/json` comme `text/event-stream`. Pas de
dépendance au SDK `mcp` (non installé) : seul httpx, déjà présent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

log = logging.getLogger("sentinel.mcp")

# Version de protocole annoncée à l'initialisation (négociée par le serveur).
_PROTOCOL_VERSION = "2025-06-18"
_MODES = ("lecture", "proposition")
_RISK_FROM_FR = {"moyen": "medium", "sensible": "sensitive"}


class McpError(RuntimeError):
    """Échec MCP — message en français, montrable à l'utilisateur."""


@dataclass(frozen=True)
class McpServer:
    name: str
    url: str = ""                    # transport http : endpoint
    mode: str = "proposition"        # lecture | proposition
    risk: str = "sensitive"          # risque des propositions : medium | sensitive
    token: str = ""
    headers: dict = field(default_factory=dict)
    transport: str = "http"          # http | stdio
    command: str = ""                # transport stdio : exécutable à lancer
    args: tuple = ()                 # transport stdio : arguments
    env: dict = field(default_factory=dict)  # transport stdio : variables d'env ajoutées


def load_mcp_config(path: Path) -> list[McpServer]:
    """Charge `config/mcp.yml`. Format tolérant : une liste, ou {servers: [...]}.
    Un serveur mal formé est ignoré (jamais d'exception). Absent → []."""
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        log.error("mcp.yml invalide : %s — serveurs MCP ignorés", exc)
        return []
    items = raw.get("servers") if isinstance(raw, dict) else raw
    servers: list[McpServer] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("nom") or item.get("name") or "").strip()
        if not name or name in seen:
            continue
        transport = str(item.get("transport") or "http").strip().lower()
        if transport not in ("http", "stdio"):
            transport = "http"
        url = str(item.get("url") or "").strip()
        command = str(item.get("commande") or item.get("command") or "").strip()
        # Un serveur http exige une URL ; un serveur stdio exige une commande.
        if transport == "http" and not url:
            continue
        if transport == "stdio" and not command:
            continue
        mode = str(item.get("mode") or "proposition").strip().lower()
        if mode not in _MODES:
            mode = "proposition"
        risk = _RISK_FROM_FR.get(str(item.get("risque") or "sensible").strip().lower(), "sensitive")
        token = str(item.get("jeton") or item.get("token") or "").strip()
        raw_headers = item.get("entetes") or item.get("headers") or {}
        headers = {str(k): str(v) for k, v in raw_headers.items()} if isinstance(raw_headers, dict) else {}
        raw_args = item.get("args") or item.get("arguments") or []
        args = tuple(str(a) for a in raw_args) if isinstance(raw_args, list) else ()
        raw_env = item.get("env") or item.get("environnement") or {}
        env = {str(k): str(v) for k, v in raw_env.items()} if isinstance(raw_env, dict) else {}
        servers.append(McpServer(
            name=name, url=url, mode=mode, risk=risk, token=token, headers=headers,
            transport=transport, command=command, args=args, env=env,
        ))
        seen.add(name)
    return servers


def _req(rpc_id: int, method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}


def _last_sse_json(body: str) -> dict:
    """Extrait le dernier objet JSON des lignes `data:` d'un flux SSE."""
    for line in reversed(body.splitlines()):
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            try:
                return json.loads(payload)
            except ValueError:
                continue
    raise McpError("Flux SSE MCP sans donnée JSON exploitable.")


def _content_text(content) -> str:
    """Concatène le texte des blocs `content` d'un résultat MCP (liste de blocs,
    bloc unique, ou chaîne)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):  # un prompt renvoie souvent un bloc unique
        content = [content]
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(p for p in parts if p).strip()


class McpManager:
    """Gère les connexions aux serveurs MCP déclarés et l'appel de leurs outils."""

    def __init__(self, servers: list[McpServer], *, timeout: float = 20.0):
        self._servers = {s.name: s for s in servers}
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._session: dict[str, str] = {}   # serveur → Mcp-Session-Id (http)
        self._ready: set[str] = set()         # serveurs déjà initialisés
        self._caps: dict[str, dict] = {}      # serveur → capacités annoncées
        self._procs: dict[str, asyncio.subprocess.Process] = {}  # stdio : sous-processus
        self._proc_locks: dict[str, asyncio.Lock] = {}           # stdio : un échange à la fois

    @property
    def configured(self) -> bool:
        return bool(self._servers)

    def servers(self) -> list[McpServer]:
        return list(self._servers.values())

    def get(self, name: str) -> McpServer | None:
        return self._servers.get(name)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        for proc in list(self._procs.values()):  # stdio : couper les sous-processus
            with contextlib.suppress(Exception):
                proc.terminate()
        self._procs.clear()

    async def _rpc(self, server: str, payload: dict, *, expect_response: bool = True) -> dict | None:
        """Envoie une requête JSON-RPC au serveur, quel que soit son transport."""
        if self._servers[server].transport == "stdio":
            return await self._stdio_rpc(server, payload, expect_response=expect_response)
        return await self._http_rpc(server, payload, expect_response=expect_response)

    async def _http_rpc(self, server: str, payload: dict, *, expect_response: bool = True) -> dict | None:
        s = self._servers[server]
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": _PROTOCOL_VERSION,
        }
        if s.token:
            headers["Authorization"] = f"Bearer {s.token}"
        headers.update(s.headers)
        if (sid := self._session.get(server)):
            headers["Mcp-Session-Id"] = sid
        try:
            resp = await self._http().post(s.url, headers=headers, json=payload)
        except (httpx.HTTPError, OSError) as exc:
            raise McpError(f"Serveur MCP « {server} » injoignable : {exc}.") from exc
        if (new_sid := resp.headers.get("mcp-session-id")):
            self._session[server] = new_sid
        if resp.status_code >= 400:
            raise McpError(f"Serveur MCP « {server} » a refusé la requête (HTTP {resp.status_code}).")
        if not expect_response:
            return None
        ctype = (resp.headers.get("content-type") or "").lower()
        if "text/event-stream" in ctype:
            message = _last_sse_json(resp.text)
        else:
            try:
                message = resp.json()
            except ValueError as exc:
                raise McpError(f"Réponse MCP illisible de « {server} » : {exc}.") from exc
        return self._unwrap(server, message)

    def _unwrap(self, server: str, message) -> dict:
        """Normalise une réponse JSON-RPC : lève sur `error`, renvoie `result`."""
        if isinstance(message, list):  # lot JSON-RPC → 1re réponse porteuse
            message = next(
                (m for m in message if isinstance(m, dict) and ("result" in m or "error" in m)), {}
            )
        if not isinstance(message, dict):
            raise McpError(f"Réponse MCP inattendue de « {server} ».")
        if message.get("error"):
            err = message["error"] or {}
            raise McpError(f"Serveur MCP « {server} » — erreur {err.get('code', '?')} : "
                           f"{err.get('message', 'inconnue')}.")
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    # ── Transport stdio : un sous-processus par serveur ──────────────────
    async def _stdio_proc(self, server: str) -> asyncio.subprocess.Process:
        proc = self._procs.get(server)
        if proc is not None and proc.returncode is None:
            return proc
        s = self._servers[server]
        env = {**os.environ, **s.env}
        try:
            proc = await asyncio.create_subprocess_exec(
                s.command, *s.args,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=env,
            )
        except (OSError, ValueError) as exc:
            raise McpError(f"Serveur MCP « {server} » : lancement impossible ({exc}).") from exc
        self._procs[server] = proc
        return proc

    async def _stdio_rpc(self, server: str, payload: dict, *, expect_response: bool = True) -> dict | None:
        lock = self._proc_locks.setdefault(server, asyncio.Lock())
        async with lock:  # un échange à la fois par serveur (une seule réponse en vol)
            proc = await self._stdio_proc(server)
            try:
                proc.stdin.write((json.dumps(payload) + "\n").encode())
                await proc.stdin.drain()
            except (OSError, BrokenPipeError) as exc:
                raise McpError(f"Serveur MCP « {server} » : écriture impossible ({exc}).") from exc
            if not expect_response:
                return None
            wanted = payload.get("id")
            while True:  # ignore les notifications du serveur, attend NOTRE réponse
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=self._timeout)
                except asyncio.TimeoutError as exc:
                    raise McpError(f"Serveur MCP « {server} » : pas de réponse (délai dépassé).") from exc
                if not raw:
                    raise McpError(f"Serveur MCP « {server} » : le processus s'est arrêté.")
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # ligne de bruit (log du serveur) → on ignore
                if isinstance(message, dict) and message.get("id") == wanted:
                    return self._unwrap(server, message)

    async def _ensure(self, server: str) -> None:
        if server in self._ready:
            return
        result = await self._rpc(server, _req(1, "initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "sentinel", "version": "1"},
        }))
        self._caps[server] = (result or {}).get("capabilities") or {}
        # Notification d'initialisation (sans id, sans réponse attendue).
        await self._rpc(server, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        expect_response=False)
        self._ready.add(server)

    async def list_tools(self, server: str) -> list[dict]:
        if server not in self._servers:
            raise McpError(f"Serveur MCP inconnu : « {server} ».")
        await self._ensure(server)
        result = await self._rpc(server, _req(2, "tools/list", {}))
        tools = (result or {}).get("tools")
        return tools if isinstance(tools, list) else []

    async def list_resources(self, server: str) -> list[dict]:
        if server not in self._servers:
            raise McpError(f"Serveur MCP inconnu : « {server} ».")
        await self._ensure(server)
        if "resources" not in self._caps.get(server, {}):
            return []  # le serveur n'annonce pas de ressources
        result = await self._rpc(server, _req(4, "resources/list", {}))
        items = (result or {}).get("resources")
        return items if isinstance(items, list) else []

    async def list_prompts(self, server: str) -> list[dict]:
        if server not in self._servers:
            raise McpError(f"Serveur MCP inconnu : « {server} ».")
        await self._ensure(server)
        if "prompts" not in self._caps.get(server, {}):
            return []
        result = await self._rpc(server, _req(5, "prompts/list", {}))
        items = (result or {}).get("prompts")
        return items if isinstance(items, list) else []

    async def catalog(self) -> dict:
        """{serveur: {mode, risque, transport, outils/ressources/invites|erreur}} —
        jamais d'exception : un serveur en panne apparaît avec son message d'erreur,
        pas en cassant l'inventaire."""
        out: dict = {}
        for name, s in self._servers.items():
            entry: dict = {
                "mode": s.mode, "transport": s.transport,
                "risque": "sensible" if s.risk == "sensitive" else "moyen",
            }
            try:
                entry["outils"] = [
                    {"nom": t.get("name"), "description": t.get("description", ""),
                     "schema": t.get("inputSchema") or {}}
                    for t in await self.list_tools(name) if isinstance(t, dict)
                ]
                ressources = [
                    {"uri": r.get("uri"), "nom": r.get("name", ""), "description": r.get("description", "")}
                    for r in await self.list_resources(name) if isinstance(r, dict)
                ]
                if ressources:
                    entry["ressources"] = ressources
                invites = [
                    {"nom": p.get("name"), "description": p.get("description", ""),
                     "arguments": p.get("arguments") or []}
                    for p in await self.list_prompts(name) if isinstance(p, dict)
                ]
                if invites:
                    entry["invites"] = invites
            except McpError as exc:
                entry["erreur"] = str(exc)
            out[name] = entry
        return out

    async def call_tool(self, server: str, tool: str, arguments: dict | None = None) -> str:
        if server not in self._servers:
            raise McpError(f"Serveur MCP inconnu : « {server} ».")
        if not tool:
            raise McpError("Nom d'outil MCP manquant.")
        await self._ensure(server)
        result = await self._rpc(server, _req(3, "tools/call", {
            "name": tool, "arguments": arguments if isinstance(arguments, dict) else {},
        })) or {}
        text = _content_text(result.get("content"))
        if result.get("isError"):
            raise McpError(text or f"L'outil MCP « {tool} » a signalé une erreur.")
        return text or "(l'outil MCP n'a renvoyé aucun texte)"

    async def read_resource(self, server: str, uri: str) -> str:
        """Lit une ressource MCP (lecture seule). Renvoie le texte concaténé."""
        if server not in self._servers:
            raise McpError(f"Serveur MCP inconnu : « {server} ».")
        if not uri:
            raise McpError("URI de ressource MCP manquante.")
        await self._ensure(server)
        result = await self._rpc(server, _req(6, "resources/read", {"uri": uri})) or {}
        contents = result.get("contents")
        parts: list[str] = []
        for block in contents if isinstance(contents, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("text"):
                parts.append(str(block["text"]))
            elif block.get("blob"):
                parts.append(f"[contenu binaire {block.get('mimeType', '')}]".strip())
        text = "\n".join(p for p in parts if p).strip()
        return text or "(ressource vide)"

    async def get_prompt(self, server: str, name: str, arguments: dict | None = None) -> str:
        """Récupère un prompt MCP (modèle réutilisable) rendu en texte."""
        if server not in self._servers:
            raise McpError(f"Serveur MCP inconnu : « {server} ».")
        if not name:
            raise McpError("Nom du prompt MCP manquant.")
        await self._ensure(server)
        result = await self._rpc(server, _req(7, "prompts/get", {
            "name": name, "arguments": arguments if isinstance(arguments, dict) else {},
        })) or {}
        messages = result.get("messages")
        parts: list[str] = []
        for msg in messages if isinstance(messages, list) else []:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "")
            texte = _content_text(msg.get("content"))
            if texte:
                parts.append(f"{role}: {texte}" if role else texte)
        text = "\n\n".join(parts).strip()
        return text or "(prompt vide)"
