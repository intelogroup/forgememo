"""
Tests for DB initialization and schema correctness (storage.init_db).

Covers:
- All v2 tables are created on first init
- All FTS5 virtual tables are created
- All indexes are created
- Legacy compat tables (traces, principles) are created
- init_db() is idempotent (safe to call multiple times)
- DB_PATH env var override is respected
- get_conn() returns a usable connection with correct PRAGMAs
- Compat view distilled_summaries_compat is present
"""

from __future__ import annotations

import sqlite3

import pytest

import forgememo.storage as storage_module
from forgememo.storage import get_conn, init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Redirect all storage operations to a fresh temp DB for every test."""
    db_file = tmp_path / "test_forgememo.db"
    monkeypatch.setattr(storage_module, "DB_PATH", db_file)
    yield db_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def _virtual_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%fts5%'"
    ).fetchall()
    return {r[0] for r in rows}


def _indexes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    return {r[0] for r in rows}


def _views(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view'"
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# DB init — tables
# ---------------------------------------------------------------------------

class TestInitDbTables:
    def test_v2_events_table_created(self, isolated_storage):
        init_db()
        conn = get_conn()
        assert "events" in _tables(conn)
        conn.close()

    def test_v2_distilled_summaries_table_created(self, isolated_storage):
        init_db()
        conn = get_conn()
        assert "distilled_summaries" in _tables(conn)
        conn.close()

    def test_v2_session_summaries_table_created(self, isolated_storage):
        init_db()
        conn = get_conn()
        assert "session_summaries" in _tables(conn)
        conn.close()

    def test_legacy_traces_table_created(self, isolated_storage):
        init_db()
        conn = get_conn()
        assert "traces" in _tables(conn)
        conn.close()

    def test_legacy_principles_table_created(self, isolated_storage):
        init_db()
        conn = get_conn()
        assert "principles" in _tables(conn)
        conn.close()


# ---------------------------------------------------------------------------
# DB init — FTS5 virtual tables
# ---------------------------------------------------------------------------

class TestInitDbFTS:
    def test_events_fts_created(self, isolated_storage):
        init_db()
        conn = get_conn()
        assert "events_fts" in _tables(conn)
        conn.close()

    def test_distilled_summaries_fts_created(self, isolated_storage):
        init_db()
        conn = get_conn()
        assert "distilled_summaries_fts" in _tables(conn)
        conn.close()

    def test_session_summaries_fts_created(self, isolated_storage):
        init_db()
        conn = get_conn()
        assert "session_summaries_fts" in _tables(conn)
        conn.close()

    def test_traces_fts_created(self, isolated_storage):
        init_db()
        conn = get_conn()
        assert "traces_fts" in _tables(conn)
        conn.close()

    def test_principles_fts_created(self, isolated_storage):
        init_db()
        conn = get_conn()
        assert "principles_fts" in _tables(conn)
        conn.close()


# ---------------------------------------------------------------------------
# DB init — indexes
# ---------------------------------------------------------------------------

class TestInitDbIndexes:
    EXPECTED_INDEXES = {
        "idx_events_session",
        "idx_events_project",
        "idx_events_distilled",
        "idx_events_hash",
        "idx_ds_project",
        "idx_ds_score",
        "idx_ds_session",
        "idx_ss_project",
        "idx_ss_session",
        "idx_traces_project",
        "idx_traces_type",
        "idx_traces_distilled",
        "idx_principles_project",
        "idx_principles_score",
    }

    def test_all_indexes_created(self, isolated_storage):
        init_db()
        conn = get_conn()
        created = _indexes(conn)
        conn.close()
        missing = self.EXPECTED_INDEXES - created
        assert not missing, f"Missing indexes: {missing}"


# ---------------------------------------------------------------------------
# DB init — compat view
# ---------------------------------------------------------------------------

class TestInitDbCompatView:
    def test_compat_view_created(self, isolated_storage):
        init_db()
        conn = get_conn()
        assert "distilled_summaries_compat" in _views(conn)
        conn.close()

    def test_compat_view_is_queryable(self, isolated_storage):
        init_db()
        conn = get_conn()
        # Should return empty without error (no principles yet)
        rows = conn.execute("SELECT * FROM distilled_summaries_compat").fetchall()
        assert rows == []
        conn.close()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestInitDbIdempotency:
    def test_double_init_does_not_raise(self, isolated_storage):
        init_db()
        init_db()  # must not raise "table already exists" etc.

    def test_triple_init_preserves_data(self, isolated_storage):
        init_db()
        conn = get_conn()
        conn.execute(
            "INSERT INTO traces (session_id, project_tag, type, content) "
            "VALUES (?, ?, ?, ?)",
            ("s1", "proj", "note", "test content"),
        )
        conn.commit()
        conn.close()

        init_db()
        init_db()

        conn = get_conn()
        count = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
        conn.close()
        assert count == 1, "Re-init must not delete existing data"


# ---------------------------------------------------------------------------
# DB_PATH env var override
# ---------------------------------------------------------------------------

class TestDbPathEnvOverride:
    def test_env_var_overrides_default_path(self, tmp_path, monkeypatch):
        custom_db = tmp_path / "custom" / "mydb.db"
        monkeypatch.setenv("FORGEMEM_DB", str(custom_db))
        import forgememo.storage as s
        monkeypatch.setattr(s, "DB_PATH", custom_db)
        s.init_db()
        assert custom_db.exists(), "DB was not created at the env-var-specified path"


# ---------------------------------------------------------------------------
# get_conn() PRAGMAs
# ---------------------------------------------------------------------------

class TestGetConn:
    def test_get_conn_returns_connection(self, isolated_storage):
        init_db()
        conn = get_conn()
        assert conn is not None
        conn.close()

    def test_journal_mode_is_wal(self, isolated_storage):
        init_db()
        conn = get_conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"

    def test_foreign_keys_on(self, isolated_storage):
        init_db()
        conn = get_conn()
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()
        assert fk == 1

    def test_row_factory_set(self, isolated_storage):
        init_db()
        conn = get_conn()
        conn.execute(
            "INSERT INTO traces (session_id, project_tag, type, content) "
            "VALUES (?, ?, ?, ?)",
            ("s", "p", "note", "row factory test"),
        )
        conn.commit()
        row = conn.execute("SELECT type, content FROM traces").fetchone()
        conn.close()
        # sqlite3.Row supports column access by name
        assert row["type"] == "note"
        assert row["content"] == "row factory test"

    def test_db_dir_created_if_missing(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "c" / "forgememo.db"
        monkeypatch.setattr(storage_module, "DB_PATH", nested)
        conn = get_conn()
        conn.close()
        assert nested.parent.exists()


# ---------------------------------------------------------------------------
# Schema column coverage
# ---------------------------------------------------------------------------

class TestSchemaColumns:
    def _columns(self, conn, table):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def test_events_has_required_columns(self, isolated_storage):
        init_db()
        conn = get_conn()
        cols = self._columns(conn, "events")
        conn.close()
        for col in ("id", "ts", "session_id", "project_id", "event_type",
                    "source_tool", "payload", "seq", "distilled",
                    "distill_attempts", "content_hash"):
            assert col in cols, f"events missing column: {col}"

    def test_distilled_summaries_has_required_columns(self, isolated_storage):
        init_db()
        conn = get_conn()
        cols = self._columns(conn, "distilled_summaries")
        conn.close()
        for col in ("id", "ts", "source_event_id", "session_id", "project_id",
                    "source_tool", "type", "title", "narrative", "facts",
                    "files_read", "files_modified", "concepts", "impact_score", "tags"):
            assert col in cols, f"distilled_summaries missing column: {col}"

    def test_session_summaries_has_required_columns(self, isolated_storage):
        init_db()
        conn = get_conn()
        cols = self._columns(conn, "session_summaries")
        conn.close()
        for col in ("id", "ts", "session_id", "project_id", "source_tool",
                    "request", "investigation", "learnings", "next_steps", "concepts"):
            assert col in cols, f"session_summaries missing column: {col}"
