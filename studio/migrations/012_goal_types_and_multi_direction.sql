-- v0.59: 起号目标分类（goal_type）+ 多方向选择（chosen_direction_idxs）
--
-- 之前架构 ：1 个 direction → 30 篇全部锁死。问题：
--   · 老司机账号往往 3-5 个相关主题轮换，单方向锁死太死板
--   · 不同账号目标（情感/学术/产品/科技/教学）的整套打法完全不一样，
--     但当前所有 goal 共用一套 prompt
--
-- v0.59 改造 ：
--   · goal_type: 8 大起号目标（个人分享 / 情感 / 学术 / 产品/SaaS /
--     实物种草 / 科技 / 教学 / 职业），每个 voice + required fields 不同
--   · chosen_direction_idxs: JSON array of int，用户可多选 2-5 个方向
--     原 chosen_direction_idx (单选) 字段保留作向后兼容
--   · 旧包打开时前端把 chosen_direction_idx 转为 chosen_direction_idxs=[idx]
ALTER TABLE studio_strategies ADD COLUMN goal_type TEXT;
ALTER TABLE studio_strategies ADD COLUMN chosen_direction_idxs TEXT;  -- JSON array, NULL = use legacy single chosen_direction_idx

CREATE INDEX IF NOT EXISTS idx_strategies_goal ON studio_strategies(goal_type);
