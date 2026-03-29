#!/usr/bin/env python3
"""
Forgemem Query MCP Tool — A standalone MCP tool that wraps forgemem queries.

This tool exposes forgemem as an MCP service with additional query capabilities
beyond the base mcp_server.py tools.

Install:
  pip install fastmcp

Run:
  python3 forgemem_query_tool.py --http

Then add to ~/.claude/settings.json:
  "mcpServers": {
    "forgemem-query": {
      "command": "python3",
      "args": ["/path/to/forgemem_query_tool.py", "--http"]
    }
  }
"""

import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.environ.get("FORGEMEM_DB", Path.home() / "Developer" / "Forgemem" / "forgemem_memory.db"))

try:
    from fastmcp import FastMCP
except ImportError:
    print("ERROR: pip install fastmcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("forgemem-query")


def _conn() -> Optional[sqlite3.Connection]:
    """Get DB connection with row factory."""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool()
def search_principles(
    query: str,
    k: int = 5,
    project: str = None,
    type_filter: str = None,
    min_score: int = 0,
) -> str:
    """
    Search principles with optional filtering and scoring.
    
    Args:
        query: Search terms (FTS5 syntax)
        k: Max results (default 5, max 50)
        project: Filter by project tag (optional)
        type_filter: Filter by type (success|failure|plan|note)
        min_score: Only return principles with impact_score >= min_score
    """
    conn = _conn()
    if not conn:
        return "Forgemem DB not found"
    
    k = min(k, 50)
    has_project = bool(project)
    has_type = bool(type_filter)
    
    _base = (
        "SELECT p.id, p.ts, p.project_tag, p.type, p.principle, p.impact_score, p.tags"
        " FROM principles p"
        " WHERE p.id IN (SELECT rowid FROM principles_fts WHERE principles_fts MATCH ?)"
        " AND p.impact_score >= ? "
    )

    params = [query, min_score]

    if has_project and has_type:
        params.extend([project, type_filter])
        sql = _base + "AND p.project_tag = ? AND p.type = ? ORDER BY p.impact_score DESC, p.ts DESC LIMIT ?"
    elif has_project:
        params.append(project)
        sql = _base + "AND p.project_tag = ? ORDER BY p.impact_score DESC, p.ts DESC LIMIT ?"
    elif has_type:
        params.append(type_filter)
        sql = _base + "AND p.type = ? ORDER BY p.impact_score DESC, p.ts DESC LIMIT ?"
    else:
        sql = _base + "ORDER BY p.impact_score DESC, p.ts DESC LIMIT ?"

    params.append(k)

    try:
        rows = conn.execute(sql, params).fetchall()
        results = [
            {
                "id": r["id"],
                "ts": r["ts"],
                "project": r["project_tag"],
                "type": r["type"],
                "principle": r["principle"],
                "score": r["impact_score"],
                "tags": r["tags"]
            }
            for r in rows
        ]
        
        md = f"# Principles: \"{query}\"\n\n"
        if project:
            md += f"_Filtered by project: {project}_\n"
        if type_filter:
            md += f"_Filtered by type: {type_filter}_\n"
        if min_score > 0:
            md += f"_Min score: {min_score}_\n"
        md += f"\nFound {len(results)} principles:\n\n"
        
        for r in results:
            md += f"**[{r['score']}/10] {r['type']} — {r['project']}** ({r['ts'][:10]})\n"
            md += f"> {r['principle']}\n"
            if r["tags"]:
                md += f"Tags: {r['tags']}\n"
            md += "\n"
        
        conn.close()
        return md if results else "No principles found matching your criteria."
    except Exception as e:
        conn.close()
        return f"Error searching principles: {str(e)}"


@mcp.tool()
def search_traces(
    query: str,
    k: int = 5,
    project: str = None,
    type_filter: str = None,
    distilled_only: bool = False,
) -> str:
    """
    Search traces/learnings with optional filtering.
    
    Args:
        query: Search terms (FTS5 syntax)
        k: Max results (default 5, max 20)
        project: Filter by project tag
        type_filter: Filter by type (success|failure|plan|note)
        distilled_only: Only show traces with extracted principles
    """
    conn = _conn()
    if not conn:
        return "Forgemem DB not found"
    
    k = min(k, 20)
    has_project = bool(project)
    has_type = bool(type_filter)
    
    _base = (
        "SELECT t.id, t.ts, t.project_tag, t.type, t.content, t.distilled"
        " FROM traces t"
        " WHERE t.id IN (SELECT rowid FROM traces_fts WHERE traces_fts MATCH ?)"
        + (" AND t.distilled = 1 " if distilled_only else " ")
    )

    params = [query]

    if has_project and has_type:
        params.extend([project, type_filter])
        sql = _base + "AND t.project_tag = ? AND t.type = ? ORDER BY t.ts DESC LIMIT ?"
    elif has_project:
        params.append(project)
        sql = _base + "AND t.project_tag = ? ORDER BY t.ts DESC LIMIT ?"
    elif has_type:
        params.append(type_filter)
        sql = _base + "AND t.type = ? ORDER BY t.ts DESC LIMIT ?"
    else:
        sql = _base + "ORDER BY t.ts DESC LIMIT ?"

    params.append(k)

    try:
        rows = conn.execute(sql, params).fetchall()
        results = [
            {
                "id": r["id"],
                "ts": r["ts"],
                "project": r["project_tag"],
                "type": r["type"],
                "content": r["content"],
                "distilled": bool(r["distilled"])
            }
            for r in rows
        ]
        
        md = f"# Traces: \"{query}\"\n\n"
        if project:
            md += f"_Project: {project}_\n"
        if type_filter:
            md += f"_Type: {type_filter}_\n"
        if distilled_only:
            md += "_Distilled only_\n"
        md += f"\nFound {len(results)} traces:\n\n"
        
        for r in results:
            distilled = " ✓" if r["distilled"] else ""
            md += f"**[{r['type']}] {r['project']}** — {r['ts'][:10]}{distilled}\n"
            preview = r["content"][:300] + ("..." if len(r["content"]) > 300 else "")
            md += f"{preview}\n\n"
        
        conn.close()
        return md if results else "No traces found matching your criteria."
    except Exception as e:
        conn.close()
        return f"Error searching traces: {str(e)}"


@mcp.tool()
def list_top_principles(
    project: str = None,
    type_filter: str = None,
    limit: int = 10,
) -> str:
    """
    List top-scoring principles without full-text search.
    Useful for getting the "greatest hits" for a project.
    
    Args:
        project: Filter by project tag
        type_filter: Filter by type (success|failure|plan|note)
        limit: Max results (default 10, max 50)
    """
    conn = _conn()
    if not conn:
        return "Forgemem DB not found"
    
    limit = min(limit, 50)
    
    query = "SELECT p.id, p.ts, p.project_tag, p.type, p.principle, p.impact_score, p.tags FROM principles p WHERE 1=1 "
    params = []
    
    if project:
        query += "AND p.project_tag = ? "
        params.append(project)
    if type_filter:
        query += "AND p.type = ? "
        params.append(type_filter)
    
    query += "ORDER BY p.impact_score DESC, p.ts DESC LIMIT ?"
    params.append(limit)
    
    try:
        rows = conn.execute(query, params).fetchall()
        results = [dict(r) for r in rows]
        
        md = "# Top Principles\n\n"
        if project:
            md += f"_Project: {project}_\n"
        if type_filter:
            md += f"_Type: {type_filter}_\n"
        md += f"\nTop {len(results)} principles (by impact score):\n\n"
        
        for r in results:
            md += f"**[{r['impact_score']}/10] {r['type']}**\n"
            md += f"> {r['principle']}\n"
            if r["tags"]:
                md += f"Tags: {r['tags']}\n"
            md += f"_{r['project_tag'] or 'global'}_ — {r['ts'][:10]}\n\n"
        
        conn.close()
        return md if results else "No principles found."
    except Exception as e:
        conn.close()
        return f"Error listing principles: {str(e)}"


@mcp.tool()
def get_project_summary(project: str) -> str:
    """
    Get a full summary of a project: principles, recent traces, stats.
    
    Args:
        project: Project tag (required)
    """
    conn = _conn()
    if not conn:
        return "Forgemem DB not found"
    
    try:
        # Counts by type
        by_type = conn.execute(
            "SELECT type, COUNT(*) as n FROM traces WHERE project_tag = ? GROUP BY type",
            (project,)
        ).fetchall()
        
        # Top principles
        top_principles = conn.execute(
            "SELECT id, principle, impact_score, type, tags FROM principles WHERE project_tag = ? ORDER BY impact_score DESC LIMIT 5",
            (project,)
        ).fetchall()
        
        # Recent traces
        recent_traces = conn.execute(
            "SELECT ts, type, content FROM traces WHERE project_tag = ? ORDER BY ts DESC LIMIT 3",
            (project,)
        ).fetchall()
        
        # Total stats
        total_traces = conn.execute(
            "SELECT COUNT(*) FROM traces WHERE project_tag = ?",
            (project,)
        ).fetchone()[0]
        
        total_principles = conn.execute(
            "SELECT COUNT(*) FROM principles WHERE project_tag = ?",
            (project,)
        ).fetchone()[0]
        
        conn.close()
        
        md = f"# Project Summary: {project}\n\n"
        md += f"**Traces:** {total_traces} | **Principles:** {total_principles}\n\n"
        
        md += "## By Type\n"
        for row in by_type:
            md += f"- {row['type']}: {row['n']}\n"
        
        md += "\n## Top Principles\n"
        for p in top_principles:
            md += f"- **[{p['impact_score']}/10] {p['type']}** — {p['principle']}\n"
            if p["tags"]:
                md += f"  Tags: {p['tags']}\n"
        
        md += "\n## Recent Traces\n"
        for t in recent_traces:
            preview = t["content"][:200] + ("..." if len(t["content"]) > 200 else "")
            md += f"- **{t['type']}** ({t['ts'][:10]}) — {preview}\n"
        
        return md
    except Exception as e:
        conn.close()
        return f"Error getting project summary: {str(e)}"


@mcp.tool()
def get_forgemem_status() -> str:
    """
    Get overall Forgemem statistics and health.
    """
    conn = _conn()
    if not conn:
        return "Forgemem DB not found at " + str(DB_PATH)
    
    try:
        total_traces = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
        total_principles = conn.execute("SELECT COUNT(*) FROM principles").fetchone()[0]
        undistilled = conn.execute("SELECT COUNT(*) FROM traces WHERE distilled = 0").fetchone()[0]
        
        by_type = conn.execute(
            "SELECT type, COUNT(*) as n FROM traces GROUP BY type ORDER BY n DESC"
        ).fetchall()
        
        by_project = conn.execute(
            "SELECT project_tag, COUNT(*) as n FROM traces GROUP BY project_tag ORDER BY n DESC LIMIT 10"
        ).fetchall()
        
        conn.close()
        
        md = "# Forgemem Status\n\n"
        md += f"**Database:** {DB_PATH}\n"
        md += f"**Traces:** {total_traces} ({undistilled} undistilled)\n"
        md += f"**Principles:** {total_principles}\n\n"
        
        md += "## By Type\n"
        for row in by_type:
            md += f"- {row['type']}: {row['n']}\n"
        
        md += "\n## Top Projects\n"
        for row in by_project:
            proj = row["project_tag"] or "(no project)"
            md += f"- {proj}: {row['n']} traces\n"
        
        return md
    except Exception as e:
        return f"Error getting status: {str(e)}"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="Run as HTTP/SSE server")
    parser.add_argument("--port", type=int, default=7475, help="Port for HTTP mode (default 7475)")
    parser.add_argument("--host", default="127.0.0.1", help="Host for HTTP mode")
    args = parser.parse_args()
    
    if args.http:
        print(f"Forgemem Query Tool → http://{args.host}:{args.port}/sse", file=sys.stderr)
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run()
