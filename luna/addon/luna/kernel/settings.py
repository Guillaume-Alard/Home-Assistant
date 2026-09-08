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

from pydantic import BaseModel, ConfigDict, Field

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

    reglages = Reglages(**brut)  # type: ignore[arg-type]

    # Le Supervisor fournit le jeton ; en développement, on vise une instance
    # HA classique avec un jeton longue durée.
    jeton = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("LUNA_JETON_HA", "")
    url = os.environ.get("LUNA_URL_HA") or URL_HA_SUPERVISOR
    base = Path(os.environ.get("LUNA_BASE", str(CHEMIN_BASE)))
    return reglages.model_copy(
        update={"jeton_ha": jeton, "url_ha": url, "chemin_base": base}
    )
