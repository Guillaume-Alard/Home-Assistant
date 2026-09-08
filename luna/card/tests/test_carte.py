"""La carte Loggia, dans un vrai navigateur.

Chromium sans interface, via Playwright. On charge `luna-card.js` tel qu'il
sera servi depuis `/config/www/`, avec un faux `hass` qui rejoue le contrat
§4 — et on vérifie que la carte s'y tient : ordre des événements, mode
dégradé, propositions, désabonnement.

Le désabonnement mérite son test : Lovelace détruit les cartes à chaque
changement de vue, et une souscription oubliée fuit à chaque aller-retour.
"""

from __future__ import annotations

import os
import re
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

RACINE = Path(__file__).resolve().parent.parent

#: Court : un test de carte qui échoue doit le dire tout de suite, pas au bout
#: de cinq secondes d'attente multipliées par le nombre d'assertions.
DELAI = 3000
expect.set_options(timeout=DELAI)


def _chromium() -> str | None:
    """Un Chromium déjà installé, s'il y en a un.

    `LUNA_CHROMIUM` gagne ; sinon on regarde l'emplacement usuel d'un poste de
    développement ; sinon on laisse Playwright résoudre lui-même — c'est ce qui
    se passe en CI, après `playwright install chromium`.
    """
    if chemin := os.environ.get("LUNA_CHROMIUM"):
        return chemin
    usuel = Path("/opt/pw-browsers/chromium")
    return str(usuel) if usuel.exists() else None


@pytest.fixture(scope="session")
def navigateur():
    with sync_playwright() as p:
        chemin = _chromium()
        try:
            # Micro synthétique : `getUserMedia` répond sans demander la
            # permission et sans matériel. On teste ainsi le vrai chemin —
            # AudioWorklet compris — plutôt qu'une doublure.
            args = [
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ]
            nav = (
                p.chromium.launch(executable_path=chemin, args=args)
                if chemin
                else p.chromium.launch(args=args)
            )
        except PlaywrightError as err:
            pytest.skip(f"Chromium indisponible : {err}")
        yield nav
        nav.close()


@pytest.fixture(scope="session")
def banc():
    """Le banc est servi en HTTP, pas ouvert en `file://`.

    Un `file://` est bien un contexte sécurisé, mais son origine opaque
    interdit de charger un `AudioWorklet` depuis un Blob — ce que la carte fait
    pour tenir la promesse « un seul fichier » de §8. En HTTP local on teste le
    chemin réel, celui de `/local/luna-card.js`.
    """
    gestionnaire = partial(SimpleHTTPRequestHandler, directory=str(RACINE))
    serveur = ThreadingHTTPServer(("127.0.0.1", 0), gestionnaire)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{serveur.server_address[1]}/tests/banc.html"
    finally:
        serveur.shutdown()


@pytest.fixture
def page(navigateur, banc):
    contexte = navigateur.new_context(permissions=["microphone"])
    page = contexte.new_page()
    erreurs: list[str] = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.set_default_timeout(DELAI)
    page.goto(banc)
    yield page
    assert not erreurs, f"erreurs JavaScript : {erreurs}"
    contexte.close()


def monter(page: Page, **options) -> None:
    page.evaluate("(o) => window.monter(o)", options)
    page.wait_for_function("() => window.__luna.fluxOuvert('feed')")


def dans_carte(page: Page, selecteur: str):
    """Le moteur CSS de Playwright traverse les shadow roots ouverts tout seul ;
    l'opérateur `>>>` ne franchit qu'un niveau et rate tout ce qui est imbriqué."""
    return page.locator("luna-card").locator(selecteur)


def emettre(page: Page, cle: str, evenement: dict) -> None:
    page.evaluate("([c, e]) => window.__luna.emettre(c, e)", [cle, evenement])


def journal(page: Page) -> dict:
    return page.evaluate("() => window.__luna.journal")


def attendre_transcription(page: Page) -> None:
    """Attend que la carte soit vraiment en train d'attendre le texte.

    Émettre `stt-end` avant ce moment est ignoré en silence — et le test
    devient intermittent au lieu d'échouer franchement.
    """
    page.wait_for_function("() => window.__carte._attenteStt !== null", timeout=DELAI)


class TestMontage:
    def test_charte_visuelle(self, page):
        """§8 : fond #0f1923, cartes #1e2d3d, accent #c9396d."""
        monter(page)
        fond = dans_carte(page, ".carte").evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        assert fond == "rgb(15, 25, 35)"
        accent = dans_carte(page, ".noyau").evaluate("el => getComputedStyle(el).fill")
        assert accent == "rgb(201, 57, 109)"

    def test_composants_de_la_section_8(self, page):
        monter(page)
        for selecteur in (
            ".orbe",
            ".fil",
            "textarea",
            ".envoi",
            ".micro",
            ".badge",
            ".tiroir",
        ):
            expect(dans_carte(page, selecteur)).to_have_count(1)

    def test_badge_didentite(self, page):
        monter(page)
        expect(dans_carte(page, ".prenom")).to_have_text("Guillaume")
        expect(dans_carte(page, ".avatar")).to_have_text("G")

    def test_configuration_invalide_est_refusee(self, page):
        erreur = page.evaluate("""() => {
          const c = document.createElement('luna-card');
          try { c.setConfig({ height: 10 }); return null; }
          catch (e) { return e.message; }
        }""")
        assert "≥ 400" in erreur

    def test_le_tiroir_dit_ce_quil_ne_fait_pas_encore(self, page):
        """§8 : jamais d'échec silencieux, y compris pour une phase future."""
        monter(page)
        dans_carte(page, ".cloche").click()
        expect(dans_carte(page, ".tiroir .vide")).to_contain_text("phase 4")


class TestConversation:
    def test_envoi_et_flux(self, page):
        monter(page)
        dans_carte(page, "textarea").fill("allume le salon")
        dans_carte(page, ".envoi").click()

        expect(dans_carte(page, ".bulle.moi")).to_contain_text("allume le salon")
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')")
        assert dans_carte(page, ".carte").get_attribute("data-orbe") == "thinking"

        emettre(
            page,
            "chat",
            {"event": "accepted", "message_id": "m_1", "conversation_id": "c_1"},
        )
        emettre(
            page, "chat", {"event": "delta", "message_id": "m_1", "text": "J'allume "}
        )
        assert dans_carte(page, ".carte").get_attribute("data-orbe") == "speaking"
        emettre(
            page, "chat", {"event": "delta", "message_id": "m_1", "text": "le salon."}
        )
        emettre(
            page,
            "chat",
            {
                "event": "done",
                "message_id": "m_1",
                "text": "J'allume le salon.",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

        expect(dans_carte(page, ".bulle.luna").last).to_contain_text("J'allume le salon.")
        assert dans_carte(page, ".carte").get_attribute("data-orbe") == "idle"

    def test_le_flux_se_ferme_a_la_fin(self, page):
        """Sans ça, chaque échange laisse une souscription ouverte."""
        monter(page)
        dans_carte(page, "textarea").fill("salut")
        dans_carte(page, ".envoi").click()
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')")
        emettre(
            page,
            "chat",
            {"event": "done", "message_id": "m_1", "text": "Salut.", "usage": {}},
        )
        page.wait_for_function("() => !window.__luna.fluxOuvert('chat')")

    def test_outils_affiches_avec_leur_niveau(self, page):
        monter(page)
        dans_carte(page, "textarea").fill("allume")
        dans_carte(page, ".envoi").click()
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')")

        for statut in ("running", "done"):
            emettre(
                page,
                "chat",
                {
                    "event": "tool",
                    "message_id": "m_1",
                    "name": "commander_lumiere",
                    "label": "Allumer Séjour (2)",
                    "level": 2,
                    "status": statut,
                },
            )
        outils = dans_carte(page, ".outil")
        expect(outils).to_have_count(1)  # une ligne, mise à jour, pas deux
        assert outils.get_attribute("data-statut") == "done"

    def test_texte_non_interprete(self, page):
        """Le contenu passe par textContent : pas d'injection possible."""
        monter(page)
        dans_carte(page, "textarea").fill("<img src=x onerror=alert(1)>")
        dans_carte(page, ".envoi").click()
        contenu = dans_carte(page, ".bulle.moi").inner_html()
        assert "&lt;img" in contenu
        assert "<img" not in contenu

    def test_erreur_affichee_telle_quelle(self, page):
        monter(page)
        dans_carte(page, "textarea").fill("salut")
        dans_carte(page, ".envoi").click()
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')")
        emettre(
            page,
            "chat",
            {
                "event": "error",
                "message_id": "m_1",
                "code": "claude_no_credit",
                "message": "Le crédit de la clé API Anthropic est épuisé.",
            },
        )
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("crédit")

    def test_historique_restaure(self, page):
        monter(
            page,
            historique={
                "conversation_id": "c_9",
                "messages": [
                    {
                        "id": "m_1",
                        "role": "user",
                        "text": "bonsoir",
                        "ts": "2026-09-08T20:31:04+02:00",
                        "profile": "guillaume",
                        "client_id": "loggia",
                        "tools": [],
                    },
                    {
                        "id": "m_2",
                        "role": "luna",
                        "text": "Bonsoir Guillaume.",
                        "ts": "2026-09-08T20:31:06+02:00",
                        "profile": None,
                        "client_id": None,
                        "tools": [
                            {
                                "name": "lister_pieces",
                                "label": "Lire les pièces",
                                "level": 1,
                                "status": "done",
                            }
                        ],
                    },
                ],
                "has_more": False,
            },
        )
        expect(dans_carte(page, ".bulle.moi")).to_contain_text("bonsoir")
        expect(dans_carte(page, ".bulle.luna")).to_contain_text("Bonsoir Guillaume.")
        expect(dans_carte(page, ".outil")).to_contain_text("Lire les pièces")


class TestProposition:
    def _proposer(self, page):
        monter(page)
        dans_carte(page, "textarea").fill("mets 20 degrés")
        dans_carte(page, ".envoi").click()
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')")
        emettre(
            page,
            "chat",
            {
                "event": "proposal",
                "message_id": "m_1",
                "proposal": {
                    "id": "p_1",
                    "level": 3,
                    "title": "Régler Séjour sur 20 °C",
                    "why": "Il fait 17,5 °C.",
                    "actions": [],
                    "expires_at": "2099-01-01T00:00:00+01:00",
                },
            },
        )

    def test_affichage(self, page):
        self._proposer(page)
        expect(dans_carte(page, ".proposition .niveau")).to_contain_text("Niveau 3")
        expect(dans_carte(page, ".proposition h4")).to_have_text(
            "Régler Séjour sur 20 °C"
        )
        expect(dans_carte(page, ".proposition p")).to_contain_text("17,5 °C")

    def test_acceptation(self, page):
        self._proposer(page)
        dans_carte(page, ".proposition button.primaire").click()
        expect(dans_carte(page, ".proposition p").last).to_have_text("Fait.")
        envoyes = journal(page)["envoyes"]
        decision = next(m for m in envoyes if m["type"] == "luna/proposal/decide")
        assert decision["proposal_id"] == "p_1"
        assert decision["decision"] == "accept"

    def test_refus(self, page):
        self._proposer(page)
        dans_carte(page, ".proposition button").nth(1).click()
        expect(dans_carte(page, ".proposition p").last).to_contain_text("Rien n'a changé")


class TestModeDegrade:
    def test_hors_ligne_au_montage(self, page):
        """§8 : chat coupé, entités HA toujours lisibles, message explicite."""
        page.evaluate("(o) => window.monter(o)", {"enLigne": "off"})
        expect(dans_carte(page, ".bandeau")).to_be_visible()
        expect(dans_carte(page, "textarea")).to_be_disabled()
        expect(dans_carte(page, ".envoi")).to_be_disabled()
        # La carte ne masque rien de Home Assistant : les états restent là.
        assert (
            page.evaluate("() => window.__carte.hass.states['light.salon'].state")
            == "off"
        )

    def test_bascule_en_ligne_puis_hors_ligne(self, page):
        monter(page)
        expect(dans_carte(page, "textarea")).to_be_enabled()
        page.evaluate("""() => {
          const h = window.__luna.hass;
          h.states['binary_sensor.luna_en_ligne'] = { state: 'off' };
          window.__carte.hass = h;
        }""")
        expect(dans_carte(page, "textarea")).to_be_disabled()
        expect(dans_carte(page, ".sous-titre")).to_have_text("Hors ligne")

    def test_statut_degrade_depuis_le_flux(self, page):
        monter(page)
        emettre(
            page,
            "feed",
            {
                "event": "status",
                "addon": "degraded",
                "detail": "Home Assistant injoignable",
            },
        )
        expect(dans_carte(page, ".bandeau")).to_contain_text("Home Assistant injoignable")
        # Dégradé n'est pas hors ligne : on peut toujours écrire.
        expect(dans_carte(page, "textarea")).to_be_enabled()


class TestFuites:
    def test_le_demontage_ferme_le_flux(self, page):
        """Lovelace détruit les cartes à chaque changement de vue."""
        monter(page)
        assert page.evaluate("() => window.__luna.fluxOuvert('feed')") is True
        page.evaluate("() => window.__carte.remove()")
        assert page.evaluate("() => window.__luna.fluxOuvert('feed')") is False
        assert journal(page)["desabonnements"] >= 1


class TestMicro:
    """§8 : « le refus de permission micro et le contexte non-HTTPS produisent
    un message d'erreur explicite et actionnable »."""

    def test_actif_quand_la_voix_est_prete(self, page):
        monter(page)
        expect(dans_carte(page, ".micro")).to_be_enabled()
        assert "Maintenir" in dans_carte(page, ".micro").get_attribute("title")

    def test_grise_tant_que_la_voix_nest_pas_la(self, page):
        monter(
            page,
            info={
                "version": "0.1.0",
                "addon": "online",
                "capabilities": ["chat"],
                "profile": {
                    "id": "guillaume",
                    "display_name": "Guillaume",
                    "confidence": 1.0,
                    "signals": {},
                },
                "phases": {
                    "voice": False,
                    "identity": False,
                    "veille": False,
                    "guardian": False,
                },
            },
        )
        expect(dans_carte(page, ".micro")).to_be_disabled()
        assert "phase 2" in dans_carte(page, ".micro").get_attribute("title")

    def test_contexte_non_securise(self, page):
        monter(page)
        page.evaluate(
            """() => {
              Object.defineProperty(window, 'isSecureContext',
                { value: false, configurable: true });
              const c = window.__carte;
              c.shadowRoot.querySelector('.micro').disabled = false;
              c._demarrerEcoute();
            }"""
        )
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("HTTPS")
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("phase 0")

    def test_permission_refusee(self, page):
        monter(page)
        page.evaluate(
            """() => {
              navigator.mediaDevices.getUserMedia = () => {
                const e = new Error('refusé');
                e.name = 'NotAllowedError';
                return Promise.reject(e);
              };
            }"""
        )
        dans_carte(page, ".micro").click()
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("Autorise")
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("réglages")


class TestAppuiPourParler:
    """Le vrai chemin : capture, rééchantillonnage, pipeline, transcription."""

    @staticmethod
    def _appuyer(page):
        micro = dans_carte(page, ".micro")
        micro.dispatch_event("pointerdown")
        page.wait_for_function(
            "() => window.__luna.fluxOuvert('pipeline')", timeout=DELAI
        )

    @staticmethod
    def _relacher(page, attendre=True):
        dans_carte(page, ".micro").dispatch_event("pointerup")
        if attendre:
            attendre_transcription(page)

    def test_lappui_ouvre_le_pipeline_en_stt_seul(self, page):
        monter(page)
        self._appuyer(page)

        assert dans_carte(page, ".carte").get_attribute("data-orbe") == "listening"
        demande = next(
            m
            for m in journal(page)["souscriptions"]
            if m["type"] == "assist_pipeline/run"
        )
        assert demande["start_stage"] == "stt"
        assert demande["end_stage"] == "stt"
        assert demande["input"]["sample_rate"] == 16000
        self._relacher(page)

    def test_le_pcm_part_prefixe_du_canal_binaire(self, page):
        """Le micro synthétique de Chromium émet un vrai signal : les trames
        doivent sortir en 16 bits, préfixées de l'octet donné par `run-start`."""
        monter(page)
        self._appuyer(page)
        emettre(
            page,
            "pipeline",
            {"type": "run-start", "data": {"runner_data": {"stt_binary_handler_id": 7}}},
        )
        page.wait_for_function(
            "() => window.__luna.journal.binaire.length > 0", timeout=DELAI
        )
        trames = page.evaluate("() => window.__luna.journal.binaire")
        assert trames[0][0] == 7, "chaque trame porte l'octet de canal"
        # 1024 échantillons de 16 bits, plus l'octet de canal.
        assert len(trames[0]) == 1 + 1024 * 2
        self._relacher(page)

    def test_le_debut_de_phrase_nest_pas_perdu(self, page):
        """Le pipeline met un instant à donner le canal binaire ; ce qui a été
        capté avant doit être conservé, pas jeté."""
        monter(page)
        self._appuyer(page)
        page.wait_for_timeout(300)  # on parle avant que run-start n'arrive
        avant = page.evaluate("() => window.__luna.journal.binaire.length")
        assert avant == 0

        emettre(
            page,
            "pipeline",
            {"type": "run-start", "data": {"runner_data": {"stt_binary_handler_id": 3}}},
        )
        page.wait_for_function(
            "() => window.__luna.journal.binaire.length > 0", timeout=DELAI
        )
        assert page.evaluate("() => window.__luna.journal.binaire.length") > 0
        self._relacher(page)

    def test_un_appui_bref_ne_casse_rien(self, page):
        """Relâcher avant que la capture soit prête ne doit pas tuer
        l'enregistrement en silence : sur iPad, on tapote."""
        micro = dans_carte(page, ".micro")
        monter(page)
        micro.dispatch_event("pointerdown")
        micro.dispatch_event("pointerup")  # sans attendre que ce soit prêt
        attendre_transcription(page)
        emettre(
            page,
            "pipeline",
            {"type": "stt-end", "data": {"stt_output": {"text": "bonsoir"}}},
        )
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')", timeout=DELAI)
        expect(dans_carte(page, ".bulle.moi")).to_contain_text("bonsoir")

    def test_le_relachement_ferme_le_flux_audio(self, page):
        monter(page)
        self._appuyer(page)
        emettre(
            page,
            "pipeline",
            {"type": "run-start", "data": {"runner_data": {"stt_binary_handler_id": 5}}},
        )
        page.wait_for_function(
            "() => window.__luna.journal.binaire.length > 0", timeout=DELAI
        )
        self._relacher(page, attendre=False)
        page.wait_for_function(
            "() => window.__luna.journal.binaire.some(t => t.length === 1)",
            timeout=DELAI,
        )
        fin = [
            t for t in page.evaluate("() => window.__luna.journal.binaire") if len(t) == 1
        ]
        assert fin[-1] == [5], "une trame réduite au canal dit « j'ai fini »"

    def test_la_transcription_part_dans_luna_chat(self, page):
        monter(page)
        self._appuyer(page)
        emettre(
            page,
            "pipeline",
            {"type": "run-start", "data": {"runner_data": {"stt_binary_handler_id": 1}}},
        )
        self._relacher(page)
        emettre(
            page,
            "pipeline",
            {"type": "stt-end", "data": {"stt_output": {"text": "allume le salon"}}},
        )
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')", timeout=DELAI)
        demande = next(
            m for m in journal(page)["souscriptions"] if m["type"] == "luna/chat"
        )
        assert demande["text"] == "allume le salon"
        expect(dans_carte(page, ".bulle.moi")).to_contain_text("allume le salon")

    def test_une_transcription_vide_le_dit(self, page):
        monter(page)
        self._appuyer(page)
        emettre(
            page,
            "pipeline",
            {"type": "run-start", "data": {"runner_data": {"stt_binary_handler_id": 1}}},
        )
        self._relacher(page)
        emettre(
            page, "pipeline", {"type": "stt-end", "data": {"stt_output": {"text": ""}}}
        )
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("rien compris")
        assert dans_carte(page, ".carte").get_attribute("data-orbe") == "idle"

    def test_une_erreur_de_pipeline_le_dit(self, page):
        monter(page)
        self._appuyer(page)
        self._relacher(page)
        emettre(
            page,
            "pipeline",
            {"type": "error", "data": {"message": "Aucun moteur de transcription."}},
        )
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("Aucun moteur")


class TestLecture:
    """B3 — la carte parle par `luna/speak`."""

    @staticmethod
    def _echange_vocal(page):
        micro = dans_carte(page, ".micro")
        micro.dispatch_event("pointerdown")
        page.wait_for_function("() => window.__luna.fluxOuvert('pipeline')")
        emettre(
            page,
            "pipeline",
            {"type": "run-start", "data": {"runner_data": {"stt_binary_handler_id": 1}}},
        )
        micro.dispatch_event("pointerup")
        emettre(
            page,
            "pipeline",
            {"type": "stt-end", "data": {"stt_output": {"text": "salut"}}},
        )
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')")
        emettre(
            page,
            "chat",
            {"event": "accepted", "message_id": "m_1", "conversation_id": "c_1"},
        )
        emettre(
            page,
            "chat",
            {
                "event": "done",
                "message_id": "m_1",
                "text": "Bonjour Guillaume.",
                "usage": {},
            },
        )

    def test_une_reponse_a_la_voix_est_lue(self, page):
        monter(page)
        self._echange_vocal(page)
        page.wait_for_function(
            "() => window.__luna.journal.envoyes.some(m => m.type === 'luna/speak')",
            timeout=DELAI,
        )
        demande = next(m for m in journal(page)["envoyes"] if m["type"] == "luna/speak")
        assert demande["text"] == "Bonjour Guillaume."

    def test_une_reponse_a_lecrit_nest_pas_lue(self, page):
        """Personne ne veut être lu à haute voix parce qu'il a tapé."""
        monter(page)
        dans_carte(page, "textarea").fill("salut")
        dans_carte(page, ".envoi").click()
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')")
        emettre(
            page,
            "chat",
            {"event": "done", "message_id": "m_1", "text": "Bonjour.", "usage": {}},
        )
        page.wait_for_timeout(300)
        assert not any(m["type"] == "luna/speak" for m in journal(page)["envoyes"])

    def test_mode_toujours(self, page):
        monter(page, config={"height": 620, "speak": "toujours"})
        dans_carte(page, "textarea").fill("salut")
        dans_carte(page, ".envoi").click()
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')")
        emettre(
            page,
            "chat",
            {"event": "done", "message_id": "m_1", "text": "Bonjour.", "usage": {}},
        )
        page.wait_for_function(
            "() => window.__luna.journal.envoyes.some(m => m.type === 'luna/speak')",
            timeout=DELAI,
        )

    def test_mode_jamais(self, page):
        monter(page, config={"height": 620, "speak": "jamais"})
        self._echange_vocal(page)
        page.wait_for_timeout(300)
        assert not any(m["type"] == "luna/speak" for m in journal(page)["envoyes"])

    def test_configuration_invalide(self, page):
        erreur = page.evaluate(
            """() => {
              const c = document.createElement('luna-card');
              try { c.setConfig({ speak: 'parfois' }); return null; }
              catch (e) { return e.message; }
            }"""
        )
        assert "speak" in erreur

    def test_laudio_est_debloque_pendant_le_geste(self, page):
        """Sur iPad, un `play()` hors geste est refusé : le lecteur doit être
        amorcé au premier appui, sinon Luna reste muette."""
        monter(page)
        assert page.evaluate("() => window.__carte._lecteur") is None
        dans_carte(page, ".micro").dispatch_event("pointerdown")
        assert page.evaluate("() => Boolean(window.__carte._lecteur)") is True
        dans_carte(page, ".micro").dispatch_event("pointerup")


def _code_seul() -> str:
    """Le fichier sans ses commentaires : les interdits portent sur le code,
    pas sur les phrases qui les expliquent."""
    source = (Path(__file__).parent.parent / "luna-card.js").read_text("utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(
        ligne for ligne in source.splitlines() if not ligne.strip().startswith("//")
    )


def test_le_fichier_na_aucune_dependance_externe():
    """§8 : aucun build, aucun npm, aucun CDN, aucune dépendance à l'exécution.
    §3 : « jamais de fetch() vers un hôte externe »."""
    code = _code_seul()
    for interdit in (
        "import ",
        "require(",
        "//cdn",
        "https://",
        "http://",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket(",
    ):
        assert interdit not in code, f"« {interdit} » dans luna-card.js"


def test_la_carte_ne_parle_qua_home_assistant():
    """Tout passe par `hass.connection` ou `hass.states`, rien d'autre."""
    code = _code_seul()
    assert "this._hass.connection.sendMessagePromise" in code
    assert "this._hass.connection.subscribeMessage" in code
    assert "this._hass.states" in code
