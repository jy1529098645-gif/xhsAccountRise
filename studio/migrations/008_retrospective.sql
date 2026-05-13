-- Retrospective ("复盘") — closes the loop on the 起号策略 flow.
-- After user publishes drafts, they come back here with actual numbers
-- (likes/comments/saves/views), and AI rolls those + the drafts up into a
-- review report and the next-cycle strategy.

-- A draft becomes "final" when user has picked the chosen candidate, and
-- becomes "published" when they actually posted it externally. We track
-- the two states + the (possibly edited) final body so the retrospective
-- page can show what was REALLY posted vs what AI originally drafted.
ALTER TABLE studio_drafts ADD COLUMN published      INTEGER NOT NULL DEFAULT 0;
ALTER TABLE studio_drafts ADD COLUMN published_at   INTEGER;
ALTER TABLE studio_drafts ADD COLUMN published_url  TEXT;
ALTER TABLE studio_drafts ADD COLUMN published_title TEXT;
ALTER TABLE studio_drafts ADD COLUMN published_body  TEXT;
ALTER TABLE studio_drafts ADD COLUMN published_notes TEXT;  -- user free-form
CREATE INDEX IF NOT EXISTS idx_drafts_published ON studio_drafts(published);

-- Per-published-draft performance datapoint(s). One draft can have several
-- (e.g. user logs metrics at +1d / +1w / +1m).
CREATE TABLE IF NOT EXISTS studio_draft_performance (
    perf_id        TEXT PRIMARY KEY,
    draft_id       TEXT NOT NULL,
    project_id     TEXT,
    recorded_at    INTEGER NOT NULL,
    likes          INTEGER,
    comments       INTEGER,
    saves          INTEGER,
    shares         INTEGER,
    views          INTEGER,
    follower_delta INTEGER,
    notes          TEXT,
    FOREIGN KEY (draft_id) REFERENCES studio_drafts(draft_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_perf_draft   ON studio_draft_performance(draft_id);
CREATE INDEX IF NOT EXISTS idx_perf_project ON studio_draft_performance(project_id);

-- A "retrospective report" is the LLM's analysis across a batch of published
-- drafts + their performance. Mirrors studio_insight_reports.
CREATE TABLE IF NOT EXISTS studio_retrospective_reports (
    review_id       TEXT PRIMARY KEY,
    project_id      TEXT,
    library_id      TEXT,
    created_at      INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    draft_ids_json  TEXT,            -- which drafts this review covers
    analysis_json   TEXT,            -- {wins, losses, patterns, next_actions, ...}
    elapsed_s       INTEGER,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_project ON studio_retrospective_reports(project_id);
