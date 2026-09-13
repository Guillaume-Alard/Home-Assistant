"""Brique 2 — Vision en lecture.

Luna REGARDE une caméra de Nova et DÉCRIT ce qu'elle voit. Deux garanties tenues
ici :
  • c'est un CAPTEUR, jamais un bras — l'outil ne passe jamais par le moteur
    d'actions et n'émet aucun `call_service` (l'image n'est qu'une lecture) ;
  • réservé aux personnes reconnues (une caméra touche à l'intimité) ; la
    reconnaissance n'élève aucun droit — c'est une lecture, pas une action.

On teste `VisionService.describe` (construction/lecture de la charge utile, sans
réseau) et l'outil `regarder` (résolution de caméra, permissions, erreurs).
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from conftest import PROTOCOLS_TEST_YML, make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.brain.toolbox import Toolbox
from app.brain.vision import VisionError, VisionService
from app.ha.client import HAError
from app.ha.protocols import ProtocolBook
from app.identity import OWNER, UNKNOWN, Speaker
from app.store import Store

_HOUSEHOLD = Speaker(key="cam", name="Camille", known=True, is_owner=False, score=0.9)


def _vsettings(**over):
    base = dict(
        vision_enabled=True,
        vision_base_url="http://vision.local/v1",
        vision_model="fake-vlm",
        vision_api_key="",
        vision_max_tokens=64,
        vision_timeout=10,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeCreate:
    """Capture les kwargs de chat.completions.create et renvoie une complétion."""

    def __init__(self, content: str):
        self.content = content
        self.seen: dict | None = None

    async def __call__(self, **kwargs):
        self.seen = kwargs
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeVision:
    """Service de vision factice injecté dans la Toolbox (pas de réseau)."""

    def __init__(self, *, available=True, text="Une personne attend devant la porte.", error=None):
        self._available = available
        self.text = text
        self.error = error
        self.calls: list[tuple] = []

    @property
    def available(self) -> bool:
        return self._available

    async def describe(self, image, question=None, *, content_type="image/jpeg"):
        self.calls.append((image, question, content_type))
        if self.error is not None:
            raise self.error
        return self.text


# ── VisionService : disponibilité + charge utile ───────────────────────────


def test_vision_indisponible_sans_url_ni_modele():
    assert VisionService(_vsettings(vision_base_url="")).available is False
    assert VisionService(_vsettings(vision_model="")).available is False
    assert VisionService(_vsettings(vision_enabled=False)).available is False
    assert VisionService(_vsettings()).available is True


async def test_describe_envoie_image_et_lit_la_reponse():
    svc = VisionService(_vsettings())
    fake = _FakeCreate("Je vois une porte fermée.")
    # On court-circuite le client réseau par un faux exposant chat.completions.create.
    svc._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake))
    )
    out = await svc.describe(b"\xff\xd8jpeg-bytes", "qui est là ?", content_type="image/jpeg")
    assert out == "Je vois une porte fermée."

    assert fake.seen["model"] == "fake-vlm" and fake.seen["max_tokens"] == 64
    user = fake.seen["messages"][-1]
    assert user["role"] == "user"
    img = next(b for b in user["content"] if b["type"] == "image_url")
    expected = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8jpeg-bytes").decode()
    assert img["image_url"]["url"] == expected
    # La question oriente le regard.
    assert any(b.get("text") == "qui est là ?" for b in user["content"] if b["type"] == "text")


async def test_describe_non_configuree_leve_erreur_claire():
    svc = VisionService(_vsettings(vision_base_url=""))
    with pytest.raises(VisionError):
        await svc.describe(b"data")


async def test_describe_reponse_vide_est_une_erreur():
    svc = VisionService(_vsettings())
    svc._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_FakeCreate("")))
    )
    with pytest.raises(VisionError):
        await svc.describe(b"data")


# ── Outil « regarder » : résolution, lecture seule, permissions ────────────


@pytest.fixture()
async def vbox(tmp_path):
    ha, calls = make_ha_stub()
    ha._states["camera.entree"] = {
        "entity_id": "camera.entree", "state": "idle",
        "attributes": {"friendly_name": "Caméra entrée"},
    }
    ha._entity_area["camera.entree"] = "area_salon"
    snaps: list[str] = []

    async def fake_snapshot(entity_id, *, timeout=10.0):
        snaps.append(entity_id)
        return b"\xff\xd8jpeg", "image/jpeg"

    ha.camera_snapshot = fake_snapshot  # lecture stubbée (pas de réseau HA)

    proto_path = tmp_path / "protocols.yml"
    proto_path.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    protocols = ProtocolBook.load(proto_path)
    store = Store(tmp_path / "vision.db")
    await store.open()
    engine = ActionEngine(build_registry(ha, protocols), store)
    vision = FakeVision()
    toolbox = Toolbox(ha, engine, protocols, store, vision=vision, tz="Europe/Paris")
    yield SimpleNamespace(ha=ha, calls=calls, toolbox=toolbox, store=store, vision=vision, snaps=snaps)
    await store.close()


async def _look(vbox, args, speaker=OWNER):
    return await vbox.toolbox.run("regarder", args, utterance="regarde", source="voice", speaker=speaker)


async def test_regarder_decrit_la_camera_sans_agir(vbox):
    content, is_error = await _look(vbox, {"camera": "camera.entree", "question": "qui est là ?"})
    assert not is_error
    assert "personne attend devant la porte" in content
    # La vision a bien reçu l'image et la question.
    assert vbox.vision.calls and vbox.vision.calls[0][1] == "qui est là ?"
    assert vbox.snaps == ["camera.entree"]
    # LECTURE SEULE : aucun service Nova appelé (le capteur n'agit jamais).
    assert vbox.calls == []


async def test_regarder_resout_la_camera_unique_par_defaut(vbox):
    content, is_error = await _look(vbox, {})  # une seule caméra → prise d'office
    assert not is_error and vbox.snaps == ["camera.entree"]


async def test_regarder_plusieurs_cameras_demande_precision(vbox):
    vbox.ha._states["camera.jardin"] = {
        "entity_id": "camera.jardin", "state": "idle",
        "attributes": {"friendly_name": "Caméra jardin"},
    }
    content, is_error = await _look(vbox, {})
    assert is_error and "Précise" in content
    assert vbox.snaps == []  # rien n'a été capturé tant que la cible est ambiguë


async def test_regarder_zone_inconnue(vbox):
    content, is_error = await _look(vbox, {"zone": "garage"})
    assert is_error and "Pièce inconnue" in content


async def test_regarder_entity_non_camera_refusee(vbox):
    content, is_error = await _look(vbox, {"camera": "light.salon"})
    assert is_error and "n'est pas une caméra" in content
    assert vbox.snaps == []


async def test_regarder_reserve_aux_personnes_reconnues(vbox):
    content, is_error = await _look(vbox, {"camera": "camera.entree"}, speaker=UNKNOWN)
    assert "reconnais pas" in content.lower()
    assert vbox.snaps == [] and vbox.vision.calls == []
    # Une personne reconnue de la maisonnée, elle, peut regarder.
    _, is_error2 = await _look(vbox, {"camera": "camera.entree"}, speaker=_HOUSEHOLD)
    assert not is_error2


async def test_regarder_erreur_vision_remontee(vbox):
    vbox.vision.error = VisionError("Service de vision local injoignable.")
    content, is_error = await _look(vbox, {"camera": "camera.entree"})
    assert is_error and "injoignable" in content


async def test_regarder_camera_injoignable(vbox):
    async def boom(entity_id, *, timeout=10.0):
        raise HAError("Caméra injoignable (camera.entree).")
    vbox.ha.camera_snapshot = boom
    content, is_error = await _look(vbox, {"camera": "camera.entree"})
    assert is_error and "injoignable" in content


async def test_outil_regarder_absent_si_vision_indisponible(tmp_path):
    ha, _ = make_ha_stub()
    proto_path = tmp_path / "p.yml"
    proto_path.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    protocols = ProtocolBook.load(proto_path)
    store = Store(tmp_path / "novision.db")
    await store.open()
    try:
        # Sans service de vision : l'outil n'est pas déclaré et l'appel échoue proprement.
        toolbox = Toolbox(ha, None, protocols, store, vision=None, tz="Europe/Paris")
        assert "regarder" not in [s["name"] for s in toolbox.specs()]
        content, is_error = await toolbox.run(
            "regarder", {}, utterance="regarde", source="voice", speaker=OWNER
        )
        assert is_error and "vision locale n'est pas configurée" in content
        # Idem si le service existe mais n'est pas disponible (non configuré).
        tb2 = Toolbox(ha, None, protocols, store, vision=FakeVision(available=False), tz="Europe/Paris")
        assert "regarder" not in [s["name"] for s in tb2.specs()]
    finally:
        await store.close()
