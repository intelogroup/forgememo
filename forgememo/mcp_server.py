#!/usr/bin/env python3
"""
Forgememo MCP Server — read-only tools that call the daemon API.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys


try:
    import requests
except ImportError:
    print("ERROR: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from fastmcp import FastMCP
except ImportError:
    print("ERROR: pip install fastmcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("forgememo")

DAEMON_URL = os.environ.get("FORGEMEMO_DAEMON_URL")
SOCKET_PATH = os.environ.get("FORGEMEMO_SOCKET", "/tmp/forgememo.sock")
HTTP_PORT = os.environ.get("FORGEMEMO_HTTP_PORT")


def _socket_session():
    try:
        import requests_unixsocket

        return requests_unixsocket.Session()
    except Exception:
        return None


@functools.lru_cache(maxsize=64)
def _resolve_project_id(workspace_root: str) -> str:
    """Canonical project ID: git root realpath, or workspace_root itself."""
    override = os.environ.get("FORGEMEMO_PROJECT_ID")
    if override:
        return override
    try:
        result = subprocess.run(
            ["git", "-C", workspace_root, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            return os.path.realpath(result.stdout.strip())
    except Exception:
        pass
    return os.path.realpath(workspace_root)


def _daemon_get(path: str, params: dict | None = None) -> dict:
    if not DAEMON_URL:
        session = _socket_session()
        if session:
            socket_url = "http+unix://" + SOCKET_PATH.replace("/", "%2F")
            resp = session.get(f"{socket_url}{path}", params=params, timeout=5)
            if not resp.ok:
                raise RuntimeError(f"daemon error {resp.status_code}: {resp.text[:200]}")
            return resp.json()
    if not DAEMON_URL and not HTTP_PORT:
        raise RuntimeError("daemon transport unavailable (no socket and no HTTP port)")
    url = DAEMON_URL.rstrip("/") if DAEMON_URL else f"http://127.0.0.1:{HTTP_PORT}"
    resp = requests.get(f"{url}{path}", params=params, timeout=5)
    if not resp.ok:
        raise RuntimeError(f"daemon error {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _daemon_post(path: str, payload: dict) -> dict:
    if not DAEMON_URL:
        session = _socket_session()
        if session:
            socket_url = "http+unix://" + SOCKET_PATH.replace("/", "%2F")
            resp = session.post(f"{socket_url}{path}", json=payload, timeout=5)
            if not resp.ok:
                raise RuntimeError(f"daemon error {resp.status_code}: {resp.text[:200]}")
            return resp.json()
    if not DAEMON_URL and not HTTP_PORT:
        raise RuntimeError("daemon transport unavailable (no socket and no HTTP port)")
    url = DAEMON_URL.rstrip("/") if DAEMON_URL else f"http://127.0.0.1:{HTTP_PORT}"
    resp = requests.post(f"{url}{path}", json=payload, timeout=5)
    if not resp.ok:
        raise RuntimeError(f"daemon error {resp.status_code}: {resp.text[:200]}")
    return resp.json()


@mcp.tool()
def search_memories(
    query: str,
    workspace_root: str,
    k: int = 10,
    type: str = None,
    concepts: list[str] = None,
) -> str:
    """Compact index. Returns IDs, titles, types, scores only.
    Call get_memory_details(ids) for full content."""
    project_id = _resolve_project_id(workspace_root)
    params = {
        "q": query,
        "k": k,
        "project_id": project_id,
    }
    if type:
        params["type"] = type
    if concepts:
        params["concepts"] = ",".join(concepts)
    data = _daemon_get("/search", params=params)
    results = data.get("results", [])
    if not results:
        return "_No memories found._"

    lines = []
    for r in results:
        score = f" | score:{r['impact_score']}" if r.get("impact_score") is not None else ""
        date = (r.get("ts") or "")[:10]
        lines.append(f"{r['id']} | {r.get('type','')} | {date}{score} | {r.get('title','')}")
    return "\n".join(lines)


@mcp.tool()
def get_memory_details(ids: list[str], workspace_root: str) -> str:
    """Full content for IDs from search_memories.
    Prefixes: d: (distilled_summaries), s: (session_summaries), c: (compat/legacy principles).
    Raises ValueError on unknown prefixes."""
    _ = _resolve_project_id(workspace_root)
    blocks = []
    for id_str in ids:
        parts = id_str.split(":", 1)
        if len(parts) != 2 or parts[0] not in {"d", "s", "c"}:
            raise ValueError(f"Unknown ID prefix in '{id_str}'. Valid: d:, s:, c:")
        prefix, raw = parts[0], parts[1]
        data = _daemon_get(f"/observation/{prefix}/{int(raw)}")
        blocks.append(json.dumps(data, indent=2))
    return "\n\n".join(blocks)


@mcp.tool()
def get_memory_timeline(
    anchor_id: str,
    workspace_root: str,
    depth_before: int = 3,
    depth_after: int = 3,
) -> str:
    """Chronological context around a distilled summary."""
    project_id = _resolve_project_id(workspace_root)
    data = _daemon_get(
        "/timeline",
        params={
            "anchor_id": anchor_id,
            "project_id": project_id,
            "depth_before": depth_before,
            "depth_after": depth_after,
        },
    )
    lines = []
    for r in data.get("timeline", []):
        date = (r.get("ts") or "")[:10]
        lines.append(f"{r['id']} | {r.get('type','')} | {date} | {r.get('title','')}")
    return "\n".join(lines)


@mcp.tool()
def save_session_summary(
    request: str,
    workspace_root: str,
    investigation: str = None,
    learnings: str = None,
    next_steps: str = None,
    concepts: list[str] = None,
    session_id: str = None,
) -> str:
    """POST to daemon /session_summaries — never writes DB directly."""
    project_id = _resolve_project_id(workspace_root)
    payload = {
        "request": request,
        "project_id": project_id,
        "source_tool": "mcp",
        "investigation": investigation,
        "learnings": learnings,
        "next_steps": next_steps,
        "concepts": concepts or [],
        "session_id": session_id,
    }
    data = _daemon_post("/session_summaries", payload)
    return f"Saved session summary s:{data.get('id')}"


@mcp.tool()
def get_session_summary(
    workspace_root: str,
    session_id: str = None,
    k: int = 3,
) -> str:
    """GET from daemon /session_summaries."""
    project_id = _resolve_project_id(workspace_root)
    params = {"project_id": project_id, "k": k}
    if session_id:
        params["session_id"] = session_id
    data = _daemon_get("/session_summaries", params=params)
    results = data.get("results", [])
    if not results:
        return "_No session summaries found._"
    return json.dumps(results, indent=2)


@mcp.tool()
def retrieve_memories(
    query: str,
    workspace_root: str,
    k: int = 5,
    type: str = None,
) -> str:
    """Deprecated alias for search_memories (still requires workspace_root)."""
    return search_memories(query=query, workspace_root=workspace_root, k=k, type=type)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
