"""Test de la carte Luna avec un vrai navigateur (Chromium via Playwright).

Frontend, donc éprouvé dans un navigateur — et volontairement HORS de la suite
`core/tests` (purement Python/backend) et hors de pytest : l'intégration est un
paquet Home Assistant dont l'import exige `homeassistant`, absent d'un simple
poste de dev. On lance donc ce fichier directement :

    pip install playwright && playwright install chromium
    python custom_components/sentinel_assist/tests/test_carte.py

Un binaire Chromium déjà présent peut être désigné par la variable PW_CHROMIUM.
Sortie : « OK » et code 0 si tout passe ; une AssertionError sinon.
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import os
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # custom_components/sentinel_assist/


@contextlib.contextmanager
def _serveur(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


@contextlib.contextmanager
def _page(sync_playwright):
    launch = {}
    if os.environ.get("PW_CHROMIUM"):
        launch["executable_path"] = os.environ["PW_CHROMIUM"]
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 460, "height": 560})
        try:
            yield page
        finally:
            browser.close()


def _ouvrir(page, base):
    page.goto(f"{base}/tests/banc.html")
    page.wait_for_function("() => window.__banc && document.getElementById('carte').shadowRoot")


def test_conversation_et_orbe(sync_playwright):
    with _serveur(ROOT) as base, _page(sync_playwright) as page:
        _ouvrir(page, base)
        assert page.evaluate("() => window.__banc.etatOrbe()") == "idle"

        page.evaluate("() => window.__banc.envoyer('éteins le salon')")
        # Pendant l'attente : l'orbe réfléchit, et ma bulle est déjà là.
        page.wait_for_function("() => window.__banc.etatOrbe() === 'thinking'")
        assert any(b["classe"].endswith("moi") for b in page.evaluate("() => window.__banc.bulles()"))

        # La réponse de Luna arrive, puis l'orbe revient au repos.
        page.wait_for_function(
            "() => window.__banc.bulles().some(b => b.classe.includes('luna') && !b.classe.includes('points'))"
        )
        page.wait_for_function("() => window.__banc.etatOrbe() === 'idle'", timeout=4000)
        luna = [b for b in page.evaluate("() => window.__banc.bulles()")
                if "luna" in b["classe"] and "points" not in b["classe"]]
        assert luna and "salon" in luna[-1]["texte"]

        # La carte a parlé au bon agent, par le pipeline d'HA (jamais un fetch).
        proc = [a for a in page.evaluate("() => window.__banc.appels()")
                if a["type"] == "conversation/process"]
        assert proc and proc[0]["agent_id"] == "conversation.sentinel"
        assert proc[0]["text"] == "éteins le salon"


def test_erreur_affiche_une_bulle(sync_playwright):
    with _serveur(ROOT) as base, _page(sync_playwright) as page:
        _ouvrir(page, base)
        page.evaluate("() => window.__banc.modeErreur()")
        page.evaluate("() => window.__banc.envoyer('coucou')")
        page.wait_for_function("() => window.__banc.bulles().some(b => b.classe.includes('erreur'))")
        assert page.evaluate("() => window.__banc.etatOrbe()") == "idle"


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright absent — `pip install playwright && playwright install chromium`.")
        return 0  # pas un échec : le test est simplement indisponible ici
    test_conversation_et_orbe(sync_playwright)
    test_erreur_affiche_une_bulle(sync_playwright)
    print("OK — carte Luna : conversation, orbe et gestion d'erreur.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
