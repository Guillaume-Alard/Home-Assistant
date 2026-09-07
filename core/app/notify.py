"""Notifications mobiles (Phase 14) : Luna te joint sur ton téléphone.

Quand tu n'es pas devant le cockpit, Luna peut pousser sur ton téléphone (via
l'app Home Assistant) ce qui compte : un rappel qui sonne, une alerte de sécurité,
le briefing du matin. C'est de la COMMUNICATION, jamais du pilotage : rien n'agit
sur la maison. La poussée passe par le moteur d'actions (chemin « système »,
risque faible), donc par le service `notify` de Nova — comme toute écriture, elle
est journalisée, et l'invariant reste intact (seul l'exécuteur appelle Nova).

Le service à utiliser (`notify.mobile_app_xxx`) est déclaré dans .env
(`SENTINEL_NOTIFY_SERVICE`). Vide = notifications désactivées.
"""

from __future__ import annotations

import logging

log = logging.getLogger("sentinel.notify")


class Notifier:
    def __init__(
        self,
        engine,
        service: str,
        *,
        reminders: bool = True,
        alerts: bool = True,
        briefing: bool = False,
    ):
        self._engine = engine
        self._service = (service or "").strip()
        # Quels événements sont poussés vers le téléphone (le reste reste au cockpit).
        self.reminders = reminders
        self.alerts = alerts
        self.briefing = briefing

    @property
    def enabled(self) -> bool:
        """Actif seulement si un moteur d'actions ET un service sont configurés."""
        return self._engine is not None and bool(self._service)

    @property
    def service(self) -> str:
        return self._service

    async def push(self, message: str, *, title: str | None = None) -> bool:
        """Pousse une notification. Renvoie True si elle est bien partie.

        Passe par `run_system` (chemin pré-autorisé, risque faible uniquement) :
        `ha.notify` est faible, donc accepté ; toute autre action serait refusée.
        """
        message = (message or "").strip()
        if not self.enabled or not message:
            return False
        try:
            outcome = await self._engine.run_system(
                "ha.notify",
                {"service": self._service, "message": message, "title": title},
                authorization="notification mobile (Sentinel)",
            )
        except Exception:
            log.exception("Notification mobile en échec inattendu")
            return False
        if not outcome.ok:
            log.warning("Notification mobile non délivrée : %s", outcome.text)
        return outcome.ok
