-- v0.56 — Note extras for Douyin (and future video-platform) ingests.
--
-- The canonical `notes` table comes from upstream crawlers and we don't want
-- to ALTER it (xhs vs douyin ingests would diverge, schema_map.json views
-- aren't ALTER-friendly, etc.). Keep new platform-specific signals in a
-- side table keyed by note_id and JOIN at query time.
--
-- search_keyword         — the original retrieval keyword the row came in
--                          under (Douyin's "搜索词" column). Lets the RAG
--                          filter by topic-cluster without re-matching text.
-- author_follower_count  — author fan count at ingest time. Douyin's
--                          "粉丝数". xhs author data doesn't carry this so
--                          xhs rows will simply have NULL here.

CREATE TABLE IF NOT EXISTS studio_note_extras (
    note_id                TEXT PRIMARY KEY,
    search_keyword         TEXT,
    author_follower_count  INTEGER,
    created_at             INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_note_extras_search
    ON studio_note_extras(search_keyword);
CREATE INDEX IF NOT EXISTS idx_note_extras_followers
    ON studio_note_extras(author_follower_count);
