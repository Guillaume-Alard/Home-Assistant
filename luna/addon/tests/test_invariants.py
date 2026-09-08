"""Les tests structurels de docs/P1-CONTRATS.md §14.

Ils ne vérifient pas un comportement mais une **propriété du code**. Chacun
empêche une classe entière de régressions que la revue humaine laisse passer :

  1. qu'un chemin de code atteigne `appeler_service` sans passer par l'arbitre ;
  2. qu'un service oublié reçoive autre chose que le niveau 3 ;
  3. qu'un outil déclaré à Claude ne soit routé nulle part ;
  4. qu'un domaine hors périmètre v1 devienne joignable par un outil ;
  5. que Luna écrive dans la configuration de Home Assistant (P5, E1).
"""

from __future__ import annotations

import ast
import configparser
from pathlib import Path

import pytest

from luna.engine import arbiter
from luna.engine.tools import NOMS_OUTILS
from luna.kernel.autonomy import NIVEAU_PAR_DEFAUT, REGISTRE, Niveau, niveau_de

PAQUET = Path(__file__).resolve().parent.parent / "luna"

#: Qui a le droit de seulement mentionner `appeler_service`.
#: `home.py` la définit, `contracts.py` la déclare, `arbiter.py` l'appelle.
FICHIERS_AUTORISES = {
    "providers/home.py",
    "kernel/contracts.py",
    "engine/arbiter.py",
}

NOM_INTERDIT = "appeler_service"


def _fichiers_python() -> list[Path]:
    return sorted(PAQUET.rglob("*.py"))


def _mentionne(chemin: Path) -> bool:
    arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Attribute) and noeud.attr == NOM_INTERDIT:
            return True
        if (
            isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef))
            and noeud.name == NOM_INTERDIT
        ):
            return True
        if isinstance(noeud, ast.Name) and noeud.id == NOM_INTERDIT:
            return True
    return False


class TestInvariantCallService:
    """§9.2 : la détection critique et l'exécution ne passent jamais par le
    modèle, et l'exécution ne passe que par l'arbitre."""

    def test_seuls_trois_fichiers_touchent_a_lecriture(self):
        coupables = {
            str(chemin.relative_to(PAQUET))
            for chemin in _fichiers_python()
            if _mentionne(chemin)
        }
        assert coupables == FICHIERS_AUTORISES, (
            "Un fichier hors de l'arbitre atteint l'écriture Home Assistant. "
            "C'est l'invariant de sécurité du projet (§9.2) : toute écriture "
            "doit passer par engine/arbiter.py."
        )

    def test_larbitre_lappelle_vraiment(self):
        """Le test précédent serait vert si plus personne n'écrivait jamais."""
        source = (PAQUET / "engine" / "arbiter.py").read_text(encoding="utf-8")
        assert f"_maison.{NOM_INTERDIT}(" in source

    def test_lorchestrateur_ny_touche_pas(self):
        source = (PAQUET / "engine" / "orchestrator.py").read_text(encoding="utf-8")
        assert NOM_INTERDIT not in source


class TestNiveauParDefaut:
    def test_le_defaut_est_bien_trois(self):
        assert NIVEAU_PAR_DEFAUT is Niveau.PERSISTANT
        assert niveau_de("quoi", "que_ce_soit") is Niveau.PERSISTANT

    def test_aucune_entree_du_registre_nest_au_niveau_0(self):
        """Le niveau 0 est « répondre, informer » : il ne correspond à aucun
        appel de service. Une entrée à 0 serait une exécution libre déguisée."""
        assert all(niveau > Niveau.REPONDRE for niveau in REGISTRE.values())


class TestOutils:
    async def test_chaque_outil_declare_est_route(self, arbitre, contexte):
        """Un outil déclaré à Claude mais non routé produirait un « Outil
        inconnu » au pire moment — en pleine conversation."""

        async def emettre(_):
            return None

        for nom in NOMS_OUTILS:
            resultat = await arbitre.executer(
                nom, {}, contexte=contexte, message_id="m", emettre=emettre
            )
            assert "Outil inconnu" not in resultat.contenu, nom

    def test_aucun_outil_ne_vise_un_domaine_hors_perimetre(self):
        """Décision A2 : le refus est structurel. Claude ne peut pas demander
        `cover`, `lock`, `alarm_control_panel` ni `notify`."""
        joignables = set()
        for nom in dir(arbiter):
            if nom.startswith("DOMAINES_"):
                joignables.update(getattr(arbiter, nom))
        interdits = {
            cle.split(".", 1)[0]
            for cle, niveau in REGISTRE.items()
            if niveau is Niveau.INTERDIT_V1
        }
        assert joignables & interdits == set(), (
            f"Un outil peut atteindre un domaine de niveau 5 : {joignables & interdits}"
        )

    def test_les_schemas_sont_stricts(self):
        """`strict: true` exige `additionalProperties: false` et un `required`
        exhaustif ; sans ça, l'API renvoie des entrées à moitié remplies."""
        from luna.engine.tools import OUTILS

        for outil in OUTILS:
            schema = outil["input_schema"]
            assert outil["strict"] is True, outil["name"]
            assert schema["additionalProperties"] is False, outil["name"]
            assert set(schema["required"]) == set(schema["properties"]), outil["name"]

    def test_lordre_des_outils_ne_bouge_pas(self):
        """L'ordre de rendu est tools → system → messages : réordonner la liste
        invalide tout le préfixe mis en cache, donc double le coût."""
        assert NOMS_OUTILS == (
            "lister_pieces",
            "etat_maison",
            "commander_lumiere",
            "commander_interrupteur",
            "activer_scene",
            "regler_thermostat",
        )


class TestCouches:
    """§3.1 — les contrats sont exécutés par `lint-imports` en CI ; ici on
    vérifie seulement qu'ils n'ont pas été discrètement retirés du fichier."""

    def test_les_quatre_contrats_sont_declares(self):
        config = configparser.ConfigParser()
        config.read(Path(__file__).resolve().parent.parent / ".importlinter")
        contrats = [s for s in config.sections() if s.startswith("importlinter:contract")]
        assert len(contrats) == 4
        assert all(config[s]["type"] == "forbidden" for s in contrats)

    def test_le_noyau_ninterdit_pas_que_les_couches(self):
        """Le quatrième contrat (H15) : sans lui, « stdlib + pydantic
        uniquement » n'est vérifié par rien."""
        config = configparser.ConfigParser()
        config.read(Path(__file__).resolve().parent.parent / ".importlinter")
        interdits = config["importlinter:contract:l0-stdlib"]["forbidden_modules"]
        assert "anthropic" in interdits
        assert "aiohttp" in interdits


class TestPromptSysteme:
    def test_rien_de_variable_dans_le_bloc_mis_en_cache(self):
        """Le doublon volontaire de test_claude.py : c'est la régression la plus
        coûteuse et la plus silencieuse du projet."""
        from luna.providers.claude import PROMPT_SYSTEME

        for indice in ("{", "}", "%s", "2026", "Contexte :"):
            assert indice not in PROMPT_SYSTEME, indice

    def test_le_prompt_ne_promet_pas_les_phases_futures(self):
        from luna.providers.claude import PROMPT_SYSTEME

        assert "phase 1" in PROMPT_SYSTEME
        for absent in ("ouvrants", "alarme"):
            assert absent in PROMPT_SYSTEME, (
                "le prompt doit dire explicitement ce que Luna ne fait pas"
            )


@pytest.mark.parametrize("chemin", _fichiers_python(), ids=lambda p: p.name)
def test_chaque_module_a_une_docstring(chemin: Path):
    """Une couche sans explication est une couche qu'on contournera."""
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    assert ast.get_docstring(arbre), f"{chemin.name} n'a pas de docstring de module"


#: P5, E1 — les commandes WebSocket qui **écrivent** dans la configuration de
#: Home Assistant. Aucun fichier du projet n'a le droit de les mentionner.
#:
#: Ce test-là est le plus important de P5, et le seul de la liste qui ne
#: protège pas d'une erreur d'inattention. Luna est administratrice de Home
#: Assistant : elle passe par le Supervisor, dont l'utilisateur vit dans
#: `GROUP_ID_ADMIN`. Rien du côté de Home Assistant ne l'empêcherait de
#: réécrire Loggia ou de désactiver une intégration. La seule barrière est
#: celle-ci, et §10 raconte ce qui arrive quand elle n'existe pas.
COMMANDES_ECRITURE = (
    "lovelace/config/save",
    "lovelace/config/delete",
    "config_entries/update",
    "config_entries/disable",
    "config/automation/config",
)

#: Les fichiers qui ont le droit de **nommer** ces commandes pour dire qu'elles
#: sont interdites — ce document-ci, et lui seul.
FICHIERS_TEMOINS = {"tests/test_invariants.py"}


class TestGardienneNecritJamais:
    """P5, E1 : la gardienne lit. Elle n'écrit pas, et ça se prouve."""

    def test_aucune_commande_decriture_dans_le_paquet(self):
        coupables: dict[str, list[str]] = {}
        for chemin in _fichiers_python():
            texte = chemin.read_text(encoding="utf-8")
            trouvees = [c for c in COMMANDES_ECRITURE if c in texte]
            if trouvees:
                coupables[str(chemin.relative_to(PAQUET))] = trouvees
        assert not coupables, (
            "Luna est administratrice de Home Assistant : rien ne l'empêche "
            f"d'écrire, sauf ce test. Écritures trouvées : {coupables}"
        )

    def test_les_lectures_de_la_gardienne_sont_toutes_declarees(self):
        """Ce que la gardienne demande à Home Assistant est une liste fermée.

        Sans ça, une lecture ajoutée un jour de fatigue passerait inaperçue —
        et la frontière entre lire et écrire est exactement ce qui tient cette
        phase debout.
        """
        from luna.providers.home import COMMANDES_GARDIENNE

        assert all(
            not c.endswith(("/save", "/delete", "/update", "/disable"))
            for c in COMMANDES_GARDIENNE
        )
        texte = (PAQUET / "providers" / "home.py").read_text(encoding="utf-8")
        for commande in COMMANDES_GARDIENNE:
            assert texte.count(f'"{commande}"') >= 1, (
                f"{commande} est déclarée mais n'est utilisée nulle part"
            )

    def test_laddon_na_pas_acces_en_ecriture_au_disque(self):
        """`config.yaml` ne mappe que `share:ro`.

        C'est le second refus de E1 : pas d'accès à `/config`, donc pas
        d'YAML écrit par un modèle dans une configuration vivante.
        """
        manifeste = (PAQUET.parent / "config.yaml").read_text(encoding="utf-8")
        cartes = [
            ligne.strip().lstrip("- ").strip()
            for ligne in manifeste.splitlines()
            if ligne.strip().startswith("- ") and ":" in ligne and "/" not in ligne
        ]
        montages = [
            c
            for c in cartes
            if c.split(":")[0] in {"share", "config", "ssl", "addons", "backup", "media"}
        ]
        assert montages == ["share:ro"], (
            f"L'add-on ne doit monter que « share:ro ». Trouvé : {montages}"
        )


class TestNiveauDuCorrectif:
    """P5, E7 : le seul acte réparateur de la phase est en niveau 4."""

    def test_recharger_une_integration_demande_une_validation(self):
        assert niveau_de("homeassistant", "reload_config_entry") == Niveau.CONFIGURATION

    def test_redemarrer_home_assistant_reste_en_niveau_quatre(self):
        """Une gardienne qui redémarre la maison pour réparer une pile est pire
        que la panne."""
        assert niveau_de("homeassistant", "restart") == Niveau.CONFIGURATION
