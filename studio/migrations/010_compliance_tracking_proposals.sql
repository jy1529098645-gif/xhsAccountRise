-- v0.53: closes 6 product gaps in one migration.
--   - studio_compliance_checks: hard redline gate result per candidate
--   - studio_url_fetches:       URL-paste → crawler reingest log
--   - studio_prompt_proposals:  LLM-proposed prompt diffs from retrospective
--   - studio_drafts.parent_draft_id: variant fan-out ancestry
--   - studio_drafts.rag_json:       persist Researcher refs/comments/hooks
--   - studio_performance_rollup:    view bridging strategy + draft perf

-- ---- compliance gate ----------------------------------------------------
CREATE TABLE IF NOT EXISTS studio_compliance_checks (
    check_id        TEXT PRIMARY KEY,
    candidate_id    TEXT NOT NULL,
    draft_id        TEXT NOT NULL,
    checked_at      INTEGER NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'pass',  -- 'pass'|'warn'|'block'
    hits_json       TEXT,                          -- [{term, category, severity, span_start, span_end, where, safe_alternative}]
    rewritten_body  TEXT,                          -- if user clicked "改成安全词"
    rewritten_title TEXT,
    FOREIGN KEY (candidate_id) REFERENCES studio_draft_candidates(candidate_id),
    FOREIGN KEY (draft_id)     REFERENCES studio_drafts(draft_id)
);
CREATE INDEX IF NOT EXISTS idx_compliance_candidate ON studio_compliance_checks(candidate_id);
CREATE INDEX IF NOT EXISTS idx_compliance_draft     ON studio_compliance_checks(draft_id);
CREATE INDEX IF NOT EXISTS idx_compliance_severity  ON studio_compliance_checks(severity);

-- ---- URL paste → reingest log ------------------------------------------
CREATE TABLE IF NOT EXISTS studio_url_fetches (
    fetch_id        TEXT PRIMARY KEY,
    draft_id        TEXT,
    url             TEXT NOT NULL,
    note_id         TEXT,                          -- xhs note_id parsed from URL
    fetched_at      INTEGER NOT NULL,
    status          TEXT NOT NULL,                 -- 'ok'|'rate_limited'|'login_wall'|'no_ssr'|'no_crawler'|'parse_err'|'http_err'
    likes           INTEGER,
    saves           INTEGER,
    comments        INTEGER,
    shares          INTEGER,
    raw_summary     TEXT,                          -- short detail for forensics
    perf_id         TEXT                           -- which perf row was created (if any)
);
CREATE INDEX IF NOT EXISTS idx_url_fetch_draft  ON studio_url_fetches(draft_id);
CREATE INDEX IF NOT EXISTS idx_url_fetch_status ON studio_url_fetches(status);

-- ---- variant fan-out: track ancestry between drafts --------------------
ALTER TABLE studio_drafts ADD COLUMN parent_draft_id TEXT;
ALTER TABLE studio_drafts ADD COLUMN variant_label   TEXT;   -- human-readable: '同主题·痛点角度'
CREATE INDEX IF NOT EXISTS idx_drafts_parent ON studio_drafts(parent_draft_id);

-- ---- persist Researcher refs/comments/hooks per draft -----------------
-- (the bundle already carries this in the API response, but it's not in the
--  DB so we can't show provenance on later visits to DraftDetail)
ALTER TABLE studio_drafts ADD COLUMN rag_json TEXT;

-- ---- prompt-version proposals from retrospective ----------------------
CREATE TABLE IF NOT EXISTS studio_prompt_proposals (
    proposal_id       TEXT PRIMARY KEY,
    review_id         TEXT,                         -- which retrospective triggered it
    project_id        TEXT,
    generator_name    TEXT NOT NULL,                -- 'title_body_gen' | 'critic' | ...
    parent_version    TEXT NOT NULL,                -- the version the diff applies on top of
    proposed_version  TEXT NOT NULL,                -- semver-ish next id
    diff_summary      TEXT,                         -- 1-2 sentences why
    proposed_prompt   TEXT NOT NULL,                -- the full new prompt text
    expected_gain     TEXT,                         -- LLM's claim (qualitative)
    evidence_json     TEXT,                         -- [{draft_id, signal, why}, ...]
    created_at        INTEGER NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',  -- 'pending'|'approved'|'rejected'|'superseded'
    decided_at        INTEGER,
    decided_by        TEXT,
    decision_notes    TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposal_status ON studio_prompt_proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposal_review ON studio_prompt_proposals(review_id);

-- ---- feedback rollup view ---------------------------------------------
-- Unifies pack-level (studio_strategy_performance) and per-draft
-- (studio_draft_performance) so iterate.py + retrospective.py can read
-- from one place. Per-draft rows roll up by joining studio_drafts.notes
-- (which holds the strategy linkage in the brief) — for now we expose
-- both feeds side-by-side and let callers pick. Concrete merging happens
-- in studio/feedback/aggregate.py.
CREATE VIEW IF NOT EXISTS studio_performance_rollup AS
SELECT
    'pack' AS source_kind,
    sp.feedback_id      AS row_id,
    sp.pack_id          AS pack_id,
    NULL                AS draft_id,
    sp.project_id,
    sp.library_id,
    sp.created_at,
    sp.raw_notes        AS notes,
    NULL                AS likes,
    NULL                AS comments,
    NULL                AS saves,
    NULL                AS shares,
    NULL                AS views,
    NULL                AS follower_delta,
    sp.per_slot_json,
    sp.overall_json
FROM studio_strategy_performance sp
UNION ALL
SELECT
    'draft' AS source_kind,
    dp.perf_id          AS row_id,
    NULL                AS pack_id,
    dp.draft_id         AS draft_id,
    dp.project_id,
    NULL                AS library_id,
    dp.recorded_at      AS created_at,
    dp.notes,
    dp.likes,
    dp.comments,
    dp.saves,
    dp.shares,
    dp.views,
    dp.follower_delta,
    NULL                AS per_slot_json,
    NULL                AS overall_json
FROM studio_draft_performance dp;
