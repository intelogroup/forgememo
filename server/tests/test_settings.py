"""Tests for GET /v1/user/settings."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("FORGEMEM_JWT_SECRET", "testsecret_64chars_padding_padding_padding_padding_padding_pad")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_fake")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test")
os.environ.setdefault("RESEND_API_KEY", "re_test")
os.environ.setdefault("WEBAPP_ORIGIN", "http://localhost:3000")

import pytest
from fastapi.testclient import TestClient

from auth import create_session_token
from db import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_client(tmp_path, monkeypatch):
    import main as m
    test_db = Database(tmp_path / "settings.db")
    test_db.init()
    monkeypatch.setattr(m, "db", test_db)
    client = TestClient(m.app, raise_server_exceptions=True)
    return client, test_db


def _auth_headers(db: Database, email: str = "settings@example.com") -> dict:
    uid = db.create_user(email)
    token = create_session_token(uid)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# GET /v1/user/settings
# ---------------------------------------------------------------------------

def test_settings_authenticated(app_client):
    client, db = app_client
    headers = _auth_headers(db)
    resp = client.get("/v1/user/settings", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"provider": "forgemem"}


def test_settings_missing_auth_header(app_client):
    client, _ = app_client
    resp = client.get("/v1/user/settings")
    assert resp.status_code == 422  # FastAPI: missing required header


def test_settings_bad_token(app_client):
    client, _ = app_client
    resp = client.get("/v1/user/settings", headers={"Authorization": "Bearer bad_token_xyz"})
    assert resp.status_code == 401


def test_settings_expired_jwt(app_client):
    client, db = app_client
    uid = db.create_user("expired@example.com")
    # Create a token that is already expired (ttl=-1)
    token = create_session_token(uid, ttl_seconds=-1)
    resp = client.get("/v1/user/settings", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_settings_nonexistent_user_jwt(app_client):
    """JWT for a user_id not in DB should return 401."""
    client, _ = app_client
    token = create_session_token("ghost_user_id_not_in_db")
    resp = client.get("/v1/user/settings", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
