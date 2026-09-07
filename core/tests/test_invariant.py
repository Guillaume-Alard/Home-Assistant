"""L'invariant de sécurité, verrouillé statiquement : AUCUNE écriture hors moteur.

Toute écriture vers Nova passe par `call_service`, défini dans ha/client.py et
appelé uniquement par les exécuteurs du moteur d'actions. Si un futur module
tente d'écrire directement, ces tests cassent la CI.
"""

from __future__ import annotations

from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Seuls fichiers autorisés à APPELER une écriture Nova
CALLERS_AUTORISES = {"actions/executors.py"}
# Seul fichier autorisé à la DÉFINIR (et à l'utiliser en interne)
DEFINITION_AUTORISEE = {"ha/client.py"}


def _python_files():
    return sorted(APP_DIR.rglob("*.py"))


def test_call_service_uniquement_dans_les_executeurs():
    offenders = []
    for path in _python_files():
        rel = path.relative_to(APP_DIR).as_posix()
        if rel in CALLERS_AUTORISES or rel in DEFINITION_AUTORISEE:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ".call_service(" in line:
                offenders.append(f"{rel}:{i}")
    assert not offenders, (
        "Écriture Nova hors du moteur d'actions (interdit) : " + ", ".join(offenders)
    )


def test_definition_call_service_uniquement_dans_le_client():
    offenders = []
    for path in _python_files():
        rel = path.relative_to(APP_DIR).as_posix()
        if rel in DEFINITION_AUTORISEE:
            continue
        src = path.read_text(encoding="utf-8")
        if "def call_service(" in src:
            offenders.append(rel)
    assert not offenders, "call_service redéfini hors ha/client.py : " + ", ".join(offenders)


def test_pas_de_contournement_du_protocole_ws_nova():
    """`_send_wait` (canal brut vers Nova) ne doit jamais fuiter hors du client."""
    offenders = []
    for path in _python_files():
        rel = path.relative_to(APP_DIR).as_posix()
        if rel in DEFINITION_AUTORISEE:
            continue
        src = path.read_text(encoding="utf-8")
        if "_send_wait(" in src:
            offenders.append(rel)
    assert not offenders, "_send_wait utilisé hors ha/client.py : " + ", ".join(offenders)


def test_les_outils_llm_n_importent_pas_le_client_nova_en_ecriture():
    """La toolbox ne reçoit le client que pour la LECTURE ; les écritures passent
    par ActionEngine. On vérifie qu'elle n'appelle aucune méthode d'écriture."""
    toolbox = (APP_DIR / "brain" / "toolbox.py").read_text(encoding="utf-8")
    assert ".call_service(" not in toolbox
    assert "_send_wait(" not in toolbox
    assert ".restart_container(" not in toolbox


def test_ecritures_worker_uniquement_dans_les_executeurs():
    """start_task (workspace) et push_branch (GitHub) suivent la même règle :
    appelables uniquement par les exécuteurs, définis dans le client worker."""
    for path in _python_files():
        rel = path.relative_to(APP_DIR).as_posix()
        src = path.read_text(encoding="utf-8")
        for needle in (".start_task(", ".push_branch("):
            if needle in src and rel not in ("actions/executors.py", "devwork/worker_client.py"):
                raise AssertionError(f"écriture worker hors du moteur d'actions : {rel} ({needle})")
        for needle in ("def start_task(", "def push_branch("):
            if needle in src and rel != "devwork/worker_client.py":
                raise AssertionError(f"{needle.removeprefix('def ')} redéfini hors devwork/worker_client.py : {rel}")


def test_restart_docker_uniquement_dans_les_executeurs():
    """Le redémarrage de conteneur (écriture Docker) suit la même règle que Nova :
    appelable uniquement par les exécuteurs, défini uniquement dans le moniteur."""
    for path in _python_files():
        rel = path.relative_to(APP_DIR).as_posix()
        src = path.read_text(encoding="utf-8")
        if ".restart_container(" in src and rel not in ("actions/executors.py", "monitors/docker.py"):
            raise AssertionError(f"écriture Docker hors du moteur d'actions : {rel}")
        if "def restart_container(" in src and rel != "monitors/docker.py":
            raise AssertionError(f"restart_container redéfini hors monitors/docker.py : {rel}")


# ── Auto-amélioration encadrée (Phase 6) : verrous statiques ─────────────────
#
# Luna PROPOSE des diffs ; elle n'en applique JAMAIS aucun. On le prouve
# statiquement : le paquet selfmod (et le flux d'auto-amélioration) n'écrit sur
# aucun fichier, ne lance aucun processus, n'applique aucun patch.

SELFMOD_DIR = APP_DIR / "selfmod"

# Jamais d'écriture ni d'exécution dans le paquet selfmod (lecture pure).
_SELFMOD_INTERDITS = (
    "open(", "subprocess", "os.system", "os.popen", "Popen(",
    ".write_text(", ".write_bytes(", ".write(", "shutil.",
    "os.remove", "os.unlink", ".unlink(", "git apply",
)


def test_selfmod_n_applique_jamais_rien():
    """Le paquet selfmod ne fait que LIRE et évaluer : aucune écriture/exécution."""
    offenders = []
    for path in sorted(SELFMOD_DIR.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        for needle in _SELFMOD_INTERDITS:
            if needle in src:
                offenders.append(f"{path.name} contient « {needle} »")
    assert not offenders, (
        "selfmod doit rester en lecture pure (jamais d'exécution automatique) : "
        + ", ".join(offenders)
    )


def test_aucune_application_de_diff_dans_le_flux():
    """Le flux d'auto-amélioration (toolbox, main, selfmod) n'applique aucun diff :
    ni « git apply », ni patch, ni processus — l'application reste manuelle."""
    concernes = [
        APP_DIR / "brain" / "toolbox.py",
        APP_DIR / "main.py",
        *SELFMOD_DIR.glob("*.py"),
    ]
    for path in concernes:
        src = path.read_text(encoding="utf-8")
        for needle in ("git apply", "subprocess", "os.system", "Popen("):
            assert needle not in src, f"{path.name} ne doit jamais {needle} (pas d'exécution auto)"


def test_la_politique_protege_tous_les_garde_fous():
    """`is_protected_path` doit refuser chaque fichier-garde-fou de sécurité.
    Ajouter un garde-fou au dépôt sans l'inscrire dans la politique casse ce test."""
    from app.selfmod import is_protected_path

    garde_fous = [
        "core/app/identity.py",
        "core/app/actions/engine.py",
        "core/app/actions/executors.py",
        "core/app/ha/client.py",
        "core/app/monitors/docker.py",
        "core/app/mail/client.py",
        "core/app/mail/authorize.py",
        "core/app/selfmod/policy.py",
        "core/app/selfmod/source.py",
        "worker/app.py",
        "docker-compose.yml",
        "core/tests/test_invariant.py",
        ".env",
    ]
    manquants = [p for p in garde_fous if is_protected_path(p) is None]
    assert not manquants, "Garde-fous non protégés par la politique : " + ", ".join(manquants)


def test_proposer_evolution_passe_par_la_politique():
    """L'outil de proposition doit router le diff par la politique (jamais le stocker
    sans l'évaluer)."""
    toolbox = (APP_DIR / "brain" / "toolbox.py").read_text(encoding="utf-8")
    assert "evaluate_diff(" in toolbox, "proposer_evolution doit appeler la politique (evaluate)"
    # La proposition est bien conditionnée par le verdict.
    assert "if not verdict.allowed" in toolbox


# ── Proactivité contextuelle (Phase 7) : verrou statique ─────────────────────
#
# Luna observe et SUGGÈRE — jamais n'exécute. Le veilleur proactif ne peut que
# créer une PROPOSITION (propose), qu'un humain approuve ensuite ; il n'exécute
# aucune action de lui-même.

PROACTIVE_DIR = APP_DIR / "proactive"


def test_proactif_ne_declenche_jamais_une_action():
    """Le paquet proactive n'appelle jamais run_direct/run_system/decide : au mieux
    propose(). Aucune action ne part sans le double accord de Guillaume."""
    offenders = []
    for path in sorted(PROACTIVE_DIR.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        for needle in (".run_direct(", ".run_system(", ".decide(", ".call_service("):
            if needle in src:
                offenders.append(f"{path.name} contient « {needle} »")
    assert not offenders, (
        "La proactivité ne doit jamais exécuter — seulement proposer : " + ", ".join(offenders)
    )


def test_proactif_passe_par_propose():
    """L'escalade d'une suggestion en action se fait via le moteur de PROPOSITIONS."""
    engine = (PROACTIVE_DIR / "engine.py").read_text(encoding="utf-8")
    assert "_engine.propose(" in engine
