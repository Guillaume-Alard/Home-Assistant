"""L1 — le client Home Assistant.

Repris du client éprouvé de Sentinel (`core/app/ha/client.py`), ramené sur
aiohttp et sur le Supervisor. Connexion unique tenue en tâche de fond,
authentification par `SUPERVISOR_TOKEN`, cache d'états entretenu par
`state_changed`, registres pièces/entités/appareils pour résoudre « le salon »,
reconnexion avec backoff.

⚠️ **Invariant.** `appeler_service` ne doit être appelé QUE par l'arbitre
(`engine/arbiter.py`). C'est l'invariant de sécurité du projet (§9.2), verrouillé
par `tests/test_invariants.py` : aucun autre fichier du paquet n'a le droit de
mentionner ce nom.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any

import aiohttp

from ..kernel.bus import Bus
from ..kernel.errors import MaisonIndisponible
from ..kernel.schemas import (
    ActionHA,
    ChangementEtat,
    EnregistrementJournal,
    EntreeConfig,
    EtatEntite,
    MaisonConnectee,
    Piece,
)
from .texte import normaliser

log = logging.getLogger("luna.maison")

DELAI_COMMANDE = 10.0
BACKOFF_MIN = 1.0
BACKOFF_MAX = 30.0
DELAI_FERMETURE = 2.0

#: Attributs qu'on remonte tels quels dans `EtatEntite`.
_ATTR_NOM = "friendly_name"
_ATTR_CLASSE = "device_class"
_ATTR_UNITE = "unit_of_measurement"


class ClientMaison:
    def __init__(self, url: str, jeton: str, bus: Bus) -> None:
        self._url = url
        self._jeton = jeton
        self._bus = bus

        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._tache: asyncio.Task[None] | None = None
        self._ferme = False
        self._id = 0
        self._attentes: dict[int, asyncio.Future[Any]] = {}

        self.connectee = False
        self.version_ha: str | None = None
        self._etats: dict[str, dict[str, Any]] = {}
        self._pieces: dict[str, str] = {}  # area_id → nom
        self._piece_entite: dict[str, str] = {}  # entity_id → area_id
        # (entry_id, plateforme) par entité — de quoi regrouper les pannes par
        # intégration plutôt que par appareil (P5, H68).
        self._entree_entite: dict[str, tuple[str, str]] = {}
        self._noms: dict[str, str] = {}

    # ── Cycle de vie ─────────────────────────────────────────────────────

    async def demarrer(self) -> None:
        self._ferme = False
        self._session = aiohttp.ClientSession()
        self._tache = asyncio.create_task(self._boucle(), name="luna-maison")

    async def fermer(self) -> None:
        """Arrête tout, sans jamais se bloquer.

        L'ordre compte. On ferme **la session en premier** : couper le
        transport est ce qui fait sortir la lecture WebSocket. Annuler la tâche
        de réception ne suffit pas — l'annulation ne traverse pas toujours
        `receive()` d'aiohttp, et l'add-on reste alors bloqué à l'arrêt.
        """
        self._ferme = True

        session, self._session = self._session, None
        if session is not None:
            await session.close()

        tache, self._tache = self._tache, None
        if tache is not None:
            tache.cancel()
            await asyncio.wait({tache}, timeout=DELAI_FERMETURE)

        self._ws = None
        await self._signaler(False, "arrêt")

    async def _boucle(self) -> None:
        backoff = BACKOFF_MIN
        while not self._ferme:
            reception: asyncio.Task[None] | None = None
            try:
                await self._poignee_de_main()
                backoff = BACKOFF_MIN
                # La boucle de réception doit tourner AVANT de demander les
                # registres : `_envoyer` attend une réponse que seule cette
                # boucle résout. Les charger depuis la poignée de main, comme
                # au premier jet, garantit un délai de 10 s puis une reconnexion
                # sans fin — et rien dans les journaux qui le dise.
                reception = asyncio.create_task(self._recevoir(), name="luna-maison-rx")
                await self._initialiser()
                await reception
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — on veut vraiment tout rattraper
                log.warning("Connexion à Home Assistant perdue : %s", exc)
            finally:
                await self._couper(reception)
                await self._signaler(False, "connexion perdue")
                self._reveiller_attentes(MaisonIndisponible())
            if self._ferme:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def _couper(self, reception: asyncio.Task[None] | None) -> None:
        """Coupe la connexion courante, sans jamais se bloquer.

        `asyncio.wait` plutôt que `wait_for` : à l'expiration, `wait` rend la
        main sans annuler ce qu'il attendait, donc il ne peut pas se bloquer à
        son tour sur une annulation qui n'aboutit pas.
        """
        ws, self._ws = self._ws, None
        if ws is not None and not ws.closed:
            fermeture = asyncio.create_task(_fermer_ws(ws), name="luna-maison-close")
            await asyncio.wait({fermeture}, timeout=DELAI_FERMETURE)
        if reception is not None and not reception.done():
            reception.cancel()
            await asyncio.wait({reception}, timeout=DELAI_FERMETURE)

    async def _poignee_de_main(self) -> None:
        assert self._session is not None
        log.info("Connexion à Home Assistant : %s", self._url)
        self._ws = await self._session.ws_connect(self._url, heartbeat=30)
        self._id = 0

        premier = await self._ws.receive_json()
        if premier.get("type") != "auth_required":
            raise MaisonIndisponible(
                "Home Assistant n'a pas demandé d'authentification — URL inattendue."
            )
        await self._ws.send_json({"type": "auth", "access_token": self._jeton})
        reponse = await self._ws.receive_json()
        if reponse.get("type") != "auth_ok":
            raise MaisonIndisponible(
                "Home Assistant a refusé mon jeton. Vérifie l'option "
                "`homeassistant_api` de l'add-on."
            )
        self.version_ha = reponse.get("ha_version")

    async def _initialiser(self) -> None:
        """Registres et abonnement, une fois la réception en route."""
        await self._charger_registres()
        await self._envoyer(
            {"type": "subscribe_events", "event_type": "state_changed"}, attendre=False
        )
        await self._signaler(True, None)
        log.info("Home Assistant %s — %s entités", self.version_ha, len(self._etats))

    async def _signaler(self, connectee: bool, detail: str | None) -> None:
        if self.connectee == connectee:
            return
        self.connectee = connectee
        await self._bus.publier(MaisonConnectee(connectee=connectee, detail=detail))

    # ── Réception ────────────────────────────────────────────────────────

    async def _recevoir(self) -> None:
        assert self._ws is not None
        async for message in self._ws:
            if message.type is not aiohttp.WSMsgType.TEXT:
                break
            charge = message.json()
            genre = charge.get("type")
            if genre == "result":
                self._resoudre_attente(charge)
            elif genre == "event":
                await self._appliquer_evenement(charge.get("event") or {})
        raise MaisonIndisponible("Le flux WebSocket s'est refermé.")

    def _resoudre_attente(self, charge: dict[str, Any]) -> None:
        future = self._attentes.pop(charge.get("id", -1), None)
        if future is None or future.done():
            return
        if charge.get("success"):
            future.set_result(charge.get("result"))
        else:
            erreur = (charge.get("error") or {}).get("message", "erreur inconnue")
            future.set_exception(
                MaisonIndisponible(f"Home Assistant a refusé la commande : {erreur}")
            )

    async def _appliquer_evenement(self, evenement: dict[str, Any]) -> None:
        """Entretient le cache, puis publie le changement sur le bus (H63).

        C'est la seule source d'observation de P4 : les observateurs réagissent
        à cet événement, rien n'interroge la maison en boucle. Sur un N95 qui
        fait déjà tourner Whisper et une empreinte de locuteur, une boucle de
        scrutation serait le premier vrai gaspillage du projet.

        Un changement d'attributs sans changement d'état — une lampe qui varie
        de luminosité — met le cache à jour mais ne publie rien : les
        observateurs raisonnent sur des états, pas sur du bruit.
        """
        donnees = evenement.get("data") or {}
        entity_id = donnees.get("entity_id")
        nouvel_etat = donnees.get("new_state")
        ancien_etat = donnees.get("old_state")
        if not entity_id:
            return
        if nouvel_etat is None:
            self._etats.pop(entity_id, None)
        else:
            self._etats[entity_id] = nouvel_etat

        avant = str(ancien_etat["state"]) if ancien_etat else None
        apres = str(nouvel_etat["state"]) if nouvel_etat else None
        if avant == apres:
            return
        await self._bus.publier(
            ChangementEtat(
                entity_id=entity_id,
                ancien=avant,
                nouveau=apres,
                ts=datetime.now().astimezone(),
            )
        )

    def _reveiller_attentes(self, erreur: Exception) -> None:
        for future in self._attentes.values():
            if not future.done():
                future.set_exception(erreur)
        self._attentes.clear()

    # ── Émission ─────────────────────────────────────────────────────────

    async def _envoyer(self, charge: dict[str, Any], *, attendre: bool = True) -> Any:
        if self._ws is None or self._ws.closed:
            raise MaisonIndisponible()
        self._id += 1
        identifiant = self._id
        charge = {"id": identifiant, **charge}
        future: asyncio.Future[Any] | None = None
        if attendre:
            future = asyncio.get_running_loop().create_future()
            self._attentes[identifiant] = future
        await self._ws.send_json(charge)
        if future is None:
            return None
        try:
            return await asyncio.wait_for(future, DELAI_COMMANDE)
        except TimeoutError as exc:
            self._attentes.pop(identifiant, None)
            raise MaisonIndisponible(
                "Home Assistant n'a pas répondu dans les temps."
            ) from exc

    async def _charger_registres(self) -> None:
        etats = await self._envoyer({"type": "get_states"})
        self._etats = {e["entity_id"]: e for e in etats or []}

        pieces = await self._envoyer({"type": "config/area_registry/list"})
        self._pieces = {p["area_id"]: p["name"] for p in pieces or []}

        appareils = await self._envoyer({"type": "config/device_registry/list"})
        piece_appareil = {
            a["id"]: a["area_id"] for a in appareils or [] if a.get("area_id")
        }

        entites = await self._envoyer({"type": "config/entity_registry/list"})
        self._piece_entite = {}
        self._entree_entite = {}
        self._noms = {}
        for entree in entites or []:
            entity_id = entree["entity_id"]
            area = entree.get("area_id") or piece_appareil.get(entree.get("device_id"))
            if area:
                self._piece_entite[entity_id] = area
            self._entree_entite[entity_id] = (
                entree.get("config_entry_id") or "",
                entree.get("platform") or "",
            )
            if nom := (entree.get("name") or entree.get("original_name")):
                self._noms[entity_id] = nom

    # ── Lecture (niveau 1, libre) ────────────────────────────────────────

    async def pieces(self) -> list[Piece]:
        compte: dict[str, dict[str, int]] = {a: {} for a in self._pieces}
        for entity_id, area_id in self._piece_entite.items():
            if area_id not in compte:
                continue
            domaine = entity_id.split(".", 1)[0]
            compte[area_id][domaine] = compte[area_id].get(domaine, 0) + 1
        return [
            Piece(area_id=area_id, nom=nom, entites=compte.get(area_id, {}))
            for area_id, nom in sorted(self._pieces.items(), key=lambda p: p[1])
        ]

    async def etats(
        self, *, piece: str | None = None, domaine: str | None = None
    ) -> list[EtatEntite]:
        area_id = self._area_id(piece) if piece else None
        if piece and area_id is None:
            return []
        resultats = []
        for entity_id, brut in sorted(self._etats.items()):
            if domaine and not entity_id.startswith(f"{domaine}."):
                continue
            entite_area = self._piece_entite.get(entity_id)
            if area_id and entite_area != area_id:
                continue
            resultats.append(self._vers_etat(entity_id, brut, entite_area))
        return resultats

    def _vers_etat(
        self, entity_id: str, brut: dict[str, Any], area_id: str | None
    ) -> EtatEntite:
        attributs = brut.get("attributes") or {}
        return EtatEntite(
            entity_id=entity_id,
            nom=attributs.get(_ATTR_NOM) or entity_id,
            etat=str(brut.get("state", "inconnu")),
            piece=self._pieces.get(area_id) if area_id else None,
            device_class=attributs.get(_ATTR_CLASSE),
            unite=attributs.get(_ATTR_UNITE),
        )

    def _area_id(self, cible: str) -> str | None:
        vise = normaliser(cible)
        for area_id, nom in self._pieces.items():
            if normaliser(nom) == vise:
                return area_id
        for area_id, nom in self._pieces.items():
            if vise and vise in normaliser(nom):
                return area_id
        return None

    async def resoudre(self, cible: str, domaines: tuple[str, ...]) -> list[str]:
        """« le salon » → les entity_id des domaines demandés dans cette pièce.

        Trois passes, de la plus sûre à la plus floue : identifiant exact,
        pièce, puis nom convivial de l'entité.
        """
        if "." in cible and cible in self._etats:
            return [cible] if cible.split(".", 1)[0] in domaines else []

        area_id = self._area_id(cible)
        if area_id is not None:
            trouves = [
                entity_id
                for entity_id, area in self._piece_entite.items()
                if area == area_id
                and entity_id.split(".", 1)[0] in domaines
                and entity_id in self._etats
            ]
            if trouves:
                return sorted(trouves)

        vise = normaliser(cible)
        return sorted(
            entity_id
            for entity_id, brut in self._etats.items()
            if entity_id.split(".", 1)[0] in domaines
            and vise
            and vise in normaliser((brut.get("attributes") or {}).get(_ATTR_NOM, ""))
        )

    def nom_piece(self, entity_id: str) -> str | None:
        area_id = self._piece_entite.get(entity_id)
        return self._pieces.get(area_id) if area_id else None

    # ── Écriture — RÉSERVÉ À L'ARBITRE (§9.2) ────────────────────────────

    # ── Gardienne de l'installation (P5) — lecture seule ─────────────────

    def integration_de(self, entity_id: str) -> tuple[str, str]:
        return self._entree_entite.get(entity_id, ("", ""))

    def nom_entite(self, entity_id: str) -> str:
        if nom := self._noms.get(entity_id):
            return nom
        brut = self._etats.get(entity_id) or {}
        return str((brut.get("attributes") or {}).get(_ATTR_NOM) or entity_id)

    def entites_connues(self) -> set[str]:
        """Tout ce que Home Assistant connaît, disponible ou non.

        Sert à repérer les cartes de Loggia qui pointent dans le vide : une
        entité *indisponible* est en panne, une entité *inconnue* a été
        supprimée. Les deux ne se réparent pas de la même façon.
        """
        return set(self._etats) | set(self._entree_entite)

    async def entrees_config(self) -> list[EntreeConfig]:
        brut = await self._envoyer({"type": "config_entries/get"})
        entrees = []
        for e in brut or []:
            entrees.append(
                EntreeConfig(
                    entry_id=e.get("entry_id", ""),
                    domain=e.get("domain", ""),
                    title=e.get("title") or "",
                    state=e.get("state") or "",
                    reason=e.get("reason"),
                    disabled_by=e.get("disabled_by"),
                )
            )
        return entrees

    async def journal_systeme(self) -> list[EnregistrementJournal]:
        """`system_log/list` — demande des droits d'administrateur (H64).

        Un refus rend une liste vide plutôt qu'une exception : la famille
        « automatisations » s'éteint, les trois autres continuent, et
        `luna/health` le dit franchement dans `sources`.
        """
        try:
            brut = await self._envoyer({"type": "system_log/list"})
        except MaisonIndisponible as exc:
            log.info("Journal système indisponible : %s", exc.message)
            return []
        return [
            EnregistrementJournal(
                name=e.get("name", ""),
                message=list(e.get("message") or []),
                level=e.get("level", ""),
                source=list(e.get("source") or []),
                count=int(e.get("count") or 1),
                first_occurred=float(e.get("first_occurred") or 0.0),
            )
            for e in brut or []
            if isinstance(e, dict)
        ]

    async def config_loggia(self, url_path: str = "") -> dict[str, Any]:
        """La configuration brute d'un dashboard, **en lecture**.

        Il n'existe pas de méthode jumelle qui écrirait : la commande Lovelace
        d'enregistrement n'apparaît nulle part dans ce projet, et un test
        statique refuse toute ligne qui la nommerait (E1, H73). Le test m'a
        d'ailleurs attrapé sur cette docstring — c'est bon signe.
        """
        charge: dict[str, Any] = {"type": "lovelace/config"}
        if url_path:
            charge["url_path"] = url_path
        try:
            return await self._envoyer(charge) or {}
        except MaisonIndisponible as exc:
            log.info("Configuration Loggia illisible : %s", exc.message)
            return {}

    async def appeler_service(self, action: ActionHA) -> None:
        """⚠️ Ne doit être appelé que par `engine/arbiter.py`.

        Vérifié statiquement par `tests/test_invariants.py`.
        """
        charge: dict[str, Any] = {
            "type": "call_service",
            "domain": action.domain,
            "service": action.service,
        }
        if action.target:
            charge["target"] = action.target
        if action.data:
            charge["service_data"] = action.data
        await self._envoyer(charge)


#: Ce que la gardienne a le droit de demander à Home Assistant. La liste est
#: **exhaustive et en lecture seule** : c'est la moitié structurelle du refus de
#: E1. Aucune commande d'écriture n'a de place ici, et `tests/test_invariants.py`
#: vérifie qu'aucun fichier du projet n'en mentionne une.
COMMANDES_GARDIENNE = (
    "config_entries/get",
    "system_log/list",
    "lovelace/config",
)


async def _fermer_ws(ws: aiohttp.ClientWebSocketResponse) -> None:
    with contextlib.suppress(Exception):
        await ws.close()
