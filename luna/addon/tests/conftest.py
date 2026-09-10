"""Doublures pour tester L2 sans Claude, sans Home Assistant et sans réseau.

Aucun test de cette suite n'appelle l'API Anthropic : la CI ne consomme jamais
de crédit (hypothèse H19).
"""

from __future__ import annotations

import struct
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest

from luna.engine.arbiter import Arbitre
from luna.engine.health import Gardienne
from luna.engine.identity import MoteurIdentite
from luna.engine.orchestrator import Orchestrateur
from luna.engine.veille import MoteurVeille
from luna.kernel.bus import Bus
from luna.kernel.contracts import ExecuteurOutil
from luna.kernel.errors import MaisonIndisponible
from luna.kernel.schemas import (
    ActionHA,
    ChangementEtat,
    ContexteRequete,
    EnregistrementJournal,
    EntreeConfig,
    EtatEntite,
    EvenementCerveau,
    Piece,
    Usage,
)
from luna.kernel.settings import Gardienne as ReglagesGardienne
from luna.providers.store import MagasinSQLite


class FauxMaison:
    """Satisfait `MaisonProvider` sans réseau. Enregistre tout ce qu'on lui demande."""

    def __init__(self) -> None:
        self.connectee = True
        self.appels: list[ActionHA] = []
        self.echoue = False
        #: Entités qui acceptent l'appel sans changer d'état — l'ampoule
        #: retirée de sa douille. C'est le cas que §8 rendait invisible.
        self.sourdes: set[str] = set()
        #: Entités qui ne disent plus leur état après l'appel.
        self.muettes: set[str] = set()
        self._entites = {
            "light.salon_plafond": ("Plafond du salon", "off", "sejour"),
            "light.salon_lampadaire": ("Lampadaire", "off", "sejour"),
            "light.cuisine": ("Cuisine", "on", "cuisine"),
            "switch.cafetiere": ("Cafetière", "off", "cuisine"),
            "cover.baie_vitree": ("Baie vitrée", "open", "sejour"),
            "climate.sejour": ("Thermostat séjour", "heat", "sejour"),
            "scene.cinema": ("Cinéma", "unknown", None),
            "alarm_control_panel.alarmo": ("Alarmo", "disarmed", None),
            "device_tracker.tel_guillaume": ("Téléphone de Guillaume", "home", None),
            "device_tracker.tel_clara": ("Téléphone de Clara", "home", None),
        }
        self._pieces = {"sejour": "Séjour", "cuisine": "Cuisine"}
        #: entity_id → (entry_id, plateforme). Trois lampes du même hub : de
        #: quoi éprouver le regroupement de H68.
        self.entrees_entites: dict[str, tuple[str, str]] = {
            "light.salon_plafond": ("e_zigbee", "mqtt"),
            "light.salon_lampadaire": ("e_zigbee", "mqtt"),
            "light.cuisine": ("e_zigbee", "mqtt"),
            "switch.cafetiere": ("e_zigbee", "mqtt"),
            "climate.sejour": ("e_netatmo", "netatmo"),
        }
        self.entrees: list[EntreeConfig] = []
        self.journal: list[EnregistrementJournal] = []
        self.loggia: dict[str, Any] = {}
        self.version_ha = "2026.2.3"

    async def pieces(self) -> list[Piece]:
        compte: dict[str, dict[str, int]] = {a: {} for a in self._pieces}
        for entity_id, (_, _, area) in self._entites.items():
            if area:
                domaine = entity_id.split(".", 1)[0]
                compte[area][domaine] = compte[area].get(domaine, 0) + 1
        return [
            Piece(area_id=a, nom=n, entites=compte[a]) for a, n in self._pieces.items()
        ]

    async def etats(
        self, *, piece: str | None = None, domaine: str | None = None
    ) -> list[EtatEntite]:
        vise = None
        if piece:
            vise = next(
                (a for a, n in self._pieces.items() if n.lower() == piece.lower()), "?"
            )
        return [
            EtatEntite(
                entity_id=eid,
                nom=nom,
                etat=etat,
                piece=self._pieces.get(area) if area else None,
            )
            for eid, (nom, etat, area) in sorted(self._entites.items())
            if (not domaine or eid.startswith(f"{domaine}."))
            and (vise is None or area == vise)
        ]

    async def resoudre(self, cible: str, domaines: tuple[str, ...]) -> list[str]:
        if cible in self._entites:
            return [cible] if cible.split(".", 1)[0] in domaines else []
        cible_bas = cible.lower().removeprefix("le ").removeprefix("la ")
        area = next((a for a, n in self._pieces.items() if n.lower() == cible_bas), None)
        if area:
            return sorted(
                eid
                for eid, (_, _, a) in self._entites.items()
                if a == area and eid.split(".", 1)[0] in domaines
            )
        return sorted(
            eid
            for eid, (nom, _, _) in self._entites.items()
            if eid.split(".", 1)[0] in domaines and cible_bas in nom.lower()
        )

    async def appeler_service(self, action: ActionHA) -> None:
        """Accepte l'appel **et applique son effet**, comme le vrai.

        Un faux qui enregistre sans agir ne peut pas distinguer « accepté » de
        « fait » — la confusion même que la vérification traque. Les entités
        `sourdes` acceptent sans obéir ; les `muettes` cessent de répondre.
        """
        if self.echoue:
            raise MaisonIndisponible()
        self.appels.append(action)
        vise = {
            "turn_on": "on",
            "turn_off": "off",
        }.get(action.service)
        cibles = action.target.get("entity_id") or []
        if isinstance(cibles, str):
            cibles = [cibles]
        for identifiant in cibles:
            if identifiant not in self._entites:
                continue
            nom, etat, area = self._entites[identifiant]
            if identifiant in self.muettes:
                self._entites[identifiant] = (nom, "unavailable", area)
                continue
            if identifiant in self.sourdes:
                continue
            if vise is not None:
                self._entites[identifiant] = (nom, vise, area)
            elif action.service == "toggle":
                self._entites[identifiant] = (
                    nom,
                    "off" if etat == "on" else "on",
                    area,
                )

    # ── Ce que P5 demande en plus (lecture seule) ────────────────────────

    def integration_de(self, entity_id: str) -> tuple[str, str]:
        return self.entrees_entites.get(entity_id, ("", ""))

    def nom_entite(self, entity_id: str) -> str:
        nom, _, _ = self._entites.get(entity_id, (entity_id, None, None))
        return nom or entity_id

    def entites_connues(self) -> set[str]:
        return set(self._entites)

    async def entrees_config(self):
        return list(self.entrees)

    async def journal_systeme(self):
        """`None` = droits refusés, liste vide = rien à signaler."""
        return None if self.journal is None else list(self.journal)

    async def config_loggia(self, url_path: str = ""):
        return dict(self.loggia)

    # ── Ce que P4 demande en plus ────────────────────────────────────────

    def nom_piece(self, entity_id: str) -> str | None:
        _, _, area = self._entites.get(entity_id, (None, None, None))
        return self._pieces.get(area) if area else None

    def poser(self, entity_id: str, etat: str) -> ChangementEtat:
        """Change un état et rend l'événement que le vrai client publierait."""
        nom, ancien, area = self._entites.get(entity_id, (entity_id, None, None))
        self._entites[entity_id] = (nom, etat, area)
        return ChangementEtat(
            entity_id=entity_id,
            ancien=ancien,
            nouveau=etat,
            ts=datetime.now().astimezone(),
        )


class FauxCerveau:
    """Rejoue un scénario d'événements au lieu d'appeler l'API.

    Un élément `("outil", nom, entrée)` déclenche vraiment l'exécuteur injecté :
    c'est ce qui permet de tester l'arbitre de bout en bout.
    """

    def __init__(self, scenario: list[tuple[Any, ...]] | None = None) -> None:
        self.scenario = scenario or [("texte", "Bonjour.")]
        self.appels: list[dict[str, Any]] = []
        self.leve: Exception | None = None
        #: Ce que l'entretien nocturne recevra en retour (P4).
        self.faits: list[dict[str, str]] = []
        self.extraits: list[str] = []
        #: Ce que la gardienne recevra en retour (P5). Vide = pas de
        #: diagnostic, donc le résumé de secours.
        self.diagnostic: str = ""
        self.constats: list[str] = []

    async def extraire_faits(self, extraits: str) -> list[dict[str, str]]:
        self.extraits.append(extraits)
        if self.leve is not None:
            raise self.leve
        return list(self.faits)

    async def diagnostiquer(self, anomalie: str) -> str:
        """P5 : le modèle met en français, il ne décide de rien."""
        self.constats.append(anomalie)
        if self.leve is not None:
            raise self.leve
        return self.diagnostic

    async def repondre(
        self,
        *,
        historique: list[dict[str, Any]],
        contexte: str,
        outils: list[dict[str, Any]],
        executer_outil: ExecuteurOutil,
    ) -> AsyncIterator[EvenementCerveau]:
        from luna.kernel.schemas import (
            CerveauDelta,
            CerveauOutilDebut,
            CerveauOutilFin,
            CerveauTermine,
        )

        self.appels.append(
            {"historique": historique, "contexte": contexte, "outils": outils}
        )
        if self.leve is not None:
            raise self.leve

        morceaux: list[str] = []
        for element in self.scenario:
            if element[0] == "texte":
                morceaux.append(element[1])
                yield CerveauDelta(text=element[1])
            elif element[0] == "outil":
                _, nom, entree = element
                yield CerveauOutilDebut(name=nom, entree=entree)
                resultat = await executer_outil(nom, entree)
                yield CerveauOutilFin(name=nom, ok=not resultat.erreur)
        yield CerveauTermine(
            text="".join(morceaux),
            usage=Usage(input_tokens=100, output_tokens=10, cache_read_input_tokens=80),
        )


# ── Identité (P3) ────────────────────────────────────────────────────────

#: Trois « locuteurs » synthétiques, marqués dans le premier échantillon PCM.
#: Les vecteurs sont orthogonaux deux à deux : deux voix ne se ressemblent que
#: si on le demande explicitement, ce qui rend les seuils testables.
LOCUTEURS = {1: [1.0, 0.0, 0.0, 0.0], 2: [0.0, 1.0, 0.0, 0.0], 3: [0.0, 0.0, 1.0, 0.0]}


def audio_de(locuteur: int, secondes: float = 3.0, niveau: int = 8000) -> bytes:
    """PCM 16 bits, 16 kHz, mono, dont le premier échantillon dit qui parle."""
    n = int(16000 * secondes)
    echantillons = [locuteur] + [niveau if i % 2 else -niveau for i in range(n - 1)]
    return struct.pack(f"<{len(echantillons)}h", *echantillons)


class FauxEmpreinte:
    """Satisfait `EmpreinteProvider` sans modèle ni ONNX.

    Le vecteur dépend du locuteur marqué dans l'audio, avec un léger bruit :
    deux phrases du même locuteur ne donnent pas exactement le même vecteur,
    comme dans la vraie vie.
    """

    def __init__(self, nom: str = "faux-modele") -> None:
        self.nom = nom
        self.disponible = True
        self.motif_indisponible = ""
        self.appels: list[int] = []
        self._bruit = 0

    def encoder(self, pcm: bytes) -> list[float]:
        if len(pcm) < 16000 * 2:
            from luna.kernel.errors import AudioTropCourt

            raise AudioTropCourt()
        locuteur = struct.unpack("<h", pcm[:2])[0]
        self.appels.append(locuteur)
        base = LOCUTEURS.get(locuteur, LOCUTEURS[3])
        self._bruit += 1
        delta = 0.02 * (self._bruit % 3)
        vecteur = [v + delta for v in base]
        norme = sum(v * v for v in vecteur) ** 0.5
        return [v / norme for v in vecteur]


@pytest.fixture
def empreinte() -> FauxEmpreinte:
    return FauxEmpreinte()


@pytest.fixture
def identite(empreinte, maison, memoire, bus) -> MoteurIdentite:
    return MoteurIdentite(
        empreinte=empreinte,
        maison=maison,
        memoire=memoire,
        bus=bus,
        profils=lambda: ["guillaume", "clara", "liam", "guest"],
        capteur_presence=lambda p: {
            "guillaume": "device_tracker.tel_guillaume",
            "clara": "device_tracker.tel_clara",
        }.get(p),
        profil_de_session=lambda c: None,  # appareil partagé : c'est le cas de P3
        noms={"guillaume": "Guillaume", "clara": "Clara", "liam": "Liam"},
    )


@pytest.fixture
def bus() -> Bus:
    return Bus()


@pytest.fixture
async def memoire(tmp_path):
    magasin = MagasinSQLite(tmp_path / "luna.db")
    await magasin.demarrer()
    yield magasin
    await magasin.fermer()


@pytest.fixture
def maison() -> FauxMaison:
    return FauxMaison()


@pytest.fixture
def arbitre(maison, memoire) -> Arbitre:
    # Le vrai délai est de 1,5 s ; la suite n'a pas à l'attendre.
    return Arbitre(maison, memoire, delai_verification=0.001)


class FauxEmetteur:
    """Recueille ce que la veille pousserait vers les cartes."""

    def __init__(self) -> None:
        self.evenements: list[Any] = []

    async def __call__(self, evenement: Any) -> None:
        self.evenements.append(evenement)

    def genres(self) -> list[str]:
        return [getattr(e, "event", "?") for e in self.evenements]


@pytest.fixture
def emetteur() -> FauxEmetteur:
    return FauxEmetteur()


def veille_avec(
    regles, memoire, maison, arbitre, emetteur, horloge=None, annonce=None
) -> MoteurVeille:
    return MoteurVeille(
        regles=regles,
        memoire=memoire,
        maison=maison,
        arbitre=arbitre,
        emettre=emetteur,
        annonce=annonce,
        horloge=horloge,
    )


@pytest.fixture
def veille(memoire, maison, arbitre, emetteur) -> MoteurVeille:
    """Un moteur sans aucune règle : suffisant pour le relais et les routes."""
    return veille_avec([], memoire, maison, arbitre, emetteur)


def gardienne_avec(
    memoire, maison, cerveau, veille, reglages=None, horloge=None
) -> Gardienne:
    return Gardienne(
        maison=maison,
        memoire=memoire,
        cerveau=cerveau,
        reglages=reglages or ReglagesGardienne(),
        signaler=veille.signaler,
        effacer=veille.lever,
        horloge=horloge,
    )


@pytest.fixture
def gardienne(memoire, maison, veille) -> Gardienne:
    """Une gardienne branchée sur le moteur de veille, comme à l'amorçage."""
    return gardienne_avec(memoire, maison, FauxCerveau(), veille)


@pytest.fixture
def contexte() -> ContexteRequete:
    return ContexteRequete(
        ha_user_id="u-1",
        ha_user_name="Guillaume",
        is_admin=True,
        profile="guillaume",
        client_id="tests",
        local=True,
    )


def orchestrateur_avec(cerveau, maison, memoire, arbitre) -> Orchestrateur:
    return Orchestrateur(
        cerveau=cerveau,
        maison=maison,
        memoire=memoire,
        arbitre=arbitre,
        fuseau="Europe/Paris",
    )


async def collecter(flux) -> list[Any]:
    return [evenement async for evenement in flux]
