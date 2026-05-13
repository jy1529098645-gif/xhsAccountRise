-- studio v0.1 schema additions. All new tables namespaced studio_* to avoid
-- collision with upstream crawler tables (notes/comments/authors/images/
-- discover_queue/crawl_log).

-- ---------- generation pipeline ----------

CREATE TABLE IF NOT EXISTS studio_drafts (
    draft_id          TEXT PRIMARY KEY,
    generated_at      INTEGER NOT NULL,
    prompt_version    TEXT,
    brief_json        TEXT NOT NULL,        -- {topic, angle, length, cta, references...}
    status            TEXT NOT NULL DEFAULT 'generated',
                                            -- 'generated' | 'chosen' | 'discarded'
    notes             TEXT
);
CREATE INDEX IF NOT EXISTS idx_drafts_generated_at ON studio_drafts(generated_at DESC);

CREATE TABLE IF NOT EXISTS studio_draft_candidates (
    candidate_id      TEXT PRIMARY KEY,
    draft_id          TEXT NOT NULL,
    llm               TEXT NOT NULL,        -- 'claude-opus-4-7' | 'deepseek-v3' | ...
    title             TEXT,
    body              TEXT,
    tags_json         TEXT,
    cover_prompt      TEXT,
    hook_type         TEXT,
    predicted_likes   INTEGER,
    self_score        REAL,                 -- LLM's own 0-10 confidence
    self_critique     TEXT,
    meta_json         TEXT,                 -- char_count, latency_ms, token_usage, cost
    human_score       INTEGER,              -- 1-5 user rating; NULL = unreviewed
    chosen            INTEGER NOT NULL DEFAULT 0,
    created_at        INTEGER NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES studio_drafts(draft_id)
);
CREATE INDEX IF NOT EXISTS idx_candidates_draft ON studio_draft_candidates(draft_id);
CREATE INDEX IF NOT EXISTS idx_candidates_llm ON studio_draft_candidates(llm);

-- ---------- my published posts (closed-loop tracking) ----------

CREATE TABLE IF NOT EXISTS studio_my_posts (
    my_post_id        TEXT PRIMARY KEY,     -- our UUID, stable across renames
    draft_id          TEXT,                 -- which generation produced this
    candidate_id      TEXT,                 -- which LLM's candidate was chosen
    xhs_note_id       TEXT,                 -- xhs's real note_id once published
    xhs_url           TEXT,
    published_at      INTEGER,
    status            TEXT NOT NULL DEFAULT 'draft',
                                            -- 'draft' | 'scheduled' | 'published' | 'archived'
    title             TEXT,
    body              TEXT,
    tags_json         TEXT,
    cover_local_path  TEXT,
    niche             TEXT,                 -- product-facing categorisation
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES studio_drafts(draft_id),
    FOREIGN KEY (candidate_id) REFERENCES studio_draft_candidates(candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_mypost_xhs ON studio_my_posts(xhs_note_id);
CREATE INDEX IF NOT EXISTS idx_mypost_status ON studio_my_posts(status);

CREATE TABLE IF NOT EXISTS studio_post_metrics (
    my_post_id        TEXT NOT NULL,
    ts                INTEGER NOT NULL,
    liked             INTEGER,
    collected         INTEGER,
    commented         INTEGER,
    shared            INTEGER,
    raw_json          TEXT,
    PRIMARY KEY (my_post_id, ts),
    FOREIGN KEY (my_post_id) REFERENCES studio_my_posts(my_post_id)
);

-- ---------- DNA analysis artifacts ----------

CREATE TABLE IF NOT EXISTS studio_dna_artifacts (
    version           TEXT PRIMARY KEY,     -- 'YYYY-MM-DD' or 'YYYY-MM-DD-N'
    created_at        INTEGER NOT NULL,
    payload_json      TEXT NOT NULL,        -- full DNA report
    summary           TEXT                  -- 1-paragraph TL;DR
);

CREATE TABLE IF NOT EXISTS studio_hook_templates (
    template_id          TEXT PRIMARY KEY,
    category             TEXT NOT NULL,     -- '数字型' | '痛点型' | '故事型' | ...
    pattern              TEXT NOT NULL,     -- human-readable structural pattern
    example_note_ids_json TEXT,             -- top exemplars
    avg_likes            REAL,
    sample_size          INTEGER,
    last_updated         INTEGER NOT NULL,
    active               INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_hook_active ON studio_hook_templates(active);

-- ---------- prompt versioning ----------

CREATE TABLE IF NOT EXISTS studio_prompt_versions (
    version           TEXT PRIMARY KEY,     -- semver-ish: 'title_gen-1.0.0'
    generator_name    TEXT NOT NULL,        -- 'title_gen' | 'body_gen' | 'tag_gen' | 'retro'
    prompt_text       TEXT NOT NULL,
    created_at        INTEGER NOT NULL,
    parent_version    TEXT,
    diff_summary      TEXT,                 -- why this version was forked
    performance_json  TEXT,                 -- aggregated KPI of drafts using this version
    active            INTEGER NOT NULL DEFAULT 1
);

-- ---------- retrospectives ----------

CREATE TABLE IF NOT EXISTS studio_retrospectives (
    retro_id              TEXT PRIMARY KEY,
    my_post_id            TEXT NOT NULL,
    ran_at                INTEGER NOT NULL,
    llm                   TEXT,
    predicted_metrics_json TEXT,
    actual_metrics_json   TEXT,
    insights_md           TEXT,
    prompt_diff_suggested TEXT,
    diff_status           TEXT NOT NULL DEFAULT 'pending',
                                            -- 'pending' | 'approved' | 'rejected'
    FOREIGN KEY (my_post_id) REFERENCES studio_my_posts(my_post_id)
);
CREATE INDEX IF NOT EXISTS idx_retro_post ON studio_retrospectives(my_post_id);
CREATE INDEX IF NOT EXISTS idx_retro_diff_status ON studio_retrospectives(diff_status);

-- ---------- embeddings (W2 will populate) ----------

CREATE TABLE IF NOT EXISTS studio_embeddings (
    obj_type          TEXT NOT NULL,        -- 'note' | 'comment'
    obj_id            TEXT NOT NULL,
    model             TEXT NOT NULL,        -- 'bge-m3' | 'voyage-3' | ...
    dim               INTEGER NOT NULL,
    vector            BLOB NOT NULL,        -- float32 packed
    text_hash         TEXT NOT NULL,        -- detect content change
    created_at        INTEGER NOT NULL,
    PRIMARY KEY (obj_type, obj_id, model)
);
