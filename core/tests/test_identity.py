"""Reconnaissance de locuteur (Phase 2) : matching, niveaux de confiance, droits."""

from __future__ import annotations

from app.identity import OWNER, UNKNOWN, Speaker, cosine, identify


def test_cosine_bornes():
    assert cosine([1, 0], [1, 0]) == 1.0
    assert abs(cosine([1, 0], [0, 1])) < 1e-9
    assert cosine([], [1]) == 0.0          # vecteurs incompatibles → 0
    assert cosine([0, 0], [1, 1]) == 0.0   # vecteur nul → 0


def _profile(pid, name, vec, is_owner=False):
    return {"id": pid, "name": name, "is_owner": is_owner, "vectors": [vec]}


def test_identify_reconnait_le_meilleur_profil():
    profiles = [
        _profile("g", "Guillaume", [1.0, 0.0, 0.0], is_owner=True),
        _profile("c", "Camille", [0.0, 1.0, 0.0]),
    ]
    who = identify([0.9, 0.1, 0.0], profiles, threshold=0.75)
    assert who.known and who.is_owner and who.name == "Guillaume"
    assert who.key == "guillaume"  # propriétaire → sujet mémoire « guillaume »
    assert who.can_act


def test_identify_membre_maisonnee_non_proprietaire():
    profiles = [_profile("c", "Camille", [0.0, 1.0, 0.0])]
    who = identify([0.05, 0.99, 0.0], profiles, threshold=0.75)
    assert who.known and not who.is_owner
    assert who.key == "c" and who.subject == "c"  # sa propre mémoire
    assert who.can_act


def test_identify_voix_inconnue_sous_le_seuil():
    profiles = [_profile("g", "Guillaume", [1.0, 0.0, 0.0], is_owner=True)]
    who = identify([0.0, 1.0, 0.0], profiles, threshold=0.75)
    assert who is UNKNOWN
    assert not who.known and not who.can_act and who.subject is None
    assert who.label == "invité"


def test_identify_sans_profil():
    assert identify([1.0, 0.0], [], threshold=0.75) is UNKNOWN


def test_droits_par_niveau():
    # Propriétaire : agit + admin + mémoire « guillaume »
    assert OWNER.can_act and OWNER.is_owner and OWNER.subject == "guillaume"
    # Maisonnée : agit, pas admin, mémoire propre
    household = Speaker(key="c", name="Camille", known=True, is_owner=False, score=0.9)
    assert household.can_act and not household.is_owner and household.subject == "c"
    # Invité : ni action, ni mémoire
    assert not UNKNOWN.can_act and UNKNOWN.subject is None
