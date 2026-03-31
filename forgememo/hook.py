#!/usr/bin/env python3
"""
Forgememo hook adapter — normalize tool events and POST to daemon.

Usage:
  echo '{...}' | python forgememo/hook.py post_tool_use
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import requests


DAEMON_URL = os.environ.get("FORGEMEMO_DAEMON_URL")
SOCKET_PATH = os.environ.get("FORGEMEMO_SOCKET", "/tmp/forgememo.sock")
HTTP_PORT = os.environ.get("FORGEMEMO_HTTP_PORT")
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

    # Socket-first (requests-unixsocket if available)
    if not DAEMON_URL:
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
    event = _normalize_event(event_name, payload)
    _post_event(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
