"""L1 — l'empreinte de locuteur, en ONNX (décision C2).

`onnxruntime` pèse 23 Mo, PyTorch plus de huit cents. Sur une machine dont §2
dit que « le CPU N95 est le facteur limitant », le choix ne se discute pas.

**Le modèle est remplaçable.** Son nom voyage avec chaque empreinte : en
changer invalide les anciennes au lieu de produire des ressemblances
silencieusement fausses. Rien ici ne suppose un modèle particulier — la forme
de l'entrée est inspectée au chargement, et un modèle incompatible se signale
par un message clair plutôt que par un vecteur absurde.

Le chargement est **paresseux et tolérant** : Luna démarre, converse et pilote
la maison même si le modèle manque. Seule la reconnaissance de voix s'éteint,
et elle le dit.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Any

from ..kernel.errors import AudioTropCourt, ModeleVoixIndisponible

log = logging.getLogger("luna.empreinte")

TAUX = 16000
#: En dessous, l'empreinte n'a aucune valeur : mieux vaut le dire.
DUREE_MINIMALE = 1.0
#: Au-delà, on tronque : un vecteur ne gagne rien à écouter plus longtemps,
#: et le N95 a mieux à faire (décision C3).
DUREE_MAXIMALE = 8.0

_OCTETS_MIN = int(TAUX * DUREE_MINIMALE) * 2
_OCTETS_MAX = int(TAUX * DUREE_MAXIMALE) * 2


class EmpreinteOnnx:
    def __init__(self, chemin: str) -> None:
        self._chemin = chemin
        self._nom = Path(chemin).stem if chemin else ""
        self._session: Any = None
        self._np: Any = None
        self._entree: str = ""
        self._rang: int = 2
        self._tente = False
        self._motif = ""

    # ── Chargement ───────────────────────────────────────────────────────

    @property
    def nom(self) -> str:
        return self._nom

    @property
    def disponible(self) -> bool:
        self._charger()
        return self._session is not None

    @property
    def motif_indisponible(self) -> str:
        self._charger()
        return self._motif

    def _charger(self) -> None:
        if self._tente:
            return
        self._tente = True

        if not self._chemin:
            self._motif = "aucun modèle n'est configuré (option « modele_voix »)"
            return
        if not Path(self._chemin).is_file():
            self._motif = f"fichier introuvable : {self._chemin}"
            log.warning("Empreinte vocale désactivée — %s", self._motif)
            return

        try:
            import numpy
            import onnxruntime
        except ImportError as exc:  # pragma: no cover - dépend de l'image
            self._motif = f"onnxruntime absent de l'image ({exc})"
            log.warning("Empreinte vocale désactivée — %s", self._motif)
            return

        options = onnxruntime.SessionOptions()
        # Le N95 a quatre cœurs et Whisper tourne à côté : on n'en prend pas
        # plus que nécessaire.
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        try:
            session = onnxruntime.InferenceSession(
                self._chemin, options, providers=["CPUExecutionProvider"]
            )
        except Exception as exc:  # noqa: BLE001 — un modèle illisible n'arrête pas Luna
            self._motif = f"modèle illisible : {exc}"
            log.warning("Empreinte vocale désactivée — %s", self._motif)
            return

        entree = session.get_inputs()[0]
        rang = len(entree.shape)
        if rang not in (1, 2):
            self._motif = (
                f"ce modèle attend une entrée de rang {rang} ({entree.shape}). "
                "Luna ne sait fournir que la forme d'onde brute, en rang 1 ou 2. "
                "Un modèle attendant des coefficients mel demande une étape de "
                "calcul qui n'est pas écrite."
            )
            log.warning("Empreinte vocale désactivée — %s", self._motif)
            return

        self._session = session
        self._np = numpy
        self._entree = entree.name
        self._rang = rang
        log.info(
            "Empreinte vocale : %s (entrée %s %s)", self._nom, entree.name, entree.shape
        )

    # ── Inférence ────────────────────────────────────────────────────────

    def encoder(self, pcm: bytes) -> list[float]:
        """PCM 16 bits, 16 kHz, mono → empreinte normalisée."""
        self._charger()
        if self._session is None:
            raise ModeleVoixIndisponible(
                f"Reconnaissance de voix indisponible : {self._motif}."
            )
        if len(pcm) < _OCTETS_MIN:
            raise AudioTropCourt()

        np = self._np
        brut = pcm[:_OCTETS_MAX]
        # Aligner sur des échantillons entiers : une troncature au milieu d'un
        # échantillon décalerait tout le reste.
        brut = brut[: len(brut) - (len(brut) % 2)]

        audio = np.frombuffer(brut, dtype="<i2").astype(np.float32) / 32768.0
        crete = float(np.max(np.abs(audio))) if audio.size else 0.0
        if crete > 0:
            audio = audio / crete

        entree = audio[None, :] if self._rang == 2 else audio
        sortie = self._session.run(None, {self._entree: entree})[0]
        vecteur = np.asarray(sortie).reshape(-1).astype(np.float32)
        norme = float(np.linalg.norm(vecteur))
        if norme > 0:
            vecteur = vecteur / norme
        return [float(x) for x in vecteur]


def empaqueter(vecteur: list[float]) -> bytes:
    """Vecteur → BLOB SQLite. Float32, petit-boutien, sans en-tête."""
    return struct.pack(f"<{len(vecteur)}f", *vecteur)


def depaqueter(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))
