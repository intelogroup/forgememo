-- Forgememo Server Schema
-- Apply via: supabase db push
-- Or: Supabase dashboard → SQL Editor → Run

-- ── Users (custom auth — not Supabase Auth) ──────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT UNIQUE NOT NULL,
    balance_usd DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    created_at  BIGINT NOT NULL,
    provider    TEXT NOT NULL DEFAULT 'forgemem',
    provider_id TEXT,
    name        TEXT,
    avatar_url  TEXT,
    username    TEXT
);

-- ── Magic link tokens ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS magic_link_tokens (
    token       TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    callback    TEXT NOT NULL,
    state       TEXT NOT NULL,
    created_at  BIGINT NOT NULL,
    expires_at  BIGINT NOT NULL,
    used        BOOLEAN NOT NULL DEFAULT false
);

-- ── Usage / billing ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS usage_runs (
    run_id      TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cost_usd    DOUBLE PRECISION NOT NULL,
    model       TEXT NOT NULL,
    balance_usd DOUBLE PRECISION NOT NULL,
    ts          BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS stripe_events (
    event_id     TEXT PRIMARY KEY,
    processed_at BIGINT NOT NULL
);

-- ── Sessions ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  BIGINT NOT NULL,
    expires_at  BIGINT NOT NULL
);

-- ── Device registry ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS devices (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL DEFAULT '',
    last_sync   BIGINT NOT NULL
);

-- ── Synced traces ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sync_traces (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id   TEXT REFERENCES devices(id) ON DELETE SET NULL,
    local_id    TEXT NOT NULL,
    ts          BIGINT,
    session_id  TEXT,
    project_tag TEXT,
    type        TEXT NOT NULL DEFAULT 'note',
    content     TEXT NOT NULL,
    distilled   BOOLEAN NOT NULL DEFAULT false,
    synced_at   BIGINT NOT NULL,
    UNIQUE (user_id, device_id, local_id)
);

-- ── Synced principles ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sync_principles (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id       TEXT REFERENCES devices(id) ON DELETE SET NULL,
    local_id        TEXT NOT NULL,
    source_local_id TEXT,
    project_tag     TEXT,
    type            TEXT,
    principle       TEXT NOT NULL,
    impact_score    INTEGER NOT NULL DEFAULT 5,
    tags            TEXT,
    synced_at       BIGINT NOT NULL,
    UNIQUE (user_id, device_id, local_id)
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_usage_runs_user_ts
    ON usage_runs (user_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_sync_traces_user_synced
    ON sync_traces (user_id, synced_at DESC);

CREATE INDEX IF NOT EXISTS idx_sync_principles_user_synced
    ON sync_principles (user_id, synced_at DESC);

CREATE INDEX IF NOT EXISTS idx_sessions_user
    ON sessions (user_id);
