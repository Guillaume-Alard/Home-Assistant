"""Doublures pour tester L2 sans Claude, sans Home Assistant et sans réseau.

Aucun test de cette suite n'appelle l'API Anthropic : la CI ne consomme jamais
de crédit (hypothèse H19).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from luna.engine.arbiter import Arbitre
from luna.engine.orchestrator import Orchestrateur
from luna.kernel.contracts import ExecuteurOutil
from luna.kernel.errors import MaisonIndisponible
from luna.kernel.schemas import (
    ActionHA,
    ContexteRequete,
    EtatEntite,
    EvenementCerveau,
    Piece,
    Usage,
)
from luna.providers.store import MagasinSQLite


class FauxMaison:
    """Satisfait `MaisonProvider` sans réseau. Enregistre tout ce qu'on lui demande."""

    def __init__(self) -> None:
        self.connectee = True
        self.appels: list[ActionHA] = []
        self.echoue = False
        self._entites = {
            "light.salon_plafond": ("Plafond du salon", "off", "sejour"),
            "light.salon_lampadaire": ("Lampadaire", "off", "sejour"),
            "light.cuisine": ("Cuisine", "on", "cuisine"),
            "switch.cafetiere": ("Cafetière", "off", "cuisine"),
            "cover.baie_vitree": ("Baie vitrée", "open", "sejour"),
            "climate.sejour": ("Thermostat séjour", "heat", "sejour"),
            "scene.cinema": ("Cinéma", "unknown", None),
            "alarm_control_panel.alarmo": ("Alarmo", "disarmed", None),
        }
        self._pieces = {"sejour": "Séjour", "cuisine": "Cuisine"}

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
        if self.echoue:
            raise MaisonIndisponible()
        self.appels.append(action)


class FauxCerveau:
    """Rejoue un scénario d'événements au lieu d'appeler l'API.

    Un élément `("outil", nom, entrée)` déclenche vraiment l'exécuteur injecté :
    c'est ce qui permet de tester l'arbitre de bout en bout.
    """

    def __init__(self, scenario: list[tuple[Any, ...]] | None = None) -> None:
        self.scenario = scenario or [("texte", "Bonjour.")]
        self.appels: list[dict[str, Any]] = []
        self.leve: Exception | None = None

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
    return Arbitre(maison, memoire)


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
