"""Chargement et recherche des protocoles (YAML)."""

from __future__ import annotations

from conftest import PROTOCOLS_TEST_YML

from app.ha.protocols import ProtocolBook


def _book(tmp_path, content: str) -> ProtocolBook:
    path = tmp_path / "protocols.yml"
    path.write_text(content, encoding="utf-8")
    return ProtocolBook.load(path)


def test_chargement_et_risques_francais(tmp_path):
    book = _book(tmp_path, PROTOCOLS_TEST_YML)
    test = book.get("test")
    verrou = book.get("Verrou")  # par nom affiché aussi
    assert test and test.risk == "low" and test.steps[0].service == "persistent_notification.create"
    assert verrou and verrou.risk == "sensitive"
    assert verrou.steps[0].target == {"entity_id": ["lock.entree"]}  # str → liste


def test_phrases_implicites_et_explicites(tmp_path):
    book = _book(tmp_path, """\
forteresse:
  nom: "Forteresse"
  risque: moyen
  phrases: ["verrouille la maison"]
  actions:
    - service: lock.lock
""")
    assert book.find_in_text("sentinel protocole forteresse s'il te plait").key == "forteresse"
    assert book.find_in_text("passe en mode forteresse").key == "forteresse"
    assert book.find_in_text("verrouille la maison").key == "forteresse"
    assert book.find_in_text("verrouille la porte") is None


def test_entrees_invalides_ignorees(tmp_path):
    book = _book(tmp_path, """\
ok:
  risque: faible
  actions: [{ service: light.turn_off }]
sans_action:
  risque: faible
risque_inconnu:
  risque: cosmique
  actions: [{ service: light.turn_off }]
service_invalide:
  actions: [{ service: pasdedomaine }]
""")
    assert book.get("ok") is not None
    assert book.get("sans_action") is None
    assert book.get("risque_inconnu") is None
    assert book.get("service_invalide") is None


def test_fichier_absent(tmp_path):
    book = ProtocolBook.load(tmp_path / "inexistant.yml")
    assert book.all() == []
    assert book.find_in_text("protocole test") is None
