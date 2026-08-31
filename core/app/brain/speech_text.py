"""Préparation du texte pour la voix.

- `SentenceChunker` : découpe un flux de texte (deltas du LLM) en phrases
  complètes, pour lancer la synthèse vocale sans attendre la fin de la réponse.
- `markdown_to_speech` : convertit une phrase Markdown en texte prononçable.
"""

from __future__ import annotations

import re

# Fin de phrase : ponctuation forte, éventuelles guillemets/parenthèses fermants,
# suivie d'un blanc. Limite connue (acceptée en Phase 1) : les abréviations du
# type « M. Dupont » provoquent une coupure — sans effet sur le sens prononcé.
_SENTENCE_END = re.compile(r"[.!?…]+[\"'»)\]]*(?=\s)")


class SentenceChunker:
    def __init__(self, max_buffer: int = 400):
        self._buffer = ""
        self._max_buffer = max_buffer

    def feed(self, delta: str) -> list[str]:
        """Ajoute un delta de texte, renvoie les phrases complètes disponibles."""
        self._buffer += delta
        sentences: list[str] = []
        while True:
            cut = self._find_cut(self._buffer)
            if cut is None:
                break
            sentence = self._buffer[:cut].strip()
            self._buffer = self._buffer[cut:].lstrip()
            if sentence:
                sentences.append(sentence)

        # Garde-fou : très longue « phrase » sans ponctuation → coupe au dernier espace
        while len(self._buffer) > self._max_buffer:
            space = self._buffer.rfind(" ", 0, self._max_buffer)
            if space <= 0:
                break
            sentences.append(self._buffer[:space].strip())
            self._buffer = self._buffer[space:].lstrip()

        return sentences

    def flush(self) -> str | None:
        """Renvoie ce qui reste dans le tampon (fin de flux)."""
        rest = self._buffer.strip()
        self._buffer = ""
        return rest or None

    @staticmethod
    def _find_cut(text: str) -> int | None:
        """Position de coupe la plus proche, hors blocs de code ``` non refermés."""
        candidates: list[int] = []

        for match in _SENTENCE_END.finditer(text):
            if text.count("```", 0, match.end()) % 2 == 0:
                candidates.append(match.end())
                break

        idx = text.find("\n")
        while idx != -1:
            if text.count("```", 0, idx) % 2 == 0:
                candidates.append(idx + 1)
                break
            idx = text.find("\n", idx + 1)

        return min(candidates) if candidates else None


def markdown_to_speech(text: str) -> str:
    """Rend une phrase Markdown prononçable (le texte affiché reste, lui, intact)."""
    # Blocs de code → mention orale ; fence orpheline → supprimée
    text = re.sub(r"```.*?```", " (code affiché à l'écran) ", text, flags=re.S)
    text = re.sub(r"^\s*```.*$", "", text, flags=re.M)
    # Lignes de tableau → supprimées (le tableau reste visible dans le chat)
    text = re.sub(r"^\s*\|.*\|\s*$", "", text, flags=re.M)
    # Images / liens → texte alternatif ou libellé
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    # Code en ligne, titres, citations, puces, emphase
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"\*\*|__|\*|_", "", text)
    # Symboles muets : dessins de boîtes, émojis
    text = re.sub(r"[─-▟]", "", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", text)
    return re.sub(r"\s+", " ", text).strip()
