"""L2 — la gardienne de l'installation (P5, §5 F2).

Quatre familles d'anomalies, un seul chemin de sortie : le tiroir de veille de
P4. Une anomalie **est** une alerte — même mise en sourdine, même boucle de
retour, même respect des heures de silence. P5 n'a pas de tuyau à elle (E3).

**Ce que cette gardienne ne fait pas**, et qui la définit mieux que ce qu'elle
fait : elle n'écrit rien. Pas dans le dashboard, pas dans les intégrations, pas
sur le disque. Luna est pourtant administratrice de Home Assistant — elle passe
par le Supervisor, dont l'utilisateur vit dans `GROUP_ID_ADMIN`. Rien du côté
de Home Assistant ne l'empêcherait de réécrire Loggia. La seule barrière est
ici, et elle est vérifiée par `tests/test_invariants.py` plutôt que promise
(E1, H73).

Le modèle de langage n'entre qu'à la fin, pour mettre en français un constat
déjà établi. Il ne fait jamais naître une anomalie, et ce qu'il rend est du
texte — jamais une action (E6, H71).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

from ..kernel.contracts import CerveauProvider, MaisonProvider, MemoireProvider
from ..kernel.health import (
    CATEGORIE,
    DIAGNOSTICS_PAR_JOUR,
    ETAT_PANNE,
    ETATS_INTEGRATION_CASSEE,
    GRACE,
    Groupe,
    Panne,
    assez_longtemps,
    cle_incident,
    duree_lisible,
    regrouper,
    resume_de_secours,
)
from ..kernel.ids import nouvel_id
from ..kernel.schemas import (
    ActionHA,
    Alerte,
    ChangementEtat,
    EntreeConfig,
    Incident,
    MaisonConnectee,
    SanteInstallation,
)
from ..kernel.settings import Gardienne as ReglagesGardienne

log = logging.getLogger("luna.gardienne")

#: Les journaux d'automatisation et de script, et rien d'autre. `system_log`
#: contient tout ce qui a mal tourné dans Home Assistant ; s'en servir comme
#: d'une source générale d'anomalies ferait exactement la gardienne bavarde que
#: E4 cherche à éviter.
PREFIXES_JOURNAL = (
    "homeassistant.components.automation",
    "homeassistant.components.script",
)

#: Ce que la carte reçoit pour une anomalie. `warning` par défaut : une panne
#: mérite qu'on la voie, pas qu'on la crie.
NIVEAU_PAR_DEFAUT = "warning"

#: Émetteur d'alerte, injecté par l'amorçage. C'est `MoteurVeille.signaler`.
Signaleur = Callable[[Alerte], Awaitable[None]]
Effaceur = Callable[[str], Awaitable[None]]


class Gardienne:
    def __init__(
        self,
        *,
        maison: MaisonProvider,
        memoire: MemoireProvider,
        cerveau: CerveauProvider,
        reglages: ReglagesGardienne,
        signaler: Signaleur,
        effacer: Effaceur,
        horloge: Callable[[], datetime] | None = None,
    ) -> None:
        self._maison = maison
        self._memoire = memoire
        self._cerveau = cerveau
        self._reglages = reglages
        self._signaler = signaler
        self._effacer = effacer
        self._maintenant = horloge or (lambda: datetime.now().astimezone())

        self._seuil = timedelta(minutes=max(1, reglages.minutes_avant_panne))
        self._ignorees = set(reglages.ignorer)

        #: Quand chaque entité est tombée. Pas encore une alerte : une panne en
        #: attente de durée.
        self._tombees: dict[str, datetime] = {}
        #: H69 — les entités jamais vues disponibles depuis la connexion. Une
        #: entité désactivée n'est pas une panne neuve, et elle reviendrait à
        #: chaque redémarrage.
        self._jamais_vues: set[str] = set()
        #: Les intégrations en mauvais état, et depuis quand.
        self._entrees_cassees: dict[str, tuple[datetime, EntreeConfig]] = {}
        #: Les clés déjà signalées, pour ne pas re-signaler à chaque battement.
        self._signalees: set[str] = set()
        #: Les pannes d'intégration reprises d'un incident ouvert au démarrage :
        #: leur horloge repart de l'ouverture réelle, pas de la reconnexion.
        self._reprises: dict[str, datetime] = {}

        self._connexion: datetime | None = None
        self._sources = {"system_log": True, "lovelace": reglages.loggia}
        self._diagnostics_du_jour = 0
        self._jour_diagnostics: date | None = None

    @property
    def active(self) -> bool:
        return self._reglages.active

    # ── Entrées ──────────────────────────────────────────────────────────

    async def sur_connexion(self, evenement: MaisonConnectee) -> None:
        """Repart de zéro à chaque (re)connexion, et ouvre la période de grâce.

        Une reconnexion, c'est presque toujours un redémarrage de Home
        Assistant : tout est indisponible pendant quelques minutes. Signaler
        pendant ce moment-là produirait trois cents alertes et ferait fermer le
        tiroir pour de bon (H67).
        """
        if not evenement.connectee:
            return
        self._connexion = self._maintenant()
        self._tombees.clear()
        self._entrees_cassees.clear()
        self._signalees.clear()
        self._jamais_vues = {
            etat.entity_id
            for etat in await self._maison.etats()
            if etat.etat == ETAT_PANNE
        }

        # Une panne **déjà connue** n'est pas une entité « jamais vue ».
        #
        # H69 protège d'une entité désactivée qui reviendrait à chaque
        # redémarrage. Appliquée telle quelle, elle fait disparaître pour
        # toujours une vraie panne en cours dès que Luna redémarre — le
        # contraire de ce qu'on veut d'une gardienne. Un incident ouvert en
        # base tranche : Luna sait déjà que c'est cassé, et depuis quand.
        connus = await self._memoire.incidents_ouverts()
        for incident in connus:
            if incident.famille == "entite":
                self._jamais_vues.discard(incident.sujet)
                self._tombees[incident.sujet] = incident.ouvert_le
            elif incident.famille == "integration":
                self._entrees_cassees.pop(incident.sujet, None)
                self._reprises[incident.sujet] = incident.ouvert_le

        log.info(
            "Gardienne : grâce de %s min, %s ignorée(s), %s panne(s) déjà connue(s)",
            int(GRACE.total_seconds() // 60),
            len(self._jamais_vues),
            len(connus),
        )

    async def sur_changement(self, evenement: ChangementEtat) -> None:
        """Une entité tombe ou revient. Aucune scrutation : H63 tenu."""
        if not self.active or evenement.entity_id in self._ignorees:
            return
        entite = evenement.entity_id
        if evenement.nouveau == ETAT_PANNE:
            self._tombees.setdefault(entite, evenement.ts)
            return

        # Elle répond : elle n'est plus « jamais vue », et si elle était
        # signalée, l'alerte n'a plus lieu d'être.
        self._jamais_vues.discard(entite)
        if self._tombees.pop(entite, None) is None:
            return
        await self._refermer(cle_incident("entite", entite))

    async def battre(self) -> None:
        """Un tour de gardienne. Appelé par l'ordonnanceur, jamais en boucle.

        Trois des quatre familles ne coûtent rien ici : les entités viennent du
        bus, les intégrations d'une lecture bon marché. Seul le journal système
        est relu, et il ne parle pas à la maison — il lit un journal.
        """
        if not self.active or self._en_grace():
            return
        maintenant = self._maintenant()
        await self._verifier_entites(maintenant)
        await self._verifier_integrations(maintenant)
        if self._reglages.journal_systeme:
            await self._verifier_journal(maintenant)

    async def relire_loggia(self) -> int:
        """Les cartes de Loggia qui pointent dans le vide (Q1 : oui).

        Une carte qui référence une entité supprimée affiche « Entity not
        available » et personne ne le remarque jamais. C'est la seule famille
        qui apporte quelque chose que Home Assistant ne dit nulle part.

        Lecture stricte, une fois par jour, depuis l'entretien nocturne.
        """
        if not self.active or not self._reglages.loggia:
            return 0
        config = await self._maison.config_loggia(self._reglages.loggia_url_path)
        self._sources["lovelace"] = bool(config)
        if not config:
            return 0

        connues = self._maison.entites_connues()
        maintenant = self._maintenant()
        manquantes = sorted(
            {e for e in _entites_citees(config) if e not in connues} - self._ignorees
        )
        for entite in manquantes:
            await self._ouvrir(
                Groupe(
                    famille="loggia",
                    sujet=entite,
                    depuis=maintenant,
                    nom=entite,
                ),
                maintenant,
                details={"dashboard": self._reglages.loggia_url_path or "défaut"},
            )
        # Une carte réparée referme son incident : on ne garde que ce qui
        # manque encore.
        for incident in await self._memoire.incidents_ouverts():
            if incident.famille == "loggia" and incident.sujet not in manquantes:
                await self._refermer(incident.cle)
        log.info("Loggia : %s carte(s) pointant dans le vide", len(manquantes))
        return len(manquantes)

    # ── Les quatre familles ──────────────────────────────────────────────

    async def _verifier_entites(self, maintenant: datetime) -> None:
        pannes = []
        for entite, depuis in self._tombees.items():
            if entite in self._jamais_vues or entite in self._ignorees:
                continue
            if not assez_longtemps(depuis, maintenant, self._seuil):
                continue
            entree, plateforme = self._maison.integration_de(entite)
            pannes.append(
                Panne(
                    entity_id=entite,
                    depuis=depuis,
                    entree=entree,
                    integration=plateforme,
                    nom=self._maison.nom_entite(entite),
                )
            )
        for groupe in regrouper(pannes):
            await self._ouvrir(
                groupe,
                maintenant,
                details={"entites": list(groupe.entites)},
            )

    async def _verifier_integrations(self, maintenant: datetime) -> None:
        entrees = await self._maison.entrees_config()
        cassees = {
            e.entry_id: e
            for e in entrees
            if e.state in ETATS_INTEGRATION_CASSEE and not e.disabled_by
        }
        for entry_id, entree in cassees.items():
            defaut = self._reprises.get(entry_id, maintenant)
            depuis, _ = self._entrees_cassees.get(entry_id, (defaut, entree))
            self._entrees_cassees[entry_id] = (depuis, entree)
            if not assez_longtemps(depuis, maintenant, self._seuil):
                continue
            await self._ouvrir(
                Groupe(
                    famille="integration",
                    sujet=entry_id,
                    depuis=depuis,
                    integration=entree.domain,
                    nom=entree.title or entree.domain,
                ),
                maintenant,
                details={"etat": entree.state, "motif": entree.reason or ""},
                actions=[
                    ActionHA(
                        domain="homeassistant",
                        service="reload_config_entry",
                        target={"entry_id": entry_id},
                    )
                ],
            )
        for entry_id in list(self._entrees_cassees):
            if entry_id not in cassees:
                self._entrees_cassees.pop(entry_id, None)
                self._reprises.pop(entry_id, None)
                await self._refermer(cle_incident("integration", entry_id))

    async def _verifier_journal(self, maintenant: datetime) -> None:
        enregistrements = await self._maison.journal_systeme()
        self._sources["system_log"] = bool(enregistrements) or not self._sources.get(
            "system_log", True
        )
        for ligne in enregistrements:
            if not ligne.name.startswith(PREFIXES_JOURNAL) or ligne.level != "ERROR":
                continue
            depuis = _horodatage(ligne.first_occurred, maintenant)
            await self._ouvrir(
                Groupe(
                    famille="automatisation",
                    sujet=ligne.cle,
                    depuis=depuis,
                    nom=_nom_automatisation(ligne.name),
                ),
                maintenant,
                details={
                    "message": " ".join(ligne.message)[:500],
                    "occurrences": ligne.count,
                },
            )

    # ── Ouvrir, fermer, raconter ─────────────────────────────────────────

    async def _ouvrir(
        self,
        groupe: Groupe,
        maintenant: datetime,
        *,
        details: dict[str, Any] | None = None,
        actions: list[ActionHA] | None = None,
    ) -> None:
        if groupe.cle in self._signalees:
            return
        incident = await self._memoire.ouvrir_incident(
            Incident(
                id=nouvel_id("i"),
                cle=groupe.cle,
                famille=groupe.famille,
                sujet=groupe.sujet,
                ouvert_le=groupe.depuis,
                details=details or {},
            )
        )
        # L'incident en base fait foi : après un redémarrage, la panne date de
        # son ouverture, pas de la reconnexion de Luna (E8).
        depuis = incident.ouvert_le
        titre, raison = await self._raconter(groupe, depuis, details or {}, maintenant)
        self._signalees.add(groupe.cle)
        await self._signaler(
            Alerte(
                id=nouvel_id("al"),
                key=groupe.cle,
                level=NIVEAU_PAR_DEFAUT,
                category=CATEGORIE,
                title=titre,
                why=raison,
                entity_id=groupe.sujet if groupe.famille == "entite" else None,
                ts=maintenant,
                actions=actions or [],
            )
        )

    async def _refermer(self, cle: str) -> None:
        self._signalees.discard(cle)
        if await self._memoire.fermer_incident(cle, self._maintenant()) is not None:
            await self._effacer(cle)

    async def _raconter(
        self,
        groupe: Groupe,
        depuis: datetime,
        details: dict[str, Any],
        maintenant: datetime,
    ) -> tuple[str, str]:
        """Le titre et la raison. Le modèle si possible, le secours sinon.

        L'ordre compte : le secours est calculé **d'abord**, et le modèle ne
        fait que le remplacer s'il rend quelque chose d'exploitable. Une clé
        épuisée, une panne réseau ou le plafond quotidien atteint ne font pas
        disparaître l'alerte — ils lui laissent une formulation plus sèche.
        """
        # `depuis` vient de l'incident en base, pas du groupe : après un
        # redémarrage, la panne date de son ouverture réelle.
        secours = resume_de_secours(replace(groupe, depuis=depuis), maintenant)
        if not self._peut_diagnostiquer(maintenant):
            return secours
        constat = _constat(groupe, depuis, details, maintenant)
        try:
            rendu = await self._cerveau.diagnostiquer(constat)
        except Exception as exc:  # noqa: BLE001 — un diagnostic raté n'est pas une panne
            log.info("Diagnostic indisponible : %s", exc)
            return secours
        titre, raison = _deux_lignes(rendu)
        if not titre or not raison:
            return secours
        self._diagnostics_du_jour += 1
        return titre, raison

    def _peut_diagnostiquer(self, maintenant: datetime) -> bool:
        """H72 : dix appels par jour, pas un de plus.

        Sans plafond, une intégration qui bat de l'aile coûte cher, et personne
        ne s'en aperçoit avant la facture.
        """
        if self._jour_diagnostics != maintenant.date():
            self._jour_diagnostics = maintenant.date()
            self._diagnostics_du_jour = 0
        return self._diagnostics_du_jour < DIAGNOSTICS_PAR_JOUR

    def _en_grace(self) -> bool:
        if self._connexion is None:
            return True
        return self._maintenant() - self._connexion < GRACE

    # ── Rapport (§C.1) ───────────────────────────────────────────────────

    async def rapport(self) -> SanteInstallation:
        maintenant = self._maintenant()
        entrees = await self._maison.entrees_config()
        incidents = await self._memoire.incidents_ouverts()
        etats = await self._maison.etats()
        return SanteInstallation(
            checked_at=maintenant,
            ha_version=getattr(self._maison, "version_ha", None),
            entities={
                "total": len(etats),
                "unavailable": sum(1 for e in etats if e.etat == ETAT_PANNE),
                "grace": self._en_grace(),
            },
            integrations=[
                e
                for e in entrees
                if e.state in ETATS_INTEGRATION_CASSEE and not e.disabled_by
            ],
            incidents=[
                {
                    "id": i.id,
                    "famille": i.famille,
                    "sujet": i.sujet,
                    "ouvert_le": i.ouvert_le.isoformat(),
                    "duree": duree_lisible(i.ouvert_le, maintenant),
                    "details": i.details,
                }
                for i in incidents
            ],
            sources=dict(self._sources),
        )


# ── Lecture de Loggia ────────────────────────────────────────────────────

#: Les clés d'une configuration Lovelace qui portent une entité. La liste est
#: volontairement courte : mieux vaut rater une carte exotique que signaler des
#: faux positifs sur un dashboard qu'on n'a pas écrit.
CLES_ENTITE = ("entity", "entity_id", "camera_image", "badge")


def _entites_citees(config: Any) -> set[str]:
    """Toutes les entités que le dashboard référence, à n'importe quel niveau.

    Les cartes s'imbriquent — une grille dans une pile dans une vue — et chaque
    intégration personnalisée invente ses propres clés. Une descente récursive
    sur quelques noms connus attrape l'essentiel sans prétendre comprendre le
    format.
    """
    trouvees: set[str] = set()
    _descendre(config, trouvees)
    return {e for e in trouvees if _ressemble_a_une_entite(e)}


def _descendre(noeud: Any, trouvees: set[str]) -> None:
    if isinstance(noeud, dict):
        for cle, valeur in noeud.items():
            if cle in CLES_ENTITE and isinstance(valeur, str):
                trouvees.add(valeur)
            elif cle == "entities" and isinstance(valeur, list):
                for element in valeur:
                    if isinstance(element, str):
                        trouvees.add(element)
                    else:
                        _descendre(element, trouvees)
            else:
                _descendre(valeur, trouvees)
    elif isinstance(noeud, list):
        for element in noeud:
            _descendre(element, trouvees)


def _ressemble_a_une_entite(valeur: str) -> bool:
    """`domaine.objet`, et rien d'autre.

    Sans ce filtre, un titre contenant un point deviendrait une entité
    manquante — et la gardienne signalerait des cartes parfaitement saines.
    """
    domaine, _, objet = valeur.partition(".")
    return bool(domaine and objet) and domaine.isidentifier() and "/" not in objet


# ── Mise en forme ────────────────────────────────────────────────────────


def _constat(
    groupe: Groupe,
    depuis: datetime,
    details: dict[str, Any],
    maintenant: datetime,
) -> str:
    """Le constat envoyé au modèle : du JSON, pas de la prose.

    Il n'y a rien de sensible là-dedans — des identifiants d'entités et un
    message d'erreur — et rien qui vienne des habitudes de la maison (§4, H60).
    """
    return json.dumps(
        {
            "famille": groupe.famille,
            "sujet": groupe.sujet,
            "nom": groupe.nom,
            "integration": groupe.integration,
            "depuis": duree_lisible(depuis, maintenant),
            "entites_touchees": list(groupe.entites),
            "details": details,
        },
        ensure_ascii=False,
        indent=1,
    )


def _deux_lignes(rendu: str) -> tuple[str, str]:
    """`TITRE:` / `RAISON:` → un couple. Tout le reste est jeté.

    Un modèle qui répond hors format ne casse rien : on retombe sur le secours.
    """
    titre = raison = ""
    for ligne in rendu.splitlines():
        nue = ligne.strip()
        if nue.upper().startswith("TITRE:"):
            titre = nue[6:].strip()
        elif nue.upper().startswith("RAISON:"):
            raison = nue[7:].strip()
    return titre, raison


def _nom_automatisation(nom_journal: str) -> str:
    """`homeassistant.components.automation.reveil` → « reveil »."""
    return nom_journal.rsplit(".", 1)[-1] if "." in nom_journal else nom_journal


def _horodatage(epoch: float, defaut: datetime) -> datetime:
    if epoch <= 0:
        return defaut
    return datetime.fromtimestamp(epoch, tz=defaut.tzinfo)
