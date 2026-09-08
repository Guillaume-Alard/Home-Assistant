"""L1 — SQLite : conversations, fil, journal des actions (§9.1, §11)."""

from __future__ import annotations

from datetime import datetime, timedelta

from luna.kernel.autonomy import Niveau
from luna.kernel.ids import nouvel_id
from luna.kernel.schemas import ActionHA, EntreeJournal, MessageEnregistre, OutilResume


def _message(conversation: str, role: str, texte: str, **extra) -> MessageEnregistre:
    return MessageEnregistre(
        id=nouvel_id("m"),
        conversation_id=conversation,
        role=role,  # type: ignore[arg-type]
        text=texte,
        ts=datetime.now().astimezone(),
        **extra,
    )


class TestConversations:
    async def test_reprend_le_fil_du_profil(self, memoire):
        premier = await memoire.conversation_courante("guillaume")
        second = await memoire.conversation_courante("guillaume")
        assert premier == second

    async def test_chaque_profil_a_son_fil(self, memoire):
        assert await memoire.conversation_courante(
            "guillaume"
        ) != await memoire.conversation_courante("clara")


class TestHistorique:
    async def test_ordre_chronologique_et_pagination(self, memoire):
        conversation = await memoire.conversation_courante("guillaume")
        for i in range(5):
            await memoire.ajouter_message(_message(conversation, "user", f"m{i}"))

        _, messages, encore = await memoire.historique(
            conversation, profil="guillaume", limite=3
        )
        assert [m.text for m in messages] == ["m2", "m3", "m4"]
        assert encore is True

        _, tous, encore = await memoire.historique(
            conversation, profil="guillaume", limite=50
        )
        assert [m.text for m in tous] == ["m0", "m1", "m2", "m3", "m4"]
        assert encore is False

    async def test_sans_identifiant_on_retrouve_le_fil_courant(self, memoire):
        """C'est ce qui permet à la carte de se remettre après rechargement."""
        conversation = await memoire.conversation_courante("guillaume")
        await memoire.ajouter_message(_message(conversation, "user", "coucou"))
        identifiant, messages, _ = await memoire.historique(
            None, profil="guillaume", limite=10
        )
        assert identifiant == conversation
        assert [m.text for m in messages] == ["coucou"]

    async def test_les_outils_survivent_a_laller_retour(self, memoire):
        conversation = await memoire.conversation_courante("guillaume")
        await memoire.ajouter_message(
            _message(
                conversation,
                "luna",
                "J'allume.",
                tools=[
                    OutilResume(
                        name="commander_lumiere",
                        label="Allumer le salon",
                        level=2,
                        status="done",
                    )
                ],
            )
        )
        _, messages, _ = await memoire.historique(
            conversation, profil="guillaume", limite=10
        )
        assert messages[0].tools[0].label == "Allumer le salon"
        assert messages[0].tools[0].status == "done"


class TestJournalDesActions:
    async def test_ecriture_et_relecture(self, memoire):
        entree = EntreeJournal(
            id=nouvel_id("a"),
            ts=datetime.now().astimezone(),
            profile="guillaume",
            ha_user_id="u-1",
            level=Niveau.PERSISTANT,
            action=ActionHA(
                domain="climate",
                service="set_temperature",
                target={"entity_id": ["climate.sejour"]},
                data={"temperature": 20},
            ),
            justification="Il fait 17,5 °C.",
            decision="accepted",
            executed=True,
        )
        await memoire.journaliser(entree)

        actions = await memoire.actions()
        assert len(actions) == 1
        relu = actions[0]
        assert relu.action.cle == "climate.set_temperature"
        assert relu.action.data == {"temperature": 20}
        assert relu.justification == "Il fait 17,5 °C."
        assert relu.decision == "accepted"
        assert relu.executed is True
        assert relu.level is Niveau.PERSISTANT

    async def test_derniere_action(self, memoire):
        assert await memoire.derniere_action() is None
        quand = datetime.now().astimezone() - timedelta(minutes=5)
        await memoire.journaliser(
            EntreeJournal(
                id=nouvel_id("a"),
                ts=quand,
                profile="guillaume",
                ha_user_id=None,
                level=Niveau.INTERDIT_V1,
                action=ActionHA(domain="cover", service="close_cover"),
                justification="Demande explicite.",
                decision="refused_v1",
                executed=False,
            )
        )
        assert await memoire.derniere_action() == quand
