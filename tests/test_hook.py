"""
Tests for the hook adapter (forgememo/hook.py).

Covers:
- strip_private: strings, dicts, lists, nested structures
- _normalize_event: field mapping, fallbacks
- _resolve_project_id: env var override, cwd fallback
- _format_context_json: per-tool output format
- _read_stdin_json: empty / valid input
- _post_event: transport selection, DAEMON_URL, exception swallowing
- _daemon_get: HTTP, socket, error paths
- _handle_session_recall: memories, daemon-down, narrative truncation
- _handle_session_end: POSIX / Windows, missing binary, missing cwd
- main(): error exits, cross-agent event dispatch
"""

from __future__ import annotations

import io
import json
import os
import sys

import pytest
from unittest.mock import MagicMock, patch

import forgememo.hook as hook
from forgememo.hook import (
    strip_private,
    _normalize_event,
    _resolve_project_id,
    _ensure_daemon,
    _format_context_json,
    _handle_post_tool_use,
    _handle_session_recall,
    _handle_session_end,
    _read_stdin_json,
    _post_event,
    _daemon_get,
    _SESSION_RECALL_EVENTS,
    _SESSION_END_EVENTS,
    _POST_TOOL_USE_EVENTS,
    _WRITE_TOOL_NAMES,
)


# ---------------------------------------------------------------------------
# strip_private
# ---------------------------------------------------------------------------


class TestStripPrivate:
    def test_removes_private_tag(self):
        result = strip_private("hello <private>SECRET</private> world")
        assert "SECRET" not in result
        assert "hello" in result
        assert "world" in result

    def test_removes_multiline_private(self):
        text = "before <private>\nline1\nline2\n</private> after"
        result = strip_private(text)
        assert "line1" not in result
        assert "before" in result
        assert "after" in result

    def test_case_insensitive(self):
        result = strip_private("a <PRIVATE>secret</PRIVATE> b")
        assert "secret" not in result

    def test_no_private_tag_unchanged(self):
        text = "nothing special here"
        assert strip_private(text) == text

    def test_dict_values_stripped(self):
        d = {"key": "value <private>hidden</private> end", "other": "clean"}
        result = strip_private(d)
        assert "hidden" not in result["key"]
        assert result["other"] == "clean"

    def test_dict_keys_not_stripped(self):
        d = {"<private>key</private>": "value"}
        result = strip_private(d)
        # Keys are not recursed into — only values
        assert "<private>key</private>" in result

    def test_list_items_stripped(self):
        lst = ["clean", "has <private>secret</private> end", "also clean"]
        result = strip_private(lst)
        assert "secret" not in result[1]
        assert result[0] == "clean"
        assert result[2] == "also clean"

    def test_nested_dict_stripped(self):
        d = {"outer": {"inner": "x <private>hidden</private> y"}}
        result = strip_private(d)
        assert "hidden" not in result["outer"]["inner"]

    def test_nested_list_in_dict_stripped(self):
        d = {"items": ["a", "<private>b</private>", "c"]}
        result = strip_private(d)
        assert result["items"][1] == ""

    def test_non_string_passthrough(self):
        assert strip_private(42) == 42
        assert strip_private(3.14) == 3.14
        assert strip_private(None) is None
        assert strip_private(True) is True

    def test_multiple_private_blocks(self):
        text = "a <private>x</private> b <private>y</private> c"
        result = strip_private(text)
        assert "x" not in result
        assert "y" not in result
        assert "a" in result
        assert "b" in result
        assert "c" in result

    def test_empty_private_block(self):
        result = strip_private("before <private></private> after")
        assert result.strip() in ("before  after", "before after")


# ---------------------------------------------------------------------------
# _resolve_project_id
# ---------------------------------------------------------------------------


class TestResolveProjectId:
    def test_env_var_overrides_all(self, monkeypatch):
        monkeypatch.setenv("FORGEMEMO_PROJECT_ID", "/override/project")
        result = _resolve_project_id({"cwd": "/some/cwd", "project_id": "inline"})
        assert result == "/override/project"

    def test_payload_project_id_used(self, monkeypatch):
        monkeypatch.delenv("FORGEMEMO_PROJECT_ID", raising=False)
        result = _resolve_project_id({"project_id": "myproject"})
        assert result == "myproject"

    def test_cwd_used_as_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FORGEMEMO_PROJECT_ID", raising=False)
        result = _resolve_project_id({"cwd": str(tmp_path)})
        assert result == str(tmp_path.resolve())

    def test_getcwd_used_when_no_hints(self, monkeypatch):
        monkeypatch.delenv("FORGEMEMO_PROJECT_ID", raising=False)
        result = _resolve_project_id({})
        assert result == os.path.realpath(os.getcwd())

    def test_env_var_takes_precedence_over_cwd(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FORGEMEMO_PROJECT_ID", "/env/project")
        result = _resolve_project_id({"cwd": str(tmp_path)})
        assert result == "/env/project"


# ---------------------------------------------------------------------------
# _normalize_event
# ---------------------------------------------------------------------------


class TestNormalizeEvent:
    def setup_method(self):
        os.environ.pop("FORGEMEMO_PROJECT_ID", None)

    def _payload(self, **overrides):
        base = {
            "session_id": "sess-abc",
            "project_id": "/tmp/proj",
            "source_tool": "claude",
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "seq": 10,
        }
        base.update(overrides)
        return base

    def test_session_id_mapped(self):
        event = _normalize_event("PostToolUse", self._payload())
        assert event["session_id"] == "sess-abc"

    def test_event_type_from_hook_event_name(self):
        event = _normalize_event("PostToolUse", self._payload())
        assert event["event_type"] == "PostToolUse"

    def test_event_type_fallback_to_arg(self):
        payload = self._payload()
        del payload["hook_event_name"]
        event = _normalize_event("my_event", payload)
        assert event["event_type"] == "my_event"

    def test_tool_name_mapped(self):
        event = _normalize_event("PostToolUse", self._payload())
        assert event["tool_name"] == "Edit"

    def test_seq_mapped(self):
        event = _normalize_event("PostToolUse", self._payload())
        assert event["seq"] == 10

    def test_seq_defaults_to_timestamp_when_missing(self):
        payload = self._payload()
        del payload["seq"]
        event = _normalize_event("PostToolUse", payload)
        assert isinstance(event["seq"], int)
        assert event["seq"] > 0

    def test_source_tool_from_payload(self):
        event = _normalize_event("PostToolUse", self._payload(source_tool="codex"))
        assert event["source_tool"] == "codex"

    def test_source_tool_fallback_to_env(self, monkeypatch):
        monkeypatch.setenv("FORGEMEMO_SOURCE_TOOL", "gemini")
        payload = self._payload()
        del payload["source_tool"]
        import importlib
        import forgememo.hook as hook_module

        importlib.reload(hook_module)
        event = hook_module._normalize_event("PostToolUse", payload)
        assert event["source_tool"] == "gemini"

    def test_private_stripped_in_normalized_event(self):
        payload = self._payload()
        payload["secret"] = "<private>token=abc123</private>"
        event = _normalize_event("PostToolUse", payload)
        assert "abc123" not in str(event)

    def test_unknown_session_fallback(self):
        payload = self._payload()
        del payload["session_id"]
        event = _normalize_event("PostToolUse", payload)
        assert event["session_id"] == "unknown"


# ---------------------------------------------------------------------------
# _ensure_daemon
# ---------------------------------------------------------------------------


class TestEnsureDaemon:
    def test_returns_true_when_daemon_healthy(self, monkeypatch):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("forgememo.hook.requests.get", return_value=mock_resp) as mock_get:
            result = _ensure_daemon()
        assert result is True
        mock_get.assert_called_once()

    def test_spawns_subprocess_and_polls_when_daemon_down(self, monkeypatch):
        import requests as _requests

        call_count = {"n": 0}

        def fake_get(url, timeout=1):
            call_count["n"] += 1
            if call_count["n"] <= 1:
                raise _requests.exceptions.ConnectionError("refused")
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with (
            patch("forgememo.hook.requests.get", side_effect=fake_get),
            patch("forgememo.hook.subprocess.Popen") as mock_popen,
            patch("forgememo.daemon.wait_for_port", return_value=True),
        ):
            result = _ensure_daemon()

        assert result is True
        mock_popen.assert_called_once()

    def test_returns_false_when_daemon_never_starts(self, monkeypatch):
        import requests as _requests

        with (
            patch(
                "forgememo.hook.requests.get",
                side_effect=_requests.exceptions.ConnectionError("refused"),
            ),
            patch("forgememo.hook.subprocess.Popen"),
            patch("forgememo.daemon.wait_for_port", return_value=False),
        ):
            result = _ensure_daemon()

        assert result is False


# ---------------------------------------------------------------------------
# _handle_post_tool_use
# ---------------------------------------------------------------------------


class TestPostToolUseHook:
    def test_write_tool_is_posted(self, monkeypatch):
        posted = []
        monkeypatch.setattr("forgememo.hook._post_event", lambda e: posted.append(e))
        _handle_post_tool_use(
            {"tool_name": "Edit", "session_id": "s1", "project_id": "/tmp"},
            "PostToolUse",
        )
        assert len(posted) == 1
        assert posted[0]["tool_name"] == "Edit"

    def test_read_tool_is_skipped(self, monkeypatch):
        posted = []
        monkeypatch.setattr("forgememo.hook._post_event", lambda e: posted.append(e))
        for read_tool in ("Read", "Grep", "Glob", "WebSearch", "WebFetch"):
            _handle_post_tool_use(
                {"tool_name": read_tool, "session_id": "s1"}, "PostToolUse"
            )
        assert len(posted) == 0

    def test_all_write_tools_captured(self, monkeypatch):
        posted = []
        monkeypatch.setattr("forgememo.hook._post_event", lambda e: posted.append(e))
        for tool in _WRITE_TOOL_NAMES:
            _handle_post_tool_use(
                {"tool_name": tool, "session_id": "s1"}, "PostToolUse"
            )
        assert len(posted) == len(_WRITE_TOOL_NAMES)

    def test_unknown_tool_is_skipped(self, monkeypatch):
        posted = []
        monkeypatch.setattr("forgememo.hook._post_event", lambda e: posted.append(e))
        _handle_post_tool_use({"tool_name": "", "session_id": "s1"}, "PostToolUse")
        _handle_post_tool_use({"session_id": "s1"}, "PostToolUse")
        assert len(posted) == 0

    def test_private_content_stripped(self, monkeypatch):
        posted = []
        monkeypatch.setattr("forgememo.hook._post_event", lambda e: posted.append(e))
        _handle_post_tool_use(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo <private>secret</private>"},
                "session_id": "s1",
            },
            "PostToolUse",
        )
        assert len(posted) == 1
        assert "secret" not in str(posted[0])

    def test_gemini_aftertool_variant(self, monkeypatch):
        posted = []
        monkeypatch.setattr("forgememo.hook._post_event", lambda e: posted.append(e))
        _handle_post_tool_use({"tool_name": "Write", "session_id": "s1"}, "AfterTool")
        assert len(posted) == 1


# ---------------------------------------------------------------------------
# _format_context_json
# ---------------------------------------------------------------------------


class TestFormatContextJson:
    def test_claude_code_uses_hook_specific_output(self, monkeypatch):
        monkeypatch.setattr(hook, "SOURCE_TOOL", "claude-code")
        result = json.loads(_format_context_json("hello", "UserPromptSubmit"))
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert result["hookSpecificOutput"]["additionalContext"] == "hello"

    def test_gemini_uses_hook_specific_output(self, monkeypatch):
        monkeypatch.setattr(hook, "SOURCE_TOOL", "gemini")
        result = json.loads(_format_context_json("ctx", "BeforeAgent"))
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["hookEventName"] == "BeforeAgent"
        assert result["hookSpecificOutput"]["additionalContext"] == "ctx"

    def test_codex_uses_system_message(self, monkeypatch):
        monkeypatch.setattr(hook, "SOURCE_TOOL", "codex")
        result = json.loads(_format_context_json("msg", "UserPromptSubmit"))
        assert "systemMessage" in result
        assert "hookSpecificOutput" not in result
        assert result["systemMessage"] == "msg"

    def test_unknown_tool_uses_system_message(self, monkeypatch):
        monkeypatch.setattr(hook, "SOURCE_TOOL", "opencode")
        result = json.loads(_format_context_json("msg", "session.created"))
        assert "systemMessage" in result

    def test_empty_text_embedded(self, monkeypatch):
        monkeypatch.setattr(hook, "SOURCE_TOOL", "claude-code")
        result = json.loads(_format_context_json("", "SessionStart"))
        assert result["hookSpecificOutput"]["additionalContext"] == ""


# ---------------------------------------------------------------------------
# _read_stdin_json
# ---------------------------------------------------------------------------


class TestReadStdinJson:
    def test_valid_json_parsed(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"key": "val"}'))
        result = _read_stdin_json()
        assert result == {"key": "val"}

    def test_empty_string_returns_empty_dict(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        assert _read_stdin_json() == {}

    def test_whitespace_only_returns_empty_dict(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("   \n  "))
        assert _read_stdin_json() == {}

    def test_nested_json_parsed(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"a":{"b":1}}'))
        result = _read_stdin_json()
        assert result["a"]["b"] == 1


# ---------------------------------------------------------------------------
# _post_event: additional transport coverage
# ---------------------------------------------------------------------------


class TestPostEventTransport:
    def _event(self):
        return {
            "session_id": "s1",
            "project_id": "/p",
            "source_tool": "test",
            "event_type": "evt",
            "tool_name": None,
            "payload": {},
            "seq": 1,
        }

    def test_daemon_url_override_used(self, monkeypatch):
        calls = []
        monkeypatch.setattr(hook, "DAEMON_URL", "http://remote:8080")
        monkeypatch.setattr(
            hook.requests,
            "post",
            lambda url, json=None, timeout=None: calls.append(url),
        )
        _post_event(self._event())
        assert len(calls) == 1
        assert calls[0] == "http://remote:8080/events"

    def test_http_exception_swallowed(self, monkeypatch):
        monkeypatch.setattr(hook, "DAEMON_URL", None)
        monkeypatch.setattr(hook, "_http_port", lambda: "5555")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            hook.requests,
            "post",
            lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("refused")),
        )
        # Must not raise
        _post_event(self._event())

    def test_no_daemon_url_uses_discovered_port(self, monkeypatch):
        """With no DAEMON_URL, _post_event posts to the discovered port."""
        calls = []
        monkeypatch.setattr(hook, "DAEMON_URL", None)
        monkeypatch.setattr(hook, "_http_port", lambda: "5555")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(hook.requests, "post", lambda *a, **kw: calls.append(a[0]))
        _post_event(self._event())
        assert len(calls) == 1
        assert "5555" in calls[0]

    def test_payload_serialized_as_json_string(self, monkeypatch):
        captured = []
        monkeypatch.setattr(hook, "DAEMON_URL", None)
        monkeypatch.setattr(hook, "_http_port", lambda: "5555")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            hook.requests,
            "post",
            lambda url, json=None, timeout=None: captured.append(json),
        )
        evt = self._event()
        evt["payload"] = {"key": "val"}
        _post_event(evt)
        assert len(captured) == 1
        # payload must arrive as a JSON string, not a dict
        assert isinstance(captured[0]["payload"], str)
        assert json.loads(captured[0]["payload"]) == {"key": "val"}

    def test_posix_socket_failure_falls_back_to_http(self, monkeypatch):
        http_calls = []

        class _BrokenSession:
            def post(self, *a, **kw):
                raise OSError("no socket")

        fake_module = MagicMock()
        fake_module.Session.return_value = _BrokenSession()
        monkeypatch.setitem(sys.modules, "requests_unixsocket", fake_module)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(hook, "DAEMON_URL", None)
        monkeypatch.setattr(hook, "_http_port", lambda: "5555")
        monkeypatch.setattr(
            hook.requests,
            "post",
            lambda url, json=None, timeout=None: http_calls.append(url),
        )
        _post_event(self._event())
        assert len(http_calls) == 1
        assert "5555" in http_calls[0]


# ---------------------------------------------------------------------------
# _daemon_get
# ---------------------------------------------------------------------------


class TestDaemonGet:
    def test_http_success_returns_json(self, monkeypatch):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"results": []}
        monkeypatch.setattr(hook, "DAEMON_URL", None)
        monkeypatch.setattr(hook, "_http_port", lambda: "5555")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            hook.requests, "get", lambda url, params=None, timeout=None: mock_resp
        )
        result = _daemon_get("/search", {"q": "test"})
        assert result == {"results": []}

    def test_http_non_ok_returns_empty_dict(self, monkeypatch):
        mock_resp = MagicMock()
        mock_resp.ok = False
        monkeypatch.setattr(hook, "DAEMON_URL", None)
        monkeypatch.setattr(hook, "_http_port", lambda: "5555")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            hook.requests, "get", lambda url, params=None, timeout=None: mock_resp
        )
        assert _daemon_get("/search") == {}

    def test_http_exception_returns_empty_dict(self, monkeypatch):
        monkeypatch.setattr(hook, "DAEMON_URL", None)
        monkeypatch.setattr(hook, "_http_port", lambda: "5555")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            hook.requests,
            "get",
            lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("down")),
        )
        assert _daemon_get("/search") == {}

    def test_daemon_url_override(self, monkeypatch):
        calls = []
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"x": 1}

        def fake_get(url, params=None, timeout=None):
            calls.append(url)
            return mock_resp

        monkeypatch.setattr(hook, "DAEMON_URL", "http://remote:9000")
        monkeypatch.setattr(hook.requests, "get", fake_get)
        result = _daemon_get("/session_summaries")
        assert result == {"x": 1}
        assert calls[0].startswith("http://remote:9000")

    def test_no_url_falls_back_to_discovered_port(self, monkeypatch):
        """With no DAEMON_URL, _daemon_get uses the discovered port (returns {} on failure)."""
        monkeypatch.setattr(hook, "DAEMON_URL", None)
        monkeypatch.setattr(hook, "_http_port", lambda: "19997")  # nothing listening
        monkeypatch.setattr(sys, "platform", "win32")
        assert _daemon_get("/search") == {}

    def test_posix_socket_success(self, monkeypatch):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"results": ["a"]}

        class _FakeSession:
            def get(self, url, params=None, timeout=None):
                return mock_resp

        fake_module = MagicMock()
        fake_module.Session.return_value = _FakeSession()
        monkeypatch.setitem(sys.modules, "requests_unixsocket", fake_module)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(hook, "DAEMON_URL", None)
        result = _daemon_get("/search")
        assert result == {"results": ["a"]}

    def test_posix_socket_failure_falls_back_to_http(self, monkeypatch):
        class _BrokenSession:
            def get(self, *a, **kw):
                raise OSError("socket dead")

        fake_module = MagicMock()
        fake_module.Session.return_value = _BrokenSession()
        monkeypatch.setitem(sys.modules, "requests_unixsocket", fake_module)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(hook, "DAEMON_URL", None)
        monkeypatch.setattr(hook, "_http_port", lambda: "5555")

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"fallback": True}
        monkeypatch.setattr(
            hook.requests, "get", lambda url, params=None, timeout=None: mock_resp
        )
        result = _daemon_get("/search")
        assert result == {"fallback": True}


# ---------------------------------------------------------------------------
# _handle_session_recall: additional coverage
# ---------------------------------------------------------------------------


class TestSessionRecallAdditional:
    @pytest.fixture(autouse=True)
    def _daemon_up(self, monkeypatch):
        monkeypatch.setattr(hook, "_ensure_daemon", lambda: True)

    def test_narrative_truncated_at_120_chars(self, monkeypatch, capsys):
        long_narrative = "x" * 200
        monkeypatch.setattr(
            hook,
            "_daemon_get",
            lambda path, params=None: (
                {"results": [{"title": "T", "narrative": long_narrative}]}
                if path == "/recent"
                else {"results": []}
            ),
        )
        monkeypatch.setattr(hook, "SOURCE_TOOL", "claude-code")
        _handle_session_recall({"cwd": "/proj"}, "SessionStart")
        out = capsys.readouterr().out
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        # narrative appears truncated — should not contain the full 200-char string
        assert long_narrative not in ctx
        assert "x" * 120 in ctx

    def test_session_field_used_as_session_id(self, monkeypatch, capsys):
        """Payload may use 'session' instead of 'session_id'."""
        monkeypatch.setattr(
            hook,
            "_daemon_get",
            lambda path, params=None: {"results": []},
        )
        monkeypatch.setattr(hook, "SOURCE_TOOL", "claude-code")
        rc = _handle_session_recall(
            {"session": "alt-session-id", "cwd": "/proj"}, "SessionStart"
        )
        assert rc == 0

    def test_multiple_summaries_all_included(self, monkeypatch, capsys):
        monkeypatch.setattr(
            hook,
            "_daemon_get",
            lambda path, params=None: (
                {
                    "results": [
                        {
                            "ts": "2026-01-01T00:00:00",
                            "request": "TaskA",
                            "learnings": "LearnA",
                        },
                        {
                            "ts": "2026-01-02T00:00:00",
                            "request": "TaskB",
                            "learnings": "LearnB",
                        },
                    ]
                }
                if path == "/session_summaries"
                else {"results": []}
            ),
        )
        monkeypatch.setattr(hook, "SOURCE_TOOL", "claude-code")
        _handle_session_recall({"cwd": "/proj"}, "SessionStart")
        out = capsys.readouterr().out
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "TaskA" in ctx
        assert "TaskB" in ctx

    def test_copilot_session_start_uses_system_message(self, monkeypatch, capsys):
        monkeypatch.setattr(
            hook,
            "_daemon_get",
            lambda path, params=None: (
                {"results": [{"ts": "2026-01-01", "request": "R", "learnings": "L"}]}
                if path == "/session_summaries"
                else {"results": []}
            ),
        )
        monkeypatch.setattr(hook, "SOURCE_TOOL", "copilot")
        _handle_session_recall({"cwd": "/proj"}, "sessionStart")
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "systemMessage" in data
        assert "hookSpecificOutput" not in data


# ---------------------------------------------------------------------------
# _handle_session_end: Windows path and missing cwd
# ---------------------------------------------------------------------------


class TestSessionEndAdditional:
    @pytest.fixture(autouse=True)
    def _daemon_up(self, monkeypatch):
        monkeypatch.setattr(hook, "_ensure_daemon", lambda: True)

    def test_windows_uses_detached_process_flags(self, monkeypatch, capsys):
        import shutil as _shutil

        spawned_kwargs = []

        def fake_popen(cmd, **kwargs):
            spawned_kwargs.append(kwargs)

        monkeypatch.setattr(_shutil, "which", lambda _: "C:\\bin\\forgememo.exe")
        monkeypatch.setattr(hook.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(hook, "_http_port", lambda: "5555")
        # On POSIX these constants don't exist; define them so the win32 branch runs
        monkeypatch.setattr(hook.subprocess, "DETACHED_PROCESS", 8, raising=False)
        monkeypatch.setattr(
            hook.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False
        )

        rc = _handle_session_end({"session_id": "s1", "cwd": "C:\\proj"})
        assert rc == 0
        assert len(spawned_kwargs) == 1
        kw = spawned_kwargs[0]
        assert kw.get("creationflags") is not None

    def test_missing_cwd_falls_back_to_getcwd(self, monkeypatch, capsys):
        import shutil as _shutil

        spawned_cmds = []

        def fake_popen(cmd, **kwargs):
            spawned_cmds.append(cmd)

        monkeypatch.setattr(_shutil, "which", lambda _: "/usr/bin/forgememo")
        monkeypatch.setattr(hook.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(sys, "platform", "linux")

        rc = _handle_session_end({"session_id": "s1"})
        assert rc == 0
        # cwd should be some path (os.getcwd()), not empty
        assert "--project-dir" in spawned_cmds[0]
        idx = spawned_cmds[0].index("--project-dir")
        assert spawned_cmds[0][idx + 1]  # non-empty

    def test_session_id_empty_string_handled(self, monkeypatch, capsys):
        import shutil as _shutil

        spawned = []
        monkeypatch.setattr(_shutil, "which", lambda _: "/usr/bin/forgememo")
        monkeypatch.setattr(
            hook.subprocess, "Popen", lambda cmd, **kw: spawned.append(cmd)
        )
        monkeypatch.setattr(sys, "platform", "linux")

        rc = _handle_session_end({"cwd": "/proj"})
        assert rc == 0
        assert len(spawned) == 1


# ---------------------------------------------------------------------------
# main(): error exits and cross-agent dispatch
# ---------------------------------------------------------------------------


class TestMainErrors:
    def test_no_argv_exits_2(self, monkeypatch):
        with patch("sys.argv", ["hook.py"]):
            rc = hook.main()
        assert rc == 2

    def test_invalid_json_exits_1(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("not-json{{{"))
        with patch("sys.argv", ["hook.py", "UserPromptSubmit"]):
            rc = hook.main()
        assert rc == 1


class TestCrossAgentDispatch:
    """Every event name in each dispatch set must route to the right handler."""

    def _run_main(self, event_name, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"cwd":"/p"}'))
        with patch("sys.argv", ["hook.py", event_name]):
            return hook.main()

    @pytest.mark.parametrize("event_name", sorted(_SESSION_RECALL_EVENTS))
    def test_session_recall_events_dispatched(self, event_name, monkeypatch):
        recalled = []
        monkeypatch.setattr(
            hook, "_handle_session_recall", lambda p, e: recalled.append(e) or 0
        )
        rc = self._run_main(event_name, monkeypatch)
        assert rc == 0
        assert recalled == [event_name]

    @pytest.mark.parametrize("event_name", sorted(_SESSION_END_EVENTS))
    def test_session_end_events_dispatched(self, event_name, monkeypatch):
        ended = []
        monkeypatch.setattr(
            hook, "_handle_session_end", lambda p: ended.append(True) or 0
        )
        rc = self._run_main(event_name, monkeypatch)
        assert rc == 0
        assert ended == [True]

    @pytest.mark.parametrize("event_name", sorted(_POST_TOOL_USE_EVENTS))
    def test_post_tool_use_events_dispatched(self, event_name, monkeypatch):
        handled = []
        monkeypatch.setattr(
            hook,
            "_handle_post_tool_use",
            lambda p, e: handled.append(e) or 0,
        )
        rc = self._run_main(event_name, monkeypatch)
        assert rc == 0
        assert handled == [event_name]

    def test_unknown_event_falls_through_to_post_event(self, monkeypatch):
        posted = []
        monkeypatch.setattr(hook, "_post_event", lambda e: posted.append(e))
        monkeypatch.setattr(
            sys, "stdin", io.StringIO('{"session_id":"s","project_id":"/p","seq":1}')
        )
        with patch("sys.argv", ["hook.py", "SomeCustomEvent"]):
            rc = hook.main()
        assert rc == 0
        assert len(posted) == 1
        assert posted[0]["event_type"] == "SomeCustomEvent"
