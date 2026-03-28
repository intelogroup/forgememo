# Forgemem

**Long-term memory and knowledge base built from your real activity as a developer.**

Forgemem mines your git history, session traces, and project notes to extract what actually happened — failures, successes, plans, and hard-won lessons. It stores them in a local SQLite database so that any agent you work with can learn from your past before wasting tokens walking down paths you've already hit dead ends on.

## The Problem It Solves

Every new agent session starts blind. It doesn't know that you already tried the Zod 4 schema approach and it broke. It doesn't know that wildcard CORS killed auth in production. It doesn't know that the cleanup phase always needs to come after the dev phase, not during.

Without Forgemem, agents repeat your mistakes. With it, they skip to what works.

## How It Works

1. **Daily scan** — runs against your git repos and memory files, extracts failure/success/plan traces via Claude, and saves them with an impact score
2. **SQLite DB** — stores all traces and distilled principles locally at `~/Developer/Forgemem/forgemem_memory.db`
3. **MCP server** — exposes the DB to agents via the Model Context Protocol so they can query it before starting any task

## MCP Access

### Claude Code (stdio — already configured)
Any Claude Code session on this machine has Forgemem registered globally in `~/.claude/settings.json`. No setup needed — just call `retrieve_memories` before starting work.

### Other agents (Gemini CLI, OpenCode, Copilot) — HTTP/SSE
Start the HTTP server:
```bash
cd ~/Developer/Forgemem
.venv/bin/python3 mcp_server.py --http
# → http://127.0.0.1:7474/sse
```
Then point your agent's MCP config at `http://127.0.0.1:7474/sse`.

## Tools Exposed

| Tool | Purpose |
|------|---------|
| `retrieve_memories` | Search principles and traces by keyword before starting a task |
| `save_trace` | Save a failure, success, plan, or note during/after a session |
| `forgemem_stats` | Summary of DB contents + agent connection history |

## Key Commands

```bash
# Run the daily knowledge mining scan
.venv/bin/python3 daily_scan.py

# Query from CLI
.venv/bin/python3 forgemem.py retrieve "auth debugging"

# Check DB stats + who connected
.venv/bin/python3 forgemem.py stats
```

## The Goal

Agents should always call `retrieve_memories` at the start of any task. That one call surfaces the top failures and principles from your history that match what they're about to do — so they don't burn tokens re-learning what you already know.
