#!/usr/bin/env python3
"""
Smoke-tests for mcp_server.session_sync and _post_event_bg.
Designed to run with or without a live daemon (no daemon = graceful fallback).
"""
import os
import sys
import tempfile
import time

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        print(f"[PASS] {name}")
        PASS += 1
    else:
        print(f"[FAIL] {name}" + (f": {detail}" if detail else ""))
        FAIL += 1


def run():
    # ------------------------------------------------------------------ import
    try:
        import forgememo.mcp_server as ms
        check("import mcp_server", True)
    except Exception as e:
        check("import mcp_server", False, str(e))
        return

    # ------------------------------------------------------------------ _post_event_bg: non-blocking
    t0 = time.time()
    try:
        ms._post_event_bg(
            event_type="SessionStart",
            tool_name=None,
            payload={"cwd": "/tmp", "request": "test"},
            project_id="/tmp",
            session_id="test-session",
        )
        elapsed = time.time() - t0
        check("_post_event_bg returns immediately (< 0.1s)", elapsed < 0.1, f"took {elapsed:.3f}s")
    except Exception as e:
        check("_post_event_bg does not raise", False, str(e))

    # ------------------------------------------------------------------ session_sync: no daemon
    # Without daemon the function must return a string (never raise).
    tmp = tempfile.mkdtemp()
    try:
        result = ms.session_sync(workspace_root=tmp)
        check("session_sync returns str when no daemon", isinstance(result, str), repr(result))
        check("session_sync graceful no-daemon message", "No previous memory context" in result or "Forgememo context" in result, result)
    except Exception as e:
        check("session_sync no-daemon: does not raise", False, str(e))

    # ------------------------------------------------------------------ session_sync: with daemon running
    import pathlib
    import shutil
    import sqlite3
    import subprocess

    # Prefer venv-sibling binary (works when test runs inside a venv without
    # the venv activated, e.g. called as venv/bin/python test_session_sync.py).
    _venv_bin = pathlib.Path(sys.executable).parent / "forgememo"
    forgememo_bin = shutil.which("forgememo") or (str(_venv_bin) if _venv_bin.exists() else None)

    if not forgememo_bin:
        print("[SKIP] daemon test — forgememo binary not found")
    else:
        db_path = tempfile.mktemp(suffix=".db")
        env = {**os.environ, "FORGEMEM_DB": db_path, "FORGEMEMO_HTTP_PORT": "15777"}
        subprocess.run([forgememo_bin, "init", "--yes"], env=env, capture_output=True, timeout=15)
        daemon = subprocess.Popen(
            [forgememo_bin, "daemon"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(2)

        try:
            # Monkey-patch transport so mcp_server routes to our isolated test
            # daemon via HTTP instead of the real UNIX socket.
            _orig_http_port = ms.HTTP_PORT
            _orig_socket = ms.SOCKET_PATH
            _orig_daemon_url = ms.DAEMON_URL
            _orig_socket_session = ms._socket_session
            ms.HTTP_PORT = "15777"
            ms.SOCKET_PATH = "/tmp/_forgememo_no_such_socket.sock"
            ms.DAEMON_URL = None
            ms._socket_session = lambda: None  # force HTTP path

            result2 = ms.session_sync(workspace_root=tmp, session_id="e2e-test")
            check("session_sync returns str with live daemon", isinstance(result2, str), repr(result2))

            time.sleep(0.5)  # let _post_event_bg thread complete

            # E2E: verify SessionStart event landed in the DB via daemon HTTP API
            import urllib.request
            url = "http://127.0.0.1:15777/search?q=recent&project_id=%2Ftmp&k=1"
            try:
                resp = urllib.request.urlopen(url, timeout=3)
                check("daemon /search responds after session_sync", resp.status == 200)
            except Exception as e:
                check("daemon /search responds after session_sync", False, str(e))

            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT event_type, session_id FROM events "
                "WHERE event_type='SessionStart' AND session_id='e2e-test'"
            ).fetchall()
            conn.close()
            check("SessionStart event written to DB", len(rows) == 1, f"rows={rows}")
        except Exception as e:
            check("session_sync with daemon: no exception", False, str(e))
        finally:
            ms.HTTP_PORT = _orig_http_port
            ms.SOCKET_PATH = _orig_socket
            ms.DAEMON_URL = _orig_daemon_url
            ms._socket_session = _orig_socket_session
            daemon.kill()
            try:
                daemon.wait(timeout=3)
            except Exception:
                pass

    # ------------------------------------------------------------------ v0.2.8 SQLite + batch fixes
    import json
    import unittest.mock

    os.environ["FORGEMEMO_ALLOW_TMP_LOG"] = "1"
    try:
        from forgememo import storage as _storage
        from forgememo.daemon import create_app as _create_app
        import sqlite3 as _sqlite3

        _db = tempfile.mktemp(suffix=".db")
        os.environ["FORGEMEM_DB"] = _db
        _storage.DB_PATH = pathlib.Path(_db)
        _storage.init_db()
        _app = _create_app()
        _c = _app.test_client()

        # 1. PRAGMA values via get_conn()
        _conn = _storage.get_conn()
        _sync = _conn.execute("PRAGMA synchronous").fetchone()[0]
        _bt   = _conn.execute("PRAGMA busy_timeout").fetchone()[0]
        _jm   = _conn.execute("PRAGMA journal_mode").fetchone()[0]
        _conn.close()
        check("PRAGMA journal_mode=WAL",          _jm == "wal",    f"got {_jm}")
        check("PRAGMA synchronous=NORMAL (1)",    _sync == 1,      f"got {_sync}")
        check("PRAGMA busy_timeout=30000ms",      _bt == 30000,    f"got {_bt}ms")

        # 2. /events/batch — 6 events in one transaction
        _batch = [
            {"session_id": "b1", "project_id": "/tmp", "source_tool": "um",
             "event_type": "ToolResult", "tool_name": "store",
             "payload": json.dumps({"content": f"trace {i}"}), "seq": i}
            for i in range(6)
        ]
        _r = _c.post("/events/batch", json=_batch)
        _body = _r.get_json()
        _saved = sum(1 for x in _body["results"] if x.get("status") == "ok")
        check("/events/batch returns 207",        _r.status_code == 207, f"got {_r.status_code}")
        check("/events/batch saves 6/6 events",   _saved == 6,           f"saved {_saved}/6")

        _conn2 = _storage.get_conn()
        _cnt = _conn2.execute("SELECT COUNT(*) FROM events WHERE session_id='b1'").fetchone()[0]
        _conn2.close()
        check("/events/batch: 6 rows in DB",      _cnt == 6,             f"got {_cnt}")

        # 3. /events 503 on lock
        with unittest.mock.patch(
            "forgememo.daemon._insert_event",
            side_effect=_sqlite3.OperationalError("database is locked"),
        ):
            _r2 = _c.post("/events", json={
                "session_id": "s503", "project_id": "/tmp", "source_tool": "test",
                "event_type": "Test", "payload": "{}", "seq": 9999,
            })
        check("/events returns 503 on lock",      _r2.status_code == 503, f"got {_r2.status_code}")
        check("503 body has error=db_locked",     _r2.get_json().get("error") == "db_locked")

    except Exception as e:
        check("v0.2.8 daemon tests: no import error", False, str(e))

    # ------------------------------------------------------------------ skill file version bump
    skill = pathlib.Path(__file__).parent / "forgememo" / "skills" / "gemini.md"
    if skill.exists():
        content = skill.read_text()
        check("gemini.md version >= 4", "forgememo-skill-version: 4" in content, content[:80])
        check("gemini.md references session_sync", "session_sync" in content)
    else:
        check("gemini.md exists", False, str(skill))

    # ------------------------------------------------------------------ results
    print()
    print(f"{'='*50}")
    print(f"RESULTS: {PASS} passed, {FAIL} failed")
    print(f"{'='*50}")
    return FAIL


if __name__ == "__main__":
    sys.exit(run() or 0)
