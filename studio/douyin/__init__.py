"""Douyin-specific content pipeline.

This package codifies everything we know about Douyin video generation that
makes it materially different from the xhs (small-red-book) flow the rest of
studio defaults to.

What lives here:
  - `playbook`        — codified KPI thresholds, content-type baselines,
                         keyword opportunity scores, hashtag priors, duration
                         buckets. Distilled from a 10091-video analysis report
                         (see assets/抖音视频起号数据分析报告.pdf).
  - `title_library`   — 1380 hand-curated Douyin titles across 15 categories
                         (DDL急救 / 留子精神状态 / 文献检索 / ...) loaded into
                         SQLite for FTS retrieval during compose.
  - `prompts`         — Douyin-shape generation prompts (caption + hashtags +
                         hook_3s + shots[] + cta_voice + predicted_metrics).
  - `ingest`          — One-shot scripts that parse the source PDFs into
                         JSON (assets/title_library.json) and seed the DB.

Activation path: when `Brief.platform == "douyin"`, the agents/drafter
pipeline routes through this module's prompts + schema and persists per-draft
Douyin metadata that the DraftDetail page renders via DouyinProvenancePanel.
"""
