"""Traduction du flux `stream-json` du worker en journal français.

Le module testé vit dans worker/ (pur stdlib, sans dépendance FastAPI) :
on l'importe par chemin depuis la racine du dépôt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))

from streamlog import translate  # noqa: E402


def _event(obj: dict) -> str:
    return json.dumps(obj)


def test_initialisation():
    lines, summary = translate(_event(
        {"type": "system", "subtype": "init", "model": "claude-sonnet-5"}
    ))
    assert lines == ["Claude Code démarré (modèle claude-sonnet-5)."]
    assert summary is None


def test_message_assistant_texte_et_outils():
    lines, _ = translate(_event({
        "type": "assistant",
        "message": {"content": [
            {"type": "text", "text": "Je corrige le README.\n"},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "README.md"}},
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "npm test", "description": "Lance les tests"}},
            {"type": "tool_use", "name": "OutilInconnu", "input": {"x": 1}},
        ]},
    }))
    assert lines[0] == "💬 Je corrige le README."
    assert lines[1] == "▸ modifie README.md"
    assert lines[2] == "▸ exécute Lance les tests"
    assert lines[3] == "▸ outil OutilInconnu"


def test_resultat_final_et_resume():
    lines, summary = translate(_event({
        "type": "result", "subtype": "success", "duration_ms": 83000,
        "num_turns": 12, "result": "J'ai corrigé trois fautes.",
    }))
    assert lines == ["Claude Code a terminé en 83 s, 12 tour(s)."]
    assert summary == "J'ai corrigé trois fautes."

    lines, summary = translate(_event(
        {"type": "result", "subtype": "error_max_turns", "duration_ms": 500}
    ))
    assert "error_max_turns" in lines[0]
    assert summary is None


def test_tolerance_au_bruit():
    # Ligne non-JSON : montrée brute (le CLI peut évoluer)
    lines, _ = translate("npm WARN deprecated machin")
    assert lines == ["npm WARN deprecated machin"]
    # Type inconnu ou résultat d'outil ordinaire : silencieux
    assert translate(_event({"type": "quelque_chose"})) == ([], None)
    assert translate(_event({
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": "ok"}]},
    })) == ([], None)
    # Résultat d'outil en erreur : signalé
    lines, _ = translate(_event({
        "type": "user",
        "message": {"content": [{"type": "tool_result", "is_error": True}]},
    }))
    assert "erreur" in lines[0]
    assert translate("") == ([], None)


def test_texte_long_borne_sur_une_ligne():
    lines, _ = translate(_event({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "mot " * 200}]},
    }))
    assert len(lines) == 1
    assert len(lines[0]) < 260
    assert "\n" not in lines[0]
