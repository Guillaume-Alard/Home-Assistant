"""Session de capture audio d'un client : accumule le PCM entre audio_start et audio_end."""

from __future__ import annotations


class CaptureSession:
    def __init__(self, max_seconds: int = 60):
        self._max_seconds = max_seconds
        self._buffer = bytearray()
        self._active = False
        self._rate = 16000
        self._width = 2
        self._channels = 1

    @property
    def active(self) -> bool:
        return self._active

    def start(self, rate: int = 16000, width: int = 2, channels: int = 1) -> None:
        self._buffer = bytearray()
        self._rate = max(8000, min(rate, 48000))
        self._width = width
        self._channels = channels
        self._active = True

    def add(self, chunk: bytes) -> bool:
        """Ajoute un morceau de PCM. Renvoie False quand la durée maximale est atteinte."""
        if not self._active:
            return True
        self._buffer.extend(chunk)
        max_bytes = self._rate * self._width * self._channels * self._max_seconds
        return len(self._buffer) <= max_bytes

    def finish(self) -> tuple[bytes, int]:
        """Clôt la capture et renvoie (pcm, fréquence d'échantillonnage)."""
        pcm = bytes(self._buffer)
        rate = self._rate
        self.reset()
        return pcm, rate

    def reset(self) -> None:
        self._buffer = bytearray()
        self._active = False
