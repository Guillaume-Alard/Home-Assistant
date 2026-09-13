"""RAG mémoire — service d'embeddings (client compatible OpenAI).

On teste la disponibilité et la construction/lecture de la charge utile sans
réseau (le client est court-circuité par un faux). Non configuré → indisponible,
et la mémoire retombe sur la récence (couvert dans test_memory)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.brain.embeddings import EmbeddingsError, EmbeddingsService


def _es(**over):
    base = dict(
        embeddings_enabled=True, embeddings_base_url="http://emb.local/v1",
        embeddings_model="bge-m3", embeddings_api_key="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_available():
    assert EmbeddingsService(_es()).available is True
    assert EmbeddingsService(_es(embeddings_base_url="")).available is False
    assert EmbeddingsService(_es(embeddings_model="")).available is False
    assert EmbeddingsService(_es(embeddings_enabled=False)).available is False


class _FakeEmbeddings:
    def __init__(self):
        self.seen = None

    async def create(self, **kwargs):
        self.seen = kwargs
        n = len(kwargs["input"])
        # Renvoie volontairement dans le désordre pour vérifier le tri par index.
        data = [SimpleNamespace(index=i, embedding=[float(i), 1.0]) for i in reversed(range(n))]
        return SimpleNamespace(data=data)


async def test_embed_renvoie_les_vecteurs_dans_l_ordre():
    svc = EmbeddingsService(_es())
    fake = _FakeEmbeddings()
    svc._client = SimpleNamespace(embeddings=fake)  # court-circuite le réseau
    vecs = await svc.embed(["a", "b"])
    assert vecs == [[0.0, 1.0], [1.0, 1.0]]
    assert fake.seen["model"] == "bge-m3" and fake.seen["input"] == ["a", "b"]


async def test_embed_textes_vides_sans_appel():
    svc = EmbeddingsService(_es())
    # Aucun texte exploitable → pas d'appel réseau, vecteurs vides.
    assert await svc.embed(["", "   "]) == [[], []]


async def test_embed_non_configure_leve():
    svc = EmbeddingsService(_es(embeddings_base_url=""))
    with pytest.raises(EmbeddingsError):
        await svc.embed(["x"])
