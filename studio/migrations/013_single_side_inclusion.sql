-- v0.61.11 ：让用户选择把哪些 single_side_views（没合并的单方观点）
-- 提升到「下游采纳」的状态。被选中的会被注入 Strategy / Composer 的
-- prompt 作为「用户已认可的额外观点」。
--
-- JSON array of int indices (into consensus.single_side_views[])，
-- e.g. "[0, 2, 3]" 表示采纳第 0 / 2 / 3 个 single_side_view。
-- NULL 或 [] = 用户没选任何 = 老行为（默认全部丢掉，只用 consensus_*）。

ALTER TABLE studio_integrated_reports
    ADD COLUMN included_single_side_view_indices TEXT;
