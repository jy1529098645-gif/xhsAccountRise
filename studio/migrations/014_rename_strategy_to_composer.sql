-- v0.62.5 ：起号策略板块整体并入出稿板块 (Composer)。
-- 表名/模块名从 strategy 改为 composer 以反映新的板块归属：
--   studio_strategies            → studio_composer_packs
--   studio_strategy_performance  → studio_composer_pack_performance
--
-- SQLite ≥3.25 的 ALTER TABLE RENAME 会自动更新 view / trigger / FK 的引用，
-- 但为了在更老的 SQLite 上也能跑且语义清晰，我们手动 DROP+CREATE 那个 VIEW。
-- 索引名按 SQLite 习惯随表自动迁移（idx_strategies_* 还指向新表）。
--
-- IF EXISTS / IF NOT EXISTS 保证 ：
--   • 老库（有 studio_strategies）：ALTER 把表改名到新名字
--   • 新库（migration 004 创建的还是老名）：跑完 004 → 跑到这里把它改名
--     一次完成，跑完后 schema 用新名

-- 1. 主 pack 表
ALTER TABLE studio_strategies RENAME TO studio_composer_packs;

-- 2. pack 表现表
ALTER TABLE studio_strategy_performance RENAME TO studio_composer_pack_performance;

-- 3. 重建跨表 view（migration 010 创建的 studio_performance_rollup）
--    确保 view 的 SELECT 文本指向新表名。
DROP VIEW IF EXISTS studio_performance_rollup;

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
FROM studio_composer_pack_performance sp
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
