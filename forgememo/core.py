#!/usr/bin/env python3
"""
Forgemem CLI — Long-term memory store for AI agents.
SQLite + FTS5, append-only, zero external deps (distill requires anthropic).

Usage:
  forgemem init
  forgemem save --type TYPE --content TEXT [--project P] [--session S]
                [--principle TEXT] [--score N] [--tags t1,t2] [--distill]
  forgemem retrieve QUERY [--k N] [--project P] [--type TYPE] [--format md|json]
  forgemem distill [--session S] [--project P]
  forgemem stats [--project P]
  forgemem export [--project P] [--k N]
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

# Load .env from Forgemem directory if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

DB_PATH = Path(os.environ.get("FORGEMEM_DB", Path.home() / ".forgemem" / "forgemem_memory.db"))

VALID_TYPES = ("success", "failure", "plan", "note")

INIT_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA auto_vacuum=INCREMENTAL;

CREATE TABLE IF NOT EXISTS traces (
  id          INTEGER PRIMARY KEY,
  ts          DATETIME DEFAULT CURRENT_TIMESTAMP,
  session_id  TEXT,
  project_tag TEXT,
  type        TEXT NOT NULL CHECK(type IN ('success','failure','plan','note')),
  content     TEXT NOT NULL,
  distilled   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_traces_project ON traces(project_tag);
CREATE INDEX IF NOT EXISTS idx_traces_type ON traces(type);
CREATE INDEX IF NOT EXISTS idx_traces_distilled ON traces(distilled);

CREATE VIRTUAL TABLE IF NOT EXISTS traces_fts
  USING fts5(content, project_tag, type);

CREATE TABLE IF NOT EXISTS principles (
  id              INTEGER PRIMARY KEY,
  ts              DATETIME DEFAULT CURRENT_TIMESTAMP,
  source_trace_id INTEGER REFERENCES traces(id),
  project_tag     TEXT,
  type            TEXT NOT NULL CHECK(type IN ('success','failure','plan','note')),
  principle       TEXT NOT NULL,
  impact_score    INTEGER DEFAULT 5 CHECK(impact_score BETWEEN 0 AND 10),
  tags            TEXT
);
CREATE INDEX IF NOT EXISTS idx_principles_project ON principles(project_tag);
CREATE INDEX IF NOT EXISTS idx_principles_score ON principles(impact_score DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS principles_fts
  USING fts5(principle, project_tag, tags);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def detect_project() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=os.getcwd()
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).name
    except FileNotFoundError:
        pass
    return Path(os.getcwd()).name


def cmd_init(args):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.executescript(INIT_SQL)
    conn.commit()
    conn.close()
    print(f"Forgemem DB initialized at {DB_PATH}")


def insert_principle(conn, source_trace_id, project_tag, trace_type, principle, score, tags_str):
    cur = conn.execute(
        "INSERT INTO principles (source_trace_id, project_tag, type, principle, impact_score, tags) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source_trace_id, project_tag, trace_type, principle, score, tags_str)
    )
    p_id = cur.lastrowid
    conn.execute(
        "INSERT INTO principles_fts(rowid, principle, project_tag, tags) VALUES (?, ?, ?, ?)",
        (p_id, principle, project_tag or "", tags_str or "")
    )
    return p_id


def distill_via_api(content: str, trace_type: str) -> dict:
    """Extract a principle from a trace using the configured AI provider."""
    from forgememo import inference
    prompt = (
        f'Extract a single 1-2 sentence principle from this {trace_type} trace. '
        f'Be concrete and actionable. '
        f'Return JSON only, no markdown: {{"principle": "...", "impact_score": 5, "tags": ["..."]}}\n\n'
        f'Trace:\n{content[:2000]}'
    )
    raw = inference.call(prompt, max_tokens=300)
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError as e:
        raise ValueError(f"Provider returned non-JSON (len={len(raw)}): {raw[:200]}") from e


def cmd_save(args):
    conn = get_conn()
    project = args.project or detect_project()
    tags_str = ",".join(t.strip() for t in args.tags.split(",")) if args.tags else None

    with conn:
        cur = conn.execute(
            "INSERT INTO traces (session_id, project_tag, type, content) VALUES (?, ?, ?, ?)",
            (args.session, project, args.type, args.content)
        )
        trace_id = cur.lastrowid
        conn.execute(
            "INSERT INTO traces_fts(rowid, content, project_tag, type) VALUES (?, ?, ?, ?)",
            (trace_id, args.content, project or "", args.type)
        )

    p_id = None

    if args.principle:
        # Manual principle — instant, no API
        p_id = insert_principle(conn, trace_id, project, args.type, args.principle, args.score, tags_str)
        conn.execute("UPDATE traces SET distilled=1 WHERE id=?", (trace_id,))
    elif args.distill:
        # Explicit distill flag — call API now
        print("Distilling via Claude API...", file=sys.stderr)
        result = distill_via_api(args.content, args.type)
        principle = result.get("principle", "")
        score = result.get("impact_score", args.score)
        tags_from_api = ",".join(result.get("tags", [])) or tags_str
        p_id = insert_principle(conn, trace_id, project, args.type, principle, score, tags_from_api)
        conn.execute("UPDATE traces SET distilled=1 WHERE id=?", (trace_id,))
        print(f"  Principle: {principle}", file=sys.stderr)

    conn.commit()
    conn.close()

    summary = f"Saved trace #{trace_id} [{args.type}] project={project}"
    if p_id:
        summary += f" + principle #{p_id}"
    else:
        summary += " (no principle yet — run: bm distill)"
    print(summary)


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


def _sanitize_fts_query(query: str) -> str:
    """Escape single quotes and strip FTS5-unsafe characters to prevent syntax errors."""
    return query.replace("'", "''").replace('"', '')


def cmd_retrieve(args):
    conn = get_conn()
    query = _sanitize_fts_query(args.query)
    k = args.k
    results = {"principles": [], "traces": []}
    has_project = bool(args.project)
    has_type = bool(args.type)

    # Search principles first (higher quality)
    p_params = [query]
    if has_project:
        p_params.append(args.project)
    if has_type:
        p_params.append(args.type)
    p_params.append(k)
    try:
        rows = conn.execute(_P_QUERIES[(has_project, has_type)], p_params).fetchall()
        results["principles"] = [dict(r) for r in rows]
    except sqlite3.OperationalError as e:
        print(f"FTS error: {e}", file=sys.stderr)

    # Also search traces (raw context, capped at 3 — they're verbose)
    t_params = [query]
    if has_project:
        t_params.append(args.project)
    if has_type:
        t_params.append(args.type)
    t_params.append(min(k, 3))
    try:
        rows = conn.execute(_T_QUERIES[(has_project, has_type)], t_params).fetchall()
        results["traces"] = [dict(r) for r in rows]
    except sqlite3.OperationalError as e:
        print(f"FTS error: {e}", file=sys.stderr)

    conn.close()

    if args.format == "json":
        print(json.dumps(results, indent=2, default=str))
        return

    # Markdown output (default — clean for agent prompts)
    lines = [f'# Forgemem: "{query}"']
    if args.project:
        lines.append(f"_project: {args.project}_")
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

    print("\n".join(lines))


_DISTILL_QUERIES = {
    (False, False): "SELECT id, type, content, project_tag FROM traces WHERE distilled=0",
    (True,  False): "SELECT id, type, content, project_tag FROM traces WHERE distilled=0 AND session_id=?",
    (False, True):  "SELECT id, type, content, project_tag FROM traces WHERE distilled=0 AND project_tag=?",
    (True,  True):  "SELECT id, type, content, project_tag FROM traces WHERE distilled=0 AND session_id=? AND project_tag=?",
}


def cmd_distill(args):
    conn = get_conn()
    has_session = bool(args.session)
    has_project = bool(args.project)
    params = []
    if has_session:
        params.append(args.session)
    if has_project:
        params.append(args.project)

    rows = conn.execute(_DISTILL_QUERIES[(has_session, has_project)], params).fetchall()

    if not rows:
        print("No undistilled traces found.")
        conn.close()
        return

    print(f"Distilling {len(rows)} trace(s)...")
    count = 0
    for row in rows:
        try:
            result = distill_via_api(row["content"], row["type"])
            principle = result.get("principle", "")
            score = result.get("impact_score", 5)
            tags = ",".join(result.get("tags", []))
            insert_principle(conn, row["id"], row["project_tag"], row["type"], principle, score, tags or None)
            conn.execute("UPDATE traces SET distilled=1 WHERE id=?", (row["id"],))
            conn.commit()
            count += 1
            print(f"  #{row['id']} [{row['type']}]: {principle[:80]}")
        except Exception as e:
            print(f"  #{row['id']} FAILED: {e}", file=sys.stderr)

    conn.close()
    print(f"Done. Distilled {count}/{len(rows)} traces.")


def cmd_stats(args):
    conn = get_conn()
    if args.project:
        p = [args.project]
        t_total     = conn.execute("SELECT COUNT(*) FROM traces WHERE project_tag=?", p).fetchone()[0]
        t_by_type   = conn.execute("SELECT type, COUNT(*) as n FROM traces WHERE project_tag=? GROUP BY type ORDER BY n DESC", p).fetchall()
        p_total     = conn.execute("SELECT COUNT(*) FROM principles WHERE project_tag=?", p).fetchone()[0]
        undistilled = conn.execute("SELECT COUNT(*) FROM traces WHERE project_tag=? AND distilled=0", p).fetchone()[0]
    else:
        t_total     = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
        t_by_type   = conn.execute("SELECT type, COUNT(*) as n FROM traces GROUP BY type ORDER BY n DESC").fetchall()
        p_total     = conn.execute("SELECT COUNT(*) FROM principles").fetchone()[0]
        undistilled = conn.execute("SELECT COUNT(*) FROM traces WHERE distilled=0").fetchone()[0]
    top_projects = conn.execute(
        "SELECT project_tag, COUNT(*) as n FROM traces GROUP BY project_tag ORDER BY n DESC LIMIT 5"
    ).fetchall()

    conn.close()

    title = "Forgemem Stats" + (f" — {args.project}" if args.project else " — all projects")
    print(f"\n{title}")
    print(f"  Traces:     {t_total} ({undistilled} undistilled)")
    print(f"  Principles: {p_total}")
    if t_by_type:
        breakdown = ", ".join(f"{r['type']}:{r['n']}" for r in t_by_type)
        print(f"  By type:    {breakdown}")
    if top_projects and not args.project:
        print("  Top projects:")
        for r in top_projects:
            print(f"    {r['project_tag'] or '(none)'}: {r['n']} traces")


def mine_memories_via_api(md_content: str, filename: str) -> list[dict]:
    """Extract traces from a memory .md file using the configured AI provider."""
    from forgememo import inference
    prompt = (
        "You are reading a project memory file. Extract ALL meaningful traces (successes, failures, notes, plans) "
        "that could be saved as long-term lessons. For each trace return a JSON object with keys: "
        '"type" (success|failure|note|plan), "project" (infer from content or filename), '
        '"content" (1-3 sentence description of what happened), "tags" (array of strings).\n'
        "Return a JSON array only, no markdown. If nothing useful, return [].\n\n"
        f"File: {filename}\n\n{md_content[:4000]}"
    )
    raw = inference.call(prompt, max_tokens=1500)
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def cmd_mine_memories(args):
    memory_dir = Path(args.dir).expanduser()
    if not memory_dir.exists():
        print(f"ERROR: Directory not found: {memory_dir}", file=sys.stderr)
        sys.exit(1)

    md_files = [f for f in memory_dir.glob("*.md") if f.name != "MEMORY.md"]
    if not md_files:
        print("No .md memory files found.")
        return

    print(f"Mining {len(md_files)} memory file(s) from {memory_dir}...")
    conn = get_conn()
    total_saved = 0

    for md_file in sorted(md_files):
        content = md_file.read_text(encoding="utf-8")
        print(f"  Reading {md_file.name}...", end=" ", flush=True)
        try:
            traces = mine_memories_via_api(content, md_file.name)
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        if not traces:
            print("nothing extracted.")
            continue

        print(f"{len(traces)} trace(s) found.")
        for t in traces:
            trace_type = t.get("type", "note")
            if trace_type not in VALID_TYPES:
                trace_type = "note"
            project = t.get("project") or md_file.stem.split("_")[1] if "_" in md_file.stem else md_file.stem
            trace_content = t.get("content", "").strip()
            if not trace_content:
                continue

            # Skip if near-duplicate already exists
            existing = conn.execute(
                "SELECT id FROM traces WHERE project_tag=? AND substr(content,1,80)=?",
                (project, trace_content[:80])
            ).fetchone()
            if existing:
                print(f"    skip (duplicate): {trace_content[:60]}...")
                continue

            cur = conn.execute(
                "INSERT INTO traces (project_tag, type, content) VALUES (?, ?, ?)",
                (project, trace_type, trace_content)
            )
            trace_id = cur.lastrowid
            conn.execute(
                "INSERT INTO traces_fts(rowid, content, project_tag, type) VALUES (?, ?, ?, ?)",
                (trace_id, trace_content, project or "", trace_type)
            )
            conn.commit()
            total_saved += 1
            print(f"    #{trace_id} [{trace_type}|{project}]: {trace_content[:70]}")

    conn.close()
    print(f"\nDone. Saved {total_saved} new trace(s). Run: forgemem distill")


def cmd_capture(args):
    """Capture raw content from stdin, a file, or git log into Forgemem."""
    # Resolve content from source
    if args.git:
        limit = args.limit or 50
        since = ["--since", args.since] if args.since else []
        result = subprocess.run(
            ["git", "log", "--oneline", f"-n{limit}"] + since,
            capture_output=True, text=True, cwd=os.getcwd()
        )
        if result.returncode != 0:
            print(f"ERROR: git log failed: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)
        content = result.stdout.strip()
        if not content:
            print("No git commits found for given range.")
            return
        content = f"git log (last {limit} commits):\n{content}"
    elif args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"ERROR: File not found: {path}", file=sys.stderr)
            sys.exit(1)
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > 8000:
            content = content[:8000] + f"\n... (truncated at 8000 chars, full file: {path})"
    elif not sys.stdin.isatty():
        content = sys.stdin.read().strip()
    else:
        print("ERROR: Provide --git, --file PATH, or pipe content via stdin.", file=sys.stderr)
        sys.exit(1)

    if not content.strip():
        print("Nothing to capture — content was empty.")
        return

    project = args.project or detect_project()

    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO traces (project_tag, type, content) VALUES (?, ?, ?)",
        (project, args.type, content)
    )
    trace_id = cur.lastrowid
    conn.execute(
        "INSERT INTO traces_fts(rowid, content, project_tag, type) VALUES (?, ?, ?, ?)",
        (trace_id, content, project or "", args.type)
    )

    p_id = None
    if args.distill:
        print("Distilling via configured provider...", file=sys.stderr)
        result = distill_via_api(content, args.type)
        principle = result.get("principle", "")
        score = result.get("impact_score", 5)
        tags = ",".join(result.get("tags", [])) or None
        p_id = insert_principle(conn, trace_id, project, args.type, principle, score, tags)
        conn.execute("UPDATE traces SET distilled=1 WHERE id=?", (trace_id,))
        print(f"  Principle: {principle}", file=sys.stderr)

    conn.commit()
    conn.close()

    summary = f"Captured trace #{trace_id} [{args.type}] project={project} ({len(content)} chars)"
    if p_id:
        summary += f" + principle #{p_id}"
    else:
        summary += " — run: bm distill"
    print(summary)


def cmd_backup(args):
    """Safe online backup using SQLite's backup API — copies WAL state correctly."""
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path(args.dest) if args.dest else DB_PATH.parent / f"forgemem_backup_{ts}.db"
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    print(f"Backup saved to {dest}")


def cmd_export(args):
    conn = get_conn()
    where = "WHERE p.project_tag=?" if args.project else ""
    params = [args.project] if args.project else []

    rows = conn.execute(
        f"SELECT p.ts, p.project_tag, p.type, p.principle, p.impact_score, p.tags "
        f"FROM principles p {where} ORDER BY p.impact_score DESC, p.ts DESC LIMIT ?",
        params + [args.k]
    ).fetchall()
    conn.close()

    if not rows:
        print("No principles found.")
        return

    lines = ["# Forgemem Principles Export"]
    if args.project:
        lines.append(f"_project: {args.project}_")
    lines.append(f"_top {len(rows)} by impact score_\n")

    for r in rows:
        tags = f" [{r['tags']}]" if r["tags"] else ""
        lines.append(f"- **[{r['type']}|{r['project_tag'] or 'global'}|{r['ts'][:10]}|score:{r['impact_score']}]**{tags}")
        lines.append(f"  {r['principle']}")

    print("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Forgemem — AI agent long-term memory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize DB")

    p_save = sub.add_parser("save", help="Save a trace")
    p_save.add_argument("--type", required=True, choices=VALID_TYPES)
    p_save.add_argument("--content", required=True)
    p_save.add_argument("--project", default=None)
    p_save.add_argument("--session", default=None)
    p_save.add_argument("--principle", default=None, help="Manual principle (no API)")
    p_save.add_argument("--score", type=int, default=5)
    p_save.add_argument("--tags", default=None, help="Comma-separated tags")
    p_save.add_argument("--distill", action="store_true", help="Auto-distill via Claude API")

    p_ret = sub.add_parser("retrieve", help="Search traces and principles")
    p_ret.add_argument("query")
    p_ret.add_argument("--k", type=int, default=5)
    p_ret.add_argument("--project", default=None)
    p_ret.add_argument("--type", choices=VALID_TYPES, default=None)
    p_ret.add_argument("--format", choices=["md", "json"], default="md")

    p_cap = sub.add_parser("capture", help="Capture content from stdin, file, or git log")
    p_cap.add_argument("--type", required=True, choices=VALID_TYPES)
    p_cap.add_argument("--stdin", action="store_true", help="Read from stdin (also works implicitly via pipe)")
    p_cap.add_argument("--file", default=None, metavar="PATH", help="Read from a file")
    p_cap.add_argument("--git", action="store_true", help="Capture git log from current repo")
    p_cap.add_argument("--limit", type=int, default=50, help="Max commits for --git (default 50)")
    p_cap.add_argument("--since", default=None, help="--git: e.g. '2 days ago', 'yesterday'")
    p_cap.add_argument("--project", default=None, help="Project tag (auto-detected from cwd)")
    p_cap.add_argument("--distill", action="store_true", help="Distill immediately via Claude Haiku")

    p_distill = sub.add_parser("distill", help="Batch distill undistilled traces via Claude API")
    p_distill.add_argument("--session", default=None)
    p_distill.add_argument("--project", default=None)

    p_stats = sub.add_parser("stats", help="Show DB stats")
    p_stats.add_argument("--project", default=None)

    p_backup = sub.add_parser("backup", help="Safe online backup (WAL-safe, timestamped)")
    p_backup.add_argument("--dest", default=None, metavar="PATH", help="Backup destination (default: auto-timestamped)")

    p_export = sub.add_parser("export", help="Export top principles as markdown")
    p_export.add_argument("--project", default=None)
    p_export.add_argument("--k", type=int, default=20)

    args = parser.parse_args()
    cmds = {
        "init": cmd_init,
        "save": cmd_save,
        "capture": cmd_capture,
        "retrieve": cmd_retrieve,
        "distill": cmd_distill,
        "stats": cmd_stats,
        "backup": cmd_backup,
        "export": cmd_export,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
