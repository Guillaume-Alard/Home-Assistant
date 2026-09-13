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

import json
import logging
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
    url: str
    mode: str = "proposition"        # lecture | proposition
    risk: str = "sensitive"          # risque des propositions : medium | sensitive
    token: str = ""
    headers: dict = field(default_factory=dict)


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
        url = str(item.get("url") or "").strip()
        if not name or not url or name in seen:
            continue
        mode = str(item.get("mode") or "proposition").strip().lower()
        if mode not in _MODES:
            mode = "proposition"
        risk = _RISK_FROM_FR.get(str(item.get("risque") or "sensible").strip().lower(), "sensitive")
        token = str(item.get("jeton") or item.get("token") or "").strip()
        raw_headers = item.get("entetes") or item.get("headers") or {}
        headers = {str(k): str(v) for k, v in raw_headers.items()} if isinstance(raw_headers, dict) else {}
        servers.append(McpServer(name=name, url=url, mode=mode, risk=risk, token=token, headers=headers))
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
    """Concatène le texte des blocs `content` d'un résultat MCP."""
    if isinstance(content, str):
        return content.strip()
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
        self._session: dict[str, str] = {}   # serveur → Mcp-Session-Id
        self._ready: set[str] = set()         # serveurs déjà initialisés

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

    async def _post(self, server: str, payload: dict, *, expect_response: bool = True) -> dict | None:
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

    async def _ensure(self, server: str) -> None:
        if server in self._ready:
            return
        await self._post(server, _req(1, "initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "sentinel", "version": "1"},
        }))
        # Notification d'initialisation (sans id, sans réponse attendue).
        await self._post(server, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                         expect_response=False)
        self._ready.add(server)

    async def list_tools(self, server: str) -> list[dict]:
        if server not in self._servers:
            raise McpError(f"Serveur MCP inconnu : « {server} ».")
        await self._ensure(server)
        result = await self._post(server, _req(2, "tools/list", {}))
        tools = (result or {}).get("tools")
        return tools if isinstance(tools, list) else []

    async def catalog(self) -> dict:
        """{serveur: {mode, risque, tools|error}} — jamais d'exception : un serveur
        en panne apparaît avec son message d'erreur, pas en cassant l'inventaire."""
        out: dict = {}
        for name, s in self._servers.items():
            entry: dict = {"mode": s.mode, "risque": "sensible" if s.risk == "sensitive" else "moyen"}
            try:
                entry["outils"] = [
                    {
                        "nom": t.get("name"),
                        "description": t.get("description", ""),
                        "schema": t.get("inputSchema") or {},
                    }
                    for t in await self.list_tools(name) if isinstance(t, dict)
                ]
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
        result = await self._post(server, _req(3, "tools/call", {
            "name": tool, "arguments": arguments if isinstance(arguments, dict) else {},
        })) or {}
        text = _content_text(result.get("content"))
        if result.get("isError"):
            raise McpError(text or f"L'outil MCP « {tool} » a signalé une erreur.")
        return text or "(l'outil MCP n'a renvoyé aucun texte)"
