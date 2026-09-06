"""Phase 5A : détection du mot d'éveil (client Wyoming openWakeWord)."""

from __future__ import annotations

import asyncio

from app.voice.wyoming import VoiceServiceError, WakeWordDetector


async def test_detection_puis_fin_de_session(fake_wyoming):
    detected = asyncio.Event()
    names: list[str] = []

    async def on_detection(name: str) -> None:
        names.append(name)
        detected.set()

    detector = WakeWordDetector("127.0.0.1", fake_wyoming.wake_port, timeout=5)
    stream = await detector.open(16000, on_detection)
    try:
        for _ in range(fake_wyoming.WAKE_AFTER_CHUNKS + 1):
            await stream.send(b"\x00\x00" * 320, 16000)
        await asyncio.wait_for(detected.wait(), 3)
        assert names == ["hey_jarvis"]
    finally:
        await stream.close()

    # Après close(), l'envoi est un no-op silencieux (pas d'exception)
    await stream.send(b"\x00\x00" * 320, 16000)


async def test_service_injoignable(unused_tcp_port=None):
    detector = WakeWordDetector("127.0.0.1", 1, timeout=2)  # port fermé

    async def on_detection(name: str) -> None:  # pragma: no cover
        raise AssertionError("aucune détection attendue")

    try:
        await detector.open(16000, on_detection)
    except VoiceServiceError as exc:
        assert "injoignable" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("VoiceServiceError attendue")
