"""Ce que Luna écrit n'est pas toujours ce qui se dit.

Le texte parlé est exactement le texte écrit. Un identifiant d'entité devient
« light point salon », un nombre décimal « dix-neuf point cinq », une astérisque
« astérisque ». Ces tests fixent ce qui est corrigé — et surtout ce qui ne l'est
pas : une normalisation trop zélée abîmerait des phrases déjà correctes.
"""

from __future__ import annotations

import pytest

from custom_components.luna.prononciation import pour_la_voix

NOMS = {
    "light.salon_plafond": "Plafond du salon",
    "switch.cafetiere": "Cafetière",
}


def nom_de(entity_id: str) -> str | None:
    return NOMS.get(entity_id)


class TestCeQuiEstCorrige:
    def test_le_point_decimal_devient_une_virgule(self):
        """« 19,5 » est juste à l'écrit *et* lu « virgule » par le synthétiseur."""
        assert pour_la_voix("Il fait 19.5 degrés.") == "Il fait 19,5 degrés."

    def test_un_identifiant_devient_un_nom(self):
        rendu = pour_la_voix("J'allume light.salon_plafond.", nom_de)
        assert rendu == "J'allume Plafond du salon."

    def test_plusieurs_identifiants_dans_la_meme_phrase(self):
        rendu = pour_la_voix(
            "light.salon_plafond et switch.cafetiere sont allumés.", nom_de
        )
        assert "Plafond du salon et Cafetière" in rendu
        assert "." not in rendu.removesuffix(".")

    def test_le_balisage_disparait(self):
        assert pour_la_voix("C'est **important** et *urgent*.") == (
            "C'est important et urgent."
        )
        assert pour_la_voix("Le `capteur` répond.") == "Le capteur répond."

    def test_un_titre_et_des_puces_deviennent_du_texte(self):
        rendu = pour_la_voix("## Bilan\n- le salon\n- la cuisine")
        assert "#" not in rendu
        assert "-" not in rendu
        assert "le salon" in rendu and "la cuisine" in rendu

    def test_un_lien_ne_laisse_que_son_texte(self):
        assert pour_la_voix("Vois [le tableau](http://nova.local:8123).") == (
            "Vois le tableau."
        )

    def test_une_ponctuation_collee_est_decollee(self):
        """« Fait.Ensuite » n'est pas une phrase, c'est deux."""
        assert pour_la_voix("Fait.Ensuite je ferme.") == "Fait. Ensuite je ferme."


class TestCeQuiNeDoitPasBouger:
    def test_une_phrase_ordinaire_est_rendue_telle_quelle(self):
        phrase = "La fenêtre du salon est restée ouverte depuis deux heures."
        assert pour_la_voix(phrase) == phrase

    def test_la_fin_de_phrase_suivie_d_un_nombre_reste_un_point(self):
        """Le point décimal se borne aux chiffres : sinon on casserait ceci."""
        phrase = "Il est tard. 22 heures passées."
        assert pour_la_voix(phrase) == phrase

    def test_un_mot_qui_ressemble_a_un_identifiant_n_est_pas_traduit(self):
        """Inventer un nom pour `nova.local` ferait pire que le point."""
        phrase = "Regarde nova.local pour voir."
        assert pour_la_voix(phrase, nom_de) == phrase

    def test_un_identifiant_inconnu_reste_intact(self):
        phrase = "light.inexistante ne répond plus."
        assert pour_la_voix(phrase, nom_de) == phrase

    def test_sans_resolveur_les_identifiants_sont_laisses(self):
        """La fonction reste pure et prévisible quand on ne lui donne rien."""
        phrase = "light.salon_plafond est allumée."
        assert pour_la_voix(phrase) == phrase

    def test_le_vide_reste_vide(self):
        assert pour_la_voix("") == ""
        assert pour_la_voix("   ") == ""

    def test_les_apostrophes_et_accents_survivent(self):
        phrase = "J'ai éteint la salle à manger, l'entrée et le couloir."
        assert pour_la_voix(phrase) == phrase


class TestBranchement:
    """Les deux chemins de parole doivent en bénéficier, pas un seul."""

    async def test_la_carte_fait_lire_le_texte_normalise(
        self, hass, entree, hass_ws_client
    ):
        from unittest.mock import MagicMock, patch

        hass.states.async_set(
            "light.salon_plafond", "on", {"friendly_name": "Plafond du salon"}
        )
        flux = MagicMock()
        flux.url = "/api/tts_proxy/abc.mp3"
        flux.async_set_message = MagicMock()

        with (
            patch(
                "custom_components.luna.websocket._voix_du_pipeline",
                return_value=("tts.piper", "fr", "fr_FR-siwis-medium"),
            ),
            patch(
                "custom_components.luna.websocket.tts.async_create_stream",
                return_value=flux,
            ),
        ):
            client = await hass_ws_client(hass)
            await client.send_json_auto_id(
                {
                    "type": "luna/speak",
                    "text": "**light.salon_plafond** est à 19.5 degrés.",
                }
            )
            reponse = await client.receive_json()

        assert reponse["success"] is True
        flux.async_set_message.assert_called_once_with(
            "Plafond du salon est à 19,5 degrés."
        )


@pytest.mark.parametrize(
    "brut",
    [
        "Rien à signaler.",
        "Trois lampes sont allumées : le salon, la cuisine et l'entrée.",
        "Il est 22 h 30, tu m'avais demandé de te le rappeler.",
    ],
)
def test_les_phrases_du_corpus_traversent_sans_dommage(brut):
    """Le corpus de voix sert aussi de garde-fou : ces phrases sont déjà justes."""
    assert pour_la_voix(brut, nom_de) == brut
