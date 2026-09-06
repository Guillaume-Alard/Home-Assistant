"""Mémoire persistante (Phase 1) : mise en forme du profil et injection prompt."""

from __future__ import annotations

from app.brain.llm import _system_blocks
from app.brain.memory import format_profile, normalize_category
from app.config import Settings


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
