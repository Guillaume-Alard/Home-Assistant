"""Point d'entrée de l'add-on : `python -m luna.interfaces`.

L'arrêt propre est géré dans `app.servir` (SIGTERM du Supervisor, SIGINT en
développement) : le conteneur doit pouvoir s'arrêter sans être tué de force.
"""

from __future__ import annotations

import asyncio
import contextlib

from .app import servir

if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(servir())
