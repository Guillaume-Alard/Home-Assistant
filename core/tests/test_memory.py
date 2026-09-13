"""Mémoire persistante (Phase 1) : mise en forme du profil et injection prompt."""

from __future__ import annotations

from app.brain.llm import _speaker_line, _system_blocks
from app.brain.memory import (
    format_profile, normalize_category, normalize_scope, rank_by_similarity, select_context,
)
from app.config import Settings
from app.identity import OWNER, UNKNOWN, Speaker


def _mem(content, category="fait"):
    return {"content": content, "category": category}


def test_normalize_category_variantes_et_repli():
    assert normalize_category("preference") == "preference"
    assert normalize_category("Préférences") == "preference"
    assert normalize_category("habitudes") == "habitude"
    assert normalize_category("langage") == "style"
    assert normalize_category(None) == "fait"
    assert normalize_category("n'importe quoi") == "fait"


def test_format_profile_groupe_par_categorie():
    memories = [
        _mem("Réponses courtes", "preference"),
        _mem("Se couche tard", "habitude"),
        _mem("Aime le tutoiement", "style"),
        _mem("Habite à Lyon", "fait"),
    ]
    out = format_profile(memories)
    # Ordre des catégories stable, en-têtes présents, puces pour chaque souvenir
    assert out.index("Préférences") < out.index("Habitudes") < out.index("À savoir")
    assert "- Réponses courtes" in out
    assert "- Habite à Lyon" in out


def test_format_profile_vide():
    assert format_profile([]) == ""
    assert format_profile([{"content": "   "}]) == ""  # que du vide → rien


# ── Niveaux de mémoire (brique 4) ────────────────────────────────────────────

def test_normalize_scope_variantes_et_repli():
    assert normalize_scope("utilisateur") == "utilisateur"
    assert normalize_scope("travail") == "projet"       # alias → projet
    assert normalize_scope("Domotique") == "maison"
    assert normalize_scope("session") == "conversation"
    assert normalize_scope(None) == "utilisateur"        # défaut = profil stable
    assert normalize_scope("n'importe quoi") == "utilisateur"


def _memn(content, scope, category="fait"):
    return {"content": content, "scope": scope, "category": category}


def test_format_profile_groupe_par_niveau():
    memories = [
        _memn("Réponses courtes", "utilisateur", "preference"),
        _memn("Cuisine ouverte sur le salon", "maison"),
        _memn("Refonte de la cave à vin", "projet"),
    ]
    out = format_profile(memories)
    # Le profil stable d'abord, puis maison, puis projet.
    assert out.index("Profil de Guillaume") < out.index("La maison") < out.index("Projets en cours")
    assert "- Réponses courtes" in out and "- Refonte de la cave à vin" in out


def test_format_profile_conversation_bornee():
    from app.brain.memory import CONVERSATION_INJECT_MAX
    memories = [_memn(f"note {i}", "conversation") for i in range(CONVERSATION_INJECT_MAX + 5)]
    out = format_profile(memories)
    # Contexte passager : seuls les plus récents sont injectés.
    assert "note 0" not in out
    assert f"note {CONVERSATION_INJECT_MAX + 4}" in out


# ── RAG mémoire : classement par similarité + sélection du contexte ──────────

def _emb(mid, content, scope, vec, category="fait"):
    return {"id": mid, "content": content, "scope": scope, "category": category, "embedding": vec}


def test_rank_by_similarity_ordonne_et_ignore_sans_vecteur():
    q = [1.0, 0.0]
    mems = [
        _emb("a", "aligné", "maison", [1.0, 0.0]),
        _emb("b", "orthogonal", "maison", [0.0, 1.0]),
        _emb("c", "proche", "maison", [0.9, 0.1]),
        _emb("d", "sans vecteur", "maison", None),
    ]
    assert [m["id"] for m in rank_by_similarity(q, mems, top_k=2)] == ["a", "c"]


def test_rank_ignore_dimension_differente():
    assert rank_by_similarity([1.0, 0.0], [_emb("x", "x", "maison", [1.0, 0.0, 0.0])], top_k=3) == []


def test_rank_parse_embedding_json():
    import json
    ranked = rank_by_similarity([1.0, 0.0], [_emb("x", "x", "maison", json.dumps([1.0, 0.0]))], top_k=1)
    assert [m["id"] for m in ranked] == ["x"]  # vecteur stocké en texte (comme en base)


def test_select_context_repli_recence_sans_vecteur():
    mems = [_emb(str(i), f"m{i}", "utilisateur", None) for i in range(5)]
    sel = select_context(mems, None, top_k=3, recent=2)
    assert [m["id"] for m in sel] == ["3", "4"]  # les 2 plus récents (comportement d'origine)


def test_select_context_profil_stable_plus_pertinents():
    q = [1.0, 0.0]
    mems = [
        _emb("p1", "profil", "utilisateur", [0.0, 1.0]),         # profil (peu pertinent)
        _emb("proj", "projet pertinent", "projet", [1.0, 0.0]),  # très pertinent
        _emb("m1", "maison hors sujet", "maison", [0.0, 1.0]),   # hors sujet
    ]
    ids = [m["id"] for m in select_context(mems, q, top_k=1, recent=60)]
    assert "p1" in ids and "proj" in ids   # profil stable TOUJOURS + le plus pertinent
    assert "m1" not in ids                  # le hors-sujet n'est pas rappelé


def test_system_blocks_injecte_la_memoire_apres_le_cache():
    settings = Settings.from_env()
    blocks = _system_blocks(settings, "Préférences :\n- Réponses courtes")

    # Le bloc mémoire vient APRÈS le point de cache (jamais figé)
    assert blocks[0]["cache_control"]["type"] == "ephemeral"
    assert "cache_control" not in blocks[-1]
    assert "Réponses courtes" in blocks[-1]["text"]
    assert "mémoire persistante" in blocks[-1]["text"].lower()


def test_system_blocks_sans_memoire_reste_inchange():
    settings = Settings.from_env()
    base = _system_blocks(settings)
    # Sans profil : bloc stable + date uniquement, aucun bloc mémoire ajouté
    assert len(base) == 2
    assert not any("mémoire persistante" in b["text"].lower() for b in base)


# ── Locuteur → prompt (Phase 2) ──────────────────────────────────────────────

def test_speaker_line_proprietaire_muet():
    # Propriétaire (écrit/UI ou voix reconnue) : aucune ligne — comportement normal
    assert _speaker_line(None) == ""
    assert _speaker_line(OWNER) == ""


def test_speaker_line_maisonnee_nomme_la_personne():
    line = _speaker_line(Speaker(key="c", name="Camille", known=True, is_owner=False, score=0.9))
    assert "Camille" in line and "pas Guillaume" in line


def test_speaker_line_invite_restreint():
    line = _speaker_line(UNKNOWN)
    low = line.lower()
    assert "invité" in low and "n'agis pas" in low and "interface" in low


def test_system_blocks_injecte_le_locuteur():
    settings = Settings.from_env()
    guest = _system_blocks(settings, "", UNKNOWN)
    assert any("invité" in b["text"].lower() for b in guest)
    # Le bloc locuteur reste après le point de cache (variable)
    assert "cache_control" not in guest[-1]
