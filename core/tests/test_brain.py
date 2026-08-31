"""Traduction des erreurs API et garde-fous de l'historique envoyé au modèle."""

from app.brain.llm import _api_error_message
from app.main import _build_history


def test_erreur_api_avec_detail():
    body = {"error": {"type": "invalid_request_error", "message": "messages: bad thing"}}
    out = _api_error_message(400, body)
    assert "(400)" in out
    assert "messages: bad thing" in out


def test_erreur_api_credit_epuise():
    body = {"error": {"type": "invalid_request_error",
                      "message": "Your credit balance is too low to access the Anthropic API."}}
    out = _api_error_message(400, body)
    assert "crédit" in out
    assert "console.anthropic.com" in out


def test_erreur_api_sans_corps():
    assert _api_error_message(500, None) == "L'API Anthropic a renvoyé une erreur (500)."
    assert _api_error_message(400, "pas un dict") == "L'API Anthropic a renvoyé une erreur (400)."


def _rec(role, content):
    return {"role": role, "content": content}


def test_build_history_filtre_et_borne():
    records = [
        _rec("assistant", "orphelin en tête"),   # retiré : premier ≠ user
        _rec("user", "salut"),
        _rec("assistant", "   "),                # retiré : contenu vide
        _rec("assistant", "réponse"),
        _rec("user", "question"),
    ]
    history = _build_history(records)
    assert history == [
        {"role": "user", "content": "salut"},
        {"role": "assistant", "content": "réponse"},
        {"role": "user", "content": "question"},
    ]


def test_build_history_retire_un_assistant_final():
    # Un dernier message assistant serait un « prefill » → 400 sur les modèles récents
    records = [_rec("user", "salut"), _rec("assistant", "réponse")]
    assert _build_history(records) == [{"role": "user", "content": "salut"}]


def test_build_history_vide():
    assert _build_history([]) == []
    assert _build_history([_rec("assistant", "seul")]) == []
