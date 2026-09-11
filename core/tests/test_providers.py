"""Multi-LLM : profils, adaptateur compatible OpenAI, bascule, et — surtout —
la preuve que la sécurité tient quel que soit le modèle (un appel d'outil d'un
fournisseur alternatif repasse par la Toolbox et le moteur d'actions)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import openai
import pytest
from conftest import make_ha_stub

from app.brain.llm import Brain
from app.brain.providers import (
    ANTHROPIC_ID,
    LLMUnavailable,
    OpenAICompatProvider,
    ProviderProfile,
    PRESETS,
    load_profiles,
    public_view,
    resolve_default,
    to_openai_tools,
    _translate_error,
)
from app.config import Settings
from app.identity import OWNER


# ── Traduction des outils (Anthropic → OpenAI) ───────────────────────────────

def test_to_openai_tools_transpose_le_schema():
    specs = [{
        "name": "action_domotique",
        "description": "Agit sur la maison",
        "input_schema": {"type": "object", "properties": {"operation": {"type": "string"}},
                         "required": ["operation"]},
    }]
    out = to_openai_tools(specs)
    assert out == [{
        "type": "function",
        "function": {
            "name": "action_domotique",
            "description": "Agit sur la maison",
            "parameters": {"type": "object", "properties": {"operation": {"type": "string"}},
                           "required": ["operation"]},
        },
    }]


def test_to_openai_tools_schema_par_defaut_si_absent():
    out = to_openai_tools([{"name": "x"}])
    assert out[0]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert to_openai_tools(None) == []


# ── Profils & configuration ──────────────────────────────────────────────────

def _settings(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings.from_env()


def test_load_profiles_claude_en_tete_et_presets(monkeypatch, tmp_path):
    profiles = load_profiles(_settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="clef"))
    ids = [p.id for p in profiles]
    assert ids[0] == ANTHROPIC_ID
    assert ids[1:] == [p.id for p in PRESETS]  # ordre stable openai/gemini/groq/openrouter
    claude = profiles[0]
    assert claude.kind == "anthropic" and claude.web_search is True and claude.available


def test_profil_disponible_selon_la_cle(monkeypatch, tmp_path):
    profiles = {p.id: p for p in load_profiles(
        _settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="clef", GROQ_API_KEY="g")
    )}
    assert profiles["groq"].available          # clé présente + SDK openai installé
    assert not profiles["openai"].available     # pas de clé
    assert profiles["groq"].web_search is False  # recherche web = Claude uniquement


def test_modele_surchargeable(monkeypatch, tmp_path):
    profiles = {p.id: p for p in load_profiles(
        _settings(monkeypatch, tmp_path, GEMINI_MODEL="gemini-2.5-pro")
    )}
    assert profiles["gemini"].model == "gemini-2.5-pro"
    # sans surcharge : le modèle par défaut du preset
    monkeypatch.delenv("GEMINI_MODEL")
    default = {p.id: p for p in load_profiles(Settings.from_env())}
    assert default["gemini"].model == next(p.default_model for p in PRESETS if p.id == "gemini")


def test_resolve_default(monkeypatch, tmp_path):
    profiles = load_profiles(_settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="clef", GROQ_API_KEY="g"))
    assert resolve_default(profiles, "groq") == "groq"        # demandé + disponible
    assert resolve_default(profiles, "openai") == ANTHROPIC_ID  # demandé indisponible → Claude
    assert resolve_default(profiles, "") == ANTHROPIC_ID        # défaut → Claude si dispo
    # Sans Claude, on prend le premier disponible.
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    only_groq = load_profiles(Settings.from_env())
    assert resolve_default(only_groq, "") == "groq"


def test_public_view_ne_fuit_jamais_la_cle(monkeypatch, tmp_path):
    profiles = load_profiles(_settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="secret", OPENAI_API_KEY="aussi-secret"))
    view = public_view(profiles, ANTHROPIC_ID, ANTHROPIC_ID)
    assert view["active"] == ANTHROPIC_ID and view["default"] == ANTHROPIC_ID
    blob = repr(view)
    assert "secret" not in blob and "aussi-secret" not in blob
    for p in view["providers"]:
        assert set(p) == {"id", "label", "kind", "model", "available", "web_search", "hint", "configured"}
        # `configured` = une clé est présente, SANS jamais révéler laquelle ni sa valeur.
        assert isinstance(p["configured"], bool)
    by_id = {p["id"]: p for p in view["providers"]}
    assert by_id[ANTHROPIC_ID]["configured"] is True
    assert by_id["openai"]["configured"] is True


# ── Adaptateur compatible OpenAI : faux client streaming ──────────────────────

def _tc(index, *, id=None, name=None, args=None):
    return SimpleNamespace(index=index, id=id, type="function",
                           function=SimpleNamespace(name=name, arguments=args))


def _chunk(*, content=None, tool_calls=None, finish=None, empty=False):
    if empty:
        return SimpleNamespace(choices=[])
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return False
    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c
        return gen()


class _FakeCompletions:
    def __init__(self, responses, captured):
        self._responses = list(responses)
        self._captured = captured
    async def create(self, **kwargs):
        self._captured.append(kwargs)
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class _FakeOpenAI:
    def __init__(self, responses, captured):
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses, captured))


def _openai_profile(model="gpt-4o"):
    return ProviderProfile(id="openai", label="ChatGPT (OpenAI)", kind="openai",
                           model=model, base_url="https://api.openai.com/v1",
                           api_key="test-key", web_search=False)


def _provider(responses, captured):
    prov = OpenAICompatProvider(_openai_profile())
    prov._client = _FakeOpenAI(responses, captured)  # faux client injecté
    return prov


async def _drain(agen):
    return "".join([c async for c in agen])


async def _noop_activity(_name):
    pass


async def test_stream_texte_simple():
    captured = []
    prov = _provider([_FakeStream([_chunk(content="Bonjour"), _chunk(content=" Guillaume"),
                                   _chunk(empty=True), _chunk(finish="stop")])], captured)

    async def run_tool(name, args):
        raise AssertionError("aucun outil ne devait être appelé")

    text = await _drain(prov.stream(
        messages=[{"role": "user", "content": "salut"}], tools=None, system_text="SYS",
        max_tokens=512, notify_activity=_noop_activity, run_tool=run_tool,
    ))
    assert text == "Bonjour Guillaume"
    assert captured[0]["messages"][0] == {"role": "system", "content": "SYS"}
    assert "tools" not in captured[0]  # aucun outil déclaré → pas de clé tools


async def test_stream_tour_d_outil_arguments_fragmentes():
    captured, calls, acts = [], [], []
    responses = [
        _FakeStream([
            _chunk(tool_calls=[_tc(0, id="call_1", name="action_domotique", args='{"operation":"allumer",')]),
            _chunk(tool_calls=[_tc(0, args='"zone":"salon"}')], finish="tool_calls"),
        ]),
        _FakeStream([_chunk(content="C'est fait."), _chunk(finish="stop")]),
    ]
    prov = _provider(responses, captured)

    async def run_tool(name, args):
        calls.append((name, args))
        return "résultat outil", False

    async def notify(name):
        acts.append(name)

    tools = [{"name": "action_domotique", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
    text = await _drain(prov.stream(
        messages=[{"role": "user", "content": "allume le salon"}], tools=tools, system_text="SYS",
        max_tokens=512, notify_activity=notify, run_tool=run_tool,
    ))
    assert text == "C'est fait."
    # Les fragments d'arguments sont recollés puis passés parsés à run_tool.
    assert calls == [("action_domotique", {"operation": "allumer", "zone": "salon"})]
    assert acts == ["action_domotique"]
    # Le 2e appel API contient bien le message assistant (tool_calls) + le résultat outil.
    msgs = captured[1]["messages"]
    assert msgs[-2]["role"] == "assistant" and msgs[-2]["tool_calls"][0]["function"]["name"] == "action_domotique"
    assert msgs[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "résultat outil"}


async def test_stream_repli_max_completion_tokens():
    captured = []
    bad = openai.BadRequestError.__new__(openai.BadRequestError)
    bad.message = "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."
    prov = _provider([bad, _FakeStream([_chunk(content="ok"), _chunk(finish="stop")])], captured)
    text = await _drain(prov.stream(
        messages=[{"role": "user", "content": "x"}], tools=None, system_text="",
        max_tokens=777, notify_activity=_noop_activity,
        run_tool=lambda n, a: None,
    ))
    assert text == "ok"
    assert captured[0].get("max_tokens") == 777          # 1er essai
    assert captured[1].get("max_completion_tokens") == 777  # repli
    assert "max_tokens" not in captured[1]


async def test_stream_erreur_inattendue_devient_llm_unavailable():
    prov = _provider([RuntimeError("boum")], [])
    with pytest.raises(LLMUnavailable) as exc:
        await _drain(prov.stream(
            messages=[{"role": "user", "content": "x"}], tools=None, system_text="",
            max_tokens=512, notify_activity=_noop_activity,
            run_tool=lambda n, a: None,
        ))
    assert "ChatGPT (OpenAI)" in str(exc.value)


def test_translate_error_messages():
    prof = _openai_profile()

    def mk(cls):
        return cls.__new__(cls)

    assert "invalide" in str(_translate_error(prof, mk(openai.AuthenticationError)))
    assert "débit" in str(_translate_error(prof, mk(openai.RateLimitError)))
    assert "joindre" in str(_translate_error(prof, mk(openai.APIConnectionError)))
    status = mk(openai.APIStatusError)
    status.status_code = 500
    status.body = {"error": {"message": "boom interne"}}
    msg = str(_translate_error(prof, status))
    assert "(500)" in msg and "boom interne" in msg


# ── Bascule de fournisseur (Brain) ────────────────────────────────────────────

def test_set_provider_refuse_indisponible(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="clef")
    brain = Brain(settings, toolbox=None)
    assert brain.active_id == ANTHROPIC_ID
    assert brain.set_provider("openai") is False   # pas de clé OpenAI
    assert brain.active_id == ANTHROPIC_ID          # inchangé
    assert brain.set_provider("inexistant") is False


def test_set_provider_accepte_disponible(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="clef", GROQ_API_KEY="g")
    brain = Brain(settings, toolbox=None)
    assert brain.set_provider("groq") is True
    assert brain.active_id == "groq"
    view = brain.providers_public()
    assert view["active"] == "groq" and view["default"] == ANTHROPIC_ID


# ── Réglages LLM éditables (apply_config) ─────────────────────────────────────

def test_apply_config_pose_une_cle_active_le_fournisseur(monkeypatch, tmp_path):
    """Coller une clé depuis le cockpit rend un fournisseur disponible — sans .env
    ni redémarrage — et la vue publique le signale SANS jamais révéler la clé."""
    settings = _settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="clef")  # OpenAI sans clé
    brain = Brain(settings, toolbox=None)
    assert brain._by_id["openai"].available is False

    brain.apply_config({"providers": {"openai": {"key": "sk-secret-xyz"}}})
    assert brain._by_id["openai"].available is True
    view = brain.providers_public()
    by_id = {p["id"]: p for p in view["providers"]}
    assert by_id["openai"]["configured"] is True and by_id["openai"]["available"] is True
    # La clé ne fuit JAMAIS dans la vue cockpit.
    assert "sk-secret-xyz" not in repr(view)
    for p in view["providers"]:
        assert "api_key" not in p and "key" not in p


def test_apply_config_modele_et_actif(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="clef")
    brain = Brain(settings, toolbox=None)
    brain.apply_config({
        "active": "openai",
        "providers": {"openai": {"key": "k", "model": "gpt-4o-mini"}},
    })
    assert brain.active_id == "openai"
    assert brain._by_id["openai"].model == "gpt-4o-mini"
    # Modèle vide ⇒ retour au modèle par défaut du preset.
    brain.apply_config({"providers": {"openai": {"key": "k", "model": ""}}})
    assert brain._by_id["openai"].model == next(p.default_model for p in PRESETS if p.id == "openai")


def test_apply_config_actif_indisponible_retombe_sur_defaut(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="clef")
    brain = Brain(settings, toolbox=None)
    # OpenAI demandé actif mais sans clé → on retombe sur le défaut (Claude).
    brain.apply_config({"active": "openai"})
    assert brain.active_id == ANTHROPIC_ID


def test_apply_config_params_bornes(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="clef")
    brain = Brain(settings, toolbox=None)
    brain.apply_config({"params": {"effort": "high", "max_tokens": 4096, "history_window": 24}})
    view = brain.providers_public()
    assert view["effort"] == "high" and view["max_tokens"] == 4096 and view["history_window"] == 24
    assert brain.history_window == 24
    # Valeurs hors bornes ou absurdes → bornées / valeur par défaut, jamais d'exception.
    brain.apply_config({"params": {"effort": "turbo", "max_tokens": 999999, "history_window": 0}})
    view = brain.providers_public()
    assert view["effort"] == settings.effort          # « turbo » invalide → défaut
    assert view["max_tokens"] == 64000                # plafonné
    assert view["history_window"] == 1                # plancher


def test_apply_config_vide_revient_a_l_environnement(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="clef", GROQ_API_KEY="g")
    brain = Brain(settings, toolbox=None)
    brain.apply_config({"active": "groq", "params": {"max_tokens": 4096}})
    assert brain.active_id == "groq" and brain.providers_public()["max_tokens"] == 4096
    # Config vidée → on repart des valeurs d'environnement (actif = défaut Claude).
    brain.apply_config({})
    assert brain.active_id == ANTHROPIC_ID
    assert brain.providers_public()["max_tokens"] == settings.max_tokens


# ── L'INVARIANT DE SÉCURITÉ tient pour un fournisseur alternatif ──────────────

async def test_fournisseur_alternatif_passe_par_le_moteur(monkeypatch, tmp_path):
    """Un appel d'outil émis par un modèle alternatif (chemin compatible OpenAI)
    aboutit à une écriture Nova UNIQUEMENT via la Toolbox → moteur → exécuteur →
    call_service. La sécurité ne dépend pas du modèle qui parle."""
    from app.actions.engine import ActionEngine
    from app.actions.executors import build_registry
    from app.brain.toolbox import Toolbox
    from app.ha.protocols import ProtocolBook
    from app.store import Store

    settings = _settings(monkeypatch, tmp_path, ANTHROPIC_API_KEY="clef", OPENAI_API_KEY="test-key")
    ha, calls = make_ha_stub()
    protocols = ProtocolBook.load(tmp_path / "none.yml")  # aucun protocole (fichier absent)
    store = Store(tmp_path / "prov.db")
    await store.open()
    engine = ActionEngine(build_registry(ha, protocols), store)
    toolbox = Toolbox(ha, engine, protocols, store)
    brain = Brain(settings, toolbox=toolbox)

    assert brain.set_provider("openai") is True
    # Injecte un faux client dans l'adaptateur réel (mêmes profil et boucle).
    captured = []
    prov = brain._openai_provider(brain._by_id["openai"])
    prov._client = _FakeOpenAI([
        _FakeStream([_chunk(
            tool_calls=[_tc(0, id="c1", name="action_domotique",
                            args='{"operation":"allumer","zone":"salon"}')],
            finish="tool_calls")]),
        _FakeStream([_chunk(content="Le salon est allumé."), _chunk(finish="stop")]),
    ], captured)

    text = await _drain(brain.stream_reply(
        [{"role": "user", "content": "allume le salon"}], utterance="allume le salon",
        source="text", speaker=OWNER,
    ))
    await store.close()

    assert "salon" in text
    # La lumière du salon a bien été allumée — via le moteur (service générique
    # homeassistant.turn_on ciblant light.salon), jamais en direct.
    turn_ons = [c for c in calls if c[0] == "homeassistant" and c[1] == "turn_on"]
    assert turn_ons, f"aucune écriture Nova enregistrée : {calls}"
    targeted = [e for c in turn_ons for e in ((c[3] or {}).get("entity_id") or [])]
    assert "light.salon" in targeted
