"""L'action a-t-elle vraiment eu lieu ? (§8, « jamais d'échec silencieux »)

Home Assistant accepte `light.turn_on` sur une ampoule retirée de sa douille :
il ne lève rien, et rien ne s'allume. Luna annonçait « Fait » sur cette seule
acceptation. Ces tests couvrent les trois issues qu'elle sait maintenant
distinguer.
"""

from __future__ import annotations

import pytest

from luna.engine.arbiter import Arbitre
from luna.kernel.schemas import EvtOutil
from luna.kernel.verification import (
    Verdict,
    attendu_de,
    est_une_bascule,
    juger,
    verifiable,
)


@pytest.fixture
def emis():
    evenements: list[object] = []

    async def emettre(evenement):
        evenements.append(evenement)

    emettre.evenements = evenements  # type: ignore[attr-defined]
    return emettre


def _statuts(emis) -> list[str]:
    return [e.status for e in emis.evenements if isinstance(e, EvtOutil)]


async def _proposer(arbitre, maison, contexte, emis):
    """Fabrique une proposition de niveau 3 portant sur une lampe sourde.

    Le registre range `input_boolean` en niveau 3 ; on emprunte son service pour
    viser une entité dont on sait qu'elle n'obéira pas.
    """
    from datetime import datetime, timedelta

    from luna.kernel.ids import nouvel_id
    from luna.kernel.schemas import ActionHA, Proposition, PropositionEnAttente

    acte = ActionHA(
        domain="light",
        service="turn_on",
        target={"entity_id": ["light.salon_plafond"]},
    )
    proposition = Proposition(
        id=nouvel_id("p"),
        level=3,
        title="Allumer le plafond du salon",
        why="test",
        actions=[acte],
        expires_at=datetime.now().astimezone() + timedelta(minutes=5),
    )
    arbitre._en_attente[proposition.id] = PropositionEnAttente(
        proposition=proposition, contexte=contexte, message_id="m1"
    )
    return proposition


class TestRegistre:
    def test_les_services_a_etat_fixe_sont_verifiables(self):
        assert attendu_de("light", "turn_on") == "on"
        assert attendu_de("switch", "turn_off") == "off"
        assert verifiable("light", "turn_on")

    def test_une_bascule_ne_vise_aucun_etat_mais_reste_verifiable(self):
        assert attendu_de("light", "toggle") is None
        assert est_une_bascule("light", "toggle")
        assert verifiable("light", "toggle")

    def test_ce_qui_ne_se_lit_pas_dans_l_etat_n_est_pas_verifiable(self):
        """Une scène reste à un horodatage, une consigne ne vit pas dans `state`.

        Les déclarer vérifiables ferait échouer des actions réussies.
        """
        assert not verifiable("scene", "turn_on")
        assert not verifiable("climate", "set_temperature")


class TestJugement:
    def test_tout_a_obei(self):
        verdict, detail = juger(
            attendu="on", avant={}, apres={"light.a": "on", "light.b": "on"}
        )
        assert verdict is Verdict.FAIT
        assert detail == ""

    def test_rien_n_a_bouge(self):
        verdict, detail = juger(attendu="on", avant={}, apres={"light.a": "off"})
        assert verdict is Verdict.SANS_EFFET
        assert "light.a n'a pas changé d'état" in detail

    def test_une_seule_sur_trois_a_desobei(self):
        verdict, detail = juger(
            attendu="on",
            avant={},
            apres={"light.a": "on", "light.b": "on", "light.c": "off"},
        )
        assert verdict is Verdict.SANS_EFFET
        assert "light.c" in detail

    def test_toutes_muettes(self):
        verdict, detail = juger(
            attendu="on", avant={}, apres={"light.a": "unavailable"}
        )
        assert verdict is Verdict.INJOIGNABLE
        assert "ne répond plus" in detail

    def test_unknown_vaut_unavailable(self):
        """Les deux disent la même chose : Luna ne peut pas confirmer."""
        verdict, _ = juger(attendu="on", avant={}, apres={"light.a": "unknown"})
        assert verdict is Verdict.INJOIGNABLE

    def test_une_muette_parmi_des_obeissantes_reste_un_echec(self):
        verdict, detail = juger(
            attendu="on", avant={}, apres={"light.a": "on", "light.b": "unavailable"}
        )
        assert verdict is Verdict.SANS_EFFET
        assert "light.b ne répond plus" in detail

    def test_une_bascule_se_juge_par_comparaison(self):
        verdict, _ = juger(
            attendu=None, avant={"light.a": "off"}, apres={"light.a": "on"}
        )
        assert verdict is Verdict.FAIT

        verdict, detail = juger(
            attendu=None, avant={"light.a": "off"}, apres={"light.a": "off"}
        )
        assert verdict is Verdict.SANS_EFFET
        assert "light.a" in detail

    def test_aucune_reponse_du_tout(self):
        verdict, _ = juger(attendu="on", avant={}, apres={})
        assert verdict is Verdict.INJOIGNABLE

    def test_au_dela_de_deux_on_compte_au_lieu_de_citer(self):
        """§7 : une liste de huit identifiants est inaudible à voix haute."""
        _, detail = juger(
            attendu="on",
            avant={},
            apres={f"light.l{n}": "off" for n in range(8)},
        )
        assert "8 entités" in detail
        assert "light.l0" not in detail


class TestArbitre:
    async def test_une_lampe_qui_obeit_donne_fait(self, arbitre, contexte, emis):
        resultat = await arbitre.executer(
            "commander_lumiere",
            {"cible": "light.cuisine", "action": "eteindre"},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert not resultat.erreur
        assert resultat.contenu.startswith("Fait :")
        assert _statuts(emis) == ["running", "done"]

    async def test_une_lampe_sourde_ne_donne_plus_fait(
        self, arbitre, maison, contexte, emis
    ):
        """L'ampoule retirée de sa douille : l'appel passe, rien ne s'allume."""
        maison.sourdes.add("light.salon_plafond")
        resultat = await arbitre.executer(
            "commander_lumiere",
            {"cible": "light.salon_plafond", "action": "allumer"},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert resultat.erreur
        assert "Fait" not in resultat.contenu
        assert "n'a pas changé d'état" in resultat.contenu
        assert _statuts(emis) == ["running", "error"]

    async def test_une_lampe_muette_se_distingue_d_une_lampe_sourde(
        self, arbitre, maison, contexte, emis
    ):
        """Les deux échouent, mais pas pour la même raison, donc pas du même mot."""
        maison.muettes.add("light.salon_plafond")
        resultat = await arbitre.executer(
            "commander_lumiere",
            {"cible": "light.salon_plafond", "action": "allumer"},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert resultat.erreur
        assert "ne répond plus" in resultat.contenu
        assert "n'a pas changé d'état" not in resultat.contenu

    async def test_une_proposition_validee_est_verifiee_elle_aussi(
        self, arbitre, maison, memoire, contexte, emis
    ):
        """Le second point d'appel avait le même défaut, et il porte plus gros.

        Une action de niveau 3 passe par une proposition, donc par un humain qui
        a dit oui. Lui répondre « c'est fait » quand rien n'a bougé est pire que
        pour une lumière : c'est le journal de §9.1 qui devient faux.
        """
        maison.sourdes.add("light.salon_plafond")
        proposition = await _proposer(arbitre, maison, contexte, emis)
        rendu = await arbitre.decider(proposition.id, "accept", contexte=contexte)

        assert rendu["executed"] is False
        assert rendu["results"][0]["ok"] is False
        assert "n'a pas changé d'état" in rendu["results"][0]["error"]

        lignes = await memoire.actions(limite=10)
        assert lignes, "une action de niveau 3 est toujours journalisée (§9.1)"
        assert lignes[0].executed is False
        assert lignes[0].error

    async def test_une_scene_reste_fait_sans_verification(
        self, arbitre, contexte, emis
    ):
        """Rien à relire : l'état d'une scène ne dit pas si elle s'est jouée."""
        resultat = await arbitre.executer(
            "activer_scene",
            {"nom": "Cinéma"},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert not resultat.erreur
        assert resultat.contenu.startswith("Fait :")

    async def test_a_zero_la_verification_est_coupee(
        self, maison, memoire, contexte, emis
    ):
        """Le réglage rend son ancien comportement à Luna, sans toucher au code."""
        arbitre = Arbitre(maison, memoire, delai_verification=0)
        maison.sourdes.add("light.salon_plafond")
        resultat = await arbitre.executer(
            "commander_lumiere",
            {"cible": "light.salon_plafond", "action": "allumer"},
            contexte=contexte,
            message_id="m1",
            emettre=emis,
        )
        assert not resultat.erreur
        assert resultat.contenu.startswith("Fait :")
