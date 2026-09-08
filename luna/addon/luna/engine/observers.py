"""L2 — les observateurs déterministes (D2).

Du code, pas un modèle de langage. Ce que ces observateurs produisent est
**mesuré** : ils naissent donc `active`, avec une confiance construite sur le
nombre d'observations. Les faits déduits d'une conversation, eux, arrivent par
l'entretien nocturne et attendent une relecture.

Ils réagissent au bus, jamais à une horloge de scrutation (H63) : sur un N95 qui
fait déjà tourner Whisper et une empreinte de locuteur, une boucle qui
interrogerait la maison en continu serait le premier vrai gaspillage du projet.

Deux observateurs, et pas un de plus :

* **le coucher** — l'heure à laquelle la maison bascule en contexte « coucher ».
  C'est la matière du rappel de §11 ;
* **les séquences** — deux entités d'une même pièce allumées coup sur coup,
  assez souvent pour que ce soit une manière de faire et pas une coïncidence.
  Désactivé par défaut : c'est le plus bavard, et le moins demandé par §5.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..kernel.contracts import MemoireProvider
from ..kernel.ids import nouvel_id
from ..kernel.schemas import ChangementEtat, EvenementJournal, Fait

log = logging.getLogger("luna.observateurs")

#: Au-delà, ce n'est plus la même habitude de coucher mais une autre : le fait
#: en place est supplanté (H62) plutôt que tiré vers une moyenne qui ne
#: décrirait plus personne.
TOLERANCE_COUCHER = timedelta(minutes=45)

#: Deux allumages séparés de plus de ça ne sont pas une séquence.
FENETRE_SEQUENCE = timedelta(minutes=3)

#: Bornes mémoire de l'observateur de séquences. Il tourne dans un add-on qui
#: doit tenir des mois sans redémarrer : tout ce qu'il retient est plafonné.
DOMAINES_SEQUENCE = ("light", "switch")
MEMOIRE_SEQUENCE = 32


class ObservateurCoucher:
    """L'heure à laquelle la maison se couche, observée soir après soir.

    La valeur du fait est une **moyenne mobile** : chaque soir la déplace un
    peu, sans jamais créer de doublon. Un décalage franc — plus de trois quarts
    d'heure — n'est pas une dérive mais une autre habitude, et supplante la
    précédente.
    """

    PREDICAT = "heure_de_coucher"
    CATEGORIE = "heure_de_coucher"

    def __init__(
        self, memoire: MemoireProvider, *, entite: str, profil: str | None
    ) -> None:
        self._memoire = memoire
        self._entite = entite
        self._profil = profil or None

    @property
    def actif(self) -> bool:
        return bool(self._entite)

    async def sur_changement(self, evenement: ChangementEtat) -> Fait | None:
        if not self.actif or evenement.entity_id != self._entite:
            return None
        if evenement.nouveau != "on" or evenement.ancien == "on":
            return None
        return await self.observer(evenement.ts)

    async def observer(self, quand: datetime) -> Fait | None:
        journal = EvenementJournal(
            id=nouvel_id("e"),
            ts=quand,
            kind="state",
            profile=self._profil,
            entity_id=self._entite,
            payload={"observation": self.PREDICAT, "heure": _hhmm(quand)},
        )
        await self._memoire.enregistrer_evenement(journal)

        courant = await self._fait_courant()
        if courant is None:
            fait = Fait(
                id=nouvel_id("f"),
                predicate=self.PREDICAT,
                value=_hhmm(quand),
                profile=self._profil,
                entity_id=None,
                category=self.CATEGORIE,
                status="active",
                source="observateur",
                created_at=quand,
                last_seen_at=quand,
                observations=1,
                why="Observé sur le contexte « coucher » de la maison.",
            )
            cree, _ = await self._memoire.observer_fait(fait, event_id=journal.id)
            return cree

        ecart = _ecart(courant.value, quand)
        if ecart is not None and ecart <= TOLERANCE_COUCHER:
            moyenne = _moyenne(courant.value, quand, courant.observations)
            return await self._memoire.renforcer_fait(
                courant.id, valeur=moyenne, quand=quand, event_id=journal.id
            )

        # Décalage franc : une autre habitude, pas une dérive.
        fait = Fait(
            id=nouvel_id("f"),
            predicate=self.PREDICAT,
            value=_hhmm(quand),
            profile=self._profil,
            entity_id=None,
            category=self.CATEGORIE,
            status="active",
            source="observateur",
            created_at=quand,
            last_seen_at=quand,
            observations=1,
            why=f"L'heure de coucher a changé — c'était {courant.value}.",
        )
        remplacant, _ = await self._memoire.observer_fait(fait, event_id=journal.id)
        log.info("Coucher : %s remplace %s", remplacant.value, courant.value)
        return remplacant

    async def _fait_courant(self) -> Fait | None:
        for fait in await self._memoire.faits(statut="active"):
            if fait.predicate == self.PREDICAT and fait.profile == self._profil:
                return fait
        return None


class ObservateurSequences:
    """Deux entités d'une même pièce allumées coup sur coup.

    L'observateur ne retient qu'une petite fenêtre glissante d'allumages
    récents, plafonnée : il ne connaît pas l'histoire de la maison, seulement
    les trois dernières minutes. La récurrence, elle, est comptée par la base —
    c'est `observations` sur le fait qui distingue une manière de faire d'une
    coïncidence.
    """

    PREDICAT = "sequence_recurrente"
    CATEGORIE = "sequence_recurrente"

    def __init__(
        self, memoire: MemoireProvider, *, actif: bool, piece_de: object = None
    ) -> None:
        self._memoire = memoire
        self._actif = actif
        # `piece_de(entity_id) -> str | None`, fourni par le client HA. Sans lui,
        # l'observateur reste inerte : une séquence entre deux pièces sans
        # rapport n'apprend rien.
        self._piece_de = piece_de
        self._recents: list[tuple[str, str, datetime]] = []

    @property
    def actif(self) -> bool:
        return self._actif and self._piece_de is not None

    async def sur_changement(self, evenement: ChangementEtat) -> Fait | None:
        if not self.actif:
            return None
        domaine = evenement.entity_id.split(".", 1)[0]
        if domaine not in DOMAINES_SEQUENCE or evenement.nouveau != "on":
            return None
        if evenement.ancien == "on":
            return None

        piece = self._piece_de(evenement.entity_id)  # type: ignore[misc]
        if not piece:
            return None

        precedent = self._precedent(piece, evenement)
        self._retenir(piece, evenement)
        if precedent is None:
            return None

        fait = Fait(
            id=nouvel_id("f"),
            predicate=self.PREDICAT,
            value=f"{precedent} → {evenement.entity_id}",
            profile=None,
            entity_id=precedent,
            category=self.CATEGORIE,
            status="active",
            source="observateur",
            created_at=evenement.ts,
            last_seen_at=evenement.ts,
            observations=1,
            why=f"Observé dans « {piece} », deux allumages coup sur coup.",
        )
        observe, neuf = await self._memoire.observer_fait(fait)
        if neuf:
            log.debug("Séquence repérée : %s", fait.value)
        return observe

    def _precedent(self, piece: str, evenement: ChangementEtat) -> str | None:
        limite = evenement.ts - FENETRE_SEQUENCE
        for entite, sa_piece, quand in reversed(self._recents):
            if quand < limite:
                break
            if sa_piece == piece and entite != evenement.entity_id:
                return entite
        return None

    def _retenir(self, piece: str, evenement: ChangementEtat) -> None:
        self._recents.append((evenement.entity_id, piece, evenement.ts))
        if len(self._recents) > MEMOIRE_SEQUENCE:
            del self._recents[: len(self._recents) - MEMOIRE_SEQUENCE]


# ── Arithmétique des heures ──────────────────────────────────────────────


def _hhmm(quand: datetime) -> str:
    return quand.strftime("%H:%M")


def _minutes(valeur: str) -> int | None:
    try:
        heures, minutes = valeur.split(":", 1)
        return int(heures) * 60 + int(minutes)
    except ValueError:
        return None


def _ecart(valeur: str, quand: datetime) -> timedelta | None:
    """L'écart entre une heure enregistrée et une observation, à travers minuit.

    Sans le passage par minuit, un coucher à 23 h 50 et un autre à 00 h 10
    seraient à vingt-trois heures quarante l'un de l'autre au lieu de vingt
    minutes — et chaque nuit supplanterait la précédente.
    """
    stockee = _minutes(valeur)
    if stockee is None:
        return None
    observee = quand.hour * 60 + quand.minute
    brut = abs(observee - stockee)
    return timedelta(minutes=min(brut, 1440 - brut))


def _moyenne(valeur: str, quand: datetime, observations: int) -> str:
    """Moyenne mobile, en tenant compte du passage par minuit."""
    stockee = _minutes(valeur)
    observee = quand.hour * 60 + quand.minute
    if stockee is None:
        return _hhmm(quand)
    # On ramène l'observation du côté de la valeur stockée avant de moyenner :
    # 23 h 50 et 00 h 10 se moyennent en 00 h 00, pas en 11 h 50.
    if observee - stockee > 720:
        observee -= 1440
    elif stockee - observee > 720:
        observee += 1440
    poids = max(1, observations)
    moyenne = round((stockee * poids + observee) / (poids + 1)) % 1440
    return f"{moyenne // 60:02d}:{moyenne % 60:02d}"
