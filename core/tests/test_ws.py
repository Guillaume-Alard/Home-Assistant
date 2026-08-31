"""Test d'intégration : le protocole WebSocket de bout en bout.

Le LLM est simulé (pas d'appel réseau) ; whisper et piper sont les faux
serveurs Wyoming de conftest.py — le chemin audio complet est donc exercé.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import FAKE_TRANSCRIPT


@pytest.fixture()
def client(fake_wyoming, tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WHISPER_HOST", "127.0.0.1")
    monkeypatch.setenv("WHISPER_PORT", str(fake_wyoming.whisper_port))
    monkeypatch.setenv("PIPER_HOST", "127.0.0.1")
    monkeypatch.setenv("PIPER_PORT", str(fake_wyoming.piper_port))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # le cerveau est simulé ci-dessous

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def fake_brain(monkeypatch):
    from app.brain.llm import Brain

    async def fake_stream(self, history):
        assert history, "l'historique ne doit pas être vide"
        assert history[0]["role"] == "user"
        yield "Bonjour "
        yield "Guillaume."

    monkeypatch.setattr(Brain, "stream_reply", fake_stream)


def _drain(ws, stop_types: set[str], max_frames: int = 200):
    """Lit les trames jusqu'à avoir vu tous les `stop_types`. → (événements, binaires)"""
    events, blobs = [], []
    seen: set[str] = set()
    for _ in range(max_frames):
        frame = ws.receive()
        if frame.get("bytes") is not None:
            blobs.append(frame["bytes"])
            continue
        msg = json.loads(frame["text"])
        events.append(msg)
        if msg["type"] in stop_types:
            seen.add(msg["type"])
            if seen == stop_types:
                return events, blobs
    raise AssertionError(f"trames attendues non reçues : {stop_types - seen}")


def test_hello_et_sante(client):
    assert client.get("/health").json()["status"] == "ok"
    with client.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive()["text"])
        assert hello["type"] == "hello"
        assert hello["history"] == []
        assert hello["state"] == "idle"


def test_tour_ecrit(client, fake_brain):
    with client.websocket_connect("/ws") as ws:
        ws.receive()  # hello
        ws.send_text(json.dumps({"type": "chat", "text": "Salut Sentinel"}))
        events, blobs = _drain(ws, {"assistant_end"})

    types = [e["type"] for e in events]
    assert "assistant_start" in types

    user_messages = [e["message"] for e in events if e["type"] == "message"]
    assert user_messages[0]["content"] == "Salut Sentinel"
    assert user_messages[0]["source"] == "text"

    deltas = "".join(e["text"] for e in events if e["type"] == "assistant_delta")
    assert deltas == "Bonjour Guillaume."

    end = next(e for e in events if e["type"] == "assistant_end")
    assert end["message"]["content"] == "Bonjour Guillaume."
    assert end["cancelled"] is False
    assert blobs == []  # un message écrit ne déclenche pas de voix

    # L'historique persiste : une nouvelle connexion le reçoit dans son hello
    with client.websocket_connect("/ws") as ws2:
        hello = json.loads(ws2.receive()["text"])
        contents = [m["content"] for m in hello["history"]]
        assert "Salut Sentinel" in contents
        assert "Bonjour Guillaume." in contents


def test_tour_vocal_complet(client, fake_brain):
    with client.websocket_connect("/ws") as ws:
        ws.receive()  # hello
        ws.send_text(json.dumps({"type": "audio_start", "rate": 16000}))
        ws.send_bytes(b"\x00\x00" * 1600)  # ~100 ms de PCM
        ws.send_bytes(b"\x10\x00" * 1600)
        ws.send_text(json.dumps({"type": "audio_end"}))

        events, blobs = _drain(ws, {"assistant_end", "speak_end"})
        # L'état final « idle » est diffusé juste après la fin du tour
        for _ in range(20):
            frame = ws.receive()
            if frame.get("bytes") is not None:
                blobs.append(frame["bytes"])
                continue
            msg = json.loads(frame["text"])
            events.append(msg)
            if msg["type"] == "status" and msg["state"] == "idle":
                break

    # La transcription du faux whisper devient le message utilisateur (source voix)
    user_messages = [e["message"] for e in events if e["type"] == "message"]
    assert user_messages[0]["content"] == FAKE_TRANSCRIPT
    assert user_messages[0]["source"] == "voice"

    # La réponse simulée est diffusée puis synthétisée par le faux piper
    end = next(e for e in events if e["type"] == "assistant_end")
    assert end["message"]["content"] == "Bonjour Guillaume."

    speak_start = next(e for e in events if e["type"] == "speak_start")
    assert speak_start["rate"] == 22050
    assert len(blobs) >= 2  # les chunks PCM de la voix

    # Les états ont été diffusés dans un ordre cohérent
    states = [e["state"] for e in events if e["type"] == "status"]
    assert "transcribing" in states
    assert "thinking" in states
    assert "speaking" in states
    assert states[-1] == "idle"


def test_audio_vide(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive()  # hello
        ws.send_text(json.dumps({"type": "audio_start", "rate": 16000}))
        ws.send_text(json.dumps({"type": "audio_end"}))
        events, _ = _drain(ws, {"notice"})
        assert any("rien entendu" in e.get("text", "") for e in events if e["type"] == "notice")


def test_erreur_llm_sans_cle(client):
    # Pas de fake_brain ici : le vrai cerveau signale l'absence de clé API
    with client.websocket_connect("/ws") as ws:
        ws.receive()  # hello
        ws.send_text(json.dumps({"type": "chat", "text": "Bonjour"}))
        events, _ = _drain(ws, {"error"})
        error = next(e for e in events if e["type"] == "error")
        assert "ANTHROPIC_API_KEY" in error["text"]
