"""P3 — l'identité : fusion, inscription, reconnaissance, et ses limites.

La sortie testable de §11 est « Luna distingue Guillaume de Clara ». Ce fichier
la vérifie, et vérifie surtout **ce que l'identité n'a pas le droit de faire** :
§6 dit qu'elle sert à la personnalisation, pas à la sécurité.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import audio_de

from luna.engine.identity import PHRASES_INSCRIPTION, MoteurIdentite, _niveau
from luna.kernel.errors import (
    AudioTropCourt,
    BiometrieDistante,
    ModeleVoixIndisponible,
    NonAutorise,
    ProfilNonInscrit,
)
from luna.kernel.identity import (
    INCONNU,
    MARGE_MINIMUM,
    PRESENCE_ABSENT,
    PRESENCE_PRESENT,
    Candidat,
    centroide,
    coherence,
    fusionner,
    presence_depuis_etat,
    similarite,
)
from luna.kernel.schemas import ContexteRequete

PARTAGE = ContexteRequete(
    ha_user_id="u-tablette",
    ha_user_name="Tablette",
    is_admin=False,
    profile="guest",
    client_id="loggia",
    local=True,
    device="d_ipad",
)


# ═══ L0 — la fusion, au vecteur près ═════════════════════════════════════


class TestSimilarite:
    def test_identiques(self):
        assert similarite([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonaux(self):
        assert similarite([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_cas_degeneres(self):
        assert similarite([], [1.0]) == 0.0
        assert similarite([0.0, 0.0], [1.0, 0.0]) == 0.0
        assert similarite([1.0], [1.0, 0.0]) == 0.0, "dimensions différentes"

    def test_centroide_est_normalise(self):
        c = centroide([[1.0, 0.0], [0.0, 1.0]])
        assert sum(x * x for x in c) == pytest.approx(1.0)

    def test_coherence_dit_si_linscription_est_ratee(self):
        propre = coherence([[1.0, 0.0], [0.99, 0.14], [1.0, 0.05]])
        sale = coherence([[1.0, 0.0], [0.0, 1.0], [0.0, -1.0]])
        assert propre > 0.95
        assert sale < 0.5
        assert coherence([[1.0, 0.0]]) == 0.0, "une seule phrase ne prouve rien"


class TestFusion:
    def test_un_cas_net(self):
        d = fusionner(
            [
                Candidat(profil="guillaume", presence=PRESENCE_PRESENT, cosinus=0.91),
                Candidat(profil="clara", presence=PRESENCE_ABSENT, cosinus=0.58),
            ]
        )
        assert d.profil == "guillaume"
        assert d.decide
        assert not d.demander
        assert d.marge > MARGE_MINIMUM

    def test_deux_voix_trop_proches_font_demander(self):
        """C'est là que se joue « distinguer » : deux scores hauts ne
        distinguent rien."""
        d = fusionner(
            [
                Candidat(profil="guillaume", presence=0.85, cosinus=0.90),
                Candidat(profil="clara", presence=0.85, cosinus=0.89),
            ]
        )
        assert d.profil == INCONNU
        assert d.demander, "Luna doit poser la question plutôt que deviner"

    def test_une_voix_inconnue_ne_fait_meme_pas_demander(self):
        d = fusionner(
            [
                Candidat(profil="guillaume", presence=0.85, cosinus=0.10),
                Candidat(profil="clara", presence=0.85, cosinus=0.05),
            ]
        )
        assert d.profil == INCONNU
        assert not d.demander, "rien ne se détache : il n'y a rien à demander"

    def test_la_presence_departage(self):
        """§6 : le téléphone indique une probabilité, la voix confirme."""
        proches = {"cosinus": 0.88}
        avec = fusionner(
            [
                Candidat(profil="guillaume", presence=PRESENCE_PRESENT, **proches),
                Candidat(profil="clara", presence=PRESENCE_ABSENT, cosinus=0.86),
            ]
        )
        sans = fusionner(
            [
                Candidat(profil="guillaume", presence=0.5, **proches),
                Candidat(profil="clara", presence=0.5, cosinus=0.86),
            ]
        )
        assert avec.marge > sans.marge

    def test_la_presence_ne_peut_pas_opposer_de_veto(self):
        """Le défaut qu'une multiplication simple aurait laissé passer : avec
        `presence = 0.15`, aucun score n'aurait pu atteindre le seuil, si
        franche que soit la voix."""
        seul = fusionner(
            [Candidat(profil="guillaume", presence=PRESENCE_ABSENT, cosinus=0.97)]
        )
        assert seul.profil == "guillaume", (
            "un téléphone oublié dans la voiture ne rend pas méconnaissable"
        )

    def test_sans_candidat(self):
        d = fusionner([])
        assert d.profil == INCONNU and not d.demander

    def test_sous_le_seuil_le_cosinus_ne_compte_pas(self):
        assert Candidat(profil="x", cosinus=0.54).voix == 0.0
        assert Candidat(profil="x", cosinus=0.55).voix == 0.0
        assert Candidat(profil="x", cosinus=1.0).voix == pytest.approx(1.0)

    @pytest.mark.parametrize(
        ("etat", "attendu"),
        [("home", PRESENCE_PRESENT), ("not_home", PRESENCE_ABSENT), (None, 0.5)],
    )
    def test_presence_depuis_letat(self, etat, attendu):
        assert presence_depuis_etat(etat) == attendu


# ═══ L2 — inscription ════════════════════════════════════════════════════


async def _inscrire(identite, profil, locuteur, phrases=5):
    session, liste = identite.demarrer_inscription(contexte=PARTAGE, profil=profil)
    assert len(liste) == len(PHRASES_INSCRIPTION)
    for i in range(phrases):
        qualite, _ = identite.ajouter_echantillon(
            contexte=PARTAGE, session=session, index=i, pcm=audio_de(locuteur)
        )
        assert qualite == "ok"
    return await identite.terminer_inscription(contexte=PARTAGE, session=session)


class TestInscription:
    async def test_cinq_phrases_variees(self, identite):
        resultat = await _inscrire(identite, "guillaume", 1)
        assert resultat["profile"] == "guillaume"
        assert resultat["samples"] == 5
        assert resultat["coherence"] > 0.9

    def test_les_phrases_sont_variees(self):
        """H46 : cinq fois la même reconnaîtrait la phrase, pas la personne."""
        assert len(set(PHRASES_INSCRIPTION)) == 5
        assert any("?" in p for p in PHRASES_INSCRIPTION)
        assert any(len(p) > 100 for p in PHRASES_INSCRIPTION), "une phrase longue"
        assert any(c.isalpha() and "vingt" in p for p in PHRASES_INSCRIPTION for c in p)

    def test_un_echantillon_trop_court_est_refuse(self, identite):
        session, _ = identite.demarrer_inscription(contexte=PARTAGE, profil="guillaume")
        qualite, restant = identite.ajouter_echantillon(
            contexte=PARTAGE, session=session, index=0, pcm=audio_de(1, secondes=0.3)
        )
        assert qualite == "trop_court"
        assert restant == 5, "rien n'a été retenu"

    def test_un_echantillon_trop_faible_est_refuse(self, identite):
        session, _ = identite.demarrer_inscription(contexte=PARTAGE, profil="guillaume")
        qualite, _ = identite.ajouter_echantillon(
            contexte=PARTAGE, session=session, index=0, pcm=audio_de(1, niveau=10)
        )
        assert qualite == "trop_faible"

    async def test_une_phrase_seule_ne_suffit_pas(self, identite):
        session, _ = identite.demarrer_inscription(contexte=PARTAGE, profil="guillaume")
        identite.ajouter_echantillon(
            contexte=PARTAGE, session=session, index=0, pcm=audio_de(1)
        )
        with pytest.raises(AudioTropCourt):
            await identite.terminer_inscription(contexte=PARTAGE, session=session)

    def test_profil_inconnu(self, identite):
        with pytest.raises(Exception, match="Profil inconnu"):
            identite.demarrer_inscription(contexte=PARTAGE, profil="voisin")

    def test_sans_modele_on_le_dit(self, identite, empreinte):
        empreinte.disponible = False
        with pytest.raises(ModeleVoixIndisponible):
            identite.demarrer_inscription(contexte=PARTAGE, profil="guillaume")

    async def test_oublier(self, identite, memoire):
        await _inscrire(identite, "clara", 2)
        assert await identite.oublier(profil="clara") == 5
        assert await memoire.empreintes("faux-modele") == []


# ═══ L2 — reconnaissance ═════════════════════════════════════════════════


class TestReconnaissance:
    async def test_luna_distingue_guillaume_de_clara(self, identite):
        """La sortie testable de §11, mot pour mot."""
        await _inscrire(identite, "guillaume", 1)
        await _inscrire(identite, "clara", 2)

        decision, etat = await identite.identifier(audio_de(1), contexte=PARTAGE)
        assert decision.profil == "guillaume"
        assert etat.profil == "guillaume"

        decision, etat = await identite.identifier(audio_de(2), contexte=PARTAGE)
        assert decision.profil == "clara"
        assert etat.profil == "clara"

    async def test_une_voix_inconnue_reste_inconnue(self, identite):
        await _inscrire(identite, "guillaume", 1)
        await _inscrire(identite, "clara", 2)
        decision, etat = await identite.identifier(audio_de(3), contexte=PARTAGE)
        assert decision.profil == INCONNU
        assert etat.profil == INCONNU

    async def test_personne_inscrit(self, identite):
        with pytest.raises(ProfilNonInscrit):
            await identite.identifier(audio_de(1), contexte=PARTAGE)

    async def test_un_telephone_absent_pese_sans_opposer_de_veto(self, identite, maison):
        """§6 : le téléphone est une présomption, la voix confirme.

        Un téléphone oublié dans la voiture ne doit pas rendre son
        propriétaire méconnaissable — seulement un peu moins évident.
        """
        await _inscrire(identite, "guillaume", 1)
        await _inscrire(identite, "clara", 2)

        maison._entites["device_tracker.tel_guillaume"] = (
            "Téléphone de Guillaume",
            "not_home",
            None,
        )
        decision, _ = await identite.identifier(audio_de(1), contexte=PARTAGE)
        assert decision.profil == "guillaume", "la voix reste franche"
        loin = decision.marge

        maison._entites["device_tracker.tel_guillaume"] = (
            "Téléphone de Guillaume",
            "home",
            None,
        )
        decision, _ = await identite.identifier(audio_de(1), contexte=PARTAGE)
        assert decision.marge > loin, "présent, c'est plus net"

    async def test_chaque_decision_est_journalisee(self, identite, memoire):
        """C'est dans ce journal qu'on règlera les seuils de H48, sur de vraies
        voix plutôt qu'au jugé."""
        await _inscrire(identite, "guillaume", 1)
        await identite.identifier(audio_de(1), contexte=PARTAGE)

        journal = await memoire.decisions_identite()
        assert len(journal) == 1
        entree = journal[0]
        assert entree.decided == "guillaume"
        assert entree.device == "d_ipad"
        assert "guillaume" in entree.signals
        assert set(entree.signals["guillaume"]) == {"presence", "voix", "cosinus"}

    async def test_laudio_brut_nest_jamais_conserve(self, identite, memoire):
        """H50 : seul le vecteur est gardé. C'est ce qui rend l'inscription
        acceptable pour quelqu'un d'autre que soi."""
        await _inscrire(identite, "guillaume", 1)
        empreintes = await memoire.empreintes("faux-modele")
        assert empreintes
        for e in empreintes:
            assert len(e.vector) == 4, "un vecteur, pas des échantillons"
            assert e.model == "faux-modele"

    async def test_changer_de_modele_invalide_les_empreintes(self, identite, memoire):
        await _inscrire(identite, "guillaume", 1)
        assert await memoire.empreintes("faux-modele")
        assert await memoire.empreintes("un-autre-modele") == [], (
            "comparer deux modèles donnerait un nombre, pas une ressemblance"
        )


class TestDureeDeVie:
    async def test_lidentite_expire(self, identite):
        await _inscrire(identite, "guillaume", 1)
        await identite.identifier(audio_de(1), contexte=PARTAGE)
        assert identite.etat(PARTAGE).profil == "guillaume"

        etat = identite._etats["d_ipad"]
        identite._etats["d_ipad"] = etat.model_copy(
            update={"expires_at": datetime.now().astimezone() - timedelta(seconds=1)}
        )
        assert identite.etat(PARTAGE).profil == INCONNU

    async def test_confirmer_pose_lidentite(self, identite):
        etat = await identite.confirmer(
            contexte=PARTAGE, profil="guillaume", accepte=True
        )
        assert etat.profil == "guillaume"
        assert etat.confiance == 1.0
        assert etat.expires_at is not None

    async def test_refuser_efface_lidentite(self, identite):
        await identite.confirmer(contexte=PARTAGE, profil="guillaume", accepte=True)
        etat = await identite.confirmer(
            contexte=PARTAGE, profil="guillaume", accepte=False
        )
        assert etat.profil == INCONNU

    async def test_deux_appareils_ont_deux_identites(self, identite):
        await _inscrire(identite, "guillaume", 1)
        await _inscrire(identite, "clara", 2)
        ipad = PARTAGE
        cuisine = PARTAGE.model_copy(update={"device": "d_cuisine"})

        await identite.identifier(audio_de(1), contexte=ipad)
        await identite.identifier(audio_de(2), contexte=cuisine)
        assert identite.etat(ipad).profil == "guillaume"
        assert identite.etat(cuisine).profil == "clara"


class TestSessionHomeAssistant:
    """C1 — quand la session désigne quelqu'un, il n'y a rien à deviner."""

    def _ancre(self, empreinte, maison, memoire, bus):
        return MoteurIdentite(
            empreinte=empreinte,
            maison=maison,
            memoire=memoire,
            bus=bus,
            profils=lambda: ["guillaume", "clara"],
            capteur_presence=lambda p: None,
            profil_de_session=lambda c: "guillaume",
            noms={"guillaume": "Guillaume"},
        )

    def test_la_session_gagne_et_nexpire_pas(self, empreinte, maison, memoire, bus):
        moteur = self._ancre(empreinte, maison, memoire, bus)
        etat = moteur.etat(PARTAGE)
        assert etat.profil == "guillaume"
        assert etat.confiance == 1.0
        assert etat.expires_at is None
        assert etat.ancre_sur_session

    async def test_la_carte_sait_quelle_na_pas_a_envoyer_daudio(
        self, empreinte, maison, memoire, bus
    ):
        """C1 : ne pas calculer est la meilleure optimisation sur un N95."""
        moteur = self._ancre(empreinte, maison, memoire, bus)
        assert (await moteur.info(PARTAGE))["voice_needed"] is False

    async def test_sur_un_appareil_partage_elle_doit(self, identite):
        assert (await identite.info(PARTAGE))["voice_needed"] is True


# ═══ Les limites de §6 ═══════════════════════════════════════════════════


class TestPerimetreReseau:
    """§6 : « La biométrie n'est active que sur le réseau local. »"""

    @property
    def _distant(self) -> ContexteRequete:
        return PARTAGE.model_copy(update={"local": False})

    async def test_identifier_est_refuse_a_distance(self, identite):
        with pytest.raises(BiometrieDistante) as info:
            await identite.identifier(audio_de(1), contexte=self._distant)
        assert "PIN" in info.value.message

    def test_inscrire_est_refuse_a_distance(self, identite):
        with pytest.raises(BiometrieDistante):
            identite.demarrer_inscription(contexte=self._distant, profil="guillaume")

    def test_aucun_encodage_na_lieu_a_distance(self, identite, empreinte):
        """Le refus doit précéder le calcul, pas le suivre."""
        with pytest.raises(BiometrieDistante):
            identite.ajouter_echantillon(
                contexte=self._distant, session="e_x", index=0, pcm=audio_de(1)
            )
        assert empreinte.appels == []


class TestPersonnalisationPasSecurite:
    """C5 — l'identité personnalise, elle n'autorise jamais.

    C'est l'invariant de §6 : « Aucune action du niveau critique ne doit être
    déverrouillée par la biométrie. »
    """

    async def test_un_profil_reconnu_ne_valide_pas_un_niveau_4(
        self, identite, arbitre, memoire
    ):
        from luna.kernel.schemas import ActionHA, EvtProposition

        await _inscrire(identite, "guillaume", 1)
        await identite.identifier(audio_de(1), contexte=PARTAGE)
        assert identite.etat(PARTAGE).profil == "guillaume"

        emis: list[object] = []

        async def emettre(evenement):
            emis.append(evenement)

        # Le contexte porte le profil reconnu à la voix, mais `is_admin` reste
        # celui de la session Home Assistant : la voix n'y touche pas.
        contexte = PARTAGE.model_copy(update={"profile": "guillaume"})
        await arbitre._appliquer(
            ActionHA(domain="automation", service="reload"),
            libelle="Recharger les automatisations",
            nom_outil="x",
            justification="Test.",
            message_id="m1",
            contexte=contexte,
            emettre=emettre,
        )
        proposition = next(e for e in emis if isinstance(e, EvtProposition)).proposal
        assert proposition.level == 4

        with pytest.raises(NonAutorise):
            await arbitre.decider(proposition.id, "accept", contexte=contexte)

    async def test_la_voix_ne_change_aucun_niveau(self, identite):
        """Le niveau vient du registre de L0, à partir du couple
        domaine.service. Rien dans P3 ne le touche."""
        from luna.kernel.autonomy import Niveau, niveau_de

        await _inscrire(identite, "guillaume", 1)
        await identite.identifier(audio_de(1), contexte=PARTAGE)
        assert niveau_de("light", "turn_on") is Niveau.CONFORT
        assert niveau_de("cover", "open_cover") is Niveau.INTERDIT_V1
        assert niveau_de("automation", "reload") is Niveau.CONFIGURATION


class TestNiveauSonore:
    def test_silence(self):
        assert _niveau(b"\x00\x00" * 1000) == 0.0

    def test_signal(self):
        assert _niveau(audio_de(1, secondes=0.5)) > 0.2
