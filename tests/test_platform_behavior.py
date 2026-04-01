from __future__ import annotations

import importlib
import io
import json
import os
import sys
import tempfile

from rich.console import Console

import forgememo.config as config
import forgememo.hook as hook
import forgememo.mcp_server as mcp_server


class _Stdout:
    def __init__(self, encoding: str):
        self.encoding = encoding
        self.buffer = io.BytesIO()


def test_cli_symbols_ascii(monkeypatch, request):
    import forgememo.cli as cli

    monkeypatch.setattr("sys.stdout", _Stdout("ascii"))
    importlib.reload(cli)
    assert cli.CHECK == "ok"
    assert cli.CROSS == "x"
    # Reload again after monkeypatch restores stdout so the module's console
    # object doesn't retain a reference to the now-closed _Stdout buffer.
    request.addfinalizer(lambda: importlib.reload(cli))


def test_cli_symbols_utf8(monkeypatch, request):
    import forgememo.cli as cli

    monkeypatch.setattr("sys.stdout", _Stdout("utf-8"))
    importlib.reload(cli)
    assert cli.CHECK == "\u2713"
    assert cli.CROSS == "\u2717"
    request.addfinalizer(lambda: importlib.reload(cli))


def test_cli_make_console_ascii(monkeypatch, request):
    import forgememo.cli as cli

    monkeypatch.setattr("sys.stdout", _Stdout("ascii"))
    importlib.reload(cli)
    console = cli._make_console()
    assert isinstance(console, Console)
    request.addfinalizer(lambda: importlib.reload(cli))


def test_mcp_resolve_project_id_falls_back(monkeypatch):
    def _boom(*_a, **_kw):
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(mcp_server.subprocess, "run", _boom)
    monkeypatch.setattr("os.path.realpath", lambda p: "/fallback")
    mcp_server._resolve_project_id.cache_clear()
    assert mcp_server._resolve_project_id("/tmp/work") == "/fallback"


def test_hook_resolve_project_id_windows_path(monkeypatch):
    monkeypatch.setattr("os.path.realpath", lambda p: r"C:\Proj")
    result = hook._resolve_project_id({"cwd": r"C:\Proj"})
    assert result == r"C:\Proj"


def test_config_get_ollama_url_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11435")
    assert config.get_ollama_url() == "http://127.0.0.1:11435"


# ---------------------------------------------------------------------------
# Socket path defaults use tempfile.gettempdir(), not hardcoded /tmp
# ---------------------------------------------------------------------------

def test_mcp_server_socket_path_default_uses_tempdir(monkeypatch):
    monkeypatch.delenv("FORGEMEMO_SOCKET", raising=False)
    importlib.reload(mcp_server)
    expected = os.path.join(tempfile.gettempdir(), "forgememo.sock")
    assert mcp_server.SOCKET_PATH == expected


def test_hook_socket_path_default_uses_tempdir(monkeypatch):
    monkeypatch.delenv("FORGEMEMO_SOCKET", raising=False)
    importlib.reload(hook)
    expected = os.path.join(tempfile.gettempdir(), "forgememo.sock")
    assert hook.SOCKET_PATH == expected


def test_mcp_server_socket_path_env_override(monkeypatch):
    monkeypatch.setenv("FORGEMEMO_SOCKET", "/custom/my.sock")
    importlib.reload(mcp_server)
    assert mcp_server.SOCKET_PATH == "/custom/my.sock"


# ---------------------------------------------------------------------------
# HTTP_PORT defaults: None on POSIX, "5555" on Windows
# ---------------------------------------------------------------------------

def test_mcp_server_http_port_default_posix(monkeypatch):
    monkeypatch.delenv("FORGEMEMO_HTTP_PORT", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    importlib.reload(mcp_server)
    assert mcp_server.HTTP_PORT is None


def test_mcp_server_http_port_default_windows(monkeypatch):
    monkeypatch.delenv("FORGEMEMO_HTTP_PORT", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    importlib.reload(mcp_server)
    assert mcp_server.HTTP_PORT == "5555"


def test_hook_http_port_default_posix(monkeypatch):
    monkeypatch.delenv("FORGEMEMO_HTTP_PORT", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    importlib.reload(hook)
    assert hook.HTTP_PORT is None


def test_hook_http_port_default_windows(monkeypatch):
    monkeypatch.delenv("FORGEMEMO_HTTP_PORT", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    importlib.reload(hook)
    assert hook.HTTP_PORT == "5555"


def test_http_port_env_overrides_windows_default(monkeypatch):
    monkeypatch.setenv("FORGEMEMO_HTTP_PORT", "9999")
    monkeypatch.setattr(sys, "platform", "win32")
    importlib.reload(mcp_server)
    assert mcp_server.HTTP_PORT == "9999"


# ---------------------------------------------------------------------------
# hook._post_event: socket skipped on Windows, HTTP used
# ---------------------------------------------------------------------------

def test_post_event_skips_socket_on_windows(monkeypatch):
    """On Windows, _post_event must not attempt unix socket — HTTP only."""
    http_calls = []

    def fake_post(url, json=None, timeout=None):
        http_calls.append(url)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hook, "DAEMON_URL", None)
    monkeypatch.setattr(hook, "HTTP_PORT", "5555")
    monkeypatch.setattr(hook.requests, "post", fake_post)

    event = {
        "session_id": "s1", "project_id": "/p", "source_tool": "test",
        "event_type": "evt", "tool_name": None, "payload": "{}", "seq": 1,
    }
    hook._post_event(event)

    assert len(http_calls) == 1
    assert http_calls[0] == "http://127.0.0.1:5555/events"


def test_post_event_uses_socket_on_posix(monkeypatch):
    """On POSIX, _post_event should attempt unix socket first."""
    socket_calls = []

    class _FakeSession:
        def post(self, url, json=None, timeout=None):
            socket_calls.append(url)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(hook, "DAEMON_URL", None)
    monkeypatch.setattr(hook, "HTTP_PORT", None)

    # Patch requests_unixsocket at the hook module level via monkeypatching the import
    import unittest.mock as mock
    fake_module = mock.MagicMock()
    fake_module.Session.return_value = _FakeSession()
    monkeypatch.setitem(sys.modules, "requests_unixsocket", fake_module)

    event = {
        "session_id": "s1", "project_id": "/p", "source_tool": "test",
        "event_type": "evt", "tool_name": None, "payload": "{}", "seq": 1,
    }
    hook._post_event(event)

    assert len(socket_calls) == 1
    assert "http+unix://" in socket_calls[0]


# ---------------------------------------------------------------------------
# Session recall hook: _handle_session_recall
# ---------------------------------------------------------------------------

class TestSessionRecall:
    def _fake_daemon_get(self, responses: dict):
        """Return a _daemon_get replacement that returns preset responses."""
        def _get(path, params=None):
            return responses.get(path, {})
        return _get

    def test_empty_db_outputs_empty_context(self, monkeypatch, capsys):
        """Empty DB should emit hookSpecificOutput with empty additionalContext."""
        monkeypatch.setattr(hook, "_daemon_get", self._fake_daemon_get({
            "/session_summaries": {"results": []},
            "/search": {"results": []},
        }))
        monkeypatch.setattr(hook, "SOURCE_TOOL", "claude-code")
        rc = hook._handle_session_recall({"cwd": "/proj"}, "UserPromptSubmit")
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert data["hookSpecificOutput"]["additionalContext"] == ""

    def test_with_memories_outputs_additional_context_claude(self, monkeypatch, capsys):
        monkeypatch.setattr(hook, "_daemon_get", self._fake_daemon_get({
            "/session_summaries": {"results": [
                {"ts": "2026-01-01T00:00:00", "request": "Fix auth bug", "learnings": "JWT expiry was wrong"}
            ]},
            "/search": {"results": []},
        }))
        monkeypatch.setattr(hook, "SOURCE_TOOL", "claude-code")
        hook._handle_session_recall({"cwd": "/proj"}, "UserPromptSubmit")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "hookSpecificOutput" in data
        assert data["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "Fix auth bug" in data["hookSpecificOutput"]["additionalContext"]

    def test_with_memories_outputs_additional_context_gemini(self, monkeypatch, capsys):
        monkeypatch.setattr(hook, "_daemon_get", self._fake_daemon_get({
            "/session_summaries": {"results": [
                {"ts": "2026-01-01T00:00:00", "request": "Refactor DB", "learnings": "Use transactions"}
            ]},
            "/search": {"results": []},
        }))
        monkeypatch.setattr(hook, "SOURCE_TOOL", "gemini")
        hook._handle_session_recall({"cwd": "/proj"}, "BeforeAgent")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["hookSpecificOutput"]["hookEventName"] == "BeforeAgent"
        assert "Refactor DB" in data["hookSpecificOutput"]["additionalContext"]

    def test_with_memories_outputs_system_message_codex(self, monkeypatch, capsys):
        monkeypatch.setattr(hook, "_daemon_get", self._fake_daemon_get({
            "/session_summaries": {"results": [
                {"ts": "2026-01-01T00:00:00", "request": "Add tests", "learnings": "Use pytest fixtures"}
            ]},
            "/search": {"results": []},
        }))
        monkeypatch.setattr(hook, "SOURCE_TOOL", "codex")
        hook._handle_session_recall({"cwd": "/proj"}, "UserPromptSubmit")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "systemMessage" in data
        assert "hookSpecificOutput" not in data
        assert "Add tests" in data["systemMessage"]

    def test_search_results_included(self, monkeypatch, capsys):
        monkeypatch.setattr(hook, "_daemon_get", self._fake_daemon_get({
            "/session_summaries": {"results": []},
            "/search": {"results": [
                {"title": "Auth pattern", "narrative": "Always validate JWT on the server side"}
            ]},
        }))
        monkeypatch.setattr(hook, "SOURCE_TOOL", "claude-code")
        hook._handle_session_recall({"cwd": "/proj"}, "UserPromptSubmit")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "Auth pattern" in data["hookSpecificOutput"]["additionalContext"]

    def test_daemon_failure_outputs_empty_context(self, monkeypatch, capsys):
        """If daemon is unreachable, gracefully emit empty context (not an error)."""
        monkeypatch.setattr(hook, "_daemon_get", self._fake_daemon_get({}))
        monkeypatch.setattr(hook, "SOURCE_TOOL", "claude-code")
        rc = hook._handle_session_recall({"cwd": "/proj"}, "UserPromptSubmit")
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data["hookSpecificOutput"]["additionalContext"] == ""


# ---------------------------------------------------------------------------
# Session end hook: _handle_session_end
# ---------------------------------------------------------------------------

class TestSessionEnd:
    def test_spawns_background_process(self, monkeypatch, capsys):
        spawned = []

        def fake_popen(cmd, **kwargs):
            spawned.append(cmd)

        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", lambda _: "/usr/bin/forgememo")
        monkeypatch.setattr(hook.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(sys, "platform", "linux")

        rc = hook._handle_session_end({"session_id": "abc", "cwd": "/proj"})
        out = capsys.readouterr().out
        assert rc == 0
        assert json.loads(out) == {}
        assert len(spawned) == 1
        assert "end-session" in spawned[0]
        assert "abc" in spawned[0]

    def test_missing_forgememo_bin_returns_empty_json(self, monkeypatch, capsys):
        """No forgememo binary → return {} (Claude Code ignores empty JSON)."""
        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", lambda _: None)
        rc = hook._handle_session_end({"session_id": "abc", "cwd": "/proj"})
        out = capsys.readouterr().out
        assert rc == 0
        assert json.loads(out) == {}

    def test_popen_failure_returns_empty_json(self, monkeypatch, capsys):
        """Popen failure → swallow exception and return {} gracefully."""
        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", lambda _: "/usr/bin/forgememo")
        monkeypatch.setattr(hook.subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(OSError("no such file")))
        monkeypatch.setattr(sys, "platform", "linux")
        rc = hook._handle_session_end({"session_id": "abc", "cwd": "/proj"})
        out = capsys.readouterr().out
        assert rc == 0
        assert json.loads(out) == {}


# ---------------------------------------------------------------------------
# main() dispatch: session event routing
# ---------------------------------------------------------------------------

class TestMainDispatch:
    """Verify main() routes session events to the correct handler."""

    def test_user_prompt_submit_dispatched_to_recall(self, monkeypatch, capsys):
        recalled = []
        monkeypatch.setattr(hook, "_handle_session_recall",
                            lambda p, e: recalled.append(e) or 0)
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO('{"cwd":"/p"}'))
        import unittest.mock as mock
        with mock.patch("sys.argv", ["hook.py", "UserPromptSubmit"]):
            rc = hook.main()
        assert rc == 0
        assert recalled == ["UserPromptSubmit"]

    def test_stop_dispatched_to_session_end(self, monkeypatch, capsys):
        ended = []
        monkeypatch.setattr(hook, "_handle_session_end",
                            lambda p: ended.append(True) or 0)
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO('{"cwd":"/p"}'))
        import unittest.mock as mock
        with mock.patch("sys.argv", ["hook.py", "Stop"]):
            rc = hook.main()
        assert rc == 0
        assert ended == [True]

    def test_pre_tool_use_dispatched_to_post_event(self, monkeypatch, capsys):
        posted = []
        monkeypatch.setattr(hook, "_post_event", lambda e: posted.append(e))
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(
            '{"session_id":"s","project_id":"/p","source_tool":"t","seq":1}'))
        import unittest.mock as mock
        with mock.patch("sys.argv", ["hook.py", "PreToolUse"]):
            rc = hook.main()
        assert rc == 0
        assert len(posted) == 1
