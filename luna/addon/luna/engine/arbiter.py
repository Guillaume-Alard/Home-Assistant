"""L2 — l'arbitre d'autonomie. Le cœur de §9.

C'est **le seul endroit du projet** qui a le droit d'appeler
`maison.appeler_service`. `tests/test_invariants.py` interdit statiquement à
tout autre fichier de seulement mentionner ce nom.

Le fil de décision, dans cet ordre, et jamais autrement :

    outil demandé par Claude
      └─ résolution en actions Home Assistant concrètes   (l'arbitre)
           └─ niveau, lu dans le registre de L0            (kernel.autonomy)
                └─ politique                               (kernel.permissions)
                     ├─ 5      → refus, journalisé
                     ├─ 3, 4   → proposition, journalisée à la décision
                     ├─ 2 ok   → exécution
                     └─ 2 non  → proposition
                          └─ journal si niveau ≥ 3         (§9.1)

Claude n'intervient à aucune étape après la première. Il ne voit pas les
niveaux, ne les nomme pas, ne peut pas les contourner (§9.2).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from ..kernel.autonomy import Niveau, doit_etre_journalise, libelle_v1, niveau_de
from ..kernel.contracts import MaisonProvider, MemoireProvider
from ..kernel.errors import (
    LunaError,
    MaisonIndisponible,
    NonAutorise,
    PropositionExpiree,
    PropositionInconnue,
)
from ..kernel.ids import nouvel_id
from ..kernel.permissions import peut_agir_seule, peut_valider
from ..kernel.schemas import (
    ActionHA,
    ContexteRequete,
    EntreeJournal,
    EvenementCarte,
    EvtOutil,
    EvtProposition,
    Proposition,
    PropositionEnAttente,
    ResultatOutil,
)
from .tools import SERVICES

log = logging.getLogger("luna.arbitre")

DUREE_PROPOSITION = timedelta(minutes=5)

Emetteur = Callable[[EvenementCarte], Awaitable[None]]

DOMAINES_LUMIERE = ("light",)
DOMAINES_INTERRUPTEUR = ("switch",)
DOMAINES_SCENE = ("scene",)
DOMAINES_THERMOSTAT = ("climate",)


class Arbitre:
    def __init__(self, maison: MaisonProvider, memoire: MemoireProvider) -> None:
        self._maison = maison
        self._memoire = memoire
        self._en_attente: dict[str, PropositionEnAttente] = {}

    # ── Point d'entrée depuis le cerveau ─────────────────────────────────

    async def executer(
        self,
        nom: str,
        entree: dict[str, Any],
        *,
        contexte: ContexteRequete,
        message_id: str,
        emettre: Emetteur,
    ) -> ResultatOutil:
        try:
            return await self._router(
                nom, entree, contexte=contexte, message_id=message_id, emettre=emettre
            )
        except LunaError as exc:
            await emettre(
                EvtOutil(
                    message_id=message_id,
                    name=nom,
                    label=exc.message,
                    level=int(Niveau.LIRE),
                    status="error",
                )
            )
            return ResultatOutil(contenu=exc.message, erreur=True)

    async def _router(
        self,
        nom: str,
        entree: dict[str, Any],
        *,
        contexte: ContexteRequete,
        message_id: str,
        emettre: Emetteur,
    ) -> ResultatOutil:
        if nom == "lister_pieces":
            return await self._lister_pieces(message_id, emettre)
        if nom == "etat_maison":
            return await self._etat_maison(entree, message_id, emettre)
        if nom == "commander_lumiere":
            return await self._commander(
                entree,
                domaines=DOMAINES_LUMIERE,
                message_id=message_id,
                contexte=contexte,
                emettre=emettre,
                luminosite=entree.get("luminosite"),
            )
        if nom == "commander_interrupteur":
            return await self._commander(
                entree,
                domaines=DOMAINES_INTERRUPTEUR,
                message_id=message_id,
                contexte=contexte,
                emettre=emettre,
            )
        if nom == "activer_scene":
            return await self._activer_scene(entree, message_id, contexte, emettre)
        if nom == "regler_thermostat":
            return await self._regler_thermostat(entree, message_id, contexte, emettre)
        return ResultatOutil(contenu=f"Outil inconnu : {nom}.", erreur=True)

    # ── Lecture — niveau 1, libre ────────────────────────────────────────

    async def _lister_pieces(self, message_id: str, emettre: Emetteur) -> ResultatOutil:
        await self._outil(
            emettre,
            message_id,
            "lister_pieces",
            "Lire les pièces",
            Niveau.LIRE,
            "running",
        )
        pieces = await self._maison.pieces()
        if not pieces:
            texte = "Aucune pièce n'est déclarée dans Home Assistant."
        else:
            texte = "\n".join(
                f"- {p.nom} : "
                + (
                    ", ".join(f"{n} {d}" for d, n in sorted(p.entites.items()))
                    or "aucune entité"
                )
                for p in pieces
            )
        await self._outil(
            emettre, message_id, "lister_pieces", "Lire les pièces", Niveau.LIRE, "done"
        )
        return ResultatOutil(contenu=texte)

    async def _etat_maison(
        self, entree: dict[str, Any], message_id: str, emettre: Emetteur
    ) -> ResultatOutil:
        piece = entree.get("piece") or None
        domaine = entree.get("domaine") or None
        libelle = "Lire l'état" + (f" de {piece}" if piece else " de la maison")
        await self._outil(
            emettre, message_id, "etat_maison", libelle, Niveau.LIRE, "running"
        )
        etats = await self._maison.etats(piece=piece, domaine=domaine)
        if not etats:
            contenu = f"Rien ne correspond à « {piece or 'toute la maison'} »" + (
                f" dans le domaine {domaine}." if domaine else "."
            )
        else:
            lignes = []
            for e in etats[:120]:
                unite = f" {e.unite}" if e.unite else ""
                lieu = f" [{e.piece}]" if e.piece else ""
                lignes.append(f"- {e.nom}{lieu} ({e.entity_id}) : {e.etat}{unite}")
            contenu = "\n".join(lignes)
            if len(etats) > 120:
                contenu += f"\n… et {len(etats) - 120} autres entités."
        await self._outil(
            emettre, message_id, "etat_maison", libelle, Niveau.LIRE, "done"
        )
        return ResultatOutil(contenu=contenu)

    # ── Action ───────────────────────────────────────────────────────────

    async def _commander(
        self,
        entree: dict[str, Any],
        *,
        domaines: tuple[str, ...],
        message_id: str,
        contexte: ContexteRequete,
        emettre: Emetteur,
        luminosite: Any = None,
    ) -> ResultatOutil:
        cible = str(entree.get("cible") or "").strip()
        action = str(entree.get("action") or "").strip()
        if action not in SERVICES:
            return ResultatOutil(contenu=f"Action inconnue : {action}.", erreur=True)
        entites = await self._maison.resoudre(cible, domaines)
        if not entites:
            quoi = "lumière" if domaines == DOMAINES_LUMIERE else "prise"
            return ResultatOutil(
                contenu=f"Je ne trouve aucune {quoi} qui corresponde à « {cible} ».",
                erreur=True,
            )
        donnees: dict[str, Any] = {}
        if luminosite is not None and action != "eteindre":
            donnees["brightness_pct"] = max(1, min(100, int(luminosite)))

        acte = ActionHA(
            domain=domaines[0],
            service=SERVICES[action],
            target={"entity_id": entites},
            data=donnees,
        )
        libelle = f"{action.capitalize()} {cible} ({len(entites)})"
        return await self._appliquer(
            acte,
            libelle=libelle,
            nom_outil="commander_lumiere"
            if domaines == DOMAINES_LUMIERE
            else "commander_interrupteur",
            justification=f"Demande explicite de {contexte.profile}.",
            message_id=message_id,
            contexte=contexte,
            emettre=emettre,
        )

    async def _activer_scene(
        self,
        entree: dict[str, Any],
        message_id: str,
        contexte: ContexteRequete,
        emettre: Emetteur,
    ) -> ResultatOutil:
        nom = str(entree.get("nom") or "").strip()
        entites = await self._maison.resoudre(nom, DOMAINES_SCENE)
        if not entites:
            return ResultatOutil(
                contenu=f"Je ne trouve pas de scène nommée « {nom} ».", erreur=True
            )
        acte = ActionHA(
            domain="scene", service="turn_on", target={"entity_id": entites[:1]}
        )
        return await self._appliquer(
            acte,
            libelle=f"Activer la scène {nom}",
            nom_outil="activer_scene",
            justification=f"Demande explicite de {contexte.profile}.",
            message_id=message_id,
            contexte=contexte,
            emettre=emettre,
        )

    async def _regler_thermostat(
        self,
        entree: dict[str, Any],
        message_id: str,
        contexte: ContexteRequete,
        emettre: Emetteur,
    ) -> ResultatOutil:
        cible = str(entree.get("cible") or "").strip()
        temperature = float(entree.get("temperature", 0))
        raison = str(entree.get("raison") or "").strip()
        entites = await self._maison.resoudre(cible, DOMAINES_THERMOSTAT)
        if not entites:
            return ResultatOutil(
                contenu=f"Je ne trouve pas de thermostat pour « {cible} ».", erreur=True
            )
        acte = ActionHA(
            domain="climate",
            service="set_temperature",
            target={"entity_id": entites},
            data={"temperature": temperature},
        )
        return await self._appliquer(
            acte,
            libelle=f"Régler {cible} sur {temperature:g} °C",
            nom_outil="regler_thermostat",
            justification=raison or f"Demande de {contexte.profile}.",
            message_id=message_id,
            contexte=contexte,
            emettre=emettre,
        )

    # ── La décision (§9) ─────────────────────────────────────────────────

    async def _appliquer(
        self,
        acte: ActionHA,
        *,
        libelle: str,
        nom_outil: str,
        justification: str,
        message_id: str,
        contexte: ContexteRequete,
        emettre: Emetteur,
    ) -> ResultatOutil:
        niveau = niveau_de(acte.domain, acte.service)
        await self._outil(emettre, message_id, nom_outil, libelle, niveau, "running")

        if niveau >= Niveau.INTERDIT_V1:
            message = (
                f"Je ne commande pas {libelle_v1(acte.domain)} — c'est hors de mon "
                "périmètre pour l'instant. Tu peux le faire depuis Loggia."
            )
            await self._journaliser(
                acte,
                niveau,
                justification,
                "refused_v1",
                False,
                contexte,
                message_id,
                None,
            )
            await self._outil(emettre, message_id, nom_outil, libelle, niveau, "error")
            return ResultatOutil(contenu=message, erreur=True)

        if peut_agir_seule(contexte.profile, niveau):
            try:
                await self._maison.appeler_service(acte)
            except MaisonIndisponible as exc:
                if doit_etre_journalise(niveau):
                    await self._journaliser(
                        acte,
                        niveau,
                        justification,
                        "accepted",
                        False,
                        contexte,
                        message_id,
                        exc.message,
                    )
                await self._outil(
                    emettre, message_id, nom_outil, libelle, niveau, "error"
                )
                return ResultatOutil(contenu=exc.message, erreur=True)
            if doit_etre_journalise(niveau):
                await self._journaliser(
                    acte,
                    niveau,
                    justification,
                    "accepted",
                    True,
                    contexte,
                    message_id,
                    None,
                )
            await self._outil(emettre, message_id, nom_outil, libelle, niveau, "done")
            return ResultatOutil(contenu=f"Fait : {libelle.lower()}.")

        # Niveau 3 ou 4, ou niveau 2 qu'un profil sans scope ne peut pas déclencher.
        proposition = Proposition(
            id=nouvel_id("p"),
            level=int(niveau),
            title=libelle,
            why=justification,
            actions=[acte],
            expires_at=datetime.now().astimezone() + DUREE_PROPOSITION,
        )
        self._en_attente[proposition.id] = PropositionEnAttente(
            proposition=proposition, contexte=contexte, message_id=message_id
        )
        await emettre(EvtProposition(message_id=message_id, proposal=proposition))
        await self._outil(emettre, message_id, nom_outil, libelle, niveau, "done")
        log.info("Proposition %s (niveau %s) : %s", proposition.id, int(niveau), libelle)
        return ResultatOutil(
            contenu=(
                "Je n'exécute pas cette action moi-même : une proposition a été "
                "créée et attend une validation. Annonce-le simplement."
            )
        )

    # ── Décision de l'utilisateur ────────────────────────────────────────

    async def decider(
        self, proposition_id: str, decision: str, *, contexte: ContexteRequete
    ) -> dict[str, Any]:
        en_attente = self._en_attente.get(proposition_id)
        if en_attente is None:
            raise PropositionInconnue()
        proposition = en_attente.proposition
        niveau = Niveau(proposition.level)

        if datetime.now().astimezone() > proposition.expires_at:
            self._en_attente.pop(proposition_id, None)
            await self._journaliser_lot(
                proposition, niveau, "expired", False, en_attente, None
            )
            raise PropositionExpiree()

        if not peut_valider(niveau, est_admin=contexte.is_admin):
            raise NonAutorise(
                "Cette action touche à la configuration de Home Assistant : il "
                "faut un compte administrateur pour la valider."
            )

        self._en_attente.pop(proposition_id, None)

        if decision == "reject":
            await self._journaliser_lot(
                proposition, niveau, "rejected", False, en_attente, None
            )
            return {"executed": False, "results": []}

        resultats = []
        for acte in proposition.actions:
            erreur: str | None = None
            try:
                await self._maison.appeler_service(acte)
            except LunaError as exc:
                erreur = exc.message
            resultats.append(
                {
                    "domain": acte.domain,
                    "service": acte.service,
                    "ok": erreur is None,
                    "error": erreur,
                }
            )
            await self._journaliser(
                acte,
                niveau,
                proposition.why,
                "accepted",
                erreur is None,
                contexte,
                en_attente.message_id,
                erreur,
            )
        return {"executed": all(r["ok"] for r in resultats), "results": resultats}

    def propositions_en_attente(self) -> list[Proposition]:
        maintenant = datetime.now().astimezone()
        return [
            e.proposition
            for e in self._en_attente.values()
            if e.proposition.expires_at > maintenant
        ]

    def purger(self) -> int:
        """Retire les propositions expirées. Appelé périodiquement par L3."""
        maintenant = datetime.now().astimezone()
        expirees = [
            i
            for i, e in self._en_attente.items()
            if e.proposition.expires_at <= maintenant
        ]
        for identifiant in expirees:
            self._en_attente.pop(identifiant, None)
        return len(expirees)

    # ── Écritures annexes ────────────────────────────────────────────────

    @staticmethod
    async def _outil(
        emettre: Emetteur,
        message_id: str,
        nom: str,
        libelle: str,
        niveau: Niveau,
        statut: str,
    ) -> None:
        await emettre(
            EvtOutil(
                message_id=message_id,
                name=nom,
                label=libelle,
                level=int(niveau),
                status=statut,  # type: ignore[arg-type]
            )
        )

    async def _journaliser(
        self,
        acte: ActionHA,
        niveau: Niveau,
        justification: str,
        decision: str,
        execute: bool,
        contexte: ContexteRequete,
        message_id: str | None,
        erreur: str | None,
    ) -> None:
        await self._memoire.journaliser(
            EntreeJournal(
                id=nouvel_id("a"),
                ts=datetime.now().astimezone(),
                profile=contexte.profile,
                ha_user_id=contexte.ha_user_id,
                level=niveau,
                action=acte,
                justification=justification,
                decision=decision,  # type: ignore[arg-type]
                executed=execute,
                error=erreur,
                message_id=message_id,
            )
        )

    async def _journaliser_lot(
        self,
        proposition: Proposition,
        niveau: Niveau,
        decision: str,
        execute: bool,
        en_attente: PropositionEnAttente,
        erreur: str | None,
    ) -> None:
        for acte in proposition.actions:
            await self._journaliser(
                acte,
                niveau,
                proposition.why,
                decision,
                execute,
                en_attente.contexte,
                en_attente.message_id,
                erreur,
            )
