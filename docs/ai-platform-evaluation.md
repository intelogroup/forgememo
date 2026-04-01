# AI Inference Platform Evaluation for Forgememo Managed Service

**Date:** 2026-04-01
**Context:** Selecting the optimal AI inference provider for Forgememo's managed
service, where users pay per-distillation and Forgememo keeps a margin.

## Business Model

```
User picks "forgememo" provider in terminal
  -> forgememo auth login (magic link)
  -> session-end distillation triggers managed inference
  -> User pays: (token cost + PLATFORM_FEE) from their credit balance
  -> Forgememo margin = PLATFORM_FEE + (charge_to_user - wholesale_cost)
```

The goal: **maximize margin per distillation** while keeping inference quality
high enough for structured JSON extraction (mining, distilling, session summaries).

## Workload Profile

| Metric | Value |
|--------|-------|
| Task | Extract structured JSON from raw tool events |
| Input tokens | ~500-2000 per event |
| Output tokens | ~300-500 (structured JSON) |
| Latency tolerance | High (background, session-end) |
| Quality bar | Reliable JSON output with correct field extraction |
| Scale | Per-user, per-session (bursty, not streaming) |

## Provider Comparison

### Tier 1: Direct API (recommended for margin)

| Provider | Model | Input $/M | Output $/M | Cost per distill* | Quality | Margin at $0.005 charge |
|----------|-------|-----------|------------|-------------------|---------|------------------------|
| **Google Gemini** | gemini-2.0-flash | $0.10 | $0.40 | ~$0.00035 | Good | **93%** |
| OpenAI | gpt-4o-mini | $0.15 | $0.60 | ~$0.00053 | Good | 89% |
| Anthropic | claude-haiku-4-5 | $0.80 | $4.00 | ~$0.0036 | Excellent | 28% |

*Assuming 1500 input tokens + 400 output tokens per distillation.

### Tier 2: Aggregators (NOT recommended - double margin)

| Provider | Model access | Their markup | Problem |
|----------|-------------|-------------|---------|
| OpenRouter | 100+ models | 5-20% on top | Middleman eats our margin |
| AWS Bedrock | Multi-provider | ~20% markup | Enterprise overhead |

### Tier 3: Self-hosted (future consideration at scale)

| Provider | Model | Fixed cost | Break-even |
|----------|-------|-----------|------------|
| GPU server (Hetzner/OCI) | Llama 3.1 8B | ~$150/mo | ~400K distillations/mo |
| Oracle Cloud free tier | Llama 3.1 8B | $0 (free A1) | Immediately |

## Decision: Google Gemini 2.0 Flash (Direct API)

### Why

1. **Lowest cost per distillation** among production-grade APIs (~$0.00035)
2. **93% margin** at a $0.005 per-distillation charge to users
3. **Reliable structured JSON output** - Flash is optimized for fast, structured tasks
4. **Already integrated** in forgememo's inference.py (BYOK path)
5. **No middleman** - direct API key, full margin control
6. **Generous free tier** for development/testing

### Why NOT Oracle AI

- Not a model aggregator - limited to Llama/Cohere models
- Complex OCI setup (IAM, networking, compartments) for a simple API call
- No Anthropic/OpenAI/Gemini models available
- Enterprise-oriented pricing and tooling overkill for our workload

### Why NOT OpenRouter

- Adds 5-20% margin on top of provider costs
- We ARE the margin layer - don't want another middleman
- Reduces our profit per distillation unnecessarily
- Good for BYOK users who want model flexibility, but not for our managed backend

### Fallback Strategy

1. **Primary:** Google Gemini 2.0 Flash (cheapest, good quality)
2. **Fallback:** OpenAI gpt-4o-mini (if Gemini is down)
3. **Premium tier (future):** Claude Haiku for users willing to pay more

## Competitor Analysis: Claude-mem

Claude-mem (Claude Code memory plugin) does NOT offer managed services.
Likely revenue model: open-source + consulting/enterprise, or no revenue (side project).

Forgememo's managed inference model is a **competitive advantage**:
- Recurring usage-based revenue
- Zero-friction onboarding (no API key needed)
- Stickier than BYOK (users don't want to manage keys)

## Implementation (Inference — DONE)

Server changes completed:
1. Replace `anthropic` SDK with `google-genai` in `server/main.py`
2. Update `_estimate_cost()` with Gemini Flash pricing
3. Update default model from `claude-haiku-4-5-20251001` to `gemini-2.0-flash`
4. Update `server/requirements.txt` (swap anthropic for google-genai)
5. Update `server/.env.example` (GEMINI_API_KEY replaces ANTHROPIC_API_KEY)

---

# Cloud Stack Evaluation: Auth, Paywall, and Cross-Device Sync

**Context:** Choosing the cloud backend for three concerns:
1. Fast OAuth check-in + auto-login from the CLI terminal
2. Stripe paywall for managed inference credits
3. Opt-in cross-device sync — push local SQLite learnings to the cloud so a
   user benefits from Machine A's memories on Machine B

## Current State (what's already built)

| Component | Implementation | Files |
|-----------|---------------|-------|
| Auth | Custom magic links + Google/GitHub OAuth + JWT | `server/auth.py`, `server/main.py` |
| Billing | Stripe credit packs ($5/$20/$50) + webhooks | `server/billing.py` |
| Cloud DB | SQLite (local dev) / MySQL (production) dual schema | `server/db.py` |
| Sync | REST push/pull endpoints, device tracking, upsert | `server/main.py` `/v1/sync/*` |
| Deploy | Render.com (FastAPI + uvicorn) | `render.yaml` |

## Provider Comparison: Supabase vs OCI Oracle vs AWS

| | **Supabase** | **OCI Oracle (MySQL)** | **AWS (RDS + Cognito)** |
|---|---|---|---|
| Database | PostgreSQL | MySQL (Always Free) | MySQL or Postgres |
| Free tier | 500MB, 50K MAU | 50GB Always Free | None (paid from day 1) |
| Auth built-in | Magic links, Google, GitHub, Apple, Microsoft — zero code | No — build everything yourself | Cognito (complex, poor DX) |
| Row Level Security | Native PostgreSQL RLS | No — app-level `WHERE user_id=?` only | No — app-level only |
| Realtime sync | Built-in WebSocket subscriptions | No — build yourself | AppSync (separate service, $$) |
| Multi-tenant isolation | DB-level RLS policies | App-level only (one bug = data leak) | App-level only |
| Setup time | 5 minutes | 2+ hours (compartment, VCN, subnet, security list, wallet) | 1+ hour (VPC, SG, IAM roles) |
| SDK | `supabase-py` (excellent) | `pymysql` (raw SQL) | `boto3` + driver |
| JSON support | Native JSONB with GIN indexes | Basic JSON column, no indexing | Depends on engine |
| Cost at scale | $25/mo Pro | Free tier generous | $15-50/mo minimum |
| Vendor lock-in | Low (standard Postgres, self-hostable) | High (OCI-specific tooling) | High (Cognito, IAM, VPC) |

## Decision: Supabase

### Why Supabase wins for forgememo

**1. Delete code, don't write code**

Supabase Auth replaces custom magic link + OAuth code entirely:

| What | Current (custom) | With Supabase |
|------|-------------------|---------------|
| Magic link flow | `auth.py` (45 lines) + email sender + 3 endpoints | Built-in, zero code |
| Google OAuth | 55 lines of manual token exchange in `main.py` | Built-in, zero code |
| GitHub OAuth | 55 lines of manual token exchange in `main.py` | Built-in, zero code |
| JWT issuance/verify | Custom `create_session_token` / `verify_session_token` | Supabase issues JWTs |
| Session management | `sessions` table + custom logic | Handled by Supabase |
| Additional providers | Build each one manually | Toggle on in dashboard (Apple, Microsoft, etc.) |

**Net result:** Delete `auth.py`, delete ~150 lines of OAuth from `main.py`, delete
`sessions` and `magic_link_tokens` tables from `db.py`.

**2. Row Level Security = multi-tenant safety**

Current approach (app-level, one bug away from data leak):
```python
# db.py — every query must remember to filter by user_id
def pull_traces(self, user_id, ...):
    sql = "SELECT * FROM sync_traces WHERE user_id=? AND synced_at>?"
```

With Supabase RLS (enforced at DB level, impossible to bypass):
```sql
-- One-time policy, applies to ALL queries on this table
CREATE POLICY user_owns_traces ON sync_traces
  USING (user_id = auth.uid());

-- Now even if app code forgets WHERE user_id=?, RLS blocks cross-user access
```

**3. Realtime enables live cross-device sync (future)**

Current sync is pull-based: Machine B must poll `/v1/sync/pull?since=...`.

With Supabase Realtime: when Machine A pushes a trace, Machine B gets it
instantly via WebSocket. Zero additional infrastructure.

```python
# Future: client subscribes to their own traces
supabase.channel("sync").on("INSERT", table="sync_traces").subscribe()
```

**4. PostgreSQL > MySQL for forgememo's data**

Sync data is JSON-heavy (`content`, `tags`, `facts`, `concepts`). PostgreSQL
has native JSONB with GIN indexes for fast queries inside JSON fields.
MySQL's JSON support is basic and lacks indexable operators.

**5. Free tier covers early growth**

500MB storage + 50K monthly active users. At ~1KB per distilled summary,
that's ~500K summaries before hitting the limit. By then, Stripe revenue
easily covers the $25/mo Pro plan.

### Why NOT OCI Oracle

- **Auth:** None built-in. You keep maintaining `auth.py` + OAuth routes forever.
- **RLS:** None. Multi-tenant isolation is app-level only.
- **DX:** OCI console is painful — compartments, VCNs, security lists, auth tokens, wallets.
- **Best for:** Heavy compute (GPU for self-hosted Llama). Not for SaaS plumbing.
- **Free tier is generous** but not worth the operational complexity for this use case.
- **Verdict:** Good for future self-hosted inference. Bad for auth/sync/billing backend.

### Why NOT AWS

- **No free tier for RDS** — paying from day 1 for a database.
- **Cognito is painful** — notoriously bad DX for auth, especially magic links.
- **IAM + VPC overhead** — just to get a DB connection requires security groups, subnets, roles.
- **Best for:** Large enterprises with existing AWS footprint.
- **Verdict:** Overkill for early-stage. Revisit only if you land enterprise customers who require AWS.

## Architecture: After Supabase Migration

```
┌──────────────────────┐      ┌─────────────────────────────┐
│  User's Machine       │      │  Cloud                       │
│                       │      │                              │
│  SQLite (local)       │      │  Supabase (PostgreSQL + RLS) │
│  ├─ events            │─push─│  ├─ sync_traces              │
│  ├─ distilled_summaries│     │  ├─ sync_principles          │
│  └─ session_summaries │◄pull─│  ├─ users     (Supabase Auth)│
│                       │      │  ├─ usage_runs               │
│  forgememo CLI        │      │  └─ devices                  │
│  └─ JWT from Supabase │      │                              │
│                       │      │  Supabase Auth               │
└──────────────────────┘      │  ├─ Magic links (built-in)   │
                               │  ├─ Google OAuth (built-in)  │
┌──────────────────────┐      │  ├─ GitHub OAuth (built-in)  │
│  FastAPI (Render.com) │      │  └─ JWT auto-issued          │
│  ├─ POST /v1/inference│      │                              │
│  │   └─ Gemini Flash  │      │  Supabase Realtime (future)  │
│  ├─ Stripe webhooks   │      │  └─ live cross-device sync   │
│  └─ /v1/sync (proxy)  │      │                              │
└──────────────────────┘      │  Stripe (stays as-is)        │
                               │  └─ Credit packs + webhooks  │
                               └─────────────────────────────┘
```

## Migration Plan (incremental)

### Phase 1: Database migration (MySQL -> Supabase Postgres)
- Create Supabase project
- Migrate schema from `_SCHEMA_MYSQL` to Postgres (minimal changes)
- Add Postgres backend to `db.py` (alongside existing MySQL/SQLite)
- Set `DATABASE_URL=postgresql://...` on Render
- Enable RLS policies on sync tables

### Phase 2: Auth migration (custom -> Supabase Auth)
- Configure magic link + Google + GitHub providers in Supabase dashboard
- Update CLI `_do_auth_login()` to use Supabase Auth flow
- Update `_auth_user()` in `main.py` to verify Supabase JWTs
- Delete `auth.py`, `sessions` table, `magic_link_tokens` table
- Delete manual OAuth routes from `main.py`

### Phase 3: Cross-device sync enhancement
- Add Supabase Realtime subscriptions for `sync_traces` and `sync_principles`
- CLI daemon listens for realtime inserts from other devices
- Merge remote traces into local SQLite on arrival

### Phase 4 (future): Edge inference
- Move `/v1/inference` to Supabase Edge Functions (Deno)
- Lower latency for geographically distributed users
- Gemini Flash API call from edge, closer to user
