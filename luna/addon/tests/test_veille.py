"""P4 de bout en bout : capteurs → alertes justes, et habitudes → rappels.

La recette de `docs/P4-HABITUDES-VEILLE.md`, partie E, point par point. Le
point 8 — « un rappel de coucher pertinent, à la bonne heure » — est le seul
que seule la vraie maison peut juger ; tout le reste est ici.

L'horloge est injectée : deux mois de décroissance et une nuit à 3 h se
vérifient sans attendre, et sans rendre les tests intermittents.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import FauxCerveau, veille_avec

from luna.engine.nightly import EntretienNocturne
from luna.engine.observers import ObservateurCoucher, ObservateurSequences
from luna.engine.scheduler import Ordonnanceur
from luna.kernel.facts import confiance
from luna.kernel.schemas import ActionHA, MessageEnregistre
from luna.kernel.settings import RegleVeille

OUVRANT = "binary_sensor.luna_ouvrant_oublie"
AMPOULE = "binary_sensor.luna_lumiere_oubliee"
COUCHER = "binary_sensor.luna_contexte_coucher"


class Horloge:
    """Une horloge qu'on avance à la main."""

    def __init__(self, depart: datetime) -> None:
        self.maintenant = depart

    def __call__(self) -> datetime:
        return self.maintenant

    def avancer(self, **delta) -> datetime:
        self.maintenant += timedelta(**delta)
        return self.maintenant

    def a(self, heure: int, minute: int = 0) -> datetime:
        self.maintenant = self.maintenant.replace(
            hour=heure, minute=minute, second=0, microsecond=0
        )
        return self.maintenant


@pytest.fixture
def horloge() -> Horloge:
    return Horloge(datetime(2026, 9, 8, 20, 0).astimezone())


def regle_ouvrant(**extra) -> RegleVeille:
    return RegleVeille(
        entite=OUVRANT,
        categorie="ouvrant",
        niveau="warning",
        message="Un ouvrant est resté ouvert et il est tard.",
        raison="La baie vitrée du séjour est ouverte.",
        **extra,
    )


async def allumer(veille, maison, entite: str, horloge: Horloge) -> None:
    evenement = maison.poser(entite, "on")
    await veille.sur_changement(evenement.model_copy(update={"ts": horloge.maintenant}))


async def eteindre(veille, maison, entite: str, horloge: Horloge) -> None:
    evenement = maison.poser(entite, "off")
    await veille.sur_changement(evenement.model_copy(update={"ts": horloge.maintenant}))


class TestAlerte:
    async def test_un_capteur_qui_passe_a_on_produit_une_alerte(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Recette 1 : l'alerte apparaît, avec sa justification."""
        veille = veille_avec(
            [regle_ouvrant()], memoire, maison, arbitre, emetteur, horloge
        )
        await allumer(veille, maison, OUVRANT, horloge)

        assert emetteur.genres() == ["alert"]
        alerte = emetteur.evenements[0].alert
        assert alerte.title == "Un ouvrant est resté ouvert et il est tard."
        assert alerte.why == "La baie vitrée du séjour est ouverte."
        assert alerte.entity_id == OUVRANT
        assert veille.alertes() == [alerte]

    async def test_dix_soubresauts_en_une_heure_font_une_alerte(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Recette 2, H57 : c'est la différence entre une veille et un
        harcèlement."""
        veille = veille_avec(
            [regle_ouvrant()], memoire, maison, arbitre, emetteur, horloge
        )
        for _ in range(10):
            await allumer(veille, maison, OUVRANT, horloge)
            horloge.avancer(minutes=3)
            await eteindre(veille, maison, OUVRANT, horloge)
            horloge.avancer(minutes=3)

        assert emetteur.genres().count("alert") == 1

    async def test_apres_quatre_heures_elle_a_le_droit_de_revenir(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        veille = veille_avec(
            [regle_ouvrant()], memoire, maison, arbitre, emetteur, horloge
        )
        await allumer(veille, maison, OUVRANT, horloge)
        await eteindre(veille, maison, OUVRANT, horloge)
        # Douze heures, pas quatre : quatre heures après 20 h tombent en pleine
        # nuit, et c'est alors H58 qui retiendrait l'alerte, pas H57.
        horloge.avancer(hours=12)
        await allumer(veille, maison, OUVRANT, horloge)

        assert emetteur.genres().count("alert") == 2

    async def test_la_condition_qui_cesse_retire_lalerte(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Une veille n'est pas une boîte de réception : ce qui n'a plus lieu
        d'être disparaît sans qu'on ait à le ranger."""
        veille = veille_avec(
            [regle_ouvrant()], memoire, maison, arbitre, emetteur, horloge
        )
        await allumer(veille, maison, OUVRANT, horloge)
        await eteindre(veille, maison, OUVRANT, horloge)

        assert emetteur.genres() == ["alert", "alert_cleared"]
        assert veille.alertes() == []

    async def test_un_ouvrant_deja_ouvert_au_demarrage_est_vu(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Sans l'amorçage, il faudrait le refermer pour qu'il soit signalé —
        c'est-à-dire trop tard."""
        maison.poser(OUVRANT, "on")
        veille = veille_avec(
            [regle_ouvrant()], memoire, maison, arbitre, emetteur, horloge
        )
        await veille.amorcer()

        assert emetteur.genres() == ["alert"]

    async def test_un_capteur_non_declare_nest_pas_regarde(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """§1 : ce qui n'est pas déclaré n'existe pas."""
        veille = veille_avec(
            [regle_ouvrant()], memoire, maison, arbitre, emetteur, horloge
        )
        await allumer(veille, maison, "binary_sensor.autre_chose", horloge)

        assert emetteur.evenements == []


class TestHeuresDeSilence:
    async def test_une_ampoule_oubliee_a_trois_heures_attend_sept_heures(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Recette 3, H58. Elle n'est pas perdue : elle est différée."""
        regle = RegleVeille(
            entite=AMPOULE,
            categorie="eclairage",
            niveau="warning",
            message="Une lumière est restée allumée.",
        )
        veille = veille_avec([regle], memoire, maison, arbitre, emetteur, horloge)

        horloge.a(3, 0)
        await allumer(veille, maison, AMPOULE, horloge)
        assert emetteur.evenements == [], "3 h du matin : Luna se tait"

        horloge.a(6, 55)
        await veille.reexaminer()
        assert emetteur.evenements == []

        horloge.a(7, 5)
        await veille.reexaminer()
        assert emetteur.genres() == ["alert"]

    async def test_une_alerte_differee_dont_la_condition_cesse_ne_sort_jamais(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """C'est tout l'intérêt de différer plutôt que d'émettre en silence :
        si la lampe est éteinte à 6 h, personne n'en entend parler."""
        regle = RegleVeille(entite=AMPOULE, message="Une lumière est restée allumée.")
        veille = veille_avec([regle], memoire, maison, arbitre, emetteur, horloge)

        horloge.a(3, 0)
        await allumer(veille, maison, AMPOULE, horloge)
        horloge.a(6, 0)
        await eteindre(veille, maison, AMPOULE, horloge)
        horloge.a(8, 0)
        await veille.reexaminer()

        assert emetteur.evenements == []

    async def test_une_regle_sans_silence_parle_la_nuit(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Le rappel de coucher, dont c'est tout l'intérêt (H58)."""
        regle = RegleVeille(
            entite=COUCHER, message="Il est l'heure d'aller te coucher.", silence=False
        )
        veille = veille_avec([regle], memoire, maison, arbitre, emetteur, horloge)

        horloge.a(23, 20)
        await allumer(veille, maison, COUCHER, horloge)

        assert emetteur.genres() == ["alert"]


class TestBoucleDeRetour:
    async def test_ne_plus_me_le_dire_fait_taire_la_regle_trente_jours(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Recette 4."""
        veille = veille_avec(
            [regle_ouvrant()], memoire, maison, arbitre, emetteur, horloge
        )
        await allumer(veille, maison, OUVRANT, horloge)
        alerte = emetteur.evenements[0].alert

        resultat = await veille.retour(alerte.id, "muted")
        assert resultat["ok"] is True
        assert emetteur.genres() == ["alert", "alert_cleared"]

        horloge.avancer(days=10)
        await eteindre(veille, maison, OUVRANT, horloge)
        await allumer(veille, maison, OUVRANT, horloge)
        assert emetteur.genres().count("alert") == 1, "toujours en sourdine"

        horloge.avancer(days=25)
        await eteindre(veille, maison, OUVRANT, horloge)
        await allumer(veille, maison, OUVRANT, horloge)
        assert emetteur.genres().count("alert") == 2, "la sourdine a expiré"

    async def test_refusee_encore_et_encore_vaut_une_sourdine(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Recette 5 : le même effet, sans avoir cliqué « ne plus me le dire ».

        Deux refus suffisent depuis le score initial : le second fait passer
        sous le plancher, ce qui vaut sourdine (voir `kernel/veille.py`). La
        troisième soirée est silencieuse — c'est ce que la recette demande.
        """
        # `silence=False` : ce test mesure la boucle de retour, pas les heures
        # de silence. Sans ça, les soirées suivantes tomberaient dans la nuit
        # et c'est H58 qui ferait taire l'alerte — le test passerait pour la
        # mauvaise raison.
        veille = veille_avec(
            [regle_ouvrant(silence=False)], memoire, maison, arbitre, emetteur, horloge
        )
        for _ in range(2):
            await allumer(veille, maison, OUVRANT, horloge)
            alerte = veille.alertes()[0]
            await veille.retour(alerte.id, "rejected")
            await eteindre(veille, maison, OUVRANT, horloge)
            horloge.avancer(hours=5)

        await allumer(veille, maison, OUVRANT, horloge)
        assert emetteur.genres().count("alert") == 2, "la troisième ne sort pas"

        horloge.avancer(days=31)
        await eteindre(veille, maison, OUVRANT, horloge)
        await allumer(veille, maison, OUVRANT, horloge)
        assert emetteur.genres().count("alert") == 3, "trente jours plus tard, à l'essai"

    async def test_le_compteur_porte_sur_la_regle_pas_sur_loccurrence(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """§12 : trois refus **de la règle**, pas trois soirées distinctes.

        Le test vérifie que le score est bien retrouvé sous la même clé d'une
        occurrence à l'autre : sinon chaque alerte repartirait de 0,5 et rien
        ne se muterait jamais.
        """
        veille = veille_avec(
            [regle_ouvrant(silence=False)], memoire, maison, arbitre, emetteur, horloge
        )
        await allumer(veille, maison, OUVRANT, horloge)
        premiere = veille.alertes()[0]
        await veille.retour(premiere.id, "rejected")
        await eteindre(veille, maison, OUVRANT, horloge)

        horloge.avancer(hours=5)
        await allumer(veille, maison, OUVRANT, horloge)
        seconde = veille.alertes()[0]
        assert seconde.id != premiere.id
        assert seconde.key == premiere.key

        score = await memoire.score_suggestion(seconde.key)
        assert score is not None
        assert score.rejections == 1

    async def test_un_retour_sur_une_alerte_inconnue_le_dit(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        veille = veille_avec([], memoire, maison, arbitre, emetteur, horloge)
        from luna.engine.veille import AlerteInconnue

        with pytest.raises(AlerteInconnue):
            await veille.retour("al_inexistante", "muted")


class TestAgir:
    async def test_agir_sur_un_ouvrant_est_refuse_au_niveau_cinq(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Recette 10, D8 : le bouton « Agir » ne court-circuite rien.

        La règle déclare pourtant un `cover.close_cover` — c'est bien la
        déclaration qui est refusée, pas son absence.
        """
        regle = regle_ouvrant(
            actions=[
                ActionHA(
                    domain="cover",
                    service="close_cover",
                    target={"entity_id": "cover.baie_vitree"},
                )
            ]
        )
        veille = veille_avec([regle], memoire, maison, arbitre, emetteur, horloge)
        await allumer(veille, maison, OUVRANT, horloge)
        alerte = veille.alertes()[0]

        resultat = await veille.agir(alerte.id, contexte=_contexte())

        assert resultat["executed"] is False
        assert "ouvrants" in resultat["results"][0]["message"]
        assert maison.appels == [], "aucun service n'a été appelé"

        journal = await memoire.actions()
        assert [(e.action.cle, e.decision) for e in journal] == [
            ("cover.close_cover", "refused_v1")
        ]

    async def test_agir_sur_une_lumiere_passe(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Le pendant du précédent : le niveau 2 s'exécute, par le même chemin."""
        regle = RegleVeille(
            entite=AMPOULE,
            message="Une lumière est restée allumée.",
            actions=[
                ActionHA(
                    domain="light",
                    service="turn_off",
                    target={"entity_id": "light.cuisine"},
                )
            ],
        )
        veille = veille_avec([regle], memoire, maison, arbitre, emetteur, horloge)
        await allumer(veille, maison, AMPOULE, horloge)
        alerte = veille.alertes()[0]

        resultat = await veille.agir(alerte.id, contexte=_contexte())

        assert resultat["executed"] is True
        assert [a.cle for a in maison.appels] == ["light.turn_off"]

    async def test_une_alerte_sans_action_ne_fait_rien(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        veille = veille_avec(
            [regle_ouvrant()], memoire, maison, arbitre, emetteur, horloge
        )
        await allumer(veille, maison, OUVRANT, horloge)
        resultat = await veille.agir(veille.alertes()[0].id, contexte=_contexte())
        assert resultat == {"executed": False, "results": []}


class TestObservateurCoucher:
    async def test_vingt_soirs_donnent_une_habitude_sure(self, memoire, horloge):
        """Recette 6."""
        observateur = ObservateurCoucher(memoire, entite=COUCHER, profil="guillaume")
        for _ in range(20):
            horloge.a(23, 20)
            await observateur.observer(horloge.maintenant)
            horloge.avancer(days=1)

        faits = await memoire.faits(statut="active")
        assert len(faits) == 1, "renforcé, jamais dupliqué (§4)"
        fait = faits[0]
        assert fait.observations == 20
        assert fait.value == "23:20"
        valeur = confiance(
            observations=fait.observations,
            derniere=fait.last_seen_at,
            categorie=fait.category,
            maintenant=fait.last_seen_at,
        )
        assert valeur > 0.7

    async def test_la_valeur_derive_sans_supplanter(self, memoire, horloge):
        """Se coucher à 23 h 18 puis 23 h 24 reste la même habitude."""
        observateur = ObservateurCoucher(memoire, entite=COUCHER, profil="guillaume")
        for minute in (10, 20, 30):
            horloge.a(23, minute)
            await observateur.observer(horloge.maintenant)
            horloge.avancer(days=1)

        faits = await memoire.faits()
        assert len(faits) == 1
        assert faits[0].observations == 3
        assert "23:1" in faits[0].value or "23:2" in faits[0].value

    async def test_un_decalage_franc_supplante_lancienne_habitude(self, memoire, horloge):
        """H62 : `superseded` et une relation, jamais une suppression."""
        observateur = ObservateurCoucher(memoire, entite=COUCHER, profil="guillaume")
        horloge.a(22, 0)
        await observateur.observer(horloge.maintenant)
        horloge.avancer(days=1)
        horloge.a(23, 30)
        await observateur.observer(horloge.maintenant)

        actifs = await memoire.faits(statut="active")
        supplantes = await memoire.faits(statut="superseded")
        assert [f.value for f in actifs] == ["23:30"]
        assert [f.value for f in supplantes] == ["22:00"]

    async def test_minuit_ne_casse_pas_la_moyenne(self, memoire, horloge):
        """23 h 50 et 00 h 10 sont à vingt minutes l'un de l'autre, pas à
        vingt-trois heures quarante."""
        observateur = ObservateurCoucher(memoire, entite=COUCHER, profil="guillaume")
        horloge.a(23, 50)
        await observateur.observer(horloge.maintenant)
        horloge.avancer(days=1)
        horloge.a(0, 10)
        await observateur.observer(horloge.maintenant)

        faits = await memoire.faits(statut="active")
        assert len(faits) == 1, "la même habitude, pas deux"
        assert faits[0].observations == 2

    async def test_un_observateur_sans_entite_ne_tourne_pas(self, memoire, horloge):
        """§1 : Luna n'invente pas ce qu'elle surveille."""
        observateur = ObservateurCoucher(memoire, entite="", profil=None)
        assert not observateur.actif


class TestObservateurSequences:
    async def test_deux_allumages_de_la_meme_piece_font_une_sequence(
        self, memoire, maison, horloge
    ):
        observateur = ObservateurSequences(memoire, actif=True, piece_de=maison.nom_piece)
        premier = maison.poser("light.salon_plafond", "on")
        await observateur.sur_changement(
            premier.model_copy(update={"ts": horloge.maintenant})
        )
        horloge.avancer(seconds=30)
        second = maison.poser("light.salon_lampadaire", "on")
        await observateur.sur_changement(
            second.model_copy(update={"ts": horloge.maintenant})
        )

        faits = await memoire.faits(statut="active")
        assert [f.predicate for f in faits] == ["sequence_recurrente"]

    async def test_deux_pieces_differentes_nen_font_pas_une(
        self, memoire, maison, horloge
    ):
        observateur = ObservateurSequences(memoire, actif=True, piece_de=maison.nom_piece)
        for entite in ("light.salon_plafond", "light.cuisine"):
            evenement = maison.poser(entite, "on")
            await observateur.sur_changement(
                evenement.model_copy(update={"ts": horloge.maintenant})
            )
            horloge.avancer(seconds=10)

        assert await memoire.faits() == []

    async def test_trop_espaces_nen_font_pas_une(self, memoire, maison, horloge):
        observateur = ObservateurSequences(memoire, actif=True, piece_de=maison.nom_piece)
        premier = maison.poser("light.salon_plafond", "on")
        await observateur.sur_changement(
            premier.model_copy(update={"ts": horloge.maintenant})
        )
        horloge.avancer(minutes=10)
        second = maison.poser("light.salon_lampadaire", "on")
        await observateur.sur_changement(
            second.model_copy(update={"ts": horloge.maintenant})
        )

        assert await memoire.faits() == []

    async def test_desactive_par_defaut(self, memoire, maison):
        assert not ObservateurSequences(
            memoire, actif=False, piece_de=maison.nom_piece
        ).actif


class TestEntretienNocturne:
    async def test_un_fait_extrait_arrive_en_relecture(self, memoire, horloge):
        """Recette 9 : il n'entre pas en vigueur tout seul (D2, §4)."""
        cerveau = FauxCerveau()
        cerveau.faits = [
            {
                "predicat": "preference_eclairage",
                "valeur": "couloir tamisé le soir",
                "profil": "guillaume",
                "pourquoi": "Tu me l'as dit le 6 septembre.",
            }
        ]
        await _un_message(memoire, horloge)
        entretien = EntretienNocturne(cerveau=cerveau, memoire=memoire)

        compte = await entretien.passer(horloge.maintenant)

        assert compte["passe"] is True
        assert compte["deposes"] == 1
        assert await memoire.faits(statut="active") == []
        a_relire = await memoire.faits(statut="needs_review")
        assert [f.value for f in a_relire] == ["couloir tamisé le soir"]
        assert a_relire[0].source == "modele"

    async def test_il_saute_quand_il_ny_a_rien_de_neuf(self, memoire, horloge):
        """Recette 11, D3 : un appel qu'on ne fait pas est un appel gratuit."""
        cerveau = FauxCerveau()
        entretien = EntretienNocturne(cerveau=cerveau, memoire=memoire)

        compte = await entretien.passer(horloge.maintenant)

        assert compte == {"passe": False, "motif": "rien de neuf", "evenements": 0}
        assert cerveau.extraits == []

    async def test_il_ne_repasse_pas_sur_ce_quil_a_deja_lu(self, memoire, horloge):
        cerveau = FauxCerveau()
        entretien = EntretienNocturne(cerveau=cerveau, memoire=memoire)
        await _un_message(memoire, horloge)
        await entretien.passer(horloge.maintenant)
        horloge.avancer(days=1)

        compte = await entretien.passer(horloge.maintenant)

        assert compte["passe"] is False
        assert len(cerveau.extraits) == 1

    async def test_il_ne_depasse_jamais_son_plafond(self, memoire, horloge):
        """H56 : sans plafond, la facture grandit avec la maison."""
        cerveau = FauxCerveau()
        for _ in range(30):
            await _un_message(memoire, horloge)
            horloge.avancer(minutes=1)
        entretien = EntretienNocturne(cerveau=cerveau, memoire=memoire, plafond=10)

        compte = await entretien.passer(horloge.maintenant)

        assert compte["evenements"] == 10
        assert len(cerveau.extraits[0].splitlines()) == 10

    async def test_un_predicat_hors_liste_est_jete(self, memoire, horloge):
        """Le dernier filet après l'`enum` du schéma d'outil.

        `heure_de_coucher` se **mesure** ; un modèle qui la déduit d'une phrase
        n'a pas voix au chapitre (D2).
        """
        cerveau = FauxCerveau()
        cerveau.faits = [
            {"predicat": "heure_de_coucher", "valeur": "23:00", "pourquoi": "déduit"},
            {
                "predicat": "fait_declare",
                "valeur": "Clara est allergique",
                "pourquoi": "dit",
            },
        ]
        await _un_message(memoire, horloge)
        entretien = EntretienNocturne(cerveau=cerveau, memoire=memoire)

        await entretien.passer(horloge.maintenant)

        assert [f.predicate for f in await memoire.faits()] == ["fait_declare"]

    async def test_un_cerveau_en_panne_ne_fait_pas_tomber_la_nuit(self, memoire, horloge):
        cerveau = FauxCerveau()
        cerveau.leve = RuntimeError("API injoignable")
        await _un_message(memoire, horloge)
        entretien = EntretienNocturne(cerveau=cerveau, memoire=memoire)

        compte = await entretien.passer(horloge.maintenant)

        assert compte["passe"] is False
        assert compte["motif"] == "cerveau indisponible"

    async def test_desactive_il_ne_fait_rien(self, memoire, horloge):
        cerveau = FauxCerveau()
        await _un_message(memoire, horloge)
        entretien = EntretienNocturne(cerveau=cerveau, memoire=memoire, actif=False)

        assert await entretien.passer(horloge.maintenant) == {
            "passe": False,
            "motif": "désactivé",
        }
        assert cerveau.extraits == []


class TestRelecture:
    async def test_accepter_met_le_fait_en_vigueur(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        veille = veille_avec([], memoire, maison, arbitre, emetteur, horloge)
        fait = await _fait_a_relire(memoire, horloge)

        assert await veille.trancher_fait(fait.id, "accept") == {"status": "active"}
        assert [f.status for f in await memoire.faits()] == ["active"]

    async def test_refuser_le_garde_sans_le_ressortir(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """§4 : jamais supprimé. Et jamais reproposé non plus."""
        veille = veille_avec([], memoire, maison, arbitre, emetteur, horloge)
        fait = await _fait_a_relire(memoire, horloge)

        assert await veille.trancher_fait(fait.id, "reject") == {"status": "rejected"}
        assert await veille.faits_a_relire() == []
        assert len(await memoire.faits()) == 1, "il est toujours en base"

    async def test_un_fait_refuse_nest_pas_repropose_la_nuit_suivante(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Sans ça, l'entretien reposerait la même question toutes les nuits."""
        veille = veille_avec([], memoire, maison, arbitre, emetteur, horloge)
        fait = await _fait_a_relire(memoire, horloge)
        await veille.trancher_fait(fait.id, "reject")

        cerveau = FauxCerveau()
        cerveau.faits = [
            {
                "predicat": fait.predicate,
                "valeur": fait.value,
                "profil": fait.profile,
                "pourquoi": "encore",
            }
        ]
        horloge.avancer(days=1)
        await _un_message(memoire, horloge)
        entretien = EntretienNocturne(cerveau=cerveau, memoire=memoire)
        await entretien.passer(horloge.maintenant)

        assert len(await memoire.faits()) == 1
        assert await veille.faits_a_relire() == []

    async def test_une_relecture_oubliee_expire(self, memoire, horloge):
        """H59 : une file qu'on n'ouvre plus ne protège plus rien."""
        await _fait_a_relire(memoire, horloge)
        horloge.avancer(days=15)

        expires = await memoire.expirer_relectures(
            horloge.maintenant - timedelta(days=14)
        )

        assert expires == 1
        assert await memoire.faits(statut="needs_review") == []

    async def test_une_decision_inconnue_est_refusee(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        from luna.kernel.errors import LunaError

        veille = veille_avec([], memoire, maison, arbitre, emetteur, horloge)
        fait = await _fait_a_relire(memoire, horloge)
        with pytest.raises(LunaError):
            await veille.trancher_fait(fait.id, "peut-être")


class TestJournalImmuable:
    async def test_une_action_journalisee_arrive_aussi_dans_events(
        self, memoire, maison, arbitre, contexte, emetteur
    ):
        """D7 : `action_log` reste, `events` s'ajoute. Rien n'a été migré."""
        await arbitre.agir_hors_conversation(
            ActionHA(domain="climate", service="set_temperature", target={}),
            libelle="Régler le thermostat",
            justification="test",
            contexte=contexte,
            reference="al_1",
            emettre=emetteur,
        )
        # Niveau 3 : une proposition, journalisée à la décision — pas ici.
        await memoire.journaliser(
            (await _journal_bidon(memoire, contexte)),
        )

        evenements = await memoire.evenements_depuis(None, limite=50)
        assert [e.kind for e in evenements] == ["action"]

    async def test_un_message_arrive_dans_events(self, memoire, horloge):
        """C'est la matière de l'entretien nocturne."""
        await _un_message(memoire, horloge)
        evenements = await memoire.evenements_depuis(None, limite=50)
        assert [e.kind for e in evenements] == ["message"]
        assert evenements[0].payload["text"] == "Je me couche tôt ce soir."


class TestOrdonnanceur:
    async def test_demarrer_a_quatorze_heures_ne_declenche_pas_la_nuit(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Le rendez-vous de 3 h 30 déjà passé est tenu pour honoré."""
        horloge.a(14, 0)
        cerveau = FauxCerveau()
        await _un_message(memoire, horloge)
        entretien = EntretienNocturne(cerveau=cerveau, memoire=memoire)
        ordonnanceur = Ordonnanceur(
            veille=veille_avec([], memoire, maison, arbitre, emetteur, horloge),
            entretien=entretien,
            memoire=memoire,
            heure=(3, 30),
            horloge=horloge,
        )

        await ordonnanceur.battre()

        assert cerveau.extraits == []

    async def test_lentretien_part_une_fois_a_lheure_dite(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        horloge.a(2, 0)
        cerveau = FauxCerveau()
        await _un_message(memoire, horloge)
        entretien = EntretienNocturne(cerveau=cerveau, memoire=memoire)
        ordonnanceur = Ordonnanceur(
            veille=veille_avec([], memoire, maison, arbitre, emetteur, horloge),
            entretien=entretien,
            memoire=memoire,
            heure=(3, 30),
            horloge=horloge,
        )

        await ordonnanceur.battre()
        assert cerveau.extraits == [], "il n'est que 2 h"

        horloge.a(3, 35)
        await ordonnanceur.battre()
        assert len(cerveau.extraits) == 1

        horloge.a(4, 0)
        await _un_message(memoire, horloge)
        await ordonnanceur.battre()
        assert len(cerveau.extraits) == 1, "une seule fois par nuit"

    async def test_le_battement_reexamine_les_alertes_differees(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        regle = RegleVeille(entite=AMPOULE, message="Une lumière est restée allumée.")
        veille = veille_avec([regle], memoire, maison, arbitre, emetteur, horloge)
        ordonnanceur = Ordonnanceur(
            veille=veille,
            entretien=EntretienNocturne(cerveau=FauxCerveau(), memoire=memoire),
            memoire=memoire,
            heure=(3, 30),
            horloge=horloge,
        )

        horloge.a(3, 0)
        await allumer(veille, maison, AMPOULE, horloge)
        assert emetteur.evenements == []

        horloge.a(8, 0)
        await ordonnanceur.battre()
        assert emetteur.genres() == ["alert"]


class TestRappelDeCoucher:
    """§11 : « un rappel de coucher pertinent ». Le mot qui compte est le
    dernier."""

    def _regle(self) -> RegleVeille:
        return RegleVeille(
            entite=COUCHER,
            categorie="coucher",
            niveau="info",
            message="Il est l'heure d'aller te coucher.",
            raison="Tu te couches plutôt vers {valeur}, il est {heure}.",
            fait="heure_de_coucher",
            silence=False,
        )

    async def test_sans_habitude_observee_luna_ne_dit_rien(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Le contraire d'un rappel pertinent, c'est un rappel générique."""
        veille = veille_avec([self._regle()], memoire, maison, arbitre, emetteur, horloge)
        horloge.a(23, 35)
        await allumer(veille, maison, COUCHER, horloge)

        assert emetteur.evenements == []

    async def test_une_seule_soiree_ne_suffit_pas(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        observateur = ObservateurCoucher(memoire, entite=COUCHER, profil=None)
        horloge.a(23, 20)
        await observateur.observer(horloge.maintenant)

        veille = veille_avec([self._regle()], memoire, maison, arbitre, emetteur, horloge)
        horloge.avancer(days=1)
        horloge.a(23, 35)
        await allumer(veille, maison, COUCHER, horloge)

        assert emetteur.evenements == []

    async def test_avec_une_habitude_sure_le_rappel_cite_lheure(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        observateur = ObservateurCoucher(memoire, entite=COUCHER, profil=None)
        for _ in range(5):
            horloge.a(23, 20)
            await observateur.observer(horloge.maintenant)
            horloge.avancer(days=1)

        veille = veille_avec([self._regle()], memoire, maison, arbitre, emetteur, horloge)
        horloge.a(23, 35)
        await allumer(veille, maison, COUCHER, horloge)

        assert emetteur.genres() == ["alert"]
        alerte = emetteur.evenements[0].alert
        assert alerte.why == "Tu te couches plutôt vers 23:20, il est 23:35."

    async def test_une_habitude_perimee_fait_taire_le_rappel(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Recette 7, vue de l'autre bout : le fait existe encore, il ne sert
        simplement plus à rien."""
        observateur = ObservateurCoucher(memoire, entite=COUCHER, profil=None)
        for _ in range(5):
            horloge.a(23, 20)
            await observateur.observer(horloge.maintenant)
            horloge.avancer(days=1)

        veille = veille_avec([self._regle()], memoire, maison, arbitre, emetteur, horloge)
        horloge.avancer(days=120)
        horloge.a(23, 35)
        await allumer(veille, maison, COUCHER, horloge)

        assert emetteur.evenements == []
        assert len(await memoire.faits(statut="active")) == 1, "§4 : jamais supprimé"


class TestPatterns:
    async def test_la_confiance_est_recalculee_a_la_lecture(
        self, memoire, maison, arbitre, emetteur, horloge
    ):
        """Jamais celle qu'on avait mise en base : elle serait toujours fausse."""
        observateur = ObservateurCoucher(memoire, entite=COUCHER, profil="guillaume")
        for _ in range(10):
            horloge.a(23, 20)
            await observateur.observer(horloge.maintenant)
            horloge.avancer(days=1)

        veille = veille_avec([], memoire, maison, arbitre, emetteur, horloge)
        tot = (await veille.patterns("guillaume"))[0]["confidence"]

        horloge.avancer(days=60)
        tard = (await veille.patterns("guillaume"))[0]["confidence"]

        assert tard < tot / 2


# ── Aides ────────────────────────────────────────────────────────────────


def _contexte():
    from luna.kernel.schemas import ContexteRequete

    return ContexteRequete(
        ha_user_id="u-1",
        ha_user_name="Guillaume",
        is_admin=True,
        profile="guillaume",
        client_id="tests",
        local=True,
    )


async def _un_message(memoire, horloge) -> None:
    conversation = await memoire.conversation_courante("guillaume")
    await memoire.ajouter_message(
        MessageEnregistre(
            id=f"m_{horloge.maintenant.timestamp()}",
            conversation_id=conversation,
            role="user",
            text="Je me couche tôt ce soir.",
            ts=horloge.maintenant,
            profile="guillaume",
        )
    )


async def _fait_a_relire(memoire, horloge):
    from luna.kernel.ids import nouvel_id
    from luna.kernel.schemas import Fait

    fait = Fait(
        id=nouvel_id("f"),
        predicate="preference_eclairage",
        value="couloir tamisé le soir",
        profile="guillaume",
        category="preference_eclairage",
        status="needs_review",
        source="modele",
        created_at=horloge.maintenant,
        last_seen_at=horloge.maintenant,
        why="Tu me l'as dit.",
    )
    depose, _ = await memoire.observer_fait(fait)
    return depose


async def _journal_bidon(memoire, contexte):
    from luna.kernel.autonomy import Niveau
    from luna.kernel.ids import nouvel_id
    from luna.kernel.schemas import EntreeJournal

    return EntreeJournal(
        id=nouvel_id("a"),
        ts=datetime.now().astimezone(),
        profile=contexte.profile,
        ha_user_id=contexte.ha_user_id,
        level=Niveau.PERSISTANT,
        action=ActionHA(domain="climate", service="set_temperature", target={}),
        justification="test",
        decision="accepted",
        executed=True,
    )
