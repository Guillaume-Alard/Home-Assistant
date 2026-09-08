"""L0 — la confiance d'un fait et les règles de la veille, au jour près.

Aucune base, aucune maison, aucune horloge réelle : ces deux modules sont
purs, et c'est précisément ce qui permet de vérifier une décroissance sur deux
mois en quelques microsecondes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from luna.kernel.facts import (
    SEUIL_PROPOSITION,
    SEUIL_UTILE,
    assez_sur_pour_proposer,
    confiance,
    demi_vie,
    encore_utile,
    fraicheur,
    maturite,
)
from luna.kernel.veille import (
    Score,
    appliquer_retour,
    cle_suggestion,
    dans_les_heures_de_silence,
    peut_parler,
    peut_repeter,
)

MINUIT = datetime(2026, 9, 8, 0, 0).astimezone()


def a(heure: int, minute: int = 0) -> datetime:
    return MINUIT.replace(hour=heure, minute=minute)


class TestMaturite:
    @pytest.mark.parametrize(
        ("observations", "attendu"),
        [(0, 0.0), (1, 0.29), (2, 0.50), (3, 0.65), (5, 0.82), (10, 0.97)],
    )
    def test_le_tableau_de_d6(self, observations, attendu):
        """Les chiffres du cadrage, vérifiés — pas seulement écrits."""
        assert maturite(observations) == pytest.approx(attendu, abs=0.01)

    def test_une_observation_negative_ne_vaut_rien(self):
        assert maturite(-3) == 0.0

    def test_elle_croit_et_reste_bornee(self):
        """Croissante et plafonnée à 1.

        Mathématiquement elle n'atteint jamais 1 ; en flottants, `0,5 ** 500`
        s'annule et elle y arrive. Ça ne change rien : une habitude vue mille
        fois est une certitude, et c'est la fraîcheur qui la fera redescendre.
        """
        assert maturite(4) > maturite(3)
        assert maturite(1000) <= 1.0


class TestFraicheur:
    def test_une_demi_vie_divise_par_deux(self):
        assert fraicheur(30, "heure_de_coucher") == pytest.approx(0.5)
        assert fraicheur(60, "heure_de_coucher") == pytest.approx(0.25)

    def test_une_preference_dure_plus_quune_habitude(self):
        """§4 : « une habitude de coucher se périme plus vite qu'une préférence
        de température. » C'est le test de cette phrase."""
        assert demi_vie("preference_temperature") > demi_vie("heure_de_coucher")
        jours = 60
        assert fraicheur(jours, "preference_temperature") > fraicheur(
            jours, "heure_de_coucher"
        )

    def test_une_categorie_inconnue_se_perime_le_plus_vite(self):
        """Comme le niveau 3 par défaut de l'autonomie : l'inconnu échoue du
        côté sûr."""
        assert demi_vie("catégorie_qui_nexiste_pas") == min(
            demi_vie(c)
            for c in (
                "heure_de_coucher",
                "sequence_recurrente",
                "preference_eclairage",
                "preference_temperature",
                "fait_declare",
            )
        )

    def test_le_futur_ne_vaut_pas_mieux_que_maintenant(self):
        """Une horloge qui recule ne doit pas rendre un fait plus sûr que neuf."""
        assert fraicheur(-10, "heure_de_coucher") == 1.0


class TestRecette:
    """Les points 6 et 7 de la recette de P4."""

    def test_vingt_soirs_de_coucher_donnent_plus_de_zero_sept(self):
        valeur = confiance(
            observations=20,
            derniere=MINUIT - timedelta(days=1),
            categorie="heure_de_coucher",
            maintenant=MINUIT,
        )
        assert valeur > 0.7

    def test_deux_mois_sans_coucher_font_retomber_sous_le_seuil(self):
        valeur = confiance(
            observations=20,
            derniere=MINUIT - timedelta(days=60),
            categorie="heure_de_coucher",
            maintenant=MINUIT,
        )
        assert valeur < SEUIL_UTILE
        assert not encore_utile(valeur)
        assert valeur > 0.0, "le fait n'a pas disparu — il n'est plus assez sûr"

    def test_une_seule_observation_ne_suffit_pas_a_proposer(self):
        """Une soirée décrit une soirée, pas quelqu'un.

        0,29 passe le seuil d'oubli mais pas celui de la proposition : c'est
        toute la raison d'avoir deux seuils.
        """
        une = confiance(
            observations=1,
            derniere=MINUIT,
            categorie="heure_de_coucher",
            maintenant=MINUIT,
        )
        assert encore_utile(une)
        assert not assez_sur_pour_proposer(une)
        assert SEUIL_PROPOSITION > SEUIL_UTILE

    def test_trois_soirs_suffisent_a_proposer(self):
        trois = confiance(
            observations=3,
            derniere=MINUIT,
            categorie="heure_de_coucher",
            maintenant=MINUIT,
        )
        assert assez_sur_pour_proposer(trois)


class TestHeuresDeSilence:
    @pytest.mark.parametrize("heure", [22, 23, 0, 3, 6])
    def test_la_nuit_luna_se_tait(self, heure):
        quand = a(heure, 45 if heure == 22 else 0)
        assert dans_les_heures_de_silence(quand)

    @pytest.mark.parametrize(("heure", "minute"), [(7, 0), (12, 0), (19, 0), (22, 29)])
    def test_la_journee_elle_parle(self, heure, minute):
        assert not dans_les_heures_de_silence(a(heure, minute))

    def test_une_ampoule_oubliee_a_trois_heures_attend(self):
        """Recette 3 : rien avant 7 h."""
        assert not peut_parler("warning", a(3))
        assert peut_parler("warning", a(7))

    def test_le_rappel_de_coucher_a_le_droit_de_parler_a_vingt_trois_heures(self):
        """H58 : c'est tout son intérêt."""
        assert not peut_parler("warning", a(23))
        assert peut_parler("warning", a(23), silence=False)

    def test_une_alerte_critique_traverse_toujours(self):
        assert peut_parler("critical", a(3))


class TestRepetition:
    def test_une_premiere_alerte_passe(self):
        assert peut_repeter(None, a(20))

    def test_le_meme_capteur_ne_reparle_pas_avant_quatre_heures(self):
        """Recette 2 : dix soubresauts en une heure font une alerte."""
        depart = a(20)
        assert not peut_repeter(depart, depart + timedelta(minutes=6))
        assert not peut_repeter(depart, depart + timedelta(hours=3, minutes=59))
        assert peut_repeter(depart, depart + timedelta(hours=4))


class TestBoucleDeRetour:
    """§12, C.2 — écrit dans le cadrage, vérifié ici."""

    def test_le_depart(self):
        assert Score().score == 0.5
        assert Score().remonte(MINUIT)

    def test_accepter_renforce_et_remet_les_refus_a_zero(self):
        score = appliquer_retour(Score(score=0.5, refus=2), "accepted", MINUIT)
        assert score.score == pytest.approx(0.65)
        assert score.refus == 0

    def test_refuser_affaiblit(self):
        score = appliquer_retour(Score(), "rejected", MINUIT)
        assert score.score == pytest.approx(0.25)
        assert score.refus == 1

    def test_ne_plus_me_le_dire_met_en_sourdine_trente_jours(self):
        """Recette 4."""
        score = appliquer_retour(Score(), "muted", MINUIT)
        assert score.sourdine_jusqua == MINUIT + timedelta(days=30)
        assert not score.remonte(MINUIT)
        assert score.remonte(MINUIT + timedelta(days=31))

    def test_trois_refus_valent_un_ne_plus_me_le_dire(self):
        """Recette 5 : sans avoir cliqué « ne plus ».

        Depuis un score haut — une règle qu'on a souvent acceptée — c'est bien
        le compteur de refus qui déclenche, au troisième.
        """
        score = Score(score=0.95)
        for attendu in (0.70, 0.45):
            score = appliquer_retour(score, "rejected", MINUIT)
            assert score.score == pytest.approx(attendu)
            assert score.sourdine_jusqua is None
        score = appliquer_retour(score, "rejected", MINUIT)
        assert score.sourdine_jusqua == MINUIT + timedelta(days=30)
        assert score.refus == 0, "le compteur repart après la sourdine"
        assert not score.remonte(MINUIT)

    def test_un_refus_qui_creve_le_plancher_met_aussi_en_sourdine(self):
        """La contradiction de §12, résolue.

        Depuis 0,5, deux refus amènent à 0,0 — sous le plancher de 0,2. La
        règle cesserait d'apparaître, donc plus personne ne pourrait jamais
        l'accepter, donc le score ne remonterait jamais : une mort définitive,
        que ni le plancher ni la règle des trois refus ne demandaient. Elle
        part donc en sourdine trente jours, et revient à l'essai.
        """
        score = appliquer_retour(Score(), "rejected", MINUIT)
        assert score.sourdine_jusqua is None
        score = appliquer_retour(score, "rejected", MINUIT)

        assert score.sourdine_jusqua == MINUIT + timedelta(days=30)
        assert not score.remonte(MINUIT)
        assert score.remonte(MINUIT + timedelta(days=31)), "elle a une seconde chance"
        assert score.score == pytest.approx(0.2), "à l'essai, pas réhabilitée"

    def test_deux_refus_espaces_par_une_acceptation_ne_mutent_pas(self):
        """Le compteur vise trois refus **consécutifs**.

        Depuis un score haut, pour que ce soit bien le compteur qu'on observe
        et non le plancher.
        """
        score = Score(score=0.95)
        score = appliquer_retour(score, "rejected", MINUIT)
        score = appliquer_retour(score, "rejected", MINUIT)
        score = appliquer_retour(score, "accepted", MINUIT)
        score = appliquer_retour(score, "rejected", MINUIT)
        assert score.sourdine_jusqua is None

    def test_un_score_trop_bas_ne_remonte_plus(self):
        assert not Score(score=0.19).remonte(MINUIT)
        assert Score(score=0.2).remonte(MINUIT)

    def test_le_score_reste_borne(self):
        haut = Score(score=1.0)
        assert appliquer_retour(haut, "accepted", MINUIT).score == 1.0
        bas = appliquer_retour(Score(score=0.0), "rejected", MINUIT)
        assert 0.0 <= bas.score <= 1.0

    def test_une_action_inconnue_ne_change_rien(self):
        score = Score(score=0.42, refus=1)
        assert appliquer_retour(score, "n_importe_quoi", MINUIT) == score


class TestCle:
    def test_predicat_profil_entite(self):
        assert cle_suggestion("coucher", "guillaume", None) == "coucher|guillaume|"
        assert (
            cle_suggestion("ouvrant", None, "binary_sensor.x")
            == "ouvrant||binary_sensor.x"
        )

    def test_la_cle_ne_depend_pas_de_loccurrence(self):
        """§12 : refuser trois fois une règle la mute, pas trois soirées."""
        assert cle_suggestion("ouvrant", None, "b.x") == cle_suggestion(
            "ouvrant", None, "b.x"
        )
