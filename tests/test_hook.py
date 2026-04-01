"""
Tests for the hook adapter (forgememo/hook.py).

Covers:
- strip_private: strings, dicts, lists, nested structures
- _normalize_event: field mapping, fallbacks
- _resolve_project_id: env var override, cwd fallback
"""

from __future__ import annotations

import os

from unittest.mock import MagicMock, patch

from forgememo.hook import (
    strip_private,
    _normalize_event,
    _resolve_project_id,
    _ensure_daemon,
    _handle_post_tool_use,
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
            if call_count["n"] <= 2:
                raise _requests.exceptions.ConnectionError("refused")
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with patch("forgememo.hook.requests.get", side_effect=fake_get), \
             patch("forgememo.hook.subprocess.Popen") as mock_popen, \
             patch("forgememo.hook.time.sleep"):
            result = _ensure_daemon()

        assert result is True
        mock_popen.assert_called_once()

    def test_returns_false_when_daemon_never_starts(self, monkeypatch):
        import requests as _requests

        with patch("forgememo.hook.requests.get",
                   side_effect=_requests.exceptions.ConnectionError("refused")), \
             patch("forgememo.hook.subprocess.Popen"), \
             patch("forgememo.hook.time.sleep"):
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
            {"tool_name": "Edit", "session_id": "s1", "project_id": "/tmp"}, "PostToolUse"
        )
        assert len(posted) == 1
        assert posted[0]["tool_name"] == "Edit"

    def test_read_tool_is_skipped(self, monkeypatch):
        posted = []
        monkeypatch.setattr("forgememo.hook._post_event", lambda e: posted.append(e))
        for read_tool in ("Read", "Grep", "Glob", "WebSearch", "WebFetch"):
            _handle_post_tool_use({"tool_name": read_tool, "session_id": "s1"}, "PostToolUse")
        assert len(posted) == 0

    def test_all_write_tools_captured(self, monkeypatch):
        posted = []
        monkeypatch.setattr("forgememo.hook._post_event", lambda e: posted.append(e))
        for tool in _WRITE_TOOL_NAMES:
            _handle_post_tool_use({"tool_name": tool, "session_id": "s1"}, "PostToolUse")
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


