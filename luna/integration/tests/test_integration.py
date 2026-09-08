"""L'intégration Luna, dans une vraie instance de Home Assistant."""

from __future__ import annotations

import asyncio

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
        await client.send_json_auto_id({"type": "luna/patterns"})
        reponse = await client.receive_json()
        assert reponse["success"] is False
        # Le code de l'add-on ressort intact : le réécrire en « addon_offline »
        # ferait passer une capacité de phase future pour une panne.
        assert reponse["error"]["code"] == "not_implemented"
        assert "Phase 4." in reponse["error"]["message"]


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
