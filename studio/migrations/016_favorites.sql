-- v0.66 (item4) ：星标收藏库 — 用户把满意的「方向 / 排期 slot」收藏起来，
-- 之后起号/排期时可以从自己的库里复用，避免「返回上层调整就拿不到原来那条」。
--
-- 设计选择：
--   1. 存当前 library db（跟 studio_composer_packs / notes 同一个 db）。切
--      library 收藏列表跟着切，命名空间天然隔离。
--   2. project_id 作用域：一个 library 里多个 project 各有各的收藏。
--   3. payload_json 存完整的方向/slot 数据，复用时直接读回，不依赖原 pack 还在。
--   4. kind ∈ {'direction','slot'}，label 是显示名（方向名 / slot 标题）。

CREATE TABLE IF NOT EXISTS studio_favorites (
    fav_id       TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    kind         TEXT NOT NULL,           -- 'direction' | 'slot'
    label        TEXT,                    -- 显示名（方向名 / slot 标题）
    payload_json TEXT NOT NULL,           -- 完整方向 / slot 数据，可复用
    created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_favorites_proj
    ON studio_favorites(project_id, kind, created_at DESC);
