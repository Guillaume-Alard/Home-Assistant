"""La politique d'auto-amélioration encadrée (Phase 6) : ce que Luna a le droit
de PROPOSER sur son propre code / sa configuration — et, surtout, ce qu'elle ne
peut JAMAIS proposer.

Principe non négociable : Luna ne modifie jamais rien elle-même. Elle produit un
*diff* qui devient une proposition, relue et appliquée par Guillaume (revue
humaine avant toute exécution — jamais contournée). Ce module ne fait qu'*évaluer*
un diff : il ne l'applique pas, n'écrit aucun fichier, ne lance aucun processus.

`evaluate(diff)` renvoie un `Verdict` :
  - REFUS si le diff touche un GARDE-FOU de sécurité (niveaux de confiance, moteur
    d'actions, isolation de l'atelier, secrets, la politique elle-même, les
    tests-verrous…), s'il introduit un secret en dur, ou s'il modifie une ligne
    de logique de sécurité (motifs surveillés) ;
  - AUTORISÉ sinon — la proposition part en revue humaine.

Le refus est le comportement sûr par défaut : en cas de doute, on refuse, et
Guillaume peut toujours faire le changement lui-même à la main.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Fichiers-garde-fous : intouchables par une proposition de Luna ────────────
#
# Comparaison par SUFFIXE de chemin : « core/app/identity.py » et « app/identity.py »
# désignent le même fichier (le dépôt préfixe par « core/ », le conteneur non).
_PROTECTED_SUFFIXES = (
    "app/identity.py",            # définition des niveaux de confiance
    "app/actions/engine.py",      # « propose puis approuve » (le cœur)
    "app/actions/executors.py",   # seuls écrivains autorisés vers le monde réel
    "app/ha/client.py",           # définition de call_service (écriture Nova)
    "healthcheck.py",             # sonde TLS
)

# Sous-arbres entiers qui SONT des garde-fous : le moteur d'actions et la
# politique d'auto-amélioration elle-même.
_PROTECTED_MARKERS = (
    "app/actions/",
    "app/selfmod/",
    "data/certs/",        # certificats TLS
    ".github/",           # chaîne d'intégration
)

# Fichiers d'infrastructure sensibles (isolation, déploiement, tests-verrous).
_PROTECTED_NAMES = (
    "docker-compose.yml",
    "dockerfile",             # comparé en minuscules
    "entrypoint.sh",
    "test_invariant.py",
    "test_selfmod.py",
)


def _is_secret_file(path: str) -> bool:
    """Un fichier qui contient (ou pourrait contenir) des secrets — jamais proposable.

    `.env` (secrets réels) est refusé, mais `.env.example` (le modèle, sans
    secret) reste autorisé : c'est le support d'une proposition de configuration.
    """
    base = path.rsplit("/", 1)[-1].lower()
    if base == ".env" or base.startswith(".env."):
        return not (base.endswith(".example") or base.endswith(".sample") or base.endswith(".template"))
    if base.endswith((".pem", ".key", ".p12", ".pfx", ".crt", ".cer")):
        return True
    return any(tok in base for tok in ("secret", "credential", "password"))


def is_protected_path(path: str) -> str | None:
    """Renvoie la RAISON du refus si `path` est un garde-fou, sinon None."""
    p = path.strip()
    if p.startswith("./"):  # préfixe « ./ » seulement — ne pas manger le point de « .env »
        p = p[2:]
    low = p.lower()
    base = low.rsplit("/", 1)[-1]
    if _is_secret_file(p):
        return f"« {path} » peut contenir des secrets — jamais proposable."
    if any(low.endswith(s) for s in _PROTECTED_SUFFIXES):
        return f"« {path} » est un garde-fou de sécurité — jamais modifiable par une proposition."
    if any(m in low for m in _PROTECTED_MARKERS):
        return f"« {path} » fait partie d'un garde-fou (moteur d'actions, secrets, isolation ou politique) — intouchable."
    if base in _PROTECTED_NAMES:
        return f"« {path} » relève de l'isolation/du déploiement — intouchable par une proposition."
    return None


# ── Motifs de LOGIQUE de sécurité : intouchables même dans un fichier autorisé ─
#
# Ne s'appliquent qu'aux lignes AJOUTÉES d'un fichier Python (`.py`) : la logique
# des garde-fous vit dans le code. Une doc qui *mentionne* ces mots reste libre.
_GUARDRAIL_TOKENS = (
    "_tool_level",        # table de gating des outils
    "is_owner",           # niveau propriétaire
    "can_act",            # « personne reconnue »
    "call_service",       # écriture Nova
    "_send_wait",         # canal brut Nova
    "start_task",         # écriture atelier
    "push_branch",        # push GitHub
    "restart_container",  # écriture Docker
    "_assist_guard",      # jeton de l'API Assist
    "protected_",         # les listes de protection de CE module
    "content-security-policy",
    "_page_csp",
)
# Une entrée de gating (« "resume_mails": "owner" ») n'utilise pas forcément le
# mot _TOOL_LEVEL : on refuse aussi toute ligne qui attribue un niveau de confiance.
_TRUST_ASSIGN = re.compile(r'["\']\s*:\s*["\'](owner|known)["\']')


def _guardrail_in_line(body: str) -> str | None:
    low = body.lower()
    for tok in _GUARDRAIL_TOKENS:
        if tok in low:
            return tok
    if _TRUST_ASSIGN.search(body):
        return "attribution d'un niveau de confiance"
    if 'via != "ui"' in body or "via != 'ui'" in body or 'via!="ui"' in body:
        return 'via != "ui"'
    return None


# ── Détection d'un secret introduit « en dur » ───────────────────────────────
#
# Motifs à FORT signal (peu de faux positifs) : clés API/jetons reconnaissables.
_SECRET_PATTERNS = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"),      # clé Anthropic
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),             # clé style OpenAI
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),            # jeton GitHub (classic)
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),    # jeton GitHub (fine-grained)
    re.compile(r"\bgho_[A-Za-z0-9]{20,}"),            # OAuth GitHub
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),    # jeton Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),              # clé d'accès AWS
)
_SECRET_LITERAL = re.compile(
    r"(?i)['\"]?\b(api[_-]?key|secret|token|password|passwd|refresh[_-]?token|"
    r"client[_-]?secret|authorization|bearer)\b['\"]?\s*[:=]\s*(.+)$"
)
_PLACEHOLDER = re.compile(
    r"""(?ix)^\s*['"]?\s*($|<|\{|your|xxx+|change[_-]?me|placeholder|example|
    todo|\.\.\.|none\b|null\b|""\s*$|''\s*$)"""
)
# Une vraie valeur de secret : longue chaîne « aléatoire » de caractères de jeton.
_TOKENISH = re.compile(r"[A-Za-z0-9_\-+/=]{20,}")
# Références de code légitimes (pas des secrets en dur) : os.environ, settings.…
_CODE_REF = re.compile(r"(?i)\b(os\.|environ|getenv|settings\.|self\.|config\.|process\.env)")


_WRAP = " \t{}[](),;"


def _looks_like_secret_value(value: str) -> bool:
    v = value.strip().strip(_WRAP)
    if not v or _PLACEHOLDER.match(v):
        return False
    if _CODE_REF.search(v):
        return False  # api_key=os.environ.get(...) : légitime
    inner = v.strip("\"'").strip(_WRAP)
    if inner.lower().startswith("bearer "):
        inner = inner[7:].strip().strip("\"'").strip(_WRAP)
    return bool(_TOKENISH.fullmatch(inner))


def _secret_in_line(body: str) -> bool:
    if "BEGIN" in body and "PRIVATE KEY" in body:
        return True
    if any(pat.search(body) for pat in _SECRET_PATTERNS):
        return True
    m = _SECRET_LITERAL.search(body)
    if m and _looks_like_secret_value(m.group(2)):
        return True
    return False


# ── Configuration : liste blanche des réglages ajustables ────────────────────
#
# Une proposition « config » ne touche QUE .env.example, et QUE ces clés (des
# réglages de confort/coût). Tout jeton, secret ou clé de sécurité (TLS, dépôts
# de l'atelier…) en est exclu : ils ne sont pas ajustables par une proposition.
ALLOWED_CONFIG_KEYS = frozenset({
    "SENTINEL_MODEL", "SENTINEL_MAX_TOKENS", "SENTINEL_EFFORT", "SENTINEL_HISTORY_WINDOW",
    "SENTINEL_MEMORY", "SENTINEL_MEMORY_WINDOW",
    "SENTINEL_WEB_SEARCH", "SENTINEL_WEB_SEARCH_MAX",
    "SPEAKER_THRESHOLD", "MAIL_MAX",
    "WHISPER_MODEL", "PIPER_VOICE", "TTS_ENGINE", "CLONED_TTS_VOICE",
    "WAKEWORD_MODEL", "SENTINEL_DAILY_REPORT", "SENTINEL_CONTAINER_MEM_MO",
    "LOG_LEVEL", "TZ", "SENTINEL_SELF_IMPROVE",
})
_ENV_KEY = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=")


def _norm_path(raw: str) -> str:
    """En-tête de diff → chemin propre (« a/core/app/x.py\\t… » → « core/app/x.py »)."""
    token = raw.strip().split("\t", 1)[0].strip()
    if not token or token == "/dev/null":
        return ""
    if token.startswith(("a/", "b/")):
        token = token[2:]
    return token


def _touched_paths(diff: str) -> list[str]:
    paths: list[str] = []
    for line in diff.splitlines():
        if line.startswith(("+++ ", "--- ")):
            p = _norm_path(line[4:])
        elif line.startswith("diff --git "):
            parts = line.split()
            p = _norm_path(parts[-1]) if len(parts) >= 4 else ""
        else:
            continue
        if p and p not in paths:
            paths.append(p)
    return paths


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = ""
    paths: list[str] = field(default_factory=list)


def evaluate(diff: str, *, kind: str = "code") -> Verdict:
    """Évalue un diff proposé. N'APPLIQUE RIEN : renvoie seulement un verdict."""
    diff = diff or ""
    paths = _touched_paths(diff)
    if not paths:
        return Verdict(False, "Je n'ai repéré aucun fichier : donne un vrai diff unifié (avec les en-têtes --- / +++).")

    # 1) Aucun chemin ne doit être un garde-fou.
    for p in paths:
        reason = is_protected_path(p)
        if reason:
            return Verdict(False, reason, paths)

    # 2) Analyse ligne à ligne : secrets en dur, logique de sécurité, config.
    current = ""
    is_config_diff = False
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = _norm_path(line[4:]) or current
            continue
        if line.startswith(("--- ", "diff --git ", "@@", "index ")):
            continue
        base = current.rsplit("/", 1)[-1].lower()
        touches_env_example = base.startswith(".env")

        if line.startswith("+") and not line.startswith("+++"):
            body = line[1:]
            if _secret_in_line(body):
                return Verdict(False, "Ce diff introduit ce qui ressemble à un secret en dur — refusé. Les secrets restent dans .env (hors du dépôt).", paths)
            if current.lower().endswith(".py"):
                pat = _guardrail_in_line(body)
                if pat:
                    return Verdict(False, f"Ce diff modifie un garde-fou de sécurité (motif « {pat} ») — c'est justement ce que je ne peux pas proposer.", paths)

        # Configuration : seules des clés de la liste blanche, dans .env.example.
        if touches_env_example and line[:1] in "+-" and not line.startswith(("+++", "---")):
            m = _ENV_KEY.match(line[1:])
            if m:
                is_config_diff = True
                key = m.group(1)
                if key not in ALLOWED_CONFIG_KEYS:
                    return Verdict(False, f"Le réglage « {key} » n'est pas ajustable par une proposition (clé de sécurité ou hors liste blanche).", paths)

    # 3) Cohérence du type annoncé.
    if kind == "config" and not is_config_diff:
        return Verdict(False, "Une proposition de configuration doit modifier .env.example (une ligne CLE=valeur).", paths)

    return Verdict(True, "", paths)


def summarize(diff: str) -> dict:
    """Petit résumé pour l'affichage (fichiers touchés, lignes +/-)."""
    added = removed = 0
    for line in (diff or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"paths": _touched_paths(diff), "added": added, "removed": removed}
