-- Forgemem Sync Schema
-- Apply via Supabase dashboard → SQL Editor, or: supabase db push
-- Run once on your Supabase project after creating it.

-- ── Device registry ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS devices (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id     UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
    name        TEXT,
    created_at  TIMESTAMPTZ DEFAULT now() NOT NULL,
    last_sync   TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- ── Synced traces ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sync_traces (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id     UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
    device_id   UUID REFERENCES devices(id) ON DELETE SET NULL,
    local_id    INTEGER NOT NULL,
    ts          TIMESTAMPTZ,
    session_id  TEXT,
    project_tag TEXT,
    type        TEXT CHECK (type IN ('success', 'failure', 'plan', 'note')) NOT NULL,
    content     TEXT NOT NULL,
    distilled   BOOLEAN DEFAULT false NOT NULL,
    synced_at   TIMESTAMPTZ DEFAULT now() NOT NULL,
    UNIQUE (user_id, device_id, local_id)
);

-- ── Synced principles ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sync_principles (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id         UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
    device_id       UUID REFERENCES devices(id) ON DELETE SET NULL,
    local_id        INTEGER NOT NULL,
    source_local_id INTEGER,
    project_tag     TEXT,
    type            TEXT,
    principle       TEXT NOT NULL,
    impact_score    INTEGER DEFAULT 5 CHECK (impact_score BETWEEN 0 AND 10),
    tags            TEXT,
    synced_at       TIMESTAMPTZ DEFAULT now() NOT NULL,
    UNIQUE (user_id, device_id, local_id)
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_sync_traces_user_synced
    ON sync_traces (user_id, synced_at DESC);

CREATE INDEX IF NOT EXISTS idx_sync_principles_user_synced
    ON sync_principles (user_id, synced_at DESC);

-- ── Row-Level Security ────────────────────────────────────────────────────────

ALTER TABLE devices         ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_traces     ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_principles ENABLE ROW LEVEL SECURITY;

-- Users see only their own rows
CREATE POLICY "own_devices"
    ON devices FOR ALL USING (user_id = auth.uid());

CREATE POLICY "own_sync_traces"
    ON sync_traces FOR ALL USING (user_id = auth.uid());

CREATE POLICY "own_sync_principles"
    ON sync_principles FOR ALL USING (user_id = auth.uid());
