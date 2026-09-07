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
    monkeypatch.setenv("WAKE_HOST", "")  # mot d'éveil désactivé par défaut (fixture dédiée)


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
def client_assist(fake_wyoming, tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path, fake_wyoming)
    monkeypatch.setenv("HA_URL", "")
    monkeypatch.setenv("SENTINEL_ASSIST_TOKEN", "jeton-assist-test")

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def client_wake(fake_wyoming, tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path, fake_wyoming)
    monkeypatch.setenv("HA_URL", "")
    monkeypatch.setenv("WAKE_HOST", "127.0.0.1")
    monkeypatch.setenv("WAKE_PORT", str(fake_wyoming.wake_port))

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def client_dev(fake_wyoming, fake_worker, tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path, fake_wyoming)
    monkeypatch.setenv("HA_URL", "")
    monkeypatch.setenv("WORKER_URL", f"http://127.0.0.1:{fake_worker.port}")

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


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
        # Bloc « engine » (affichage seul) attendu par la page Paramètres du cockpit
        assert hello["engine"]["model"]
        for key in ("effort", "whisper_model", "piper_voice", "wake_model", "tz"):
            assert key in hello["engine"]
        # Capacités booléennes pour les cartes Connexions
        for key in ("ha", "worker", "assist", "anthropic", "memory", "speaker", "mail",
                    "web_search", "self_improve", "proactive", "routines", "music", "reminders"):
            assert key in hello["config"]


def test_memoire_via_ws(client):
    """Ajout / lecture / suppression de souvenirs par l'UI, rediffusés à tous."""
    with client.websocket_connect("/ws") as ws:
        ws.receive()  # hello

        ws.send_text(json.dumps({
            "type": "memoire_add",
            "content": "Préfère les réponses courtes",
            "category": "preference",
        }))
        pushed = json.loads(ws.receive()["text"])
        assert pushed["type"] == "memoires"
        assert [m["content"] for m in pushed["memories"]] == ["Préfère les réponses courtes"]
        assert pushed["memories"][0]["source"] == "manuel"
        assert pushed["memories"][0]["category"] == "preference"
        mem_id = pushed["memories"][0]["id"]

        # Lecture explicite (réponse au seul demandeur)
        ws.send_text(json.dumps({"type": "memoires"}))
        listing = json.loads(ws.receive()["text"])
        assert listing["type"] == "memoires" and len(listing["memories"]) == 1

        # Suppression → rediffusion d'une liste vide
        ws.send_text(json.dumps({"type": "memoire_delete", "id": mem_id}))
        after = json.loads(ws.receive()["text"])
        assert after["type"] == "memoires" and after["memories"] == []


def test_evolutions_via_ws(fake_wyoming, tmp_path, monkeypatch):
    """Revue des propositions d'évolution par l'UI : liste, diff, décision, suppression.
    « Accepter » ne fait que marquer la décision — aucun code n'est appliqué."""
    import asyncio

    from app.store import Store

    _base_env(monkeypatch, tmp_path, fake_wyoming)
    monkeypatch.setenv("HA_URL", "")
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)

    async def seed():
        store = Store(data / "sentinel.db")
        await store.open()
        s = await store.add_suggestion(
            kind="config", title="Monter l'effort", rationale="soir", target=".env.example",
            diff="--- a/.env.example\n+++ b/.env.example\n@@ -1 +1 @@\n-SENTINEL_EFFORT=low\n+SENTINEL_EFFORT=medium\n",
        )
        await store.close()
        return s["id"]

    sug_id = asyncio.run(seed())

    from app.main import app

    with TestClient(app) as tc:
        with tc.websocket_connect("/ws") as ws:
            hello = json.loads(ws.receive()["text"])
            assert hello["config"]["self_improve"] is True

            ws.send_text(json.dumps({"type": "evolutions"}))
            payload = json.loads(ws.receive()["text"])
            assert payload["type"] == "evolutions" and payload["enabled"] is True
            assert [s["id"] for s in payload["suggestions"]] == [sug_id]
            assert payload["suggestions"][0]["status"] == "pending"

            # Diff complet au seul demandeur, avec les compteurs
            ws.send_text(json.dumps({"type": "evolution_get", "id": sug_id}))
            full = json.loads(ws.receive()["text"])
            assert full["type"] == "evolution" and "SENTINEL_EFFORT=medium" in full["diff"]
            assert full["added"] == 1 and full["removed"] == 1

            # Décision humaine : accepter → rediffusion avec le statut à jour
            ws.send_text(json.dumps({"type": "evolution_accept", "id": sug_id}))
            after = json.loads(ws.receive()["text"])
            assert after["type"] == "evolutions" and after["suggestions"][0]["status"] == "accepted"

            # Suppression → liste vide
            ws.send_text(json.dumps({"type": "evolution_delete", "id": sug_id}))
            empty = json.loads(ws.receive()["text"])
            assert empty["type"] == "evolutions" and empty["suggestions"] == []


def test_proactive_via_ws(fake_wyoming, fake_ha, tmp_path, monkeypatch):
    """Le tiroir Suggestions : lecture, « plus tard », « ne plus me suggérer ça ».
    Le veilleur ne s'active qu'avec Nova ; on seede une suggestion, on la gère."""
    import asyncio

    from app.store import Store

    _base_env(monkeypatch, tmp_path, fake_wyoming)
    monkeypatch.setenv("HA_URL", f"http://127.0.0.1:{fake_ha.port}")
    monkeypatch.setenv("HA_TOKEN", fake_ha.token)
    monkeypatch.setenv("SENTINEL_CONFIG_DIR", str(_write_config(tmp_path)))
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)

    async def seed():
        store = Store(data / "sentinel.db")
        await store.open()
        s = await store.add_proactive(
            key="volet_nuit:cover.demo", rule="volet_nuit", title="Fermer le volet",
            detail="Le volet est encore ouvert.", severity="warning", category="securite",
            action={"action_id": "ha.turn_off", "params": {"entity_ids": ["light.salon"]},
                    "title": "Éteindre", "risk": "low"},
        )
        await store.close()
        return s["id"]

    sug_id = asyncio.run(seed())

    from app.main import app

    with TestClient(app) as tc:
        _wait_ha(tc)
        with tc.websocket_connect("/ws") as ws:
            hello = json.loads(ws.receive()["text"])
            assert hello["config"]["proactive"] is True
            assert any(s["id"] == sug_id for s in hello["proactive"])

            # Lecture explicite du tiroir
            ws.send_text(json.dumps({"type": "proactive"}))
            events, _ = _drain(ws, {"proactive"})
            payload = next(e for e in events if e["type"] == "proactive")
            assert payload["enabled"] is True
            assert any(s["id"] == sug_id for s in payload["suggestions"])

            # « Ne plus me suggérer ça » : la règle est tue, la suggestion écartée
            ws.send_text(json.dumps({"type": "proactive_mute", "rule": "volet_nuit"}))
            events, _ = _drain(ws, {"proactive"})
            payload = next(e for e in events if e["type"] == "proactive")
            assert "volet_nuit" in payload["muted"]
            assert not any(s["id"] == sug_id for s in payload["suggestions"])


def test_routines_via_ws(fake_wyoming, fake_ha, tmp_path, monkeypatch):
    """Cycle cockpit : une routine proposée est activée puis déclenchée (via le
    moteur), une action courante partant bien vers Nova."""
    import asyncio

    from app.store import Store

    _base_env(monkeypatch, tmp_path, fake_wyoming)
    monkeypatch.setenv("HA_URL", f"http://127.0.0.1:{fake_ha.port}")
    monkeypatch.setenv("HA_TOKEN", fake_ha.token)
    monkeypatch.setenv("SENTINEL_CONFIG_DIR", str(_write_config(tmp_path)))
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)

    async def seed():
        store = Store(data / "sentinel.db")
        await store.open()
        r = await store.add_routine(
            name="Bonne nuit", description="Éteindre le salon",
            steps=[{"action_id": "ha.turn_off", "params": {"entity_ids": ["light.salon"]},
                    "label": "Éteindre le salon"}],
            status="proposed", source="llm",
        )
        await store.close()
        return r["id"]

    routine_id = asyncio.run(seed())

    from app.main import app

    with TestClient(app) as tc:
        _wait_ha(tc)
        with tc.websocket_connect("/ws") as ws:
            hello = json.loads(ws.receive()["text"])
            assert hello["config"]["routines"] is True
            assert any(r["id"] == routine_id and r["status"] == "proposed" for r in hello["routines"])

            # Activer (revue humaine) → rediffusion avec statut « active »
            ws.send_text(json.dumps({"type": "routine_approve", "id": routine_id}))
            events, _ = _drain(ws, {"routines"})
            payload = next(e for e in events if e["type"] == "routines")
            assert any(r["id"] == routine_id and r["status"] == "active" for r in payload["routines"])

            # Déclencher → une notice de résultat ; run_count incrémenté
            ws.send_text(json.dumps({"type": "routine_run", "id": routine_id}))
            events, _ = _drain(ws, {"notice"})
            notice = next(e for e in events if e["type"] == "notice")
            assert "Bonne nuit" in notice["text"]

    # L'action a bien été demandée à Nova (faux serveur l'enregistre : (domain, service, …))
    assert any(c[0] == "homeassistant" and c[1] == "turn_off" for c in fake_ha.calls)


def test_media_via_ws(fake_wyoming, fake_ha, tmp_path, monkeypatch):
    """La tuile musique : le cockpit pilote un lecteur, la commande passe par le
    moteur et part vers Nova ; l'état est rediffusé."""
    _base_env(monkeypatch, tmp_path, fake_wyoming)
    monkeypatch.setenv("HA_URL", f"http://127.0.0.1:{fake_ha.port}")
    monkeypatch.setenv("HA_TOKEN", fake_ha.token)
    monkeypatch.setenv("SENTINEL_CONFIG_DIR", str(_write_config(tmp_path)))

    from app.main import app

    with TestClient(app) as tc:
        _wait_ha(tc)
        with tc.websocket_connect("/ws") as ws:
            hello = json.loads(ws.receive()["text"])
            assert hello["config"]["music"] is True
            assert isinstance(hello["media"], list)

            ws.send_text(json.dumps({
                "type": "media_control", "op": "play", "entity_ids": ["media_player.salon"],
            }))
            events, _ = _drain(ws, {"media"})
            assert any(e["type"] == "media" and e["enabled"] for e in events)

    # La commande a bien été demandée à Nova (via le moteur)
    assert any(c[0] == "media_player" and c[1] == "media_play" for c in fake_ha.calls)


def test_reminders_via_ws(fake_wyoming, tmp_path, monkeypatch):
    """Minuteurs & rappels : le cockpit voit un rappel actif et peut l'annuler."""
    import asyncio
    from datetime import datetime, timedelta, timezone

    from app.store import Store

    _base_env(monkeypatch, tmp_path, fake_wyoming)
    monkeypatch.setenv("HA_URL", "")  # 100% local, aucune domotique requise
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)

    async def seed():
        store = Store(data / "sentinel.db")
        await store.open()
        due = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="milliseconds")
        r = await store.add_reminder(kind="reminder", label="appeler le garage", due_at=due)
        await store.close()
        return r["id"]

    rid = asyncio.run(seed())

    from app.main import app

    with TestClient(app) as tc:
        with tc.websocket_connect("/ws") as ws:
            hello = json.loads(ws.receive()["text"])
            assert hello["config"]["reminders"] is True
            assert any(r["id"] == rid for r in hello["reminders"])

            ws.send_text(json.dumps({"type": "reminders"}))
            events, _ = _drain(ws, {"reminders"})
            payload = next(e for e in events if e["type"] == "reminders")
            assert payload["enabled"] and any(r["id"] == rid for r in payload["reminders"])

            ws.send_text(json.dumps({"type": "reminder_cancel", "id": rid}))
            events, _ = _drain(ws, {"reminders"})
            payload = next(e for e in events if e["type"] == "reminders")
            assert not any(r["id"] == rid for r in payload["reminders"])


def test_page_publiee_servie_avec_csp(fake_wyoming, tmp_path, monkeypatch):
    """Une page publiée est servie à /p/<slug> avec une CSP verrouillant le réseau ;
    un brouillon ou un slug inconnu renvoie 404."""
    import asyncio

    from app.store import Store

    _base_env(monkeypatch, tmp_path, fake_wyoming)
    monkeypatch.setenv("HA_URL", "")
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)

    async def seed():
        store = Store(data / "sentinel.db")
        await store.open()
        pub = await store.add_page(title="Suivi Sport", html="<!doctype html><title>t</title><h1>Salut</h1>")
        await store.publish_page(pub["id"])
        draft = await store.add_page(title="Brouillon", html="<h1>secret</h1>")
        await store.close()
        return pub["slug"], draft["slug"]

    published_slug, draft_slug = asyncio.run(seed())

    from app.main import app

    with TestClient(app) as tc:
        ok = tc.get(f"/p/{published_slug}")
        assert ok.status_code == 200 and "Salut" in ok.text
        assert "connect-src 'none'" in ok.headers.get("content-security-policy", "")
        # Un brouillon n'est jamais servi publiquement, ni un slug inconnu
        assert tc.get(f"/p/{draft_slug}").status_code == 404
        assert tc.get("/p/inexistant").status_code == 404


def test_profils_vocaux_via_ws(client):
    """Création / enrôlement / suppression de profils vocaux (Phase 2)."""
    with client.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive()["text"])
        # Pas de service de reconnaissance en test → désactivé, aucun profil
        assert hello["config"]["speaker"] is False
        assert hello["speakers"] == []

        # Créer un profil → la liste est rediffusée
        ws.send_text(json.dumps({"type": "speaker_add", "name": "Camille"}))
        pushed = json.loads(ws.receive()["text"])
        assert pushed["type"] == "speakers"
        assert [s["name"] for s in pushed["speakers"]] == ["Camille"]
        sid = pushed["speakers"][0]["id"]
        assert pushed["speakers"][0]["samples"] == 0

        # Enrôlement impossible sans service actif : échec explicite, pas de plantage
        ws.send_text(json.dumps({"type": "speaker_enroll_start", "id": sid}))
        res = json.loads(ws.receive()["text"])
        assert res["type"] == "enroll_result" and res["ok"] is False

        # Suppression → liste vide
        ws.send_text(json.dumps({"type": "speaker_delete", "id": sid}))
        after = json.loads(ws.receive()["text"])
        assert after["type"] == "speakers" and after["speakers"] == []


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


# ── Mot d'éveil (Phase 5A) ───────────────────────────────────────────────


def test_mot_deveil_bout_en_bout(client_wake, fake_brain):
    """Veille → « hey jarvis » détecté → tour vocal complet, même connexion."""
    with client_wake.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive()["text"])
        assert hello["wake_available"] is True
        assert hello["wake_word"] == "hey jarvis"

        # L'appareil passe en veille et streame son micro
        ws.send_text(json.dumps({"type": "wake_start", "rate": 16000}))
        for _ in range(4):
            ws.send_bytes(b"\x00\x00" * 800)
        events, _ = _drain(ws, {"wake"})
        wake = next(e for e in events if e["type"] == "wake")
        assert wake["name"] == "hey_jarvis"

        # Le client enchaîne sur une écoute normale : le tour vocal aboutit
        ws.send_text(json.dumps({"type": "audio_start", "rate": 16000}))
        ws.send_bytes(b"\x10\x00" * 1600)
        ws.send_text(json.dumps({"type": "audio_end"}))
        events, _ = _drain(ws, {"assistant_end"})

    end = next(e for e in events if e["type"] == "assistant_end")
    assert end["message"]["content"] == "Bonjour Guillaume."


def test_mot_deveil_non_configure(client):
    with client.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive()["text"])
        assert hello["wake_available"] is False
        ws.send_text(json.dumps({"type": "wake_start", "rate": 16000}))
        events, _ = _drain(ws, {"wake_error"})
        assert "WAKE_HOST" in next(e for e in events if e["type"] == "wake_error")["text"]


# ── Agent conversationnel Assist (Phase 5B) ──────────────────────────────

_AUTH = {"Authorization": "Bearer jeton-assist-test"}


def test_assist_desactive_sans_jeton(client):
    # Aucun SENTINEL_ASSIST_TOKEN configuré → endpoint injoignable (404)
    r = client.post("/v1/chat/completions", json={"messages": []})
    assert r.status_code == 404


def test_assist_exige_le_jeton(client_assist):
    r = client_assist.post("/v1/chat/completions",
                           json={"messages": [{"role": "user", "content": "coucou"}]})
    assert r.status_code == 401
    r = client_assist.post("/v1/chat/completions", headers={"Authorization": "Bearer faux"},
                           json={"messages": [{"role": "user", "content": "coucou"}]})
    assert r.status_code == 401


def test_assist_chat_completion(client_assist, fake_brain):
    r = client_assist.post(
        "/v1/chat/completions",
        headers=_AUTH,
        json={"model": "sentinel", "messages": [
            {"role": "system", "content": "tu es un assistant"},
            {"role": "user", "content": "Bonjour Sentinel"},
        ]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Bonjour Guillaume."
    assert data["choices"][0]["finish_reason"] == "stop"

    # Le tour est bien entré dans le fil partagé (source « assist »)
    with client_assist.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive()["text"])
        contents = [(m["content"], m["source"]) for m in hello["history"]]
        assert ("Bonjour Sentinel", "assist") in contents
        assert ("Bonjour Guillaume.", "assist") in contents


def test_assist_message_vide_refuse(client_assist):
    r = client_assist.post("/v1/chat/completions", headers=_AUTH,
                           json={"messages": [{"role": "assistant", "content": "…"}]})
    assert r.status_code == 400


def test_assist_bloc_malforme_ne_casse_pas(client_assist, fake_brain):
    # Un bloc de contenu au texte null ne doit pas donner un 500 (400 propre
    # si vide, ou réponse normale si un texte exploitable subsiste).
    r = client_assist.post(
        "/v1/chat/completions", headers=_AUTH,
        json={"messages": [{"role": "user", "content": [{"type": "text", "text": None}]}]},
    )
    assert r.status_code == 400
    r = client_assist.post(
        "/v1/chat/completions", headers=_AUTH,
        json={"messages": [{"role": "user", "content": [
            {"type": "text", "text": None}, {"type": "text", "text": "Bonjour"},
        ]}]},
    )
    assert r.status_code == 200


def test_assist_models(client_assist):
    assert client_assist.get("/v1/models").status_code == 401
    r = client_assist.get("/v1/models", headers=_AUTH)
    assert r.status_code == 200
    assert any(m["id"] == "sentinel" for m in r.json()["data"])


# ── Panneaux Phase 4 : atelier, santé, historique ────────────────────────


def test_console_atelier_via_ws(client_dev, fake_worker):
    import httpx

    with client_dev.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive()["text"])
        assert hello["dev_configured"] is True
        assert hello["dev_running"] is None  # état de la pastille ⚒ dès la connexion

        # Liste + état de l'atelier (vide au départ)
        ws.send_text(json.dumps({"type": "dev_tasks"}))
        events, _ = _drain(ws, {"dev_tasks"})
        reply = next(e for e in events if e["type"] == "dev_tasks")
        assert reply["tasks"] == []
        assert reply["atelier"]["auth"] == "clé API"
        assert reply["atelier"]["repos"] == ["atrium", "loggia"]

        # Une tâche démarre côté worker : journal en direct puis diff
        task = httpx.post(
            f"http://127.0.0.1:{fake_worker.port}/tasks",
            json={"repo": "loggia", "instruction": "Corrige le README"},
        ).json()
        fake_worker.add_log(task["id"], "Clone de loggia…", "▸ modifie README.md")

        ws.send_text(json.dumps({"type": "dev_log", "id": task["id"], "after": 0}))
        events, _ = _drain(ws, {"dev_log"})
        logmsg = next(e for e in events if e["type"] == "dev_log")
        assert logmsg["id"] == task["id"] and logmsg["next"] == 2
        assert logmsg["lines"][1]["line"] == "▸ modifie README.md"

        # Lecture incrémentale : rien de neuf → aucune ligne
        ws.send_text(json.dumps({"type": "dev_log", "id": task["id"], "after": 2}))
        events, _ = _drain(ws, {"dev_log"})
        assert next(e for e in events if e["type"] == "dev_log")["lines"] == []

        ws.send_text(json.dumps({"type": "dev_diff", "id": task["id"]}))
        events, _ = _drain(ws, {"dev_diff"})
        diff = next(e for e in events if e["type"] == "dev_diff")
        assert "+correctif" in diff["diff"]


def test_atelier_non_configure(client):
    with client.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive()["text"])
        assert hello["dev_configured"] is False
        ws.send_text(json.dumps({"type": "dev_tasks"}))
        events, _ = _drain(ws, {"dev_tasks"})
        assert "WORKER_URL" in next(e for e in events if e["type"] == "dev_tasks")["error"]


def test_panneau_sante_via_ws(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive()  # hello
        ws.send_text(json.dumps({"type": "sante"}))
        events, _ = _drain(ws, {"sante"})
    sante = next(e for e in events if e["type"] == "sante")
    assert sante["data"]["nova"] == {"configuree": False}
    assert "systeme" in sante["data"]


def test_panneau_historique_via_ws(client_ha, fake_ha, brain_interdit):
    """Après un ordre direct, le journal des actions est consultable dans l'UI."""
    _wait_ha(client_ha)
    with client_ha.websocket_connect("/ws") as ws:
        ws.receive()  # hello
        ws.send_text(json.dumps({"type": "chat", "text": "Allume la lumière du salon"}))
        _drain(ws, {"assistant_end"})

        ws.send_text(json.dumps({"type": "historique"}))
        events, _ = _drain(ws, {"historique"})

    hist = next(e for e in events if e["type"] == "historique")
    entry = hist["journal"][0]
    assert entry["action_id"] == "ha.turn_on"
    assert entry["outcome"] == "ok"
    assert "Allume la lumière du salon".lower() in entry["authorization"].lower()
    assert hist["proposals"] == []
