"""L1 — le provider Claude : discipline de cache, boucle d'outils, erreurs.

Aucun appel réseau : le client du SDK est remplacé par une doublure qui
enregistre exactement ce qu'on lui a passé.

Le test central est `TestDisciplineDeCache` : c'est le seul garde-fou contre la
régression de coût la plus sournoise du projet — une variable glissée dans le
bloc système, et le cache ne prend plus jamais, sans que rien ne le signale.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

# Le SDK Anthropic construit ses exceptions autour de son client HTTP ; le nom du
# paquet a bougé entre versions, d'où les deux tentatives.
try:  # pragma: no cover - dépend de la version installée
    import httpx2 as _http
except ModuleNotFoundError:  # pragma: no cover
    import httpx as _http

from luna.kernel.errors import (
    CerveauIndisponible,
    CreditEpuise,
    RefusDuModele,
    TropDeRequetes,
)
from luna.kernel.schemas import (
    CerveauDelta,
    CerveauOutilDebut,
    CerveauTermine,
    ResultatOutil,
)
from luna.providers.claude import MAX_TOURS_OUTILS, CerveauClaude


class FauxFlux:
    def __init__(self, morceaux: list[str], final: Any) -> None:
        self._morceaux = morceaux
        self._final = final

    async def __aenter__(self) -> FauxFlux:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    @property
    def text_stream(self):
        async def generer():
            for morceau in self._morceaux:
                yield morceau

        return generer()

    async def get_final_message(self) -> Any:
        return self._final


def _usage(**kw: int) -> SimpleNamespace:
    base = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    return SimpleNamespace(**{**base, **kw})


def _final(stop: str = "end_turn", contenu: list[Any] | None = None, **usage_kw):
    return SimpleNamespace(
        stop_reason=stop, content=contenu or [], usage=_usage(**usage_kw)
    )


class FauxMessages:
    """Rejoue une liste de réponses et retient chaque appel."""

    def __init__(self, reponses: list[tuple[list[str], Any]]) -> None:
        self._reponses = reponses
        self.appels: list[dict[str, Any]] = []
        self.leve: Exception | None = None

    def stream(self, **kwargs: Any) -> FauxFlux:
        self.appels.append(kwargs)
        if self.leve is not None:
            raise self.leve
        indice = min(len(self.appels) - 1, len(self._reponses) - 1)
        morceaux, final = self._reponses[indice]
        return FauxFlux(morceaux, final)


def cerveau_avec(
    reponses: list[tuple[list[str], Any]],
) -> tuple[CerveauClaude, FauxMessages]:
    cerveau = CerveauClaude("sk-test", "claude-sonnet-5", "low")
    faux = FauxMessages(reponses)
    cerveau._client = SimpleNamespace(messages=faux)  # type: ignore[assignment]
    return cerveau, faux


async def _rien(nom: str, entree: dict[str, Any]) -> ResultatOutil:
    return ResultatOutil(contenu="ok")


async def _collecter(cerveau, **kw):
    defaut = {
        "historique": [{"role": "user", "content": "salut"}],
        "contexte": "Contexte : mardi.",
        "outils": [],
        "executer_outil": _rien,
    }
    return [e async for e in cerveau.repondre(**{**defaut, **kw})]


class TestDisciplineDeCache:
    async def test_un_seul_point_de_rupture_et_il_est_sur_le_bloc_fige(self):
        cerveau, faux = cerveau_avec([(["ok"], _final())])
        await _collecter(cerveau)

        systeme = faux.appels[0]["system"]
        assert len(systeme) == 2
        assert systeme[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in systeme[1]

    async def test_le_bloc_mis_en_cache_est_identique_dun_appel_a_lautre(self):
        """Le vrai test de non-régression : deux contextes différents, un seul
        préfixe. Un octet de différence et le cache tombe à zéro."""
        cerveau, faux = cerveau_avec([(["a"], _final())])
        await _collecter(cerveau, contexte="Contexte : mardi 8 septembre, 20 h 31.")
        await _collecter(cerveau, contexte="Contexte : mercredi 9 septembre, 7 h 02.")

        premier, second = faux.appels[0]["system"], faux.appels[1]["system"]
        assert premier[0]["text"] == second[0]["text"]
        assert premier[1]["text"] != second[1]["text"], "le contexte doit bien varier"

    async def test_rien_de_variable_dans_le_bloc_fige(self):
        from luna.providers.claude import PROMPT_SYSTEME

        for indice in ("2026", "20 h", "Contexte :", "Profil actif", "septembre"):
            assert indice not in PROMPT_SYSTEME, (
                f"« {indice} » dans le bloc mis en cache : il ne prendra jamais"
            )

    async def test_lordre_des_outils_est_stable(self):
        """L'ordre de rendu est tools → system → messages : un outil qui bouge
        invalide tout le préfixe."""
        from luna.engine.tools import OUTILS

        cerveau, faux = cerveau_avec([(["a"], _final())])
        await _collecter(cerveau, outils=OUTILS)
        await _collecter(cerveau, outils=OUTILS)

        def noms(appel: dict[str, Any]) -> list[str]:
            return [outil["name"] for outil in appel["tools"]]

        assert noms(faux.appels[0]) == noms(faux.appels[1])
        assert noms(faux.appels[0])[0] == "lister_pieces"
        assert noms(faux.appels[0]) == [o["name"] for o in OUTILS]


class TestParametres:
    async def test_reflexion_adaptative_et_effort(self):
        cerveau, faux = cerveau_avec([(["a"], _final())])
        await _collecter(cerveau)
        appel = faux.appels[0]
        assert appel["thinking"] == {"type": "adaptive"}
        assert appel["output_config"] == {"effort": "low"}
        assert appel["model"] == "claude-sonnet-5"

    async def test_aucun_parametre_rejete_par_le_modele(self):
        """`temperature`, `top_p` et `budget_tokens` renvoient un 400 sur
        claude-sonnet-5. Mieux vaut ne jamais les écrire."""
        cerveau, faux = cerveau_avec([(["a"], _final())])
        await _collecter(cerveau)
        appel = faux.appels[0]
        for interdit in ("temperature", "top_p", "top_k", "budget_tokens"):
            assert interdit not in appel


class TestBoucleDOutils:
    async def test_un_tour_doutil_puis_reponse(self):
        outil = SimpleNamespace(
            type="tool_use", id="tu_1", name="lister_pieces", input={}
        )
        cerveau, _ = cerveau_avec(
            [
                ([], _final("tool_use", [outil])),
                (["Il y a deux pièces."], _final("end_turn")),
            ]
        )
        appels_outil: list[str] = []

        async def executer(nom: str, entree: dict[str, Any]) -> ResultatOutil:
            appels_outil.append(nom)
            return ResultatOutil(contenu="Séjour, Cuisine")

        evenements = await _collecter(cerveau, executer_outil=executer)

        assert appels_outil == ["lister_pieces"]
        assert any(isinstance(e, CerveauOutilDebut) for e in evenements)
        assert isinstance(evenements[-1], CerveauTermine)
        assert evenements[-1].text == "Il y a deux pièces."

    async def test_les_resultats_paralleles_repartent_en_un_seul_message(self):
        """Les éclater apprend au modèle à ne plus paralléliser."""
        outils = [
            SimpleNamespace(type="tool_use", id=f"tu_{i}", name="lister_pieces", input={})
            for i in range(3)
        ]
        cerveau, faux = cerveau_avec(
            [([], _final("tool_use", outils)), (["fini"], _final("end_turn"))]
        )
        await _collecter(cerveau)
        messages = faux.appels[1]["messages"]
        resultats = [m for m in messages if m["role"] == "user"][-1]
        assert isinstance(resultats["content"], list)
        assert len(resultats["content"]) == 3
        assert all(b["type"] == "tool_result" for b in resultats["content"])

    async def test_plafond_de_tours(self):
        outil = SimpleNamespace(type="tool_use", id="tu", name="lister_pieces", input={})
        cerveau, faux = cerveau_avec([([], _final("tool_use", [outil]))])
        evenements = await _collecter(cerveau)

        assert len(faux.appels) == MAX_TOURS_OUTILS
        assert isinstance(evenements[-1], CerveauTermine)
        assert "perdue" in evenements[-1].text

    async def test_lusage_est_cumule_sur_tous_les_tours(self):
        outil = SimpleNamespace(type="tool_use", id="tu", name="lister_pieces", input={})
        cerveau, _ = cerveau_avec(
            [
                (
                    [],
                    _final(
                        "tool_use", [outil], input_tokens=100, cache_read_input_tokens=80
                    ),
                ),
                (
                    ["fini"],
                    _final("end_turn", input_tokens=120, cache_read_input_tokens=100),
                ),
            ]
        )
        evenements = await _collecter(cerveau)
        usage = evenements[-1].usage
        assert usage.input_tokens == 220
        assert usage.cache_read_input_tokens == 180


class TestErreurs:
    @staticmethod
    def _statut(code: int, message: str) -> anthropic.APIStatusError:
        reponse = _http.Response(
            code, request=_http.Request("POST", "https://api.anthropic.com/v1/messages")
        )
        return anthropic.APIStatusError(
            "erreur", response=reponse, body={"error": {"message": message}}
        )

    async def test_credit_epuise(self):
        cerveau, faux = cerveau_avec([([], _final())])
        faux.leve = self._statut(400, "Your credit balance is too low")
        with pytest.raises(CreditEpuise) as info:
            await _collecter(cerveau)
        assert "console.anthropic.com" in info.value.message

    async def test_le_detail_de_lapi_est_conserve(self):
        cerveau, faux = cerveau_avec([([], _final())])
        faux.leve = self._statut(400, "messages.0: unexpected role")
        with pytest.raises(CerveauIndisponible) as info:
            await _collecter(cerveau)
        assert "unexpected role" in info.value.message
        assert "400" in info.value.message

    async def test_trop_de_requetes(self):
        cerveau, faux = cerveau_avec([([], _final())])
        reponse = _http.Response(429, request=_http.Request("POST", "https://x"))
        faux.leve = anthropic.RateLimitError("429", response=reponse, body=None)
        with pytest.raises(TropDeRequetes):
            await _collecter(cerveau)

    async def test_panne_de_connexion(self):
        cerveau, faux = cerveau_avec([([], _final())])
        faux.leve = anthropic.APIConnectionError(
            request=_http.Request("POST", "https://x")
        )
        with pytest.raises(CerveauIndisponible):
            await _collecter(cerveau)

    async def test_refus_du_modele(self):
        """`stop_reason: "refusal"` renvoie un 200 : sans ce test, Luna
        paraîtrait muette sans raison."""
        cerveau, _ = cerveau_avec([([], _final("refusal"))])
        with pytest.raises(RefusDuModele):
            await _collecter(cerveau)


class TestStreaming:
    async def test_les_deltas_sortent_au_fil_de_leau(self):
        cerveau, _ = cerveau_avec([(["Bon", "jour", " Guillaume."], _final())])
        evenements = await _collecter(cerveau)
        deltas = [e.text for e in evenements if isinstance(e, CerveauDelta)]
        assert deltas == ["Bon", "jour", " Guillaume."]
        assert evenements[-1].text == "Bonjour Guillaume."
