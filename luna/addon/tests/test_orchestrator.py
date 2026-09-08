"""L2 — un échange de bout en bout, sans Claude ni Home Assistant."""

from __future__ import annotations

import asyncio

from conftest import FauxCerveau, collecter, orchestrateur_avec

from luna.kernel.errors import CreditEpuise
from luna.kernel.schemas import (
    EvtAccepte,
    EvtDelta,
    EvtErreur,
    EvtOutil,
    EvtProposition,
    EvtTermine,
)


def _types(evenements) -> list[str]:
    return [type(e).__name__ for e in evenements]


class TestEchangeSimple:
    async def test_ordre_des_evenements(self, maison, memoire, arbitre, contexte):
        cerveau = FauxCerveau([("texte", "Bonjour "), ("texte", "Guillaume.")])
        orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)

        evenements = await collecter(orchestrateur.converser("salut", contexte=contexte))
        assert _types(evenements) == [
            "EvtAccepte",
            "EvtDelta",
            "EvtDelta",
            "EvtTermine",
        ]
        assert isinstance(evenements[0], EvtAccepte)
        assert evenements[-1].text == "Bonjour Guillaume."
        assert evenements[-1].usage.cache_read_input_tokens == 80

    async def test_le_fil_est_persiste(self, maison, memoire, arbitre, contexte):
        cerveau = FauxCerveau([("texte", "Bonsoir.")])
        orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)
        await collecter(orchestrateur.converser("bonsoir", contexte=contexte))

        resultat = await orchestrateur.historique(None, contexte=contexte, limite=10)
        textes = [m["text"] for m in resultat["messages"]]
        assert textes == ["bonsoir", "Bonsoir."]
        assert resultat["messages"][0]["role"] == "user"
        assert resultat["messages"][1]["role"] == "luna"

    async def test_lhistorique_repart_vers_le_cerveau(
        self, maison, memoire, arbitre, contexte
    ):
        cerveau = FauxCerveau([("texte", "Oui.")])
        orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)
        await collecter(orchestrateur.converser("première", contexte=contexte))
        await collecter(orchestrateur.converser("seconde", contexte=contexte))

        historique = cerveau.appels[1]["historique"]
        assert [t["role"] for t in historique] == ["user", "assistant", "user"]
        assert historique[0]["content"] == "première"
        assert historique[-1]["content"] == "seconde"

    async def test_le_contexte_volatil_est_court_et_nomme_la_personne(
        self, maison, memoire, arbitre, contexte
    ):
        cerveau = FauxCerveau()
        orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)
        await collecter(orchestrateur.converser("salut", contexte=contexte))

        texte = cerveau.appels[0]["contexte"]
        assert "Guillaume" in texte
        assert "réseau local" in texte
        assert len(texte) < 260, "il n'est jamais mis en cache : il doit rester court"

    async def test_la_provenance_est_dite_au_cerveau(
        self, maison, memoire, arbitre, contexte
    ):
        """Elle change la longueur de la réponse, jamais les droits (§9.2)."""
        cerveau = FauxCerveau()
        orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)

        await collecter(orchestrateur.converser("salut", contexte=contexte))
        assert "à l'écrit" in cerveau.appels[0]["contexte"]

        a_la_voix = contexte.model_copy(update={"source": "voix"})
        await collecter(orchestrateur.converser("salut", contexte=a_la_voix))
        assert "à la voix" in cerveau.appels[1]["contexte"]
        assert "voix haute" in cerveau.appels[1]["contexte"]

    async def test_la_voix_ne_donne_aucun_droit_supplementaire(
        self, maison, memoire, arbitre, contexte
    ):
        """Un profil sans scope reste sans scope, qu'il parle ou qu'il tape."""
        cerveau = FauxCerveau(
            [
                (
                    "outil",
                    "commander_lumiere",
                    {"cible": "Cuisine", "action": "allumer", "luminosite": None},
                )
            ]
        )
        orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)
        inconnu = contexte.model_copy(update={"profile": "unknown", "source": "voix"})
        evenements = await collecter(orchestrateur.converser("allume", contexte=inconnu))
        assert maison.appels == []
        assert any(type(e).__name__ == "EvtProposition" for e in evenements)


class TestOutils:
    async def test_une_action_de_confort_traverse_larbitre(
        self, maison, memoire, arbitre, contexte
    ):
        cerveau = FauxCerveau(
            [
                ("texte", "J'allume."),
                (
                    "outil",
                    "commander_lumiere",
                    {"cible": "Séjour", "action": "allumer", "luminosite": None},
                ),
            ]
        )
        orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)
        evenements = await collecter(
            orchestrateur.converser("allume le salon", contexte=contexte)
        )

        outils = [e for e in evenements if isinstance(e, EvtOutil)]
        assert [o.status for o in outils] == ["running", "done"]
        assert outils[-1].level == 2
        assert len(maison.appels) == 1

    async def test_les_outils_sont_enregistres_avec_le_message(
        self, maison, memoire, arbitre, contexte
    ):
        cerveau = FauxCerveau(
            [
                ("texte", "Voilà."),
                ("outil", "lister_pieces", {}),
            ]
        )
        orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)
        await collecter(orchestrateur.converser("les pièces ?", contexte=contexte))

        resultat = await orchestrateur.historique(None, contexte=contexte, limite=10)
        outils = resultat["messages"][-1]["tools"]
        assert len(outils) == 1, "un outil n'apparaît qu'une fois, à son état final"
        assert outils[0]["status"] == "done"

    async def test_une_proposition_arrive_avant_la_fin(
        self, maison, memoire, arbitre, contexte
    ):
        cerveau = FauxCerveau(
            [
                ("texte", "Je te propose "),
                (
                    "outil",
                    "regler_thermostat",
                    {"cible": "Séjour", "temperature": 20, "raison": "Il fait frais."},
                ),
                ("texte", "20 degrés."),
            ]
        )
        orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)
        evenements = await collecter(
            orchestrateur.converser("plus chaud", contexte=contexte)
        )

        types = _types(evenements)
        assert "EvtProposition" in types
        assert types.index("EvtProposition") < types.index("EvtTermine")
        assert isinstance(evenements[-1], EvtTermine)
        assert maison.appels == [], "rien ne bouge tant qu'on n'a pas validé"

        proposition = next(e for e in evenements if isinstance(e, EvtProposition))
        resultat = await orchestrateur.decider(
            proposition.proposal.id, "accept", contexte=contexte
        )
        assert resultat["executed"] is True
        assert len(maison.appels) == 1


class TestErreurs:
    async def test_une_erreur_du_cerveau_devient_un_evenement_lisible(
        self, maison, memoire, arbitre, contexte
    ):
        cerveau = FauxCerveau()
        cerveau.leve = CreditEpuise()
        orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)

        evenements = await collecter(orchestrateur.converser("salut", contexte=contexte))
        erreur = evenements[-1]
        assert isinstance(erreur, EvtErreur)
        assert erreur.code == "claude_no_credit"
        assert "console.anthropic.com" in erreur.message

    async def test_une_erreur_inattendue_ne_passe_pas_en_silence(
        self, maison, memoire, arbitre, contexte
    ):
        """§8 : « Jamais d'échec silencieux »."""
        cerveau = FauxCerveau()
        cerveau.leve = ZeroDivisionError("boum")
        orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)

        evenements = await collecter(orchestrateur.converser("salut", contexte=contexte))
        assert isinstance(evenements[-1], EvtErreur)
        assert evenements[-1].code == "internal"

    async def test_un_evenement_terminal_termine_vraiment(
        self, maison, memoire, arbitre, contexte
    ):
        cerveau = FauxCerveau()
        cerveau.leve = CreditEpuise()
        orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)
        evenements = await collecter(orchestrateur.converser("salut", contexte=contexte))
        terminaux = [e for e in evenements if isinstance(e, (EvtTermine, EvtErreur))]
        assert len(terminaux) == 1
        assert evenements[-1] is terminaux[0]


class TestAnnulation:
    async def test_annuler_interrompt_le_flux(self, maison, memoire, arbitre, contexte):
        class CerveauLent(FauxCerveau):
            async def repondre(self, **kw):
                from luna.kernel.schemas import CerveauDelta

                yield CerveauDelta(text="je commence")
                await asyncio.sleep(30)
                yield CerveauDelta(text="jamais")

        orchestrateur = orchestrateur_avec(CerveauLent(), maison, memoire, arbitre)
        recus = []
        flux = orchestrateur.converser("vas-y", contexte=contexte)

        async for evenement in flux:
            recus.append(evenement)
            if isinstance(evenement, EvtDelta):
                assert await orchestrateur.annuler(evenement.message_id) is True
                break
        await flux.aclose()
        assert _types(recus) == ["EvtAccepte", "EvtDelta"]

    async def test_annuler_un_echange_inconnu(self, maison, memoire, arbitre):
        orchestrateur = orchestrateur_avec(FauxCerveau(), maison, memoire, arbitre)
        assert await orchestrateur.annuler("m_inexistant") is False


class TestInfo:
    async def test_forme_du_contrat(self, maison, memoire, arbitre, contexte):
        orchestrateur = orchestrateur_avec(FauxCerveau(), maison, memoire, arbitre)
        info = await orchestrateur.info(contexte)

        assert info["addon"] == "online"
        assert info["capabilities"] == ["chat", "ha_control", "veille", "facts"]
        assert info["profile"]["id"] == "guillaume"
        assert info["profile"]["display_name"] == "Guillaume"
        assert info["profile"]["signals"] == {"ha_user": 1.0}
        assert info["phases"] == {
            "voice": True,
            "identity": True,
            "veille": True,
            "guardian": False,
        }, "aucune phase future ne doit être annoncée comme prête"

    async def test_degrade_quand_home_assistant_manque(
        self, maison, memoire, arbitre, contexte
    ):
        maison.connectee = False
        orchestrateur = orchestrateur_avec(FauxCerveau(), maison, memoire, arbitre)
        assert (await orchestrateur.info(contexte))["addon"] == "degraded"
