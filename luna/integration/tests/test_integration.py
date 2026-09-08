"""L'intégration Luna, dans une vraie instance de Home Assistant."""

from __future__ import annotations

import asyncio
from pathlib import Path

from conftest import SECRET
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType

from custom_components.luna.const import CONF_HOTE, CONF_PORT, CONF_SECRET, DOMAINE


class TestFormulaire:
    async def test_configuration_reussie(self, hass, faux_relais):
        resultat = await hass.config_entries.flow.async_init(
            DOMAINE, context={"source": "user"}
        )
        assert resultat["type"] is FlowResultType.FORM
        assert resultat["step_id"] == "user"

        resultat = await hass.config_entries.flow.async_configure(
            resultat["flow_id"],
            {
                CONF_HOTE: faux_relais.hote,
                CONF_PORT: faux_relais.port,
                CONF_SECRET: SECRET,
            },
        )
        assert resultat["type"] is FlowResultType.CREATE_ENTRY
        assert resultat["title"] == "Luna"

    async def test_relais_injoignable(self, hass, socket_enabled):
        """§8 : jamais d'échec silencieux. Une entrée ne s'enregistre pas si
        l'add-on n'a jamais répondu."""
        resultat = await hass.config_entries.flow.async_init(
            DOMAINE, context={"source": "user"}
        )
        resultat = await hass.config_entries.flow.async_configure(
            resultat["flow_id"],
            {CONF_HOTE: "127.0.0.1", CONF_PORT: 1, CONF_SECRET: "peu importe"},
        )
        assert resultat["type"] is FlowResultType.FORM
        assert resultat["errors"] == {"base": "injoignable"}

    async def test_mauvais_secret(self, hass, faux_relais):
        faux_relais.refuser_secret = True
        resultat = await hass.config_entries.flow.async_init(
            DOMAINE, context={"source": "user"}
        )
        resultat = await hass.config_entries.flow.async_configure(
            resultat["flow_id"],
            {
                CONF_HOTE: faux_relais.hote,
                CONF_PORT: faux_relais.port,
                CONF_SECRET: "mauvais",
            },
        )
        assert resultat["errors"] == {"base": "injoignable"}

    async def test_un_seul_cerveau(self, hass, entree, faux_relais):
        resultat = await hass.config_entries.flow.async_init(
            DOMAINE, context={"source": "user"}
        )
        resultat = await hass.config_entries.flow.async_configure(
            resultat["flow_id"],
            {
                CONF_HOTE: faux_relais.hote,
                CONF_PORT: faux_relais.port,
                CONF_SECRET: SECRET,
            },
        )
        assert resultat["type"] is FlowResultType.ABORT
        assert resultat["reason"] == "already_configured"


class TestInstallation:
    async def test_entree_chargee_et_capteur_cree(self, hass, entree):
        assert entree.state is ConfigEntryState.LOADED
        etat = hass.states.get("binary_sensor.luna_en_ligne")
        assert etat is not None
        assert etat.state == "on"

    async def test_dechargement(self, hass, entree):
        assert await hass.config_entries.async_unload(entree.entry_id)
        await hass.async_block_till_done()
        assert entree.state is ConfigEntryState.NOT_LOADED
        assert hass.data[DOMAINE] == {}


class TestCommandesWebSocket:
    async def test_luna_info(self, hass, entree, hass_ws_client):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id({"type": "luna/info"})
        reponse = await client.receive_json()
        assert reponse["success"] is True
        assert reponse["result"]["addon"] == "online"
        assert reponse["result"]["capabilities"] == ["chat", "ha_control"]

    async def test_le_contexte_vient_de_lutilisateur_authentifie(
        self, hass, entree, hass_ws_client, faux_relais, hass_admin_user
    ):
        """Décision A1/A6 : la carte ne peut ni fournir ni influencer le
        contexte. Il est construit ici, depuis `connection.user`."""
        client = await hass_ws_client(hass)
        await client.send_json_auto_id({"type": "luna/info", "client_id": "tablette"})
        await client.receive_json()

        contexte = faux_relais.dernier("info")["context"]
        assert contexte["ha_user_id"] == hass_admin_user.id
        assert contexte["ha_user_name"] == hass_admin_user.name
        assert contexte["is_admin"] is True
        assert contexte["client_id"] == "tablette"
        # Le profil est résolu par l'add-on (A6) : l'intégration ne l'affirme pas.
        assert contexte["profile"] == "unknown"

    async def test_luna_chat_diffuse_les_evenements(self, hass, entree, hass_ws_client):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id({"type": "luna/chat", "text": "salut"})

        souscription = await client.receive_json()
        assert souscription["success"] is True

        evenements = []
        for _ in range(3):
            trame = await client.receive_json()
            assert trame["type"] == "event"
            evenements.append(trame["event"])
        assert [e["event"] for e in evenements] == ["accepted", "delta", "done"]
        assert evenements[-1]["text"] == "Bonjour."

    async def test_le_texte_arrive_bien_a_laddon(
        self, hass, entree, hass_ws_client, faux_relais
    ):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id(
            {"type": "luna/chat", "text": "allume le salon", "conversation_id": "c_9"}
        )
        await client.receive_json()
        await asyncio.sleep(0.05)
        charge = faux_relais.dernier("chat")["payload"]
        assert charge == {"text": "allume le salon", "conversation_id": "c_9"}

    async def test_luna_feed(self, hass, entree, hass_ws_client):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id({"type": "luna/feed"})
        assert (await client.receive_json())["success"] is True
        trame = await client.receive_json()
        assert trame["event"] == {"event": "status", "addon": "online", "detail": None}

    async def test_luna_history(self, hass, entree, hass_ws_client, faux_relais):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id({"type": "luna/history", "limit": 10})
        reponse = await client.receive_json()
        assert reponse["result"]["conversation_id"] == "c_1"
        assert faux_relais.dernier("history")["payload"] == {"limit": 10}

    async def test_luna_proposal_decide(self, hass, entree, hass_ws_client, faux_relais):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id(
            {
                "type": "luna/proposal/decide",
                "proposal_id": "p_1",
                "decision": "accept",
            }
        )
        reponse = await client.receive_json()
        assert reponse["result"]["executed"] is True
        assert faux_relais.dernier("decide")["payload"] == {
            "proposal_id": "p_1",
            "decision": "accept",
        }

    async def test_decision_invalide_refusee_par_le_schema(
        self, hass, entree, hass_ws_client
    ):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id(
            {
                "type": "luna/proposal/decide",
                "proposal_id": "p_1",
                "decision": "peut-etre",
            }
        )
        reponse = await client.receive_json()
        assert reponse["success"] is False

    async def test_une_phase_future_repond_sans_mentir(
        self, hass, entree, hass_ws_client
    ):
        """§12 : documentée dès P1. §8 : jamais un silence."""
        client = await hass_ws_client(hass)
        await client.send_json_auto_id({"type": "luna/identity/face", "image": "AAAA"})
        reponse = await client.receive_json()
        assert reponse["success"] is False
        # Le code de l'add-on ressort intact : le réécrire en « addon_offline »
        # ferait passer une capacité de phase future pour une panne.
        assert reponse["error"]["code"] == "not_implemented"
        assert "Phase 6." in reponse["error"]["message"]


class TestVeille:
    """P4 : les commandes qui répondaient 501 en P1 rendent maintenant des
    données, et le contexte part bien de l'intégration."""

    async def test_luna_suggestions(self, hass, entree, hass_ws_client):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id({"type": "luna/suggestions"})
        reponse = await client.receive_json()
        assert reponse["success"] is True
        assert reponse["result"]["suggestions"][0]["id"] == "al_1"

    async def test_luna_patterns(self, hass, entree, hass_ws_client, faux_relais):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id({"type": "luna/patterns", "profile": "guillaume"})
        reponse = await client.receive_json()
        assert reponse["result"]["patterns"][0]["value"] == "23:20"
        assert faux_relais.dernier("patterns")["payload"] == {"profile": "guillaume"}

    async def test_luna_alerts_feedback(self, hass, entree, hass_ws_client, faux_relais):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id(
            {
                "type": "luna/alerts/feedback",
                "suggestion_id": "al_1",
                "action": "muted",
            }
        )
        reponse = await client.receive_json()
        assert reponse["result"]["ok"] is True
        assert faux_relais.dernier("alerts_feedback")["payload"] == {
            "suggestion_id": "al_1",
            "action": "muted",
        }

    async def test_une_action_de_retour_inconnue_est_refusee_par_le_schema(
        self, hass, entree, hass_ws_client
    ):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id(
            {"type": "luna/alerts/feedback", "suggestion_id": "al_1", "action": "bof"}
        )
        assert (await client.receive_json())["success"] is False

    async def test_agir_porte_le_contexte_de_la_session(
        self, hass, entree, hass_ws_client, faux_relais
    ):
        """D8 : c'est ce contexte-là que l'arbitre lira pour décider du droit
        d'agir seul. La carte ne peut pas le fabriquer."""
        client = await hass_ws_client(hass)
        await client.send_json_auto_id(
            {"type": "luna/alerts/act", "suggestion_id": "al_1"}
        )
        reponse = await client.receive_json()
        assert reponse["result"]["executed"] is True
        contexte = faux_relais.dernier("alerts_act")["context"]
        assert contexte["ha_user_id"]
        assert "profile" not in contexte or contexte["profile"] == "unknown"

    async def test_la_file_de_relecture(self, hass, entree, hass_ws_client):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id({"type": "luna/facts"})
        reponse = await client.receive_json()
        assert reponse["result"]["facts"][0]["value"] == "couloir tamisé le soir"

    async def test_trancher_un_fait(self, hass, entree, hass_ws_client, faux_relais):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id(
            {"type": "luna/facts/decide", "fact_id": "f_2", "decision": "accept"}
        )
        reponse = await client.receive_json()
        assert reponse["result"]["status"] == "active"
        assert faux_relais.dernier("facts_decide")["payload"] == {
            "fact_id": "f_2",
            "decision": "accept",
        }

    async def test_une_decision_invalide_est_refusee_par_le_schema(
        self, hass, entree, hass_ws_client
    ):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id(
            {"type": "luna/facts/decide", "fact_id": "f_2", "decision": "peut-etre"}
        )
        assert (await client.receive_json())["success"] is False


class TestGardienne:
    """P5 : la commande est en lecture, et elle dit ce qu'elle a pu regarder."""

    async def test_luna_health(self, hass, entree, hass_ws_client):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id({"type": "luna/health"})
        reponse = await client.receive_json()

        assert reponse["success"] is True
        resultat = reponse["result"]
        assert resultat["integrations"][0]["state"] == "setup_retry"
        # §8 : `false` veut dire « je n'ai pas pu », jamais « tout va bien ».
        assert resultat["sources"] == {"system_log": True, "lovelace": False}

    def test_aucune_commande_decriture_nest_exposee(self):
        """L'intégration est la seule porte de la carte vers l'add-on.

        Rien de ce qu'elle enregistre ne peut écrire dans la configuration de
        Home Assistant — c'est le pendant, côté nerf, du refus de P5, E1.

        Synchrone à dessein : il lit un fichier et ne touche pas à `hass`.
        """
        from custom_components.luna import websocket

        source = Path(websocket.__file__).read_text(encoding="utf-8")
        for interdite in ("lovelace/config/save", "config_entries/update"):
            assert interdite not in source


class TestModeDegrade:
    async def test_le_capteur_tombe_quand_le_relais_disparait(
        self, hass, entree, faux_relais
    ):
        """C'est ce que la carte lit pour basculer en mode dégradé (§8)."""
        assert hass.states.get("binary_sensor.luna_en_ligne").state == "on"

        client = hass.data[DOMAINE][entree.entry_id]
        await client.fermer()
        await hass.async_block_till_done()

        assert hass.states.get("binary_sensor.luna_en_ligne").state == "off"

    async def test_une_commande_sans_relais_dit_pourquoi(
        self, hass, entree, hass_ws_client
    ):
        client = hass.data[DOMAINE][entree.entry_id]
        await client.fermer()

        ws = await hass_ws_client(hass)
        await ws.send_json_auto_id({"type": "luna/info"})
        reponse = await ws.receive_json()
        assert reponse["success"] is False
        assert reponse["error"]["code"] == "addon_offline"
        assert "add-on" in reponse["error"]["message"]


class TestAgentDeConversation:
    """B1 — la deuxième porte d'entrée vers le même cerveau."""

    async def test_lentite_existe(self, hass, entree):
        etat = hass.states.get("conversation.luna")
        assert etat is not None

    async def test_un_tour_de_parole(self, hass, entree, faux_relais):
        from homeassistant.components import conversation
        from homeassistant.core import Context

        resultat = await conversation.async_converse(
            hass,
            "allume le salon",
            None,
            Context(),
            language="fr",
            agent_id="conversation.luna",
        )
        assert resultat.response.speech["plain"]["speech"] == "Bonjour."

        envoi = faux_relais.dernier("chat")
        assert envoi["payload"]["text"] == "allume le salon"

    async def test_la_provenance_et_la_diffusion_sont_posees(
        self, hass, entree, faux_relais
    ):
        """`source: voix` change la longueur de la réponse ; `diffuser` fait
        rejoindre le fil des cartes ouvertes (§7)."""
        from homeassistant.components import conversation
        from homeassistant.core import Context

        await conversation.async_converse(
            hass, "salut", None, Context(), language="fr", agent_id="conversation.luna"
        )
        envoi = faux_relais.dernier("chat")
        assert envoi["context"]["source"] == "voix"
        assert envoi["payload"]["diffuser"] is True
        # L'identité arrive en P3 : un satellite n'est pas plus qu'un invité.
        assert envoi["context"]["profile"] == "unknown"
        assert envoi["context"]["is_admin"] is False

    async def test_les_deltas_sont_recolles(self, hass, entree, faux_relais):
        from homeassistant.components import conversation
        from homeassistant.core import Context

        faux_relais.evenements_chat = [
            {"event": "accepted", "message_id": "m_1", "conversation_id": "c_1"},
            {"event": "delta", "message_id": "m_1", "text": "J'allume "},
            {"event": "delta", "message_id": "m_1", "text": "le salon."},
            {
                "event": "done",
                "message_id": "m_1",
                "text": "J'allume le salon.",
                "usage": {},
            },
        ]
        resultat = await conversation.async_converse(
            hass,
            "allume",
            None,
            Context(),
            language="fr",
            agent_id="conversation.luna",
        )
        assert resultat.response.speech["plain"]["speech"] == "J'allume le salon."

    async def test_une_erreur_se_dit_a_voix_haute(self, hass, entree, faux_relais):
        """§8 vaut aussi au micro : un silence serait le pire des retours."""
        from homeassistant.components import conversation
        from homeassistant.core import Context

        faux_relais.evenements_chat = [
            {"event": "accepted", "message_id": "m_1", "conversation_id": "c_1"},
            {
                "event": "error",
                "message_id": "m_1",
                "code": "claude_no_credit",
                "message": "Le crédit de la clé API Anthropic est épuisé.",
            },
        ]
        resultat = await conversation.async_converse(
            hass,
            "salut",
            None,
            Context(),
            language="fr",
            agent_id="conversation.luna",
        )
        assert "crédit" in resultat.response.speech["plain"]["speech"]

    async def test_le_relais_absent_ne_laisse_pas_muet(self, hass, entree):
        from homeassistant.components import conversation
        from homeassistant.core import Context

        from custom_components.luna.const import DOMAINE as D

        await hass.data[D][entree.entry_id].fermer()
        resultat = await conversation.async_converse(
            hass,
            "salut",
            None,
            Context(),
            language="fr",
            agent_id="conversation.luna",
        )
        assert "hors ligne" in resultat.response.speech["plain"]["speech"].lower()


class TestSynthese:
    """B3 — la carte parle par `luna/speak`, sans fetch ni jeton."""

    async def test_url_de_meme_origine(self, hass, entree, hass_ws_client):
        from unittest.mock import MagicMock, patch

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
            ) as creer,
        ):
            client = await hass_ws_client(hass)
            await client.send_json_auto_id(
                {"type": "luna/speak", "text": "J'allume le salon."}
            )
            reponse = await client.receive_json()

        assert reponse["success"] is True
        assert reponse["result"]["url"].startswith("/api/")
        assert "://" not in reponse["result"]["url"], "l'URL doit être relative"
        flux.async_set_message.assert_called_once_with("J'allume le salon.")
        # La voix est celle du pipeline : Luna parle pareil partout.
        assert creer.call_args.kwargs["options"] == {"voice": "fr_FR-siwis-medium"}

    async def test_sans_moteur_le_message_est_actionnable(
        self, hass, entree, hass_ws_client
    ):
        from unittest.mock import patch

        with patch(
            "custom_components.luna.websocket._voix_du_pipeline",
            return_value=(None, None, None),
        ):
            client = await hass_ws_client(hass)
            await client.send_json_auto_id({"type": "luna/speak", "text": "coucou"})
            reponse = await client.receive_json()

        assert reponse["success"] is False
        assert reponse["error"]["code"] == "tts_unavailable"
        assert "Piper" in reponse["error"]["message"]


class TestIdentite:
    """P3 — les commandes d'identité, et la barrière de §6."""

    async def test_letat_est_relaye_avec_lappareil(
        self, hass, entree, hass_ws_client, faux_relais
    ):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id({"type": "luna/identity", "device": "d_ipad"})
        reponse = await client.receive_json()

        assert reponse["result"]["profile"]["display_name"] == "Guillaume"
        assert reponse["result"]["voice_needed"] is True
        # C6 : l'identité vit par appareil, l'add-on doit savoir lequel.
        assert faux_relais.dernier("identity")["context"]["device"] == "d_ipad"

    async def test_identification_en_local(
        self, hass, entree, hass_ws_client, faux_relais
    ):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id(
            {"type": "luna/identity/voice", "device": "d_ipad", "audio": "QUJD"}
        )
        reponse = await client.receive_json()
        assert reponse["result"]["profile"] == "guillaume"
        assert faux_relais.audio_recu == ["QUJD"]

    async def test_a_distance_laudio_ne_part_pas(
        self, hass, entree, hass_ws_client, faux_relais
    ):
        """§6 : « Aucune frame ne transite par le relais Nabu Casa. »

        Le refus doit précéder le relais — sinon l'audio aurait déjà traversé
        au moment où on le refuse.
        """
        from unittest.mock import patch

        with patch("custom_components.luna.websocket.est_local", return_value=False):
            client = await hass_ws_client(hass)
            await client.send_json_auto_id(
                {"type": "luna/identity/voice", "device": "d_ipad", "audio": "QUJD"}
            )
            reponse = await client.receive_json()

        assert reponse["success"] is False
        assert reponse["error"]["code"] == "remote_biometrics"
        assert "PIN" in reponse["error"]["message"]
        assert faux_relais.audio_recu == [], "aucun octet n'a traversé le relais"

    async def test_inscription_refusee_a_distance(
        self, hass, entree, hass_ws_client, faux_relais
    ):
        from unittest.mock import patch

        with patch("custom_components.luna.websocket.est_local", return_value=False):
            client = await hass_ws_client(hass)
            for message in (
                {"type": "luna/identity/enroll/start", "profile": "guillaume"},
                {
                    "type": "luna/identity/enroll/sample",
                    "session": "e_1",
                    "index": 0,
                    "audio": "QUJD",
                },
                {"type": "luna/identity/enroll/finish", "session": "e_1"},
            ):
                await client.send_json_auto_id(message)
                reponse = await client.receive_json()
                assert reponse["error"]["code"] == "remote_biometrics", message["type"]
        assert faux_relais.audio_recu == []

    async def test_oublier_marche_a_distance(self, hass, entree, hass_ws_client):
        """Effacer une empreinte n'est pas de la biométrie : c'est le contraire."""
        from unittest.mock import patch

        with patch("custom_components.luna.websocket.est_local", return_value=False):
            client = await hass_ws_client(hass)
            await client.send_json_auto_id(
                {"type": "luna/identity/forget", "profile": "clara"}
            )
            reponse = await client.receive_json()
        assert reponse["result"]["removed"] == 5

    async def test_inscription_complete(self, hass, entree, hass_ws_client, faux_relais):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id(
            {"type": "luna/identity/enroll/start", "profile": "guillaume"}
        )
        assert (await client.receive_json())["result"]["session"] == "e_1"

        await client.send_json_auto_id(
            {
                "type": "luna/identity/enroll/sample",
                "session": "e_1",
                "index": 0,
                "audio": "QUJD",
            }
        )
        assert (await client.receive_json())["result"]["quality"] == "ok"

        await client.send_json_auto_id(
            {"type": "luna/identity/enroll/finish", "session": "e_1"}
        )
        assert (await client.receive_json())["result"]["coherence"] == 0.91

    async def test_un_extrait_trop_long_est_refuse_avant_le_relais(
        self, hass, entree, hass_ws_client, faux_relais
    ):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id(
            {"type": "luna/identity/voice", "audio": "A" * 700_000}
        )
        reponse = await client.receive_json()
        assert reponse["success"] is False
        assert faux_relais.audio_recu == []

    async def test_confirmation(self, hass, entree, hass_ws_client, faux_relais):
        client = await hass_ws_client(hass)
        await client.send_json_auto_id(
            {
                "type": "luna/identity/confirm",
                "device": "d_ipad",
                "profile": "guillaume",
                "accept": True,
            }
        )
        reponse = await client.receive_json()
        assert reponse["result"]["confidence"] == 1.0
        assert faux_relais.dernier("identity_confirm")["payload"] == {
            "profile": "guillaume",
            "accept": True,
        }


class TestPerimetreReseau:
    async def test_une_connexion_nuage_nest_pas_locale(self, hass, entree):
        from unittest.mock import patch

        from custom_components.luna.websocket import est_local

        with patch(
            "custom_components.luna.websocket.reseau.is_cloud_connection",
            return_value=True,
        ):
            assert est_local(hass) is False

    async def test_sans_requete_on_considere_local(self, hass, entree):
        """L'agent de conversation appelle hors contexte HTTP ; ce chemin ne
        fait de toute façon aucune biométrie."""
        from custom_components.luna.websocket import est_local

        assert est_local(hass) is True
