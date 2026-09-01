"""Le moteur « propose puis approuve » — point de passage unique des écritures.

Deux chemins, et seulement deux :
  1. Ordre direct : action de la liste blanche (`spec.direct`) explicitement
     demandée par Guillaume (voix/texte). Exécution immédiate, journalisée avec
     la phrase d'origine. Une action sensible exige une double confirmation.
  2. Proposition : tout le reste. Créée en file, exécutée seulement après
     approbation (les sensibles : approbation depuis l'interface uniquement,
     jamais à la voix).

Le refus est technique : action inconnue → refus ; action hors liste blanche en
direct → refus ; proposition non approuvée → jamais exécutée ; le chemin
« système » (alertes) n'accepte que le risque faible.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..ha.client import HAError
from ..store import Store
from .registry import ActionError, ActionRegistry, ActionSpec

log = logging.getLogger("sentinel.actions")

RISK_FR = {"low": "faible", "medium": "moyen", "sensitive": "sensible"}
STATUS_FR = {
    "pending": "en attente", "approved": "approuvée", "rejected": "refusée",
    "deferred": "reportée", "executing": "en cours d'exécution",
    "done": "exécutée", "failed": "en échec",
}


@dataclass(frozen=True)
class Outcome:
    status: str  # ok | needs_confirmation | refused | failed
    text: str    # phrase à dire / afficher

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class _PendingConfirmation:
    action_id: str
    params: dict
    utterance: str
    source: str
    expires_at: float


class ActionEngine:
    CONFIRM_TTL = 60.0  # secondes pour dire « confirme » après une demande sensible

    def __init__(
        self,
        registry: ActionRegistry,
        store: Store,
        on_proposal_change: Callable[[str, dict], Awaitable[None]] | None = None,
    ):
        self._registry = registry
        self._store = store
        self._on_proposal_change = on_proposal_change
        self._pending_confirm: _PendingConfirmation | None = None

    # ── Chemin 1 : ordre direct ──────────────────────────────────────────

    async def run_direct(
        self, action_id: str, params: dict, *, utterance: str, source: str
    ) -> Outcome:
        spec = self._registry.get(action_id)
        if spec is None:
            await self._journal("direct", source, action_id, params,
                               f"ordre : « {utterance} »", "refused", "action inconnue")
            return Outcome("refused", "Cette action n'existe pas dans mon registre.")

        if not spec.direct:
            await self._journal("direct", source, action_id, params,
                               f"ordre : « {utterance} »", "refused", "hors liste blanche directe")
            return Outcome(
                "refused",
                "Cette action ne s'exécute pas sur ordre direct — je peux en faire "
                "une proposition à approuver.",
            )

        if spec.risk_for(params) == "sensitive":
            self._pending_confirm = _PendingConfirmation(
                action_id, params, utterance, source, time.monotonic() + self.CONFIRM_TTL
            )
            await self._journal("direct", source, action_id, params,
                               f"ordre : « {utterance} »", "needs_confirmation", "")
            return Outcome(
                "needs_confirmation",
                "C'est une action sensible. Pour confirmer, dis : « Sentinel, confirme ». "
                "Sinon, dis « annule ».",
            )

        return await self._execute_direct(spec, params, utterance, source, confirmed=False)

    async def confirm_pending(self, *, source: str) -> Outcome:
        pending = self._pending_confirm
        self._pending_confirm = None
        if pending is None:
            return Outcome("refused", "Je n'ai aucune action en attente de confirmation.")
        if time.monotonic() > pending.expires_at:
            await self._journal("direct", source, pending.action_id, pending.params,
                               f"ordre : « {pending.utterance} »", "refused", "confirmation expirée")
            return Outcome("refused", "La demande a expiré — répète l'ordre si tu veux toujours le faire.")
        spec = self._registry.get(pending.action_id)
        assert spec is not None
        return await self._execute_direct(
            spec, pending.params, pending.utterance, source, confirmed=True
        )

    def cancel_pending(self) -> bool:
        """Annule l'action sensible en attente ; renvoie True s'il y en avait une."""
        had = self._pending_confirm is not None
        self._pending_confirm = None
        return had

    async def _execute_direct(
        self, spec: ActionSpec, params: dict, utterance: str, source: str, *, confirmed: bool
    ) -> Outcome:
        auth = f"ordre direct ({source}{', confirmé' if confirmed else ''}) : « {utterance} »"
        try:
            result = await spec.executor(params)
        except (ActionError, HAError) as exc:
            await self._journal("direct", source, spec.id, params, auth, "failed", str(exc))
            return Outcome("failed", str(exc))
        except Exception:
            log.exception("Exécuteur %s en échec inattendu", spec.id)
            await self._journal("direct", source, spec.id, params, auth, "failed", "erreur interne")
            return Outcome("failed", "L'action a échoué — détail dans les journaux du serveur.")
        await self._journal("direct", source, spec.id, params, auth, "ok", result)
        return Outcome("ok", result)

    # ── Chemin 2 : propositions ──────────────────────────────────────────

    async def propose(
        self,
        *,
        title: str,
        description: str = "",
        justification: str = "",
        risk: str | None = None,
        rollback: str = "",
        action_id: str,
        params: dict | None = None,
        created_by: str = "sentinel",
    ) -> tuple[dict | None, str]:
        spec = self._registry.get(action_id)
        if spec is None:
            return None, f"Impossible de proposer : l'action « {action_id} » n'existe pas au registre."
        params = params or {}
        # Le risque affiché ne peut jamais être plus faible que celui du registre
        declared = risk if risk in RISK_FR else spec.risk_for(params)
        order = ["low", "medium", "sensitive"]
        final_risk = max(declared, spec.risk_for(params), key=order.index)
        proposal = await self._store.add_proposal(
            title=title, description=description, justification=justification,
            risk=final_risk, rollback=rollback, action_id=action_id,
            params=params, created_by=created_by,
        )
        await self._journal("proposal", created_by, action_id, params,
                           f"proposition n°{proposal['num']} créée", "created", title)
        await self._notify("new", proposal)
        return proposal, f"Proposition n°{proposal['num']} créée : {title} (risque {RISK_FR[final_risk]})."

    async def decide(
        self, num: int, decision: str, *, via: str, actor: str = "guillaume"
    ) -> tuple[dict | None, str]:
        proposal = await self._store.get_proposal(num)
        if proposal is None:
            return None, f"Je ne trouve pas de proposition n°{num}."
        if proposal["status"] not in ("pending", "deferred"):
            return proposal, (
                f"La proposition n°{num} est déjà {STATUS_FR.get(proposal['status'], proposal['status'])}."
            )

        if decision == "reject":
            proposal = await self._store.update_proposal(
                num, status="rejected", decided_at=_now(), decided_via=via
            )
            await self._journal("proposal", actor, proposal["action_id"], proposal["params"],
                               f"proposition n°{num} refusée via {via}", "decided", "rejected")
            await self._notify("update", proposal)
            return proposal, f"Proposition n°{num} refusée."

        if decision == "defer":
            proposal = await self._store.update_proposal(
                num, status="deferred", decided_at=_now(), decided_via=via
            )
            await self._notify("update", proposal)
            return proposal, f"Proposition n°{num} reportée — elle reste dans la file."

        if decision != "approve":
            return proposal, "Décision inconnue (approuver, refuser ou reporter)."

        # Approbation — verrou : une proposition sensible ne s'approuve pas à la voix
        if proposal["risk"] == "sensitive" and via == "voice":
            await self._journal("proposal", actor, proposal["action_id"], proposal["params"],
                               f"tentative d'approbation vocale n°{num}", "refused",
                               "sensible : interface uniquement")
            return proposal, (
                f"La proposition n°{num} est sensible : approuve-la depuis l'interface, "
                "pas à la voix."
            )

        proposal = await self._store.update_proposal(
            num, status="approved", decided_at=_now(), decided_via=via
        )
        await self._notify("update", proposal)
        return await self._execute_proposal(proposal, actor=actor, via=via)

    async def _execute_proposal(self, proposal: dict, *, actor: str, via: str) -> tuple[dict, str]:
        num = proposal["num"]
        spec = self._registry.get(proposal["action_id"])
        auth = f"proposition n°{num} approuvée via {via}"
        if spec is None:  # registre ayant changé entre-temps
            proposal = await self._store.update_proposal(num, status="failed", error="action disparue du registre")
            await self._journal("proposal", actor, proposal["action_id"], proposal["params"], auth,
                               "failed", "action disparue du registre")
            await self._notify("update", proposal)
            return proposal, f"Proposition n°{num} approuvée mais son action n'existe plus au registre."

        proposal = await self._store.update_proposal(num, status="executing")
        await self._notify("update", proposal)
        try:
            result = await spec.executor(proposal["params"])
        except (ActionError, HAError) as exc:
            proposal = await self._store.update_proposal(
                num, status="failed", error=str(exc), executed_at=_now()
            )
            await self._journal("proposal", actor, spec.id, proposal["params"], auth, "failed", str(exc))
            await self._notify("update", proposal)
            return proposal, f"Proposition n°{num} approuvée, mais l'exécution a échoué : {exc}"
        except Exception:
            log.exception("Proposition n°%s : échec inattendu", num)
            proposal = await self._store.update_proposal(
                num, status="failed", error="erreur interne", executed_at=_now()
            )
            await self._journal("proposal", actor, spec.id, proposal["params"], auth, "failed", "erreur interne")
            await self._notify("update", proposal)
            return proposal, f"Proposition n°{num} approuvée, mais l'exécution a échoué (erreur interne)."

        proposal = await self._store.update_proposal(
            num, status="done", result=result, executed_at=_now()
        )
        await self._journal("proposal", actor, spec.id, proposal["params"], auth, "ok", result)
        await self._notify("update", proposal)
        return proposal, f"Proposition n°{num} exécutée. {result}"

    # ── Chemin « système » : règles d'alerte (pré-autorisées, risque faible) ──

    async def run_system(
        self, action_id: str, params: dict, *, authorization: str
    ) -> Outcome:
        spec = self._registry.get(action_id)
        if spec is None or spec.risk_for(params) != "low":
            await self._journal("system", "sentinel", action_id, params, authorization,
                               "refused", "chemin système : risque faible uniquement")
            return Outcome("refused", "Le chemin système n'autorise que les actions à risque faible.")
        try:
            result = await spec.executor(params)
        except (ActionError, HAError) as exc:
            await self._journal("system", "sentinel", action_id, params, authorization, "failed", str(exc))
            return Outcome("failed", str(exc))
        await self._journal("system", "sentinel", action_id, params, authorization, "ok", result)
        return Outcome("ok", result)

    # ── Interne ──────────────────────────────────────────────────────────

    async def _journal(self, kind, actor, action_id, params, authorization, outcome, detail) -> None:
        try:
            await self._store.add_journal(
                kind=kind, actor=actor, action_id=action_id, params=params,
                authorization=authorization, outcome=outcome, detail=detail,
            )
        except Exception:
            log.exception("Écriture du journal impossible")

    async def _notify(self, change: str, proposal: dict) -> None:
        if self._on_proposal_change:
            try:
                await self._on_proposal_change(change, proposal)
            except Exception:
                log.exception("Notification de proposition impossible")


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
