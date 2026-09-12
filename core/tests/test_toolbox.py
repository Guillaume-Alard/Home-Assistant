"""Outils LLM : lecture libre, écriture uniquement à travers le moteur."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import PROTOCOLS_TEST_YML, make_ha_stub

from app.actions.engine import ActionEngine
from app.actions.executors import build_registry
from app.brain.toolbox import Toolbox
from app.ha.protocols import ProtocolBook
from app.store import Store


@pytest.fixture()
async def box(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "data"))
    from app.config import Settings
    from app.monitors.health import HealthService

    ha, calls = make_ha_stub()
    # Un lecteur média dans le salon, pour les outils musique (Phase 9)
    ha._states["media_player.salon"] = {
        "state": "playing",
        "attributes": {"friendly_name": "Enceinte salon", "volume_level": 0.3,
                       "media_title": "So What", "media_artist": "Miles Davis",
                       "source": "Spotify", "source_list": ["Spotify", "Radio"]},
    }
    ha._entity_area["media_player.salon"] = "area_salon"
    proto_path = tmp_path / "protocols.yml"
    proto_path.write_text(PROTOCOLS_TEST_YML, encoding="utf-8")
    protocols = ProtocolBook.load(proto_path)
    store = Store(tmp_path / "toolbox.db")
    await store.open()
    engine = ActionEngine(build_registry(ha, protocols), store)
    health = HealthService(Settings.from_env(), ha)
    import app
    from app.selfmod import SelfSource

    app_dir = Path(app.__file__).resolve().parent
    source = SelfSource(app_dir, app_dir.parent / "ui")
    from app.routines import RoutineService

    async def _noop(*a):
        pass

    routines = RoutineService(Settings.from_env(), ha, engine, store, announce=_noop, on_change=_noop)
    from app.ha.media import MediaConfig

    media = MediaConfig(presets={"jazz": {"source": "Spotify"}}, default_room="Salon")
    toolbox = Toolbox(
        ha, engine, protocols, store, health=health,
        source=source, routines=routines, media=media, reminders=True, tz="Europe/Paris",
    )
    yield SimpleNamespace(
        ha=ha, calls=calls, toolbox=toolbox, store=store, engine=engine
    )
    await store.close()


async def _run(box, name, args):
    return await box.toolbox.run(name, args, utterance="demande de test", source="text")


async def test_specs_stables_et_completes(box):
    specs = box.toolbox.specs()
    names = [s["name"] for s in specs]
    assert names == [
        "etat_maison", "details_entite", "action_domotique", "lancer_protocole",
        "creer_proposition", "lister_propositions", "liste_pieces", "chercher_entites",
        "sante_systemes", "audit_systemes",
        "memoriser", "lister_souvenirs", "oublier",
        "lire_mon_code", "proposer_evolution", "lister_evolutions",
        "proposer_routine", "lancer_routine", "lister_routines",
        "etat_musique", "musique",
        "minuteur", "rappel", "lister_rappels", "annuler_rappel",
    ]
    assert all(s["description"] for s in specs)


async def test_etat_maison_par_zone(box):
    content, is_error = await _run(box, "etat_maison", {"zone": "salon"})
    assert not is_error
    data = json.loads(content)
    assert data["piece"] == "Salon"
    assert "light.salon" in data["entites"]


async def test_etat_maison_zone_inconnue(box):
    content, is_error = await _run(box, "etat_maison", {"zone": "grenier"})
    assert is_error
    assert "Salon" in content  # il suggère les pièces existantes


async def test_action_domotique_via_moteur(box):
    content, is_error = await _run(
        box, "action_domotique", {"operation": "allumer", "zone": "salon"}
    )
    # Le résumé de l'action est stable ; le verdict de vérification (brique 1)
    # peut s'y ajouter selon l'état relu de Nova.
    assert not is_error and content.startswith("Allumé : Plafonnier salon.")
    assert box.calls == [("homeassistant", "turn_on", None, {"entity_id": ["light.salon"]})]

    journal = await box.store.list_journal()
    assert "via LLM" in journal[0]["authorization"] or "via LLM" in journal[0]["actor"]


async def test_le_deverrouillage_est_hors_de_portee_du_llm(box):
    content, is_error = await _run(
        box, "action_domotique", {"operation": "deverrouiller", "entity_ids": ["lock.entree"]}
    )
    assert is_error and "inconnue" in content.lower()
    assert box.calls == []


async def test_creer_proposition(box):
    content, is_error = await _run(box, "creer_proposition", {
        "titre": "Purger la base",
        "justification": "Elle grossit",
        "risque": "moyen",
        "rollback": "Aucun impact",
        "action": {"domain": "recorder", "service": "purge", "data": {"keep_days": 30}},
    })
    assert not is_error and "n°" in content
    assert box.calls == []  # rien d'exécuté à la création

    pending = await box.store.list_proposals("pending")
    assert pending[0]["title"] == "Purger la base"
    assert pending[0]["action_id"] == "ha.call_service"
    assert pending[0]["params"]["domain"] == "recorder"


async def test_lancer_protocole_inconnu(box):
    content, is_error = await _run(box, "lancer_protocole", {"nom": "apocalypse"})
    assert is_error and "Test" in content  # liste les protocoles réels


async def test_lancer_protocole_sensible_transmet_la_confirmation(box):
    content, is_error = await _run(box, "lancer_protocole", {"nom": "verrou"})
    assert not is_error  # needs_confirmation n'est pas une erreur : consigne à relayer
    assert "confirme" in content.lower()
    assert box.calls == []


async def test_outil_inconnu(box):
    content, is_error = await box.toolbox.run("hacker_le_pentagone", {}, utterance="", source="text")
    assert is_error


async def test_chercher_entites_meme_hors_piece(box):
    # Le capteur de porte n'a AUCUNE pièce assignée : la recherche le trouve quand même
    content, is_error = await _run(box, "chercher_entites", {"recherche": "porte"})
    assert not is_error
    data = json.loads(content)
    found = next(e for e in data if e["entity_id"] == "binary_sensor.capteur_porte_entree")
    assert found["piece"] is None and found["device_class"] == "door"

    # Recherche par device_class et filtre de domaine
    content, _ = await _run(box, "chercher_entites", {"recherche": "door", "domaine": "binary_sensor"})
    assert "capteur_porte_entree" in content

    content, is_error = await _run(box, "chercher_entites", {"recherche": "licorne"})
    assert not is_error and "Aucune entité" in content


async def test_apercu_montre_les_portes(box):
    content, is_error = await _run(box, "etat_maison", {})
    assert not is_error
    data = json.loads(content)
    hors_piece = data.get("(hors pièce)") or {}
    assert any("fermé" in v for v in (hors_piece.get("notable") or {}).values())


async def test_sante_nova(box):
    content, is_error = await _run(box, "sante_systemes", {})
    assert not is_error and '"nova"' in content


async def test_proposition_de_service_sensible_escaladee(box):
    """Un lock.unlock enveloppé dans une proposition « moyenne » devient sensible :
    impossible de l'approuver à la voix — le contournement est fermé."""
    content, is_error = await _run(box, "creer_proposition", {
        "titre": "Ouvrir la porte",
        "justification": "test",
        "risque": "moyen",  # le LLM minimise — le moteur ne le croit pas
        "action": {"domain": "lock", "service": "unlock",
                   "target": {"entity_id": ["lock.entree"]}},
    })
    assert not is_error and "sensible" in content

    pending = (await box.store.list_proposals("pending"))[0]
    assert pending["risk"] == "sensitive"

    from app.actions.engine import ActionEngine  # l'engine du fixture
    engine = box.toolbox._engine
    _, msg = await engine.decide(pending["num"], "approve", via="voice")
    assert "interface" in msg
    assert box.calls == []


# ── Mémoire (Phase 1) : enrichissement de contexte, jamais une action ─────────

async def test_memoriser_puis_lister(box):
    content, is_error = await _run(box, "memoriser", {
        "contenu": "Préfère des réponses très courtes", "categorie": "preference",
    })
    assert not is_error and "noté" in content.lower()

    stored = await box.store.list_memories(subject="guillaume")
    assert [m["content"] for m in stored] == ["Préfère des réponses très courtes"]
    assert stored[0]["category"] == "preference"
    assert stored[0]["source"] == "luna"

    listing, is_error = await _run(box, "lister_souvenirs", {})
    assert not is_error
    data = json.loads(listing)
    assert data[0]["contenu"] == "Préfère des réponses très courtes"
    assert data[0]["id"] == stored[0]["id"]


async def test_memoriser_anti_doublon(box):
    await _run(box, "memoriser", {"contenu": "Habite à Lyon", "categorie": "fait"})
    # Même contenu à la casse/accents près → pas de second enregistrement
    content, _ = await _run(box, "memoriser", {"contenu": "habite à lyon"})
    assert "déjà" in content.lower()
    assert len(await box.store.list_memories(subject="guillaume")) == 1


async def test_categorie_inconnue_repli_fait(box):
    await _run(box, "memoriser", {"contenu": "Aime le jazz", "categorie": "n'importe quoi"})
    assert (await box.store.list_memories(subject="guillaume"))[0]["category"] == "fait"


async def test_oublier(box):
    await _run(box, "memoriser", {"contenu": "À oublier", "categorie": "fait"})
    mem_id = (await box.store.list_memories(subject="guillaume"))[0]["id"]

    content, is_error = await _run(box, "oublier", {"id": mem_id})
    assert not is_error and "oublié" in content.lower()
    assert await box.store.list_memories(subject="guillaume") == []

    # Oublier un id inconnu est une erreur douce, pas une exception
    content, is_error = await _run(box, "oublier", {"id": "inexistant"})
    assert is_error and "trouvé" in content.lower()


async def test_memoire_ne_cree_jamais_de_proposition(box):
    """Garde-fou : la mémoire n'est PAS une action — rien ne passe par le moteur,
    aucune proposition n'est créée, Nova n'est jamais appelée."""
    await _run(box, "memoriser", {"contenu": "Un fait quelconque", "categorie": "fait"})
    await _run(box, "oublier", {"id": "peu importe"})
    assert await box.store.list_proposals() == []
    assert box.calls == []  # aucun appel de service Home Assistant


# ── Reconnaissance de locuteur (Phase 2) : droits selon qui parle ─────────────

from app.identity import OWNER, UNKNOWN, Speaker  # noqa: E402

_HOUSEHOLD = Speaker(key="cam", name="Camille", known=True, is_owner=False, score=0.9)


async def _run_as(box, name, args, speaker):
    return await box.toolbox.run(
        name, args, utterance="demande", source="voice", speaker=speaker
    )


async def test_invite_ne_peut_pas_agir(box):
    content, is_error = await _run_as(
        box, "action_domotique", {"operation": "allumer", "zone": "salon"}, UNKNOWN
    )
    assert not is_error and "reconnais pas" in content.lower()
    assert box.calls == []  # RIEN n'a été envoyé à Nova


async def test_invite_lecture_autorisee(box):
    # Lecture d'état : ouverte à tous, invité compris
    content, is_error = await _run_as(box, "etat_maison", {"zone": "salon"}, UNKNOWN)
    assert not is_error
    assert json.loads(content)["piece"] == "Salon"


async def test_invite_pas_de_memoire(box):
    content, is_error = await _run_as(box, "memoriser", {"contenu": "test"}, UNKNOWN)
    assert not is_error and "reconnais pas" in content.lower()
    assert await box.store.list_memories() == []


async def test_maisonnee_agit_mais_pas_admin(box):
    # Une personne reconnue (non-propriétaire) pilote la domotique courante…
    content, is_error = await _run_as(
        box, "action_domotique", {"operation": "allumer", "zone": "salon"}, _HOUSEHOLD
    )
    assert not is_error and box.calls  # action passée au moteur

    # … mais pas les outils d'administration (réservés à Guillaume)
    content, is_error = await _run_as(box, "sante_systemes", {}, _HOUSEHOLD)
    assert not is_error and "réservé à guillaume" in content.lower()


async def test_memoire_rangee_par_locuteur(box):
    await _run_as(box, "memoriser", {"contenu": "aime le jazz"}, OWNER)
    await _run_as(box, "memoriser", {"contenu": "préfère le thé"}, _HOUSEHOLD)
    gui = await box.store.list_memories(subject="guillaume")
    cam = await box.store.list_memories(subject="cam")
    assert [m["content"] for m in gui] == ["aime le jazz"]
    assert [m["content"] for m in cam] == ["préfère le thé"]


async def test_oublier_ne_traverse_pas_les_profils(box):
    await _run_as(box, "memoriser", {"contenu": "secret de Guillaume"}, OWNER)
    mem_id = (await box.store.list_memories(subject="guillaume"))[0]["id"]
    # Camille tente d'oublier un souvenir de Guillaume via son id → refus
    content, is_error = await _run_as(box, "oublier", {"id": mem_id}, _HOUSEHOLD)
    assert is_error and "pas trouvé" in content.lower()
    assert len(await box.store.list_memories(subject="guillaume")) == 1


# ── Auto-amélioration encadrée (Phase 6) : Luna PROPOSE, jamais n'applique ─────

_BENIGN_DIFF = (
    "--- a/docs/NOTES.md\n+++ b/docs/NOTES.md\n@@ -1 +1,2 @@\n"
    " Notes\n+Une ligne de plus.\n"
)
_GUARDRAIL_DIFF = (
    "--- a/core/app/identity.py\n+++ b/core/app/identity.py\n@@ -1 +1,2 @@\n"
    " x\n+OWNER = None\n"
)


async def test_proposer_evolution_stocke_une_proposition(box):
    content, is_error = await _run_as(
        box, "proposer_evolution",
        {"titre": "Petite note", "motivation": "clarté", "diff": _BENIGN_DIFF}, OWNER,
    )
    assert not is_error
    data = json.loads(content)
    assert data["fichiers"] == ["docs/NOTES.md"] and data["lignes"]["+"] == 1
    rows = await box.store.list_suggestions()
    assert len(rows) == 1 and rows[0]["status"] == "pending"


async def test_proposer_evolution_refuse_un_garde_fou(box):
    content, is_error = await _run_as(
        box, "proposer_evolution",
        {"titre": "toucher identity", "motivation": "x", "diff": _GUARDRAIL_DIFF}, OWNER,
    )
    # Refus de politique : message clair, et RIEN n'est stocké.
    assert not is_error and "je ne peux pas" in content.lower()
    assert await box.store.list_suggestions() == []


async def test_proposer_evolution_refuse_un_secret(box):
    diff = (
        "--- a/core/app/x.py\n+++ b/core/app/x.py\n@@ -1 +1,2 @@\n x\n"
        '+API_KEY = "sk-ant-api03-abcdefghijklmnop1234567890"\n'
    )
    content, is_error = await _run_as(
        box, "proposer_evolution", {"titre": "clé", "motivation": "x", "diff": diff}, OWNER
    )
    assert not is_error and "secret" in content.lower()
    assert await box.store.list_suggestions() == []


async def test_auto_amelioration_reservee_au_proprietaire(box):
    for who in (_HOUSEHOLD, UNKNOWN):
        for tool, args in (
            ("proposer_evolution", {"titre": "x", "motivation": "y", "diff": _BENIGN_DIFF}),
            ("lire_mon_code", {}),
            ("lister_evolutions", {}),
        ):
            content, is_error = await _run_as(box, tool, args, who)
            assert not is_error and "réservé à guillaume" in content.lower()
    assert await box.store.list_suggestions() == []


async def test_lire_mon_code_lecture_seule(box):
    # Sans chemin : la liste des fichiers source
    listing, is_error = await _run_as(box, "lire_mon_code", {}, OWNER)
    assert not is_error and "identity.py" in listing
    # Un fichier réel de son code
    content, is_error = await _run_as(box, "lire_mon_code", {"chemin": "core/app/identity.py"}, OWNER)
    assert not is_error and "Speaker" in content
    # Jamais un secret / hors racine
    content, is_error = await _run_as(box, "lire_mon_code", {"chemin": ".env"}, OWNER)
    assert is_error


# ── Scénarios & routines (Phase 8) : proposer / activer / déclencher ──────────

async def test_proposer_routine_puis_lister(box):
    content, is_error = await _run_as(box, "proposer_routine", {
        "nom": "Bonne nuit", "description": "Fermer + éteindre",
        "etapes": [
            {"action": "fermer_volets", "entity_ids": ["cover.salon"]},
            {"action": "eteindre", "entity_ids": ["light.salon"]},
        ],
    }, OWNER)
    assert not is_error
    data = json.loads(content)
    assert data["nom"] == "Bonne nuit" and len(data["etapes"]) == 2
    # Elle est PROPOSÉE, pas active
    routines = await box.store.list_routines()
    assert routines[0]["status"] == "proposed"
    listing, _ = await _run_as(box, "lister_routines", {}, OWNER)
    assert "Bonne nuit" in listing


async def test_proposer_routine_refuse_action_sensible(box):
    # Le vocabulaire n'expose même pas le déverrouillage — action inconnue refusée
    content, is_error = await _run_as(box, "proposer_routine", {
        "nom": "Ouvre tout", "etapes": [{"action": "deverrouiller", "entity_ids": ["lock.porte"]}],
    }, OWNER)
    assert not is_error and "inconnue" in content.lower()
    assert await box.store.list_routines() == []


async def test_lancer_routine_seulement_si_active(box):
    await _run_as(box, "proposer_routine", {
        "nom": "Soirée", "etapes": [{"action": "allumer", "entity_ids": ["light.salon"]}],
    }, OWNER)
    # Proposée → pas déclenchable
    content, is_error = await _run_as(box, "lancer_routine", {"nom": "Soirée"}, OWNER)
    assert is_error and box.calls == []
    # Activée (hors LLM) → déclenchable, via le moteur
    routine = (await box.store.list_routines())[0]
    await box.store.update_routine(routine["id"], status="active")
    content, is_error = await _run_as(box, "lancer_routine", {"nom": "soirée"}, OWNER)
    assert not is_error and box.calls


async def test_routines_reservees_selon_le_niveau(box):
    # Proposer = propriétaire uniquement
    content, is_error = await _run_as(box, "proposer_routine", {
        "nom": "X", "etapes": [{"action": "allumer", "entity_ids": ["light.salon"]}],
    }, UNKNOWN)
    assert "réservé à guillaume" in content.lower()
    # Déclencher une routine active = personne reconnue (maisonnée) ; invité non
    await box.store.add_routine(name="Nuit", steps=[{"action_id": "ha.turn_off",
        "params": {"entity_ids": ["light.salon"]}, "label": "x"}], status="active")
    content, is_error = await _run_as(box, "lancer_routine", {"nom": "Nuit"}, UNKNOWN)
    assert not box.calls and "reconnais pas" in content.lower()


# ── Musique multi-pièces (Phase 9) ───────────────────────────────────────────

async def test_etat_musique_public(box):
    content, is_error = await _run_as(box, "etat_musique", {}, UNKNOWN)  # lecture = public
    assert not is_error
    data = json.loads(content)
    assert data[0]["nom"] == "Enceinte salon" and data[0]["titre"] == "So What"


async def test_musique_pilotage_via_moteur(box):
    content, is_error = await _run_as(box, "musique", {"operation": "pause", "zone": "salon"}, _HOUSEHOLD)
    assert not is_error
    assert ("media_player", "media_pause") in {(c[0], c[1]) for c in box.calls}

    box.calls.clear()
    await _run_as(box, "musique", {"operation": "volume", "zone": "salon", "niveau": 50}, _HOUSEHOLD)
    vol = next(c for c in box.calls if c[1] == "volume_set")
    assert vol[2] == {"volume_level": 0.5}


async def test_musique_jouer_preset(box):
    # « jouer » avec le préréglage « jazz » → sélection de source
    content, is_error = await _run_as(box, "musique", {"operation": "jouer", "zone": "salon", "contenu": "jazz"}, _HOUSEHOLD)
    assert not is_error
    src = next(c for c in box.calls if c[1] == "select_source")
    assert src[2] == {"source": "Spotify"}


async def test_musique_transfert(box):
    # Ajoute une enceinte cuisine dans une autre pièce
    box.ha._states["media_player.cuisine"] = {"state": "off", "attributes": {"friendly_name": "Cuisine"}}
    box.ha._entity_area["media_player.cuisine"] = "area_chambre"
    box.ha._areas["area_chambre"] = "Cuisine"  # renomme pour le test de zone
    content, is_error = await _run_as(box, "musique", {"operation": "transferer", "zone": "salon", "cible": "cuisine"}, _HOUSEHOLD)
    assert not is_error
    join = next(c for c in box.calls if c[1] == "join")
    assert "media_player.cuisine" in join[2]["group_members"]


async def test_musique_refusee_a_l_invite(box):
    content, is_error = await _run_as(box, "musique", {"operation": "pause", "zone": "salon"}, UNKNOWN)
    assert not is_error and "reconnais pas" in content.lower()
    assert box.calls == []


# ── Minuteurs & rappels (Phase 10) ───────────────────────────────────────────

async def test_minuteur_et_liste(box):
    content, is_error = await _run_as(box, "minuteur", {"minutes": 10, "libelle": "pâtes"}, _HOUSEHOLD)
    assert not is_error and "10 min" in content and "pâtes" in content
    rows = await box.store.list_reminders("active")
    assert len(rows) == 1 and rows[0]["kind"] == "timer" and rows[0]["label"] == "pâtes"

    listing, _ = await _run_as(box, "lister_rappels", {}, _HOUSEHOLD)
    assert "pâtes" in listing and "minuteur" in listing


async def test_rappel_relatif_et_absolu(box):
    content, is_error = await _run_as(box, "rappel", {"libelle": "sortir le plat", "dans_minutes": 20}, OWNER)
    assert not is_error and "sortir le plat" in content
    # Absolu : une heure ISO future
    from datetime import datetime, timedelta
    future = (datetime.now() + timedelta(hours=3)).replace(microsecond=0).isoformat()
    content, is_error = await _run_as(box, "rappel", {"libelle": "appeler le garage", "a": future}, OWNER)
    assert not is_error
    kinds = [r["kind"] for r in await box.store.list_reminders("active")]
    assert kinds.count("reminder") == 2


async def test_rappel_sans_echeance_ni_passe(box):
    content, is_error = await _run_as(box, "rappel", {"libelle": "x"}, OWNER)
    assert not is_error and "précise quand" in content.lower()
    # Une heure déjà passée est refusée
    from datetime import datetime, timedelta
    past = (datetime.now() - timedelta(hours=1)).isoformat()
    content, _ = await _run_as(box, "rappel", {"libelle": "x", "a": past}, OWNER)
    assert "précise quand" in content.lower()
    assert await box.store.list_reminders("active") == []


async def test_annuler_rappel(box):
    await _run_as(box, "minuteur", {"minutes": 5}, OWNER)
    rid = (await box.store.list_reminders("active"))[0]["id"]
    content, is_error = await _run_as(box, "annuler_rappel", {"id": rid}, OWNER)
    assert not is_error and "annul" in content.lower()
    assert await box.store.list_reminders("active") == []


async def test_rappels_refuses_a_l_invite(box):
    content, is_error = await _run_as(box, "minuteur", {"minutes": 5}, UNKNOWN)
    assert not is_error and "reconnais pas" in content.lower()
    assert await box.store.list_reminders("active") == []
