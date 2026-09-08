"""L2 — l'arbitre. C'est ici que §9 est vraiment vérifiée.

Chaque test correspond à une ligne du tableau de comportement de
docs/P1-CONTRATS.md §7.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from luna.kernel.errors import NonAutorise, PropositionExpiree, PropositionInconnue
from luna.kernel.schemas import ContexteRequete, EvtOutil, EvtProposition


@pytest.fixture
def emis():
    evenements: list[object] = []

    async def emettre(evenement):
        evenements.append(evenement)

    emettre.evenements = evenements  # type: ignore[attr-defined]
    return emettre


def _outils(emis) -> list[EvtOutil]:
    return [e for e in emis.evenements if isinstance(e, EvtOutil)]


def _propositions(emis) -> list[EvtProposition]:
    return [e for e in emis.evenements if isinstance(e, EvtProposition)]


class TestLecture:
    async def test_lister_pieces(self, arbitre, contexte, emis):
        resultat = await arbitre.executer(
            "lister_pieces", {}, contexte=contexte, message_id="m1", emettre=emis
        )
        assert not resultat.erreur
        assert "Séjour" in resultat.contenu
        assert [o.status for o in _outils(emis)] == ["running", "done"]
        assert all(o.level == 1 for o in _outils(emis))

    async def test_etat_maison_filtre_par_piece(self, arbitre, contexte, emis):
        resultat = await arbitre.executer(
            "etat_maison",
            {"piece": "Séjour", "domaine": "light"},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert "light.salon_plafond" in resultat.contenu
        assert "light.cuisine" not in resultat.contenu

    async def test_les_ouvrants_restent_lisibles(self, arbitre, contexte, emis):
        """Décision A2 : lecture oui, action non. F5 en dépend."""
        resultat = await arbitre.executer(
            "etat_maison",
            {"piece": None, "domaine": "cover"},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert "cover.baie_vitree" in resultat.contenu
        assert "open" in resultat.contenu


class TestNiveauDeux:
    async def test_execution_directe(self, arbitre, maison, contexte, emis):
        resultat = await arbitre.executer(
            "commander_lumiere",
            {"cible": "Séjour", "action": "allumer", "luminosite": None},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert not resultat.erreur
        assert len(maison.appels) == 1
        appel = maison.appels[0]
        assert appel.cle == "light.turn_on"
        assert appel.target["entity_id"] == [
            "light.salon_lampadaire",
            "light.salon_plafond",
        ]
        assert _propositions(emis) == []

    async def test_le_niveau_2_nest_pas_journalise(
        self, arbitre, memoire, contexte, emis
    ):
        await arbitre.executer(
            "commander_lumiere",
            {"cible": "Cuisine", "action": "eteindre", "luminosite": None},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert await memoire.actions() == []

    async def test_luminosite_bornee(self, arbitre, maison, contexte, emis):
        await arbitre.executer(
            "commander_lumiere",
            {"cible": "Cuisine", "action": "allumer", "luminosite": 500},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert maison.appels[0].data == {"brightness_pct": 100}

    async def test_cible_introuvable(self, arbitre, maison, contexte, emis):
        resultat = await arbitre.executer(
            "commander_lumiere",
            {"cible": "la véranda", "action": "allumer", "luminosite": None},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert resultat.erreur
        assert "véranda" in resultat.contenu
        assert maison.appels == []

    async def test_profil_sans_scope_produit_une_proposition(
        self, arbitre, maison, contexte, emis
    ):
        """§9, niveau 2 : « Libre si le profil actif l'autorise, sinon proposition »."""
        inconnu = contexte.model_copy(update={"profile": "unknown"})
        await arbitre.executer(
            "commander_lumiere",
            {"cible": "Cuisine", "action": "allumer", "luminosite": None},
            contexte=inconnu,
            message_id="m1",
            emettre=emis,
        )
        assert maison.appels == []
        assert len(_propositions(emis)) == 1

    async def test_panne_de_home_assistant(self, arbitre, maison, contexte, emis):
        maison.echoue = True
        resultat = await arbitre.executer(
            "commander_lumiere",
            {"cible": "Cuisine", "action": "allumer", "luminosite": None},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert resultat.erreur
        assert _outils(emis)[-1].status == "error"


class TestNiveauTrois:
    async def test_toujours_une_proposition(self, arbitre, maison, contexte, emis):
        resultat = await arbitre.executer(
            "regler_thermostat",
            {"cible": "Séjour", "temperature": 20, "raison": "Il fait 17,5 °C."},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert not resultat.erreur
        assert maison.appels == [], "rien ne doit bouger avant validation"
        propositions = _propositions(emis)
        assert len(propositions) == 1
        proposition = propositions[0].proposal
        assert proposition.level == 3
        assert proposition.why == "Il fait 17,5 °C."
        assert proposition.actions[0].cle == "climate.set_temperature"

    async def test_acceptation_execute_et_journalise(
        self, arbitre, maison, memoire, contexte, emis
    ):
        await arbitre.executer(
            "regler_thermostat",
            {"cible": "Séjour", "temperature": 20, "raison": "Il fait frais."},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        identifiant = _propositions(emis)[0].proposal.id

        resultat = await arbitre.decider(identifiant, "accept", contexte=contexte)
        assert resultat["executed"] is True
        assert maison.appels[0].data == {"temperature": 20.0}

        journal = await memoire.actions()
        assert len(journal) == 1
        assert journal[0].decision == "accepted"
        assert journal[0].executed is True
        assert journal[0].justification == "Il fait frais."

    async def test_refus_journalise_aussi(self, arbitre, maison, memoire, contexte, emis):
        """§9.1 : « qu'elle soit acceptée ou refusée »."""
        await arbitre.executer(
            "regler_thermostat",
            {"cible": "Séjour", "temperature": 22, "raison": "Test."},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        identifiant = _propositions(emis)[0].proposal.id

        resultat = await arbitre.decider(identifiant, "reject", contexte=contexte)
        assert resultat["executed"] is False
        assert maison.appels == []

        journal = await memoire.actions()
        assert len(journal) == 1
        assert journal[0].decision == "rejected"
        assert journal[0].executed is False

    async def test_proposition_expiree(self, arbitre, contexte, emis):
        await arbitre.executer(
            "regler_thermostat",
            {"cible": "Séjour", "temperature": 20, "raison": "Test."},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        proposition = _propositions(emis)[0].proposal
        en_attente = arbitre._en_attente[proposition.id]
        en_attente.proposition = proposition.model_copy(
            update={"expires_at": datetime.now().astimezone() - timedelta(seconds=1)}
        )
        with pytest.raises(PropositionExpiree):
            await arbitre.decider(proposition.id, "accept", contexte=contexte)

    async def test_proposition_inconnue(self, arbitre, contexte):
        with pytest.raises(PropositionInconnue):
            await arbitre.decider("p_inexistante", "accept", contexte=contexte)

    async def test_une_proposition_ne_sert_quune_fois(self, arbitre, contexte, emis):
        await arbitre.executer(
            "regler_thermostat",
            {"cible": "Séjour", "temperature": 20, "raison": "Test."},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        identifiant = _propositions(emis)[0].proposal.id
        await arbitre.decider(identifiant, "accept", contexte=contexte)
        with pytest.raises(PropositionInconnue):
            await arbitre.decider(identifiant, "accept", contexte=contexte)

    async def test_purge_des_expirees(self, arbitre, contexte, emis):
        await arbitre.executer(
            "regler_thermostat",
            {"cible": "Séjour", "temperature": 20, "raison": "Test."},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        proposition = _propositions(emis)[0].proposal
        assert len(arbitre.propositions_en_attente()) == 1
        arbitre._en_attente[proposition.id].proposition = proposition.model_copy(
            update={"expires_at": datetime.now().astimezone() - timedelta(seconds=1)}
        )
        assert arbitre.propositions_en_attente() == []
        assert arbitre.purger() == 1
        assert arbitre.purger() == 0


class TestNiveauCinq:
    async def test_les_ouvrants_sont_refuses(self, arbitre, maison, contexte, emis):
        """Le refus est structurel : ce chemin n'est même pas atteignable via
        les outils déclarés, mais l'arbitre le refuse quand même (deuxième
        barrière)."""
        from luna.kernel.schemas import ActionHA

        resultat = await arbitre._appliquer(
            ActionHA(
                domain="cover",
                service="close_cover",
                target={"entity_id": ["cover.baie_vitree"]},
            ),
            libelle="Fermer la baie vitrée",
            nom_outil="commander_ouvrant",
            justification="Demande explicite de guillaume.",
            message_id="m1",
            contexte=contexte,
            emettre=emis,
        )
        assert resultat.erreur
        assert "ouvrants" in resultat.contenu
        assert "Loggia" in resultat.contenu
        assert maison.appels == []

    async def test_le_refus_est_journalise(self, arbitre, memoire, contexte, emis):
        from luna.kernel.schemas import ActionHA

        await arbitre._appliquer(
            ActionHA(domain="alarm_control_panel", service="alarm_disarm"),
            libelle="Désarmer l'alarme",
            nom_outil="x",
            justification="Demande explicite.",
            message_id="m1",
            contexte=contexte,
            emettre=emis,
        )
        journal = await memoire.actions()
        assert journal[0].decision == "refused_v1"
        assert journal[0].executed is False


class TestNiveauQuatre:
    async def test_un_non_administrateur_ne_peut_pas_valider(
        self, arbitre, contexte, emis
    ):
        from luna.kernel.schemas import ActionHA

        await arbitre._appliquer(
            ActionHA(domain="automation", service="reload"),
            libelle="Recharger les automatisations",
            nom_outil="x",
            justification="Test.",
            message_id="m1",
            contexte=contexte,
            emettre=emis,
        )
        proposition = _propositions(emis)[0].proposal
        assert proposition.level == 4

        simple = ContexteRequete(profile="clara", is_admin=False)
        with pytest.raises(NonAutorise):
            await arbitre.decider(proposition.id, "accept", contexte=simple)

        # L'administrateur, lui, peut.
        assert (await arbitre.decider(proposition.id, "accept", contexte=contexte))[
            "executed"
        ]


class TestNiveauInconnu:
    async def test_un_service_absent_du_registre_devient_une_proposition(
        self, arbitre, maison, contexte, emis
    ):
        """§9.1 : le défaut est 3, donc proposition. Un oubli échoue du bon côté."""
        from luna.kernel.schemas import ActionHA

        await arbitre._appliquer(
            ActionHA(domain="un_domaine_de_demain", service="faire_un_truc"),
            libelle="Faire un truc",
            nom_outil="x",
            justification="Test.",
            message_id="m1",
            contexte=contexte,
            emettre=emis,
        )
        assert maison.appels == []
        assert _propositions(emis)[0].proposal.level == 3
