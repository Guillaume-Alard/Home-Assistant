"""L0 — les réglages.

Source unique : `/data/options.json`, écrit par le Supervisor à partir des
options de l'add-on. En développement, les variables d'environnement prennent
le relais pour qu'on puisse lancer Luna hors HAOS.

Les deux secrets — clé Anthropic et secret du relais — ne sortent jamais d'ici :
ni dans les journaux, ni vers la carte, ni dans le dépôt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import ActionHA

CHEMIN_OPTIONS = Path("/data/options.json")
CHEMIN_BASE = Path("/data/luna.db")

#: Fourni par le Supervisor quand `homeassistant_api: true` (H6).
URL_HA_SUPERVISOR = "ws://supervisor/core/websocket"


class CorrespondanceProfil(BaseModel):
    model_config = ConfigDict(extra="forbid")

    utilisateur_ha: str
    profil: str
    #: L'entité `device_tracker` de la personne (H42). Sans elle, la présence
    #: ne présume rien et la voix décide seule.
    presence: str = ""


class RegleVeille(BaseModel):
    """Une règle de veille (D1, H53).

    La **détection** n'est pas ici : elle vit dans un `binary_sensor` de Home
    Assistant, visible dans les outils de développement et testable sans lire
    une ligne de Python. Luna ne regarde que le verdict, et décide de la
    pertinence, de la formulation et du moment.
    """

    model_config = ConfigDict(extra="forbid")

    entite: str
    categorie: str = "veille"
    niveau: Literal["info", "warning", "critical"] = "warning"
    message: str
    #: Pourquoi Luna le dit. Montré tel quel dans le tiroir (§8, jamais un
    #: « parce que »).
    raison: str = ""
    #: `false` sur une règle qui a le droit de parler après 22 h 30 — le rappel
    #: de coucher, dont c'est tout l'intérêt (H58).
    silence: bool = True
    #: Forcer ou interdire l'annonce vocale pour cette règle. `null` (défaut) :
    #: suivre les niveaux déclarés dans `annonce.niveaux`.
    annonce: bool | None = None
    #: Prédicat d'un fait observé qui **conditionne et justifie** la règle. Avec
    #: `heure_de_coucher`, le rappel ne part que si Luna a vraiment observé une
    #: heure de coucher, et sa raison la cite. Sans habitude assez sûre, pas de
    #: rappel : c'est ce que veut dire « pertinent » dans §11.
    fait: str = ""
    #: Ce que proposera le bouton « Agir ». Chaque acte repasse par l'arbitre,
    #: avec son niveau : un `cover.close_cover` déclaré ici sera refusé (D8).
    actions: list[ActionHA] = Field(default_factory=list)


class Observateurs(BaseModel):
    """Ce que les observateurs déterministes de D2 ont le droit de regarder.

    Rien par défaut. Un observateur sans entité déclarée ne tourne pas — Luna
    n'invente pas ce qu'elle surveille.
    """

    model_config = ConfigDict(extra="forbid")

    #: Le `binary_sensor` qui dit « la maison se couche ». Son passage à `on`
    #: est l'observation.
    coucher: str = ""
    #: À qui attribuer l'heure de coucher observée. Vide = la maison.
    coucher_profil: str = ""
    #: Les séquences récurrentes ne sont cherchées que si on le demande : c'est
    #: l'observateur le plus bavard, et le moins demandé par §5.
    sequences: bool = False


class Annonce(BaseModel):
    """Faire dire une alerte à voix haute sur une enceinte de la maison.

    Éteint par défaut : sans `enceinte`, rien n'est jamais annoncé. C'est §1 —
    ce qui n'est pas demandé n'existe pas — appliqué à une fonction qui a été
    demandée explicitement, et qui reste donc explicitement à activer.
    """

    model_config = ConfigDict(extra="forbid")

    #: Le `media_player` qui parle. Vide = aucune annonce, jamais.
    enceinte: str = ""
    #: L'entité TTS qui fabrique la voix. C'est celle du pipeline Assist.
    moteur: str = "tts.piper"
    #: Les niveaux d'alerte qui méritent qu'on parle. Une `info` s'affiche
    #: dans le tiroir sans interrompre la pièce.
    niveaux: list[Literal["info", "warning", "critical"]] = Field(
        default_factory=lambda: ["warning", "critical"]
    )
    #: Se taire pendant les heures de silence — **y compris pour une alerte
    #: `critical`**. Une alerte critique a le droit d'apparaître dans le tiroir
    #: à 3 h du matin ; réveiller la maison est une autre décision.
    silence: bool = True


class Gardienne(BaseModel):
    """La surveillance de l'installation (P5).

    Allumée par défaut, contrairement à l'annonce vocale : elle ne fait que
    lire, et ce qu'elle trouve arrive dans le tiroir de veille comme le reste.
    Ce qu'elle ne fait **jamais**, c'est écrire dans la configuration de Home
    Assistant — et ça ne se règle pas ici, c'est vérifié statiquement (E1, H73).
    """

    model_config = ConfigDict(extra="forbid")

    active: bool = True
    #: H66 — durée d'indisponibilité continue avant qu'une entité compte comme
    #: cassée. En dessous, c'est un hoquet.
    minutes_avant_panne: int = 30
    #: Les entités hors ligne par choix : un vieux capteur, une prise
    #: débranchée pour l'hiver. Sans cette liste elles polluent le tiroir pour
    #: toujours, et c'est comme ça qu'on cesse de le regarder.
    ignorer: list[str] = Field(default_factory=list)
    #: Lire la configuration de Loggia pour trouver les cartes qui pointent
    #: dans le vide. Lecture stricte, une fois par jour, rien n'en sort.
    loggia: bool = True
    #: Le dashboard à relire. Vide = celui par défaut.
    loggia_url_path: str = ""
    #: Lire `system_log` pour les automatisations en erreur. Demande des droits
    #: d'administrateur ; si Luna ne les a pas, la famille s'éteint et le dit.
    journal_systeme: bool = True


class Reglages(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anthropic_api_key: str = ""
    modele: str = "claude-sonnet-5"
    effort: str = "low"
    #: Le modèle d'empreinte de locuteur, en ONNX (C2). Vide = pas de voix.
    modele_voix: str = ""
    relay_secret: str = ""
    relay_port: int = 8099
    fuseau: str = "Europe/Paris"
    journal: str = "info"
    profils: list[CorrespondanceProfil] = Field(default_factory=list)
    profil_par_defaut: str = "guest"

    # ── Habitudes et veille (P4) ─────────────────────────────────────────
    veille: list[RegleVeille] = Field(default_factory=list)
    #: H55 — assez tard pour que la journée soit finie, assez tôt pour que le
    #: rapport soit prêt au réveil. `HH:MM`, dans le fuseau des réglages.
    heure_entretien: str = "03:30"
    #: H56 — sans plafond, la facture grandit avec la maison.
    evenements_par_entretien: int = 200
    #: Coupe l'entretien nocturne sans toucher au reste de la veille.
    entretien_actif: bool = True
    observateurs: Observateurs = Field(default_factory=Observateurs)
    annonce: Annonce = Field(default_factory=Annonce)
    gardienne: Gardienne = Field(default_factory=Gardienne)

    # Non exposés dans les options : déduits de l'environnement.
    url_ha: str = URL_HA_SUPERVISOR
    jeton_ha: str = ""
    chemin_base: Path = CHEMIN_BASE

    def profil_pour(
        self, *, nom: str | None = None, identifiant: str | None = None
    ) -> str:
        """Correspondance utilisateur HA → profil Luna (décision A6).

        Le nom **ou** l'identifiant peuvent servir de clé : selon les versions
        de Home Assistant, la carte d'utilisateur expose l'un ou l'autre.
        """
        for entree in self.profils:
            if nom and entree.utilisateur_ha == nom:
                return entree.profil
            if identifiant and entree.utilisateur_ha == identifiant:
                return entree.profil
        return self.profil_par_defaut

    def utilisateur_connu(self, *, nom: str | None, identifiant: str | None) -> bool:
        """L'utilisateur Home Assistant désigne-t-il un profil à lui ?

        C'est la règle de C1 : si oui, l'identité est déjà résolue et il n'y a
        aucune empreinte à calculer. Si non, on est sur un appareil partagé —
        l'iPad du couloir — et c'est là que P3 sert.
        """
        return any(
            (nom and entree.utilisateur_ha == nom)
            or (identifiant and entree.utilisateur_ha == identifiant)
            for entree in self.profils
        )

    def capteur_presence(self, profil: str) -> str | None:
        for entree in self.profils:
            if entree.profil == profil and entree.presence:
                return entree.presence
        return None

    def profils_declares(self) -> list[str]:
        vus: list[str] = []
        for entree in self.profils:
            if entree.profil not in vus:
                vus.append(entree.profil)
        return vus

    def moment_entretien(self) -> tuple[int, int]:
        """`heure_entretien` en (heure, minute). Une valeur illisible retombe
        sur 3 h 30 plutôt que d'empêcher Luna de démarrer : un entretien qui
        tourne à la mauvaise heure vaut mieux qu'un add-on qui refuse de partir.
        """
        try:
            heures, minutes = self.heure_entretien.split(":", 1)
            h, m = int(heures), int(minutes)
        except ValueError:
            return 3, 30
        if not (0 <= h < 24 and 0 <= m < 60):
            return 3, 30
        return h, m

    def secrets_masques(self) -> dict[str, object]:
        """Vue des réglages sûre à écrire dans les journaux."""
        vue = self.model_dump(mode="json")
        for cle in ("anthropic_api_key", "relay_secret", "jeton_ha"):
            vue[cle] = "***" if vue.get(cle) else ""
        return vue


def charger(chemin: Path | None = None) -> Reglages:
    """Lit `/data/options.json`, complété par l'environnement."""
    source = chemin or CHEMIN_OPTIONS
    brut: dict[str, object] = {}
    if source.is_file():
        brut = json.loads(source.read_text(encoding="utf-8"))

    for cle_env, cle in (
        ("LUNA_ANTHROPIC_API_KEY", "anthropic_api_key"),
        ("LUNA_MODELE", "modele"),
        ("LUNA_EFFORT", "effort"),
        ("LUNA_MODELE_VOIX", "modele_voix"),
        ("LUNA_RELAY_SECRET", "relay_secret"),
        ("LUNA_FUSEAU", "fuseau"),
        ("LUNA_JOURNAL", "journal"),
    ):
        valeur = os.environ.get(cle_env)
        if valeur:
            brut[cle] = valeur
    if port := os.environ.get("LUNA_RELAY_PORT"):
        brut["relay_port"] = int(port)
    if heure := os.environ.get("LUNA_HEURE_ENTRETIEN"):
        brut["heure_entretien"] = heure

    reglages = Reglages(**brut)  # type: ignore[arg-type]

    # Le Supervisor fournit le jeton ; en développement, on vise une instance
    # HA classique avec un jeton longue durée.
    jeton = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("LUNA_JETON_HA", "")
    url = os.environ.get("LUNA_URL_HA") or URL_HA_SUPERVISOR
    base = Path(os.environ.get("LUNA_BASE", str(CHEMIN_BASE)))
    return reglages.model_copy(
        update={"jeton_ha": jeton, "url_ha": url, "chemin_base": base}
    )
