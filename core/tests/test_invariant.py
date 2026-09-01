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
