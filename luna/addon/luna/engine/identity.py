"""L2 — le moteur d'identité (§6).

Il orchestre trois choses et n'en calcule aucune lui-même : la fusion vit dans
L0 (`kernel/identity.py`), l'empreinte dans un provider de L1, la présence dans
Home Assistant. Ici on décide **quand** demander quoi.

Deux règles gouvernent tout le fichier :

* **C1 — on ne calcule que si c'est utile.** Si l'utilisateur Home Assistant
  désigne un profil, l'identité est déjà résolue : aucune empreinte n'est
  calculée, aucun audio n'est demandé. Sur un N95, ne pas calculer est la
  meilleure optimisation.
* **C5 — l'identité personnalise, elle n'autorise jamais.** Ce fichier fixe un
  profil, donc un scope. Il ne touche ni au registre d'autonomie, ni à
  `peut_valider`, qui continue de lire la session Home Assistant.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Callable
from datetime import datetime

from ..kernel.bus import Bus
from ..kernel.contracts import EmpreinteProvider, MaisonProvider, MemoireProvider
from ..kernel.errors import (
    AudioTropCourt,
    BiometrieDistante,
    LunaError,
    ModeleVoixIndisponible,
    ProfilNonInscrit,
)
from ..kernel.identity import (
    DUREE_IDENTITE,
    INCONNU,
    Candidat,
    Decision,
    centroide,
    coherence,
    presence_depuis_etat,
    similarite,
)
from ..kernel.ids import nouvel_id
from ..kernel.schemas import (
    ContexteRequete,
    EmpreinteVocale,
    EntreeIdentite,
    EtatIdentite,
    EvtIdentite,
    ProfilActif,
)

log = logging.getLogger("luna.identite")

#: Cinq phrases, variées comme le veut H46 : une question, une énumération,
#: des chiffres, une phrase longue. Cinq fois la même reconnaîtrait la phrase,
#: pas la personne.
PHRASES_INSCRIPTION = (
    "Bonsoir Luna, c'est moi. Tu peux éteindre le salon ?",
    "Il est vingt-deux heures quarante, la température est de dix-neuf degrés.",
    "Allume la cuisine, ferme les volets, et mets la scène cinéma.",
    "Est-ce que la baie vitrée est restée ouverte pendant la nuit ?",
    "Quand je rentre le soir, j'aime bien que la lumière du couloir soit douce, "
    "et que la musique reprenne là où je l'avais laissée.",
)

#: En dessous, l'échantillon n'apprend rien.
DUREE_MINIMALE = 1.0
NIVEAU_MINIMAL = 0.02
TAUX = 16000

TAILLE_MIN = int(TAUX * DUREE_MINIMALE) * 2


class SessionInscription:
    def __init__(self, identifiant: str, profil: str) -> None:
        self.id = identifiant
        self.profil = profil
        self.echantillons: dict[int, list[float]] = {}
        self.ouverte_a = datetime.now().astimezone()


def _niveau(pcm: bytes) -> float:
    """Niveau efficace du signal, sur [0, 1].

    En Python pur : `audioop` a disparu en 3.13, et faire entrer numpy dans L2
    pour une somme de carrés serait payer cher un calcul de trois lignes.
    """
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    valeurs = struct.unpack(f"<{n}h", pcm[: n * 2])
    return (sum(v * v for v in valeurs) / n) ** 0.5 / 32768.0


class MoteurIdentite:
    def __init__(
        self,
        *,
        empreinte: EmpreinteProvider,
        maison: MaisonProvider,
        memoire: MemoireProvider,
        bus: Bus,
        profils: Callable[[], list[str]],
        capteur_presence: Callable[[str], str | None],
        profil_de_session: Callable[[ContexteRequete], str | None],
        noms: dict[str, str],
    ) -> None:
        self._empreinte = empreinte
        self._maison = maison
        self._memoire = memoire
        self._bus = bus
        self._profils = profils
        self._capteur = capteur_presence
        self._profil_de_session = profil_de_session
        self._noms = noms
        self._etats: dict[str, EtatIdentite] = {}
        self._inscriptions: dict[str, SessionInscription] = {}

    # ── État courant ─────────────────────────────────────────────────────

    def etat(self, contexte: ContexteRequete) -> EtatIdentite:
        """Le profil actif pour cet appareil.

        La session Home Assistant gagne toujours : quand elle désigne quelqu'un,
        il n'y a rien à deviner et rien à périmer (C1, C6).
        """
        depuis_session = self._profil_de_session(contexte)
        if depuis_session is not None:
            return EtatIdentite(
                device=contexte.device or "session",
                profil=depuis_session,
                confiance=1.0,
                signals={"ha_user": 1.0},
                ancre_sur_session=True,
            )

        appareil = contexte.device or "inconnu"
        etat = self._etats.get(appareil)
        if etat is None:
            return EtatIdentite(device=appareil, profil=INCONNU, confiance=0.0)
        if etat.expires_at and datetime.now().astimezone() > etat.expires_at:
            self._etats.pop(appareil, None)
            return EtatIdentite(device=appareil, profil=INCONNU, confiance=0.0)
        return etat

    async def info(self, contexte: ContexteRequete) -> dict[str, object]:
        etat = self.etat(contexte)
        inscrits = sorted(
            {e.profile for e in await self._memoire.empreintes(self._empreinte.nom)}
        )
        return {
            "profile": self._profil_actif(etat).model_dump(mode="json"),
            "expires_at": etat.expires_at.isoformat() if etat.expires_at else None,
            "enrolled": inscrits,
            # C1 : la carte ne se donne la peine d'envoyer de l'audio que si
            # l'identité n'est pas déjà résolue par la session.
            "voice_needed": not etat.ancre_sur_session and self._empreinte.disponible,
            "voice_available": self._empreinte.disponible,
        }

    def _profil_actif(self, etat: EtatIdentite) -> ProfilActif:
        return ProfilActif(
            id=etat.profil,
            display_name=self._noms.get(etat.profil, etat.profil.capitalize()),
            confidence=etat.confiance,
            signals=etat.signals,
        )

    # ── Identification ───────────────────────────────────────────────────

    async def identifier(
        self, pcm: bytes, *, contexte: ContexteRequete
    ) -> tuple[Decision, EtatIdentite]:
        self._exiger_local(contexte)
        appareil = contexte.device or "inconnu"

        vecteur = self._empreinte.encoder(pcm)
        candidats = await self._candidats(vecteur)
        decision = self._fusion(candidats)

        await self._memoire.journaliser_identite(
            EntreeIdentite(
                id=nouvel_id("i"),
                ts=datetime.now().astimezone(),
                device=appareil,
                decided=decision.profil,
                confidence=decision.confiance,
                margin=decision.marge,
                signals={
                    c.profil: {
                        "presence": round(c.presence, 3),
                        "voix": round(c.voix, 3),
                        "cosinus": round(c.cosinus, 3),
                    }
                    for c in candidats
                },
                asked=decision.demander,
            )
        )

        if not decision.decide:
            self._etats.pop(appareil, None)
            etat = EtatIdentite(device=appareil, profil=INCONNU, confiance=0.0)
            await self._publier(etat)
            return decision, etat

        etat = await self._poser(
            appareil,
            decision.profil,
            decision.confiance,
            {
                "presence": next(
                    (c.presence for c in candidats if c.profil == decision.profil), 0.0
                ),
                "voice": next(
                    (c.voix for c in candidats if c.profil == decision.profil), 0.0
                ),
            },
        )
        return decision, etat

    async def confirmer(
        self, *, contexte: ContexteRequete, profil: str, accepte: bool
    ) -> EtatIdentite:
        """La branche « Luna demande » de C4.

        Une confirmation vaut identité pour la durée de vie courante. Elle ne
        crée pas d'empreinte : on ne sait pas si l'audio était propre, et une
        mauvaise empreinte est pire que pas d'empreinte.
        """
        appareil = contexte.device or "inconnu"
        if not accepte:
            self._etats.pop(appareil, None)
            etat = EtatIdentite(device=appareil, profil=INCONNU, confiance=0.0)
            await self._publier(etat)
            return etat
        if profil not in self._profils():
            raise LunaError(f"Profil inconnu : {profil}.")
        return await self._poser(appareil, profil, 1.0, {"confirmed": 1.0})

    async def _poser(
        self, appareil: str, profil: str, confiance: float, signaux: dict[str, float]
    ) -> EtatIdentite:
        etat = EtatIdentite(
            device=appareil,
            profil=profil,
            confiance=confiance,
            signals=signaux,
            expires_at=datetime.now().astimezone() + DUREE_IDENTITE,
        )
        self._etats[appareil] = etat
        await self._publier(etat)
        log.info("Identité %s : %s (confiance %.2f)", appareil, profil, confiance)
        return etat

    async def _publier(self, etat: EtatIdentite) -> None:
        await self._bus.publier(EvtIdentite(profile=self._profil_actif(etat)))

    async def _candidats(self, vecteur: list[float]) -> list[Candidat]:
        empreintes = await self._memoire.empreintes(self._empreinte.nom)
        if not empreintes:
            raise ProfilNonInscrit(
                "Personne n'est encore inscrit. Ouvre le panneau d'identité de "
                "la carte pour enregistrer une voix."
            )

        par_profil: dict[str, list[list[float]]] = {}
        for e in empreintes:
            par_profil.setdefault(e.profile, []).append(e.vector)

        presences = await self._presences(list(par_profil))
        return [
            Candidat(
                profil=profil,
                presence=presences.get(profil, 0.5),
                cosinus=similarite(vecteur, centroide(vecteurs)),
            )
            for profil, vecteurs in sorted(par_profil.items())
        ]

    @staticmethod
    def _fusion(candidats: list[Candidat]) -> Decision:
        from ..kernel.identity import fusionner

        return fusionner(candidats)

    async def _presences(self, profils: list[str]) -> dict[str, float]:
        """§6 : « Le téléphone à la maison indique une probabilité. »"""
        voulus = {self._capteur(p): p for p in profils if self._capteur(p)}
        if not voulus:
            return {}
        etats = {}
        try:
            for etat in await self._maison.etats(domaine="device_tracker"):
                if profil := voulus.get(etat.entity_id):
                    etats[profil] = presence_depuis_etat(etat.etat)
        except LunaError as exc:
            # Home Assistant injoignable : on ne présume rien plutôt que de
            # présumer faux.
            log.warning("Présence indisponible : %s", exc.message)
        return etats

    # ── Inscription ──────────────────────────────────────────────────────

    def demarrer_inscription(
        self, *, contexte: ContexteRequete, profil: str
    ) -> tuple[str, list[str]]:
        self._exiger_local(contexte)
        if not self._empreinte.disponible:
            raise ModeleVoixIndisponible()
        if profil not in self._profils():
            raise LunaError(f"Profil inconnu : {profil}.")
        session = SessionInscription(nouvel_id("e"), profil)
        self._inscriptions[session.id] = session
        return session.id, list(PHRASES_INSCRIPTION)

    def ajouter_echantillon(
        self, *, contexte: ContexteRequete, session: str, index: int, pcm: bytes
    ) -> tuple[str, int]:
        self._exiger_local(contexte)
        inscription = self._inscriptions.get(session)
        if inscription is None:
            raise LunaError("Cette inscription n'est plus ouverte. Recommence.")
        if not 0 <= index < len(PHRASES_INSCRIPTION):
            raise LunaError("Numéro de phrase hors de la liste.")

        if len(pcm) < TAILLE_MIN:
            return "trop_court", len(PHRASES_INSCRIPTION) - len(inscription.echantillons)
        if _niveau(pcm) < NIVEAU_MINIMAL:
            return "trop_faible", len(PHRASES_INSCRIPTION) - len(inscription.echantillons)

        inscription.echantillons[index] = self._empreinte.encoder(pcm)
        return "ok", len(PHRASES_INSCRIPTION) - len(inscription.echantillons)

    async def terminer_inscription(
        self, *, contexte: ContexteRequete, session: str
    ) -> dict[str, object]:
        self._exiger_local(contexte)
        inscription = self._inscriptions.pop(session, None)
        if inscription is None:
            raise LunaError("Cette inscription n'est plus ouverte. Recommence.")
        vecteurs = list(inscription.echantillons.values())
        if len(vecteurs) < 2:
            raise AudioTropCourt(
                "Il me faut au moins deux phrases utilisables pour retenir une voix."
            )

        maintenant = datetime.now().astimezone()
        for vecteur in vecteurs:
            await self._memoire.ajouter_empreinte(
                EmpreinteVocale(
                    id=nouvel_id("v"),
                    profile=inscription.profil,
                    vector=vecteur,
                    model=self._empreinte.nom,
                    source="enrolment",
                    created_at=maintenant,
                )
            )
        cohesion = coherence(vecteurs)
        log.info(
            "Inscription de %s : %s phrases, cohérence %.2f",
            inscription.profil,
            len(vecteurs),
            cohesion,
        )
        return {
            "profile": inscription.profil,
            "samples": len(vecteurs),
            "coherence": round(cohesion, 3),
        }

    async def oublier(self, *, profil: str) -> int:
        efface = await self._memoire.oublier_empreintes(profil)
        for appareil, etat in list(self._etats.items()):
            if etat.profil == profil:
                self._etats.pop(appareil, None)
        return efface

    # ── Garde-fou ────────────────────────────────────────────────────────

    @staticmethod
    def _exiger_local(contexte: ContexteRequete) -> None:
        """§6 : la biométrie n'est active que sur le réseau local.

        L'intégration refuse déjà en amont — c'est là que ça compte, pour
        qu'aucun octet ne traverse le relais Nabu Casa. Ce contrôle-ci est la
        seconde barrière, celle qui tient si la première est contournée.
        """
        if not contexte.local:
            raise BiometrieDistante()
