"""Multi-agent — cadre de délégation + agent Research.

Ce qui compte pour le principe fondateur, testé ici :
  • un agent ne voit QUE les outils de son périmètre (filtre) et ne peut pas en
    invoquer un autre (défense en profondeur dans run_agent) ;
  • Research est en lecture seule (aucun outil d'écriture/sensible, pas de
    `deleguer` → pas de délégation récursive) ;
  • `deleguer` transmet l'identité du demandeur (aucune élévation) et reste
    réservé aux personnes reconnues ; absent si la délégation n'est pas branchée.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import PROTOCOLS_TEST_YML, make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.brain.agents import AGENTS, filter_specs
from app.brain.llm import Brain
from app.brain.toolbox import Toolbox
from app.config import Settings
from app.ha.protocols import ProtocolBook
from app.identity import OWNER, UNKNOWN, Speaker
from app.store import Store

_HOUSEHOLD = Speaker(key="cam", name="Camille", known=True, is_owner=False, score=0.9)


# ── Registre : Research est bien un rôle de LECTURE ──────────────────────────

def test_research_lecture_seule():
    r = AGENTS["research"]
    interdits = {
        "action_domotique", "creer_proposition", "lancer_protocole", "musique",
        "proposer_evolution", "lancer_routine", "mcp_appeler", "deleguer",
    }
    assert not (set(r.tools) & interdits)   # aucun outil d'écriture/sensible/délégation
    assert "deleguer" not in r.tools        # jamais de délégation récursive


def test_filter_specs_restreint_au_perimetre():
    specs = [{"name": "etat_maison"}, {"name": "action_domotique"}, {"name": "deleguer"}]
    assert [s["name"] for s in filter_specs(specs, ("etat_maison",))] == ["etat_maison"]


# ── Outil `deleguer` : routage, identité, gating ─────────────────────────────

def _protocols(tmp_path):
    path = tmp_path / "protocols.yml"
    path.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    return ProtocolBook.load(path)


@pytest.fixture()
async def agbox(tmp_path):
    ha, calls = make_ha_stub()
    protocols = _protocols(tmp_path)
    store = Store(tmp_path / "agents.db")
    await store.open()
    engine = ActionEngine(build_registry(ha, protocols), store)
    toolbox = Toolbox(ha, engine, protocols, store, tz="Europe/Paris")
    seen: list[tuple] = []

    async def fake_runner(agent, tache, *, who, source):
        seen.append((agent, tache, who, source))
        return f"résultat de {agent}"

    toolbox.set_agent_runner(fake_runner)
    yield SimpleNamespace(toolbox=toolbox, seen=seen, calls=calls, store=store)
    await store.close()


async def test_deleguer_route_et_transmet_l_identite(agbox):
    content, is_error = await agbox.toolbox.run(
        "deleguer", {"agent": "research", "tache": "cherche X"},
        utterance="", source="text", speaker=OWNER,
    )
    assert not is_error and "résultat de research" in content
    agent, tache, who, _src = agbox.seen[0]
    assert agent == "research" and tache == "cherche X"
    assert who is OWNER   # l'agent hérite de l'identité du demandeur (aucune élévation)


async def test_deleguer_agent_inconnu(agbox):
    content, is_error = await agbox.toolbox.run(
        "deleguer", {"agent": "martien", "tache": "x"}, utterance="", source="text", speaker=OWNER,
    )
    assert is_error and "inconnu" in content.lower()
    assert agbox.seen == []


async def test_deleguer_reserve_aux_personnes_reconnues(agbox):
    content, _ = await agbox.toolbox.run(
        "deleguer", {"agent": "research", "tache": "x"}, utterance="", source="voice", speaker=UNKNOWN,
    )
    assert "reconnais pas" in content.lower()
    assert agbox.seen == []   # rien délégué pour un invité


async def test_deleguer_absent_sans_runner(tmp_path):
    ha, _ = make_ha_stub()
    protocols = _protocols(tmp_path)
    store = Store(tmp_path / "norunner.db")
    await store.open()
    try:
        toolbox = Toolbox(ha, None, protocols, store, tz="Europe/Paris")  # pas de set_agent_runner
        assert "deleguer" not in [s["name"] for s in toolbox.specs()]
        content, is_error = await toolbox.run(
            "deleguer", {"agent": "research", "tache": "x"}, utterance="", source="text", speaker=OWNER,
        )
        assert is_error and "pas disponible" in content.lower()
    finally:
        await store.close()


# ── run_agent : périmètre d'outils réellement appliqué (sécurité) ────────────

def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool_use(name, tid, inp):
    return SimpleNamespace(type="tool_use", name=name, id=tid, input=inp)


def _resp(stop, blocks):
    return SimpleNamespace(stop_reason=stop, content=blocks, usage=None)


class _FakeAnthropic:
    """Client Claude factice : rejoue une séquence de réponses `messages.create`."""

    def __init__(self, script):
        self.messages = SimpleNamespace(create=self._make(script))

    def _make(self, script):
        seq = list(script)

        async def create(**kwargs):
            return seq.pop(0)

        return create


@pytest.fixture()
async def brainbox(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    ha, calls = make_ha_stub()
    protocols = _protocols(tmp_path)
    store = Store(tmp_path / "brain.db")
    await store.open()
    engine = ActionEngine(build_registry(ha, protocols), store)
    toolbox = Toolbox(ha, engine, protocols, store, tz="Europe/Paris")
    brain = Brain(Settings.from_env(), toolbox)
    yield SimpleNamespace(brain=brain, toolbox=toolbox, calls=calls, store=store)
    await store.close()


async def test_run_agent_outil_autorise_passe(brainbox):
    # Research demande un outil de LECTURE de son périmètre → il s'exécute.
    brainbox.brain._client = _FakeAnthropic([
        _resp("tool_use", [_tool_use("etat_maison", "t1", {"zone": "salon"})]),
        _resp("end_turn", [_text("Le salon est calme.")]),
    ])
    out = await brainbox.brain.run_agent("research", "état du salon ?", who=OWNER, source="test")
    assert out == "Le salon est calme."
    assert brainbox.calls == []   # lecture seule : aucune écriture Nova


async def test_run_agent_refuse_un_outil_hors_perimetre(brainbox):
    # Research demande une ACTION (hors périmètre) → refus, rien n'atteint le moteur.
    brainbox.brain._client = _FakeAnthropic([
        _resp("tool_use", [_tool_use("action_domotique", "t1", {"operation": "allumer", "zone": "salon"})]),
        _resp("end_turn", [_text("Je ne fais que lire, je ne peux pas agir.")]),
    ])
    out = await brainbox.brain.run_agent("research", "allume le salon", who=OWNER, source="test")
    assert "lire" in out.lower()
    assert brainbox.calls == []   # l'outil interdit n'a JAMAIS atteint Nova


async def test_run_agent_inconnu(brainbox):
    out = await brainbox.brain.run_agent("martien", "x", who=OWNER, source="test")
    assert "inconnu" in out.lower()


# ── Agent Home : peut agir, mais UNIQUEMENT via le moteur ────────────────────

def test_home_perimetre():
    h = AGENTS["home"]
    assert "action_domotique" in h.tools and "creer_proposition" in h.tools
    # Pas d'administration/propriétaire, pas de délégation, pas d'accès hors cadre.
    interdits = {"deleguer", "sante_systemes", "audit_systemes", "lire_mon_code",
                 "proposer_evolution", "mcp_appeler"}
    assert not (set(h.tools) & interdits)


async def test_home_agit_via_le_moteur(brainbox):
    # Home délégué par le propriétaire agit sur la domotique courante — mais l'ordre
    # part par le MOTEUR (comme Luna), pas en direct.
    brainbox.brain._client = _FakeAnthropic([
        _resp("tool_use", [_tool_use("action_domotique", "t1", {"operation": "allumer", "zone": "salon"})]),
        _resp("end_turn", [_text("Salon allumé.")]),
    ])
    out = await brainbox.brain.run_agent("home", "allume le salon", who=OWNER, source="test")
    assert "salon" in out.lower()
    assert any(c[:2] == ("homeassistant", "turn_on") for c in brainbox.calls)
