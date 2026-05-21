-- v0.64: 对标账号 — 让用户从已上传 library 里挑出"我要参考这个人的"作者，
-- /api/rag/search 在 hybrid_score 里给这些 author_id 的帖子加一个 boost，
-- 出稿时 AI 会优先用他们的爆款做 reference。
--
-- 设计选择：
--   1. 表放在 library db 里 ( per-lib ，跟 notes 同一个 db )，这样切 library
--      对标列表自动跟着切 — 不同 library 里 author_id 命名空间也不同。
--   2. account_id 不加外键到 notes.author_id ：library 里没那个 author 的帖
--      子也允许标 ( e.g. 上传新爬虫 dump 时旧 author 可能已经没了 )。FK 反
--      而会阻塞用户操作。
--   3. note 字段给用户写"为什么标这个" — 自己的备注，不参与 RAG。

CREATE TABLE IF NOT EXISTS studio_benchmark_accounts (
    account_id      TEXT PRIMARY KEY,    -- 对应 notes.author_id
    nickname        TEXT,                -- 加入时的快照，方便列表显示
    note            TEXT,                -- 用户备注，可选
    added_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_benchmark_added ON studio_benchmark_accounts(added_at DESC);
