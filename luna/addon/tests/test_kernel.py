"""L0 — le registre d'autonomie, les scopes et le bus."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from luna.kernel.autonomy import (
    NIVEAU_PAR_DEFAUT,
    REGISTRE,
    Niveau,
    doit_etre_journalise,
    niveau_de,
)
from luna.kernel.bus import Bus
from luna.kernel.permissions import peut_agir_seule, peut_valider
from luna.kernel.settings import Reglages


class TestRegistreAutonomie:
    def test_service_connu(self):
        assert niveau_de("light", "turn_on") is Niveau.CONFORT
        assert niveau_de("climate", "set_temperature") is Niveau.PERSISTANT
        assert niveau_de("automation", "reload") is Niveau.CONFIGURATION

    def test_joker_de_domaine(self):
        """Tout `cover.*` est niveau 5, même un service inventé."""
        assert niveau_de("cover", "open_cover") is Niveau.INTERDIT_V1
        assert niveau_de("cover", "un_service_qui_nexiste_pas") is Niveau.INTERDIT_V1
        assert niveau_de("lock", "unlock") is Niveau.INTERDIT_V1
        assert niveau_de("alarm_control_panel", "alarm_disarm") is Niveau.INTERDIT_V1
        assert niveau_de("notify", "mobile_app_guillaume") is Niveau.INTERDIT_V1

    def test_defaut_est_trois(self):
        """§9.1 : « Le niveau par défaut d'une action non classée est 3. »

        C'est la règle qui fait qu'un oubli échoue du côté sûr.
        """
        assert NIVEAU_PAR_DEFAUT is Niveau.PERSISTANT
        assert niveau_de("domaine_inexistant", "service_inexistant") is Niveau.PERSISTANT
        assert niveau_de("light", "un_service_ajoute_demain") is Niveau.PERSISTANT

    def test_journalisation_au_dessus_de_trois(self):
        assert not doit_etre_journalise(Niveau.CONFORT)
        assert doit_etre_journalise(Niveau.PERSISTANT)
        assert doit_etre_journalise(Niveau.CONFIGURATION)
        assert doit_etre_journalise(Niveau.INTERDIT_V1)

    def test_les_domaines_hors_perimetre_sont_bien_au_niveau_5(self):
        """Décision A2 : ouvrants, serrures, alarme et envoi externe."""
        for domaine in ("cover", "lock", "alarm_control_panel", "notify"):
            assert REGISTRE[f"{domaine}.*"] is Niveau.INTERDIT_V1


class TestPermissions:
    @pytest.mark.parametrize("profil", ["guillaume", "clara", "liam", "guest"])
    def test_le_confort_est_ouvert(self, profil):
        assert peut_agir_seule(profil, Niveau.CONFORT)

    def test_inconnu_ne_declenche_rien(self):
        assert not peut_agir_seule("unknown", Niveau.CONFORT)
        assert peut_agir_seule("unknown", Niveau.LIRE)

    def test_le_niveau_3_nest_libre_pour_personne(self):
        """Même Guillaume : §9 dit « validation explicite », sans exception."""
        for profil in ("guillaume", "clara", "liam", "guest", "unknown"):
            assert not peut_agir_seule(profil, Niveau.PERSISTANT)
            assert not peut_agir_seule(profil, Niveau.CONFIGURATION)

    def test_le_niveau_4_exige_un_administrateur(self):
        assert peut_valider(Niveau.PERSISTANT, est_admin=False)
        assert not peut_valider(Niveau.CONFIGURATION, est_admin=False)
        assert peut_valider(Niveau.CONFIGURATION, est_admin=True)


class Ping(BaseModel):
    valeur: int


class Pong(BaseModel):
    valeur: int


class TestBus:
    async def test_livraison_et_desabonnement(self):
        bus = Bus()
        recus: list[int] = []
        desabonner = bus.abonner(Ping, lambda e: _noter(recus, e.valeur))

        await bus.publier(Ping(valeur=1))
        await bus.publier(Pong(valeur=99))  # autre type : ignoré
        assert recus == [1]

        desabonner()
        await bus.publier(Ping(valeur=2))
        assert recus == [1]
        assert bus.nombre_abonnes(Ping) == 0

    async def test_un_abonne_qui_leve_nempeche_pas_les_autres(self):
        bus = Bus()
        recus: list[int] = []

        async def casse(_: Ping) -> None:
            raise RuntimeError("boum")

        bus.abonner(Ping, casse)
        bus.abonner(Ping, lambda e: _noter(recus, e.valeur))
        await bus.publier(Ping(valeur=7))
        assert recus == [7]


async def _noter(cible: list[int], valeur: int) -> None:
    await asyncio.sleep(0)
    cible.append(valeur)


class TestReglages:
    def test_correspondance_de_profil(self):
        r = Reglages(
            profils=[{"utilisateur_ha": "Guillaume", "profil": "guillaume"}],
            profil_par_defaut="guest",
        )
        assert r.profil_pour(nom="Guillaume") == "guillaume"
        assert r.profil_pour(nom="Quelquun") == "guest"

    def test_correspondance_par_identifiant(self):
        r = Reglages(profils=[{"utilisateur_ha": "abc-123", "profil": "clara"}])
        assert r.profil_pour(identifiant="abc-123") == "clara"

    def test_un_profil_par_defaut_nomme_emprunte_une_identite(self):
        """Le réglage qui donne le fil de Guillaume à un inconnu.

        `conversation_courante()` reprend le fil du profil sur douze heures :
        avec `profil_par_defaut: guillaume`, l'iPad du couloir continue la
        conversation de Guillaume et reçoit ses faits. Les deux seules valeurs
        qui n'empruntent l'identité de personne sont `guest` et `unknown`.
        """
        assert not Reglages(profil_par_defaut="guest").defaut_emprunte_une_identite
        assert not Reglages(profil_par_defaut="unknown").defaut_emprunte_une_identite
        for nomme in ("guillaume", "clara", "liam"):
            assert Reglages(profil_par_defaut=nomme).defaut_emprunte_une_identite, nomme

    def test_les_secrets_ne_fuient_pas_dans_les_journaux(self):
        r = Reglages(anthropic_api_key="sk-ant-vrai-secret", relay_secret="hunter2")
        vue = r.secrets_masques()
        assert vue["anthropic_api_key"] == "***"
        assert vue["relay_secret"] == "***"
        assert "hunter2" not in str(vue)
        assert "sk-ant" not in str(vue)
