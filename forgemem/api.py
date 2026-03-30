#!/usr/bin/env python3
"""
Forgemem HTTP API Server — Flask wrapper for Forgemem SQLite store.
Exposes REST endpoints for agents to query and save learnings.

Features:
- SQLite connection pooling (5 worker connections)
- FTS5 search across traces and principles
- Webhook registration & dispatch
- Real-time event polling
- Background retry worker for webhooks

Run: python3 forgemem_api.py (development)
Or: python3 forgemem_daemon.py (production with daemonization)
"""

import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue

try:
    from flask import Flask, request, jsonify
except ImportError:
    print("ERROR: pip install flask required for forgemem_api.py", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERROR: pip install requests required for forgemem_api.py", file=sys.stderr)
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DB_PATH = Path(os.environ.get("FORGEMEM_DB", Path.home() / "Developer" / "Forgemem" / "forgemem_memory.db"))
POOL_SIZE = 5
WEBHOOK_RETRY_INTERVAL = 30  # seconds

VALID_TYPES = {"success", "failure", "plan", "note"}


# ─────────────────────────────────────────────────────────────────────────────
# Database Connection Pool
# ─────────────────────────────────────────────────────────────────────────────

class DBPool:
    """Thread-safe connection pool using Queue."""
    
    def __init__(self, db_path, pool_size=5):
        self.db_path = db_path
        self.queue = Queue(maxsize=pool_size)
        for _ in range(pool_size):
            conn = sqlite3.connect(str(db_path), timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self.queue.put(conn)
    
    def get_conn(self):
        """Acquire a connection from the pool (5s timeout to prevent deadlock)."""
        try:
            return self.queue.get(timeout=5)
        except Exception:
            raise RuntimeError("DB pool exhausted — all connections busy")
    
    def return_conn(self, conn):
        """Return a connection to the pool."""
        self.queue.put(conn)
    
    def close_all(self):
        """Close all connections in the pool."""
        while not self.queue.empty():
            try:
                conn = self.queue.get_nowait()
                conn.close()
            except Exception:
                pass


# Initialize global pool and write serialization lock
pool = None
_write_lock = threading.Lock()


def init_pool():
    """Initialize the connection pool on startup."""
    global pool
    pool = DBPool(DB_PATH, pool_size=POOL_SIZE)


def get_db(write=False):
    """Context manager for acquiring a connection.

    Pass write=True for any INSERT/UPDATE/DELETE to serialize writes and
    prevent SQLite lock contention on concurrent requests.
    """
    class DBContext:
        def __enter__(self):
            if pool is None:
                raise RuntimeError("Pool not initialized. Call init_pool() first.")
            if write:
                _write_lock.acquire()
            self.conn = pool.get_conn()
            return self.conn

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is not None:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
            if pool is not None:
                pool.return_conn(self.conn)
            if write:
                _write_lock.release()

    return DBContext()


# ─────────────────────────────────────────────────────────────────────────────
# Request Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_query(q, max_chars=500):
    """Validate search query."""
    if not q or not isinstance(q, str):
        return None, "query required"
    if len(q) > max_chars:
        return None, f"query exceeds {max_chars} characters"
    return q.strip(), None


def validate_trace_type(trace_type):
    """Validate trace type."""
    if trace_type not in VALID_TYPES:
        return None, f"type must be one of: {', '.join(VALID_TYPES)}"
    return trace_type, None


def validate_project_tag(project):
    """Validate project tag (alphanumeric + hyphen)."""
    if not project or not isinstance(project, str):
        return None, "project tag required"
    if not all(c.isalnum() or c == '-' for c in project):
        return None, "project must be alphanumeric or hyphen"
    if len(project) > 100:
        return None, "project tag too long"
    return project.strip(), None


def validate_trace_content(content, max_chars=50000):
    """Validate trace content."""
    if not content or not isinstance(content, str):
        return None, "content required"
    if len(content) > max_chars:
        return None, f"content exceeds {max_chars} characters"
    return content.strip(), None


def validate_webhook_url(url):
    """Validate webhook URL."""
    if not url or not isinstance(url, str):
        return None, "url required"
    if not url.startswith(("http://", "https://")):
        return None, "url must be http or https"
    if len(url) > 500:
        return None, "url too long"
    return url.strip(), None


def validate_webhook_api_key(api_key):
    """Validate webhook API key."""
    if not api_key or not isinstance(api_key, str):
        return None, "api_key required"
    if len(api_key) < 10:
        return None, "api_key too short"
    if len(api_key) > 500:
        return None, "api_key too long"
    return api_key.strip(), None


# ─────────────────────────────────────────────────────────────────────────────
# Database Schema Migrations
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_VERSION = 2

SCHEMA_V1_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA auto_vacuum=INCREMENTAL;

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
"""

SCHEMA_V2_SQL = """
CREATE TABLE IF NOT EXISTS webhooks (
  id              INTEGER PRIMARY KEY,
  url             TEXT NOT NULL UNIQUE,
  api_key         TEXT NOT NULL,
  project_filter  TEXT,
  type_filter     TEXT,
  min_impact_score INTEGER DEFAULT 0,
  active          INTEGER DEFAULT 1,
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  last_fired      DATETIME,
  failure_count   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS webhook_queue (
  id              INTEGER PRIMARY KEY,
  trace_id        INTEGER NOT NULL REFERENCES traces(id),
  webhook_id      INTEGER NOT NULL REFERENCES webhooks(id),
  status          TEXT CHECK(status IN ('pending', 'delivered', 'failed')),
  retry_count     INTEGER DEFAULT 0,
  next_retry_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  error_message   TEXT,
  payload         TEXT
);

CREATE INDEX IF NOT EXISTS idx_webhook_queue_status ON webhook_queue(status);
CREATE INDEX IF NOT EXISTS idx_webhook_queue_retry ON webhook_queue(next_retry_at);
"""


def init_db():
    """Initialize database schema (idempotent)."""
    with get_db(write=True) as conn:
        # Check schema version
        cur = conn.execute("PRAGMA user_version")
        current_version = cur.fetchone()[0]
        
        # Apply migrations
        if current_version < 1:
            conn.executescript(SCHEMA_V1_SQL)
            conn.execute("PRAGMA user_version = 1")

        if current_version < 2:
            conn.executescript(SCHEMA_V2_SQL)
            conn.execute("PRAGMA user_version = 2")
        
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Flask App Factory
# ─────────────────────────────────────────────────────────────────────────────

def create_app():
    """Create and configure Flask app."""
    app = Flask(__name__)
    app.config['JSON_SORT_KEYS'] = False
    
    # ─────────────────────────────────────────────────────────────────────────
    # Error Handlers
    # ─────────────────────────────────────────────────────────────────────────
    
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({
            "error": "bad_request",
            "message": str(e.description),
            "status": 400
        }), 400
    
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({
            "error": "not_found",
            "message": "Endpoint not found",
            "status": 404
        }), 404
    
    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({
            "error": "internal_error",
            "message": "Internal server error",
            "status": 500
        }), 500
    
    # ─────────────────────────────────────────────────────────────────────────
    # Health Check
    # ─────────────────────────────────────────────────────────────────────────
    
    @app.route('/health', methods=['GET'])
    def health():
        """Health check endpoint."""
        try:
            with get_db() as conn:
                conn.execute("SELECT 1")
            return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()}), 200
        except Exception as e:
            return jsonify({
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }), 500
    
    # ─────────────────────────────────────────────────────────────────────────
    # Search Endpoint
    # ─────────────────────────────────────────────────────────────────────────
    
    @app.route('/search', methods=['GET'])
    def search():
        """
        FTS5 search across traces and principles.
        
        Query params:
          q (required): search query
          k: limit (default 5, max 100)
          project: filter by project tag
          type: filter by trace type
        """
        q = request.args.get('q', '').strip()
        if not q:
            return jsonify({
                "error": "invalid_request",
                "message": "query parameter 'q' required",
                "status": 400
            }), 400
        
        q_clean, err = validate_query(q)
        if err:
            return jsonify({
                "error": "invalid_query",
                "message": err,
                "status": 400
            }), 400
        
        k = request.args.get('k', 5, type=int)
        k = max(1, min(k, 100))
        
        project = request.args.get('project', '').strip() or None
        trace_type = request.args.get('type', '').strip() or None
        
        try:
            with get_db() as conn:
                # Search traces
                trace_query = "SELECT id, ts, project_tag, type, content FROM traces WHERE id IN (SELECT rowid FROM traces_fts WHERE traces_fts MATCH ?) "
                trace_params: list = [q_clean]
                
                if project:
                    trace_query += "AND project_tag = ? "
                    trace_params.append(project)
                
                if trace_type:
                    _, err = validate_trace_type(trace_type)
                    if err:
                        return jsonify({
                            "error": "invalid_type",
                            "message": err,
                            "status": 400
                        }), 400
                    trace_query += "AND type = ? "
                    trace_params.append(trace_type)
                
                trace_query += "ORDER BY ts DESC LIMIT ?"
                trace_params.append(k)
                
                traces = conn.execute(trace_query, trace_params).fetchall()
                traces = [dict(t) for t in traces]
                
                # Search principles
                principle_query = "SELECT id, ts, project_tag, type, principle, impact_score, tags FROM principles WHERE id IN (SELECT rowid FROM principles_fts WHERE principles_fts MATCH ?) "
                principle_params: list = [q_clean]
                
                if project:
                    principle_query += "AND project_tag = ? "
                    principle_params.append(project)
                
                if trace_type:
                    principle_query += "AND type = ? "
                    principle_params.append(trace_type)
                
                principle_query += "ORDER BY impact_score DESC LIMIT ?"
                principle_params.append(k)
                
                principles = conn.execute(principle_query, principle_params).fetchall()
                principles = [dict(p) for p in principles]
            
            return jsonify({
                "query": q,
                "results": {
                    "traces": traces,
                    "principles": principles
                },
                "count": {
                    "traces": len(traces),
                    "principles": len(principles)
                }
            }), 200
        
        except Exception as e:
            return jsonify({
                "error": "search_error",
                "message": str(e),
                "status": 500
            }), 500
    
    # ─────────────────────────────────────────────────────────────────────────
    # Save Trace Endpoint
    # ─────────────────────────────────────────────────────────────────────────
    
    @app.route('/traces', methods=['POST'])
    def save_trace():
        """
        Save a new trace (learning).
        
        Body (JSON):
          type (required): success|failure|plan|note
          content (required): text content
          project (required): project tag
          session: session identifier
          principle: optional principle text
          score: impact score (0-10, default 5)
          tags: comma-separated tags
        """
        try:
            data = request.get_json() or {}
        except Exception:
            return jsonify({
                "error": "invalid_json",
                "message": "Invalid JSON body",
                "status": 400
            }), 400
        
        # Validate inputs
        trace_type = data.get('type', '').strip()
        if not trace_type:
            return jsonify({
                "error": "missing_type",
                "message": "type required (success, failure, plan, note)",
                "status": 400
            }), 400
        
        trace_type, err = validate_trace_type(trace_type)
        if err:
            return jsonify({
                "error": "invalid_type",
                "message": err,
                "status": 400
            }), 400
        
        content = data.get('content', '').strip()
        content, err = validate_trace_content(content)
        if err:
            return jsonify({
                "error": "invalid_content",
                "message": err,
                "status": 400
            }), 400
        
        project = data.get('project', '').strip()
        project, err = validate_project_tag(project)
        if err:
            return jsonify({
                "error": "invalid_project",
                "message": err,
                "status": 400
            }), 400
        
        session = data.get('session', '').strip() or None
        principle_text = data.get('principle', '').strip() or None
        score = data.get('score', 5)
        tags = data.get('tags', '').strip() or None
        
        try:
            score = max(0, min(int(score), 10))
        except (TypeError, ValueError):
            score = 5
        
        try:
            with get_db(write=True) as conn:
                # Insert trace
                cur = conn.execute(
                    "INSERT INTO traces (session_id, project_tag, type, content) VALUES (?, ?, ?, ?)",
                    (session, project, trace_type, content)
                )
                trace_id = cur.lastrowid
                
                # Insert into FTS index
                conn.execute(
                    "INSERT INTO traces_fts(rowid, content, project_tag, type) VALUES (?, ?, ?, ?)",
                    (trace_id, content, project, trace_type)
                )
                
                principle_id = None
                
                # Insert principle if provided
                if principle_text:
                    cur = conn.execute(
                        "INSERT INTO principles (source_trace_id, project_tag, type, principle, impact_score, tags) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (trace_id, project, trace_type, principle_text, score, tags)
                    )
                    principle_id = cur.lastrowid
                    
                    # Insert into FTS index
                    conn.execute(
                        "INSERT INTO principles_fts(rowid, principle, project_tag, tags) VALUES (?, ?, ?, ?)",
                        (principle_id, principle_text, project, tags or "")
                    )
                    
                    # Mark trace as distilled
                    conn.execute("UPDATE traces SET distilled=1 WHERE id=?", (trace_id,))
                
                conn.commit()
                
                # Trigger webhooks asynchronously
                trigger_webhooks_async(trace_id, project, trace_type, score if principle_id else 0)
            
            return jsonify({
                "trace_id": trace_id,
                "principle_id": principle_id,
                "message": f"Saved trace #{trace_id}" + (f" + principle #{principle_id}" if principle_id else ""),
                "status": 201
            }), 201
        
        except Exception as e:
            return jsonify({
                "error": "save_error",
                "message": str(e),
                "status": 500
            }), 500
    
    # ─────────────────────────────────────────────────────────────────────────
    # List Principles Endpoint
    # ─────────────────────────────────────────────────────────────────────────
    
    @app.route('/principles', methods=['GET'])
    def list_principles():
        """
        List principles, sorted by impact_score DESC.
        
        Query params:
          project: filter by project tag
          type: filter by trace type
          limit: max results (default 10, max 100)
        """
        project = request.args.get('project', '').strip() or None
        trace_type = request.args.get('type', '').strip() or None
        limit = request.args.get('limit', 10, type=int)
        limit = max(1, min(limit, 100))
        
        if trace_type:
            _, err = validate_trace_type(trace_type)
            if err:
                return jsonify({
                    "error": "invalid_type",
                    "message": err,
                    "status": 400
                }), 400
        
        try:
            with get_db() as conn:
                query = "SELECT id, ts, project_tag, type, principle, impact_score, tags FROM principles "
                params = []
                
                where_clauses = []
                if project:
                    where_clauses.append("project_tag = ?")
                    params.append(project)
                if trace_type:
                    where_clauses.append("type = ?")
                    params.append(trace_type)
                
                if where_clauses:
                    query += "WHERE " + " AND ".join(where_clauses) + " "
                
                query += "ORDER BY impact_score DESC, ts DESC LIMIT ?"
                params.append(limit)
                
                principles = conn.execute(query, params).fetchall()
                principles = [dict(p) for p in principles]
            
            return jsonify({
                "principles": principles,
                "count": len(principles)
            }), 200
        
        except Exception as e:
            return jsonify({
                "error": "list_error",
                "message": str(e),
                "status": 500
            }), 500
    
    # ─────────────────────────────────────────────────────────────────────────
    # Statistics Endpoint
    # ─────────────────────────────────────────────────────────────────────────
    
    @app.route('/stats', methods=['GET'])
    def stats():
        """Get database statistics."""
        try:
            with get_db() as conn:
                trace_count = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
                principle_count = conn.execute("SELECT COUNT(*) FROM principles").fetchone()[0]
                
                by_type = conn.execute(
                    "SELECT type, COUNT(*) as count FROM traces GROUP BY type ORDER BY count DESC"
                ).fetchall()
                by_type = {row['type']: row['count'] for row in by_type}
                
                by_project = conn.execute(
                    "SELECT project_tag, COUNT(*) as count FROM traces GROUP BY project_tag ORDER BY count DESC LIMIT 10"
                ).fetchall()
                by_project = {row['project_tag']: row['count'] for row in by_project}
                
                top_principles = conn.execute(
                    "SELECT id, principle, impact_score FROM principles ORDER BY impact_score DESC LIMIT 5"
                ).fetchall()
                top_principles = [dict(p) for p in top_principles]
            
            return jsonify({
                "trace_count": trace_count,
                "principle_count": principle_count,
                "by_type": by_type,
                "by_project": by_project,
                "top_principles": top_principles
            }), 200
        
        except Exception as e:
            return jsonify({
                "error": "stats_error",
                "message": str(e),
                "status": 500
            }), 500
    
    # ─────────────────────────────────────────────────────────────────────────
    # Events Polling Endpoint
    # ─────────────────────────────────────────────────────────────────────────
    
    @app.route('/events', methods=['GET'])
    def get_events():
        """
        Polling-based real-time events.
        Returns traces created after the given timestamp.
        
        Query params:
          since: ISO timestamp (required)
        """
        since = request.args.get('since', '').strip()
        if not since:
            return jsonify({
                "error": "missing_since",
                "message": "since parameter required (ISO timestamp)",
                "status": 400
            }), 400
        
        try:
            # Validate ISO timestamp format
            datetime.fromisoformat(since.replace('Z', '+00:00'))
        except ValueError:
            return jsonify({
                "error": "invalid_since",
                "message": "since must be ISO 8601 timestamp",
                "status": 400
            }), 400
        
        try:
            with get_db() as conn:
                traces = conn.execute(
                    "SELECT id, ts, project_tag, type, content FROM traces WHERE ts > ? ORDER BY ts DESC LIMIT 100",
                    (since,)
                ).fetchall()
                traces = [dict(t) for t in traces]
                
                events = []
                for trace in traces:
                    # Get associated principle if exists
                    principle = conn.execute(
                        "SELECT id, principle, impact_score, tags FROM principles WHERE source_trace_id = ?",
                        (trace['id'],)
                    ).fetchone()
                    
                    event = {
                        "id": trace['id'],
                        "type": "trace_saved",
                        "trace": trace,
                        "principle": dict(principle) if principle else None,
                        "timestamp": trace['ts']
                    }
                    events.append(event)
            
            next_poll = datetime.utcnow() + timedelta(seconds=2)
            
            return jsonify({
                "events": events,
                "count": len(events),
                "next_poll_after": next_poll.isoformat() + "Z"
            }), 200
        
        except Exception as e:
            return jsonify({
                "error": "events_error",
                "message": str(e),
                "status": 500
            }), 500
    
    # ─────────────────────────────────────────────────────────────────────────
    # Webhook Registration Endpoint
    # ─────────────────────────────────────────────────────────────────────────
    
    @app.route('/webhooks/register', methods=['POST'])
    def register_webhook():
        """
        Register a webhook for trace events.
        
        Body (JSON):
          url (required): webhook URL
          api_key (required): API key for webhook authentication
          project_filter: comma-separated project tags (null = all)
          type_filter: comma-separated trace types (null = all)
          min_impact_score: minimum impact score to trigger (default 0)
        """
        try:
            data = request.get_json() or {}
        except Exception:
            return jsonify({
                "error": "invalid_json",
                "message": "Invalid JSON body",
                "status": 400
            }), 400
        
        url = data.get('url', '').strip()
        url, err = validate_webhook_url(url)
        if err:
            return jsonify({
                "error": "invalid_url",
                "message": err,
                "status": 400
            }), 400
        
        api_key = data.get('api_key', '').strip()
        api_key, err = validate_webhook_api_key(api_key)
        if err:
            return jsonify({
                "error": "invalid_api_key",
                "message": err,
                "status": 400
            }), 400
        
        project_filter = data.get('project_filter', '').strip() or None
        type_filter = data.get('type_filter', '').strip() or None
        min_impact_score = data.get('min_impact_score', 0)
        
        try:
            min_impact_score = max(0, min(int(min_impact_score), 10))
        except (TypeError, ValueError):
            min_impact_score = 0
        
        try:
            with get_db(write=True) as conn:
                cur = conn.execute(
                    "INSERT INTO webhooks (url, api_key, project_filter, type_filter, min_impact_score) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (url, api_key, project_filter, type_filter, min_impact_score)
                )
                webhook_id = cur.lastrowid
                conn.commit()
            
            return jsonify({
                "webhook_id": webhook_id,
                "url": url,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "status": 201
            }), 201
        
        except sqlite3.IntegrityError:
            return jsonify({
                "error": "webhook_exists",
                "message": "Webhook URL already registered",
                "status": 409
            }), 409
        
        except Exception as e:
            return jsonify({
                "error": "webhook_error",
                "message": str(e),
                "status": 500
            }), 500
    
    return app


# ─────────────────────────────────────────────────────────────────────────────
# Webhook Dispatch System
# ─────────────────────────────────────────────────────────────────────────────

def matches_webhook_filter(webhook, project, trace_type, impact_score):
    """Check if trace matches webhook filters."""
    # Project filter
    if webhook['project_filter']:
        projects = [p.strip() for p in webhook['project_filter'].split(',')]
        if project not in projects:
            return False
    
    # Type filter
    if webhook['type_filter']:
        types = [t.strip() for t in webhook['type_filter'].split(',')]
        if trace_type not in types:
            return False
    
    # Impact score filter
    if impact_score < webhook['min_impact_score']:
        return False
    
    return True


def trigger_webhooks_async(trace_id, project, trace_type, impact_score):
    """Fire webhook tasks in background thread (non-blocking)."""
    threading.Thread(
        target=dispatch_webhooks,
        args=(trace_id, project, trace_type, impact_score),
        daemon=True
    ).start()


def dispatch_webhooks(trace_id, project, trace_type, impact_score):
    """Enqueue webhooks matching filters."""
    try:
        with get_db() as conn:
            webhooks = conn.execute(
                "SELECT id, url, api_key, project_filter, type_filter, min_impact_score "
                "FROM webhooks WHERE active=1"
            ).fetchall()
            
            # Get trace for payload
            trace = conn.execute(
                "SELECT id, type, project_tag, content FROM traces WHERE id = ?",
                (trace_id,)
            ).fetchone()
            
            if not trace:
                return
            
            for webhook in webhooks:
                if matches_webhook_filter(webhook, project, trace_type, impact_score):
                    enqueue_webhook_delivery(trace_id, webhook['id'], trace)
    
    except Exception as e:
        print(f"ERROR: Failed to dispatch webhooks: {e}")


def enqueue_webhook_delivery(trace_id, webhook_id, trace):
    """Enqueue a webhook for delivery."""
    try:
        with get_db(write=True) as conn:
            payload = json.dumps({
                "event": "trace_saved",
                "trace_id": trace['id'],
                "type": trace['type'],
                "project": trace['project_tag'],
                "content": trace['content'],
                "timestamp": datetime.utcnow().isoformat() + "Z"
            })
            
            conn.execute(
                "INSERT INTO webhook_queue (trace_id, webhook_id, status, payload) "
                "VALUES (?, ?, ?, ?)",
                (trace_id, webhook_id, 'pending', payload)
            )
            conn.commit()
    
    except Exception as e:
        print(f"ERROR: Failed to enqueue webhook delivery: {e}")


def webhook_retry_worker():
    """Background worker: retries pending webhooks with exponential backoff."""
    while True:
        try:
            time.sleep(WEBHOOK_RETRY_INTERVAL)
            
            with get_db(write=True) as conn:
                pending = conn.execute(
                    "SELECT id, webhook_id, trace_id, retry_count, payload "
                    "FROM webhook_queue "
                    "WHERE status='pending' AND next_retry_at <= datetime('now')"
                ).fetchall()
                
                for queue_entry in pending:
                    attempt_webhook_delivery(queue_entry, conn)
        
        except Exception as e:
            print(f"ERROR: Webhook retry worker failed: {e}")


def attempt_webhook_delivery(queue_entry, conn=None):
    """Attempt to deliver a webhook."""
    if conn is None:
        with get_db(write=True) as conn_inner:
            _attempt_webhook_delivery_impl(queue_entry, conn_inner)
    else:
        _attempt_webhook_delivery_impl(queue_entry, conn)


def _attempt_webhook_delivery_impl(queue_entry, conn):
    """Implementation of webhook delivery."""
    queue_id = queue_entry['id']
    webhook_id = queue_entry['webhook_id']
    retry_count = queue_entry['retry_count']
    
    # Get webhook details
    webhook = conn.execute(
        "SELECT url, api_key FROM webhooks WHERE id = ?",
        (webhook_id,)
    ).fetchone()
    
    if not webhook:
        conn.execute("UPDATE webhook_queue SET status='failed', error_message='Webhook not found' WHERE id=?", (queue_id,))
        conn.commit()
        return
    
    try:
        payload = json.loads(queue_entry['payload'])
        
        response = requests.post(
            webhook['url'],
            json=payload,
            headers={"Authorization": f"Bearer {webhook['api_key']}"},
            timeout=10
        )
        
        if response.status_code in (200, 201, 204):
            # Success
            conn.execute(
                "UPDATE webhook_queue SET status='delivered' WHERE id=?",
                (queue_id,)
            )
            conn.execute(
                "UPDATE webhooks SET last_fired=datetime('now'), failure_count=0 WHERE id=?",
                (webhook_id,)
            )
            conn.commit()
        else:
            # Retry
            if retry_count < 5:
                retry_delay = [300, 1800, 7200, 43200, 86400][retry_count]
                next_retry = datetime.utcnow() + timedelta(seconds=retry_delay)
                conn.execute(
                    "UPDATE webhook_queue SET retry_count=retry_count+1, next_retry_at=?, error_message=? WHERE id=?",
                    (next_retry.isoformat(), f"HTTP {response.status_code}", queue_id)
                )
                conn.commit()
            else:
                # Max retries exceeded
                conn.execute(
                    "UPDATE webhook_queue SET status='failed', error_message='Max retries exceeded' WHERE id=?",
                    (queue_id,)
                )
                conn.execute(
                    "UPDATE webhooks SET failure_count=failure_count+1 WHERE id=?",
                    (webhook_id,)
                )
                conn.commit()
    
    except requests.RequestException as e:
        # Network error - retry
        if retry_count < 5:
            retry_delay = [300, 1800, 7200, 43200, 86400][retry_count]
            next_retry = datetime.utcnow() + timedelta(seconds=retry_delay)
            conn.execute(
                "UPDATE webhook_queue SET retry_count=retry_count+1, next_retry_at=?, error_message=? WHERE id=?",
                (next_retry.isoformat(), str(e)[:200], queue_id)
            )
            conn.commit()
        else:
            conn.execute(
                "UPDATE webhook_queue SET status='failed', error_message=? WHERE id=?",
                (str(e)[:200], queue_id)
            )
            conn.commit()
    
    except Exception as e:
        conn.execute(
            "UPDATE webhook_queue SET status='failed', error_message=? WHERE id=?",
            (str(e)[:200], queue_id)
        )
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """Initialize and run API server."""
    init_pool()
    init_db()
    
    # Start webhook retry worker
    threading.Thread(target=webhook_retry_worker, daemon=True).start()
    
    app = create_app()
    app.run(host="127.0.0.1", port=5555, debug=False, threaded=True)


if __name__ == "__main__":
    main()
