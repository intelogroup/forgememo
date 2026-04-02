"""
Forgememo storage layer.
Defines the v2 schema (events + distilled_summaries + session_summaries) and
compatibility views for legacy traces/principles data.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(
    os.environ.get("FORGEMEM_DB", Path.home() / ".forgememo" / "forgememo_memory.db")
)

MIGRATION_LOG = logging.getLogger("forgememo.migration")


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA auto_vacuum=INCREMENTAL;

-- Legacy tables (for compatibility / migration)
CREATE TABLE IF NOT EXISTS traces (
  id          INTEGER PRIMARY KEY,
  ts          DATETIME DEFAULT CURRENT_TIMESTAMP,
  session_id  TEXT,
  project_tag TEXT,
  type        TEXT NOT NULL CHECK(type IN ('success','failure','plan','note')),
  content     TEXT NOT NULL,
  distilled   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_traces_project ON traces(project_tag);
CREATE INDEX IF NOT EXISTS idx_traces_type ON traces(type);
CREATE INDEX IF NOT EXISTS idx_traces_distilled ON traces(distilled);

CREATE VIRTUAL TABLE IF NOT EXISTS traces_fts
  USING fts5(content, project_tag, type);

CREATE TABLE IF NOT EXISTS principles (
  id              INTEGER PRIMARY KEY,
  ts              DATETIME DEFAULT CURRENT_TIMESTAMP,
  source_trace_id INTEGER REFERENCES traces(id),
  project_tag     TEXT,
  type            TEXT NOT NULL CHECK(type IN ('success','failure','plan','note')),
  principle       TEXT NOT NULL,
  impact_score    INTEGER DEFAULT 5 CHECK(impact_score BETWEEN 0 AND 10),
  tags            TEXT
);
CREATE INDEX IF NOT EXISTS idx_principles_project ON principles(project_tag);
CREATE INDEX IF NOT EXISTS idx_principles_score ON principles(impact_score DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS principles_fts
  USING fts5(principle, project_tag, tags);

-- V2 raw event capture
CREATE TABLE IF NOT EXISTS events (
  id               INTEGER PRIMARY KEY,
  ts               DATETIME DEFAULT CURRENT_TIMESTAMP,
  session_id       TEXT NOT NULL,
  project_id       TEXT NOT NULL,
  event_type       TEXT NOT NULL,
  tool_name        TEXT,
  source_tool      TEXT NOT NULL,
  payload          TEXT NOT NULL,
  seq              INTEGER NOT NULL,
  distilled        INTEGER DEFAULT 0,
  distill_attempts INTEGER DEFAULT 0,
  content_hash     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_session  ON events(session_id, source_tool);
CREATE INDEX IF NOT EXISTS idx_events_project  ON events(project_id);
CREATE INDEX IF NOT EXISTS idx_events_distilled ON events(distilled, distill_attempts);
CREATE INDEX IF NOT EXISTS idx_events_hash     ON events(content_hash);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(payload);

-- Distilled output
CREATE TABLE IF NOT EXISTS distilled_summaries (
  id              INTEGER PRIMARY KEY,
  ts              DATETIME DEFAULT CURRENT_TIMESTAMP,
  source_event_id INTEGER REFERENCES events(id),
  session_id      TEXT,
  project_id      TEXT,
  source_tool     TEXT,
  type            TEXT CHECK(type IN ('bugfix','feature','decision','refactor','discovery','note')),
  title           TEXT NOT NULL,
  narrative       TEXT,
  facts           TEXT,
  files_read      TEXT,
  files_modified  TEXT,
  concepts        TEXT,
  impact_score    INTEGER DEFAULT 5 CHECK(impact_score BETWEEN 0 AND 10),
  tags            TEXT
);
CREATE INDEX IF NOT EXISTS idx_ds_project ON distilled_summaries(project_id);
CREATE INDEX IF NOT EXISTS idx_ds_score   ON distilled_summaries(impact_score DESC);
CREATE INDEX IF NOT EXISTS idx_ds_session ON distilled_summaries(session_id);

CREATE VIRTUAL TABLE IF NOT EXISTS distilled_summaries_fts
  USING fts5(title, narrative, concepts, tags, project_id);

-- Session summaries
CREATE TABLE IF NOT EXISTS session_summaries (
  id            INTEGER PRIMARY KEY,
  ts            DATETIME DEFAULT CURRENT_TIMESTAMP,
  session_id    TEXT,
  project_id    TEXT,
  source_tool   TEXT,
  request       TEXT NOT NULL,
  investigation TEXT,
  learnings     TEXT,
  next_steps    TEXT,
  concepts      TEXT
);
CREATE INDEX IF NOT EXISTS idx_ss_project ON session_summaries(project_id);
CREATE INDEX IF NOT EXISTS idx_ss_session ON session_summaries(session_id);

CREATE VIRTUAL TABLE IF NOT EXISTS session_summaries_fts
  USING fts5(request, learnings, next_steps, concepts, project_id);

-- Error tracking for mid-session recall
CREATE TABLE IF NOT EXISTS error_events (
  id              INTEGER PRIMARY KEY,
  ts              DATETIME DEFAULT CURRENT_TIMESTAMP,
  session_id      TEXT NOT NULL,
  project_id      TEXT,
  fingerprint     TEXT NOT NULL,
  error_keywords  TEXT,
  error_text      TEXT,
  recalled_at     DATETIME
);
CREATE INDEX IF NOT EXISTS idx_error_session_fp ON error_events(session_id, fingerprint);
CREATE INDEX IF NOT EXISTS idx_error_project ON error_events(project_id);
"""


COMPAT_VIEW_SQL = """
CREATE VIEW IF NOT EXISTS distilled_summaries_compat AS
  SELECT
    p.id + 1000000 AS id,
    p.ts,
    NULL AS source_event_id,
    NULL AS session_id,
    p.project_tag AS project_id,
    'claude' AS source_tool,
    p.type AS type,
    substr(p.principle, 1, 100) AS title,
    p.principle AS narrative,
    NULL AS facts,
    NULL AS files_read,
    NULL AS files_modified,
    p.tags AS concepts,
    p.impact_score,
    p.tags AS tags
  FROM principles p;
"""


MIGRATIONS = {}


def register_migration(version: int):
    """Decorator to register a migration function for a specific schema version."""

    def decorator(func):
        MIGRATIONS[version] = func
        return func

    return decorator


@register_migration(2)
def migrate_to_v2(conn: sqlite3.Connection) -> int:
    """Normalize project_id paths on case-insensitive filesystems (macOS/Windows).

    On case-insensitive filesystems, ProjectA and projecta resolve to the same
    canonical form. This migration ensures all project_ids are stored in lowercase
    form on darwin/win32 platforms.
    """
    import sys

    affected = 0
    if sys.platform not in ("darwin", "win32"):
        MIGRATION_LOG.info(
            "Case-normalization skipped: filesystem is case-sensitive (Linux)"
        )
        return 0

    tables_with_project_id = [
        "events",
        "distilled_summaries",
        "session_summaries",
        "error_events",
        "traces",
        "principles",
    ]
    project_column_map = {
        "events": "project_id",
        "distilled_summaries": "project_id",
        "session_summaries": "project_id",
        "error_events": "project_id",
        "traces": "project_tag",
        "principles": "project_tag",
    }

    for table in tables_with_project_id:
        col = project_column_map.get(table)
        if not col:
            continue
        try:
            result = conn.execute(
                f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL AND {col} != lower({col})"
            ).fetchall()
            if result:
                MIGRATION_LOG.info(
                    "  Normalizing %d rows in %s.%s", len(result), table, col
                )
                for row in result:
                    normalized = os.path.abspath(row[col]).lower()
                    conn.execute(
                        f"UPDATE {table} SET {col}=? WHERE id=?",
                        (normalized, row["id"]),
                    )
                    affected += 1
        except sqlite3.OperationalError as e:
            MIGRATION_LOG.warning("  Skipping %s.%s: %s", table, col, e)
            continue

    return affected


def run_migrations(conn: sqlite3.Connection) -> None:
    """Run all pending migrations up to the current schema version."""
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    MIGRATION_LOG.info("Database schema version: %d (current: 2)", current_version)

    for version in sorted(MIGRATIONS.keys()):
        if version <= current_version:
            continue

        MIGRATION_LOG.info("Applying migration v%d...", version)
        migration_func = MIGRATIONS[version]
        affected = migration_func(conn)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
        MIGRATION_LOG.info(
            "Migration v%d complete: %d rows normalized", version, affected
        )


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA_SQL)
    conn.executescript(COMPAT_VIEW_SQL)
    conn.commit()
    run_migrations(conn)
    conn.close()
