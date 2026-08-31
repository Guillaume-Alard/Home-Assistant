"""Fixtures : faux serveurs Wyoming (whisper/piper) sur ports éphémères.

Ils parlent le vrai protocole Wyoming (via la bibliothèque `wyoming`), dans un
thread dédié avec sa propre boucle asyncio — le serveur Sentinel sous test s'y
connecte comme aux vrais conteneurs.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # core/ → import app

from wyoming.asr import Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import async_read_event, async_write_event
from wyoming.tts import Synthesize

FAKE_TRANSCRIPT = "allume la lumière du salon"
FAKE_TTS_CHUNKS = [b"\x00\x01" * 512, b"\x02\x03" * 512]


class FakeWyoming:
    def __init__(self) -> None:
        self.whisper_port: int | None = None
        self.piper_port: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(5), "faux serveurs Wyoming non démarrés"

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start_servers())
        self._loop.run_forever()

    async def _start_servers(self) -> None:
        whisper = await asyncio.start_server(self._handle_whisper, "127.0.0.1", 0)
        piper = await asyncio.start_server(self._handle_piper, "127.0.0.1", 0)
        self.whisper_port = whisper.sockets[0].getsockname()[1]
        self.piper_port = piper.sockets[0].getsockname()[1]
        self._ready.set()

    async def _handle_whisper(self, reader, writer) -> None:
        try:
            while True:
                event = await async_read_event(reader)
                if event is None:
                    return
                if AudioStop.is_type(event.type):
                    await async_write_event(Transcript(text=FAKE_TRANSCRIPT).event(), writer)
                    await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def _handle_piper(self, reader, writer) -> None:
        try:
            while True:
                event = await async_read_event(reader)
                if event is None:
                    return
                if Synthesize.is_type(event.type):
                    await async_write_event(
                        AudioStart(rate=22050, width=2, channels=1).event(), writer
                    )
                    for chunk in FAKE_TTS_CHUNKS:
                        await async_write_event(
                            AudioChunk(audio=chunk, rate=22050, width=2, channels=1).event(),
                            writer,
                        )
                    await async_write_event(AudioStop().event(), writer)
                    await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


@pytest.fixture(scope="session")
def fake_wyoming() -> FakeWyoming:
    server = FakeWyoming()
    server.start()
    yield server
    server.stop()
