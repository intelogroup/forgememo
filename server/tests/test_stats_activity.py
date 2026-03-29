"""Tests for GET /v1/stats and GET /v1/activity."""
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
    test_db = Database(tmp_path / "stats_activity.db")
    test_db.init()
    monkeypatch.setattr(m, "db", test_db)
    client = TestClient(m.app, raise_server_exceptions=True)
    return client, test_db


def _make_auth_header(db: Database, email: str = "u@example.com") -> dict:
    uid = db.create_user(email)
    token = create_session_token(uid)
    return {"Authorization": f"Bearer {token}"}, uid


# ---------------------------------------------------------------------------
# GET /v1/stats
# ---------------------------------------------------------------------------

def test_stats_no_runs(app_client):
    client, db = app_client
    headers, uid = _make_auth_header(db, "norun@example.com")
    resp = client.get("/v1/stats", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_runs"] == 0
    assert "balance_usd" in data
    # traces/principles/projects should NOT appear when trace count is 0
    assert "traces" not in data
    assert "principles" not in data
    assert "projects" not in data


def test_stats_balance(app_client):
    client, db = app_client
    uid = db.create_user("bal@example.com", initial_balance=3.5)
    token = create_session_token(uid)
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get("/v1/stats", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["balance_usd"] == 3.5


def test_stats_with_runs(app_client):
    client, db = app_client
    headers, uid = _make_auth_header(db, "withrun@example.com")
    db.log_run(uid, "run1", 0.02, "claude-haiku", 4.98)
    db.log_run(uid, "run2", 0.03, "claude-haiku", 4.95)
    resp = client.get("/v1/stats", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total_runs"] == 2


def test_stats_with_synced_traces(app_client):
    client, db = app_client
    headers, uid = _make_auth_header(db, "traced@example.com")
    # Push traces and principles
    db.upsert_device(uid, "dev1", "My Device")
    db.upsert_trace(uid, "dev1", {"local_id": "t1", "content": "hello", "project_tag": "proj-a"})
    db.upsert_trace(uid, "dev1", {"local_id": "t2", "content": "world", "project_tag": "proj-b"})
    db.upsert_principle(uid, "dev1", {"local_id": "p1", "principle": "be good"})
    resp = client.get("/v1/stats", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["traces"] == 2
    assert data["principles"] == 1
    assert set(data["projects"]) == {"proj-a", "proj-b"}


def test_stats_requires_auth(app_client):
    client, _ = app_client
    resp = client.get("/v1/stats")
    assert resp.status_code == 422  # missing required header


def test_stats_bad_token(app_client):
    client, _ = app_client
    resp = client.get("/v1/stats", headers={"Authorization": "Bearer bad_jwt"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /v1/activity
# ---------------------------------------------------------------------------

def test_activity_no_runs_returns_empty_list(app_client):
    client, db = app_client
    headers, _ = _make_auth_header(db, "empty@example.com")
    resp = client.get("/v1/activity", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_activity_returns_run_list(app_client):
    client, db = app_client
    headers, uid = _make_auth_header(db, "active@example.com")
    db.log_run(uid, "r1", 0.02, "claude-haiku", 4.98)
    db.log_run(uid, "r2", 0.05, "claude-sonnet", 4.93)
    resp = client.get("/v1/activity", headers=headers)
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 2
    # Each item should have model, cost_usd, ts
    for r in runs:
        assert "model" in r
        assert "cost_usd" in r
        assert "ts" in r


def test_activity_run_fields(app_client):
    client, db = app_client
    headers, uid = _make_auth_header(db, "fields@example.com")
    db.log_run(uid, "r_fields", 0.033, "claude-haiku", 4.967)
    resp = client.get("/v1/activity", headers=headers)
    assert resp.status_code == 200
    run = resp.json()[0]
    assert run["model"] == "claude-haiku"
    assert abs(run["cost_usd"] - 0.033) < 1e-6


def test_activity_only_own_runs(app_client):
    """User should only see their own runs."""
    client, db = app_client
    headers_a, uid_a = _make_auth_header(db, "a@example.com")
    uid_b = db.create_user("b@example.com")
    db.log_run(uid_a, "run_a", 0.01, "claude-haiku", 4.99)
    db.log_run(uid_b, "run_b", 0.02, "claude-haiku", 4.98)
    resp = client.get("/v1/activity", headers=headers_a)
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 1


def test_activity_requires_auth(app_client):
    client, _ = app_client
    resp = client.get("/v1/activity")
    assert resp.status_code == 422  # missing required header


def test_activity_bad_token(app_client):
    client, _ = app_client
    resp = client.get("/v1/activity", headers={"Authorization": "Bearer not_a_jwt"})
    assert resp.status_code == 401
