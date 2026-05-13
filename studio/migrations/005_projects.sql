-- Project (multi-tenant) layer.
-- Existing data stays valid: all rows default to project_id='default' which
-- is auto-created by the project module's bootstrap.

CREATE TABLE IF NOT EXISTS studio_projects (
    project_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    emoji           TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    is_default      INTEGER NOT NULL DEFAULT 0,
    archived        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_projects_archived ON studio_projects(archived);

-- Per-row ownership. NULL'd values mean "global" (legacy data) — code maps
-- them to the default project transparently.
ALTER TABLE studio_drafts ADD COLUMN project_id TEXT;
ALTER TABLE studio_strategies ADD COLUMN project_id TEXT;
ALTER TABLE studio_my_posts ADD COLUMN project_id TEXT;
ALTER TABLE studio_dna_artifacts ADD COLUMN project_id TEXT;

CREATE INDEX IF NOT EXISTS idx_drafts_project ON studio_drafts(project_id);
CREATE INDEX IF NOT EXISTS idx_strategies_project ON studio_strategies(project_id);
CREATE INDEX IF NOT EXISTS idx_my_posts_project ON studio_my_posts(project_id);

-- ------ Library analysis reports (Claude × OpenAI debate output) ------

CREATE TABLE IF NOT EXISTS studio_insight_reports (
    report_id        TEXT PRIMARY KEY,
    project_id       TEXT,
    library_id       TEXT NOT NULL,
    created_at       INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
                                     -- 'pending'|'completed'|'failed'
    claude_analysis  TEXT,            -- Phase 1 Claude output
    openai_analysis  TEXT,            -- Phase 1 OpenAI output
    debate_json      TEXT,            -- Phase 2 each LLM's response to the other
    consensus_json   TEXT,            -- Phase 3 moderator synthesis
    elapsed_s        INTEGER,
    cost_estimate_usd REAL,
    error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_insight_library ON studio_insight_reports(library_id);
CREATE INDEX IF NOT EXISTS idx_insight_project ON studio_insight_reports(project_id);
