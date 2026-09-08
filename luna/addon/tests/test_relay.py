"""L3 — le relais : authentification, trames, flux, arrêt (contrat §5)."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web
from conftest import FauxCerveau, orchestrateur_avec

from luna.interfaces.http import construire_app
from luna.interfaces.relay import EN_TETE_SECRET, FERMETURE_NON_AUTORISE, Relais
from luna.kernel.schemas import MaisonConnectee

SECRET = "secret-de-test"

CORRESPONDANCE = {"Guillaume": "guillaume", "Clara": "clara"}


def _profil_de_test(nom: str | None, identifiant: str | None) -> str:
    return CORRESPONDANCE.get(nom or "", "guest")


CONTEXTE = {
    "ha_user_id": "u-1",
    "ha_user_name": "Guillaume",
    "is_admin": True,
    "profile": "guillaume",
    "client_id": "loggia-tablette",
    "local": True,
}


class Banc:
    """Un add-on complet derrière un vrai serveur HTTP, sans Claude ni HA."""

    def __init__(self, cerveau, maison, memoire, arbitre, bus, identite, veille) -> None:
        self.cerveau = cerveau
        self.maison = maison
        self.bus = bus
        self.identite = identite
        self.veille = veille
        self.orchestrateur = orchestrateur_avec(cerveau, maison, memoire, arbitre)
        self.relais = Relais(
            self.orchestrateur, bus, SECRET, self._contexte, identite, veille
        )
        self.app = construire_app(
            orchestrateur=self.orchestrateur,
            relais=self.relais,
            maison=maison,
            memoire=memoire,
            veille=veille,
            modele="claude-sonnet-5",
        )

    def _contexte(self, brut):
        """Comme l'amorçage : la session d'abord, l'identité de l'appareil
        ensuite, et jamais ce que le client prétend."""
        from luna.kernel.identity import INCONNU
        from luna.kernel.schemas import ContexteRequete

        contexte = ContexteRequete(**brut)
        session = CORRESPONDANCE.get(contexte.ha_user_name or "")
        if session:
            return contexte.model_copy(update={"profile": session})
        etat = self.identite.etat(contexte)
        return contexte.model_copy(
            update={"profile": etat.profil if etat.profil != INCONNU else "guest"}
        )


@pytest.fixture
async def banc(maison, memoire, arbitre, bus, identite, veille):
    b = Banc(FauxCerveau(), maison, memoire, arbitre, bus, identite, veille)
    coureur = web.AppRunner(b.app, shutdown_timeout=1.0)
    await coureur.setup()
    site = web.TCPSite(coureur, "127.0.0.1", 0)
    await site.start()
    b.base = site.name.rstrip("/")  # type: ignore[attr-defined]
    b.ws_url = b.base.replace("http://", "ws://") + "/relay"  # type: ignore[attr-defined]
    async with ClientSession() as session:
        b.session = session  # type: ignore[attr-defined]
        try:
            yield b
        finally:
            await coureur.cleanup()


async def _ouvrir(banc, secret: str = SECRET):
    return await banc.session.ws_connect(banc.ws_url, headers={EN_TETE_SECRET: secret})


async def _demander(ws, identifiant: int, op: str, charge=None, contexte=None):
    await ws.send_json(
        {
            "id": identifiant,
            "op": op,
            "payload": charge or {},
            "context": contexte if contexte is not None else CONTEXTE,
        }
    )
    return await asyncio.wait_for(ws.receive_json(), 5)


class TestAuthentification:
    async def test_sans_secret_la_connexion_est_fermee(self, banc):
        ws = await banc.session.ws_connect(banc.ws_url)
        await asyncio.wait_for(ws.receive(), 5)
        assert ws.close_code == FERMETURE_NON_AUTORISE

    async def test_mauvais_secret(self, banc):
        ws = await _ouvrir(banc, "pas-le-bon")
        await asyncio.wait_for(ws.receive(), 5)
        assert ws.close_code == FERMETURE_NON_AUTORISE

    async def test_bon_secret(self, banc):
        ws = await _ouvrir(banc)
        reponse = await _demander(ws, 1, "info")
        assert reponse["id"] == 1
        assert reponse["result"]["addon"] == "online"
        await ws.close()


class TestOperationsPonctuelles:
    async def test_info_et_historique(self, banc):
        ws = await _ouvrir(banc)
        info = await _demander(ws, 1, "info")
        assert info["result"]["profile"]["display_name"] == "Guillaume"

        histoire = await _demander(ws, 2, "history", {"limit": 10})
        assert histoire["result"]["messages"] == []
        assert histoire["result"]["has_more"] is False
        await ws.close()

    async def test_annuler_un_echange_inconnu(self, banc):
        ws = await _ouvrir(banc)
        reponse = await _demander(ws, 1, "cancel", {"message_id": "m_x"})
        assert reponse["result"] == {"cancelled": False}
        await ws.close()

    async def test_operation_inconnue(self, banc):
        ws = await _ouvrir(banc)
        reponse = await _demander(ws, 1, "op_qui_nexiste_pas")
        assert reponse["error"]["code"] == "internal"
        await ws.close()

    @pytest.mark.parametrize(("op", "phase"), [("identity_face", "phase 6")])
    async def test_les_phases_futures_repondent_sans_mentir(self, banc, op, phase):
        """§8 : jamais d'échec silencieux, même pour ce qui n'existe pas encore."""
        ws = await _ouvrir(banc)
        reponse = await _demander(ws, 1, op)
        assert reponse["error"]["code"] == "not_implemented"
        assert phase in reponse["error"]["message"]
        await ws.close()

    @pytest.mark.parametrize(
        ("op", "cle"),
        [("suggestions", "suggestions"), ("patterns", "patterns"), ("facts", "facts")],
    )
    async def test_les_lectures_de_p4_repondent_vide_plutot_que_501(self, banc, op, cle):
        """Ce qui était `not_implemented` en P1 rend maintenant une liste.

        Vide, ici : le banc n'a aucune règle de veille ni aucun fait. C'est
        exactement ce qu'une maison neuve doit voir — pas une erreur.
        """
        ws = await _ouvrir(banc)
        reponse = await _demander(ws, 1, op)
        assert reponse["result"] == {cle: []}
        await ws.close()


class TestFluxDeConversation:
    async def test_un_echange_complet(self, banc):
        banc.cerveau.scenario = [("texte", "Bonjour "), ("texte", "Guillaume.")]
        ws = await _ouvrir(banc)
        await ws.send_json(
            {"id": 7, "op": "chat", "payload": {"text": "salut"}, "context": CONTEXTE}
        )
        evenements = []
        while True:
            trame = await asyncio.wait_for(ws.receive_json(), 5)
            assert trame["id"] == 7
            evenements.append(trame)
            if trame.get("event") in ("done", "error"):
                break
        assert [e["event"] for e in evenements] == [
            "accepted",
            "delta",
            "delta",
            "done",
        ]
        assert evenements[-1]["text"] == "Bonjour Guillaume."
        await ws.close()

    async def test_le_profil_est_resolu_par_laddon_pas_annonce(self, banc):
        """Décision A6 : la correspondance vit dans les options de l'add-on.

        Se déclarer « guillaume » ne sert à rien — seul l'utilisateur HA
        authentifié, transmis par l'intégration, décide.
        """
        ws = await _ouvrir(banc)
        info = await _demander(
            ws,
            1,
            "info",
            contexte={"profile": "guillaume", "ha_user_name": "Quelquun"},
        )
        assert info["result"]["profile"]["id"] == "guest"

        vrai = await _demander(
            ws, 2, "info", contexte={"profile": "guest", "ha_user_name": "Clara"}
        )
        assert vrai["result"]["profile"]["id"] == "clara"
        await ws.close()

    async def test_stop_interrompt_un_flux(self, banc):
        class CerveauLent(FauxCerveau):
            async def repondre(self, **kw):
                from luna.kernel.schemas import CerveauDelta

                yield CerveauDelta(text="je commence")
                await asyncio.sleep(30)

        banc.orchestrateur._cerveau = CerveauLent()
        ws = await _ouvrir(banc)
        await ws.send_json(
            {"id": 9, "op": "chat", "payload": {"text": "vas-y"}, "context": CONTEXTE}
        )
        assert (await asyncio.wait_for(ws.receive_json(), 5))["event"] == "accepted"
        assert (await asyncio.wait_for(ws.receive_json(), 5))["event"] == "delta"

        await ws.send_json(
            {"id": 10, "op": "stop", "payload": {"stream_id": 9}, "context": CONTEXTE}
        )
        reponse = await asyncio.wait_for(ws.receive_json(), 5)
        assert reponse == {"id": 10, "result": {"stopped": True}}
        await ws.close()


class TestDiffusion:
    """§7 : « à l'écrit et à l'oral, indifféremment ».

    Un tour de parole né hors de la carte — l'agent de conversation d'Assist —
    doit rejoindre le fil des cartes ouvertes. Sans ça, deux fils parallèles
    qui s'ignorent.
    """

    async def test_un_tour_dassist_arrive_dans_le_feed_des_cartes(self, banc):
        banc.cerveau.scenario = [("texte", "J'allume le salon.")]
        carte = await _ouvrir(banc)
        assert (await _demander(carte, 1, "feed"))["event"] == "status"

        assist = await _ouvrir(banc)
        await assist.send_json(
            {
                "id": 2,
                "op": "chat",
                "payload": {"text": "allume le salon", "diffuser": True},
                "context": {**CONTEXTE, "source": "voix", "client_id": "assist"},
            }
        )

        recus = []
        for _ in range(2):
            trame = await asyncio.wait_for(carte.receive_json(), 5)
            recus.append(trame["message"])
        assert [m["role"] for m in recus] == ["user", "luna"]
        assert recus[0]["text"] == "allume le salon"
        assert recus[1]["text"] == "J'allume le salon."
        # Home Assistant a déjà parlé : la carte ne doit pas répéter.
        assert all(m["speak"] is False for m in recus)
        await carte.close()
        await assist.close()

    async def test_une_proposition_vocale_est_diffusee(self, banc):
        """On ne clique pas dans un haut-parleur : la proposition doit arriver
        là où quelqu'un peut l'accepter."""
        banc.cerveau.scenario = [
            ("texte", "Je te propose 20 degrés."),
            (
                "outil",
                "regler_thermostat",
                {"cible": "Séjour", "temperature": 20, "raison": "Il fait frais."},
            ),
        ]
        carte = await _ouvrir(banc)
        await _demander(carte, 1, "feed")

        assist = await _ouvrir(banc)
        await assist.send_json(
            {
                "id": 2,
                "op": "chat",
                "payload": {"text": "mets 20 degrés", "diffuser": True},
                "context": {**CONTEXTE, "source": "voix", "client_id": "assist"},
            }
        )

        vus = []
        for _ in range(3):
            vus.append(await asyncio.wait_for(carte.receive_json(), 5))
        genres = [t["event"] for t in vus]
        assert genres == ["message", "proposal", "message"]
        proposition = next(t for t in vus if t["event"] == "proposal")["proposal"]
        assert proposition["level"] == 3
        assert proposition["why"] == "Il fait frais."
        await carte.close()
        await assist.close()

    async def test_sans_diffuser_rien_ne_sort(self, banc):
        """Un échange né dans la carte ne doit pas revenir en double par le feed."""
        carte = await _ouvrir(banc)
        await _demander(carte, 1, "feed")

        await carte.send_json(
            {"id": 2, "op": "chat", "payload": {"text": "salut"}, "context": CONTEXTE}
        )
        vus = []
        while True:
            trame = await asyncio.wait_for(carte.receive_json(), 5)
            vus.append(trame)
            if trame.get("event") in ("done", "error"):
                break
        assert all(t["id"] == 2 for t in vus), "aucun événement sur le feed"
        await carte.close()


class TestFeed:
    async def test_statut_initial_puis_changement(self, banc):
        ws = await _ouvrir(banc)
        initial = await _demander(ws, 3, "feed")
        assert initial == {"id": 3, "event": "status", "addon": "online", "detail": None}

        banc.maison.connectee = False
        await banc.bus.publier(MaisonConnectee(connectee=False))
        suite = await asyncio.wait_for(ws.receive_json(), 5)
        assert suite["addon"] == "degraded"
        assert suite["detail"] == "Home Assistant injoignable"
        await ws.close()


class TestRoutesHTTP:
    async def test_sante(self, banc):
        async with banc.session.get(f"{banc.base}/health") as reponse:
            assert reponse.status == 200
            charge = await reponse.json()
        assert charge["status"] == "ok"
        assert charge["ha"] is True
        assert charge["claude"] == "claude-sonnet-5"

    async def test_sante_degradee(self, banc):
        banc.maison.connectee = False
        async with banc.session.get(f"{banc.base}/health") as reponse:
            assert (await reponse.json())["status"] == "degraded"

    @pytest.mark.parametrize(
        ("methode", "chemin"),
        [("post", "/identity/voice"), ("post", "/identity/face")],
    )
    async def test_les_routes_de_la_section_12_existent_deja(self, banc, methode, chemin):
        """§12 : « À documenter dès la première phase, même si implémentés plus
        tard. » Elles répondent 501, jamais 404."""
        async with getattr(banc.session, methode)(f"{banc.base}{chemin}") as reponse:
            assert reponse.status == 501
            assert (await reponse.json())["code"] == "not_implemented"

    @pytest.mark.parametrize(
        ("methode", "chemin", "cle"),
        [
            ("get", "/profile/guillaume/patterns", "patterns"),
            ("get", "/suggestions", "suggestions"),
        ],
    )
    async def test_les_routes_de_p4_ne_repondent_plus_501(
        self, banc, methode, chemin, cle
    ):
        async with getattr(banc.session, methode)(f"{banc.base}{chemin}") as reponse:
            assert reponse.status == 200
            assert (await reponse.json()) == {cle: []}

    async def test_feedback_sur_une_alerte_inconnue(self, banc):
        """§8 : un identifiant périmé a droit à un message, pas à un 500."""
        async with banc.session.post(
            f"{banc.base}/feedback", json={"suggestion_id": "al_x", "action": "muted"}
        ) as reponse:
            assert reponse.status == 404
            assert (await reponse.json())["code"] == "alert_unknown"
