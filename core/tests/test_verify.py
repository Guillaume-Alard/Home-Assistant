"""Brique 1 — la boucle de vérification.

Après une action, Luna relit l'état de Nova et rend un verdict HONNÊTE : elle ne
dit plus « c'est fait » à l'aveugle. Deux garanties structurelles vérifiées ici :

  • la vérification est en LECTURE SEULE — elle ne rejoue jamais l'action (une
    action sensible ne peut pas se ré-exécuter sans repasser par l'approbation) ;
  • une vérification qui casse ne masque ni ne fausse jamais le résultat réel.

On utilise le vrai registre (`build_registry`) et un HAClient sans réseau dont
les écritures sont enregistrées : régler l'état AVANT l'exécution simule le
monde tel qu'il devient (ou ne devient pas) après l'ordre.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.store import Store


@pytest.fixture()
async def env(tmp_path):
    ha, calls = make_ha_stub()
    store = Store(tmp_path / "verify.db")
    await store.open()
    engine = ActionEngine(build_registry(ha), store)
    yield SimpleNamespace(ha=ha, calls=calls, engine=engine, store=store)
    await store.close()


def _set(ha, entity_id: str, state: str, **attrs) -> None:
    """Force l'état d'une entité dans le cache (ce que Nova « renverra »)."""
    ha._states[entity_id] = {
        "entity_id": entity_id,
        "state": state,
        "attributes": {"friendly_name": ha.friendly_name(entity_id), **attrs},
    }


# ── Confirmation positive ────────────────────────────────────────────────


async def test_allumage_confirme(env):
    _set(env.ha, "light.salon", "on")
    out = await env.engine.run_direct(
        "ha.turn_on", {"entity_ids": ["light.salon"]}, utterance="allume le salon", source="voice"
    )
    assert out.ok
    assert out.text == "Allumé : Plafonnier salon. Vérifié côté Nova."


async def test_extinction_confirmee(env):
    _set(env.ha, "light.salon", "off")
    out = await env.engine.run_direct(
        "ha.turn_off", {"entity_ids": ["light.salon"]}, utterance="éteins le salon", source="text"
    )
    assert out.ok and out.text.endswith("Vérifié côté Nova.")


async def test_volet_ouverture_confirmee(env):
    # Un volet en train de s'ouvrir compte comme confirmé (le mouvement a démarré).
    _set(env.ha, "cover.salon", "opening")
    out = await env.engine.run_direct(
        "ha.cover", {"op": "open", "entity_ids": ["cover.salon"]}, utterance="ouvre le volet", source="voice"
    )
    assert out.ok and "Vérifié côté Nova." in out.text


async def test_alarme_armement_confirme(env):
    _set(env.ha, "alarm_control_panel.maison", "armed_away")
    out = await env.engine.run_direct(
        "ha.alarm_arm", {"mode": "away", "entity_ids": ["alarm_control_panel.maison"]},
        utterance="arme l'alarme", source="text",
    )
    assert out.ok and "Vérifié côté Nova." in out.text


async def test_consigne_chauffage_confirmee(env):
    _set(env.ha, "climate.salon", "heat", temperature=20.0)
    out = await env.engine.run_direct(
        "ha.climate_set_temperature", {"temperature": 20, "entity_ids": ["climate.salon"]},
        utterance="mets 20 degrés", source="voice",
    )
    assert out.ok and "Vérifié côté Nova." in out.text


# ── Verdict honnête quand rien n'a bougé ───────────────────────────────────


async def test_allumage_non_confirme_est_dit_franchement(env):
    # La lumière reste éteinte : l'ordre est parti, mais Nova ne confirme rien.
    _set(env.ha, "light.salon", "off")
    out = await env.engine.run_direct(
        "ha.turn_on", {"entity_ids": ["light.salon"]}, utterance="allume le salon", source="voice"
    )
    assert out.ok  # la commande a bien été acceptée par Nova…
    assert "je n'ai pas pu le confirmer" in out.text  # …mais l'effet n'est pas prouvé
    assert "à vérifier" in out.text
    # Le journal garde la trace du verdict, pas un « c'est fait » mensonger.
    journal = await env.store.list_journal()
    assert "je n'ai pas pu le confirmer" in journal[0]["detail"]


async def test_consigne_non_prise_en_compte(env):
    _set(env.ha, "climate.salon", "heat", temperature=18.0)  # loin des 21 demandés
    out = await env.engine.run_direct(
        "ha.climate_set_temperature", {"temperature": 21, "entity_ids": ["climate.salon"]},
        utterance="mets 21 degrés", source="text",
    )
    assert out.ok and "je n'ai pas pu le confirmer" in out.text


# ── Actions non vérifiables : aucun verdict inventé ─────────────────────────


async def test_volet_stop_non_verifiable(env):
    _set(env.ha, "cover.salon", "open")
    out = await env.engine.run_direct(
        "ha.cover", {"op": "stop", "entity_ids": ["cover.salon"]}, utterance="stop le volet", source="voice"
    )
    assert out.ok
    assert "Vérifié" not in out.text and "confirmer" not in out.text


async def test_scene_sans_verdict(env):
    _set(env.ha, "scene.cinema", "scening")
    out = await env.engine.run_direct(
        "ha.scene", {"entity_ids": ["scene.cinema"]}, utterance="ambiance cinéma", source="voice"
    )
    assert out.ok
    assert "Vérifié" not in out.text and "confirmer" not in out.text


# ── Garanties structurelles ────────────────────────────────────────────────


async def test_verification_est_lecture_seule_jamais_de_rejeu(env):
    """Même non confirmée, l'action n'est JAMAIS rejouée : une seule écriture."""
    _set(env.ha, "light.salon", "off")  # restera éteint → non confirmé
    await env.engine.run_direct(
        "ha.turn_on", {"entity_ids": ["light.salon"]}, utterance="allume", source="voice"
    )
    assert env.calls == [("homeassistant", "turn_on", None, {"entity_id": ["light.salon"]})]


async def test_deverrouillage_sensible_verifie_apres_confirmation(env):
    """Une action sensible passe par la double confirmation ; la vérification
    n'intervient qu'APRÈS, sans jamais court-circuiter l'approbation."""
    ask = await env.engine.run_direct(
        "ha.unlock", {"entity_ids": ["lock.entree"]}, utterance="déverrouille la porte", source="voice"
    )
    assert ask.status == "needs_confirmation"
    assert env.calls == []  # rien n'est parti vers Nova avant le « confirme »

    _set(env.ha, "lock.entree", "unlocked")
    done = await env.engine.confirm_pending(source="voice")
    assert done.ok and "Vérifié côté Nova." in done.text
    assert env.calls == [("lock", "unlock", None, {"entity_id": ["lock.entree"]})]


async def test_une_verification_qui_casse_ne_masque_pas_le_resultat(env):
    """Si la relecture d'état lève une exception, on rend le résultat brut —
    jamais un faux « vérifié » ni une erreur qui effacerait l'action réussie."""
    def boom(entity_id):
        raise RuntimeError("cache d'états indisponible")
    env.ha.get_state = boom

    out = await env.engine.run_direct(
        "ha.turn_on", {"entity_ids": ["light.salon"]}, utterance="allume", source="voice"
    )
    assert out.ok
    assert out.text == "Allumé : Plafonnier salon."  # résultat brut, sans verdict


async def test_proposition_approuvee_est_verifiee(env):
    """Le verdict s'applique aussi au chemin des propositions approuvées."""
    _set(env.ha, "light.chambre", "off")
    proposal, _ = await env.engine.propose(
        title="Éteindre la chambre", risk="low",
        action_id="ha.turn_off", params={"entity_ids": ["light.chambre"]},
    )
    updated, msg = await env.engine.decide(proposal["num"], "approve", via="ui")
    assert updated["status"] == "done"
    assert "Vérifié côté Nova." in updated["result"]
    assert "Vérifié côté Nova." in msg
