#!/usr/bin/env python3
"""
Forgememo hook adapter — normalize tool events and POST to daemon.

Usage:
  echo '{...}' | python forgememo/hook.py post_tool_use
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any

import requests


DAEMON_URL = os.environ.get("FORGEMEMO_DAEMON_URL")
SOCKET_PATH = os.environ.get("FORGEMEMO_SOCKET", os.path.join(tempfile.gettempdir(), "forgememo.sock"))
HTTP_PORT = os.environ.get("FORGEMEMO_HTTP_PORT", "5555" if sys.platform == "win32" else None)
SOURCE_TOOL = os.environ.get("FORGEMEMO_SOURCE_TOOL", "unknown")

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


def _read_stdin_json() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def _resolve_project_id(payload: dict) -> str:
    override = os.environ.get("FORGEMEMO_PROJECT_ID")
    if override:
        return override
    if payload.get("project_id"):
        return str(payload["project_id"])
    if payload.get("cwd"):
        return os.path.realpath(str(payload["cwd"]))
    return os.path.realpath(os.getcwd())


def _normalize_event(event_name: str, payload: dict) -> dict:
    session_id = payload.get("session_id") or payload.get("session") or "unknown"
    project_id = _resolve_project_id(payload)
    event_type = payload.get("hook_event_name") or event_name
    tool_name = payload.get("tool_name")
    source_tool = payload.get("source_tool") or SOURCE_TOOL
    seq = payload.get("seq") or payload.get("sequence")
    if seq is None:
        seq = int(time.time() * 1000)

    event = {
        "session_id": session_id,
        "project_id": project_id,
        "source_tool": source_tool,
        "event_type": event_type,
        "tool_name": tool_name,
        "payload": payload,
        "seq": int(seq),
    }
    return strip_private(event)


def _post_event(event: dict) -> None:
    # Daemon expects payload as JSON string
    event = dict(event)
    event["payload"] = json.dumps(event["payload"])

    # Socket-first (requests-unixsocket if available, POSIX only)
    if not DAEMON_URL and sys.platform != "win32":
        try:
            import requests_unixsocket

            session = requests_unixsocket.Session()
            socket_url = "http+unix://" + SOCKET_PATH.replace("/", "%2F")
            session.post(f"{socket_url}/events", json=event, timeout=1.5)
            return
        except Exception:
            pass

    # Fallback HTTP
    try:
        if DAEMON_URL:
            url = DAEMON_URL.rstrip("/")
        elif HTTP_PORT:
            url = f"http://127.0.0.1:{HTTP_PORT}"
        else:
            return
        requests.post(f"{url}/events", json=event, timeout=1.5)
    except Exception:
        # Hook must never crash the host process
        pass


def _daemon_get(path: str, params: dict | None = None) -> dict:
    """GET from daemon — never raises (hook must not crash host process)."""
    if not DAEMON_URL and sys.platform != "win32":
        try:
            import requests_unixsocket
            session = requests_unixsocket.Session()
            socket_url = "http+unix://" + SOCKET_PATH.replace("/", "%2F")
            resp = session.get(f"{socket_url}{path}", params=params, timeout=3)
            if resp.ok:
                return resp.json()
        except Exception:
            pass
    try:
        if DAEMON_URL:
            url = DAEMON_URL.rstrip("/")
        elif HTTP_PORT:
            url = f"http://127.0.0.1:{HTTP_PORT}"
        else:
            return {}
        resp = requests.get(f"{url}{path}", params=params, timeout=3)
        return resp.json() if resp.ok else {}
    except Exception:
        return {}


def _format_context_json(text: str, event_name: str) -> str:
    """Return platform-appropriate JSON for context injection."""
    if SOURCE_TOOL in ("claude-code", "gemini"):
        return json.dumps({
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": text,
            }
        })
    return json.dumps({"systemMessage": text})


def _handle_session_recall(payload: dict, event_name: str) -> int:
    """Fetch recent memories and inject them as context (stdout JSON)."""
    project_id = _resolve_project_id(payload)
    summaries = _daemon_get("/session_summaries", {"project_id": project_id, "k": 2})
    search = _daemon_get("/search", {"q": "recent", "project_id": project_id, "k": 5})

    parts = []
    for s in summaries.get("results", []):
        ts = (s.get("ts") or "")[:10]
        parts.append(f"[Session {ts}] {s.get('request', '')} — {s.get('learnings', '')}")
    for r in search.get("results", []):
        narrative = (r.get("narrative") or "")[:120]
        parts.append(f"[Memory] {r.get('title', '')}: {narrative}")

    if not parts:
        print(_format_context_json("", event_name))
        return 0

    context = "Forgememo context from previous sessions:\n" + "\n".join(parts)
    print(_format_context_json(context, event_name))
    return 0


def _handle_session_end(payload: dict) -> int:
    """Spawn background end-session synthesis; return immediately."""
    session_id = payload.get("session_id") or ""
    cwd = payload.get("cwd") or os.getcwd()
    import shutil as _shutil
    forgememo_bin = _shutil.which("forgememo")
    if not forgememo_bin:
        print(json.dumps({}))
        return 0
    cmd = [forgememo_bin, "end-session", "--session-id", session_id, "--project-dir", cwd]
    try:
        if sys.platform == "win32":
            env = {**os.environ, "FORGEMEMO_HTTP_PORT": HTTP_PORT or "5555"}
            subprocess.Popen(
                cmd, env=env,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            subprocess.Popen(
                cmd, start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass
    print(json.dumps({}))
    return 0


# ---------------------------------------------------------------------------
# Event dispatch tables
# ---------------------------------------------------------------------------

_SESSION_RECALL_EVENTS = {
    "UserPromptSubmit",  # Claude Code, Codex
    "BeforeAgent",       # Gemini
    "sessionStart",      # Copilot
    "session.created",   # OpenCode
    "SessionStart",      # generic
}

_SESSION_END_EVENTS = {
    "Stop",              # Claude Code, Codex
    "SessionEnd",        # Claude Code, Gemini
    "AfterAgent",        # Gemini (per-turn fallback)
    "agentStop",         # Copilot
    "session.idle",      # OpenCode (agent finished)
    "session.deleted",   # OpenCode (session closed)
}


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python forgememo/hook.py <event_name>", file=sys.stderr)
        return 2
    event_name = sys.argv[1]
    try:
        payload = _read_stdin_json()
    except Exception as e:
        print(f"Invalid JSON payload: {e}", file=sys.stderr)
        return 1

    if event_name in _SESSION_RECALL_EVENTS:
        return _handle_session_recall(payload, event_name)
    if event_name in _SESSION_END_EVENTS:
        return _handle_session_end(payload)

    event = _normalize_event(event_name, payload)
    _post_event(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
