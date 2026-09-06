"""Voix de Luna : adaptateur ClonedTTS (API compatible OpenAI) + repli Piper."""

from __future__ import annotations

import pytest

from app.voice import wyoming
from app.voice.wyoming import ClonedTTS, FallbackTTS, VoiceServiceError


# ── Faux serveur HTTP (streaming) pour ClonedTTS ──────────────────────────────
class _FakeResp:
    def __init__(self, status: int, chunks: list[bytes]):
        self.status_code = status
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def aread(self):
        return b""


class _FakeClient:
    def __init__(self, resp: _FakeResp):
        self._resp = resp
        self.sent = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, json=None):
        self.sent = {"method": method, "url": url, "json": json}
        return self._resp


def _patch_http(monkeypatch, resp: _FakeResp):
    holder = {}

    def factory(**kw):
        client = _FakeClient(resp)
        holder["client"] = client
        return client

    monkeypatch.setattr(wyoming.httpx, "AsyncClient", factory)
    return holder


@pytest.mark.asyncio
async def test_cloned_tts_aligne_le_pcm_sur_16_bits(monkeypatch):
    # 3 + 2 = 5 octets : le flux doit ressortir aligné (dernier octet complété).
    _patch_http(monkeypatch, _FakeResp(200, [b"\x01\x02\x03", b"\x04\x05"]))
    tts = ClonedTTS("http://xtts:8000", "luna", rate=24000)
    out = [(rate, chunk) async for rate, chunk in tts.synthesize("bonjour")]
    assert all(rate == 24000 for rate, _ in out)
    joined = b"".join(chunk for _, chunk in out)
    assert len(joined) % 2 == 0
    assert joined == b"\x01\x02\x03\x04\x05\x00"


@pytest.mark.asyncio
async def test_cloned_tts_envoie_le_bon_payload(monkeypatch):
    holder = _patch_http(monkeypatch, _FakeResp(200, [b"\x00\x00"]))
    tts = ClonedTTS("http://xtts:8000/", "luna", model="tts-1")
    _ = [x async for x in tts.synthesize("salut")]
    sent = holder["client"].sent
    assert sent["url"].endswith("/v1/audio/speech")
    assert sent["json"] == {"model": "tts-1", "input": "salut", "voice": "luna", "response_format": "pcm"}


@pytest.mark.asyncio
async def test_cloned_tts_erreur_http(monkeypatch):
    _patch_http(monkeypatch, _FakeResp(500, [b""]))
    tts = ClonedTTS("http://xtts:8000", "luna")
    with pytest.raises(VoiceServiceError):
        _ = [x async for x in tts.synthesize("oups")]


# ── Fausses voix pour FallbackTTS ─────────────────────────────────────────────
class _FakeTTS:
    def __init__(self, chunks, *, fail_at=None, tag=b"P"):
        self._chunks = chunks
        self._fail_at = fail_at  # index où lever VoiceServiceError
        self.tag = tag
        self.calls = 0

    async def synthesize(self, text):
        self.calls += 1
        for i, c in enumerate(self._chunks):
            if self._fail_at is not None and i == self._fail_at:
                raise VoiceServiceError("boom")
            yield 24000, c
        if self._fail_at == len(self._chunks):
            raise VoiceServiceError("boom-fin")


@pytest.mark.asyncio
async def test_fallback_bascule_si_echec_avant_tout_son():
    primary = _FakeTTS([b"AA"], fail_at=0)          # échoue avant le 1er chunk
    fallback = _FakeTTS([b"BB", b"CC"])
    fb = FallbackTTS(primary, fallback)
    out = b"".join([c async for _, c in fb.synthesize("x")])
    assert out == b"BBCC"
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_fallback_ne_double_pas_si_echec_en_cours():
    primary = _FakeTTS([b"AA"], fail_at=1)          # 1 chunk émis puis échec
    fallback = _FakeTTS([b"BB"])
    fb = FallbackTTS(primary, fallback)
    out = b"".join([c async for _, c in fb.synthesize("x")])
    assert out == b"AA"                              # pas de repli (déjà commencé)
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_fallback_utilise_la_principale_si_ok():
    primary = _FakeTTS([b"AA", b"BB"])
    fallback = _FakeTTS([b"ZZ"])
    fb = FallbackTTS(primary, fallback)
    out = b"".join([c async for _, c in fb.synthesize("x")])
    assert out == b"AABB"
    assert fallback.calls == 0
