# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install
pip install -r requirements.txt && pip install -e .

# Run all tests
pytest -q

# Run single test file
pytest tests/test_daemon_api.py

# Run single test
pytest tests/test_daemon_api.py::test_health_endpoint -v

# Test env vars required for CI (no live daemon, no circuit breaker)
FORGEMEMO_MOCK_TRANSPORT=1 FORGEMEMO_DISABLE_BREAKER=1 pytest -q

# Start daemon manually
python -m forgememo.daemon

# Run MCP server
forgememo mcp --http
```

## Architecture

Forgememo is an **event-sourced memory layer for AI agents**. All writes flow through one HTTP path:

```
Agent tool use
     ↓
hook.py  (captures UserPromptSubmit / Stop events, POSTs to daemon)
     ↓
daemon.py  (Flask HTTP API @ 127.0.0.1:5555)
     ↓
storage.py  (SQLite + FTS5: events, distilled_summaries, session_summaries)
     ↑             ↑
worker.py     mcp_server.py
(async         (FastMCP stdio/HTTP — exposes daemon as
 distillation)  query tools to agents via MCP)
```

**Background services** (launched by `forgememo init`/`start`):
- `worker.py` — pulls undistilled events, calls LLM via `inference.py`, writes summaries
- `scanner.py` — nightly LaunchAgent (macOS): scans 24h git commits + `~/.claude/projects/*/memory/*.md` for learnings

**CLI** (`cli.py` + `forgememo/commands/`) uses Typer with a flat command structure. Key commands: `init`, `start`, `stop`, `status`, `search`, `store`, `config`, `auth`, `skill`.

## Key Design Decisions

**Global daemon, not per-project**: One daemon on port 5555 handles all projects; project isolation is via `project_id` in DB queries.

**HTTP-first transport**: Unix socket tried first on POSIX for performance, HTTP fallback always available. `FORGEMEMO_HTTP_PORT` defaults to `5555` on all platforms (was Windows-only before PR #8 — do not revert this).

**Transport in tests**: Always set `FORGEMEMO_MOCK_TRANSPORT=1` and `FORGEMEMO_DISABLE_BREAKER=1` in test environments. The `conftest.py` does this automatically; `mcp_server.py` respects these flags.

**Port precedence**: `FORGEMEMO_HTTP_PORT` env var > `~/.forgememo/daemon.port` lockfile > default `5555`.

**Multi-provider inference**: `inference.py` routes to `anthropic`, `openai`, `gemini`, `ollama`, `claude_code`, or `forgememo` (managed) based on `~/.forgememo/config.json`. Config path is `FORGEMEM_CONFIG`, DB path is `FORGEMEM_DB`.

## Key Files

| File | Purpose |
|------|---------|
| `forgememo/cli.py` | Typer app entry point |
| `forgememo/daemon.py` | Flask HTTP API server |
| `forgememo/mcp_server.py` | FastMCP server (exposes daemon as MCP tools) |
| `forgememo/hook.py` | Hook adapter — normalizes agent events, POSTs to daemon |
| `forgememo/storage.py` | SQLite + FTS5 schema and migrations |
| `forgememo/inference.py` | Multi-provider LLM routing |
| `forgememo/scanner.py` | Daily git/memory scanner (LaunchAgent) |
| `forgememo/worker.py` | Background distillation worker |
| `forgememo/commands/lifecycle.py` | init, start, stop, status, doctor |
| `tests/conftest.py` | Fixtures; sets mock transport env vars |

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `FORGEMEMO_HTTP_PORT` | `5555` | Daemon HTTP port |
| `FORGEMEMO_MOCK_TRANSPORT` | `0` | Skip real HTTP calls (tests) |
| `FORGEMEMO_DISABLE_BREAKER` | `0` | Disable circuit breaker (tests) |
| `FORGEMEM_DB` | `~/.forgememo/forgememo_memory.db` | SQLite DB path |
| `FORGEMEM_CONFIG` | `~/.forgemem/config.json` | Provider config |
| `FORGEMEMO_SOURCE_TOOL` | `"unknown"` | Identifies the agent (claude, gemini, codex) |
| `FORGEMEMO_PROJECT_ID` | auto-detected | Override project ID |
| `FORGEMEM_API_URL` | `https://api.forgememo.com` | Managed service endpoint |
