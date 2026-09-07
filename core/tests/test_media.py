"""Musique multi-pièces (Phase 9) : lecture des lecteurs, résolution pièce →
lecteur, et l'exécuteur ha.media (via le moteur, jamais Nova en direct)."""

from __future__ import annotations

import pytest
from conftest import PROTOCOLS_TEST_YML, make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.ha import media as media_lib
from app.ha.protocols import ProtocolBook
from app.store import Store


def _ha_with_players():
    ha, calls = make_ha_stub()
    ha._states["media_player.salon"] = {
        "state": "playing",
        "attributes": {
            "friendly_name": "Enceinte salon", "volume_level": 0.3, "is_volume_muted": False,
            "media_title": "So What", "media_artist": "Miles Davis",
            "source": "Spotify", "source_list": ["Spotify", "Radio"],
        },
    }
    ha._states["media_player.cuisine"] = {
        "state": "off", "attributes": {"friendly_name": "Enceinte cuisine"},
    }
    ha._entity_area["media_player.salon"] = "area_salon"
    ha._entity_area["media_player.cuisine"] = "area_chambre"  # peu importe, une pièce
    return ha, calls


# ── Configuration (préréglages) ──────────────────────────────────────────────

def test_load_config(tmp_path):
    (tmp_path / "media.yml").write_text(
        "piece_defaut: Salon\npresets:\n  Jazz: {source: Spotify}\n  Détente: {content_id: 'spotify:x', content_type: music}\n",
        encoding="utf-8",
    )
    (tmp_path / "media2.yml").write_text(
        "lecteur_defaut: media_player.spotify_x\npiece_defaut: Salon\npresets:\n  Jazz: {source: Spotify}\n",
        encoding="utf-8",
    )
    cfg = media_lib.load_config(tmp_path / "media2.yml")
    assert cfg.default_room == "Salon" and cfg.default_player == "media_player.spotify_x"
    assert "jazz" in cfg.presets and cfg.presets["jazz"]["source"] == "Spotify"  # clé normalisée

    assert media_lib.load_config(tmp_path / "absent.yml").presets == {}  # facultatif


# ── Lecture (snapshot / résolution) ──────────────────────────────────────────

def test_snapshot_ne_montre_que_les_lecteurs_allumes():
    ha, _ = _ha_with_players()
    players = media_lib.snapshot(ha, live_only=True)
    assert [p["entity_id"] for p in players] == ["media_player.salon"]
    p = players[0]
    assert p["joue"] and p["volume"] == 30 and p["titre"] == "So What" and p["source"] == "Spotify"


def test_resolve_players():
    ha, _ = _ha_with_players()
    assert media_lib.resolve_players(ha, entity_ids=["media_player.cuisine"]) == ["media_player.cuisine"]
    assert media_lib.resolve_players(ha, zone="salon") == ["media_player.salon"]
    # Sans cible : ce qui joue déjà
    assert media_lib.resolve_players(ha) == ["media_player.salon"]
    # Pièce inconnue → rien
    assert media_lib.resolve_players(ha, zone="grenier") == []

    # Rien ne joue : dernier recours = le lecteur par défaut (ex. Spotify hors pièce)
    ha._states["media_player.salon"]["state"] = "off"
    assert media_lib.resolve_players(ha, default_player="media_player.cuisine") == ["media_player.cuisine"]
    assert media_lib.resolve_players(ha, default_player="media_player.inexistant") == []


# ── Exécuteur ha.media (via le moteur d'actions) ─────────────────────────────

@pytest.fixture()
async def engine_media(tmp_path):
    ha, calls = _ha_with_players()
    proto = tmp_path / "protocols.yml"
    proto.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    store = Store(tmp_path / "media.db")
    await store.open()
    engine = ActionEngine(build_registry(ha, ProtocolBook.load(proto), None), store)
    from types import SimpleNamespace
    yield SimpleNamespace(engine=engine, ha=ha, calls=calls)
    await store.close()


async def _run(engine, params):
    return await engine.run_direct("ha.media", params, utterance="x", source="test")


async def test_media_lecture_et_volume(engine_media):
    out = await _run(engine_media.engine, {"op": "play", "entity_ids": ["media_player.salon"]})
    assert out.status == "ok"
    assert ("media_player", "media_play") in {(c[0], c[1]) for c in engine_media.calls}

    await _run(engine_media.engine, {"op": "volume", "level": 0.5, "entity_ids": ["media_player.salon"]})
    vol = next(c for c in engine_media.calls if c[1] == "volume_set")
    assert vol[2] == {"volume_level": 0.5}


async def test_media_source_et_mute(engine_media):
    await _run(engine_media.engine, {"op": "source", "source": "Radio", "entity_ids": ["media_player.salon"]})
    src = next(c for c in engine_media.calls if c[1] == "select_source")
    assert src[2] == {"source": "Radio"}

    await _run(engine_media.engine, {"op": "mute", "entity_ids": ["media_player.salon"]})
    mute = next(c for c in engine_media.calls if c[1] == "volume_mute")
    assert mute[2] == {"is_volume_muted": True}


async def test_media_transfert_multipieces(engine_media):
    out = await _run(engine_media.engine, {
        "op": "join", "entity_ids": ["media_player.salon"],
        "group_members": ["media_player.cuisine"],
    })
    assert out.status == "ok"
    join = next(c for c in engine_media.calls if c[1] == "join")
    assert join[2] == {"group_members": ["media_player.cuisine"]}


async def test_media_refuse_domaine_et_op_inconnus(engine_media):
    # Mauvais domaine d'entité
    out = await _run(engine_media.engine, {"op": "play", "entity_ids": ["light.salon"]})
    assert out.status == "failed"
    # Opération inconnue
    out = await _run(engine_media.engine, {"op": "teleporter", "entity_ids": ["media_player.salon"]})
    assert out.status == "failed"
