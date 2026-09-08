"""L3 — l'API HTTP interne de l'add-on.

Les routes littérales de §12 du cahier des charges. Consommées par l'intégration
et par `curl` en débogage, **jamais par le navigateur** (décision A5) : la carte
passe par les commandes WebSocket `luna/*`, jamais par `fetch()`.

Les routes des phases futures existent déjà et répondent `501 not_implemented`.
Documentées dès P1, comme l'exige §12 ; jamais un silence, comme l'exige §8.
"""

from __future__ import annotations

import time
from typing import Any

from aiohttp import web

from .. import __version__
from ..engine.orchestrator import Orchestrateur
from ..engine.veille import MoteurVeille
from ..kernel.contracts import MaisonProvider, MemoireProvider
from ..kernel.errors import LunaError
from .relay import Relais

DEMARRAGE = time.monotonic()

#: Route §12 → phase où elle prend vie.
ROUTES_FUTURES = {
    "identity_voice": "Cette capacité arrive en phase 3.",
    "identity_face": "Cette capacité arrive en phase 6.",
}


def _pas_encore(cle: str) -> web.Response:
    return web.json_response(
        {"code": "not_implemented", "message": ROUTES_FUTURES[cle]}, status=501
    )


def construire_app(
    *,
    orchestrateur: Orchestrateur,
    relais: Relais,
    maison: MaisonProvider,
    memoire: MemoireProvider,
    veille: MoteurVeille,
    modele: str,
) -> web.Application:
    app = web.Application()

    async def sante(_: web.Request) -> web.Response:
        derniere = await memoire.derniere_action()
        return web.json_response(
            {
                "status": "ok" if maison.connectee else "degraded",
                "version": __version__,
                "ha": maison.connectee,
                "claude": modele,
                "db": True,
                "uptime": round(time.monotonic() - DEMARRAGE, 1),
                "derniere_action": derniere.isoformat() if derniere else None,
            }
        )

    async def info(_: web.Request) -> web.Response:
        return web.json_response(await orchestrateur.info())

    # ── §12, à leur lettre ───────────────────────────────────────────────

    async def patterns(requete: web.Request) -> web.Response:
        """§12, à la lettre : `GET /profile/{user}/patterns`.

        `{user}` est un profil Luna, pas un utilisateur Home Assistant : c'est
        le profil qui porte les habitudes. `tous` les rend toutes.
        """
        profil = requete.match_info.get("user") or ""
        return web.json_response(
            {"patterns": await veille.patterns(None if profil == "tous" else profil)}
        )

    async def suggestions(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "suggestions": [
                    s.model_dump(mode="json") for s in await veille.suggestions()
                ]
            }
        )

    async def feedback(requete: web.Request) -> web.Response:
        """§12 : `POST /feedback` — `{suggestion_id, action}`.

        L'API interne n'a pas de session Home Assistant : elle enregistre un
        retour, elle n'agit jamais. « Agir » passe par le relais, donc par un
        contexte construit à partir de `connection.user`, donc par l'arbitre.
        """
        try:
            corps = await requete.json()
        except ValueError:
            return web.json_response(
                {"code": "bad_request", "message": "Corps JSON illisible."}, status=400
            )
        try:
            resultat = await veille.retour(
                str(corps.get("suggestion_id") or ""), str(corps.get("action") or "")
            )
        except LunaError as exc:
            return web.json_response(exc.charge_utile(), status=404)
        return web.json_response(resultat)

    async def identite_voix(_: web.Request) -> web.Response:
        return _pas_encore("identity_voice")

    async def identite_visage(_: web.Request) -> web.Response:
        return _pas_encore("identity_face")

    app.add_routes(
        [
            web.get("/health", sante),
            web.get("/info", info),
            web.get("/profile/{user}/patterns", patterns),
            web.get("/suggestions", suggestions),
            web.post("/feedback", feedback),
            web.post("/identity/voice", identite_voix),
            web.post("/identity/face", identite_visage),
            web.get("/relay", relais.handler),
        ]
    )
    return app


def routes_declarees(app: web.Application) -> list[str]:
    """Sert à la recette : vérifier qu'aucune route de §12 n'a été oubliée."""
    chemins: list[str] = []
    for ressource in app.router.resources():
        info: dict[str, Any] = ressource.get_info()
        chemins.append(str(info.get("path") or info.get("formatter") or ""))
    return sorted(chemins)
