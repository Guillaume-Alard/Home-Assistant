"""Courriel en lecture seule (Phase 3) : parsing du résumé et repli d'erreur."""

from __future__ import annotations

import httpx
import pytest

from app.mail import GmailClient, MailError


def _ok_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "oauth2.googleapis.com/token" in url:
        return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
    if url.rstrip("?").endswith("/messages") or "/messages?" in url:
        return httpx.Response(200, json={
            "messages": [{"id": "1"}, {"id": "2"}], "resultSizeEstimate": 7,
        })
    if "/messages/1" in url:
        return httpx.Response(200, json={
            "snippet": "On se voit demain ?",
            "labelIds": ["UNREAD", "IMPORTANT"],
            "payload": {"headers": [
                {"name": "From", "value": "Alice Martin <alice@example.com>"},
                {"name": "Subject", "value": "Réunion"},
                {"name": "Date", "value": "Mon, 8 Sep 2026 09:00:00 +0200"},
            ]},
        })
    if "/messages/2" in url:
        return httpx.Response(200, json={
            "snippet": "Profitez des soldes",
            "labelIds": ["UNREAD"],
            "payload": {"headers": [
                {"name": "From", "value": "news@shop.com"},
                {"name": "Subject", "value": "Soldes"},
            ]},
        })
    return httpx.Response(404)


def _client(handler, **kw):
    return GmailClient("id", "secret", "refresh", transport=httpx.MockTransport(handler), **kw)


async def test_resume_structure():
    data = await _client(_ok_handler).summary()
    assert data["unread_total"] == 7  # estimation Gmail, pas le nombre détaillé
    m0, m1 = data["messages"]
    assert m0["from_name"] == "Alice Martin" and m0["from_email"] == "alice@example.com"
    assert m0["subject"] == "Réunion" and m0["important"] is True
    assert "demain" in m0["snippet"]
    # Expéditeur sans nom affiché → l'adresse tient lieu de nom
    assert m1["from_name"] == "news@shop.com" and m1["important"] is False


async def test_token_revoque_leve_mailerror():
    def handler(request):
        if "token" in str(request.url):
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={})
    with pytest.raises(MailError) as exc:
        await _client(handler).summary()
    assert "autorisation" in str(exc.value).lower()


async def test_jeton_dacces_mis_en_cache():
    calls = {"token": 0}

    def handler(request):
        url = str(request.url)
        if "token" in url:
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if "/messages" in url and "/messages/" not in url:
            return httpx.Response(200, json={"messages": [], "resultSizeEstimate": 0})
        return httpx.Response(404)

    client = _client(handler)
    await client.summary()
    await client.summary()
    assert calls["token"] == 1  # deux relevés, un seul rafraîchissement de jeton
