"""Recherche web (Phase 4) : outil natif, sources citées, garde-fous d'accès."""

from __future__ import annotations

from types import SimpleNamespace

from app.brain.llm import (
    Brain,
    _collect_sources,
    _dedup_sources,
    _web_search_tool,
)
from app.config import Settings
from app.identity import OWNER, UNKNOWN


def _settings(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clef-de-test")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings.from_env()


def test_outil_web_search_bien_forme(monkeypatch, tmp_path):
    tool = _web_search_tool(_settings(monkeypatch, tmp_path))
    assert tool["type"] == "web_search_20260209" and tool["name"] == "web_search"
    assert tool["max_uses"] == 5
    assert tool["user_location"]["country"] == "FR"
    assert tool["user_location"]["timezone"] == "Europe/Paris"


def test_collect_sources_lit_les_citations():
    txt = SimpleNamespace(type="text", citations=[
        SimpleNamespace(url="https://lemonde.fr/a", title="Le Monde"),
        SimpleNamespace(url="https://insee.fr/x", title="INSEE"),
    ])
    other = SimpleNamespace(type="web_search_tool_result", citations=None)
    empty = SimpleNamespace(type="text", citations=[SimpleNamespace(url=None, title="rien")])
    got = _collect_sources([txt, other, empty])
    assert got == [
        {"url": "https://lemonde.fr/a", "title": "Le Monde"},
        {"url": "https://insee.fr/x", "title": "INSEE"},
    ]


def test_dedup_sources():
    items = [{"url": "u1", "title": "a"}, {"url": "u1", "title": "a"}, {"url": "u2", "title": "b"}]
    assert _dedup_sources(items) == [{"url": "u1", "title": "a"}, {"url": "u2", "title": "b"}]


# ── Boucle de génération avec un faux client Anthropic ───────────────────────

class _FakeStreamCtx:
    def __init__(self, text, final):
        self._text = text
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def gen():
            yield self._text
        return gen()

    async def get_final_message(self):
        return self._final


class _FakeClient:
    def __init__(self, captured, final):
        self._captured = captured
        self._final = final
        self.messages = self

    def stream(self, **kwargs):
        self._captured.append(kwargs)
        return _FakeStreamCtx("Voici la réponse.", self._final)


def _brain_with_fake(settings, final, captured, on_sources=None):
    brain = Brain(settings, toolbox=None, on_sources=on_sources)
    brain._client = _FakeClient(captured, final)
    return brain


async def _drain(agen):
    return "".join([c async for c in agen])


async def test_web_search_pour_owner_et_sources_emises(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path)
    cited = SimpleNamespace(type="text", citations=[
        SimpleNamespace(url="https://lemonde.fr/actu", title="Le Monde"),
    ])
    final = SimpleNamespace(stop_reason="end_turn", content=[cited])
    captured, got = [], []

    async def on_sources(s):
        got.append(s)

    brain = _brain_with_fake(settings, final, captured, on_sources)
    text = await _drain(brain.stream_reply([{"role": "user", "content": "quoi de neuf ?"}], speaker=OWNER))

    assert text == "Voici la réponse."
    tools = captured[-1].get("tools") or []
    assert any(t.get("name") == "web_search" for t in tools)  # outil proposé au modèle
    assert got and got[0][0]["url"] == "https://lemonde.fr/actu"  # sources rediffusées


async def test_pas_de_web_search_pour_un_invite(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path)
    final = SimpleNamespace(stop_reason="end_turn", content=[])
    captured = []
    brain = _brain_with_fake(settings, final, captured)
    await _drain(brain.stream_reply([{"role": "user", "content": "actu ?"}], speaker=UNKNOWN))
    tools = captured[-1].get("tools")
    assert not tools or all(t.get("name") != "web_search" for t in tools)


async def test_web_search_desactivable(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path, SENTINEL_WEB_SEARCH="off")
    final = SimpleNamespace(stop_reason="end_turn", content=[])
    captured = []
    brain = _brain_with_fake(settings, final, captured)
    await _drain(brain.stream_reply([{"role": "user", "content": "actu ?"}], speaker=OWNER))
    tools = captured[-1].get("tools")
    assert not tools or all(t.get("name") != "web_search" for t in tools)
