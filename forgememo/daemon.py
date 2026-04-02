#!/usr/bin/env python3
"""
Forgememo Daemon — single write path + read API.

Runs a Flask API on 127.0.0.1:5555 with:
  POST /events
  GET  /search
  GET  /timeline
  GET  /observation/<prefix>/<id>
  POST /session_summaries
  GET  /session_summaries
  GET  /health
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from typing import Any

from forgememo.port import delete_port, write_port

from forgememo.storage import get_conn, init_db
from pathlib import Path

try:
    from flask import Flask, request, jsonify
except ImportError:
    print("ERROR: pip install flask required for forgememo daemon", file=sys.stderr)
    sys.exit(1)

try:
    from werkzeug.serving import make_server
except Exception:
    make_server = None


_DEFAULT_LOG_PATH = os.path.join(
    Path.home(), ".forgememo", "logs", "forgememo_daemon.log"
)
LOG_FILE = os.environ.get("FORGEMEMO_DAEMON_LOG", _DEFAULT_LOG_PATH)
SOCKET_PATH = os.environ.get(
    "FORGEMEMO_SOCKET", os.path.join(tempfile.gettempdir(), "forgememo.sock")
)
HTTP_PORT = os.environ.get("FORGEMEMO_HTTP_PORT", "5555")


def wait_for_port(
    host: str = "127.0.0.1",
    port: int = 5555,
    timeout: float = 30,
    proc: Any = None,
) -> bool:
    """Poll until port is listening. Returns True on success, False on timeout.

    If proc is provided, also checks that the subprocess is still alive.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            pass
        time.sleep(0.5)
    return False


def _canonicalize_project_id(path: str) -> str:
    """Normalize path for consistent project identification across OSes.

    On case-insensitive filesystems (macOS/Windows), ProjectA and projecta
    resolve to the same canonical form. On case-sensitive (Linux), they remain distinct.
    """
    if not path:
        return path
    abs_path = os.path.abspath(os.path.expanduser(path))
    if sys.platform == "darwin" or sys.platform == "win32":
        return abs_path.lower()
    return abs_path


try:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
except OSError:
    if os.environ.get("FORGEMEMO_ALLOW_TMP_LOG") == "1":
        # Fallback for restricted environments (e.g., test sandboxes)
        LOG_FILE = os.path.join(tempfile.gettempdir(), "forgememo_daemon.log")
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    else:
        raise
_log_handlers: list[logging.Handler] = [logging.FileHandler(LOG_FILE)]
if os.environ.get("FORGEMEMO_LOG_STDERR", "1") != "0":
    _log_handlers.append(logging.StreamHandler())
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)

migration_logger = logging.getLogger("forgememo.migration")
migration_logger.setLevel(logging.INFO)
migration_logger.propagate = False
if os.environ.get("FORGEMEMO_LOG_STDERR", "1") != "0":
    _migration_handler = logging.StreamHandler(sys.stderr)
    _migration_handler.setLevel(logging.INFO)
    _migration_handler.setFormatter(
        logging.Formatter("[%(asctime)s] MIGRATION: %(message)s")
    )
    migration_logger.addHandler(_migration_handler)

_write_lock = threading.Lock()

_ERROR_EVENTS_CIRCUIT_BREAKER_FAIL_LIMIT = 3
_ERROR_EVENTS_HALF_OPEN_INTERVAL = 60  # seconds before allowing a probe attempt
_error_events_consecutive_failures = 0
_error_events_disabled = False
_error_events_tripped_at: float | None = None
_DISABLE_BREAKER = os.environ.get("FORGEMEMO_DISABLE_BREAKER") == "1"


def _error_events_circuit_open() -> bool:
    """Check if error_events circuit breaker is open (DISABLED).

    Returns False during the half-open probe window so one request can attempt
    recovery. If the probe succeeds, _error_events_record_success closes the
    circuit. If it fails, _error_events_record_failure re-opens it.
    """
    if _DISABLE_BREAKER:
        return False
    if not _error_events_disabled:
        return False
    # Half-open: allow one probe after the interval
    if _error_events_tripped_at is not None:
        if time.time() - _error_events_tripped_at >= _ERROR_EVENTS_HALF_OPEN_INTERVAL:
            return False
    return True


def _error_events_record_failure() -> None:
    """Record a failure and trip circuit breaker if limit exceeded."""
    global _error_events_consecutive_failures, _error_events_disabled, _error_events_tripped_at
    _error_events_consecutive_failures += 1
    if _error_events_consecutive_failures >= _ERROR_EVENTS_CIRCUIT_BREAKER_FAIL_LIMIT:
        was_disabled = _error_events_disabled
        _error_events_disabled = True
        _error_events_tripped_at = time.time()
        if not was_disabled:
            logger.error(
                "error_events circuit breaker OPEN: module DISABLED after %d consecutive failures",
                _error_events_consecutive_failures,
            )


def _error_events_record_success() -> None:
    """Record success and reset circuit breaker."""
    global _error_events_consecutive_failures, _error_events_disabled, _error_events_tripped_at
    _error_events_consecutive_failures = 0
    if _error_events_disabled:
        _error_events_disabled = False
        _error_events_tripped_at = None
        logger.info("error_events circuit breaker CLOSED: module re-enabled")


_PRIVATE_RE = None


def _compile_private_re():
    import re

    return re.compile(r"<private>.*?</private>", re.DOTALL | re.IGNORECASE)


def strip_private(obj: Any):
    """Recursively strip <private>...</private> from any string in a dict/list."""
    global _PRIVATE_RE
    if _PRIVATE_RE is None:
        _PRIVATE_RE = _compile_private_re()
    if isinstance(obj, str):
        return _PRIVATE_RE.sub("", obj).strip()
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    return obj


def _insert_event(
    conn,
    session_id: str,
    project_id: str,
    source_tool: str,
    event_type: str,
    tool_name: str | None,
    payload: str,
    seq: int,
) -> int | None:
    """Internal only — not exposed to MCP or CLI."""
    payload_dict = json.loads(payload)
    payload = json.dumps(strip_private(payload_dict))

    h = hashlib.sha256(f"{event_type}:{tool_name}:{payload}".encode()).hexdigest()
    dup = conn.execute(
        "SELECT id FROM events WHERE content_hash=? AND session_id=? "
        "AND ts >= datetime('now', '-60 seconds')",
        (h, session_id),
    ).fetchone()
    if dup:
        return None

    cur = conn.execute(
        "INSERT INTO events (session_id, project_id, source_tool, event_type, "
        "tool_name, payload, seq, content_hash) VALUES (?,?,?,?,?,?,?,?)",
        (session_id, project_id, source_tool, event_type, tool_name, payload, seq, h),
    )
    event_id = cur.lastrowid
    conn.execute(
        "INSERT INTO events_fts(rowid, payload) VALUES (?, ?)", (event_id, payload)
    )
    return event_id


def _parse_id(id_str: str) -> tuple[str, int]:
    parts = id_str.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Unknown ID prefix in '{id_str}'. Valid: d:, s:, c:, e:")
    prefix, raw = parts[0], parts[1]
    if prefix not in {"d", "s", "c", "e"}:
        raise ValueError(f"Unknown ID prefix in '{id_str}'. Valid: d:, s:, c:, e:")
    n = int(raw)
    if prefix == "c" and n < 1_000_000:
        raise ValueError(f"Invalid c: ID '{id_str}' — compat IDs must be >= 1,000,000")
    return prefix, n


def _json_load_list(value: str | None) -> list:
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        return []


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/health")
    def health():
        return jsonify({"ok": True})

    @app.route("/events", methods=["POST"])
    def post_event():
        data = request.get_json(silent=True) or {}
        required = [
            "session_id",
            "project_id",
            "source_tool",
            "event_type",
            "payload",
            "seq",
        ]
        missing = [k for k in required if k not in data or data[k] in (None, "")]
        if missing:
            return jsonify({"error": "missing_fields", "fields": missing}), 400

        payload = data["payload"]
        if isinstance(payload, dict):
            payload = json.dumps(payload)

        with _write_lock:
            conn = get_conn()
            try:
                event_id = _insert_event(
                    conn,
                    session_id=str(data["session_id"]),
                    project_id=str(data["project_id"]),
                    source_tool=str(data["source_tool"]),
                    event_type=str(data["event_type"]),
                    tool_name=data.get("tool_name"),
                    payload=payload,
                    seq=int(data["seq"]),
                )
                conn.commit()
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower():
                    return jsonify({"error": "db_locked", "message": str(e)}), 503
                raise
            finally:
                conn.close()

        if event_id is None:
            return jsonify({"status": "duplicate"}), 200
        return jsonify({"status": "ok", "event_id": event_id}), 201

    @app.route("/events/batch", methods=["POST"])
    def post_events_batch():
        """Accept a list of events and write them in a single transaction."""
        items = request.get_json(silent=True)
        if not isinstance(items, list):
            return jsonify({"error": "expected_array"}), 400

        results = []
        with _write_lock:
            conn = get_conn()
            try:
                for data in items:
                    required = [
                        "session_id",
                        "project_id",
                        "source_tool",
                        "event_type",
                        "payload",
                        "seq",
                    ]
                    missing = [
                        k for k in required if k not in data or data[k] in (None, "")
                    ]
                    if missing:
                        results.append({"error": "missing_fields", "fields": missing})
                        continue
                    payload = data["payload"]
                    if isinstance(payload, dict):
                        payload = json.dumps(payload)
                    event_id = _insert_event(
                        conn,
                        session_id=str(data["session_id"]),
                        project_id=str(data["project_id"]),
                        source_tool=str(data["source_tool"]),
                        event_type=str(data["event_type"]),
                        tool_name=data.get("tool_name"),
                        payload=payload,
                        seq=int(data["seq"]),
                    )
                    results.append(
                        {
                            "status": "duplicate" if event_id is None else "ok",
                            "event_id": event_id,
                        }
                    )
                conn.commit()
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower():
                    return jsonify({"error": "db_locked", "message": str(e)}), 503
                raise
            finally:
                conn.close()

        return jsonify({"results": results}), 207

    @app.route("/search")
    def search():
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"error": "query_required"}), 400
        k = int(request.args.get("k") or 10)
        k = max(1, min(k, 50))
        project_id = request.args.get("project_id")
        if project_id:
            project_id = _canonicalize_project_id(project_id)
        type_filter = request.args.get("type")
        concepts_raw = request.args.get("concepts")
        concepts = (
            [c.strip() for c in concepts_raw.split(",")] if concepts_raw else None
        )

        # Sanitize query for FTS5: wrap each token in double quotes so
        # hyphens/special chars aren't interpreted as FTS operators.
        # Internal double quotes are escaped by doubling them per SQLite docs.
        def _fts5_safe(raw: str) -> str:
            tokens = raw.split()
            return " ".join(
                f'"{t.replace(chr(34), chr(34) + chr(34))}"' for t in tokens if t
            )

        fts_q = _fts5_safe(q)
        if not fts_q:
            return jsonify({"results": []}), 200

        conn = get_conn()
        try:
            results = []

            try:
                # Distilled summaries
                params = [fts_q]
                sql = (
                    "SELECT d.id, d.ts, d.type, d.title, d.impact_score, d.project_id "
                    "FROM distilled_summaries d "
                    "WHERE d.id IN (SELECT rowid FROM distilled_summaries_fts "
                    "WHERE distilled_summaries_fts MATCH ?) "
                )
                if project_id:
                    sql += "AND d.project_id = ? "
                    params.append(project_id)
                if type_filter:
                    sql += "AND d.type = ? "
                    params.append(type_filter)
                sql += "ORDER BY d.impact_score DESC, d.ts DESC LIMIT ?"
                params.append(k)
                rows = conn.execute(sql, params).fetchall()
                for r in rows:
                    results.append(
                        {
                            "id": f"d:{r['id']}",
                            "ts": r["ts"],
                            "type": r["type"],
                            "title": r["title"],
                            "impact_score": r["impact_score"],
                            "project_id": r["project_id"],
                        }
                    )

                # Session summaries
                params = [fts_q]
                sql = (
                    "SELECT s.id, s.ts, s.request, s.project_id "
                    "FROM session_summaries s "
                    "WHERE s.id IN (SELECT rowid FROM session_summaries_fts "
                    "WHERE session_summaries_fts MATCH ?) "
                )
                if project_id:
                    sql += "AND s.project_id = ? "
                    params.append(project_id)
                sql += "ORDER BY s.ts DESC LIMIT ?"
                params.append(max(1, min(k, 10)))
                rows = conn.execute(sql, params).fetchall()
                for r in rows:
                    results.append(
                        {
                            "id": f"s:{r['id']}",
                            "ts": r["ts"],
                            "type": "summary",
                            "title": r["request"],
                            "impact_score": None,
                            "project_id": r["project_id"],
                        }
                    )

                # Raw events
                params = [fts_q]
                sql = (
                    "SELECT e.id, e.ts, e.project_id, e.event_type, e.tool_name "
                    "FROM events e "
                    "WHERE e.id IN (SELECT rowid FROM events_fts WHERE events_fts MATCH ?) "
                )
                if project_id:
                    sql += "AND e.project_id = ? "
                    params.append(project_id)
                if type_filter:
                    sql += "AND e.event_type = ? "
                    params.append(type_filter)
                sql += "ORDER BY e.ts DESC LIMIT ?"
                params.append(k)
                rows = conn.execute(sql, params).fetchall()
                for r in rows:
                    title = r["event_type"]
                    if r["tool_name"]:
                        title = f"{title} ({r['tool_name']})"
                    results.append(
                        {
                            "id": f"e:{r['id']}",
                            "ts": r["ts"],
                            "type": "event",
                            "title": title,
                            "impact_score": None,
                            "project_id": r["project_id"],
                        }
                    )

                # Compat principles (legacy)
                try:
                    params = [fts_q]
                    sql = (
                        "SELECT p.id, p.ts, p.type, p.principle, p.impact_score, p.project_tag "
                        "FROM principles p "
                        "WHERE p.id IN (SELECT rowid FROM principles_fts WHERE principles_fts MATCH ?) "
                    )
                    if project_id:
                        sql += "AND p.project_tag = ? "
                        params.append(project_id)
                    if type_filter:
                        sql += "AND p.type = ? "
                        params.append(type_filter)
                    sql += "ORDER BY p.impact_score DESC, p.ts DESC LIMIT ?"
                    params.append(k)
                    rows = conn.execute(sql, params).fetchall()
                    for r in rows:
                        compat_id = int(r["id"]) + 1_000_000
                        results.append(
                            {
                                "id": f"c:{compat_id}",
                                "ts": r["ts"],
                                "type": r["type"],
                                "title": str(r["principle"])[:100],
                                "impact_score": r["impact_score"],
                                "project_id": r["project_tag"],
                            }
                        )
                except Exception as e:
                    logger.debug("Compat principles query failed (may be empty): %s", e)
            except sqlite3.OperationalError as e:
                return jsonify({"error": "invalid_query", "message": str(e)}), 400

            if concepts:
                # Batch-fetch concepts for all d: results in one query
                d_ids = [
                    int(r["id"].split(":")[1])
                    for r in results
                    if r["id"].startswith("d:")
                ]
                if d_ids:
                    placeholders = ",".join("?" * len(d_ids))
                    concept_rows = conn.execute(
                        f"SELECT id, concepts FROM distilled_summaries WHERE id IN ({placeholders})",
                        d_ids,
                    ).fetchall()
                    concept_map = {
                        row["id"]: _json_load_list(row["concepts"])
                        for row in concept_rows
                    }
                else:
                    concept_map = {}

                filtered = []
                for r in results:
                    if r["id"].startswith("d:"):
                        c_list = concept_map.get(int(r["id"].split(":")[1]), [])
                        if any(c in c_list for c in concepts):
                            filtered.append(r)
                    else:
                        filtered.append(r)
                results = filtered

            return jsonify({"results": results})
        finally:
            conn.close()

    @app.route("/recent")
    def recent():
        """Return the most recent memories by timestamp with inline excerpts."""
        k = min(int(request.args.get("k") or 5), 20)
        project_id = request.args.get("project_id")
        proj_clause = "AND project_id = ?" if project_id else ""
        proj_params = [project_id] if project_id else []

        conn = get_conn()
        try:
            results = []

            # Distilled summaries (highest signal)
            rows = conn.execute(
                f"SELECT id, ts, type, title, narrative, impact_score, project_id "
                f"FROM distilled_summaries WHERE 1=1 {proj_clause} "
                f"ORDER BY ts DESC LIMIT ?",
                proj_params + [k],
            ).fetchall()
            for r in rows:
                results.append(
                    {
                        "id": f"d:{r['id']}",
                        "ts": r["ts"],
                        "type": r["type"],
                        "title": r["title"],
                        "narrative": (r["narrative"] or "")[:200],
                        "impact_score": r["impact_score"],
                        "project_id": r["project_id"],
                    }
                )

            # Recent scanner_learning events (have content field in payload)
            if len(results) < k:
                rows = conn.execute(
                    f"SELECT id, ts, project_id, event_type, tool_name, payload "
                    f"FROM events WHERE event_type='scanner_learning' {proj_clause} "
                    f"ORDER BY ts DESC LIMIT ?",
                    proj_params + [k - len(results)],
                ).fetchall()
                for r in rows:
                    try:
                        p = json.loads(r["payload"]) if r["payload"] else {}
                    except Exception:
                        p = {}
                    excerpt = (p.get("content") or "")[:200]
                    results.append(
                        {
                            "id": f"e:{r['id']}",
                            "ts": r["ts"],
                            "type": "event",
                            "title": r["event_type"],
                            "excerpt": excerpt,
                            "impact_score": None,
                            "project_id": r["project_id"],
                        }
                    )

            # Recent PostToolUse events as fallback
            if len(results) < k:
                rows = conn.execute(
                    f"SELECT id, ts, project_id, event_type, tool_name, payload "
                    f"FROM events WHERE event_type != 'scanner_learning' {proj_clause} "
                    f"ORDER BY ts DESC LIMIT ?",
                    proj_params + [k - len(results)],
                ).fetchall()
                for r in rows:
                    try:
                        p = json.loads(r["payload"]) if r["payload"] else {}
                    except Exception:
                        p = {}
                    excerpt = (p.get("content") or "")[:200]
                    title = r["event_type"]
                    if r["tool_name"]:
                        title = f"{title} ({r['tool_name']})"
                    results.append(
                        {
                            "id": f"e:{r['id']}",
                            "ts": r["ts"],
                            "type": "event",
                            "title": title,
                            "excerpt": excerpt,
                            "impact_score": None,
                            "project_id": r["project_id"],
                        }
                    )

            results.sort(key=lambda x: x["ts"] or "", reverse=True)
            return jsonify({"results": results[:k]})
        finally:
            conn.close()

    @app.route("/timeline")
    def timeline():
        anchor_id = request.args.get("anchor_id")
        if not anchor_id:
            return jsonify({"error": "anchor_id_required"}), 400
        depth_before = int(request.args.get("depth_before") or 3)
        depth_after = int(request.args.get("depth_after") or 3)
        project_id = request.args.get("project_id")

        prefix, anchor = _parse_id(anchor_id)
        if prefix != "d":
            return jsonify({"error": "timeline_only_supports_distilled"}), 400

        conn = get_conn()
        try:
            anchor_row = conn.execute(
                "SELECT id, ts, type, title, project_id FROM distilled_summaries WHERE id=?",
                (anchor,),
            ).fetchone()
            if not anchor_row:
                return jsonify({"error": "anchor_not_found"}), 404

            anchor_ts = anchor_row["ts"]
            proj_filter = "AND project_id = ?" if project_id else ""

            before = conn.execute(
                "SELECT id, ts, type, title FROM distilled_summaries "
                "WHERE (ts < ? OR (ts = ? AND id < ?)) "
                f"{proj_filter} "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (
                    [anchor_ts, anchor_ts, anchor]
                    + ([project_id] if project_id else [])
                    + [depth_before]
                ),
            ).fetchall()
            after = conn.execute(
                "SELECT id, ts, type, title FROM distilled_summaries "
                "WHERE (ts > ? OR (ts = ? AND id > ?)) "
                f"{proj_filter} "
                "ORDER BY ts ASC, id ASC LIMIT ?",
                (
                    [anchor_ts, anchor_ts, anchor]
                    + ([project_id] if project_id else [])
                    + [depth_after]
                ),
            ).fetchall()

            items = []
            for r in reversed(before):
                items.append(
                    {
                        "id": f"d:{r['id']}",
                        "ts": r["ts"],
                        "type": r["type"],
                        "title": r["title"],
                    }
                )
            items.append(
                {
                    "id": f"d:{anchor_row['id']}",
                    "ts": anchor_row["ts"],
                    "type": anchor_row["type"],
                    "title": anchor_row["title"],
                }
            )
            for r in after:
                items.append(
                    {
                        "id": f"d:{r['id']}",
                        "ts": r["ts"],
                        "type": r["type"],
                        "title": r["title"],
                    }
                )

            return jsonify({"timeline": items})
        finally:
            conn.close()

    @app.route("/observation/<prefix>/<int:row_id>")
    def observation(prefix: str, row_id: int):
        conn = get_conn()
        try:
            if prefix == "d":
                row = conn.execute(
                    "SELECT * FROM distilled_summaries WHERE id=?",
                    (row_id,),
                ).fetchone()
                if not row:
                    return jsonify({"error": "not_found"}), 404
                result = dict(row)
                result["facts"] = _json_load_list(result.get("facts"))
                result["files_read"] = _json_load_list(result.get("files_read"))
                result["files_modified"] = _json_load_list(result.get("files_modified"))
                result["concepts"] = _json_load_list(result.get("concepts"))
                return jsonify(result)
            if prefix == "s":
                row = conn.execute(
                    "SELECT * FROM session_summaries WHERE id=?",
                    (row_id,),
                ).fetchone()
                if not row:
                    return jsonify({"error": "not_found"}), 404
                result = dict(row)
                result["concepts"] = _json_load_list(result.get("concepts"))
                return jsonify(result)
            if prefix == "c":
                legacy_id = row_id - 1_000_000
                row = conn.execute(
                    "SELECT * FROM principles WHERE id=?",
                    (legacy_id,),
                ).fetchone()
                if not row:
                    return jsonify({"error": "not_found"}), 404
                return jsonify(
                    {
                        "id": f"c:{row_id}",
                        "ts": row["ts"],
                        "project_id": row["project_tag"],
                        "type": row["type"],
                        "title": str(row["principle"])[:100],
                        "narrative": row["principle"],
                        "impact_score": row["impact_score"],
                        "concepts": row["tags"],
                    }
                )
            if prefix == "e":
                row = conn.execute(
                    "SELECT * FROM events WHERE id=?",
                    (row_id,),
                ).fetchone()
                if not row:
                    return jsonify({"error": "not_found"}), 404
                payload = row["payload"]
                try:
                    payload = json.loads(payload) if payload else {}
                except Exception:
                    pass
                return jsonify(
                    {
                        "id": f"e:{row_id}",
                        "ts": row["ts"],
                        "project_id": row["project_id"],
                        "type": "event",
                        "title": row["event_type"],
                        "narrative": payload,
                        "event_type": row["event_type"],
                        "tool_name": row["tool_name"],
                        "source_tool": row["source_tool"],
                        "session_id": row["session_id"],
                        "seq": row["seq"],
                    }
                )
            return jsonify({"error": "invalid_prefix"}), 400
        finally:
            conn.close()

    @app.route("/session_summaries", methods=["POST"])
    def save_session_summary():
        data = request.get_json(silent=True) or {}
        required = ["request", "project_id", "source_tool"]
        missing = [k for k in required if k not in data or data[k] in (None, "")]
        if missing:
            return jsonify({"error": "missing_fields", "fields": missing}), 400

        payload = {
            "request": data.get("request"),
            "investigation": data.get("investigation"),
            "learnings": data.get("learnings"),
            "next_steps": data.get("next_steps"),
            "concepts": data.get("concepts") or [],
        }
        payload = strip_private(payload)

        with _write_lock:
            conn = get_conn()
            try:
                cur = conn.execute(
                    "INSERT INTO session_summaries "
                    "(session_id, project_id, source_tool, request, investigation, learnings, next_steps, concepts) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        data.get("session_id"),
                        data.get("project_id"),
                        data.get("source_tool"),
                        payload.get("request"),
                        payload.get("investigation"),
                        payload.get("learnings"),
                        payload.get("next_steps"),
                        json.dumps(payload.get("concepts") or []),
                    ),
                )
                ss_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO session_summaries_fts(rowid, request, learnings, next_steps, concepts, project_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        ss_id,
                        payload.get("request") or "",
                        payload.get("learnings") or "",
                        payload.get("next_steps") or "",
                        json.dumps(payload.get("concepts") or []),
                        data.get("project_id") or "",
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return jsonify({"status": "ok", "id": ss_id}), 201

    @app.route("/session_summaries", methods=["GET"])
    def get_session_summaries():
        project_id = request.args.get("project_id")
        if not project_id:
            return jsonify({"error": "project_id_required"}), 400
        session_id = request.args.get("session_id")
        k = int(request.args.get("k") or 3)
        k = max(1, min(k, 50))

        conn = get_conn()
        try:
            params = [project_id]
            sql = "SELECT * FROM session_summaries WHERE project_id=? "
            if session_id:
                sql += "AND session_id=? "
                params.append(session_id)
            sql += "ORDER BY ts DESC LIMIT ?"
            params.append(k)
            rows = conn.execute(sql, params).fetchall()
            results = []
            for r in rows:
                item = dict(r)
                item["concepts"] = _json_load_list(item.get("concepts"))
                results.append(item)
            return jsonify({"results": results})
        finally:
            conn.close()

    @app.route("/error_events", methods=["POST"])
    def post_error_event():
        if _error_events_circuit_open():
            return jsonify(
                {
                    "error": "module_disabled",
                    "message": "error_events module temporarily disabled",
                }
            ), 503

        data = request.get_json(silent=True) or {}
        session_id = data.get("session_id")
        fingerprint = data.get("fingerprint")
        if not session_id or not fingerprint:
            return jsonify({"error": "session_id and fingerprint required"}), 400

        error_text = strip_private(data.get("error_text") or "")
        project_id = data.get("project_id")
        if project_id:
            project_id = _canonicalize_project_id(project_id)

        with _write_lock:
            conn = get_conn()
            try:
                conn.execute(
                    "INSERT INTO error_events (session_id, project_id, fingerprint, error_keywords, error_text) "
                    "VALUES (?,?,?,?,?)",
                    (
                        session_id,
                        project_id,
                        fingerprint,
                        data.get("error_keywords"),
                        error_text[:1000] if error_text else None,
                    ),
                )
                conn.commit()
                _error_events_record_success()
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "no such table" not in msg:
                    _error_events_record_failure()
                    logger.warning("error_events INSERT failed: %s", e)
                    conn.close()
                    return jsonify({"error": "db_error", "message": str(e)}), 503
                try:
                    conn.executescript(
                        "CREATE TABLE IF NOT EXISTS error_events ("
                        "  id INTEGER PRIMARY KEY,"
                        "  ts DATETIME DEFAULT CURRENT_TIMESTAMP,"
                        "  session_id TEXT NOT NULL,"
                        "  project_id TEXT,"
                        "  fingerprint TEXT NOT NULL,"
                        "  error_keywords TEXT,"
                        "  error_text TEXT,"
                        "  recalled_at DATETIME"
                        ");"
                        "CREATE INDEX IF NOT EXISTS idx_error_session_fp ON error_events(session_id, fingerprint);"
                        "CREATE INDEX IF NOT EXISTS idx_error_project ON error_events(project_id);"
                    )
                    conn.execute(
                        "INSERT INTO error_events (session_id, project_id, fingerprint, error_keywords, error_text) "
                        "VALUES (?,?,?,?,?)",
                        (
                            session_id,
                            project_id,
                            fingerprint,
                            data.get("error_keywords"),
                            error_text[:1000] if error_text else None,
                        ),
                    )
                    conn.commit()
                    _error_events_record_success()
                except Exception as e:
                    _error_events_record_failure()
                    logger.warning(
                        "Failed to create error_events table inline: %s",
                        e,
                        exc_info=True,
                    )
                    conn.close()
                    return jsonify({"error": "db_error", "message": str(e)}), 503
            finally:
                conn.close()

        return jsonify({"status": "ok"}), 201

    @app.route("/error_events", methods=["GET"])
    def get_error_events():
        session_id = request.args.get("session_id")
        fingerprint = request.args.get("fingerprint")
        if not session_id or not fingerprint:
            return jsonify({"error": "session_id and fingerprint required"}), 400

        conn = get_conn()
        try:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt, MAX(ts) as last_ts, MAX(recalled_at) as last_recalled_at "
                    "FROM error_events WHERE session_id=? AND fingerprint=?",
                    (session_id, fingerprint),
                ).fetchone()
                count = row["cnt"] if row else 0
                last_ts = row["last_ts"] if row else None
                last_recalled_at = row["last_recalled_at"] if row else None
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "no such table" in msg or "no such column" in msg:
                    count = 0
                    last_ts = None
                    last_recalled_at = None
                else:
                    conn.close()
                    return jsonify({"error": "db_error", "message": str(e)}), 503
            return jsonify(
                {
                    "count": count,
                    "last_ts": last_ts,
                    "last_recalled_at": last_recalled_at,
                }
            )
        finally:
            conn.close()

    @app.route("/error_events/recall", methods=["POST"])
    def mark_error_recalled():
        data = request.get_json(silent=True) or {}
        session_id = data.get("session_id")
        fingerprint = data.get("fingerprint")
        if not session_id or not fingerprint:
            return jsonify({"error": "session_id and fingerprint required"}), 400

        with _write_lock:
            conn = get_conn()
            try:
                conn.execute(
                    "UPDATE error_events SET recalled_at=CURRENT_TIMESTAMP "
                    "WHERE id=(SELECT id FROM error_events "
                    "WHERE session_id=? AND fingerprint=? ORDER BY ts DESC LIMIT 1)",
                    (session_id, fingerprint),
                )
                conn.commit()
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "locked" in msg:
                    logger.error("mark_error_recalled DB locked: %s", e)
                    conn.close()
                    return jsonify({"error": "db_locked", "message": str(e)}), 503
                logger.debug("mark_error_recalled skipped: %s", e)
            finally:
                conn.close()

        return jsonify({"status": "ok"}), 200

    return app


class GracefulShutdown:
    def __init__(self):
        self.shutdown = False
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, sig, frame):
        logger.info(f"Received signal {sig}, shutting down gracefully...")
        self.shutdown = True


def _check_port(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


def main():
    logger.info("=" * 80)
    logger.info("Forgememo Daemon Starting")
    logger.info("=" * 80)

    try:
        migration_logger.info("Starting Forgememo v2 migration check...")
        migration_logger.info("Platform: %s", sys.platform)
        if sys.platform in ("darwin", "win32"):
            migration_logger.info(
                "Case-insensitive filesystem detected: project_id paths will be normalized"
            )
        else:
            migration_logger.info("Case-sensitive filesystem: normalization skipped")
        logger.info("Initializing database schema...")
        init_db()

        logger.info("Creating Flask application...")
        app = create_app()

        GracefulShutdown()

        if make_server is None:
            logger.error(
                "Werkzeug server unavailable. Install werkzeug (via flask) to run."
            )
            sys.exit(1)

        if sys.platform == "win32":
            # Windows: HTTP-only (AF_UNIX not reliable on Windows)
            port = int(HTTP_PORT)  # always set — defaults to "5555" above
            if _check_port("127.0.0.1", port):
                logger.error(f"Port {port} already in use — cannot start.")
                sys.exit(1)
            logger.info(f"Starting HTTP server on 127.0.0.1:{port} (Windows mode)...")
            http_server = make_server("127.0.0.1", port, app, threaded=True)
            write_port(port)
            from forgememo.port import write_pid, delete_pid as _delete_pid
            write_pid(os.getpid())
            logger.info("Health check: curl http://127.0.0.1:%s/health", port)
            try:
                http_server.serve_forever()
                logger.info("serve_forever() returned — daemon shutting down.")
            except Exception as _exc:
                logger.exception("Unhandled exception in serve_forever(): %s", _exc)
                raise
            finally:
                delete_port()
                _delete_pid()
                logger.info("Daemon stopped (Windows). Port and PID files removed.")
        else:
            # POSIX: UNIX socket primary, HTTP optional
            socket_host = f"unix://{SOCKET_PATH}"
            logger.info(f"Starting UNIX socket server on {SOCKET_PATH}...")
            socket_server = make_server(socket_host, 0, app, threaded=True)
            try:
                os.chmod(SOCKET_PATH, 0o600)
            except Exception:
                pass

            http_server = None
            if HTTP_PORT:
                port = int(HTTP_PORT)
                if _check_port("127.0.0.1", port):
                    logger.error(f"Port {port} already in use — HTTP server disabled.")
                else:
                    logger.info(f"Starting HTTP server on 127.0.0.1:{port}...")
                    http_server = make_server("127.0.0.1", port, app, threaded=True)
                    write_port(port)
                    threading.Thread(
                        target=http_server.serve_forever, daemon=True
                    ).start()

            logger.info(
                "Health check (socket): curl --unix-socket %s http://localhost/health",
                SOCKET_PATH,
            )
            if HTTP_PORT:
                logger.info(
                    "Health check (http): curl http://127.0.0.1:%s/health", HTTP_PORT
                )

            try:
                socket_server.serve_forever()
            finally:
                delete_port()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

    logger.info("=" * 80)
    logger.info("Forgememo Daemon Stopped")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
