"""
Tests for error_events API endpoints (POST/GET/recall).
"""

import json
import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

import forgememo.daemon as daemon_module
import forgememo.storage as storage_module
from forgememo.daemon import create_app
from forgememo.storage import SCHEMA_SQL


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own database."""
    db_path = tmp_path / "test.db"
    os.environ["FORGEMEM_DB"] = str(db_path)
    monkeypatch.setattr(storage_module, "DB_PATH", db_path)
    monkeypatch.setattr(daemon_module, "_write_lock", threading.Lock())

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

    yield db_path


@pytest.fixture
def client(isolated_db):
    """Create Flask test client with isolated DB."""
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


class TestPostErrorEvents:
    """Test POST /error_events endpoint."""

    def test_creates_error_event(self, client):
        """POST creates a new error event."""
        response = client.post(
            "/error_events",
            json={
                "session_id": "test-session-1",
                "fingerprint": "fp-123",
                "error_keywords": "ConnectionError,timeout",
                "error_text": "Failed to connect to server",
            },
        )

        assert response.status_code == 201
        data = response.get_json()
        assert data["status"] == "ok"

    def test_creates_with_project_id(self, client, isolated_db):
        """POST with project_id canonicalizes path on macOS/Windows."""
        response = client.post(
            "/error_events",
            json={
                "session_id": "test-session-2",
                "project_id": "/Users/Developer/ProjectA",
                "fingerprint": "fp-456",
                "error_keywords": "ImportError",
            },
        )

        assert response.status_code == 201

        conn = sqlite3.connect(str(isolated_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT project_id FROM error_events WHERE session_id=?",
            ("test-session-2",),
        ).fetchone()
        conn.close()

        assert row is not None
        # On macOS/Windows: path is lowercased
        # On Linux: path is unchanged (case-sensitive filesystem)
        if sys.platform in ("darwin", "win32"):
            assert row["project_id"].endswith("users/developer/projecta") or row[
                "project_id"
            ].endswith("users\\developer\\projecta")
        else:
            assert "Developer" in row["project_id"] or "developer" in row["project_id"]

    def test_missing_session_id_returns_400(self, client):
        """POST without session_id returns 400."""
        response = client.post(
            "/error_events",
            json={"fingerprint": "fp-123"},
        )

        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_missing_fingerprint_returns_400(self, client):
        """POST without fingerprint returns 400."""
        response = client.post(
            "/error_events",
            json={"session_id": "test-session"},
        )

        assert response.status_code == 400

    def test_empty_body_returns_400(self, client):
        """POST with empty body returns 400."""
        response = client.post(
            "/error_events",
            json={},
        )

        assert response.status_code == 400

    def test_strips_private_content(self, client, isolated_db):
        """POST strips <private> content from error_text."""
        response = client.post(
            "/error_events",
            json={
                "session_id": "test-session-3",
                "fingerprint": "fp-789",
                "error_text": "Connection failed <private>API_KEY=secret123</private> and more",
            },
        )

        assert response.status_code == 201

        conn = sqlite3.connect(str(isolated_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT error_text FROM error_events WHERE session_id=?",
            ("test-session-3",),
        ).fetchone()
        conn.close()

        assert row is not None
        assert "secret123" not in row["error_text"]
        assert "<private>" not in row["error_text"]

    def test_truncates_long_error_text(self, client, isolated_db):
        """POST truncates error_text to 1000 chars."""
        long_text = "x" * 2000
        response = client.post(
            "/error_events",
            json={
                "session_id": "test-session-4",
                "fingerprint": "fp-truncate",
                "error_text": long_text,
            },
        )

        assert response.status_code == 201

        conn = sqlite3.connect(str(isolated_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT error_text FROM error_events WHERE session_id=?",
            ("test-session-4",),
        ).fetchone()
        conn.close()

        assert row is not None
        assert len(row["error_text"]) <= 1000


class TestGetErrorEvents:
    """Test GET /error_events endpoint."""

    def test_returns_count_and_timestamps(self, client):
        """GET returns count, last_ts, and last_recalled_at."""
        client.post(
            "/error_events",
            json={
                "session_id": "test-session-5",
                "fingerprint": "fp-count",
                "error_keywords": "Error1",
            },
        )
        client.post(
            "/error_events",
            json={
                "session_id": "test-session-5",
                "fingerprint": "fp-count",
                "error_keywords": "Error2",
            },
        )

        response = client.get(
            "/error_events?session_id=test-session-5&fingerprint=fp-count"
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["count"] == 2
        assert data["last_ts"] is not None
        assert data["last_recalled_at"] is None

    def test_missing_session_id_returns_400(self, client):
        """GET without session_id returns 400."""
        response = client.get("/error_events?fingerprint=fp-123")

        assert response.status_code == 400

    def test_missing_fingerprint_returns_400(self, client):
        """GET without fingerprint returns 400."""
        response = client.get("/error_events?session_id=test-session")

        assert response.status_code == 400

    def test_no_matching_events_returns_zero_count(self, client):
        """GET for non-existent session/fingerprint returns count=0."""
        response = client.get(
            "/error_events?session_id=nonexistent&fingerprint=fp-nonexistent"
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["count"] == 0
        assert data["last_ts"] is None


class TestRecallErrorEvent:
    """Test POST /error_events/recall endpoint."""

    def test_marks_event_as_recalled(self, client):
        """POST /recall marks the most recent matching event."""
        client.post(
            "/error_events",
            json={
                "session_id": "test-session-6",
                "fingerprint": "fp-recall-1",
                "error_keywords": "Error",
            },
        )
        client.post(
            "/error_events",
            json={
                "session_id": "test-session-6",
                "fingerprint": "fp-recall-1",
                "error_keywords": "Error",
            },
        )

        response = client.post(
            "/error_events/recall",
            json={
                "session_id": "test-session-6",
                "fingerprint": "fp-recall-1",
            },
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"

        response = client.get(
            "/error_events?session_id=test-session-6&fingerprint=fp-recall-1"
        )
        data = response.get_json()
        assert data["last_recalled_at"] is not None

    def test_recall_most_recent_only(self, client, isolated_db):
        """Only the most recent event matching session/fp is recalled."""
        client.post(
            "/error_events",
            json={
                "session_id": "test-session-7",
                "fingerprint": "fp-recall-2",
                "error_keywords": "First",
            },
        )
        client.post(
            "/error_events",
            json={
                "session_id": "test-session-7",
                "fingerprint": "fp-recall-2",
                "error_keywords": "Second",
            },
        )

        client.post(
            "/error_events/recall",
            json={
                "session_id": "test-session-7",
                "fingerprint": "fp-recall-2",
            },
        )

        conn = sqlite3.connect(str(isolated_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ts, recalled_at FROM error_events WHERE session_id=? ORDER BY ts DESC",
            ("test-session-7",),
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        assert rows[0]["recalled_at"] is not None
        assert rows[1]["recalled_at"] is None

    def test_recall_missing_fields_returns_400(self, client):
        """POST /recall without session_id or fingerprint returns 400."""
        response = client.post(
            "/error_events/recall",
            json={"session_id": "test-session"},
        )

        assert response.status_code == 400

        response = client.post(
            "/error_events/recall",
            json={"fingerprint": "fp-123"},
        )

        assert response.status_code == 400


class TestErrorEventsFallback:
    """Test error_events endpoint handles missing table gracefully."""

    def test_handles_missing_table_on_insert(self, tmp_path):
        """POST returns error if table creation fails."""
        db_path = tmp_path / "test.db"

        conn = sqlite3.connect(str(db_path))
        conn.close()

        os.environ["FORGEMEM_DB"] = str(db_path)

        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.post(
            "/error_events",
            json={
                "session_id": "test-session",
                "fingerprint": "fp-123",
            },
        )

        assert response.status_code in (201, 503)
