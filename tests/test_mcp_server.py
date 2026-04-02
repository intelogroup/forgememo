from __future__ import annotations

import json

import pytest

import forgememo.mcp_server as mcp_server


def test_search_memories_formats_results(monkeypatch):
    def fake_get(_path, params=None):
        return {
            "results": [
                {
                    "id": "d:1",
                    "ts": "2026-03-31T12:00:00Z",
                    "type": "bugfix",
                    "title": "Fixed crash",
                    "impact_score": 8,
                    "project_id": "/tmp/proj",
                },
                {
                    "id": "s:2",
                    "ts": "2026-03-30T10:00:00Z",
                    "type": "summary",
                    "title": "Session recap",
                    "impact_score": None,
                    "project_id": "/tmp/proj",
                },
            ]
        }

    monkeypatch.setattr(mcp_server, "_daemon_get", fake_get)
    monkeypatch.setattr(mcp_server, "_resolve_project_id", lambda _root: "/tmp/proj")

    out = mcp_server.search_memories(query="crash", workspace_root="/tmp/proj", k=5)
    lines = out.splitlines()
    assert lines[0].startswith("d:1 | bugfix | 2026-03-31")
    assert "score:8" in lines[0]
    assert lines[1].startswith("s:2 | summary | 2026-03-30")


def test_search_memories_no_results(monkeypatch):
    monkeypatch.setattr(mcp_server, "_daemon_get", lambda _path, params=None: {"results": []})
    monkeypatch.setattr(mcp_server, "_resolve_project_id", lambda _root: "/tmp/proj")

    out = mcp_server.search_memories(query="none", workspace_root="/tmp/proj", k=5)
    assert out == "_No memories found._"


def test_get_memory_details_invalid_prefix(monkeypatch):
    monkeypatch.setattr(mcp_server, "_resolve_project_id", lambda _root: "/tmp/proj")

    with pytest.raises(ValueError):
        mcp_server.get_memory_details(ids=["x:123"], workspace_root="/tmp/proj")


def test_get_memory_details_returns_json(monkeypatch):
    def fake_get(path, params=None):
        assert path == "/observation/d/7"
        return {"id": "d:7", "title": "Example", "facts": ["a", "b"]}

    monkeypatch.setattr(mcp_server, "_daemon_get", fake_get)
    monkeypatch.setattr(mcp_server, "_resolve_project_id", lambda _root: "/tmp/proj")

    out = mcp_server.get_memory_details(ids=["d:7"], workspace_root="/tmp/proj")
    data = json.loads(out)
    assert data["id"] == "d:7"


def test_daemon_get_raises_actionable_error_on_connection_refused(monkeypatch):
    import requests as _requests

    monkeypatch.setattr(mcp_server, "DAEMON_URL", None)
    monkeypatch.setattr(mcp_server, "_http_port", lambda: "5555")
    # Disable socket path
    monkeypatch.setattr(mcp_server, "_socket_session", lambda: None)

    import sys
    monkeypatch.setattr(sys, "platform", "win32")

    def fake_get(url, params=None, timeout=5):
        raise _requests.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr(mcp_server.requests, "get", fake_get)

    with pytest.raises(RuntimeError, match="forgememo start"):
        mcp_server._daemon_get("/search", params={"q": "test"})


def test_get_memory_timeline_formats(monkeypatch):
    def fake_get(path, params=None):
        assert path == "/timeline"
        return {
            "timeline": [
                {"id": "d:1", "ts": "2026-03-31T00:00:00Z", "type": "bugfix", "title": "Fixed crash"},
                {"id": "d:2", "ts": "2026-03-30T00:00:00Z", "type": "feature", "title": "Added feature"},
            ]
        }

    monkeypatch.setattr(mcp_server, "_daemon_get", fake_get)
    monkeypatch.setattr(mcp_server, "_resolve_project_id", lambda _root: "/tmp/proj")

    out = mcp_server.get_memory_timeline(anchor_id="d:1", workspace_root="/tmp/proj", depth_before=1, depth_after=1)
    lines = out.splitlines()
    assert lines[0].startswith("d:1 | bugfix | 2026-03-31")
    assert lines[1].startswith("d:2 | feature | 2026-03-30")
