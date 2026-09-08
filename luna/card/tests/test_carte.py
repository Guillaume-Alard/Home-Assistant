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
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

BANC = (Path(__file__).parent / "banc.html").resolve().as_uri()

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
            nav = (
                p.chromium.launch(executable_path=chemin)
                if chemin
                else p.chromium.launch()
            )
        except PlaywrightError as err:
            pytest.skip(f"Chromium indisponible : {err}")
        yield nav
        nav.close()


@pytest.fixture
def page(navigateur):
    contexte = navigateur.new_context()
    page = contexte.new_page()
    erreurs: list[str] = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.set_default_timeout(DELAI)
    page.goto(BANC)
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

    def test_le_micro_est_grise_en_phase_1(self, page):
        monter(page)
        expect(dans_carte(page, ".micro")).to_be_disabled()

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
    def test_contexte_non_securise(self, page):
        """§8, littéralement : « le contexte non-HTTPS produit un message
        d'erreur explicite et actionnable »."""
        monter(page)
        page.evaluate("""() => {
          Object.defineProperty(window, 'isSecureContext',
            { value: false, configurable: true });
          const c = window.__carte;
          c.shadowRoot.querySelector('.micro').disabled = false;
        }""")
        dans_carte(page, ".micro").click()
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("HTTPS")
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("phase 0")

    def test_phase_voix(self, page):
        monter(page)
        page.evaluate(
            "() => { window.__carte.shadowRoot"
            ".querySelector('.micro').disabled = false; }"
        )
        dans_carte(page, ".micro").click()
        expect(dans_carte(page, ".bulle.erreur")).to_contain_text("phase 2")


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
