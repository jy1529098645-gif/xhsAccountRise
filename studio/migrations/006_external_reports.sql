-- External reports: reports the user pulled from elsewhere (consulting decks,
-- competitor teardowns, screenshots transcribed to text, etc.) and uploaded
-- so the tool can reference them in Strategy / Composer prompts. Plus the
-- "integrated" report — a GPT-4o synthesis across all uploaded ones (and
-- optionally the tool's own consensus report).

CREATE TABLE IF NOT EXISTS studio_external_reports (
    report_id    TEXT PRIMARY KEY,
    project_id   TEXT,
    library_id   TEXT,             -- nullable: report may apply globally
    name         TEXT NOT NULL,    -- user-supplied title
    source       TEXT,             -- e.g. "粘贴文本", "uploaded.md"
    format       TEXT,             -- 'text' | 'markdown'
    content      TEXT NOT NULL,    -- the actual text body
    uploaded_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_external_reports_project ON studio_external_reports(project_id);
CREATE INDEX IF NOT EXISTS idx_external_reports_library ON studio_external_reports(library_id);

-- Integrated reports: gpt-4o's fusion of N external reports + the latest
-- tool-generated consensus. Stored separately because they have richer
-- structure (consensus-shaped JSON + source breakdown).
CREATE TABLE IF NOT EXISTS studio_integrated_reports (
    integrated_id   TEXT PRIMARY KEY,
    project_id      TEXT,
    library_id      TEXT,
    created_at      INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    source_ids      TEXT,            -- JSON array of studio_external_reports.report_id
    include_consensus_report_id TEXT, -- if set, also fused with this insight report
    consensus_json  TEXT,            -- consensus-shaped result (same renderer as InsightReport)
    elapsed_s       INTEGER,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_integrated_project ON studio_integrated_reports(project_id);
CREATE INDEX IF NOT EXISTS idx_integrated_library ON studio_integrated_reports(library_id);
