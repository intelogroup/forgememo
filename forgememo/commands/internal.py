"""Internal (hidden) commands: mcp, daemon, worker, end_session, hook."""

from __future__ import annotations

import json
import os

import typer

from forgememo.commands._shared import _write_project_context, console


def mcp_cmd(http: bool = typer.Option(False)):
    """Run the MCP server (stdio only in v0.1)."""
    if http:
        console.print(
            "[yellow]warning:[/] HTTP mode not supported in v0.1. Running stdio.",
            err=True,
        )
    from forgememo import mcp_server

    mcp_server.mcp.run()


def daemon_cmd():
    """Run the daemon API server (socket-first)."""
    from forgememo import daemon as _daemon

    _daemon.main()


def worker_cmd():
    """Run the background distillation worker."""
    from forgememo import worker as _worker

    _worker.main()


def end_session(
    session_id: str = typer.Option("", "--session-id", help="Session ID from the Stop hook"),
    project_dir: str = typer.Option("", "--project-dir", help="Working directory of the session"),
):
    """Synthesize and save a session summary (spawned by Stop/SessionEnd hook)."""
    import requests as _req
    from forgememo import inference

    cwd = project_dir or os.getcwd()
    project_id = os.path.realpath(cwd)
    _daemon_url = os.environ.get("FORGEMEMO_DAEMON_URL")
    _http_port = os.environ.get("FORGEMEMO_HTTP_PORT", "5555")
    url_base = _daemon_url.rstrip("/") if _daemon_url else f"http://127.0.0.1:{_http_port}"

    try:
        resp = _req.get(
            f"{url_base}/search",
            params={"q": "recent", "project_id": project_id, "k": 10},
            timeout=5,
        )
        raw = resp.json().get("results", {}) if resp.ok else {}
        if isinstance(raw, dict):
            results = raw.get("principles", []) + raw.get("traces", [])
        else:
            results = raw
    except Exception:
        return

    if len(results) < 2:
        return

    def _snippet(r: dict) -> str:
        text = r.get("narrative") or r.get("principle") or r.get("content") or ""
        title = r.get("title") or r.get("type") or ""
        return f"- [{r.get('type', '')}] {title}: {text[:200]}"

    snippets = "\n".join(_snippet(r) for r in results)
    prompt = (
        "You are summarizing a coding session. Based on these observations:\n"
        f"{snippets}\n\n"
        "Return JSON only:\n"
        '{\n'
        '  "request": "1-line summary of the session goal",\n'
        '  "investigation": "what was explored or investigated",\n'
        '  "learnings": "key learnings and discoveries",\n'
        '  "next_steps": "recommended next steps"\n'
        '}'
    )

    try:
        raw = inference.call(prompt, max_tokens=400)
        data = json.loads(raw)
    except Exception:
        return

    payload = {
        "request": data.get("request") or "Session summary",
        "project_id": project_id,
        "source_tool": os.environ.get("FORGEMEMO_SOURCE_TOOL", "hook"),
        "investigation": data.get("investigation"),
        "learnings": data.get("learnings"),
        "next_steps": data.get("next_steps"),
        "concepts": [],
        "session_id": session_id or None,
    }
    try:
        _req.post(f"{url_base}/session_summaries", json=payload, timeout=10)
        _write_project_context(cwd, payload)
    except Exception:
        pass


def hook_cmd(event_name: str = typer.Argument(...)):
    """Fire a forgememo hook event (stdin JSON payload). Used by settings.json hooks."""
    import sys as _sys

    _sys.argv = ["forgememo.hook", event_name]
    from forgememo.hook import main as _hook_main

    raise typer.Exit(_hook_main())
