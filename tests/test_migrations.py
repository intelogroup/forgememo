"""
Tests for migration system (PRAGMA user_version, run_migrations, migrate_to_v2).
"""

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import forgememo.storage as storage_module
from forgememo.storage import (
    MIGRATIONS,
    SCHEMA_SQL,
    init_db,
    migrate_to_v2,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own database."""
    db_path = tmp_path / "test.db"
    os.environ["FORGEMEM_DB"] = str(db_path)
    monkeypatch.setattr(storage_module, "DB_PATH", db_path)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

    yield db_path


class TestMigrationLedger:
    """Test PRAGMA user_version tracking."""

    def test_version_after_init_db(self, isolated_db):
        """init_db() sets user_version to 2 after running migrations."""
        conn = sqlite3.connect(str(isolated_db))
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
        conn.close()

        init_db()

        conn = sqlite3.connect(str(isolated_db))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()

        assert version == 2

    def test_migration_skipped_if_already_applied(self, isolated_db):
        """Migrations are skipped if user_version >= migration version."""
        init_db()

        conn = sqlite3.connect(str(isolated_db))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()

        assert version == 2

    def test_migrations_registry_has_v2(self):
        """Migration registry contains v2."""
        assert 2 in MIGRATIONS
        assert sorted(MIGRATIONS.keys()) == [2]


class TestMigrateToV2:
    """Test project_id normalization on case-insensitive filesystems."""

    @pytest.mark.skipif(
        sys.platform not in ("darwin", "win32"),
        reason="Normalization only on macOS/Windows",
    )
    def test_normalizes_mixed_case_on_darwin(self, isolated_db, monkeypatch):
        """Project IDs are lowercased on macOS."""
        monkeypatch.setattr(sys, "platform", "darwin")

        conn = sqlite3.connect(str(isolated_db))
        conn.execute(
            "INSERT INTO events (session_id, project_id, source_tool, event_type, tool_name, payload, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "sess1",
                "/Users/Developer/ProjectA/file.py",
                "claude",
                "tool_use",
                "Read",
                "{}",
                1,
            ),
        )
        conn.execute(
            "INSERT INTO events (session_id, project_id, source_tool, event_type, tool_name, payload, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "sess2",
                "/Users/Developer/ProjectB/file.py",
                "claude",
                "tool_use",
                "Write",
                "{}",
                2,
            ),
        )
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(isolated_db))
        conn.row_factory = sqlite3.Row
        migrate_to_v2(conn)

        rows = [
            r["project_id"]
            for r in conn.execute("SELECT project_id FROM events").fetchall()
        ]
        conn.close()

        assert all(r == r.lower() for r in rows)
        assert "/users/developer/projecta" in rows[0]

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific test")
    def test_normalizes_mixed_case_on_windows(self, isolated_db, monkeypatch):
        """Project IDs are lowercased on Windows."""
        monkeypatch.setattr(sys, "platform", "win32")

        conn = sqlite3.connect(str(isolated_db))
        conn.execute(
            "INSERT INTO events (session_id, project_id, source_tool, event_type, tool_name, payload, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "sess1",
                "/Users/Developer/ProjectA/file.py",
                "claude",
                "tool_use",
                "Read",
                "{}",
                1,
            ),
        )
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(isolated_db))
        conn.row_factory = sqlite3.Row
        migrate_to_v2(conn)

        rows = [
            r["project_id"]
            for r in conn.execute("SELECT project_id FROM events").fetchall()
        ]
        conn.close()

        assert all(r == r.lower() for r in rows)

    def test_skips_normalization_on_linux(self, isolated_db, monkeypatch):
        """Project IDs are NOT lowercased on Linux (case-sensitive FS)."""
        monkeypatch.setattr(sys, "platform", "linux")

        original_path = "/Users/Developer/ProjectA/file.py"
        conn = sqlite3.connect(str(isolated_db))
        conn.execute(
            "INSERT INTO events (session_id, project_id, source_tool, event_type, tool_name, payload, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("sess1", original_path, "claude", "tool_use", "Read", "{}", 1),
        )
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(isolated_db))
        conn.row_factory = sqlite3.Row
        affected = migrate_to_v2(conn)
        conn.close()

        assert affected == 0

        conn = sqlite3.connect(str(isolated_db))
        path = conn.execute("SELECT project_id FROM events").fetchone()[0]
        conn.close()
        assert path == original_path

    def test_normalizes_all_tables(self, isolated_db, monkeypatch):
        """All tables with project_id/project_tag are normalized."""
        monkeypatch.setattr(sys, "platform", "darwin")

        conn = sqlite3.connect(str(isolated_db))
        conn.execute(
            "INSERT INTO traces (session_id, project_tag, type, content) VALUES (?, ?, ?, ?)",
            ("sess1", "/Users/Developer/TraceA/file.py", "success", "test"),
        )
        conn.execute(
            "INSERT INTO principles (project_tag, type, principle) VALUES (?, ?, ?)",
            ("/Users/Developer/PrincipleA/file.py", "success", "test"),
        )
        conn.execute(
            "INSERT INTO session_summaries (session_id, project_id, source_tool, request) VALUES (?, ?, ?, ?)",
            ("sess1", "/Users/Developer/SummaryA/file.py", "claude", "test"),
        )
        conn.execute(
            "INSERT INTO error_events (session_id, fingerprint, error_keywords) VALUES (?, ?, ?)",
            ("sess1", "fp1", "test"),
        )
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(isolated_db))
        conn.row_factory = sqlite3.Row
        affected = migrate_to_v2(conn)
        conn.commit()
        conn.close()

        assert affected > 0

        conn = sqlite3.connect(str(isolated_db))
        conn.row_factory = sqlite3.Row
        trace_path = conn.execute("SELECT project_tag FROM traces").fetchone()[0]
        principle_path = conn.execute("SELECT project_tag FROM principles").fetchone()[
            0
        ]
        summary_path = conn.execute(
            "SELECT project_id FROM session_summaries"
        ).fetchone()[0]
        conn.close()

        assert trace_path == trace_path.lower(), f"traces: {trace_path}"
        assert principle_path == principle_path.lower(), f"principles: {principle_path}"
        assert summary_path == summary_path.lower(), (
            f"session_summaries: {summary_path}"
        )

    def test_already_normalized_paths_unchanged(self, isolated_db, monkeypatch):
        """Paths that are already lowercase are not modified."""
        monkeypatch.setattr(sys, "platform", "darwin")

        conn = sqlite3.connect(str(isolated_db))
        conn.execute(
            "INSERT INTO events (session_id, project_id, source_tool, event_type, tool_name, payload, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "sess1",
                "/users/developer/projecta/file.py",
                "claude",
                "tool_use",
                "Read",
                "{}",
                1,
            ),
        )
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(isolated_db))
        conn.row_factory = sqlite3.Row
        affected = migrate_to_v2(conn)
        path = conn.execute("SELECT project_id FROM events").fetchone()[0]
        conn.close()

        assert affected == 0
        assert path == "/users/developer/projecta/file.py"


class TestMigrationIntegration:
    """Integration tests for full migration flow."""

    @pytest.mark.skipif(
        sys.platform not in ("darwin", "win32"),
        reason="Migration flow test for macOS/Windows",
    )
    def test_full_migration_flow(self, isolated_db, monkeypatch):
        """Test complete migration from v0 to v2."""
        monkeypatch.setattr(sys, "platform", "darwin")

        conn = sqlite3.connect(str(isolated_db))
        conn.execute("PRAGMA user_version = 0")
        conn.execute(
            "INSERT INTO events (session_id, project_id, source_tool, event_type, tool_name, payload, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "sess1",
                "/Users/Developer/ProjectA/file.py",
                "claude",
                "tool_use",
                "Read",
                "{}",
                1,
            ),
        )
        conn.commit()
        conn.close()

        init_db()

        conn = sqlite3.connect(str(isolated_db))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        path = conn.execute("SELECT project_id FROM events").fetchone()[0]
        conn.close()

        assert version == 2
        assert path.endswith("users/developer/projecta/file.py") or path.endswith(
            "users\\developer\\projecta\\file.py"
        )

    def test_migration_idempotent(self, isolated_db):
        """Running migrations twice is safe."""
        init_db()

        conn = sqlite3.connect(str(isolated_db))
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()

        init_db()

        conn = sqlite3.connect(str(isolated_db))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()

        assert version == 2
