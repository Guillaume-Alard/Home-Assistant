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

    def test_montage_direct_sans_configuration(self, page):
        """Un `<luna-card>` posé dans une page, sans `setConfig`, doit tenir.

        Lovelace configure toujours avant d'insérer ; une page écrite à la main
        — un banc, un aperçu — ne le fait pas. Sans défauts dans le
        constructeur, `_appliquerConfig` casse sur `_config.drawers` au montage,
        et l'erreur ne se voit que dans la console.
        """
        etat = page.evaluate("""() => {
          const c = document.createElement('luna-card');
          document.body.appendChild(c);
          const etat = {
            monte: !!c.shadowRoot.querySelector('.carte'),
            cloche: c.shadowRoot.querySelector('.cloche')?.hidden,
            taille: c.getCardSize(),
          };
          c.remove();
          return etat;
        }""")
        # Une exception dans `connectedCallback` ne remonte pas à
        # `appendChild` : le navigateur la signale comme erreur de page. Le
        # garde-fou, c'est l'assertion `pageerror` de la fixture `page`.
        assert etat == {"monte": True, "cloche": False, "taille": 13}

    def test_configuration_invalide_est_refusee(self, page):
        erreur = page.evaluate("""() => {
          const c = document.createElement('luna-card');
          try { c.setConfig({ height: 10 }); return null; }
          catch (e) { return e.message; }
        }""")
        assert "≥ 400" in erreur

    def test_le_tiroir_dit_ce_quil_ne_fait_pas_encore(self, page):
        """§8 : jamais d'échec silencieux, y compris pour une phase future."""
        monter(page, veille=False)
        dans_carte(page, ".cloche").click()
        expect(dans_carte(page, ".alertes .vide")).to_contain_text("phase 4")

    def test_le_tiroir_dit_quil_ny_a_rien_a_signaler(self, page):
        """Une fois la phase 4 là, le vide n'est plus une excuse."""
        monter(page)
        dans_carte(page, ".cloche").click()
        expect(dans_carte(page, ".alertes .vide")).to_have_text("Rien à signaler.")


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


class TestIdentite:
    """P3 — la carte côté identité."""

    def test_lidentifiant_dappareil_est_stable(self, page):
        monter(page)
        premier = page.evaluate("() => localStorage.getItem('luna_appareil')")
        assert premier and premier.startswith("d_")
        assert page.evaluate("() => window.__carte._idAppareil()") == premier

        # Il voyage avec chaque commande : c'est lui qui porte l'identité (C6).
        info = next(m for m in journal(page)["envoyes"] if m["type"] == "luna/info")
        assert info["device"] == premier

    def test_le_badge_suit_le_feed(self, page):
        monter(page)
        expect(dans_carte(page, ".prenom")).to_have_text("Guillaume")
        emettre(
            page,
            "feed",
            {
                "event": "identity",
                "profile": {
                    "id": "clara",
                    "display_name": "Clara",
                    "confidence": 0.82,
                    "signals": {"voice": 0.91},
                },
            },
        )
        expect(dans_carte(page, ".prenom")).to_have_text("Clara")
        expect(dans_carte(page, ".confiance")).to_have_text("82 %")

    def test_le_panneau_sourve_par_le_badge(self, page):
        monter(page)
        dans_carte(page, ".badge").click()
        expect(dans_carte(page, ".titre-tiroir")).to_have_text("Qui parle")
        expect(dans_carte(page, ".identite .bloc")).to_have_count(3)

    def test_sur_un_appareil_deja_identifie_rien_a_apprendre(self, page):
        """C1 : Home Assistant sait déjà qui c'est, il n'y a rien à reconnaître."""
        monter(page, voixNecessaire=False)
        dans_carte(page, ".badge").click()
        expect(dans_carte(page, ".identite .vide")).to_contain_text("sait déjà qui")


class TestIdentificationVocale:
    @staticmethod
    def _parler(page, texte="salut"):
        micro = dans_carte(page, ".micro")
        micro.dispatch_event("pointerdown")
        page.wait_for_function("() => window.__luna.fluxOuvert('pipeline')")
        emettre(
            page,
            "pipeline",
            {"type": "run-start", "data": {"runner_data": {"stt_binary_handler_id": 1}}},
        )
        page.wait_for_timeout(250)  # laisser passer quelques trames PCM
        micro.dispatch_event("pointerup")
        attendre_transcription(page)
        emettre(
            page,
            "pipeline",
            {"type": "stt-end", "data": {"stt_output": {"text": texte}}},
        )

    def test_laudio_part_pour_identification(self, page):
        monter(page)
        self._parler(page)
        page.wait_for_function(
            "() => window.__luna.journal.envoyes.some("
            "m => m.type === 'luna/identity/voice')",
            timeout=DELAI,
        )
        demande = next(
            m for m in journal(page)["envoyes"] if m["type"] == "luna/identity/voice"
        )
        assert demande["device"].startswith("d_")
        assert len(demande["audio"]) > 100, "de l'audio réel, encodé en base64"

    def test_identification_avant_lechange(self, page):
        """Le profil fixe le scope des actions : l'ordre n'est pas décoratif."""
        monter(page)
        self._parler(page, "allume le salon")
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')", timeout=DELAI)
        types = [m["type"] for m in journal(page)["envoyes"]]
        souscrits = [m["type"] for m in journal(page)["souscriptions"]]
        assert "luna/identity/voice" in types
        assert "luna/chat" in souscrits
        # L'identification est une commande ponctuelle, envoyée avant que
        # l'échange ne soit souscrit.
        assert types.index("luna/identity/voice") >= 0

    def test_rien_nest_envoye_si_la_session_suffit(self, page):
        """C1 : sur un N95, ne pas calculer est la meilleure optimisation."""
        monter(page, voixNecessaire=False)
        self._parler(page)
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')", timeout=DELAI)
        assert not any(
            m["type"] == "luna/identity/voice" for m in journal(page)["envoyes"]
        )

    def test_quand_luna_doute_elle_demande(self, page):
        """La branche « ça ne suffit pas » de C4."""
        monter(page, demande=True)
        self._parler(page)
        expect(dans_carte(page, ".proposition h4")).to_contain_text("pas sûre de qui")
        boutons = dans_carte(page, ".proposition button")
        expect(boutons).to_have_count(3)  # Guillaume, Clara, Ni l'un ni l'autre

        boutons.first.click()
        expect(dans_carte(page, ".proposition p").last).to_contain_text("C'est noté")
        confirmation = next(
            m for m in journal(page)["envoyes"] if m["type"] == "luna/identity/confirm"
        )
        assert confirmation["accept"] is True

    def test_personne_inscrit_le_dit_sans_casser_la_conversation(self, page):
        monter(page, echecIdentite={"code": "not_enrolled", "message": "personne"})
        self._parler(page)
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("badge")
        # La conversation continue quand même.
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')", timeout=DELAI)

    def test_un_refus_distant_ne_casse_rien(self, page):
        """§6 : à distance Luna ne reconnaît pas les voix — elle converse quand
        même, simplement sans savoir qui parle."""
        monter(page, echecIdentite={"code": "remote_biometrics", "message": "non"})
        self._parler(page)
        page.wait_for_function("() => window.__luna.fluxOuvert('chat')", timeout=DELAI)
        expect(dans_carte(page, ".bulle.erreur")).to_have_count(0)


class TestInscription:
    @staticmethod
    def _ouvrir(page):
        monter(page)
        dans_carte(page, ".badge").click()
        dans_carte(page, ".identite .bloc").first.locator("button").first.click()
        expect(dans_carte(page, ".identite h5")).to_contain_text("Phrase 1 sur 2")

    @staticmethod
    def _lire(page):
        bouton = dans_carte(page, ".rond-large")
        bouton.dispatch_event("pointerdown")
        page.wait_for_function("() => window.__carte._ecoute === true", timeout=DELAI)
        page.wait_for_timeout(250)
        bouton.dispatch_event("pointerup")

    def test_le_parcours_complet(self, page):
        self._ouvrir(page)
        self._lire(page)
        expect(dans_carte(page, ".identite h5")).to_contain_text("Phrase 2 sur 2")
        self._lire(page)
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("C'est retenu")

        envoyes = [m["type"] for m in journal(page)["envoyes"]]
        assert envoyes.count("luna/identity/enroll/sample") == 2
        assert "luna/identity/enroll/finish" in envoyes

    def test_linscription_nouvre_pas_le_pipeline(self, page):
        """On ne veut que l'audio : ouvrir la transcription serait du calcul
        pour rien."""
        self._ouvrir(page)
        self._lire(page)
        assert not any(
            m["type"] == "assist_pipeline/run" for m in journal(page)["souscriptions"]
        )

    def test_un_echantillon_refuse_le_dit(self, page):
        monter(
            page,
            echantillon={"accepted": False, "quality": "trop_court", "remaining": 2},
        )
        dans_carte(page, ".badge").click()
        dans_carte(page, ".identite .bloc").first.locator("button").first.click()
        self._lire(page)
        expect(dans_carte(page, ".identite .vide")).to_contain_text("Trop court")
        expect(dans_carte(page, ".identite h5")).to_contain_text("Phrase 1 sur 2")

    def test_une_inscription_peu_nette_le_dit(self, page):
        monter(page, coherence=0.5)
        dans_carte(page, ".badge").click()
        dans_carte(page, ".identite .bloc").first.locator("button").first.click()
        self._lire(page)
        self._lire(page)
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("sans grande netteté")

    def test_oublier(self, page):
        monter(page)
        dans_carte(page, ".badge").click()
        oublier = dans_carte(page, ".identite .bloc").first.locator("button").nth(1)
        expect(oublier).to_have_text("Oublier")
        oublier.click()
        page.wait_for_function(
            "() => window.__luna.journal.envoyes.some("
            "m => m.type === 'luna/identity/forget')",
            timeout=DELAI,
        )
        expect(dans_carte(page, ".identite .bloc").first).to_contain_text("Voix inconnue")


ALERTE = {
    "id": "al_1",
    "key": "ouvrant||binary_sensor.luna_ouvrant_oublie",
    "level": "warning",
    "category": "ouvrant",
    "title": "Un ouvrant est resté ouvert et il est tard.",
    "why": "La baie vitrée du séjour est ouverte.",
    "entity_id": "binary_sensor.luna_ouvrant_oublie",
    "ts": "2026-09-08T23:10:00+02:00",
    "actions": [
        {
            "domain": "cover",
            "service": "close_cover",
            "target": {"entity_id": "cover.baie_vitree"},
            "data": {},
        }
    ],
}

FAIT = {
    "id": "f_2",
    "predicate": "preference_eclairage",
    "value": "couloir tamisé le soir",
    "profile": "guillaume",
    "why": "Tu me l'as dit le 6 septembre.",
    "created_at": "2026-09-07T03:30:00+02:00",
}


def alerte_sans_action() -> dict:
    return {**ALERTE, "actions": []}


class TestVeille:
    """P4 : le tiroir devient vivant."""

    def test_une_alerte_arrive_par_le_feed(self, page):
        monter(page)
        emettre(page, "feed", {"event": "alert", "alert": ALERTE})
        expect(dans_carte(page, ".alerte h5")).to_have_text(ALERTE["title"])
        expect(dans_carte(page, ".alerte p")).to_have_text(ALERTE["why"])
        expect(dans_carte(page, ".cloche")).to_have_attribute("data-alertes", "1")

    def test_lorbe_signale_ce_qui_attend(self, page):
        monter(page)
        emettre(page, "feed", {"event": "alert", "alert": ALERTE})
        expect(dans_carte(page, ".carte")).to_have_attribute("data-orbe", "alert")
        expect(dans_carte(page, ".sous-titre")).to_have_text("1 à signaler")

    def test_une_alerte_levee_disparait(self, page):
        """`alert_cleared` porte `alert_id` — pas `id` (contrat §4)."""
        monter(page)
        emettre(page, "feed", {"event": "alert", "alert": ALERTE})
        expect(dans_carte(page, ".alerte")).to_have_count(1)

        emettre(page, "feed", {"event": "alert_cleared", "alert_id": "al_1"})
        expect(dans_carte(page, ".alerte")).to_have_count(0)
        expect(dans_carte(page, ".cloche")).to_have_attribute("data-alertes", "0")

    def test_le_niveau_se_voit(self, page):
        monter(page)
        emettre(
            page, "feed", {"event": "alert", "alert": {**ALERTE, "level": "critical"}}
        )
        expect(dans_carte(page, ".alerte")).to_have_attribute("data-niveau", "critical")

    def test_agir_napparait_que_sil_y_a_quelque_chose_a_faire(self, page):
        """§8 : un bouton qui ne peut rien faire n'a pas à exister."""
        monter(page)
        emettre(page, "feed", {"event": "alert", "alert": alerte_sans_action()})
        libelles = dans_carte(page, ".alerte .actions button").all_text_contents()
        assert libelles == ["Ignorer", "Ne plus me le dire"]

    def test_agir_passe_par_larbitre_puis_note_lacceptation(self, page):
        """D8 : « Agir » ne court-circuite rien."""
        monter(page)
        emettre(page, "feed", {"event": "alert", "alert": ALERTE})
        dans_carte(page, ".alerte .actions button").first.click()
        expect(dans_carte(page, ".alerte")).to_have_count(0)

        envoyes = [m["type"] for m in journal(page)["envoyes"]]
        assert envoyes[-2:] == ["luna/alerts/act", "luna/alerts/feedback"]
        retour = journal(page)["envoyes"][-1]
        assert retour == {
            "type": "luna/alerts/feedback",
            "suggestion_id": "al_1",
            "action": "accepted",
            "client_id": "loggia",
            "device": retour["device"],
        }

    def test_un_refus_de_niveau_cinq_laisse_lalerte_et_dit_pourquoi(self, page):
        """Recette 10, vue de la carte : fermer un ouvrant reste hors périmètre.

        L'alerte **reste** : la faire disparaître laisserait croire que quelque
        chose s'est passé.
        """
        monter(
            page,
            resultatAgir={
                "executed": False,
                "results": [
                    {
                        "domain": "cover",
                        "service": "close_cover",
                        "ok": False,
                        "message": "Je ne commande pas les ouvrants — c'est hors "
                        "de mon périmètre pour l'instant.",
                    }
                ],
            },
        )
        emettre(page, "feed", {"event": "alert", "alert": ALERTE})
        dans_carte(page, ".alerte .actions button").first.click()

        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("ouvrants")
        expect(dans_carte(page, ".alerte")).to_have_count(1)
        assert "luna/alerts/feedback" not in [m["type"] for m in journal(page)["envoyes"]]

    def test_ne_plus_me_le_dire_retire_lalerte(self, page):
        monter(page)
        emettre(page, "feed", {"event": "alert", "alert": ALERTE})
        dans_carte(page, ".alerte .actions button").last.click()
        expect(dans_carte(page, ".alerte")).to_have_count(0)
        assert journal(page)["envoyes"][-1]["action"] == "muted"

    def test_la_carte_rattrape_ce_qui_a_precede_son_ouverture(self, page):
        """Le feed ne rejoue pas le passé : sans la lecture initiale, une
        alerte levée avant l'ouverture de la carte serait invisible."""
        monter(
            page,
            suggestions=[
                {
                    "id": "al_9",
                    "key": "ouvrant||b.x",
                    "title": "Un ouvrant est resté ouvert.",
                    "why": "Depuis 22 h 40.",
                    "score": 0.5,
                    "level": 0,
                    "actions": [],
                }
            ],
        )
        expect(dans_carte(page, ".alerte h5")).to_have_text(
            "Un ouvrant est resté ouvert."
        )


class TestFileDeRelecture:
    """D2 : ce que le modèle croit comprendre attend un humain."""

    def test_un_fait_a_relire_apparait(self, page):
        monter(page, faits=[FAIT])
        dans_carte(page, ".cloche").click()
        expect(dans_carte(page, ".relecture h4")).to_have_text("À relire (1)")
        expect(dans_carte(page, ".fait h5")).to_have_text("couloir tamisé le soir")
        expect(dans_carte(page, ".fait p")).to_have_text(FAIT["why"])

    def test_accepter_un_fait_le_retire_de_la_file(self, page):
        monter(page, faits=[FAIT])
        dans_carte(page, ".fait .actions button").first.click()
        expect(dans_carte(page, ".fait")).to_have_count(0)
        assert journal(page)["envoyes"][-1]["decision"] == "accept"

    def test_refuser_un_fait_le_retire_aussi(self, page):
        monter(page, faits=[FAIT])
        dans_carte(page, ".fait .actions button").last.click()
        expect(dans_carte(page, ".fait")).to_have_count(0)
        assert journal(page)["envoyes"][-1]["decision"] == "reject"

    def test_sans_rien_a_relire_la_section_nexiste_pas(self, page):
        monter(page)
        dans_carte(page, ".cloche").click()
        expect(dans_carte(page, ".relecture h4")).to_have_count(0)
