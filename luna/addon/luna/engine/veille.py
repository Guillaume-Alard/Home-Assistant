"""L2 — le moteur de veille : de capteurs Home Assistant à des alertes justes.

Le partage est celui de §5, F5, et il est structurel :

* **la détection** vit dans Home Assistant, dans des `binary_sensor` que
  Guillaume lit dans les outils de développement. Aucun modèle de langage sur
  ce chemin — §9.2 est tenu par construction, pas par vigilance ;
* **la pertinence, la formulation et le moment** vivent ici.

Ce moteur tient aussi la **file de relecture** des faits (D2). C'est le même
sujet vu par l'autre bout : les habitudes qui justifient une alerte sont celles
qu'on relit ici.

Ce que ce moteur ajoute au capteur, et que Home Assistant ne sait pas faire :
ne pas harceler (H57), respecter les heures de silence (H58), ne pas insister
sur ce qu'on a déjà refusé (§12), n'ouvrir la bouche que si une habitude
observée le justifie vraiment, et retirer l'alerte dès que la condition cesse.

Ce moteur n'a **aucun accès au cerveau**. Il ne peut pas demander à Claude si
une fenêtre est ouverte, et Claude ne peut pas lui faire émettre une alerte.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from ..kernel.contracts import MaisonProvider, MemoireProvider
from ..kernel.errors import LunaError
from ..kernel.facts import assez_sur_pour_proposer, confiance
from ..kernel.ids import nouvel_id
from ..kernel.permissions import MAISON
from ..kernel.schemas import (
    ActionHA,
    Alerte,
    ChangementEtat,
    ContexteRequete,
    EvenementCarte,
    EvenementJournal,
    EvtAlerte,
    EvtAlerteEffacee,
    Fait,
    ScoreSuggestion,
    Suggestion,
)
from ..kernel.settings import Annonce, RegleVeille
from ..kernel.veille import (
    Score,
    appliquer_retour,
    cle_suggestion,
    dans_les_heures_de_silence,
    peut_parler,
    peut_repeter,
)
from .arbiter import Arbitre

log = logging.getLogger("luna.veille")

Emetteur = Callable[[EvenementCarte], Awaitable[None]]

#: Le contexte d'une action née de la veille et non d'une personne.
#:
#: `is_admin=False` est délibéré : rien de ce que la veille déclenche seule ne
#: doit pouvoir emprunter des droits d'administrateur. Le profil `maison` n'est
#: celui de personne, et n'a donc que ce que la politique accorde à tout le
#: monde (§9, `kernel/permissions.py`).
#: Une alerte venue de la gardienne n'a pas de règle derrière elle. Les défauts
#: font l'affaire : silence respecté, annonce décidée par le niveau.
_REGLE_EXTERNE = RegleVeille(entite="", message="")

CONTEXTE_VEILLE = ContexteRequete(
    ha_user_id=None,
    ha_user_name=None,
    is_admin=False,
    profile=MAISON,
    client_id="veille",
    local=True,
)

#: Fréquence de réexamen des alertes retenues. Une ampoule oubliée à 3 h n'est
#: pas perdue : elle est **différée** jusqu'à la fin des heures de silence, et
#: c'est ce réexamen qui la sort à 7 h (H58).
PERIODE_REEXAMEN = timedelta(minutes=5)


class AlerteInconnue(LunaError):
    code = "alert_unknown"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "Je ne retrouve plus cette alerte.")


class MoteurVeille:
    def __init__(
        self,
        *,
        regles: list[RegleVeille],
        memoire: MemoireProvider,
        maison: MaisonProvider,
        arbitre: Arbitre,
        emettre: Emetteur,
        annonce: Annonce | None = None,
        horloge: Callable[[], datetime] | None = None,
    ) -> None:
        self._regles = {regle.entite: regle for regle in regles}
        self._memoire = memoire
        self._maison = maison
        self._arbitre = arbitre
        self._emettre = emettre
        self._annonce = annonce or Annonce()
        self._maintenant = horloge or (lambda: datetime.now().astimezone())

        #: Ce qui est affiché dans le tiroir, par clé de suggestion.
        self._actives: dict[str, Alerte] = {}
        #: Condition vraie, mais Luna se tait pour l'instant — heures de
        #: silence ou répétition trop rapprochée. La valeur est l'instant où la
        #: condition est devenue vraie, pas celui où on la dira.
        self._differees: dict[str, datetime] = {}
        #: Dernière émission par clé, pour H57.
        self._derniere: dict[str, datetime] = {}
        #: Les anomalies de P5 retenues par les heures de silence. Comme pour
        #: un capteur : différées, jamais perdues.
        self._differees_externes: dict[str, Alerte] = {}

    @property
    def entites_surveillees(self) -> list[str]:
        return sorted(self._regles)

    # ── Entrées ──────────────────────────────────────────────────────────

    async def amorcer(self) -> None:
        """Regarde une fois l'état des capteurs surveillés, au démarrage.

        Sans ça, un ouvrant déjà ouvert quand l'add-on redémarre passerait
        inaperçu jusqu'au prochain changement — c'est-à-dire jusqu'à ce qu'on
        le referme, ce qui est précisément trop tard.
        """
        if not self._regles:
            return
        etats = {etat.entity_id: etat.etat for etat in await self._maison.etats()}
        for entite in self._regles:
            if etats.get(entite) == "on":
                await self._condition_vraie(entite, self._maintenant())

    async def sur_changement(self, evenement: ChangementEtat) -> None:
        regle = self._regles.get(evenement.entity_id)
        if regle is None:
            return
        if evenement.nouveau == "on":
            await self._condition_vraie(evenement.entity_id, evenement.ts)
        else:
            await self._condition_fausse(evenement.entity_id)

    async def reexaminer(self) -> None:
        """Rejoue les alertes différées. Appelé par l'ordonnanceur."""
        for entite in list(self._differees):
            await self._essayer(entite)
        for alerte in list(self._differees_externes.values()):
            await self.signaler(alerte)

    # ── Cycle d'une alerte ───────────────────────────────────────────────

    async def _condition_vraie(self, entite: str, quand: datetime) -> None:
        cle = self._cle(entite)
        if cle in self._actives:
            return
        self._differees.setdefault(entite, quand)
        await self._essayer(entite)

    async def _condition_fausse(self, entite: str) -> None:
        self._differees.pop(entite, None)
        cle = self._cle(entite)
        alerte = self._actives.pop(cle, None)
        if alerte is not None:
            await self._emettre(EvtAlerteEffacee(alert_id=alerte.id))
            log.info("Alerte levée : %s", alerte.title)

    async def _essayer(self, entite: str) -> None:
        regle = self._regles[entite]
        cle = self._cle(entite)
        maintenant = self._maintenant()

        score = await self._score(cle)
        if not score.remonte(maintenant):
            # Mise en sourdine ou refusée trop souvent : la condition reste
            # vraie, Luna se tait. Elle ne redemandera pas dans quatre heures.
            self._differees.pop(entite, None)
            return

        if not peut_parler(regle.niveau, maintenant, silence=regle.silence):
            return  # différée : le réexamen la reprendra après 7 h
        if not peut_repeter(self._derniere.get(cle), maintenant):
            return

        justification = await self._justifier(regle, maintenant)
        if justification is None:
            # Une règle adossée à une habitude que Luna n'a pas encore assez
            # observée ne dit rien. C'est ce que « pertinent » veut dire (§11).
            self._differees.pop(entite, None)
            return

        alerte = Alerte(
            id=nouvel_id("al"),
            key=cle,
            level=regle.niveau,
            category=regle.categorie,
            title=regle.message,
            why=justification,
            entity_id=entite,
            ts=maintenant,
            actions=list(regle.actions),
        )
        self._actives[cle] = alerte
        self._derniere[cle] = maintenant
        self._differees.pop(entite, None)
        await self._memoire.enregistrer_evenement(
            EvenementJournal(
                id=nouvel_id("e"),
                ts=maintenant,
                kind="alert",
                profile=None,
                entity_id=entite,
                payload={"cle": cle, "titre": alerte.title, "niveau": alerte.level},
            )
        )
        await self._emettre(EvtAlerte(alert=alerte))
        log.info("Alerte : %s (%s)", alerte.title, cle)
        await self._annoncer(regle, alerte, maintenant)

    async def _annoncer(
        self, regle: RegleVeille, alerte: Alerte, maintenant: datetime
    ) -> None:
        """Dit l'alerte à voix haute, si une enceinte est déclarée.

        Trois choses valent d'être dites sur cette poignée de lignes :

        * **Elle passe par l'arbitre.** `tts.speak` a un niveau comme tout le
          reste, et le journal garde la trace. Ce n'est pas une porte de sortie
          discrète du chemin de §9.
        * **Elle ne dit que le titre**, jamais la raison. La raison contient
          l'heure — « il est 23:35 » — donc un texte différent à chaque fois,
          donc une synthèse vocale refaite à chaque fois. Le titre, lui, vient
          mot pour mot de la configuration : Home Assistant le retrouve dans
          son cache et ne rappelle pas Piper (§7, couche 1).
        * **Elle ne fait jamais tomber l'alerte.** Une enceinte débranchée,
          c'est une ligne dans le journal — pas une alerte perdue.
        """
        if not self._annonce.enceinte or not self._a_annoncer(regle, alerte):
            return
        if self._silence_vocal(regle, maintenant):
            log.debug("Annonce retenue (heures de silence) : %s", alerte.title)
            return

        acte = ActionHA(
            domain="tts",
            service="speak",
            target={"entity_id": self._annonce.moteur},
            data={
                "media_player_entity_id": self._annonce.enceinte,
                "message": alerte.title,
                "cache": True,
            },
        )
        try:
            resultat = await self._arbitre.agir_hors_conversation(
                acte,
                libelle=f"Annoncer : {alerte.title}",
                justification=alerte.why,
                contexte=CONTEXTE_VEILLE,
                reference=alerte.id,
                # Silencieux : la progression d'une annonce n'est pas l'affaire
                # des cartes. Un événement `tool` portant l'identifiant d'une
                # alerte au lieu d'un message n'aurait de sens pour personne.
                emettre=_muet,
            )
        except LunaError as exc:
            log.warning("Annonce impossible : %s", exc.message)
            return
        if resultat.erreur:
            log.warning("Annonce refusée : %s", resultat.contenu)

    def _a_annoncer(self, regle: RegleVeille, alerte: Alerte) -> bool:
        """La règle décide si elle le dit ; sinon, c'est le niveau qui décide."""
        if regle.annonce is not None:
            return regle.annonce
        return alerte.level in self._annonce.niveaux

    def _silence_vocal(self, regle: RegleVeille, maintenant: datetime) -> bool:
        """Faut-il retenir l'annonce à cette heure-ci ?

        Deux réglages, et ils ne disent pas la même chose :

        * `regle.silence = false` — « cette règle a le droit de parler la nuit ».
          Le rappel de coucher, dont c'est tout l'intérêt : il apparaît **et**
          il se dit à 23 h 20, sinon il ne sert à rien.
        * `annonce.silence = true` — « ne réveille pas la maison ». Il couvre
          tout le reste, **y compris une alerte `critical`** : elle a le droit
          d'apparaître dans le tiroir à 3 h du matin, réveiller la maison est
          une autre décision.
        """
        if not self._annonce.silence or not regle.silence:
            return False
        return dans_les_heures_de_silence(maintenant)

    async def _justifier(self, regle: RegleVeille, maintenant: datetime) -> str | None:
        """La raison montrée sous l'alerte. `None` = pas de raison, pas d'alerte.

        Sans fait adossé, la raison est celle qu'a écrite Guillaume dans la
        règle — ou, à défaut, le nom de l'entité qui a déclenché. Avec un fait
        adossé, la règle ne parle que si l'habitude est assez sûre, et la cite.
        """
        if not regle.fait:
            return regle.raison or f"Déclenché par {regle.entite}."
        fait = await self._fait_sur(regle.fait)
        if fait is None:
            return None
        sureté = confiance(
            observations=fait.observations,
            derniere=fait.last_seen_at,
            categorie=fait.category,
            maintenant=maintenant,
        )
        if not assez_sur_pour_proposer(sureté):
            return None
        modele = regle.raison or "D'habitude c'est plutôt vers {valeur}, il est {heure}."
        return modele.format(
            valeur=fait.value,
            heure=maintenant.strftime("%H:%M"),
            observations=fait.observations,
        )

    async def _fait_sur(self, predicat: str) -> Fait | None:
        for fait in await self._memoire.faits(statut="active"):
            if fait.predicate == predicat:
                return fait
        return None

    # ── §12 ──────────────────────────────────────────────────────────────

    async def suggestions(self) -> list[Suggestion]:
        """`GET /suggestions` : ce qui est vivant dans le tiroir, à cet instant."""
        maintenant = self._maintenant()
        sorties: list[Suggestion] = []
        for alerte in sorted(self._actives.values(), key=lambda a: a.ts):
            score = await self._score(alerte.key)
            if not score.remonte(maintenant):
                continue
            sorties.append(
                Suggestion(
                    id=alerte.id,
                    key=alerte.key,
                    title=alerte.title,
                    why=alerte.why,
                    score=round(score.score, 3),
                    level=0,
                    actions=list(alerte.actions),
                )
            )
        return sorties

    def alertes(self) -> list[Alerte]:
        return sorted(self._actives.values(), key=lambda a: a.ts)

    def alerte(self, alerte_id: str) -> Alerte | None:
        for alerte in self._actives.values():
            if alerte.id == alerte_id:
                return alerte
        return None

    async def retour(self, alerte_id: str, action: str) -> dict[str, object]:
        """`POST /feedback`. Le compteur porte sur la clé, pas sur l'occurrence.

        Refuser trois fois « un ouvrant est resté ouvert » met la règle en
        sourdine — pas trois soirées distinctes.
        """
        alerte = self.alerte(alerte_id)
        if alerte is None:
            raise AlerteInconnue()
        maintenant = self._maintenant()
        avant = await self._score(alerte.key)
        apres = appliquer_retour(avant, action, maintenant)
        await self._memoire.enregistrer_score(
            ScoreSuggestion(
                cle=alerte.key,
                score=apres.score,
                rejections=apres.refus,
                muted_until=apres.sourdine_jusqua,
                updated_at=maintenant,
            )
        )
        if not apres.remonte(maintenant):
            self._actives.pop(alerte.key, None)
            await self._emettre(EvtAlerteEffacee(alert_id=alerte.id))
        log.info(
            "Retour « %s » sur %s : score %.2f, refus %s",
            action,
            alerte.key,
            apres.score,
            apres.refus,
        )
        return {
            "ok": True,
            "score": round(apres.score, 3),
            "muted_until": apres.sourdine_jusqua.isoformat()
            if apres.sourdine_jusqua
            else None,
        }

    async def agir(
        self, alerte_id: str, *, contexte: ContexteRequete
    ) -> dict[str, object]:
        """Le bouton « Agir » (D8).

        Il ne court-circuite rien : chaque acte repasse par l'arbitre, avec son
        niveau. Éteindre une lampe reste du niveau 2 ; fermer un ouvrant reste
        du niveau 5, donc refusé et journalisé.
        """
        alerte = self.alerte(alerte_id)
        if alerte is None:
            raise AlerteInconnue()
        if not alerte.actions:
            return {"executed": False, "results": []}

        resultats = []
        for acte in alerte.actions:
            resultat = await self._arbitre.agir_hors_conversation(
                acte,
                libelle=alerte.title,
                justification=alerte.why,
                contexte=contexte,
                reference=alerte.id,
                emettre=self._emettre,
            )
            resultats.append(
                {
                    "domain": acte.domain,
                    "service": acte.service,
                    "ok": not resultat.erreur,
                    "message": resultat.contenu,
                }
            )
        return {
            "executed": all(r["ok"] for r in resultats),
            "results": resultats,
        }

    async def patterns(self, profil: str | None) -> list[dict[str, object]]:
        """`GET /profile/{user}/patterns` : les habitudes, avec leur confiance
        recalculée à l'instant de la lecture — jamais celle qu'on avait mise en
        base la dernière fois."""
        maintenant = self._maintenant()
        sorties = []
        for fait in await self._memoire.faits(statut="active", profil=profil):
            sorties.append(
                {
                    "id": fait.id,
                    "predicate": fait.predicate,
                    "value": fait.value,
                    "entity_id": fait.entity_id,
                    "confidence": round(
                        confiance(
                            observations=fait.observations,
                            derniere=fait.last_seen_at,
                            categorie=fait.category,
                            maintenant=maintenant,
                        ),
                        3,
                    ),
                    "observations": fait.observations,
                    "last_seen": fait.last_seen_at.isoformat(),
                    "status": fait.status,
                }
            )
        return sorted(sorties, key=lambda p: p["confidence"], reverse=True)  # type: ignore[arg-type,return-value]

    # ── Alertes venues d'ailleurs (P5) ───────────────────────────────────

    async def signaler(self, alerte: Alerte) -> None:
        """Une alerte qui ne vient d'aucun capteur.

        C'est tout ce que la gardienne de P5 a demandé au moteur de P4 : une
        porte d'entrée. Le reste — sourdine, score, heures de silence, annonce
        vocale — s'applique sans une ligne de plus, parce que c'est la même
        `Alerte` et la même clé de suggestion.

        Les heures de silence sont respectées comme pour une règle ordinaire :
        personne ne répare un Zigbee à 3 h du matin. Une anomalie retenue la
        nuit est **différée**, pas perdue — le prochain battement la reprendra.
        """
        maintenant = self._maintenant()
        if alerte.key in self._actives:
            return

        score = await self._score(alerte.key)
        if not score.remonte(maintenant):
            return
        if not peut_parler(alerte.level, maintenant):
            self._differees_externes[alerte.key] = alerte
            return
        if not peut_repeter(self._derniere.get(alerte.key), maintenant):
            return

        self._differees_externes.pop(alerte.key, None)
        self._actives[alerte.key] = alerte
        self._derniere[alerte.key] = maintenant
        await self._memoire.enregistrer_evenement(
            EvenementJournal(
                id=nouvel_id("e"),
                ts=maintenant,
                kind="alert",
                entity_id=alerte.entity_id,
                payload={"cle": alerte.key, "titre": alerte.title, "source": "gardienne"},
            )
        )
        await self._emettre(EvtAlerte(alert=alerte))
        log.info("Alerte : %s (%s)", alerte.title, alerte.key)
        await self._annoncer(_REGLE_EXTERNE, alerte, maintenant)

    async def lever(self, cle: str) -> None:
        """La condition a cessé : l'anomalie quitte le tiroir."""
        self._differees_externes.pop(cle, None)
        alerte = self._actives.pop(cle, None)
        if alerte is not None:
            await self._emettre(EvtAlerteEffacee(alert_id=alerte.id))
            log.info("Alerte levée : %s", alerte.title)

    # ── File de relecture (D2) ───────────────────────────────────────────

    async def faits_a_relire(self) -> list[Fait]:
        """`luna/facts` : ce que le modèle propose et que personne n'a tranché.

        Du plus ancien au plus récent : ce qui traîne depuis douze jours est ce
        qui va expirer, et c'est ce qu'il faut regarder en premier.
        """
        faits = await self._memoire.faits(statut="needs_review")
        return sorted(faits, key=lambda f: f.created_at)

    async def trancher_fait(self, fait_id: str, decision: str) -> dict[str, object]:
        """`luna/facts/decide`. Accepté, le fait entre en vigueur ; refusé, il
        reste en base sans jamais resservir (§4 : jamais supprimé)."""
        if decision not in ("accept", "reject"):
            raise LunaError("Décision inconnue : accepte ou refuse.")
        fait = await self._memoire.trancher_fait(fait_id, accepte=decision == "accept")
        if fait is None:
            raise LunaError("Ce fait n'est plus à relire.")
        await self._memoire.enregistrer_evenement(
            EvenementJournal(
                id=nouvel_id("e"),
                ts=self._maintenant(),
                kind="state",
                profile=fait.profile,
                payload={
                    "relecture": fait.id,
                    "predicat": fait.predicate,
                    "decision": decision,
                },
            )
        )
        log.info("Relecture : %s → %s", fait.predicate, fait.status)
        return {"status": fait.status}

    # ── Rouages ──────────────────────────────────────────────────────────

    def _cle(self, entite: str) -> str:
        regle = self._regles[entite]
        return cle_suggestion(regle.categorie, None, entite)

    async def _score(self, cle: str) -> Score:
        enregistre = await self._memoire.score_suggestion(cle)
        if enregistre is None:
            return Score()
        return Score(
            score=enregistre.score,
            refus=enregistre.rejections,
            sourdine_jusqua=enregistre.muted_until,
        )


async def _muet(_evenement: EvenementCarte) -> None:
    """Un émetteur qui n'émet rien. Voir `MoteurVeille._annoncer`."""
