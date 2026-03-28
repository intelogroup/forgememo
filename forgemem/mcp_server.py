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
import sys

from pathlib import Path

DB_PATH = Path(os.environ.get("FORGEMEM_DB", Path.home() / "Developer" / "Forgemem" / "forgemem_memory.db"))

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
    global _stdio_logged
    if not _stdio_logged:
        _stdio_logged = True
        _log_connection("stdio", agent="claude-code")

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
