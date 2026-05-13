-- 起号策略 (Account Launch Strategy) packs.

CREATE TABLE IF NOT EXISTS studio_strategies (
    pack_id              TEXT PRIMARY KEY,
    library_id           TEXT,
    platform             TEXT,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL,
    status               TEXT NOT NULL DEFAULT 'directions',
                                            -- 'directions' | 'expanded'
    -- Phase 1 inputs/outputs
    input_json           TEXT NOT NULL,     -- AccountInput
    directions_json      TEXT NOT NULL,     -- list[StrategicDirection]
    -- Phase 2 (after user picks a direction)
    chosen_direction_idx INTEGER,
    pack_json            TEXT,              -- StrategyPack (timeline, materials...)
    -- Bookkeeping
    elapsed_s            INTEGER,
    cost_estimate_usd    REAL,
    trace_json           TEXT               -- agent step trace
);

CREATE INDEX IF NOT EXISTS idx_strategies_created ON studio_strategies(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_strategies_library ON studio_strategies(library_id);
