#!/usr/bin/env python3
"""
Forgemem MCP Server — exposes Forgemem memory tools to Claude Code.
Transport: stdio (Claude Code spawns on demand, no persistent process).

Register in ~/.claude/settings.json:
  "mcpServers": {
    "forgemem": {
      "command": "python3",
      "args": ["PATH_TO/mcp_server.py"]
    }
  }
"""

import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

from forgemem import config as _cfg

DB_PATH = Path(os.environ.get("FORGEMEM_DB", Path.home() / ".forgemem" / "forgemem_memory.db"))

try:
    from fastmcp import FastMCP
except ImportError:
    print("ERROR: pip install fastmcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("forgemem")

_P_BASE = ("SELECT p.id, p.ts, p.project_tag, p.type, p.principle, p.impact_score, p.tags "
           "FROM principles p WHERE p.id IN (SELECT rowid FROM principles_fts WHERE principles_fts MATCH ?) ")
_P_QUERIES = {
    (False, False): _P_BASE + "ORDER BY p.impact_score DESC, p.ts DESC LIMIT ?",
    (True,  False): _P_BASE + "AND p.project_tag = ? ORDER BY p.impact_score DESC, p.ts DESC LIMIT ?",
    (False, True):  _P_BASE + "AND p.type = ? ORDER BY p.impact_score DESC, p.ts DESC LIMIT ?",
    (True,  True):  _P_BASE + "AND p.project_tag = ? AND p.type = ? ORDER BY p.impact_score DESC, p.ts DESC LIMIT ?",
}

_T_BASE = ("SELECT t.id, t.ts, t.project_tag, t.type, t.content, t.distilled "
           "FROM traces t WHERE t.id IN (SELECT rowid FROM traces_fts WHERE traces_fts MATCH ?) ")
_T_QUERIES = {
    (False, False): _T_BASE + "ORDER BY t.ts DESC LIMIT ?",
    (True,  False): _T_BASE + "AND t.project_tag = ? ORDER BY t.ts DESC LIMIT ?",
    (False, True):  _T_BASE + "AND t.type = ? ORDER BY t.ts DESC LIMIT ?",
    (True,  True):  _T_BASE + "AND t.project_tag = ? AND t.type = ? ORDER BY t.ts DESC LIMIT ?",
}


def _conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_connections_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_connections (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       TEXT DEFAULT (datetime('now')),
            transport TEXT NOT NULL,  -- 'stdio' or 'http'
            ip       TEXT,
            agent    TEXT            -- user-agent or process hint
        )
    """)
    conn.commit()


def _log_connection(transport: str, ip: str = None, agent: str = None):
    conn = _conn()
    if conn is None:
        return
    _ensure_connections_table(conn)
    conn.execute(
        "INSERT INTO agent_connections (transport, ip, agent) VALUES (?, ?, ?)",
        (transport, ip, agent)
    )
    conn.commit()
    conn.close()


# Track stdio sessions — log once per process on first tool call
_stdio_logged = False

# Background sync — fires once per MCP process on first tool call
_sync_triggered = False


def _trigger_sync() -> None:
    """Pull remote changes in the background if the user has a Forgemem token.
    Non-blocking: fires a subprocess and returns immediately."""
    if not _cfg.load().get("forgemem_token"):
        return
    try:
        subprocess.Popen(
            ["forgemem", "sync", "--pull-only"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


@mcp.tool()
def retrieve_memories(
    query: str,
    k: int = 5,
    project: str = None,
    type: str = None,
) -> str:
    """
    Search Forgemem long-term memory for principles and traces matching the query.
    Returns clean markdown suitable for agent prompts.

    Args:
        query: Search terms (FTS5 — supports AND, OR, phrase quotes)
        k: Max results (default 5)
        project: Filter to a specific project tag (optional)
        type: Filter to success|failure|plan|note (optional)
    """
    global _stdio_logged, _sync_triggered
    if not _stdio_logged:
        _stdio_logged = True
        _log_connection("stdio", agent="claude-code")
    if not _sync_triggered:
        _sync_triggered = True
        threading.Thread(target=_trigger_sync, daemon=True).start()

    conn = _conn()
    if conn is None:
        return "Forgemem DB not found. Run: python3 ~/Developer/Forgemem/forgemem.py init"

    has_project = bool(project)
    has_type = bool(type)
    results = {"principles": [], "traces": []}

    p_params = [query]
    if has_project:
        p_params.append(project)
    if has_type:
        p_params.append(type)
    p_params.append(k)
    try:
        rows = conn.execute(_P_QUERIES[(has_project, has_type)], p_params).fetchall()
        results["principles"] = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        pass

    t_params = [query]
    if has_project:
        t_params.append(project)
    if has_type:
        t_params.append(type)
    t_params.append(min(k, 3))
    try:
        rows = conn.execute(_T_QUERIES[(has_project, has_type)], t_params).fetchall()
        results["traces"] = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        pass

    conn.close()

    lines = [f'# Forgemem: "{query}"']
    if project:
        lines.append(f"_project: {project}_")
    lines.append("")

    if results["principles"]:
        lines.append("## Principles")
        for p in results["principles"]:
            tags = f" | tags: {p['tags']}" if p.get("tags") else ""
            lines.append(f"\n**[{p['type']}] {p['project_tag'] or 'global'}** — {p['ts'][:10]}")
            lines.append(f"> {p['principle']}")
            lines.append(f"Score: {p['impact_score']}/10{tags}")
    else:
        lines.append("_No principles found._")

    if results["traces"]:
        lines.append("\n## Raw Traces")
        for t in results["traces"]:
            distilled = " ✓" if t["distilled"] else ""
            lines.append(f"\n**[{t['type']}] {t['project_tag'] or 'global'}** — {t['ts'][:10]}{distilled}")
            lines.append(t["content"][:500] + ("..." if len(t["content"]) > 500 else ""))

    return "\n".join(lines)


@mcp.tool()
def save_trace(
    type: str,
    content: str,
    project: str = None,
    session: str = None,
    principle: str = None,
    score: int = 5,
    tags: str = None,
) -> str:
    """
    Save a trace (failure, success, plan, or note) to Forgemem memory.
    Optionally include a distilled principle for immediate storage.
    No API calls — always fast and offline.

    Args:
        type: success|failure|plan|note
        content: Full trace text
        project: Project tag (auto-detected from cwd if omitted)
        session: Session identifier for later batch distillation
        principle: Distilled 1-2 sentence principle (optional)
        score: Impact score 0-10 (default 5, used with principle)
        tags: Comma-separated tags e.g. "auth,cors"
    """
    if type not in ("success", "failure", "plan", "note"):
        return f"ERROR: type must be one of success|failure|plan|note, got: {type}"

    conn = _conn()
    if conn is None:
        return "Forgemem DB not found. Run: python3 ~/Developer/Forgemem/forgemem.py init"

    tags_str = ",".join(t.strip() for t in tags.split(",")) if tags else None

    cur = conn.execute(
        "INSERT INTO traces (session_id, project_tag, type, content) VALUES (?, ?, ?, ?)",
        (session, project, type, content)
    )
    trace_id = cur.lastrowid
    conn.execute(
        "INSERT INTO traces_fts(rowid, content, project_tag, type) VALUES (?, ?, ?, ?)",
        (trace_id, content, project or "", type)
    )

    p_id = None
    if principle:
        cur2 = conn.execute(
            "INSERT INTO principles (source_trace_id, project_tag, type, principle, impact_score, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (trace_id, project, type, principle, score, tags_str)
        )
        p_id = cur2.lastrowid
        conn.execute(
            "INSERT INTO principles_fts(rowid, principle, project_tag, tags) VALUES (?, ?, ?, ?)",
            (p_id, principle, project or "", tags_str or "")
        )
        conn.execute("UPDATE traces SET distilled=1 WHERE id=?", (trace_id,))

    conn.commit()
    conn.close()

    msg = f"Saved trace #{trace_id} [{type}] project={project or 'auto'}"
    if p_id:
        msg += f" + principle #{p_id}"
    else:
        msg += " — run `bm distill` to extract principles later"
    return msg


@mcp.tool()
def mine_session(
    memories: list,
    project: str = None,
    session: str = None,
) -> str:
    """
    Batch-store memories extracted by the agent from the current session.
    The agent (Claude Code / Gemini CLI) does the extraction inline — no API key needed.
    Call this at session end with the memories you've identified.

    Each memory in the list must be a dict with:
        type:      success|failure|plan|note
        content:   full trace text (what happened)
        principle: 1-2 sentence distilled takeaway (optional but recommended)
        score:     impact 0-10 (default 5, used only when principle is set)
        tags:      comma-separated tags e.g. "auth,cors" (optional)

    Example call:
        mine_session(memories=[
            {"type": "success", "content": "...", "principle": "...", "score": 8, "tags": "perf"},
            {"type": "failure", "content": "...", "principle": "..."},
        ], project="myapp")
    """
    conn = _conn()
    if conn is None:
        return "Forgemem DB not found. Run: forgemem init"

    if not isinstance(memories, list) or not memories:
        return "ERROR: memories must be a non-empty list of dicts"

    saved, skipped = [], []
    for i, m in enumerate(memories):
        if not isinstance(m, dict):
            skipped.append(f"#{i} not a dict")
            continue
        mtype = m.get("type", "note")
        if mtype not in ("success", "failure", "plan", "note"):
            mtype = "note"
        content = str(m.get("content", "")).strip()
        if not content:
            skipped.append(f"#{i} empty content")
            continue
        principle = str(m.get("principle", "")).strip() or None
        score = int(m.get("score", 5))
        tags_raw = m.get("tags", "")
        tags_str = ",".join(t.strip() for t in str(tags_raw).split(",") if t.strip()) or None

        cur = conn.execute(
            "INSERT INTO traces (session_id, project_tag, type, content) VALUES (?, ?, ?, ?)",
            (session, project, mtype, content),
        )
        trace_id = cur.lastrowid
        conn.execute(
            "INSERT INTO traces_fts(rowid, content, project_tag, type) VALUES (?, ?, ?, ?)",
            (trace_id, content, project or "", mtype),
        )
        p_id = None
        if principle:
            cur2 = conn.execute(
                "INSERT INTO principles (source_trace_id, project_tag, type, principle, impact_score, tags) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (trace_id, project, mtype, principle, score, tags_str),
            )
            p_id = cur2.lastrowid
            conn.execute(
                "INSERT INTO principles_fts(rowid, principle, project_tag, tags) VALUES (?, ?, ?, ?)",
                (p_id, principle, project or "", tags_str or ""),
            )
            conn.execute("UPDATE traces SET distilled=1 WHERE id=?", (trace_id,))
        saved.append(f"trace#{trace_id}" + (f"+principle#{p_id}" if p_id else ""))

    conn.commit()
    conn.close()

    msg = f"mine_session: saved {len(saved)} memories"
    if project:
        msg += f" → project={project}"
    if saved:
        msg += "\n  " + ", ".join(saved)
    if skipped:
        msg += f"\n  skipped: {', '.join(skipped)}"
    return msg


@mcp.tool()
def distill_session(
    distillations: list,
    project: str = None,
) -> str:
    """
    Write agent-computed principles for undistilled traces.
    The agent fetches undistilled traces via retrieve_memories, synthesizes
    principles inline, then calls this tool to persist them. No API key needed.

    Each item in distillations must be a dict with:
        trace_id:  int — ID of the undistilled trace
        principle: str — 1-2 sentence distilled takeaway
        score:     int — impact 0-10 (default 5)
        tags:      str — comma-separated tags (optional)

    Example:
        distill_session(distillations=[
            {"trace_id": 42, "principle": "Always validate X before Y.", "score": 8},
        ])
    """
    conn = _conn()
    if conn is None:
        return "Forgemem DB not found. Run: forgemem init"

    if not isinstance(distillations, list) or not distillations:
        return "ERROR: distillations must be a non-empty list of dicts"

    written, skipped = [], []
    for i, d in enumerate(distillations):
        if not isinstance(d, dict):
            skipped.append(f"#{i} not a dict")
            continue
        trace_id = d.get("trace_id")
        if not isinstance(trace_id, int):
            skipped.append(f"#{i} trace_id must be int")
            continue
        principle = str(d.get("principle", "")).strip()
        if not principle:
            skipped.append(f"#{i} empty principle")
            continue

        # Verify trace exists and is undistilled
        row = conn.execute(
            "SELECT id, project_tag, type FROM traces WHERE id=? AND distilled=0",
            (trace_id,)
        ).fetchone()
        if row is None:
            skipped.append(f"#{i} trace_id={trace_id} not found or already distilled")
            continue

        score = int(d.get("score", 5))
        tags_raw = d.get("tags", "")
        tags_str = ",".join(t.strip() for t in str(tags_raw).split(",") if t.strip()) or None
        proj = project or row["project_tag"]

        cur = conn.execute(
            "INSERT INTO principles (source_trace_id, project_tag, type, principle, impact_score, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (trace_id, proj, row["type"], principle, score, tags_str),
        )
        p_id = cur.lastrowid
        conn.execute(
            "INSERT INTO principles_fts(rowid, principle, project_tag, tags) VALUES (?, ?, ?, ?)",
            (p_id, principle, proj or "", tags_str or ""),
        )
        conn.execute("UPDATE traces SET distilled=1 WHERE id=?", (trace_id,))
        written.append(f"trace#{trace_id}→principle#{p_id}")

    conn.commit()
    conn.close()

    msg = f"distill_session: wrote {len(written)} principle(s)"
    if written:
        msg += "\n  " + ", ".join(written)
    if skipped:
        msg += f"\n  skipped: {', '.join(skipped)}"
    return msg


@mcp.tool()
def forgemem_stats() -> str:
    """Return a summary of traces and principles stored in Forgemem."""
    conn = _conn()
    if conn is None:
        return "Forgemem DB not found."

    t_total     = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
    p_total     = conn.execute("SELECT COUNT(*) FROM principles").fetchone()[0]
    undistilled = conn.execute("SELECT COUNT(*) FROM traces WHERE distilled=0").fetchone()[0]
    by_type     = conn.execute("SELECT type, COUNT(*) as n FROM traces GROUP BY type ORDER BY n DESC").fetchall()
    top_proj    = conn.execute("SELECT project_tag, COUNT(*) as n FROM traces GROUP BY project_tag ORDER BY n DESC LIMIT 5").fetchall()

    # Connection log
    _ensure_connections_table(conn)
    conn_total = conn.execute("SELECT COUNT(*) FROM agent_connections").fetchone()[0]
    conn_by_transport = conn.execute(
        "SELECT transport, COUNT(*) as n FROM agent_connections GROUP BY transport ORDER BY n DESC"
    ).fetchall()
    conn_recent = conn.execute(
        "SELECT ts, transport, ip, agent FROM agent_connections ORDER BY id DESC LIMIT 5"
    ).fetchall()
    conn.close()

    lines = [
        "# Forgemem Stats",
        f"Traces: {t_total} ({undistilled} undistilled) | Principles: {p_total}",
        "By type: " + ", ".join(f"{r['type']}:{r['n']}" for r in by_type),
        "Top projects:",
    ]
    for r in top_proj:
        lines.append(f"  {r['project_tag'] or '(none)'}: {r['n']} traces")

    lines.append(f"\n## Agent Connections (total: {conn_total})")
    if conn_by_transport:
        lines.append("By transport: " + ", ".join(f"{r['transport']}:{r['n']}" for r in conn_by_transport))
    if conn_recent:
        lines.append("Recent:")
        for r in conn_recent:
            ip_hint = f" from {r['ip']}" if r['ip'] else ""
            agent_hint = f" [{r['agent']}]" if r['agent'] else ""
            lines.append(f"  {r['ts'][:16]}  {r['transport']}{ip_hint}{agent_hint}")
    else:
        lines.append("  No connections recorded yet.")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="Run as HTTP/SSE server instead of stdio")
    parser.add_argument("--port", type=int, default=7474, help="Port for HTTP mode (default 7474)")
    parser.add_argument("--host", default="127.0.0.1", help="Host for HTTP mode (default 127.0.0.1)")
    args = parser.parse_args()

    if args.http:
        class _ConnectionLoggerASGI:
            """Raw ASGI middleware — safe for SSE streaming (no response buffering)."""
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if scope["type"] == "http" and scope.get("path") == "/sse":
                    client = scope.get("client")
                    ip = client[0] if client else None
                    headers = {k: v for k, v in scope.get("headers", [])}
                    ua = headers.get(b"user-agent", b"").decode()[:120] or None
                    _log_connection("http", ip=ip, agent=ua)
                await self.app(scope, receive, send)

        print(f"Forgemem MCP HTTP server → http://{args.host}:{args.port}/sse", file=sys.stderr)
        import uvicorn
        app = _ConnectionLoggerASGI(mcp.http_app(transport="sse"))
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        mcp.run()
