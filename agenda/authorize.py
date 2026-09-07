#!/usr/bin/env python3
"""Autorisation Google Agenda (une seule fois) → jeton de rafraîchissement.

À lancer SUR TON PC (celui qui a un navigateur), pas sur Nebula. Il ouvre l'écran
de consentement Google, récupère le code sur une redirection locale, l'échange
contre un jeton de rafraîchissement, et te l'affiche à coller dans .env
(GCAL_REFRESH_TOKEN). Portée demandée : calendar.readonly (LECTURE SEULE — aucune
création ni modification d'événement n'est possible, jamais).

Réutilise le MÊME identifiant OAuth « Application de bureau » que Gmail
(GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET) : il te suffit d'activer l'API Google
Calendar sur le même projet Google Cloud. Seul le jeton de rafraîchissement
diffère (il porte la portée agenda).

Aucune dépendance : uniquement la bibliothèque standard de Python 3.

    python agenda/authorize.py --client-id XXX --client-secret YYY
    # (ou définis GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET dans l'environnement)

Par défaut la portée est calendar.readonly (LECTURE seule, Phase 12). Pour aussi
autoriser Luna à PROPOSER des rendez-vous (Phase 13), ajoute --write : la portée
devient calendar.events (lecture + écriture d'événements), et il faudra mettre
GCAL_WRITE=1 dans .env. Même dans ce mode, Luna ne crée jamais rien seule : elle
dépose une proposition que tu approuves dans le cockpit.

Prérequis (voir docs/AGENDA.md) : le projet Google Cloud de Gmail, avec en plus
l'API « Google Calendar » activée.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import secrets
import socket
import urllib.parse
import urllib.request
import webbrowser

SCOPE_READ = "https://www.googleapis.com/auth/calendar.readonly"
SCOPE_WRITE = "https://www.googleapis.com/auth/calendar.events"  # lecture + écriture d'événements
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    ap = argparse.ArgumentParser(description="Autorisation Google Agenda pour Sentinel")
    ap.add_argument("--client-id", default=os.environ.get("GMAIL_CLIENT_ID", ""))
    ap.add_argument("--client-secret", default=os.environ.get("GMAIL_CLIENT_SECRET", ""))
    ap.add_argument("--write", action="store_true",
                    help="Autoriser l'écriture d'événements (portée calendar.events, Phase 13)")
    args = ap.parse_args()
    scope = SCOPE_WRITE if args.write else SCOPE_READ
    client_id = args.client_id.strip() or input("GMAIL_CLIENT_ID : ").strip()
    client_secret = args.client_secret.strip() or input("GMAIL_CLIENT_SECRET : ").strip()
    if not client_id or not client_secret:
        raise SystemExit("client-id et client-secret sont requis.")

    port = _free_port()
    redirect_uri = f"http://localhost:{port}/"
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",   # indispensable pour obtenir un refresh_token
        "prompt": "consent",        # force la délivrance d'un refresh_token
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    holder: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            holder["code"] = (q.get("code") or [""])[0]
            holder["state"] = (q.get("state") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<h2>Merci — tu peux fermer cet onglet et revenir au terminal.</h2>".encode()
            )

        def log_message(self, *_):  # silence
            return

    print("\nUne fenêtre de consentement Google va s'ouvrir.")
    print("Si rien ne s'ouvre, copie cette adresse dans ton navigateur :\n", url, "\n")
    webbrowser.open(url)

    with http.server.HTTPServer(("127.0.0.1", port), Handler) as httpd:
        httpd.handle_request()  # attend la redirection

    if not holder.get("code") or holder.get("state") != state:
        raise SystemExit("Autorisation annulée ou invalide (état non concordant).")

    body = urllib.parse.urlencode({
        "code": holder["code"],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body)
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (URL fixe et de confiance)
        token = json.load(resp)

    refresh = token.get("refresh_token")
    if not refresh:
        raise SystemExit(
            "Aucun refresh_token renvoyé. Réessaie en révoquant d'abord l'accès de "
            "l'app (myaccount.google.com/permissions), le prompt de consentement est requis."
        )
    print("\n✅ Autorisation réussie. Colle ceci dans ton fichier .env sur Nebula :\n")
    print(f"GCAL_REFRESH_TOKEN={refresh}")
    if args.write:
        print("GCAL_WRITE=1")
        print("\n(Écriture activée : Luna pourra PRÉPARER des rendez-vous — tu les "
              "approuves dans le cockpit, rien n'est créé sans ton accord.)\n")
    else:
        print()


if __name__ == "__main__":
    main()
