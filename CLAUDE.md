# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository map

| Directory | What it is |
|-----------|-----------|
| `forgememo/` | CLI package (daemon, hooks, MCP server, scanner, worker) |
| `server/` | Managed-service backend (FastAPI, Groq inference, Stripe billing) |
| `tests/` | CLI/daemon test suite |

---

## Managed service backend (`server/`)

**Production URL:** `https://forgememo-server.onrender.com`
**Deployed on:** Render (service `srv-d75ea56a2pns738s0jl0`, auto-deploy from `main`)
**Runtime:** Python 3.12, uvicorn, FastAPI 0.111

### Inference backend

Uses **Groq** (`llama-3.1-8b-instant`) via OpenAI-compatible API — NOT Anthropic.

- Input: $0.05/MTok, Output: $0.08/MTok
- Typical distill call (~3k tokens in, 300 out): ~$0.00017 actual cost
- `PLATFORM_FEE_USD = 0.00483` → user is charged ~$0.005/call
- **Margin: ~96.5%**

### Pricing / credit packs

| Pack | Price | User balance added | Distills |
|------|-------|--------------------|----------|
| starter | $5 | $5.00 | ~1,000 |
| pro | $20 | $20.00 | ~4,000 |
| team | $50 | $50.00 | ~10,000 |

Free signup credit: `FREE_CREDIT_USD = 5.0` ($5 = ~1,000 free distills).

### Run locally

```bash
cd server
pip install -r requirements.txt

# Minimum required env vars (copy from .env.example or set manually)
export GROQ_API_KEY=gsk_...
export FORGEMEM_JWT_SECRET=any-random-secret
export RESEND_API_KEY=re_...
export STRIPE_SECRET_KEY=sk_test_...
export STRIPE_WEBHOOK_SECRET=whsec_...
export STRIPE_PRICE_STARTER=price_...
export STRIPE_PRICE_PRO=price_...
export STRIPE_PRICE_TEAM=price_...
export API_BASE_URL=http://localhost:8000
export WEBAPP_ORIGIN=http://localhost:3000

uvicorn main:app --reload --port 8000
```

### Test the backend manually

```bash
BASE=https://forgememo-server.onrender.com   # or http://localhost:8000

# 1. Request magic link (check email or server logs for the link)
curl -X POST $BASE/webapp-auth/send-link \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","callback_url":"http://localhost:3000/auth/callback"}'

# 2. After clicking link, grab the JWT from the redirect URL ?token=...
TOKEN=<jwt-from-redirect>

# 3. Check balance
curl $BASE/v1/balance -H "Authorization: Bearer $TOKEN"

# 4. Run a distill
curl -X POST $BASE/v1/inference \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Summarize: user fixed a bug in auth middleware","max_tokens":200}'

# 5. Check stats
curl $BASE/v1/stats -H "Authorization: Bearer $TOKEN"

# 6. Check activity log
curl $BASE/v1/activity -H "Authorization: Bearer $TOKEN"
```

### Stripe webhook (test mode)

Webhook registered at: `https://forgememo-server.onrender.com/webhooks/stripe`
Event: `checkout.session.completed`
Secret: set as `STRIPE_WEBHOOK_SECRET` on Render.

To test locally with Stripe CLI:
```bash
stripe listen --forward-to http://localhost:8000/webhooks/stripe
```

### Render environment variables (all set)

| Variable | Value / Notes |
|----------|--------------|
| `GROQ_API_KEY` | set |
| `GROQ_MODEL` | `llama-3.1-8b-instant` |
| `PLATFORM_FEE_USD` | `0.00483` |
| `FREE_CREDIT_USD` | `5.0` (1,000 free distills on signup) |
| `STRIPE_SECRET_KEY` | set (test mode) |
| `STRIPE_WEBHOOK_SECRET` | `whsec_miuXQ3...` |
| `STRIPE_PRICE_STARTER` | `price_1TGlYz1IOlcHvGDn5ppk45vR` |
| `STRIPE_PRICE_PRO` | `price_1TGlZ21IOlcHvGDnrcIAyKNk` |
| `STRIPE_PRICE_TEAM` | `price_1TGlZ61IOlcHvGDnCgVZmTs5` |
| `API_BASE_URL` | `https://forgememo-server.onrender.com` |
| `WEBAPP_ORIGIN` | `https://forgememory-app.vercel.app` |
| `RESEND_API_KEY` | set — email from `noreply@forgememo.com` |

---

## CLI package (`forgememo/`)

### Commands

```bash
# Install
pip install -r requirements.txt && pip install -e .

# Run all tests
pytest -q

# Run single test file
pytest tests/test_daemon_api.py

# Test env vars required for CI (no live daemon, no circuit breaker)
FORGEMEMO_MOCK_TRANSPORT=1 FORGEMEMO_DISABLE_BREAKER=1 pytest -q

# Start daemon manually
python -m forgememo.daemon

# Run MCP server
forgememo mcp --http
```

### Architecture

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

**CLI** (`cli.py` + `forgememo/commands/`) uses Typer. Key commands: `init`, `start`, `stop`, `status`, `search`, `store`, `config`, `auth`, `skill`.

### Key Design Decisions

**Global daemon, not per-project**: One daemon on port 5555 handles all projects; project isolation via `project_id` in DB queries.

**HTTP-first transport**: Unix socket tried first on POSIX for performance, HTTP fallback always available. `FORGEMEMO_HTTP_PORT` defaults to `5555` on all platforms — do not revert this.

**Transport in tests**: Always set `FORGEMEMO_MOCK_TRANSPORT=1` and `FORGEMEMO_DISABLE_BREAKER=1` in test environments. `conftest.py` does this automatically.

**Multi-provider inference**: `inference.py` routes to `anthropic`, `openai`, `gemini`, `ollama`, `claude_code`, or `forgememo` (managed) based on `~/.forgememo/config.json`.

When provider = `forgememo`, the CLI sends inference requests to the managed backend at `FORGEMEM_API_URL`.

### Key Files

| File | Purpose |
|------|---------|
| `forgememo/cli.py` | Typer app entry point |
| `forgememo/daemon.py` | Flask HTTP API server |
| `forgememo/mcp_server.py` | FastMCP server |
| `forgememo/hook.py` | Hook adapter — normalizes agent events, POSTs to daemon |
| `forgememo/storage.py` | SQLite + FTS5 schema and migrations |
| `forgememo/inference.py` | Multi-provider LLM routing |
| `forgememo/scanner.py` | Daily git/memory scanner (LaunchAgent) |
| `forgememo/worker.py` | Background distillation worker |
| `forgememo/commands/lifecycle.py` | init, start, stop, status, doctor |
| `server/main.py` | FastAPI managed-service backend |
| `server/billing.py` | Stripe checkout + webhook parsing |
| `server/db.py` | SQLite/MySQL ORM for the managed service |
| `server/auth.py` | JWT + magic link token helpers |

### Environment Variables (CLI)

| Variable | Default | Purpose |
|----------|---------|---------|
| `FORGEMEMO_HTTP_PORT` | `5555` | Daemon HTTP port |
| `FORGEMEMO_MOCK_TRANSPORT` | `0` | Skip real HTTP calls (tests) |
| `FORGEMEMO_DISABLE_BREAKER` | `0` | Disable circuit breaker (tests) |
| `FORGEMEM_DB` | `~/.forgememo/forgememo_memory.db` | SQLite DB path |
| `FORGEMEM_CONFIG` | `~/.forgemem/config.json` | Provider config |
| `FORGEMEMO_SOURCE_TOOL` | `"unknown"` | Identifies the agent (claude, gemini, codex) |
| `FORGEMEMO_PROJECT_ID` | auto-detected | Override project ID |
| `FORGEMEM_API_URL` | `https://forgememo-server.onrender.com` | Managed service endpoint |
