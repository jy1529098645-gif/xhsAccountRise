-- v0.58: product context — let user upload "what your product actually is"
-- so Strategy / Compose / Insight prompts can heavily reference real features
-- and brand voice instead of hallucinating generic AI-tool marketing copy.
--
-- Why a new table (vs reusing studio_external_reports):
--   - external_reports is library-scoped (analyses/articles ABOUT a corpus)
--   - product_context is account-scoped (description OF your own product/account)
--   - Conceptually distinct: external reports = research input; product context
--     = brand bible. Keep them separate so the prompt blocks have different
--     framings ("参考分析" vs "你的产品/账号定位").
--
-- Project-level: each project has 0..N active product_contexts; pipelines
-- read all active ones for the current project.
CREATE TABLE IF NOT EXISTS studio_product_contexts (
    context_id      TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    name            TEXT NOT NULL,             -- user-friendly label
    body_text       TEXT NOT NULL,             -- the actual content (paste or extracted)
    source_format   TEXT NOT NULL DEFAULT 'paste',  -- 'paste'|'pdf'|'docx'|'md'|'txt'
    source_filename TEXT,                       -- original filename if uploaded
    chars           INTEGER,                    -- length(body_text) for quick stats
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1  -- 0 = archived (kept for history)
);
CREATE INDEX IF NOT EXISTS idx_pctx_project ON studio_product_contexts(project_id, active);
CREATE INDEX IF NOT EXISTS idx_pctx_created ON studio_product_contexts(created_at DESC);
