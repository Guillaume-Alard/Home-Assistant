"""L2 — l'entretien nocturne (D3, §4 : « il propose, il ne décide pas »).

Un appel par nuit, borné et chiffré :

* **un seul** appel au modèle, à 3 h 30 par défaut ;
* **au plus 200 événements** repris, les plus récents (H56) ;
* **sauté** s'il n'y a rien de neuf depuis la dernière fois ;
* **sortie typée**, jamais de prose à réinterpréter (H61).

Ce que le modèle rend arrive en `needs_review` — jamais en vigueur. C'est la
lecture littérale de §4 : un fait déduit d'une phrase attend qu'un humain le
relise. Les faits mesurés, eux, passent par les observateurs et n'attendent
personne.

Ce qui part vers l'API, ce sont des **extraits de conversation** — pas des
habitudes. Luna n'envoie jamais « Guillaume se couche à 23 h 20 » à un tiers
(H60). La distinction n'est pas cosmétique : elle est la raison pour laquelle
cette phase est acceptable du tout.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..kernel.contracts import CerveauProvider, MemoireProvider
from ..kernel.ids import nouvel_id
from ..kernel.schemas import EvenementJournal, Fait

log = logging.getLogger("luna.entretien")

#: Les prédicats que le modèle a le droit de proposer. Un prédicat hors de
#: cette liste est jeté sans discuter : c'est le dernier filet après l'`enum`
#: du schéma d'outil, et il ne coûte rien.
PREDICATS_ADMIS = {
    "preference_eclairage",
    "preference_temperature",
    "fait_declare",
}

#: Un extrait plus long que ça n'apprend rien de plus et coûte des jetons.
LONGUEUR_EXTRAIT = 400


class EntretienNocturne:
    def __init__(
        self,
        *,
        cerveau: CerveauProvider,
        memoire: MemoireProvider,
        plafond: int = 200,
        actif: bool = True,
    ) -> None:
        self._cerveau = cerveau
        self._memoire = memoire
        self._plafond = plafond
        self._actif = actif
        self._derniere: datetime | None = None

    @property
    def derniere_passe(self) -> datetime | None:
        return self._derniere

    async def passer(self, maintenant: datetime) -> dict[str, object]:
        """Une nuit d'entretien. Rend un compte rendu, toujours — même vide.

        Ne lève jamais : une nuit ratée ne doit pas emporter l'ordonnanceur, et
        encore moins l'add-on. Ce qui a échoué est dans le compte rendu et dans
        les journaux.
        """
        if not self._actif:
            return {"passe": False, "motif": "désactivé"}

        evenements = await self._memoire.evenements_depuis(
            self._derniere, limite=self._plafond
        )
        extraits = [e for e in evenements if e.kind == "message"]
        if not extraits:
            self._derniere = maintenant
            return {"passe": False, "motif": "rien de neuf", "evenements": 0}

        try:
            bruts = await self._cerveau.extraire_faits(_mettre_en_forme(extraits))
        except Exception as exc:  # noqa: BLE001 — une nuit ratée n'est pas une panne
            log.warning("Entretien nocturne : %s", exc)
            return {"passe": False, "motif": "cerveau indisponible", "erreur": str(exc)}

        deposes = 0
        for brut in bruts:
            fait = _vers_fait(brut, maintenant)
            if fait is None:
                continue
            _, neuf = await self._memoire.observer_fait(fait)
            deposes += int(neuf)

        self._derniere = maintenant
        await self._memoire.enregistrer_evenement(
            EvenementJournal(
                id=nouvel_id("e"),
                ts=maintenant,
                kind="state",
                payload={
                    "entretien": True,
                    "extraits": len(extraits),
                    "proposes": len(bruts),
                    "deposes": deposes,
                },
            )
        )
        log.info(
            "Entretien nocturne : %s extraits, %s fait(s) à relire",
            len(extraits),
            deposes,
        )
        return {
            "passe": True,
            "evenements": len(extraits),
            "proposes": len(bruts),
            "deposes": deposes,
        }


def _mettre_en_forme(evenements: list[EvenementJournal]) -> str:
    """Les extraits, dans l'ordre, coupés court et étiquetés par leur auteur."""
    lignes = []
    for evenement in evenements:
        texte = str(evenement.payload.get("text") or "").strip()
        if not texte:
            continue
        role = str(evenement.payload.get("role") or "?")
        qui = evenement.profile or role
        lignes.append(
            f"[{evenement.ts.strftime('%d/%m %H:%M')}] {qui} : {texte[:LONGUEUR_EXTRAIT]}"
        )
    return "\n".join(lignes)


def _vers_fait(brut: dict[str, str], maintenant: datetime) -> Fait | None:
    """Un fait du modèle, validé. Tout ce qui cloche est jeté sans bruit.

    `needs_review` n'est pas une option ici : c'est la seule valeur possible.
    Un fait déduit d'une conversation n'entre jamais en vigueur tout seul (D2).
    """
    predicat = str(brut.get("predicat") or "")
    valeur = str(brut.get("valeur") or "").strip()
    if predicat not in PREDICATS_ADMIS or not valeur:
        return None
    profil = str(brut.get("profil") or "").strip() or None
    return Fait(
        id=nouvel_id("f"),
        predicate=predicat,
        value=valeur,
        profile=profil,
        entity_id=None,
        category=predicat,
        status="needs_review",
        source="modele",
        created_at=maintenant,
        last_seen_at=maintenant,
        observations=1,
        why=str(brut.get("pourquoi") or "").strip(),
    )
