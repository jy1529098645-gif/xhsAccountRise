-- Multi-agent pipeline + library awareness.

ALTER TABLE studio_drafts ADD COLUMN mode TEXT NOT NULL DEFAULT 'single';
ALTER TABLE studio_drafts ADD COLUMN library_id TEXT;
ALTER TABLE studio_drafts ADD COLUMN final_candidate_id TEXT;

CREATE TABLE IF NOT EXISTS studio_agent_traces (
    trace_id          TEXT PRIMARY KEY,
    draft_id          TEXT NOT NULL,
    step_index        INTEGER NOT NULL,    -- 0-based execution order
    agent_name        TEXT NOT NULL,       -- 'strategist'|'researcher'|'drafter'|'critic'|'refiner'|'synthesizer'
    llm               TEXT,                -- '' for non-LLM agents (researcher)
    input_summary     TEXT,                -- short JSON or excerpt
    output_summary    TEXT,
    latency_ms        INTEGER,
    cost_estimate_usd REAL,
    error             TEXT,
    raw_response      TEXT,                -- truncated to 4KB for forensics
    created_at        INTEGER NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES studio_drafts(draft_id)
);
CREATE INDEX IF NOT EXISTS idx_traces_draft ON studio_agent_traces(draft_id);
CREATE INDEX IF NOT EXISTS idx_traces_step ON studio_agent_traces(draft_id, step_index);

CREATE TABLE IF NOT EXISTS studio_critiques (
    critique_id        TEXT PRIMARY KEY,
    candidate_id       TEXT NOT NULL,
    draft_id           TEXT NOT NULL,
    critic_llm         TEXT NOT NULL,
    scores_json        TEXT NOT NULL,      -- {hook,language_fit,shareability,brand_safety,...}
    risk_flags_json    TEXT,               -- list of issue strings
    suggestion         TEXT,               -- single most-impactful suggestion
    overall            REAL,               -- weighted aggregate 0-10
    created_at         INTEGER NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES studio_draft_candidates(candidate_id),
    FOREIGN KEY (draft_id) REFERENCES studio_drafts(draft_id)
);
CREATE INDEX IF NOT EXISTS idx_critiques_candidate ON studio_critiques(candidate_id);
CREATE INDEX IF NOT EXISTS idx_critiques_draft ON studio_critiques(draft_id);
