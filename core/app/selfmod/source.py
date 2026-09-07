"""Lecture SEULE du propre code de Luna (Phase 6).

Pour proposer un diff juste, Luna doit pouvoir relire ses fichiers source. Ce
lecteur ne sert QUE les arbres `app/` (le cerveau) et `ui/` (l'interface) —
jamais `.env`, jamais un secret, jamais hors de ces racines. Aucune écriture :
c'est un lecteur, rien d'autre.
"""

from __future__ import annotations

from pathlib import Path

from .policy import _is_secret_file

# Extensions montrables (code source lisible, pas de binaire).
_READABLE = (".py", ".js", ".css", ".html", ".md", ".yml", ".yaml", ".txt", ".json")
_MAX_BYTES = 8000
_MAX_LISTING = 400


class SelfSource:
    def __init__(self, app_dir: Path, ui_dir: Path):
        # Racines réelles sur le disque du conteneur : /opt/sentinel/app et /ui.
        self._roots = {
            "app": app_dir.resolve(),
            "ui": ui_dir.resolve(),
        }

    def _resolve(self, relpath: str) -> Path | None:
        """Chemin demandé → fichier réel, ou None si hors des racines autorisées.

        Accepte les formes du dépôt ou du conteneur : « core/app/identity.py »,
        « app/identity.py » ou « identity.py » ; « ui/js/app.js » ou « js/app.js ».
        """
        rel = (relpath or "").strip().lstrip("/").replace("\\", "/")
        if not rel or ".." in rel.split("/"):
            return None
        if rel.startswith("core/"):
            rel = rel[len("core/"):]
        if rel.startswith("ui/"):
            root, sub = self._roots["ui"], rel[len("ui/"):]
        elif rel.startswith("app/"):
            root, sub = self._roots["app"], rel[len("app/"):]
        else:
            root, sub = self._roots["app"], rel  # défaut : relatif à app/
        try:
            target = (root / sub).resolve()
            target.relative_to(root)  # anti-traversée : doit rester sous la racine
        except (ValueError, OSError):
            return None
        return target

    def read(self, relpath: str) -> tuple[str, bool]:
        """Renvoie (contenu, is_error). Refuse les secrets et le hors-racine."""
        if _is_secret_file(relpath):
            return "Je ne lis jamais un fichier de secrets.", True
        target = self._resolve(relpath)
        if target is None:
            return "Chemin hors de mon code source (app/ ou ui/ uniquement).", True
        if target.suffix.lower() not in _READABLE or not target.is_file():
            return "Fichier introuvable dans mon code source.", True
        try:
            data = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "Lecture impossible.", True
        if len(data) > _MAX_BYTES:
            data = data[:_MAX_BYTES] + "\n… (tronqué)"
        return data, False

    def listing(self) -> list[str]:
        """Liste des fichiers source lisibles (chemins relatifs, style dépôt)."""
        out: list[str] = []
        for name, root in self._roots.items():
            prefix = "core/app" if name == "app" else "ui"
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in _READABLE:
                    continue
                if "__pycache__" in path.parts:
                    continue
                out.append(f"{prefix}/{path.relative_to(root).as_posix()}")
                if len(out) >= _MAX_LISTING:
                    return out
        return out
