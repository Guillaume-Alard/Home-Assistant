"""Orchestrateur (étape 1) — le plan de travail.

Luna pose un plan VISIBLE pour les tâches à plusieurs étapes. Garantie tenue :
le plan est un fil conducteur, il n'EXÉCUTE RIEN — chaque étape qui agit passe
par les outils habituels (et donc par le moteur « propose puis approuve »).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import PROTOCOLS_TEST_YML, make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.brain.toolbox import Toolbox
from app.ha.protocols import ProtocolBook
from app.identity import OWNER
from app.store import Store


@pytest.fixture()
async def pbox(tmp_path):
    ha, calls = make_ha_stub()
    proto_path = tmp_path / "protocols.yml"
    proto_path.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    protocols = ProtocolBook.load(proto_path)
    store = Store(tmp_path / "plan.db")
    await store.open()
    engine = ActionEngine(build_registry(ha, protocols), store)
    plans: list[dict] = []

    async def on_plan(plan):
        plans.append(plan)

    toolbox = Toolbox(ha, engine, protocols, store, on_plan_change=on_plan, tz="Europe/Paris")
    yield SimpleNamespace(toolbox=toolbox, plans=plans, calls=calls, store=store)
    await store.close()


async def _plan(pbox, args):
    return await pbox.toolbox.run("plan", args, utterance="", source="text", speaker=OWNER)


async def test_plan_est_toujours_disponible(pbox):
    assert "plan" in [s["name"] for s in pbox.toolbox.specs()]


async def test_plan_pose_les_etapes_sans_rien_executer(pbox):
    content, is_error = await _plan(pbox, {"titre": "Préparer la soirée", "etapes": [
        {"texte": "Fermer les volets", "etat": "fait"},
        {"texte": "Tamiser le salon", "etat": "en_cours"},
        {"texte": "Lancer la playlist"},  # état par défaut → a_faire
    ]})
    assert not is_error and "1/3" in content
    plan = pbox.plans[-1]
    assert plan["titre"] == "Préparer la soirée"
    assert [e["etat"] for e in plan["etapes"]] == ["fait", "en_cours", "a_faire"]
    # LE PLAN N'EXÉCUTE RIEN : aucun service Nova appelé.
    assert pbox.calls == []


async def test_plan_etat_invalide_retombe_a_faire(pbox):
    await _plan(pbox, {"etapes": [{"texte": "x", "etat": "n'importe"}]})
    assert pbox.plans[-1]["etapes"][0]["etat"] == "a_faire"


async def test_plan_accepte_des_chaines(pbox):
    await _plan(pbox, {"etapes": ["étape simple"]})
    assert pbox.plans[-1]["etapes"] == [{"texte": "étape simple", "etat": "a_faire"}]


async def test_plan_effacer(pbox):
    await _plan(pbox, {"etapes": ["x"]})
    content, is_error = await _plan(pbox, {"effacer": True})
    assert not is_error and "effacé" in content.lower()
    assert pbox.plans[-1]["etapes"] == []  # plan vide → efface l'affichage


async def test_plan_sans_etapes_est_une_erreur(pbox):
    content, is_error = await _plan(pbox, {})
    assert is_error and not pbox.plans  # rien n'est diffusé


async def test_plan_etat_bloque(pbox):
    """Durcissement : une étape en échec/non confirmée a une place (« bloque »)."""
    content, is_error = await _plan(pbox, {"etapes": [
        {"texte": "Fermer le volet", "etat": "bloque"},
        {"texte": "Vérifier plus tard", "etat": "a_faire"},
    ]})
    assert not is_error and "bloquée" in content
    assert pbox.plans[-1]["etapes"][0]["etat"] == "bloque"


# ── Reprise après redémarrage : la fonction pure de « réanimation » ──────────

def test_revive_plan_frais_est_conserve():
    from app.main import _revive_plan
    raw = '{"titre": "T", "etapes": [{"texte": "x", "etat": "en_cours"}], "updated": 1000.0}'
    plan = _revive_plan(raw, now=1000.0 + 60, stale_after=12 * 3600)
    assert plan["titre"] == "T" and plan["etapes"][0]["texte"] == "x"


def test_revive_plan_perime_est_oublie():
    from app.main import _revive_plan
    raw = '{"etapes": [{"texte": "x", "etat": "a_faire"}], "updated": 1000.0}'
    # Plus vieux que le seuil → plan zombie, on l'oublie.
    assert _revive_plan(raw, now=1000.0 + 13 * 3600, stale_after=12 * 3600) == {}


def test_revive_plan_vide_ou_malforme():
    from app.main import _revive_plan
    assert _revive_plan(None, now=0, stale_after=1) == {}
    assert _revive_plan("{}", now=0, stale_after=1) == {}
    assert _revive_plan('{"etapes": []}', now=0, stale_after=1) == {}
    assert _revive_plan("pas du json", now=0, stale_after=1) == {}
