# Forgememo — Deferred TODOs

## Semantic vector search (deferred from v2 plan)

Add hybrid FTS5 + vector search to `search_memories`.

Status: deferred (Phase 8) — implement only after v2 daemon + distilled tables are stable.

**Files:** `forgememo/inference.py`, `forgememo/storage.py`

**Approach:**
- Add `embed(text) -> list[float] | None` to `inference.py`
  - `openai` provider: `text-embedding-3-small` (1536-dim)
  - `voyage` provider: `voyage-3` via `api.voyageai.com`
  - Returns `None` if no embed provider configured — graceful FTS5 fallback
- Store embeddings in `distilled_summaries_vec` (sqlite-vec `vec0` virtual table)
- Generate after worker distillation
- Hybrid search: 50% FTS5 rank + 50% cosine similarity, re-rank top-k

**Dependency:**
```toml
[project.optional-dependencies]
semantic = ["sqlite-vec>=0.1.0"]
```

**Prerequisite:** v2 daemon + distilled_summaries table must be live first.
Migration 2 in `lifecycle/migrate.py` should create `distilled_summaries_vec`.

---

## Token auth mandatory enforcement path

**Status:** Deferred — opt-in grace period active since Phase 3.

**Path:**
1. Next minor release: daemon logs `WARNING` on startup if no `~/.forgememo/daemon.token` exists
2. Release after that: add `--danger-no-auth` flag to bypass; token auth mandatory by default

**Why:** Current opt-in design preserves legacy compatibility but is permanently opt-out
without this path. 127.0.0.1-only binding is the sole protection for unenforced installs.

**Files:** `forgememo/lifecycle/token_auth.py`, `forgememo/daemon.py`

---

## tools/fix_imports.py — reusable import rewriter

**Status:** Needed for Phase 1 CLI split; should be committed as a reusable dev tool.

**Purpose:** Bulk-rewrite Python import paths across test files when module structure changes.
Avoids manual typos across 300+ files during refactors.

**Approach:** ~20 lines using `ast.parse` or `sed`; accepts `--from` and `--to` args.

**Files:** `tools/fix_imports.py`

---

## Sync conflict resolution

**Status:** Deferred — single-device use is current target.

**Problem:** If the same memory is modified on two devices, pull silently overwrites.
No merge strategy exists.

**Approach:** Design a last-write-wins or 3-way merge strategy for concurrent edits.
Last-write-wins is simplest and correct for most agent memory use cases.

**Files:** `forgememo/commands/configure.py` (sync command)
