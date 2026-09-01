"""Test d'intégration : le protocole WebSocket de bout en bout.

Le LLM est simulé (pas d'appel réseau) ; whisper et piper sont les faux
serveurs Wyoming de conftest.py — le chemin audio complet est donc exercé.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import FAKE_TRANSCRIPT


def _base_env(monkeypatch, tmp_path, fake_wyoming):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WHISPER_HOST", "127.0.0.1")
    monkeypatch.setenv("WHISPER_PORT", str(fake_wyoming.whisper_port))
    monkeypatch.setenv("PIPER_HOST", "127.0.0.1")
    monkeypatch.setenv("PIPER_PORT", str(fake_wyoming.piper_port))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # le cerveau est simulé par les tests


@pytest.fixture()
def client(fake_wyoming, tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path, fake_wyoming)
    monkeypatch.setenv("HA_URL", "")  # pas de domotique dans ces tests

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def client_ha(fake_wyoming, fake_ha, tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path, fake_wyoming)
    monkeypatch.setenv("HA_URL", f"http://127.0.0.1:{fake_ha.port}")
    monkeypatch.setenv("HA_TOKEN", fake_ha.token)
    monkeypatch.setenv("SENTINEL_CONFIG_DIR", str(_write_config(tmp_path)))

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _write_config(tmp_path):
    from conftest import PROTOCOLS_TEST_YML

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "protocols.yml").write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    return config_dir


@pytest.fixture()
def fake_brain(monkeypatch):
    from app.brain.llm import Brain

    async def fake_stream(self, history, **kwargs):
        assert history, "l'historique ne doit pas être vide"
        assert history[0]["role"] == "user"
        yield "Bonjour "
        yield "Guillaume."

    monkeypatch.setattr(Brain, "stream_reply", fake_stream)


@pytest.fixture()
def brain_interdit(monkeypatch):
    """Garantit qu'un intent local ne déclenche JAMAIS le LLM."""
    from app.brain.llm import Brain

    async def boom(self, history, **kwargs):
        raise AssertionError("le LLM ne doit pas être appelé pour un intent local")
        yield  # jamais atteint — fait de boom un générateur asynchrone

    monkeypatch.setattr(Brain, "stream_reply", boom)


def _wait_ha(test_client, timeout: float = 5.0):
    import time as _time

    sentinel = test_client.app.state.sentinel
    for _ in range(int(timeout / 0.05)):
        if sentinel.ha and sentinel.ha.connected:
            return
        _time.sleep(0.05)
    raise AssertionError("Nova (faux serveur) jamais connectée")


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
    assert end["message"]["source"] == "voice"  # réponse prononcée

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


def test_intent_local_bout_en_bout(client_ha, fake_ha, brain_interdit):
    """« Allume la lumière du salon » par WebSocket : Nova reçoit l'ordre, sans LLM."""
    _wait_ha(client_ha)
    with client_ha.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive()["text"])
        assert hello["ha_configured"] is True
        assert any(p["nom"] == "Test" for p in hello["protocols"])

        ws.send_text(json.dumps({"type": "chat", "text": "Allume la lumière du salon"}))
        events, _ = _drain(ws, {"assistant_end"})

    end = next(e for e in events if e["type"] == "assistant_end")
    assert end["message"]["content"] == "Allumé : Plafonnier salon."
    assert fake_ha.calls[-1][:2] == ("homeassistant", "turn_on")
    assert fake_ha.calls[-1][3] == {"entity_id": ["light.salon"]}


def test_protocole_bout_en_bout(client_ha, fake_ha, brain_interdit):
    _wait_ha(client_ha)
    with client_ha.websocket_connect("/ws") as ws:
        ws.receive()  # hello
        ws.send_text(json.dumps({"type": "chat", "text": "protocole test"}))
        events, _ = _drain(ws, {"assistant_end"})

    end = next(e for e in events if e["type"] == "assistant_end")
    assert end["message"]["content"] == "Protocole de test exécuté."
    assert fake_ha.calls[-1][:2] == ("persistent_notification", "create")


def test_decision_proposition_via_ws(client_ha, fake_ha):
    _wait_ha(client_ha)
    with client_ha.websocket_connect("/ws") as ws:
        ws.receive()  # hello
        ws.send_text(json.dumps({"type": "proposal_decision", "id": 999, "decision": "approve"}))
        events, _ = _drain(ws, {"notice"})
    notice = next(e for e in events if e["type"] == "notice")
    assert "999" in notice["text"]
