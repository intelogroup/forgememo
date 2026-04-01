from __future__ import annotations

import sys

import pytest

import forgememo.mcp_server as mcp_server


class _Resp:
    def __init__(self, ok=True, status_code=200, payload=None):
        self.ok = ok
        self.status_code = status_code
        self._payload = payload or {}
        self.text = "err"

    def json(self):
        return self._payload


@pytest.mark.skipif(sys.platform == "win32", reason="Unix socket not supported on Windows")
def test_daemon_get_uses_socket_when_available(monkeypatch):
    called = {}

    class _Session:
        def get(self, url, params=None, timeout=5):
            called["url"] = url
            called["params"] = params
            return _Resp(payload={"ok": True})

    monkeypatch.setattr(mcp_server, "DAEMON_URL", None)
    monkeypatch.setattr(mcp_server, "HTTP_PORT", None)
    monkeypatch.setattr(mcp_server, "_socket_session", lambda: _Session())

    data = mcp_server._daemon_get("/health", params={"a": "b"})
    assert data["ok"] is True
    assert called["url"].startswith("http+unix://")


def test_daemon_get_falls_back_to_http(monkeypatch):
    called = {}

    def fake_get(url, params=None, timeout=5):
        called["url"] = url
        called["params"] = params
        return _Resp(payload={"ok": True})

    monkeypatch.setattr(mcp_server, "DAEMON_URL", None)
    monkeypatch.setattr(mcp_server, "HTTP_PORT", "7777")
    monkeypatch.setattr(mcp_server, "_socket_session", lambda: None)
    monkeypatch.setattr(mcp_server.requests, "get", fake_get)

    data = mcp_server._daemon_get("/health", params={"x": "y"})
    assert data["ok"] is True
    assert called["url"] == "http://127.0.0.1:7777/health"


def test_daemon_get_raises_when_no_transport(monkeypatch):
    monkeypatch.setattr(mcp_server, "DAEMON_URL", None)
    monkeypatch.setattr(mcp_server, "HTTP_PORT", None)
    monkeypatch.setattr(mcp_server, "_socket_session", lambda: None)

    with pytest.raises(RuntimeError):
        mcp_server._daemon_get("/health")


@pytest.mark.skipif(sys.platform == "win32", reason="Unix socket not supported on Windows")
def test_daemon_post_uses_socket_when_available(monkeypatch):
    called = {}

    class _Session:
        def post(self, url, json=None, timeout=5):
            called["url"] = url
            called["json"] = json
            return _Resp(payload={"ok": True})

    monkeypatch.setattr(mcp_server, "DAEMON_URL", None)
    monkeypatch.setattr(mcp_server, "HTTP_PORT", None)
    monkeypatch.setattr(mcp_server, "_socket_session", lambda: _Session())

    data = mcp_server._daemon_post("/events", payload={"a": 1})
    assert data["ok"] is True
    assert called["url"].startswith("http+unix://")


def test_daemon_post_falls_back_to_http(monkeypatch):
    called = {}

    def fake_post(url, json=None, timeout=5):
        called["url"] = url
        called["json"] = json
        return _Resp(payload={"ok": True})

    monkeypatch.setattr(mcp_server, "DAEMON_URL", None)
    monkeypatch.setattr(mcp_server, "HTTP_PORT", "7777")
    monkeypatch.setattr(mcp_server, "_socket_session", lambda: None)
    monkeypatch.setattr(mcp_server.requests, "post", fake_post)

    data = mcp_server._daemon_post("/events", payload={"a": 1})
    assert data["ok"] is True
    assert called["url"] == "http://127.0.0.1:7777/events"


def test_daemon_post_raises_when_no_transport(monkeypatch):
    monkeypatch.setattr(mcp_server, "DAEMON_URL", None)
    monkeypatch.setattr(mcp_server, "HTTP_PORT", None)
    monkeypatch.setattr(mcp_server, "_socket_session", lambda: None)

    with pytest.raises(RuntimeError):
        mcp_server._daemon_post("/events", payload={"a": 1})


# ---------------------------------------------------------------------------
# Windows transport: socket must be skipped, HTTP used instead
# ---------------------------------------------------------------------------

class TestWindowsTransport:
    """Verify unix socket is never attempted on win32 and HTTP fallback is used."""

    def _make_session(self, called: dict, method: str):
        """Return a fake socket session that records if it was used."""
        class _Session:
            def get(self, url, **kw):
                called["socket"] = True
                raise AssertionError("socket session must not be used on Windows")
            def post(self, url, **kw):
                called["socket"] = True
                raise AssertionError("socket session must not be used on Windows")
        return _Session()

    def test_daemon_get_skips_socket_on_windows(self, monkeypatch):
        called = {}

        def fake_get(url, params=None, timeout=5):
            called["url"] = url
            return _Resp(payload={"ok": True})

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(mcp_server, "DAEMON_URL", None)
        monkeypatch.setattr(mcp_server, "HTTP_PORT", "5555")
        monkeypatch.setattr(mcp_server, "_socket_session", lambda: self._make_session(called, "get"))
        monkeypatch.setattr(mcp_server.requests, "get", fake_get)

        data = mcp_server._daemon_get("/health")
        assert data["ok"] is True
        assert "socket" not in called
        assert called["url"] == "http://127.0.0.1:5555/health"

    def test_daemon_post_skips_socket_on_windows(self, monkeypatch):
        called = {}

        def fake_post(url, json=None, timeout=5):
            called["url"] = url
            return _Resp(payload={"ok": True})

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(mcp_server, "DAEMON_URL", None)
        monkeypatch.setattr(mcp_server, "HTTP_PORT", "5555")
        monkeypatch.setattr(mcp_server, "_socket_session", lambda: self._make_session(called, "post"))
        monkeypatch.setattr(mcp_server.requests, "post", fake_post)

        data = mcp_server._daemon_post("/events", payload={"a": 1})
        assert data["ok"] is True
        assert "socket" not in called
        assert called["url"] == "http://127.0.0.1:5555/events"

    def test_daemon_get_uses_daemon_url_on_windows(self, monkeypatch):
        """FORGEMEMO_DAEMON_URL override still works on Windows."""
        called = {}

        def fake_get(url, params=None, timeout=5):
            called["url"] = url
            return _Resp(payload={"ok": True})

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(mcp_server, "DAEMON_URL", "http://remote:9999")
        monkeypatch.setattr(mcp_server, "HTTP_PORT", None)
        monkeypatch.setattr(mcp_server.requests, "get", fake_get)

        mcp_server._daemon_get("/health")
        assert called["url"] == "http://remote:9999/health"

    def test_daemon_get_raises_when_no_http_port_on_windows(self, monkeypatch):
        """On Windows with no HTTP port and no DAEMON_URL, must raise."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(mcp_server, "DAEMON_URL", None)
        monkeypatch.setattr(mcp_server, "HTTP_PORT", None)

        with pytest.raises(RuntimeError):
            mcp_server._daemon_get("/health")
