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
