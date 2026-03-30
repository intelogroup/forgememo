# ForgeMem — Competitive Moat Analysis

**Date:** 2026-03-30
**Status:** Internal Strategy Document

---

## What ForgeMem Is

ForgeMem is a persistent long-term memory system for AI coding agents. It mines git history and session notes, extracts reusable "principles" via LLM distillation, and surfaces them to agents (Claude Code, Copilot, Gemini, Codex) via MCP/HTTP before they start work. Core value prop: **agents stop repeating mistakes across sessions.**

---

## Moat Assessment: Weak to Moderate

### What's Defensible (Weak Moats)

| Factor | Strength | Why |
|--------|----------|-----|
| **Data network effect** | Weak | The more a user/team accumulates traces and principles, the more valuable the tool becomes. However, it's SQLite — trivially exportable — so switching cost is minimal. |
| **Multi-agent integration** | Moderate | Supports Claude Code, Copilot, Gemini, Codex from one DB. Being the shared layer across agents is a good wedge — no single vendor will build this cross-platform. |
| **First-mover in "agent memory"** | Weak | Category is new but the idea is obvious. Every agent platform will eventually ship native memory. |
| **Zero-friction onboarding** | Weak | `pip install forgemem && forgemem init` is slick, but easily replicable. |

### What's NOT Defensible

1. **No proprietary technology.** The core is SQLite + FTS5 full-text search + LLM summarization. Any competent engineer can replicate this in a weekend. The distillation algorithm is a single LLM prompt — no fine-tuned models, no novel embeddings, no proprietary ranking.

2. **Platform risk is existential.** Claude Code, Cursor, Copilot, and Gemini will all ship native persistent memory. Anthropic already has `~/.claude/projects/*/memory/` files. When these become first-class features with semantic search, ForgeMem's core value evaporates.

3. **No embedding/vector search.** FTS5 keyword matching is good but commoditized. No semantic similarity, no vector DB, no re-ranking model. A competitor using embeddings would immediately outperform on retrieval quality.

4. **Thin managed service margin.** The SaaS layer is a $0.02/call wrapper around Claude Haiku. There's no proprietary inference, no fine-tuned model, no unique data processing that justifies the margin long-term.

5. **Open source (Apache-2.0).** Great for adoption, terrible for defensibility. Anyone can fork, extend, and compete.

6. **No team/collaboration lock-in.** There's a sync feature, but no team dashboards, RBAC, shared knowledge graphs, or organizational memory that would create enterprise stickiness.

### The Real Threat Model

| Timeline | Threat |
|----------|--------|
| Now | Solo developers adopt ForgeMem ✓ |
| 6-12 months | Claude Code ships native semantic memory |
| 12-18 months | Cursor/Copilot do the same |
| 18+ months | ForgeMem becomes redundant for single-agent users |

The **only durable position** is as the **cross-agent memory layer** — the Switzerland that works across all AI coding tools.

### Competitive Update: Claude Code Auto-Memory (Feb 2026)

Since February 2026, Claude Code auto-writes `MEMORY.md` under `~/.claude/projects/<project>/memory/` — accumulating debugging insights, naming corrections, workflow preferences. Users can audit it with `/memory` and prune noise. It can be disabled with `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.

**This validates the market but does NOT kill ForgeMem.** Here's the gap:

| Feature | Claude Code MEMORY.md | ForgeMem |
|---------|----------------------|----------|
| Storage | Flat markdown file per project | Structured DB with FTS5, impact scoring, tags |
| Search | Agent reads entire file (brute force) | FTS5 search with project/type/impact filters |
| Cross-project | No — siloed per project | Yes — search across all projects |
| Cross-agent | Claude Code only | Claude + Copilot + Gemini + Codex |
| Auto-mining | Session notes only | Git history + session files + manual |
| Curation | Manual (`/memory` + prune weekly) | Automatic LLM distillation with impact scores |
| Sharing | Local file only | Cloud sync (opt-in) across devices |
| Quality control | Accumulates noise ("context liability") | Ranked principles, deduplicated, scored 1-10 |

The Claude Code blog itself calls uncurated MEMORY.md a **"context liability"** — that's literally what ForgeMem solves.

**Updated ForgeMem pitch:**

> "Claude Code writes MEMORY.md but it becomes a junk drawer you have to curate weekly. ForgeMem auto-mines your git history, distills actionable principles, ranks them by impact, and serves them across ALL your agents — not just Claude. Cross-project, cross-device, cross-agent."

**Strategic takeaway:** Claude Code built a flat file. ForgeMem should position as the **structured, searchable, cross-agent upgrade** to MEMORY.md. In fact, ForgeMem already mines MEMORY.md files as a data source (`daily_scan.py` scans `~/.claude/projects/*/memory/*.md`). The pitch becomes: "ForgeMem reads your Claude memory files, distills the best parts, and makes them available to all your agents."

**Revised threat assessment:** Claude Code's MEMORY.md is a weaker implementation than originally feared. The real threat is if Anthropic adds semantic search + cross-project querying to MEMORY.md. Monitor Claude Code changelogs for this. As long as MEMORY.md stays as flat files, ForgeMem has a clear upgrade path.

### Broader Competitive Landscape: Agent Memory Systems (2025-2026)

ForgeMem isn't just competing with platform-native memory (MEMORY.md). There's a growing ecosystem of agent memory tools. Here's how ForgeMem compares:

| System | Architecture | Key Differentiator | LoCoMo Score | vs ForgeMem |
|--------|-------------|-------------------|-------------|-------------|
| **Letta (MemGPT)** | Agent self-edits memory via tool calls. 3-tier: core/recall/archival | Agent-controlled curation, OS-inspired | ~83.2% | Conversational memory, not dev-history-aware |
| **Mem0** | Passive extraction pipeline, framework-agnostic | 91% lower latency, 90%+ token savings | ~68.5% | General-purpose memory, no git mining |
| **Zep** | Episodic + temporal memory | Structures interactions into sequences | N/A | Session-focused, no cross-agent |
| **LangMem** | JSON docs in LangGraph store | Tight LangGraph integration | N/A | Framework-locked (LangGraph only) |
| **SuperLocalMemory** | Local-first, mode-based | Privacy-first variants | ~87.7% | Research project, not production tool |

**Source:** [Letta benchmarks](https://www.letta.com/blog/benchmarking-ai-agent-memory) | [Mem0 paper (arXiv 2504.19413)](https://arxiv.org/abs/2504.19413) | [5 Memory Systems Compared (DEV)](https://dev.to/varun_pratapbhardwaj_b13/5-ai-agent-memory-systems-compared-mem0-zep-letta-supermemory-superlocalmemory-2026-benchmark-59p3)

**Key finding from Letta benchmarks:** A simple filesystem-based agent (no fancy memory) achieved 74.0% on LoCoMo with GPT-4o mini, beating Mem0's best graph variant (68.5%). Implication: sophisticated memory infrastructure isn't always better than simple persistence with good retrieval.

**Where ForgeMem is genuinely different:** All competitors above focus on **conversational memory** (what was said in chat). ForgeMem focuses on **operational memory** (what was tried, what failed, what worked in code). This is a distinct category. None of the competitors mine git history, distill from commit diffs, or rank learnings by impact score.

**The risk:** Letta and Mem0 both ship MCP servers now. They compete for the same MCP tool slot in an agent's config. If a user installs Mem0's MCP server, they may not feel the need for ForgeMem's. ForgeMem needs to clearly own the "developer operational memory" niche — not try to be general-purpose conversational memory.

**Positioning recommendation:** "Mem0/Letta remember what you said. ForgeMem remembers what you built."

---

## To Build a Real Moat

1. **Add vector/semantic search** (embeddings) to differentiate retrieval quality
2. **Build team-level knowledge graphs** that create organizational lock-in
3. **Become the cross-agent standard** before platforms ship native memory
4. **Develop a proprietary ranking/distillation model** fine-tuned on coding patterns
5. **Ship enterprise features** (SSO, audit, compliance) that make switching painful

---

## Open Strategic Questions

### 1. ForgeMem as Scheduled Inference (MacBook-Lid-Closed Mode)

The team is exploring using ForgeMem's managed inference as a **background scheduled task** that runs even when the user's MacBook lid is closed (sleeping but charging, not powered off).

**The idea:** Auto-mining and distillation happens on a schedule via the managed cloud service, not the local machine. The user's laptop doesn't need to be awake — the cloud service pulls from synced git history / traces and distills principles on a cron. When the laptop wakes, fresh principles are waiting.

**Why this matters for moat:**
- Moves core value from local CLI (easy to replace) to **cloud-hosted intelligence** (stickier)
- Creates a "set and forget" experience competitors can't match without their own infra
- Justifies managed service pricing — users pay for always-on learning, not just per-call inference

**Open questions:**
- How does the cloud service access git history without the local machine? (Requires GitHub/GitLab integration or periodic sync-push)
- Scheduling UX — cron config vs. smart triggers (e.g., "after every PR merge")
- Battery/wake behavior on macOS (launchd can wake for network tasks, but reliability varies)

### 2. White-Label Cheap Model as CLI Distillator

The team wants to **white-label a cheap/small model** (e.g., a fine-tuned Llama, Phi, or Mistral) that ships as ForgeMem's built-in distillation engine, passed through the user's CLI on auto-scheduled mining runs.

**The idea:** Instead of routing distillation through Anthropic/OpenAI APIs (costly, requires API keys), bundle a lightweight model that runs locally or via ForgeMem's managed endpoint. Users get "free" distillation without BYOK friction.

**Why this matters for moat:**
- **Removes the biggest adoption friction** — no API key needed for core functionality
- Creates a **differentiated distillation layer** (fine-tuned on coding patterns) that competitors can't trivially replicate. Note: the fine-tuned model weights would be closed-source even though the ForgeMem CLI remains Apache-2.0 — this is a **dual-licensing strategy** (open-source tool, proprietary model). This mirrors how Ollama (open CLI) distributes closed-weight models. The moat comes from the training data and fine-tuning, not the code.
- Enables **offline-first auto-scheduling** — the daemon mines + distills without any network calls
- The managed version becomes the premium tier (better model, cloud storage, sync)

**Open questions:**
- **Which base model?** Needs to be small enough for local inference but good enough for principle extraction. Updated candidates (March 2026):

  | Model | Params | Benchmark Signal | Context | Quantized RAM | License |
  |-------|--------|-----------------|---------|---------------|---------|
  | **Qwen3.5-9B** | 9B | MMLU-Pro 82.5 | 128K | ~6GB (Q4) | Apache-2.0 |
  | **Gemma 3 4B IT** | 4B | HumanEval 71.3% | 128K | ~3GB (Q4) | Open |
  | **Phi-4-mini-instruct** | 3.8B | GSM8K 88.6% | 16K | ~2.5GB (Q4) | MIT |
  | **SmolLM3-3B** | 3B | Competitive (multi-benchmark) | 8K | ~2GB (Q4) | Apache-2.0 |

  Recommendation: Start with **Gemma 3 4B IT** or **Phi-4-mini-instruct** — best balance of size, coding ability, and permissive licensing for fine-tuning. Qwen3.5-9B is strongest but may be too large for low-end machines.
- **Fine-tuning data:** Where does training data come from? Could use anonymized traces from managed service users (with consent) or synthetic data from larger models.
- **Distribution:** Ship as GGUF via `forgemem install-model`? Or use Ollama as a dependency? Or embed llama.cpp?
- **Quality bar:** How good does distillation need to be? If the cheap model extracts mediocre principles, it hurts trust in the whole system.
- **White-label branding:** Does "ForgeMem Distill" feel like a product, or does it feel like a cost-cutting measure? Positioning matters.

### 3. Oracle Cloud MySQL + Cross-Device Memory Sync

**What exists today:**
- Oracle Cloud account with MySQL cloud instance available
- Server (`server/db.py`) already supports dual backend: SQLite locally, OCI MySQL via `DATABASE_URL` env var (pymysql)
- Sync tables exist: `sync_traces`, `sync_principles`, `devices`
- Push/pull endpoints work: users can sync memories across machines

**Why this matters for moat:**
- Cross-device memory is a **real differentiator** — no competing tool offers "your agent remembers what you did on your work laptop when you switch to your personal machine"
- Oracle Cloud MySQL is cheap/free-tier friendly, keeping infra costs low
- Centralizing memory in the cloud is a prerequisite for the scheduled inference idea (item #1 above)

**Current status:** Infra is ready, sync API works, but adoption depends on the auth story (see below).

### 4. Auth Gap: No GitHub / Google OAuth in the Next.js Webapp

**What exists today:**
- Custom magic link auth only (email → Resend/Mailpit → JWT)
- CLI auth works via local loopback server (`127.0.0.1:47474/callback`)
- Webapp auth works via `fm_token` cookie (30-day JWT, HS256)
- No NextAuth / Auth.js — everything is hand-rolled in `server/auth.py`

**The problem:**
When a user picks "forgemem" as their managed provider during `forgemem init`, they're redirected to the webapp to authenticate. Today the only option is magic link email. This is **high friction for developer users** who expect "Sign in with GitHub" or "Sign in with Google" — one click, no email checking, no token expiry confusion.

**What's needed:**
- **GitHub OAuth** — natural fit for developer tool, ties identity to their repos
- **Google OAuth** — covers non-GitHub users, enterprise Google Workspace accounts
- **Keep magic link** as fallback for users without GitHub/Google

**Implementation options (researched March 2026):**

| Provider | Free Tier | Pricing at Scale | Oracle MySQL Compat | Integration Effort | Verdict |
|----------|-----------|-------------------|--------------------|--------------------|---------|
| **WorkOS AuthKit** | **1M MAU free** | $0.30/MAU after 1M | ✅ JWT-only — your DB, your schema | Medium (official FastAPI SDK + Next.js SDK) | **🏆 Recommended** |
| **Auth.js v5 + Drizzle** | **Free forever** | $0 (self-hosted) | ✅ Drizzle MySQL adapter writes directly to Oracle MySQL | Medium-High (replace `server/auth.py`, add Drizzle schema) | **Strong alternative** |
| **Clerk** | 50K MAU free | **~$1,000+/mo at 100K MAU** ($0.02/MAU) | ⚠️ JWT verification only — user data lives in Clerk | Low (drop-in React components) | Too expensive at scale |
| **Oracle IDCS** | None | **$3-10/user/month** | ✅ Native Oracle integration | High (SAML/OIDC only, no Next.js/FastAPI SDK) | Skip — overpriced, enterprise-only |
| **Supabase Auth** | 50K MAU free | $0.00325/MAU | ❌ Forces Postgres (unused DB alongside Oracle) | Low | Skip — wrong DB dependency |

#### Recommendation: WorkOS AuthKit (primary) or Auth.js (if zero-cost is critical)

**Why WorkOS wins for ForgeMem:**
1. **1M MAU free** — effectively free until ForgeMem hits serious scale
2. **Official FastAPI SDK** (`workos-python`) — `verify_session()` in `server/auth.py` replaces hand-rolled JWT verification
3. **Built-in magic link + GitHub + Google** — all three flows with zero custom OAuth code
4. **JWT-only model** — WorkOS handles identity, ForgeMem keeps all user/billing data in Oracle MySQL. No vendor lock-in on user data
5. **CLI loopback flow preserved** — WorkOS supports PKCE + custom redirect URIs, so `127.0.0.1:47474/callback` still works

**Architecture with WorkOS:**
```text
CLI (forgemem init → "forgemem" provider)
  → Opens browser to webapp/auth
  → WorkOS AuthKit handles GitHub/Google/magic link
  → WorkOS returns JWT to webapp
  → Webapp redirects to 127.0.0.1:47474/callback?token=<jwt>
  → CLI stores JWT in ~/.forgemem/config.json

FastAPI (server/auth.py):
  → workos.verify_session(token) replaces manual HS256 JWT decode
  → On first auth, upsert user row in Oracle MySQL users table
  → All billing/credits/sync data stays in Oracle MySQL
```

**Why Auth.js is the fallback:**
- **$0 forever** — no per-MAU pricing at any scale
- **Drizzle MySQL adapter** writes `users`, `accounts`, `sessions` tables directly into Oracle MySQL — total data ownership
- Requires more code changes: replace `server/auth.py` entirely, add Drizzle schema migration, handle Next.js ↔ FastAPI session sharing
- Better if the team wants zero external auth dependencies

**Why NOT Clerk:** At $0.02/MAU, 100K users = $1K/mo. ForgeMem's free-tier Ollama users would count against MAU. The math doesn't work for a tool with a large free user base.

**Why NOT Oracle IDCS:** Enterprise-only pricing ($3-10/user/month), SAML-focused, no modern SDK for Next.js or FastAPI. Designed for internal corporate SSO, not consumer developer tools.

**Migration path from current magic link:**
1. Add WorkOS AuthKit to webapp (`@workos-inc/nextjs`)
2. Replace `server/auth.py` JWT verification with `workos.verify_session()`
3. Keep existing `users` table in Oracle MySQL — WorkOS only handles identity
4. CLI loopback flow: swap magic link callback for WorkOS PKCE callback
5. Existing JWTs: add migration middleware that accepts old HS256 JWTs for 30 days, then expire

---

## Technical Deep Dives

### Business Model Reality: Who Pays, Who Doesn't

Before the architecture, the revenue model must be clear:

```text
forgemem init -> "Choose your provider"

  "ollama"      -> 100% local, NO auth, no cloud, FREE
                   (traction play -- gets users in the door)

  "anthropic"   -> BYOK, NO auth, no cloud, FREE
  "openai"      -> BYOK, NO auth, no cloud, FREE
  "gemini"      -> BYOK, NO auth, no cloud, FREE
                   (these users may convert to forgemem later)

  "forgemem"    -> Auth login (magic link today, OAuth planned),
                   cloud inference, PAID                   <-- REVENUE
                   (this is the ONLY path that requires auth)
```

**Note:** This describes the _target_ architecture. OAuth (GitHub/Google) is not yet implemented — see the "Auth Gap" section above for current state. Today, auth uses magic link email only.

**Key rules:**
- **Ollama/BYOK users are never authed.** No account, no cloud, no sync. Everything stays in `~/.forgemem/forgemem_memory.db`. They are free users who may convert later.
- **Auth only happens when user picks "forgemem" as provider.** That's the moment they create an account (OAuth, planned; currently magic-link only), get a JWT, and start using paid cloud inference for mining and distillation.
- **SQLite DB stays local by default.** Cloud sync is an explicit opt-in (`forgemem sync`), only available to authed "forgemem" provider users. Solo devs with one computer never need it.
- **Ollama makes zero revenue** but drives adoption. The funnel: Ollama (free) -> hits quality ceiling -> switches to "forgemem" provider (paid).

---

### How Can ForgeMem Provide Inference Like an AI Subscription?

The goal: when a user picks "forgemem" as provider, distillation "just works" -- no API keys, no model management. The user pays ForgeMem, ForgeMem handles the rest.

#### Hosted Inference on Oracle Cloud (The Revenue Path)

ForgeMem already has this partially built (`POST /v1/inference` in `server/main.py`). Today it proxies to Anthropic's API. The change: **swap the Anthropic backend for a self-hosted cheap model on Oracle Cloud.**

```text
User picks "forgemem" provider        Oracle Cloud (ForgeMem Server)
───────────                   ─────────────────────────────
forgemem init
  -> picks "forgemem"
  -> Auth (magic link today, OAuth planned) -> creates account, issues JWT
  -> JWT saved to config.json

daily_scan.py runs (lid open)
  -> inference.py detects provider="forgemem"
  -> POST /v1/inference -----------> FastAPI receives request
     Bearer: <jwt>                    -> verifies JWT, checks credits
                                      -> routes to vLLM serving Gemma-3-4B-IT
                                      -> deducts credits
                                   <-- returns distilled principles
  -> saves to LOCAL SQLite only
  -> (cloud sync only if user opted in)
```

**How to do it:**
1. Deploy **vLLM** or **text-generation-inference (TGI)** on Oracle Cloud with a quantized open model (Gemma 3 4B IT Q4 fits in ~3GB VRAM)
2. Oracle Cloud free tier includes Ampere A1 instances (ARM, 24GB RAM) — enough for CPU inference of small models. For GPU, OCI A10 instances are ~$1/hr
3. Change `server/main.py` `/v1/inference` to call the local vLLM endpoint instead of Anthropic:
   ```python
   # Instead of: client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
   # Use: requests.post("http://vllm-internal:8000/v1/completions", ...)
   ```
4. **Cost model flips**: no per-token Anthropic cost. Fixed infra (~$50-150/mo GPU instance) amortized across users. Platform fee ($0.02/call) becomes real margin.
5. Users see no difference -- same endpoint, same JWT, same response format

**Why this is the revenue path:**
- Ollama users pay nothing -> ForgeMem earns nothing
- BYOK users pay their own provider -> ForgeMem earns nothing
- "forgemem" provider users pay credits -> **ForgeMem earns margin on every distillation call**
- Self-hosting the model means the margin is infra cost, not Anthropic's markup

#### Ollama: The Free Tier / Traction Play (Zero Revenue)

Ollama users are **not customers, they're future customers.** The strategy:

```text
Day 1:   User picks ollama -> free, works for basic distillation
Day 30:  User notices: principles from llama3.2 are mediocre
         compared to what "forgemem" provider extracts
Day 31:  User runs: forgemem config provider forgemem
         -> OAuth login -> starts paying -> better principles
```

Ollama is the gateway. It works, it's free, but the quality ceiling pushes users toward the paid tier. **Do not invest in making Ollama quality great** -- that's the whole point.

What to invest in:
- Make Ollama setup frictionless (auto-detect, auto-pull model)
- Make the quality gap visible (e.g., "forgemem" principles have higher impact scores)
- Make switching from ollama to forgemem a one-command upgrade

#### Future: Hybrid Fallback (Long-Term)

```text
┌──────────────────────────────────────────────────────────┐
│  User picks "forgemem" provider                          │
│                                                          │
│  ┌─ Online? ──→ Cloud inference (Oracle vLLM)            │
│  │              Fast, best model, costs credits           │
│  │                                                       │
│  └─ Offline? ─→ Local Ollama (forgemem/distill-4b)       │
│                 Free, works anywhere, good-enough quality │
└──────────────────────────────────────────────────────────┘
```

For "forgemem" provider users only: change `_call_forgemem_managed()` in `inference.py` to:
1. Try cloud endpoint first (paid, best quality)
2. If network fails or credits exhausted -> fall back to local Ollama model (free, good-enough)
3. User never notices the switch

This makes "forgemem" provider feel like a subscription that always works, online or off. But this is a later optimization -- ship cloud-only first.

---

### How Can ForgeMem Auto-Mine While the MacBook Lid Is Closed?

**The core problem:** macOS sleeps when the lid closes. LaunchAgents stop. Mining stops.

**The answer: mine on the server, not the laptop.** But only for "forgemem" provider users who opted into sync.

#### Who Gets Cloud Mining?

- **Provider = ollama / BYOK** -> NO cloud mining. Mining only runs locally via LaunchAgent. Lid closed = no mining. That's fine -- it's the free tier. Everything stays in local SQLite.
- **Provider = forgemem + opted into sync** -> YES cloud mining. Server mines via GitHub webhooks. Lid closed = mining continues server-side. Principles stored in Oracle MySQL. Synced to local SQLite when laptop wakes. THIS IS A PAID FEATURE.

Cloud mining is a **premium feature** that only "forgemem" provider users get. It's part of what they're paying for.

#### The Architecture: GitHub Webhooks -> Server-Side Mining

```text
Developer pushes code                 Oracle Cloud Server
─────────────────────                 ──────────────────
git push origin main
  -> GitHub webhook fires -----------> POST /webhooks/github
                                        -> fetches diff via GitHub API
                                        -> distills via local vLLM
                                        -> saves to MySQL (user's account)
                                        -> deducts credits

MacBook wakes up next morning
  -> LaunchAgent fires on wake
  -> forgemem sync (auto)
  -> GET /v1/sync/pull ---------------> returns principles mined overnight
  -> inserts into local SQLite
  -> agent has fresh knowledge
```

**Prerequisites (in order):**
1. **GitHub OAuth** -- user authenticates, grants repo access
2. **User opts into sync** -- explicit consent to send data to cloud
3. **User connects repos** -- picks which repos to auto-mine (not all by default)
4. **Webhook registration** -- server registers GitHub webhooks on selected repos
5. **Server-side mining** -- port `daily_scan.py` extraction logic to `server/main.py`

**What's needed to build:**
- `POST /webhooks/github` endpoint in `server/main.py` (~50 lines)
- GitHub API integration to fetch commit diffs (`PyGithub` or raw REST)
- Server-side distillation function (reuse `daily_scan.py` extraction logic + vLLM)
- Repo management UI in webapp (select repos, enable/disable webhooks)
- Auto-sync on wake: add `WatchPaths` trigger to LaunchAgent plist for network changes

#### macOS Power Nap / pmset Wake (Not Recommended)

These try to keep the laptop mining locally:
- **Power Nap**: unreliable for third-party daemons, Apple controls access
- **pmset scheduled wake**: requires `sudo`, wakes entire machine, hacky

**Verdict:** Don't bother. Cloud mining is the right answer and it's also the paid feature that justifies the subscription.

---

### Recommended Architecture (Updated)

```text
USER'S MACHINE
===============
Provider: ollama / BYOK
  -> Everything local. No auth. No cloud. No sync.
  -> Mining via LaunchAgent (lid must be open)
  -> SQLite DB stays on this machine forever
  -> FREE

Provider: forgemem
  -> OAuth login (GitHub/Google) -> JWT
  -> Mining via cloud inference (POST /v1/inference)
  -> Results saved to local SQLite
  -> OPTIONAL: opt into sync -> cloud also has your principles
  -> OPTIONAL: opt into cloud mining -> lid-closed mining works
  -> PAID (credits)

Lid opens after sleep:
  -> if synced: forgemem sync pulls overnight principles
  -> if not synced: nothing changed, local DB intact

                    | (only forgemem provider + sync opt-in)
                    v
ORACLE CLOUD SERVER
====================
vLLM (Gemma 3 4B IT)  <-- serves /v1/inference for forgemem users

GitHub webhooks        <-- mines repos while laptop sleeps
  -> only for opted-in repos
  -> distills via same vLLM
  -> stores in MySQL (per-user, per-device)

/v1/sync/pull          <-- serves principles to waking laptops
/v1/sync/push          <-- receives local traces (opt-in only)

MySQL (Oracle Cloud)   <-- user data only for opted-in users
Auth (GitHub/Google)   <-- only for "forgemem" provider users
```

**The funnel:**
1. `ollama` -> free, local, good-enough quality -> **traction**
2. User hits quality ceiling -> switches to `forgemem` provider -> **revenue**
3. User wants multi-device -> opts into sync -> **stickiness**
4. User wants lid-closed mining -> connects GitHub repos -> **lock-in**

Each step increases value AND switching cost. That's the moat.

---

## Code Audit: Gotchas and Technical Questions for the Team

After a thorough read of the actual source code, here are the issues the team should address before scaling to paid users. Grouped by severity.

### CRITICAL (Fix Before Accepting Payments)

**1. Double-spend on credit deduction (`server/main.py:147-181`, `server/db.py:251-261`)**
The inference endpoint checks balance, then runs inference, then deducts credits in separate transactions. Two concurrent requests can both pass the balance check and deduct, leaving the user with negative balance. Use `BEGIN IMMEDIATE` (SQLite) or `SELECT ... FOR UPDATE` (MySQL) to make check-and-deduct atomic.

> Question for team: Are you seeing negative balances in production? Even with low traffic this will happen eventually.

**2. Stripe webhook replay / double-credit (`server/main.py:205-223`)**
`stripe_event_seen()` does a SELECT then INSERT in separate statements. Two simultaneous webhook deliveries (Stripe retries) can both pass the check and credit the user twice. Fix: use `INSERT ... ON CONFLICT DO NOTHING` and check the affected row count, or add a UNIQUE constraint on `event_id` (may already exist but not enforced atomically).

> Question for team: Is `stripe_events.event_id` a UNIQUE constraint? If not, add one immediately.

**3. Magic link token — defensive SQL improvement (`server/db.py:310-321`)** *(LOW)*
`consume_magic_link_token()` uses `SELECT ... WHERE used=0` then `UPDATE SET used=1`. Tokens are effectively single-use because the SELECT filters on `used=0` — a second request returns `None`. Under SQLite's serialized writes this is not exploitable in practice. However, as a defensive SQL best practice, the UPDATE could include the `AND used=0` predicate and check `rowcount == 1` to make it atomic even on MySQL with concurrent connections:
```python
# Defensive: atomic single-use even under MySQL concurrency
self._exec(conn, self._q("UPDATE magic_link_tokens SET used=1 WHERE token=? AND used=0"), (token,))
if cursor.rowcount != 1:
    return None  # already consumed
```

**4. JWT secret validation at startup (`server/auth.py:10-18`)** *(LOW)*
`_secret()` correctly raises `RuntimeError("FORGEMEM_JWT_SECRET env var required")` when the env var is missing — this does NOT fail silently. However, the error only surfaces on the first auth request rather than at server startup. For faster feedback in production, consider validating at startup:
```python
# In server/main.py or server/auth.py module level
if not os.environ.get("FORGEMEM_JWT_SECRET"):
    sys.exit("FORGEMEM_JWT_SECRET env var required — server cannot start without it")
```

**5. Floating-point currency math (`server/main.py:122`, `server/db.py:256`)**
`balance_usd` is a float. After thousands of transactions, rounding errors accumulate. Use `Decimal` or store as integer cents.

> Question for team: What type is `balance_usd` in MySQL? If it's FLOAT, change to DECIMAL(10,4).

### HIGH (Fix Before Public Launch)

**6. No rate limiting on magic link endpoints (`server/main.py:271-289, 375-401`)**
`/cli-auth/send-link` and `/webapp-auth/send-link` have zero rate limiting. An attacker can spam magic links to any email, exhausting your Resend quota and harassing users. Add per-email (3/hour) and per-IP (10/minute) limits.

**7. No request size limits on FastAPI**
No `max_request_size` configured. An attacker can POST gigabytes to `/v1/sync/push` and OOM the server. Add middleware: `app.add_middleware(RequestSizeLimitMiddleware, max_size=10_000_000)`.

**8. Unbounded sync responses (`server/main.py:251-264`)**
`/v1/sync/pull` returns ALL traces since `since=0` with no pagination. A user with 100K traces will get a multi-megabyte JSON response. Add `limit` and `offset` parameters, cap at 1000 per request.

**9. CORS is too permissive (`server/main.py:39-46`)**
`allow_methods=["*"]` and `allow_headers=["*"]` should be explicit lists. Change to `allow_methods=["GET", "POST", "OPTIONS"]`.

**10. API keys stored in plaintext (`~/.forgemem/config.json`)**
API keys for Anthropic/OpenAI/Gemini are stored unencrypted on disk. Anyone with filesystem access can read them. Consider using OS keychain (`keyring` library) or at minimum warn users.

> Question for team: Is this documented? Do users know their keys are in a plain JSON file?

**11. Git hooks can execute during scanning (`daily_scan.py`)**
When `daily_scan.py` runs `git log` in discovered repos, git hooks in those repos can execute arbitrary code. Fix: pass `-c core.hooksPath=/dev/null` to all git subprocess calls.

**12. No CSRF protection on webapp POST requests**
The webapp middleware checks `fm_token` cookie but has no CSRF token on state-changing requests. A malicious site can submit POST requests to your API using the user's cookies.

### MEDIUM (Fix Before Scale)

**13. No database migration strategy (`server/db.py`)**
Schema uses `CREATE TABLE IF NOT EXISTS` which won't apply column additions or type changes. You need a migration system (Alembic, or even a simple version table with sequential SQL scripts) before the first schema change.

> Question for team: How do you plan to evolve the schema? What happens when you need to add a column to `traces`?

**14. Deduplication is fragile (`daily_scan.py`)**
`is_duplicate()` compares first 120 characters of content. Two different learnings that start the same way are falsely deduplicated. Two identical learnings with different prefixes are falsely unique. Use SHA-256 hash of full content instead.

**15. Cost estimation is inaccurate (`server/main.py:118-122`)**
`input_tokens = len(prompt) / 4` is a rough guess. Real tokenization can differ by 2-3x. If you underestimate, you eat the cost. If you overestimate, users are overcharged.

> Question for team: Are you tracking actual vs estimated costs? What's the variance?

**16. No health check endpoint**
No `/health` or `/ready` endpoint for load balancer probes, uptime monitoring, or deployment validation.

**17. Expired sessions never cleaned up (`server/db.py:325-342`)**
Sessions have `expires_at` but are never deleted. Table grows forever. Add a cleanup cron or TTL-based deletion.

**18. Missing indexes on hot query paths**
`sessions` table has no index on `user_id`. `magic_link_tokens` has no index on `email`. These become full table scans at scale.

### LOW (Tech Debt)

**19.** `fcntl` file locks in scanner don't work on Windows -- use `filelock` library for cross-platform support.

**20.** Hard-coded `~/Developer` scan root and macOS-specific LaunchAgent paths. These should be configurable.

**21.** No audit logging of auth events, credit changes, or API calls. Makes incident response impossible.

**22.** Error messages leak internal state (balance amounts in 402 responses, different errors for valid vs invalid tokens).

**23.** `inference.py` only catches `ConnectionError` -- timeouts, SSL errors, and HTTP errors crash the CLI.

**24.** Principle content is rendered as raw markdown with no sanitization -- could inject formatting in agent prompts.

---

### Summary: Questions I'd Ask the Team

1. **Have you load-tested the credit system?** The double-spend bug is critical for a billing product.
2. **What's your migration plan for the DB schema?** `CREATE TABLE IF NOT EXISTS` won't survive the first schema change.
3. **Are you verifying Stripe webhook signatures?** I see event dedup but not signature verification with `stripe.Webhook.construct_event()`.
4. **What happens when vLLM goes down?** No health checks, no fallback, no circuit breaker on the inference path.
5. **How are you monitoring costs?** The `len(prompt)/4` estimation could be losing or gaining money on every call.
6. **Is `balance_usd` a FLOAT or DECIMAL in MySQL?** Float will cause audit failures.
7. **Who can access `~/.forgemem/config.json`?** It contains API keys and JWT tokens in plaintext.
8. **What's your plan for schema evolution?** Adding a column to `traces` or `principles` won't work with current approach.
9. **Are magic link emails rate-limited upstream (Resend)?** If not, your endpoint is an email bombing vector.
10. **Have you tested concurrent sync pushes from two devices?** The upsert logic may have race conditions.

---

## Verdict

ForgeMem solves a real problem at the right time, but has almost no structural moat today. The defensibility comes entirely from execution speed and user-accumulated data, both of which are fragile.

The four items above form a **connected stack** that, built together, would create a real moat:

```text
┌─────────────────────────────────────────────────┐
│  4. GitHub/Google OAuth (unblocks adoption)      │
│     ↓                                            │
│  3. Oracle MySQL cross-device sync (stickiness)  │
│     ↓                                            │
│  1. Cloud-scheduled inference (always-on value)  │
│     ↓                                            │
│  2. White-label distillation model (proprietary) │
└─────────────────────────────────────────────────┘
```

**Priority recommendation:**
1. **OAuth first** — it's the biggest UX friction point. Developer users expect social login; magic-link-only auth (which works today via `server/main.py:271-314`) limits conversion. Add GitHub + Google OAuth to the Next.js webapp.
2. **Cross-device sync** — already built, but useless without frictionless auth. Once OAuth ships, promote sync as a killer feature.
3. **Cloud-scheduled inference** — with auth + sync in place, this becomes the paid tier differentiator.
4. **White-label model** — longer-term proprietary moat. Ship after the cloud layer is generating revenue. (Note: this is emphasized in external communications because it's the most technically novel initiative and best illustrates long-term differentiation, even though OAuth/sync are tactical prerequisites that ship first.)

---

## Modern CLI + Agent-Native Design: Top Changes and Additions

Based on current best practices from leading developer CLIs (uv, gh, Vercel CLI, Supabase CLI, Charm.sh ecosystem) and the emerging agent-CLI paradigm (Claude Code, Copilot CLI, Codex), here are the top changes that would make ForgeMem both a better human CLI **and** a better agent tool.

### The Paradigm Shift: Agents Are Your Primary Users Now

The 2025 Stack Overflow survey shows 78% of developers spend over half their day in a terminal (up from 62% in 2023). Every major AI coding assistant (Claude Code, Copilot CLI, Cursor terminal, Codex) operates through the command line. ForgeMem already ships an MCP server, which puts it ahead of most tools. But the CLI itself was designed for humans first, agents second.

**The paradigm flip:** In the agent era, your MCP tools are the primary interface. The human CLI is the secondary interface. Most ForgeMem interactions will come from agents calling `retrieve_memories` and `save_trace` — not humans typing `forgemem search`. Design accordingly.

---

### TOP 10 CHANGES (Ranked by Impact)

#### 1. Add `--json` Flag to Every Command (Agent-Readable Output)

**Current state:** CLI outputs Rich-formatted markdown with colors, panels, and tables. Great for humans, unusable for agents or scripts.

**What modern CLIs do:** `gh` (GitHub CLI), `kubectl`, `uv`, and `supabase` all support `--json` flags that output structured JSON. This is now table stakes.

**What to change:**
```bash
# Today: human-only output
forgemem search "caching"
> Principle: Use Redis for session caching (impact: 8)

# Should also support:
forgemem search "caching" --json
{"principles": [{"id": 42, "principle": "Use Redis...", "impact_score": 8, "tags": ["redis","performance"]}]}

forgemem stats --json
{"traces": 156, "principles": 42, "undistilled": 12, "top_projects": ["api", "frontend"]}
```

**Why it matters:**
- Agents calling ForgeMem via subprocess (not MCP) can parse structured output
- CI/CD pipelines can consume ForgeMem data
- Other tools can build on top of ForgeMem
- Pattern from `gh`: `gh pr list --json number,title,author` lets callers pick fields

**Implementation:** Add a `--json` / `--format json` flag to `search`, `stats`, `list`, `save`. In `cli.py`, check the flag and call `json.dumps()` instead of Rich formatting.

**Bonus — TTY auto-detection:** When `stdout.isatty()` is false (piped to another command or agent subprocess), auto-switch to JSON output without requiring `--json`. This makes ForgeMem pipe-friendly with zero config:
```bash
# Human in terminal: gets Rich formatted output
forgemem search "caching"

# Agent or pipe: automatically gets JSON
forgemem search "caching" | jq '.principles[0]'
```

**Bonus — field selection (from `gh` pattern):**
```bash
forgemem search "auth" --json --fields principle,score,tags
```
Lets agents request only what they need, reducing token waste.

**Source:** [clig.dev](https://clig.dev/) | [12 Rules of Great CLI UX](https://dev.to/chengyixu/the-12-rules-of-great-cli-ux-lessons-from-building-30-developer-tools-39o6) | [CLI tools beating MCP by 33% in token efficiency](https://jannikreinhard.com/2026/02/22/why-cli-tools-are-beating-mcp-for-ai-agents/)

#### 2. Richer MCP Tool Descriptions with Usage Examples

**Current state:** MCP tools have functional docstrings but lack examples and edge-case guidance. When an agent reads the tool description, it has to guess the right parameters.

**What good MCP tools do:** Include usage examples, common patterns, and "when to use this vs. that" guidance directly in the tool description. The agent's only context for knowing how to use a tool is the description string.

**What to change in `mcp_server.py`:**
```python
# Before:
@mcp.tool()
def retrieve_memories(query: str, k: int = 5, project: str | None = None, type: str | None = None) -> str:
    """Search the memory database for relevant principles and traces."""

# After:
@mcp.tool()
def retrieve_memories(query: str, k: int = 5, project: str | None = None, type: str | None = None) -> str:
    """Search ForgeMem for principles and traces matching a query.

    WHEN TO USE: Call this BEFORE starting any task to check for prior
    learnings. Also call when you encounter an error to see if it's been
    solved before.

    EXAMPLES:
    - retrieve_memories("database connection pooling") → find caching/DB tips
    - retrieve_memories("CI failures", project="api") → project-scoped search
    - retrieve_memories("Next.js hydration", type="failure") → past failures only

    TIPS:
    - Use specific technical terms, not vague queries
    - Combine with project filter for focused results
    - Results ranked by impact_score (higher = more important lesson)
    """
```

**Why it matters:** Anthropic's engineering blog ([Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents)) states explicitly: **"Tool descriptions are prompts. Every word in your tool's name, description, and parameter documentation shapes how agents understand and use it."** They achieved state-of-the-art SWE-bench performance not by changing the model, but by refining tool descriptions. This is the single highest-ROI change for agent UX.

**Official Anthropic anti-patterns to avoid:**
- **Fragmenting operations into too many tools.** Instead of separate `search_traces`, `search_principles`, `filter_by_project`, `filter_by_type` — keep the consolidated `retrieve_memories` with filter parameters. (ForgeMem already does this correctly.)
- **Thin API wrappers.** Don't just wrap every DB query. Focus on high-impact workflows that match how agents think about tasks.
- **Returning opaque IDs.** Return human-readable context (principle text, project name) — not just row IDs that force another tool call. (ForgeMem does this correctly.)
- **Bloated responses.** Keep tool responses under 25,000 tokens. Add a `response_format` parameter with "concise" vs "detailed" options.

**Source:** [Anthropic: Building effective agents](https://www.anthropic.com/research/building-effective-agents) | [Claude API: Implement tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use)

#### 3. Add a `forgemem doctor` Command (Self-Diagnosis)

**Current state:** When things break, users get cryptic errors. No way to check if the setup is healthy.

**What modern CLIs do:** `supabase doctor`, `brew doctor`, `flutter doctor`, `npx next info` — they all have diagnostic commands that check the environment and report issues.

**What to build:**
```bash
forgemem doctor

ForgeMem Health Check
=====================
[ok] Python 3.12.1
[ok] SQLite DB exists (~/.forgemem/forgemem_memory.db, 2.4 MB)
[ok] FTS5 indexes intact (traces_fts: 156 rows, principles_fts: 42 rows)
[ok] Provider: anthropic (API key set)
[ok] MCP server registered in ~/.claude/settings.json
[!!] LaunchAgent not loaded (run: forgemem start)
[!!] 12 undistilled traces (run: forgemem distill)
[ok] Last sync: 2 hours ago
[--] Cloud mining: not configured (requires forgemem provider)
[ok] Disk space: 45 GB free
```

**Why it matters:**
- Reduces support burden (users self-diagnose)
- Catches misconfigurations before they cause silent failures
- Shows the "upgrade path" (e.g., "cloud mining: not configured" nudges toward forgemem provider)

#### 4. Completions and Shell Integration

**Current state:** No shell completions. Users must memorize commands and flags.

**What modern CLIs do:** `gh`, `uv`, `kubectl` all ship shell completions. Typer has built-in support via `typer.main.get_command()`.

**What to add:**
```bash
# One command to install completions
forgemem completions install

# Or manual:
forgemem completions bash >> ~/.bashrc
forgemem completions zsh >> ~/.zshrc
forgemem completions fish >> ~/.config/fish/completions/forgemem.fish
```

Typer already supports this via `typer.main.get_command()` — it's nearly free to add.

#### 5. Add `forgemem context` — Pre-Task Memory Dump for Agents

**Current state:** Agents must know to call `retrieve_memories` with the right query. If they don't, they start from zero.

**What to build:** A single MCP tool that returns a curated context package — the "top N things you should know" about this project.

```python
@mcp.tool()
def forgemem_context(project: str | None = None) -> str:
    """Get a curated summary of the most important principles for the current project.

    WHEN TO USE: Call this ONCE at the start of every coding session.
    Returns the top 10 highest-impact principles for the project,
    plus any recent failures to watch out for.

    Unlike retrieve_memories (which requires a specific query), this gives
    you a broad overview without knowing what to search for.
    """
```

**Why it matters:**
- Agents don't always know what to search for at session start
- A broad "what should I know?" tool is more natural than targeted search
- Claude Code's CLAUDE.md serves a similar purpose — this is the dynamic version
- Could be auto-injected via a hook or skill file instruction

#### 6. Separate Required from Optional Dependencies

**Current state:** `anthropic>=0.28.0` is a hard dependency in `pyproject.toml` even though users may pick OpenAI, Gemini, or Ollama. Every install pulls in the Anthropic SDK.

**What modern CLIs do:** Use optional dependency groups (extras):
```toml
[project]
dependencies = [
    "typer>=0.12.0",
    "rich>=13.0.0",
    "questionary>=2.0.0",
    "requests>=2.31.0",
    "fastmcp>=2.0.0",
]

[project.optional-dependencies]
anthropic = ["anthropic>=0.28.0"]
openai = ["openai>=1.0.0"]
gemini = ["google-generativeai>=0.5.0"]
all = ["anthropic>=0.28.0", "openai>=1.0.0", "google-generativeai>=0.5.0"]
```

```bash
pip install forgemem              # Minimal: just CLI + MCP + Ollama support
pip install forgemem[anthropic]   # With Anthropic SDK
pip install forgemem[all]         # Everything
```

**Why it matters:**
- Faster installs (especially for Ollama-only users)
- Smaller dependency footprint (fewer security vulnerabilities)
- Follows `uv` and modern Python packaging conventions

#### 7. Migrate from Dual CLI to Pure Typer

**Current state:** Two CLI systems coexist — Typer in `cli.py` wraps argparse in `core.py`. `cli.py` creates `argparse.Namespace` objects to call `core.py` functions:
```python
ns = argparse.Namespace(type=trace_type, content=content, ...)
core.cmd_save(ns)
```

**What to change:** Refactor `core.py` to export plain functions that take keyword arguments, not argparse Namespaces. Then `cli.py` calls them directly:
```python
# Before:
ns = argparse.Namespace(type=trace_type, content=content, project=project)
core.cmd_save(ns)

# After:
core.save_trace(type=trace_type, content=content, project=project)
```

**Why it matters:**
- Removes the argparse dependency and indirection
- Makes `core.py` functions reusable as a Python library (not just CLI)
- The MCP server can call the same functions directly
- Cleaner testing (pass kwargs, not Namespace objects)

#### 8. Add Homebrew Tap for macOS Distribution

**Current state:** `pip install forgemem` only. Requires Python knowledge and pip/pipx.

**What modern CLIs do:** Ship via Homebrew (macOS), apt (Debian), and standalone binaries.

**What to add:**
```bash
# Homebrew tap (easiest to set up):
brew tap intelogroup/forgemem
brew install forgemem

# Or via pipx (already works, just needs documentation):
pipx install forgemem

# Or via uv tool:
uv tool install forgemem
```

A Homebrew tap is a GitHub repo (`homebrew-forgemem`) with a Formula file. Takes ~30 minutes to set up and dramatically lowers the install barrier for macOS users.

**Why it matters:**
- `pip install` feels like a Python library, not a tool
- Homebrew/pipx/uv tool installs feel like installing a real CLI tool
- `brew install forgemem && forgemem init` is a better first impression than `pip install forgemem`

#### 9. Add `NO_COLOR`, `FORCE_COLOR`, and `--quiet` Support

**Current state:** Rich output always on. No way to disable colors or suppress output.

**What the standard says:** The [NO_COLOR](https://no-color.org/) convention and [clig.dev](https://clig.dev/) guidelines require:
- Respect `NO_COLOR` env var (disable all ANSI codes)
- Respect `FORCE_COLOR` env var (force colors even in pipes)
- Provide `--quiet` / `-q` flag (suppress non-essential output)
- Detect non-TTY (pipe) and auto-disable colors/spinners

**What to add:**
```python
# In cli.py:
import os

def _make_console() -> Console:
    no_color = os.environ.get("NO_COLOR") is not None
    force_color = os.environ.get("FORCE_COLOR") is not None
    return Console(
        no_color=no_color,
        force_style=force_color,
    )
```

**Why it matters:**
- CI/CD environments set `NO_COLOR` — Rich markup in log files is unreadable
- Agents may capture stdout — ANSI escape codes corrupt their context
- `--quiet` is essential for scripting (`forgemem save ... --quiet && echo "saved"`)

#### 10. Add Webhooks for Agent Orchestration

**Current state:** The local Flask API (`forgemem/api.py`) has webhook support, but the MCP server doesn't emit events and there's no way for an agent to say "tell me when a new principle is saved."

**What to build:** Event hooks that let agents react to ForgeMem activity:

```python
# In mcp_server.py, after saving a trace:
@mcp.tool()
def save_trace(...) -> str:
    # ... save logic ...
    # Emit event for any listening agents/webhooks
    _emit_event("trace_saved", {"trace_id": trace_id, "project": project, "type": trace_type})
```

More practically, add a `forgemem watch` command:
```bash
# Stream new principles as they're mined (useful for CI/agents):
forgemem watch --json
{"event": "principle_saved", "id": 43, "principle": "...", "impact_score": 8, "ts": "..."}
{"event": "principle_saved", "id": 44, "principle": "...", "impact_score": 6, "ts": "..."}
```

**Why it matters:**
- Enables multi-agent workflows (agent A mines, agent B acts on new principles)
- CI/CD integration (trigger actions on high-impact learnings)
- Real-time dashboards in the webapp

---

### Agent-Friendly HTTP API Improvements

The HTTP API (`forgemem/api.py`, `server/main.py`) is already functional but needs these changes to be truly agent-consumable. Based on [The New Stack: Prepare Your API for AI Agents](https://thenewstack.io/how-to-prepare-your-api-for-ai-agents/) and Google's [Developer's Guide to AI Agent Protocols](https://developers.googleblog.com/developers-guide-to-ai-agent-protocols/):

1. **Consistent error responses.** Every error should return `{"error": "...", "hint": "...", "code": "..."}`. The `hint` field tells agents how to self-correct (e.g., `"hint": "Parameter 'q' is required. Pass a search query."`)
2. **Pagination on all list endpoints.** `/search`, `/v1/sync/pull` must support `limit` + `cursor` params. Agents retry and paginate — unbounded responses break workflows.
3. **Idempotent writes.** Agents retry. `POST /traces` should accept an optional `idempotency_key` to prevent duplicate saves on retry.
4. **Introspection endpoint.** Add `GET /capabilities` returning available endpoints and their parameters. Agents can discover what the API offers without hardcoded knowledge.
5. **Rate limit headers.** Return `X-RateLimit-Remaining` and `Retry-After` on every response so agents can self-throttle.

---

### QUICK WINS (Ship This Week)

| Change | Effort | Impact |
|--------|--------|--------|
| `--json` flag on `search` and `stats` | 2 hours | High — unlocks scripting + agent subprocess use |
| Richer MCP tool descriptions with examples | 1 hour | High — immediate agent UX improvement |
| `NO_COLOR` + `--quiet` support | 1 hour | Medium — CI/CD and pipe compatibility |
| Shell completions via Typer | 30 min | Medium — developer UX polish |
| `forgemem doctor` | 3 hours | Medium — reduces support burden |

### MEDIUM TERM (Ship This Month)

| Change | Effort | Impact |
|--------|--------|--------|
| `forgemem context` MCP tool | 4 hours | High — best agent UX improvement |
| Pydantic return types on MCP tools | 4 hours | High — structured output for all MCP clients |
| OS keychain for secrets via `keyring` | 3 hours | High — fixes plaintext API key vulnerability |
| Optional dependency groups | 2 hours | Medium — cleaner installs |
| Homebrew tap + `uv tool install` docs | 2 hours | Medium — lowers install barrier |
| Migrate core.py from argparse to kwargs | 1 day | Medium — enables library use |
| `forgemem watch --json` | 4 hours | Medium — enables multi-agent workflows |
| Richer exit codes (0-6 by error type) | 1 hour | Medium — better scripting + CI integration |
| `--dry-run` flag for `distill` and `mine` | 2 hours | Low — safety for agents and humans |

### ADDITIONAL RECOMMENDATIONS FROM RESEARCH

**Pydantic return types on MCP tools.** FastMCP 2.10+ auto-generates structured content when tools return Pydantic models. ForgeMem currently returns raw strings. Migrate to typed returns:
```python
from pydantic import BaseModel

class MemoryResult(BaseModel):
    principles: list[dict]
    traces: list[dict]
    query: str
    total_results: int

@mcp.tool()
def retrieve_memories(...) -> MemoryResult:
    return MemoryResult(principles=..., traces=..., query=query, total_results=len(results))
```
This gives MCP clients machine-readable structured content alongside human-readable text. **Source:** [FastMCP Tools Docs](https://gofastmcp.com/servers/tools)

**OS keychain for secret storage.** API keys in `~/.forgemem/config.json` are plaintext. Use Python's `keyring` library (v25.7+) for cross-platform OS keychain integration:
```python
import keyring
keyring.set_password("forgemem", "anthropic_api_key", "sk-ant-...")
key = keyring.get_password("forgemem", "anthropic_api_key")
```
Uses macOS Keychain, Windows Credential Locker, Linux Secret Service automatically. Fall back to config.json if keyring unavailable. Add `forgemem auth status` to show where credentials are stored (without revealing values). **Source:** [Python keyring](https://keyring.readthedocs.io/)

**Richer exit codes.** Currently ForgeMem uses only 0 and 1. Adopt specific codes so agents and scripts can handle errors programmatically:
- `0` = success, `1` = general error, `2` = invalid arguments, `3` = DB not found, `4` = provider not configured, `5` = network/sync error, `6` = auth required

**Structured error responses in `--json` mode.** Currently errors are Rich-formatted strings. In JSON mode, return:
```json
{"ok": false, "error": "provider_not_configured", "message": "Run forgemem config to set up a provider", "retryable": false}
```
The `retryable` field lets agents decide whether to retry or bail. **Source:** [Writing CLI Tools That AI Agents Actually Want to Use](https://dev.to/uenyioha/writing-cli-tools-that-ai-agents-actually-want-to-use-39no)

**Opt-in anonymous telemetry.** Track command invoked, success/failure, latency, memory count, provider type. Never track content, keys, or paths. Opt-out via `FORGEMEM_TELEMETRY=false` or `DO_NOT_TRACK=1`. Pattern from [Chroma](https://docs.trychroma.com/telemetry) and [PostHog Python SDK](https://posthog.com/docs/libraries/python). ForgeMem already has `device_id` in config — use it as the anonymous identifier.

---

## Technical Deep Dive: Adding Embedding / Vector Search

### Why This Matters

ForgeMem's current search is FTS5 (keyword matching / BM25). It works well for exact terms but fails on semantic queries:

```text
# FTS5 finds this:
forgemem search "connection pooling"  →  ✓ matches "connection pooling" in text

# FTS5 misses this:
forgemem search "database too many open handles"  →  ✗ no match
  (but the "connection pooling" principle IS the answer)
```

Vector/embedding search fixes this — it matches by meaning, not keywords. This is the #1 retrieval quality improvement ForgeMem can make.

### The Architecture: Hybrid Search (FTS5 + Vector)

Don't replace FTS5. **Add vector search alongside it** and merge results. This is called hybrid search and consistently outperforms either approach alone.

```text
Agent query: "database too many open handles"
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
   FTS5 (BM25)           Vector (cosine)
   keyword match          semantic match
        │                       │
   Results:               Results:
   (empty or weak)        "Use connection pooling
                           to manage DB handles"
        │                       │
        └───────────┬───────────┘
                    ▼
           Reciprocal Rank Fusion (RRF)
           merge + re-rank
                    │
                    ▼
           Final results (best of both)
```

**Reciprocal Rank Fusion (RRF)** is the standard merging algorithm. Alex Garcia published a [pure-SQL implementation](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html) that runs the entire hybrid search in one query:

```sql
-- Pure SQL hybrid search: FTS5 + sqlite-vec + RRF in one query
WITH vec_matches AS (
  SELECT rowid, row_number() OVER () as rank_number
  FROM principles_vec
  WHERE embedding MATCH :query_embedding
  ORDER BY distance
  LIMIT :k
),
fts_matches AS (
  SELECT rowid, row_number() OVER (ORDER BY rank) as rank_number
  FROM principles_fts
  WHERE principles_fts MATCH :query_text
  LIMIT :k
),
final AS (
  SELECT
    coalesce(fts.rowid, vec.rowid) as rowid,
    coalesce(1.0 / (60 + fts.rank_number), 0.0) +
    coalesce(1.0 / (60 + vec.rank_number), 0.0)
    AS combined_score
  FROM fts_matches fts
  FULL OUTER JOIN vec_matches vec ON fts.rowid = vec.rowid
  ORDER BY combined_score DESC
)
SELECT final.rowid, final.combined_score, p.*
FROM final
JOIN principles p ON p.id = final.rowid
LIMIT :limit;
```

**Real-world performance:** [ZeroClaw benchmarked this on a Raspberry Pi Zero 2 W](https://zeroclaws.io/blog/zeroclaw-hybrid-memory-sqlite-vector-fts5/) — under 3ms total (0.3ms FTS5, 2ms vector, 0.1ms merge). On a modern laptop it'll be sub-millisecond.

**Source:** [sqlite-vec hybrid search blog](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html) | [Simon Willison on hybrid search](https://simonwillison.net/2024/Oct/4/hybrid-full-text-search-and-vector-search-with-sqlite/) | [liamca/sqlite-hybrid-search](https://github.com/liamca/sqlite-hybrid-search)

### Local vs Cloud: Same Split as Inference

The embedding strategy follows the same business model as inference:

```text
┌──────────────────────────────────────────────────────────────────┐
│  forgemem init → "Choose your provider"                          │
│                                                                  │
│  ┌─ "ollama"      → local embeddings via Ollama                  │
│  │                  (nomic-embed-text or all-minilm)             │
│  │                  FREE, runs on device, ~100ms per embedding   │
│  │                                                               │
│  ├─ "anthropic"   → Voyage AI embeddings (Anthropic's partner)   │
│  ├─ "openai"      → text-embedding-3-small ($0.02/1M tokens)    │
│  ├─ "gemini"      → Gemini embedding-001 (free tier available)   │
│  │                  BYOK, user pays their own provider           │
│  │                                                               │
│  └─ "forgemem"    → cloud embeddings via Oracle Cloud            │
│                     (self-hosted model, same as inference)        │
│                     PAID, included in forgemem credits            │
└──────────────────────────────────────────────────────────────────┘
```

### Implementation Plan: 4 Phases

#### Phase 1: sqlite-vec Extension (The Foundation)

**sqlite-vec** (by Alex Garcia) is the right choice for local vector storage. It's a SQLite extension that adds vector columns and KNN search — no external database needed. ForgeMem stays single-file SQLite.

**Schema migration:**
```sql
-- New: vector storage for principles and traces
CREATE VIRTUAL TABLE IF NOT EXISTS principles_vec USING vec0(
    principle_id INTEGER PRIMARY KEY,
    embedding    float[384]        -- 384 dims for all-MiniLM / nomic-embed-text
);

CREATE VIRTUAL TABLE IF NOT EXISTS traces_vec USING vec0(
    trace_id     INTEGER PRIMARY KEY,
    embedding    float[384]
);
```

**Installation:** `pip install sqlite-vec` — pure Python, no compilation. Works on macOS, Linux, Windows.

**Query:**
```sql
-- KNN search: find 5 nearest principles to a query vector
SELECT
    pv.principle_id,
    pv.distance,
    p.principle,
    p.impact_score,
    p.project_tag
FROM principles_vec pv
JOIN principles p ON p.id = pv.principle_id
WHERE pv.embedding MATCH ?       -- query vector (as JSON array or bytes)
  AND k = ?                      -- number of results
ORDER BY pv.distance
```

**Why sqlite-vec over alternatives:**
- **sqlite-vss**: Deprecated by the same author in favor of sqlite-vec
- **ChromaDB / Qdrant / Weaviate**: External services, violates local-first. Overkill for <100K vectors
- **FAISS / hnswlib**: Separate index files, harder to keep in sync with SQLite
- **sqlite-vec**: Single DB file, transactional, backs up with SQLite backup API, works with WAL mode

#### Phase 2: Local Embedding Generation

**For Ollama users (free tier):**

Ollama already supports embedding models natively:
```bash
ollama pull nomic-embed-text    # 274MB, 768 dims, best quality/size
ollama pull all-minilm           # 46MB, 384 dims, smallest
```

```python
import requests

def embed_local(text: str, model: str = "nomic-embed-text") -> list[float]:
    resp = requests.post("http://localhost:11434/api/embed", json={
        "model": model,
        "input": text
    })
    return resp.json()["embeddings"][0]
```

**For BYOK users:**
```python
# OpenAI
from openai import OpenAI
client = OpenAI()
resp = client.embeddings.create(model="text-embedding-3-small", input=text)
embedding = resp.data[0].embedding  # 1536 dims

# Anthropic doesn't have embeddings — use Voyage AI (their partner)
# voyage-3-lite: $0.02/1M tokens, 512 dims
```

**For forgemem provider (paid):**
Self-hosted on Oracle Cloud — same vLLM/TGI instance can serve both inference and embeddings. Or deploy a lightweight embedding model via `sentence-transformers` behind FastAPI.

**Dimension normalization:** Different providers return different dimensions (384, 512, 768, 1536). Pick one canonical dimension and either:
- Standardize on 384 (smallest, fastest, good enough for <100K docs)
- Or use Matryoshka embeddings (OpenAI's text-embedding-3-small supports truncating to any dimension)

**Best local embedding models (March 2026 benchmarks):**

| Model | Params | Dims | Context | MTEB Score | RAM | License |
|-------|--------|------|---------|------------|-----|---------|
| **nomic-embed-text v1.5** | 137M | 64-768 (Matryoshka) | 8,192 tok | ~62 | ~500MB | Apache-2.0 |
| **EmbeddingGemma** (Google) | 308M | 128-768 (Matryoshka) | — | Top <500M | <200MB | Open |
| **Qwen3-Embedding-0.6B** | 600M | 32-1024 (Matryoshka) | — | Very high | ~1GB | Apache-2.0 |
| **all-MiniLM-L6-v2** | 22M | 384 (fixed) | 512 tok | 56.3 | ~90MB | Apache-2.0 |

**Matryoshka embeddings** (nomic-embed, EmbeddingGemma, Qwen3) let you truncate to any dimension (384, 256, 128) with graceful quality degradation. This means you can start at 384 dims for speed and upgrade to 768 later without re-embedding.

**Recommendation:** Use **nomic-embed-text v1.5** as default (best quality/size, 8K context, Matryoshka, Apache-2.0). Store at **384 dimensions** (truncated from 768) to keep vector tables small. Upgrade to 768 later if retrieval quality needs it.

**Source:** [Best open-source embedding models 2026](https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models) | [Nomic Embed Matryoshka](https://www.nomic.ai/news/nomic-embed-matryoshka) | [Ollama embedding models](https://ollama.com/blog/embedding-models)

#### Phase 3: Embed on Write, Search on Read

**When a trace or principle is saved** (`core.cmd_save()`, `mcp_server.save_trace()`):
1. Save to `traces` / `principles` table (existing flow)
2. Generate embedding for the content/principle text
3. Insert into `traces_vec` / `principles_vec`

```python
def save_trace_with_embedding(conn, trace_id, content, type, project):
    # Existing: insert into traces + traces_fts
    conn.execute("INSERT INTO traces ...", ...)
    conn.execute("INSERT INTO traces_fts ...", ...)

    # New: generate embedding + insert into traces_vec
    embedding = embed(content)  # routes to configured provider
    conn.execute(
        "INSERT INTO traces_vec (trace_id, embedding) VALUES (?, ?)",
        (trace_id, serialize_vec(embedding))
    )
```

**When searching** (`core.cmd_retrieve()`, `mcp_server.retrieve_memories()`):
1. Run FTS5 query (existing)
2. Generate embedding for the query text
3. Run KNN on `principles_vec` / `traces_vec`
4. Merge results via Reciprocal Rank Fusion
5. Return merged results

```python
def retrieve_hybrid(query: str, k: int = 5, project: str = None):
    query_vec = embed(query)

    # FTS5 results (keyword)
    fts_results = fts5_search(query, k=k*2, project=project)

    # Vector results (semantic)
    vec_results = vector_search(query_vec, k=k*2, project=project)

    # Merge via RRF
    merged = reciprocal_rank_fusion(fts_results, vec_results)

    return merged[:k]
```

**Fallback behavior:** If embedding generation fails (Ollama not running, API key missing, network down), fall back to FTS5-only. The user never sees an error — they just get keyword results instead of hybrid results. Log a warning to `~/.forgemem/debug.log`.

#### Phase 4: Backfill Existing Data

Existing traces and principles don't have embeddings. Add a migration command:

```bash
# One-time backfill of all existing data
forgemem embed --backfill

# Status check
forgemem embed --status
Embeddings: 142/156 traces, 38/42 principles (90.4%)
Provider: ollama (nomic-embed-text)
Vector table size: 1.2 MB
```

**Backfill strategy:**
- Batch process in chunks of 50 (respect Ollama/API rate limits)
- Show progress bar via Rich
- Idempotent — skip rows that already have embeddings
- Run automatically after `forgemem init` if embedding provider is configured

### Cost Analysis

| Provider | Model | Dims | Cost per 1M tokens | Notes |
|----------|-------|------|-------------------|-------|
| **Ollama (local)** | nomic-embed-text v1.5 | 384-768 | Free | Best local option, 274MB |
| **Ollama (local)** | all-minilm | 384 | Free | Smallest, 46MB |
| **Mistral** | Mistral Embed | — | $0.01 | Cheapest commercial |
| **OpenAI** | text-embedding-3-small | 1536 | $0.02 ($0.01 batch) | Best value cloud |
| **Voyage AI** | voyage-3.5-lite | — | $0.02 | 200M tokens free tier |
| **Gemini** | gemini-embedding-001 | 3072 | $0.15 ($0.075 batch) | Free tier available |
| **ForgeMem cloud** | self-hosted nomic | 384 | Included in credits | Fixed infra cost |

At ForgeMem's scale (most users have <1K principles, <10K traces), embedding costs are negligible — under $0.01 for a full backfill via API. Voyage AI's 200M free-tier tokens covers ~400K principles at zero cost.

**Source:** [OpenAI pricing](https://platform.openai.com/docs/pricing) | [Voyage AI pricing](https://docs.voyageai.com/docs/pricing) | [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)

### Cloud Vector Search (for Sync Users)

For users who opt into cloud sync + forgemem provider:

```text
┌───────────────────────────────────────────────────────┐
│ LOCAL (user's machine)                                 │
│   SQLite + sqlite-vec                                  │
│   Local embeddings (Ollama) or cloud embeddings        │
│   Hybrid search (FTS5 + vector)                        │
│                                                        │
│ ──── forgemem sync ──────────────────────────────────  │
│                                                        │
│ CLOUD (Oracle MySQL HeatWave)                          │
│   MySQL VECTOR data type + DISTANCE() function         │
│   Automatic HNSW index for ANN search                  │
│   OR: HeatWave Vector Index for large-scale            │
│   Server-side hybrid search for /v1/search endpoint    │
│   Embeddings via ML_EMBED_ROW() or sentence-transformers│
└───────────────────────────────────────────────────────┘
```

**Cloud options for MySQL vector search:**
- **Oracle MySQL HeatWave (native)** — `VECTOR` data type, `DISTANCE()` function for cosine/L2 similarity, automatic HNSW index creation (MySQL 9.5+), `ML_EMBED_ROW()` / `ML_EMBED_TABLE()` for server-side embedding generation, HeatWave Vector Index for large-scale ANN search. This is the recommended path — no external extensions needed. ([MySQL HeatWave Vector Store docs](https://dev.mysql.com/doc/heatwave/en/mys-hw-genai-vector-search.html))
- **Fallback: serialize hnswlib index** — if HeatWave features are unavailable on your OCI tier, generate embeddings server-side, store in a BLOB column, load hnswlib index into memory for search

**Recommendation:** Start with local-only sqlite-vec. Add cloud vector search only when the sync user base justifies it. The local experience should be excellent first.

### Migration Path (Backwards Compatible)

```text
Phase   What ships                    User impact
─────   ──────────────────────────    ─────────────────────────────
  1     sqlite-vec tables added       Zero — no behavior change
        to schema migration           FTS5 still primary search

  2     Embedding generation added    Users who configure embeddings
        to save flow                  get vector search on new data

  3     Hybrid search in retrieval    retrieve_memories returns
                                      better results automatically

  4     Backfill command              Users can embed existing data
        forgemem embed --backfill     at their own pace
```

No breaking changes at any phase. Users who never configure embeddings keep using FTS5 exactly as today. The vector tables exist but stay empty. This is critical for the Ollama free tier — don't force embedding model downloads on users who didn't ask for it.

### Config Changes

```json
// ~/.forgemem/config.json — new fields
{
    "provider": "ollama",
    "embedding": {
        "enabled": true,
        "provider": "ollama",            // or "openai", "gemini", "forgemem"
        "model": "nomic-embed-text",     // provider-specific model name
        "dimensions": 384                // target dimensions
    }
}
```

Set during `forgemem init` with a new question:
```text
Enable semantic search? (recommended, requires embedding model)
> Yes — use Ollama (nomic-embed-text, free, 274MB download)
  Yes — use my API provider (uses your configured API key)
  No  — keep keyword search only
```

### Summary

| Decision | Choice | Why |
|----------|--------|-----|
| Vector DB | sqlite-vec | Local-first, single file, backs up with SQLite |
| Default embedding model | nomic-embed-text v1.5 (384d Matryoshka) | Best quality/size, 8K context, upgradeable to 768d |
| Search strategy | Hybrid (FTS5 + vector + RRF) | Best of both, proven in research |
| Embed timing | On write (not on read) | One-time cost, search stays fast |
| Cloud vector | Oracle MySQL HeatWave or hnswlib | Only for sync users, later phase |
| Backwards compat | Fully backwards compatible | Empty vector tables don't break anything |
| Business model | Local embeddings free, cloud included in credits | Same split as inference |

---

## UX Edge Cases, Billing Flows, and Provider Switching

### Current State Audit

We audited every user interaction path. Here are the critical gaps, a confirmed bug, and recommended fixes.

### BUG: Billing Pack ID Mismatch (Checkout Broken)

**Severity: CRITICAL — all credit purchases from webapp fail.**

The webapp billing page (`webapp/app/billing/page.tsx`) sends `pack_5`, `pack_20`, `pack_50` as `pack_id` to `POST /v1/checkout`. But the server (`server/main.py`) expects `starter`, `pro`, `team`. Every checkout attempt returns 400.

**Fix:** Align the pack IDs. Either change the frontend or the backend — one commit.

### Problem: Mining Silently Dies on Credit Exhaustion

**Severity: HIGH — users don't know mining stopped.**

When `daily_scan.py` runs and credits run out on repo #3 of 5:
1. `inference.call()` hits 402, calls `sys.exit(1)`
2. Scanner catches `SystemExit`, returns `[]`
3. Logs: "No meaningful learnings extracted" — **identical to repos with no commits**
4. Continues to repos #4, #5 — both also fail silently with 402
5. User sees no alert that the entire run failed due to credits

**The user thinks mining is working. It's not.**

**Recommended fix:**
```python
# In daily_scan.py — detect credit failure vs normal empty
class CreditsExhaustedError(Exception):
    pass

# In inference.py — raise specific exception instead of sys.exit(1)
if resp.status_code == 402:
    raise CreditsExhaustedError(f"Balance: ${balance}")

# In daily_scan.py — catch it and STOP the entire run
try:
    learnings = extract_learnings(repo, log)
except CreditsExhaustedError as e:
    log(f"CREDITS EXHAUSTED: {e} — stopping mining run")
    log(f"Add credits: https://app.forgemem.com/billing")
    log(f"Or switch provider: forgemem config provider anthropic --key ...")
    # macOS notification
    _notify("ForgeMem: Credits Exhausted", "Mining stopped. Add credits to resume.")
    break  # Stop entire run, don't waste time on remaining repos
```

### Problem: No Retry on Transient Failures

**Severity: MEDIUM — network blips cause silent data loss.**

If the API returns a transient error (network timeout, 500, 503) during mining, the scanner silently skips that repo/file. No retry. Next scheduled run (1 hour later) will only mine the last 24h of commits — so the failed repo's commits may still be in window, but there's no guarantee.

**Recommended fix:** Add simple retry with backoff for transient errors only:
```python
TRANSIENT_CODES = {429, 500, 502, 503}
MAX_RETRIES = 2

def call_with_retry(prompt, max_tokens, model):
    for attempt in range(MAX_RETRIES + 1):
        try:
            return inference.call(prompt, max_tokens, model)
        except TransientError:
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)  # 1s, 2s
                continue
            raise
```

Don't retry on 401, 402, 404 — those are permanent failures.

---

### User Journey Fixes: The 10 Scenarios

#### 1. Reinstall ForgeMem (`pip install forgemem` again)

**Current:** Config (`~/.forgemem/config.json`) and DB (`~/.forgemem/forgemem_memory.db`) survive reinstall. Auto-runs `init(yes=True)` if DB missing. Works.

**Gap:** If user reinstalls AND deletes `~/.forgemem/` (clean slate), running `forgemem init` starts fresh. If they previously had a forgemem account, they need to `forgemem auth login` again — but there's no prompt telling them this.

**Fix:** During `forgemem init`, after provider selection, if user picks "forgemem":
```text
Looks like you've selected the forgemem provider.
Do you have an existing account?
> Yes — log me in (opens browser)
  No — create new account (opens browser)
```
Both paths go through the same OAuth/magic link flow, but the UX acknowledges returning users.

#### 2. User Already Exists — Should Not Re-Signup

**Current:** `forgemem auth login` always opens the magic link flow. If the email already exists in the server DB, it logs in (no duplicate account). If new email, creates account with free credits.

**This actually works correctly** — magic link is idempotent by design. Same email = same account. But the UX doesn't communicate this.

**Fix:** After successful auth, show:
```text
Welcome back, user@email.com!
Balance: $4.82  ·  Traces synced: 156
```
vs for new users:
```text
Account created! Welcome to ForgeMem.
Your $5 free credits are ready.
```

#### 3. Alert Low Credits Before They Hit Zero

**Current:** No warning until credits hit zero (402). Then mining stops silently.

**Fix:** Add a low-balance warning threshold. Check balance after each inference call:
```python
# In inference.py _call_forgemem_managed()
balance = resp.json().get("balance_usd", 0)
if balance < 1.0 and balance > 0:
    # Don't exit — just warn
    console.print(f"[yellow]Low credits:[/] ${balance:.2f} remaining. "
                  f"Add credits: https://app.forgemem.com/billing")
```

Also add to `forgemem status`:
```text
Credits: $0.82 ⚠️ LOW — estimated 41 mining runs remaining
         Add credits → https://app.forgemem.com/billing
```

And on the server side: add a `low_balance` flag to inference responses when balance < $1.00:
```json
{"text": "...", "cost_usd": 0.02, "balance_usd": 0.82, "low_balance": true}
```

#### 4. Retry on Mining Failure

See "Problem: No Retry on Transient Failures" above. Add 2-attempt retry with backoff for transient HTTP errors only.

#### 5. Server Not Running (for HTTP API users)

**Current:** Good error messages for Ollama ("make sure Ollama is running") and forgemem API ("check your connection"). MCP server is stdio-based so it's always spawned on demand.

**Gap:** The Flask HTTP API daemon (`forgemem/daemon.py` on port 5555) has no health check or auto-restart.

**Fix:** Add `forgemem doctor` (already in recommendations above) that checks:
- Is the MCP server registered?
- Is the HTTP daemon running? (`curl localhost:5555/health`)
- Is Ollama reachable? (if provider=ollama)
- Is forgemem API reachable? (if provider=forgemem)

#### 6. Seamless Provider Switching

**Current:** `forgemem config provider X --key Y` works instantly. But:
- No warning when switching FROM forgemem (you lose cloud mining + sync)
- No warning when switching TO forgemem (you need to auth first)
- No migration of embedding config

**Recommended UX:**

```bash
# Switching TO forgemem
forgemem config provider forgemem
> Switching to forgemem managed inference.
> This enables: cloud mining, cross-device sync, lid-closed mining
> You'll need to authenticate. Opening browser...
> [browser opens for OAuth]

# Switching FROM forgemem to BYOK
forgemem config provider anthropic --key sk-ant-...
> Switching to anthropic (BYOK).
> Note: Cloud mining and cross-device sync will be disabled.
> Your local SQLite database is unchanged — all memories stay on this machine.
> Continue? [Y/n]

# Switching FROM forgemem to ollama
forgemem config provider ollama
> Switching to ollama (local inference).
> Note: Cloud mining and cross-device sync will be disabled.
> Make sure Ollama is running: ollama serve
> Continue? [Y/n]
```

#### 7. Adding Credits from CLI (Like Cline/OpenRouter)

**Current:** CLI shows `https://app.forgemem.com/billing` as text. User must copy-paste into browser.

**What Cline/OpenRouter do:** Open the billing page directly from the CLI with a pre-authenticated URL, show real-time balance updates.

**Recommended approach:**

```bash
# Open billing page directly (with auth token for auto-login)
forgemem billing

> Opening billing page in your browser...
> Current balance: $0.82
> [browser opens https://app.forgemem.com/billing?token=<short-lived-jwt>]

# Quick top-up without leaving terminal
forgemem billing --add 20

> Creating checkout for $20 Pro pack...
> Opening Stripe checkout in browser...
> [browser opens Stripe checkout URL]
> Waiting for payment confirmation...
> ✓ Payment confirmed! New balance: $20.82
```

**How the "waiting for payment" works:**
1. CLI creates checkout via `POST /v1/checkout`
2. Opens Stripe URL in browser
3. Polls `GET /v1/balance` every 3 seconds (with 2-minute timeout)
4. When balance increases, shows confirmation and exits

**Add to server:** A `POST /v1/billing-link` endpoint that returns a short-lived pre-authenticated URL:
```python
@app.post("/v1/billing-link")
def billing_link(user_id: str):
    token = create_short_lived_jwt(user_id, expires_in=300)  # 5 min
    return {"url": f"{WEBAPP_ORIGIN}/billing?auth={token}"}
```

#### 8. Usage and Billing Tracking (Like OpenRouter Dashboard)

**Current:** Webapp shows last 20 runs in activity feed. No cost trends, no per-project breakdown, no monthly totals.

**What OpenRouter does:**
- Per-model cost breakdown
- Daily/weekly/monthly usage charts
- Per-API-key usage tracking
- Rate limit status
- Credit burn rate and estimated time to zero

**Recommended additions to webapp:**

```text
Dashboard:
┌─────────────────────────────────────────┐
│ Balance: $4.82         Burn rate: $0.48/day
│ Estimated empty: 10 days
│
│ This month: $8.40 across 420 runs
│ ├── Mining:      $6.20 (310 runs)
│ ├── Distilling:  $1.80 (90 runs)
│ └── Embeddings:  $0.40 (20 runs)
│
│ Top projects:
│ ├── api:      $3.20 (160 runs)
│ ├── frontend: $2.80 (140 runs)
│ └── infra:    $2.40 (120 runs)
└─────────────────────────────────────────┘
```

**Server changes needed:**
- Add `run_type` field to `usage_runs` table: `"mine"`, `"distill"`, `"embed"`, `"search"`
- Add `project_tag` to `usage_runs` for per-project breakdown
- Add `GET /v1/usage/summary` endpoint returning aggregated stats

**CLI additions:**
```bash
forgemem usage
> This month: $8.40 / 420 runs
> Balance: $4.82 (est. 10 days remaining)
> Top project: api ($3.20)
>
> Details: https://app.forgemem.com/billing

forgemem usage --json
{"month_total": 8.40, "runs": 420, "balance": 4.82, "burn_rate_day": 0.48, ...}
```

#### 9. Auto-Detect Embedding Cost During Mining

When embedding search is enabled AND using a paid provider, mining runs now cost more (inference + embedding per trace). Users should see this.

**Recommended:** Show estimated cost before mining starts:
```bash
forgemem mine

Scanning 5 repos for learnings...
Provider: forgemem  ·  Estimated cost: ~$0.10 (inference) + ~$0.01 (embeddings)
Balance: $4.82

[ok] api — 3 learnings extracted ($0.06)
[ok] frontend — 2 learnings extracted ($0.04)
[!!] infra — credits low ($0.72 remaining)
[ok] infra — 1 learning extracted ($0.02)
[--] docs — no new commits
[--] scripts — no new commits

Summary: 6 learnings saved, $0.12 spent, balance: $0.70
```

#### 10. Quick Recovery When Credits Are Restored

**Current:** After adding credits, the `.credits_exhausted` flag persists until the next successful inference call. But if the LaunchAgent daemon isn't scheduled to run soon, mining stays paused.

**Fix:**
- After `forgemem billing --add` confirms payment, auto-clear the credits flag
- Optionally trigger an immediate mining run: `forgemem mine --now`
- Or: on next `forgemem status`, if balance > 0 but flag exists, clear it and say "Credits restored — mining will resume on next scheduled run"

---

### Recommended Implementation Order

| Priority | Fix | Effort | Impact |
|----------|-----|--------|--------|
| **P0** | Fix billing pack ID mismatch (bug) | 30 min | Critical — checkout is broken |
| **P0** | Stop mining run on credit exhaustion (not silent skip) | 1 hour | High — users don't know mining stopped |
| **P0** | Low-balance warning at $1.00 threshold | 1 hour | High — prevents surprise failures |
| **P1** | `forgemem billing` command (open browser with pre-auth URL) | 2 hours | High — Cline/OpenRouter pattern |
| **P1** | `forgemem billing --add N` (Stripe checkout from CLI) | 3 hours | High — fastest path to revenue |
| **P1** | Provider switch warnings (lose sync, need auth) | 1 hour | Medium — prevents confusion |
| **P1** | Retry logic for transient errors (2 attempts) | 2 hours | Medium — prevents data loss |
| **P2** | Welcome back vs new user messaging after auth | 1 hour | Low — polish |
| **P2** | `forgemem usage` command with cost breakdown | 3 hours | Medium — OpenRouter pattern |
| **P2** | Usage dashboard in webapp (burn rate, per-project) | 1 day | Medium — retention feature |
| **P2** | Mining cost estimate before run | 2 hours | Low — transparency |
| **P3** | Auto-clear credits flag after payment confirmed | 1 hour | Low — recovery polish |
| **P3** | `forgemem init` returning user detection | 1 hour | Low — onboarding polish |
