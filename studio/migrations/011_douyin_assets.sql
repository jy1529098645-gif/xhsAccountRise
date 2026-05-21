-- v0.57 — Douyin pipeline assets.
--
-- 1. studio_douyin_titles  : the 1380-entry hand-curated title library.
--                            Loaded once from assets/title_library.json on
--                            first run. FTS5 index for retrieval during
--                            Douyin compose.
-- 2. studio_douyin_drafts_meta : per-draft Douyin metadata
--                            (content_bucket, predicted_metrics_json,
--                             library_title_ids_json, duration_sec).
--                            Joined with studio_drafts at read time so the
--                            DraftDetail page can render the Provenance panel.

CREATE TABLE IF NOT EXISTS studio_douyin_titles (
    title_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT NOT NULL,
    title           TEXT NOT NULL,
    hashtags_json   TEXT,
    char_len        INTEGER,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_douyin_titles_category
    ON studio_douyin_titles(category);

-- FTS5 trigram index so cheap substring queries find Chinese titles too.
-- Trigram tokenizer matches the existing FTS for `notes` (build_index.py),
-- so the same query syntax we already use for RAG works here.
CREATE VIRTUAL TABLE IF NOT EXISTS studio_fts_douyin_titles
USING fts5(title_id UNINDEXED, title, category, tokenize = 'trigram');


CREATE TABLE IF NOT EXISTS studio_douyin_drafts_meta (
    candidate_id              TEXT PRIMARY KEY,
    draft_id                  TEXT NOT NULL,
    content_bucket_id         TEXT,
    content_bucket_label      TEXT,
    predicted_metrics_json    TEXT,
    library_title_ids_json    TEXT,
    duration_sec              INTEGER,
    hashtags_json             TEXT,
    created_at                INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_douyin_meta_draft
    ON studio_douyin_drafts_meta(draft_id);
