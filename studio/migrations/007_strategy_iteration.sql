-- Strategy iteration: feed actual post performance back into the planner so
-- the next cycle's strategy is informed by what actually worked.

-- Store the user's performance feedback as a JSON blob per pack (keyed by
-- slot index). We keep it loose because users will paste from all over the
-- place — likes/comments/saves, or whole reports, or a screenshot
-- transcription.
CREATE TABLE IF NOT EXISTS studio_strategy_performance (
    feedback_id     TEXT PRIMARY KEY,
    pack_id         TEXT NOT NULL,
    project_id      TEXT,
    library_id      TEXT,
    created_at      INTEGER NOT NULL,
    raw_notes       TEXT,             -- user's free-text summary
    per_slot_json   TEXT,             -- JSON: [{slot_idx, likes, comments, saves, ...}]
    overall_json    TEXT              -- JSON: {follower_delta, total_likes, ...}
);
CREATE INDEX IF NOT EXISTS idx_perf_pack    ON studio_strategy_performance(pack_id);
CREATE INDEX IF NOT EXISTS idx_perf_project ON studio_strategy_performance(project_id);

-- Link each strategy pack to its parent (so we can build a chain across cycles).
ALTER TABLE studio_strategies ADD COLUMN parent_pack_id TEXT;
ALTER TABLE studio_strategies ADD COLUMN iteration_n   INTEGER NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_strategies_parent ON studio_strategies(parent_pack_id);
