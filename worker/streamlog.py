"""Traduction du flux `claude --output-format stream-json` en journal lisible.

En mode streaming, le CLI Claude Code émet un objet JSON par ligne (NDJSON) :
initialisation, messages de l'assistant (texte + appels d'outils), résultats
d'outils, puis un événement final `result`. Ce module transforme chaque ligne
en zéro, une ou plusieurs entrées de journal en français — c'est ce que la
console de l'atelier affiche en direct dans l'interface Sentinel.

Stdlib uniquement (importé par server.py, testé par core/tests/test_streamlog.py).
"""

from __future__ import annotations

import json

# Libellés français des outils les plus courants de Claude Code
_TOOL_LABELS = {
    "Read": "lit",
    "Write": "écrit",
    "Edit": "modifie",
    "MultiEdit": "modifie",
    "NotebookEdit": "modifie",
    "Bash": "exécute",
    "Grep": "cherche",
    "Glob": "cherche",
    "LS": "explore",
    "WebFetch": "consulte",
    "WebSearch": "cherche sur le web",
    "Task": "délègue une sous-tâche",
    "TodoWrite": "organise son plan",
}


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())  # une seule ligne, espaces normalisés
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe_tool(name: str, args: dict) -> str:
    if name == "Bash":
        target = args.get("description") or args.get("command") or ""
    else:
        target = (
            args.get("file_path") or args.get("path") or args.get("pattern")
            or args.get("url") or args.get("query") or args.get("prompt") or ""
        )
    label = _TOOL_LABELS.get(name)
    if label:
        return f"▸ {label} {_clip(target, 160)}".rstrip()
    return f"▸ outil {name}" + (f" — {_clip(target, 120)}" if target else "")


def translate(line: str) -> tuple[list[str], str | None]:
    """Une ligne de sortie du CLI → (entrées de journal, résumé final ou None).

    Tolérant par construction : une ligne non-JSON est montrée brute (tronquée),
    un type d'événement inconnu est ignoré — le format du CLI peut évoluer.
    """
    line = line.strip()
    if not line:
        return [], None
    try:
        obj = json.loads(line)
    except ValueError:
        return [_clip(line, 300)], None
    if not isinstance(obj, dict):
        return [], None

    kind = obj.get("type")

    if kind == "system" and obj.get("subtype") == "init":
        return [f"Claude Code démarré (modèle {obj.get('model') or '?'})."], None

    if kind == "assistant":
        out: list[str] = []
        for block in (obj.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and (block.get("text") or "").strip():
                out.append("💬 " + _clip(block["text"], 240))
            elif block.get("type") == "tool_use":
                out.append(_describe_tool(str(block.get("name")), block.get("input") or {}))
        return out, None

    if kind == "result":
        seconds = round((obj.get("duration_ms") or 0) / 1000)
        subtype = obj.get("subtype") or "?"
        status = "terminé" if subtype == "success" else f"terminé ({subtype})"
        lines = [f"Claude Code a {status} en {seconds} s, {obj.get('num_turns', '?')} tour(s)."]
        summary = str(obj.get("result") or "").strip() or None
        return lines, summary

    if kind == "user":  # résultats d'outils : silencieux, sauf erreur
        for block in (obj.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("is_error"):
                return ["⚠ un outil a renvoyé une erreur."], None

    return [], None
