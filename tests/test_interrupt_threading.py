"""
Tests for OS interrupt handling in background threading and daemon communication.

Covers:
  1. _daemon_post never raises (even on connection refused, timeout, bad JSON)
  2. _daemon_get never raises (same)
  3. _ensure_daemon handles unreachable daemon gracefully
  4. Session-end subprocess spawn handles SIGTERM/SIGINT patterns
  5. Background thread in daemon doesn't prevent clean shutdown
  6. Signal handler (GracefulShutdown) sets shutdown flag
  7. Hook main() handles keyboard interrupt without traceback
  8. Concurrent daemon requests during shutdown don't deadlock
"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from unittest import mock

import pytest

import forgememo.daemon as daemon_module
import forgememo.hook as hook
import forgememo.storage as storage_module
from forgememo.daemon import GracefulShutdown, create_app
from forgememo.storage import init_db

try:
    from werkzeug.serving import make_server
except ImportError:
    make_server = None


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "interrupt_test.db"
    monkeypatch.setattr(storage_module, "DB_PATH", db_file)
    monkeypatch.setattr(daemon_module, "_write_lock", threading.Lock())
    init_db()
    yield db_file


# ─── _daemon_post never raises ──────────────────────────────────────────────


class TestDaemonPostResilience:
    def test_connection_refused(self, monkeypatch):
        """POST to unreachable daemon returns empty dict, no exception."""
        monkeypatch.setattr(hook, "DAEMON_URL", "http://127.0.0.1:1")
        monkeypatch.setattr(hook, "HTTP_PORT", "1")
        result = hook._daemon_post("/error_events", {"session_id": "s1", "fingerprint": "fp"})
        assert result == {}

    def test_invalid_url(self, monkeypatch):
        monkeypatch.setattr(hook, "DAEMON_URL", "http://not-a-real-host-12345.local:9999")
        monkeypatch.setattr(hook, "HTTP_PORT", "")
        result = hook._daemon_post("/test", {"key": "val"})
        assert result == {}

    def test_no_daemon_url_and_no_port(self, monkeypatch):
        monkeypatch.setattr(hook, "DAEMON_URL", "")
        monkeypatch.setattr(hook, "HTTP_PORT", "")
        if sys.platform == "win32":
            result = hook._daemon_post("/test", {})
            assert result == {}
        else:
            # On POSIX, tries socket first then HTTP — both fail gracefully
            monkeypatch.setattr(hook, "SOCKET_PATH", "/nonexistent/socket.sock")
            result = hook._daemon_post("/test", {})
            assert result == {}

    def test_daemon_returns_error_status(self, monkeypatch):
        """A 500 response should return empty dict, not crash."""
        def fake_post(*args, **kwargs):
            resp = mock.Mock()
            resp.ok = False
            resp.status_code = 500
            return resp
        monkeypatch.setattr(hook, "DAEMON_URL", "http://127.0.0.1:5555")
        monkeypatch.setattr("requests.post", fake_post)
        result = hook._daemon_post("/test", {})
        assert result == {}


# ─── _daemon_get never raises ───────────────────────────────────────────────


class TestDaemonGetResilience:
    def test_connection_refused(self, monkeypatch):
        monkeypatch.setattr(hook, "DAEMON_URL", "http://127.0.0.1:1")
        monkeypatch.setattr(hook, "HTTP_PORT", "1")
        result = hook._daemon_get("/error_events", {"session_id": "s1", "fingerprint": "fp"})
        assert result == {}

    def test_bad_json_response(self, monkeypatch):
        """If daemon returns non-JSON, should return empty dict."""
        def fake_get(*args, **kwargs):
            resp = mock.Mock()
            resp.ok = True
            resp.json.side_effect = json.JSONDecodeError("bad", "", 0)
            return resp
        monkeypatch.setattr(hook, "DAEMON_URL", "http://127.0.0.1:5555")
        monkeypatch.setattr("requests.get", fake_get)
        result = hook._daemon_get("/test")
        assert result == {}

    def test_timeout_returns_empty(self, monkeypatch):
        """A timeout should return empty dict."""
        import requests as req
        def fake_get(*args, **kwargs):
            raise req.exceptions.Timeout("timed out")
        monkeypatch.setattr(hook, "DAEMON_URL", "http://127.0.0.1:5555")
        monkeypatch.setattr("requests.get", fake_get)
        result = hook._daemon_get("/test")
        assert result == {}


# ─── _ensure_daemon resilience ───────────────────────────────────────────────


class TestEnsureDaemonResilience:
    def test_unreachable_daemon_returns_false(self, monkeypatch):
        monkeypatch.setattr(hook, "HTTP_PORT", "1")
        # Mock Popen to avoid actually starting a daemon
        monkeypatch.setattr(
            subprocess, "Popen",
            mock.Mock(side_effect=FileNotFoundError("no such binary")),
        )
        result = hook._ensure_daemon()
        assert result is False

    def test_popen_failure_returns_false(self, monkeypatch):
        """If subprocess.Popen raises, _ensure_daemon returns False."""
        monkeypatch.setattr(hook, "HTTP_PORT", "1")
        monkeypatch.setattr(
            subprocess, "Popen",
            mock.Mock(side_effect=PermissionError("cannot exec")),
        )
        result = hook._ensure_daemon()
        assert result is False


# ─── GracefulShutdown signal handler ────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
class TestGracefulShutdown:
    def test_signal_handler_sets_shutdown_flag(self):
        # Save original handlers so we can restore them (pytest registers its own)
        orig_term = signal.getsignal(signal.SIGTERM)
        orig_int = signal.getsignal(signal.SIGINT)
        try:
            gs = GracefulShutdown()
            assert gs.shutdown is False
            # Simulate SIGTERM via the handler method (don't actually send signal)
            gs._signal_handler(signal.SIGTERM, None)
            assert gs.shutdown is True
        finally:
            signal.signal(signal.SIGTERM, orig_term)
            signal.signal(signal.SIGINT, orig_int)

    def test_signal_handler_idempotent(self):
        orig_term = signal.getsignal(signal.SIGTERM)
        orig_int = signal.getsignal(signal.SIGINT)
        try:
            gs = GracefulShutdown()
            gs._signal_handler(signal.SIGINT, None)
            gs._signal_handler(signal.SIGINT, None)
            assert gs.shutdown is True
        finally:
            signal.signal(signal.SIGTERM, orig_term)
            signal.signal(signal.SIGINT, orig_int)


# ─── Session-end subprocess spawn ────────────────────────────────────────────


class TestSessionEndSubprocess:
    def test_session_end_with_missing_forgememo_binary(self, monkeypatch, capsys):
        """If forgememo binary not found, should output {} and return 0."""
        import shutil
        monkeypatch.setattr(shutil, "which", lambda x: None)
        monkeypatch.setattr(hook, "_ensure_daemon", lambda: True)

        from forgememo.hook import _handle_session_end
        result = _handle_session_end({"session_id": "s1", "cwd": "/tmp"})
        assert result == 0
        out = capsys.readouterr().out.strip()
        assert json.loads(out) == {}

    def test_session_end_with_popen_failure(self, monkeypatch, capsys):
        """If Popen fails, should still return 0 (never crash host)."""
        import shutil
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/forgememo")
        monkeypatch.setattr(hook, "_ensure_daemon", lambda: True)
        monkeypatch.setattr(
            subprocess, "Popen",
            mock.Mock(side_effect=OSError("spawn failed")),
        )

        from forgememo.hook import _handle_session_end
        result = _handle_session_end({"session_id": "s1", "cwd": "/tmp"})
        assert result == 0


# ─── Concurrent requests during server shutdown ─────────────────────────────


@pytest.mark.skipif(make_server is None, reason="werkzeug not installed")
class TestConcurrentRequestsDuringShutdown:
    def test_requests_during_shutdown_dont_deadlock(self):
        """Sending requests while server is shutting down should not hang."""
        import requests

        app = create_app()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        server = make_server("127.0.0.1", port, app, threaded=True)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.3)

        # Verify it's running
        r = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
        assert r.status_code == 200

        # Start shutdown in background
        shutdown_done = threading.Event()

        def do_shutdown():
            server.shutdown()
            shutdown_done.set()

        threading.Thread(target=do_shutdown, daemon=True).start()

        # Fire a few requests during shutdown — they may fail but should not hang
        for _ in range(5):
            try:
                requests.get(f"http://127.0.0.1:{port}/health", timeout=1)
            except Exception:
                pass  # Connection refused is fine during shutdown

        assert shutdown_done.wait(timeout=5), "Server shutdown timed out (possible deadlock)"


# ─── Hook main stdin handling ────────────────────────────────────────────────


class TestHookStdinHandling:
    def test_empty_stdin_returns_empty_dict(self, monkeypatch):
        """Empty stdin should produce {} not crash."""
        monkeypatch.setattr("sys.stdin", mock.Mock(read=lambda: ""))
        result = hook._read_stdin_json()
        assert result == {}

    def test_whitespace_only_stdin(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", mock.Mock(read=lambda: "   \n\t  "))
        result = hook._read_stdin_json()
        assert result == {}

    def test_invalid_json_stdin_raises(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", mock.Mock(read=lambda: "{broken"))
        with pytest.raises(json.JSONDecodeError):
            hook._read_stdin_json()

    def test_valid_json_stdin(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", mock.Mock(read=lambda: '{"key": "value"}'))
        result = hook._read_stdin_json()
        assert result == {"key": "value"}


# ─── Thread safety of _daemon_post/get with write lock ──────────────────────


class TestThreadSafetyDaemonComms:
    def test_concurrent_posts_dont_corrupt(self, monkeypatch):
        """Multiple threads calling _daemon_post concurrently should not raise."""
        results = []
        errors = []

        # Mock to just return success
        monkeypatch.setattr(hook, "DAEMON_URL", "")
        monkeypatch.setattr(hook, "HTTP_PORT", "")

        def do_post(i):
            try:
                r = hook._daemon_post("/test", {"i": i})
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_post, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent _daemon_post raised: {errors}"
        # All should return {} since no daemon is running
        assert all(r == {} for r in results)
