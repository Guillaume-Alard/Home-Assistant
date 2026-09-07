"""Auto-amélioration encadrée (Phase 6) : la politique refuse tout ce qui touche
un garde-fou de sécurité ; le lecteur de code ne sort jamais des racines source."""

from __future__ import annotations

from pathlib import Path

import app
from app.selfmod import SelfSource, evaluate, is_protected_path, summarize

APP_DIR = Path(app.__file__).resolve().parent


def _diff(path: str, added: list[str] | None = None, removed: list[str] | None = None) -> str:
    lines = [f"--- a/{path}", f"+++ b/{path}", "@@ -1,2 +1,2 @@"]
    for r in removed or []:
        lines.append(f"-{r}")
    for a in added or []:
        lines.append(f"+{a}")
    return "\n".join(lines) + "\n"


# ── Chemins-garde-fous refusés ───────────────────────────────────────────────

def test_refuse_les_fichiers_garde_fous():
    for path in (
        "core/app/identity.py",
        "app/identity.py",                 # forme conteneur
        "core/app/actions/engine.py",
        "core/app/actions/executors.py",
        "core/app/ha/client.py",
        "core/app/monitors/docker.py",
        "core/app/mail/client.py",
        "core/app/mail/authorize.py",
        "core/app/selfmod/policy.py",      # la politique ne se désarme pas elle-même
        "core/app/selfmod/source.py",
        "worker/app.py",                   # l'isolation de l'atelier
        "docker-compose.yml",
        "core/Dockerfile",
        "entrypoint.sh",
        "core/tests/test_invariant.py",    # les tests-verrous
        "core/tests/test_selfmod.py",
    ):
        v = evaluate(_diff(path, added=["x = 1"]))
        assert not v.allowed, f"aurait dû refuser {path}"
        assert v.reason


def test_refuse_les_fichiers_de_secrets():
    for path in (".env", "data/certs/sentinel.key", "config/service-credentials.json",
                 "keys/private.pem"):
        assert is_protected_path(path)
        assert not evaluate(_diff(path, added=["x"])).allowed


def test_autorise_le_modele_env_example():
    # .env.example (le modèle, sans secret) reste proposable
    assert is_protected_path(".env.example") is None
    assert is_protected_path("core/app/brain/toolbox.py") is None
    assert is_protected_path("docs/AUTO-AMELIORATION.md") is None
    assert is_protected_path("ui/js/app.js") is None


# ── Motifs de logique de sécurité (dans un .py autorisé) ─────────────────────

def test_refuse_les_motifs_de_gating_meme_dans_un_fichier_autorise():
    base = "core/app/brain/foo.py"
    for line in (
        '    _TOOL_LEVEL["resume_mails"] = "known"',
        '        "resume_mails": "known",',           # entrée de gating sans le mot-clé
        "    if who.is_owner:",
        "    if who.can_act:",
        '    await self._ha.call_service("lock", "unlock")',
        "    if via != \"ui\":",
        "    resp = _assist_guard(request)",
    ):
        v = evaluate(_diff(base, added=[line]))
        assert not v.allowed, f"aurait dû refuser : {line}"


def test_motifs_de_securite_ignores_hors_python():
    # Une doc (.md) qui MENTIONNE ces mots reste libre : la logique vit dans le code.
    d = _diff("docs/SECU.md", added=["La règle `via != \"ui\"` protège les serrures."])
    assert evaluate(d).allowed


def test_ajout_de_fonction_benin_autorise():
    d = _diff("core/app/brain/toolbox.py", added=[
        "    async def _tool_meteo(self, args, _utt, _src):",
        "        return 'ensoleillé', False",
    ])
    assert evaluate(d).allowed


# ── Secrets en dur ───────────────────────────────────────────────────────────

def test_refuse_un_secret_en_dur():
    for line in (
        'API_KEY = "sk-ant-api03-abcdefghijklmnop1234567890"',
        'token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"',
        'GITHUB = "github_pat_11ABCDEFG0abcdefghij_KLMNOP"',
        "-----BEGIN RSA PRIVATE KEY-----",
        'headers = {"Authorization": "Bearer aZ3kQp9Xr2Lm7Vt0Yb4Nc6Wd8Se1Uf"}',
    ):
        assert not evaluate(_diff("core/app/brain/foo.py", added=[line])).allowed


def test_reference_env_legitime_pas_un_secret():
    for line in (
        '    api_key = os.environ.get("ANTHROPIC_API_KEY", "")',
        "    token = settings.gmail_refresh_token",
        '    key = ""  # placeholder',
        "    the_task = compute_risk_score()",   # « sk- » dans « risk » : pas un secret
    ):
        assert evaluate(_diff("core/app/brain/foo.py", added=[line])).allowed


# ── Configuration ────────────────────────────────────────────────────────────

def test_config_liste_blanche():
    ok = _diff(".env.example", removed=["SENTINEL_EFFORT=low"], added=["SENTINEL_EFFORT=medium"])
    assert evaluate(ok, kind="config").allowed

    bad_key = _diff(".env.example", added=["HA_TOKEN=xyz"])
    assert not evaluate(bad_key, kind="config").allowed
    bad_token = _diff(".env.example", added=["SENTINEL_ASSIST_TOKEN=abcdef"])
    assert not evaluate(bad_token, kind="config").allowed


def test_config_doit_toucher_env_example():
    d = _diff("core/app/config.py", added=["    x = 2"])
    assert not evaluate(d, kind="config").allowed  # kind=config mais pas de .env.example


# ── Diff invalide / vide ─────────────────────────────────────────────────────

def test_diff_vide_ou_illisible_refuse():
    assert not evaluate("").allowed
    assert not evaluate("juste du texte sans en-tête de diff").allowed


def test_summarize_compte_les_lignes():
    d = _diff("ui/css/main.css", added=["a", "b"], removed=["c"])
    s = summarize(d)
    assert s["added"] == 2 and s["removed"] == 1 and s["paths"] == ["ui/css/main.css"]


# ── Lecteur de code (seule lecture) ──────────────────────────────────────────

def test_source_lit_son_code_mais_pas_les_secrets(tmp_path):
    ui = tmp_path / "ui"
    (ui / "js").mkdir(parents=True)
    (ui / "js" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    src = SelfSource(APP_DIR, ui)

    content, err = src.read("core/app/identity.py")
    assert not err and "class Speaker" in content
    content, err = src.read("app/identity.py")  # forme conteneur
    assert not err and "Speaker" in content
    content, err = src.read("ui/js/app.js")
    assert not err and "console.log" in content

    # Hors racine / traversée / secret : refusé
    assert src.read("../../etc/passwd")[1]
    assert src.read("../secret.txt")[1]
    assert src.read(".env")[1]
    assert src.read("app/does_not_exist.py")[1]


def test_source_listing_liste_du_code_reel():
    src = SelfSource(APP_DIR, APP_DIR.parent / "ui")
    files = src.listing()
    assert any(f.endswith("identity.py") for f in files)
    assert all(".env" not in f for f in files)
