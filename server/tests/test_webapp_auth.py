"""Tests for POST /webapp-auth/send-link and GET /webapp-auth/verify."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("FORGEMEM_JWT_SECRET", "testsecret_64chars_padding_padding_padding_padding_padding_pad")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_fake")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test")
os.environ.setdefault("RESEND_API_KEY", "re_test")
os.environ.setdefault("WEBAPP_ORIGIN", "http://localhost:3000")

import pytest
from fastapi.testclient import TestClient

from auth import create_magic_link_token, verify_session_token
from db import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """TestClient with an isolated DB and email sending suppressed."""
    import main as m
    test_db = Database(tmp_path / "webapp_auth.db")
    test_db.init()
    monkeypatch.setattr(m, "db", test_db)
    # Patch WEBAPP_ORIGIN in main module to match env var
    monkeypatch.setattr(m, "_WEBAPP_ORIGIN", "http://localhost:3000")
    client = TestClient(m.app, raise_server_exceptions=True)
    return client, test_db


# ---------------------------------------------------------------------------
# POST /webapp-auth/send-link
# ---------------------------------------------------------------------------

def test_send_link_valid(app_client):
    client, _ = app_client
    with patch("email_sender.send_magic_link") as mock_send:
        resp = client.post(
            "/webapp-auth/send-link",
            json={"email": "user@example.com", "callback_url": "http://localhost:3000/auth/callback"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_send.assert_called_once()


def test_send_link_invalid_callback_external(app_client):
    client, _ = app_client
    resp = client.post(
        "/webapp-auth/send-link",
        json={"email": "user@example.com", "callback_url": "https://evil.com/steal"},
    )
    assert resp.status_code == 400


def test_send_link_invalid_callback_loopback(app_client):
    """Loopback addresses are not valid for webapp auth (only for CLI)."""
    client, _ = app_client
    resp = client.post(
        "/webapp-auth/send-link",
        json={"email": "user@example.com", "callback_url": "http://127.0.0.1:9000/cb"},
    )
    assert resp.status_code == 400


def test_send_link_invalid_email(app_client):
    client, _ = app_client
    resp = client.post(
        "/webapp-auth/send-link",
        json={"email": "notanemail", "callback_url": "http://localhost:3000/auth/callback"},
    )
    assert resp.status_code == 400


def test_send_link_exact_origin(app_client):
    """callback_url == WEBAPP_ORIGIN (no trailing path) is also valid."""
    client, _ = app_client
    with patch("email_sender.send_magic_link"):
        resp = client.post(
            "/webapp-auth/send-link",
            json={"email": "user@example.com", "callback_url": "http://localhost:3000"},
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /webapp-auth/verify
# ---------------------------------------------------------------------------

def test_verify_valid_token_redirects(app_client):
    client, db = app_client
    tok = create_magic_link_token()
    db.create_magic_link_token(tok, "newuser@example.com", "http://localhost:3000/auth/callback", "")
    resp = client.get(f"/webapp-auth/verify?token={tok}", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "token=" in location
    assert location.startswith("http://localhost:3000/auth/callback")


def test_verify_creates_new_user(app_client):
    client, db = app_client
    tok = create_magic_link_token()
    db.create_magic_link_token(tok, "brandnew@example.com", "http://localhost:3000/auth/callback", "")
    assert db.get_user_by_email("brandnew@example.com") is None
    client.get(f"/webapp-auth/verify?token={tok}", follow_redirects=False)
    user = db.get_user_by_email("brandnew@example.com")
    assert user is not None
    assert user["balance_usd"] == 5.0


def test_verify_existing_user_returns_jwt(app_client):
    client, db = app_client
    # Pre-create user
    uid = db.create_user("existing@example.com", initial_balance=3.0)
    tok = create_magic_link_token()
    db.create_magic_link_token(tok, "existing@example.com", "http://localhost:3000/auth/callback", "")
    resp = client.get(f"/webapp-auth/verify?token={tok}", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    # Extract JWT from location
    import urllib.parse
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    jwt = urllib.parse.unquote(params["token"][0])
    payload = verify_session_token(jwt)
    assert payload["sub"] == uid


def test_verify_invalid_token_returns_400(app_client):
    client, _ = app_client
    resp = client.get("/webapp-auth/verify?token=totally_fake_token", follow_redirects=False)
    assert resp.status_code == 400


def test_verify_empty_token_returns_400(app_client):
    client, _ = app_client
    resp = client.get("/webapp-auth/verify?token=", follow_redirects=False)
    assert resp.status_code == 400


def test_verify_expired_token_returns_400(app_client):
    client, db = app_client
    import sqlite3
    import time
    now = int(time.time())
    conn = sqlite3.connect(db.path)
    conn.execute(
        "INSERT INTO magic_link_tokens (token, email, callback, state, created_at, expires_at) VALUES (?,?,?,?,?,?)",
        ("expired_tok", "x@x.com", "http://localhost:3000/auth/callback", "", now - 700, now - 100),
    )
    conn.commit()
    conn.close()
    resp = client.get("/webapp-auth/verify?token=expired_tok", follow_redirects=False)
    assert resp.status_code == 400


def test_verify_already_used_token_returns_400(app_client):
    client, db = app_client
    tok = create_magic_link_token()
    db.create_magic_link_token(tok, "replay@example.com", "http://localhost:3000/auth/callback", "")
    # First use succeeds
    r1 = client.get(f"/webapp-auth/verify?token={tok}", follow_redirects=False)
    assert r1.status_code == 302
    # Second use rejected
    r2 = client.get(f"/webapp-auth/verify?token={tok}", follow_redirects=False)
    assert r2.status_code == 400
