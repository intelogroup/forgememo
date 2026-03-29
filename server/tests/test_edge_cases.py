"""Edge case tests across db, auth, billing, usage, and main.py routes."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("FORGEMEM_JWT_SECRET", "testsecret_64chars_padding_padding_padding_padding_padding_pad")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_fake")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test")
os.environ.setdefault("RESEND_API_KEY", "re_test")

import pytest
from fastapi.testclient import TestClient

from auth import create_session_token
from db import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "edge.db")
    d.init()
    return d


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """TestClient with an isolated DB."""
    import main as m
    test_db = Database(tmp_path / "main_edge.db")
    test_db.init()
    monkeypatch.setattr(m, "db", test_db)
    return TestClient(m.app), test_db


def _make_session(db: Database, email: str = "u@example.com") -> str:
    uid = db.create_user(email)
    token = create_session_token(uid)
    db.create_session(token, uid)
    return token


# ---------------------------------------------------------------------------
# DB edge cases
# ---------------------------------------------------------------------------

def test_duplicate_email_raises(db):
    db.create_user("dupe@example.com")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        db.create_user("dupe@example.com")


def test_deduct_exactly_to_zero(db):
    uid = db.create_user("zero@example.com", initial_balance=0.02)
    new_balance = db.deduct_credits(uid, 0.02)
    assert new_balance == 0.0


def test_deduct_fractional_precision(db):
    uid = db.create_user("frac@example.com", initial_balance=1.0)
    b1 = db.deduct_credits(uid, 0.020002)
    b2 = db.deduct_credits(uid, 0.020002)
    assert b1 > b2 > 0


def test_expired_session_returns_none(db):
    uid = db.create_user("exp@example.com")
    # Create session that already expired
    import sqlite3
    now = int(time.time())
    conn = sqlite3.connect(db.path)
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        ("expired_tok", uid, now - 100, now - 1),
    )
    conn.commit()
    conn.close()
    assert db.get_user_by_session("expired_tok") is None


def test_expired_magic_link_returns_none(db):
    import sqlite3
    now = int(time.time())
    conn = sqlite3.connect(db.path)
    conn.execute(
        "INSERT INTO magic_link_tokens (token, email, callback, state, created_at, expires_at) VALUES (?,?,?,?,?,?)",
        ("old_tok", "x@x.com", "http://127.0.0.1:9000/cb", "s1", now - 700, now - 100),
    )
    conn.commit()
    conn.close()
    assert db.consume_magic_link_token("old_tok") is None


def test_nonexistent_user_deduct_raises(db):
    with pytest.raises(ValueError, match="not found"):
        db.deduct_credits("ghost_id", 1.0)


def test_top_up_nonexistent_user_raises(db):
    with pytest.raises(ValueError, match="not found"):
        db.top_up_credits("ghost_id", 10.0)


def test_run_count_window_excludes_old(db):
    uid = db.create_user("window@example.com")
    # Insert a run from 2 hours ago manually
    import sqlite3
    conn = sqlite3.connect(db.path)
    conn.execute(
        "INSERT INTO usage_runs (run_id, user_id, cost_usd, model, balance_usd, ts) VALUES (?,?,?,?,?,?)",
        ("old_run", uid, 0.02, "test", 4.98, int(time.time()) - 7300),
    )
    conn.commit()
    conn.close()
    # 1-hour window should not count it
    assert db.run_count_in_window(uid, window_seconds=3600) == 0
    # 3-hour window should count it
    assert db.run_count_in_window(uid, window_seconds=10800) == 1


# ---------------------------------------------------------------------------
# Auth edge cases
# ---------------------------------------------------------------------------

def test_missing_jwt_secret_raises():
    original = os.environ.pop("FORGEMEM_JWT_SECRET", None)
    try:
        import importlib
        import auth as a
        importlib.reload(a)
        # JWT_SECRET will be "" after reload with no env var
        with pytest.raises(RuntimeError, match="FORGEMEM_JWT_SECRET"):
            a.create_session_token("u")
    finally:
        if original:
            os.environ["FORGEMEM_JWT_SECRET"] = original
        import importlib
        import auth as a
        importlib.reload(a)


def test_tampered_token_raises():
    from auth import create_session_token, verify_session_token
    token = create_session_token("user1")
    # Flip last char
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(ValueError):
        verify_session_token(tampered)


# ---------------------------------------------------------------------------
# Billing edge cases
# ---------------------------------------------------------------------------

def test_checkout_session_missing_metadata_returns_none():
    mock_event = {
        "id": "evt_no_meta",
        "type": "checkout.session.completed",
        "data": {"object": {"payment_status": "paid", "metadata": {}}},
    }
    with patch("stripe.Webhook.construct_event", return_value=mock_event):
        from billing import parse_webhook_event
        assert parse_webhook_event(b"", "sig") is None


def test_checkout_session_bad_stripe_sig_raises():
    import stripe
    with patch("stripe.Webhook.construct_event", side_effect=stripe.error.SignatureVerificationError("bad", "sig")):
        from billing import parse_webhook_event
        with pytest.raises(stripe.error.SignatureVerificationError):
            parse_webhook_event(b"body", "bad_sig")


# ---------------------------------------------------------------------------
# HTTP route edge cases (via TestClient)
# ---------------------------------------------------------------------------

def test_inference_missing_auth_header(app_client):
    client, _ = app_client
    resp = client.post("/v1/inference", json={"prompt": "hello"})
    assert resp.status_code == 422  # FastAPI: missing required header


def test_inference_bad_token(app_client):
    client, _ = app_client
    resp = client.post(
        "/v1/inference",
        json={"prompt": "hello"},
        headers={"Authorization": "Bearer not_a_jwt"},
    )
    assert resp.status_code == 401


def test_balance_expired_session(app_client):
    client, db = app_client
    uid = db.create_user("old@x.com")
    import sqlite3
    import time as t
    now = int(t.time())
    conn = sqlite3.connect(db.path)
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        ("dead_token", uid, now - 100, now - 1),
    )
    conn.commit()
    conn.close()
    resp = client.get("/v1/balance", headers={"Authorization": "Bearer dead_token"})
    assert resp.status_code == 401


def test_cli_auth_rejects_external_callback(app_client):
    client, _ = app_client
    resp = client.get("/cli-auth?callback=https://evil.com/steal&state=abc")
    assert resp.status_code == 400


def test_cli_auth_rejects_empty_callback(app_client):
    client, _ = app_client
    resp = client.get("/cli-auth?callback=&state=abc")
    assert resp.status_code == 400


def test_cli_auth_send_link_invalid_email(app_client):
    client, _ = app_client
    resp = client.post(
        "/cli-auth/send-link",
        data={"email": "notanemail", "callback": "http://127.0.0.1:9000/cb", "state": "s"},
    )
    assert resp.status_code == 400


def test_cli_auth_send_link_external_callback(app_client):
    client, _ = app_client
    resp = client.post(
        "/cli-auth/send-link",
        data={"email": "x@x.com", "callback": "https://evil.com/cb", "state": "s"},
    )
    assert resp.status_code == 400


def test_cli_auth_verify_wrong_state(app_client):
    client, db = app_client
    from auth import create_magic_link_token
    tok = create_magic_link_token()
    db.create_magic_link_token(tok, "v@x.com", "http://127.0.0.1:9000/cb", "correct_state")
    resp = client.get(
        f"/cli-auth/verify?token={tok}&callback=http://127.0.0.1:9000/cb&state=wrong_state",
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_cli_auth_verify_valid_first_login(app_client):
    client, db = app_client
    from auth import create_magic_link_token
    tok = create_magic_link_token()
    db.create_magic_link_token(tok, "new@x.com", "http://127.0.0.1:9000/cb", "s1")
    resp = client.get(
        f"/cli-auth/verify?token={tok}&callback=http://127.0.0.1:9000/cb&state=s1",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "token=" in resp.headers["location"]
    # User was auto-created
    user = db.get_user_by_email("new@x.com")
    assert user is not None
    assert user["balance_usd"] == 5.0


def test_cli_auth_verify_replay_rejected(app_client):
    """Same magic link token cannot be used twice."""
    client, db = app_client
    from auth import create_magic_link_token
    tok = create_magic_link_token()
    db.create_magic_link_token(tok, "replay@x.com", "http://127.0.0.1:9000/cb", "s2")
    # First use
    r1 = client.get(
        f"/cli-auth/verify?token={tok}&callback=http://127.0.0.1:9000/cb&state=s2",
        follow_redirects=False,
    )
    assert r1.status_code == 302
    # Second use
    r2 = client.get(
        f"/cli-auth/verify?token={tok}&callback=http://127.0.0.1:9000/cb&state=s2",
        follow_redirects=False,
    )
    assert r2.status_code == 400


def test_stripe_webhook_duplicate_event(app_client):
    client, db = app_client
    uid = db.create_user("dup@x.com")
    mock_event = {
        "id": "evt_dup",
        "type": "checkout.session.completed",
        "data": {"object": {"metadata": {"user_id": uid, "credit_usd": "5.0"}, "payment_status": "paid"}},
    }
    with patch("stripe.Webhook.construct_event", return_value=mock_event):
        r1 = client.post("/webhooks/stripe", content=b"body", headers={"stripe-signature": "sig"})
        r2 = client.post("/webhooks/stripe", content=b"body", headers={"stripe-signature": "sig"})
    assert r1.json()["status"] == "ok"
    assert r2.json()["status"] == "duplicate"
    # Balance only topped up once
    user = db.get_user_by_email("dup@x.com")
    assert abs(user["balance_usd"] - 10.0) < 0.001  # 5 initial + 5 top-up (not 15)


def test_checkout_unknown_pack(app_client):
    client, db = app_client
    token = _make_session(db, "pack@x.com")
    resp = client.post(
        "/v1/checkout",
        json={"pack_id": "nonexistent"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "Unknown" in resp.json()["detail"]
