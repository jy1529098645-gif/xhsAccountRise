# v0.62.10 ：重命名 strategy → composer 系列表 + 视图。
#
# 为什么是 .py 而不是 .sql ：
#   纯 SQL 的 ALTER TABLE RENAME 不支持 IF EXISTS，且 SQLite 无控制流。
#   在如下任一异常状态下，单纯 ALTER 会失败并 rollback 整个 migration ：
#     • 已经迁移过（target 存在，source 不存在）
#     • 部分迁移（其中一张表迁了另一张没迁，Railway 中途重启可能造成）
#     • 上次迁移失败留下脏状态
#   失败 → studio_migrations 不记录 → 下次启动重试 → 再失败 → 永久 500。
#
# 这个 .py 版本读 sqlite_master 决定每张表当前状态，按需 ALTER / COPY /
# DROP，所有路径都达到目标终态。多次跑也安全（idempotent）。

from __future__ import annotations
import sqlite3


def up(con: sqlite3.Connection) -> None:
    tables = {
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    # ── 1. studio_strategies → studio_composer_packs ──────────────────
    has_old = "studio_strategies" in tables
    has_new = "studio_composer_packs" in tables
    if has_old and not has_new:
        # 干净的 rename — 最常见路径
        con.execute("ALTER TABLE studio_strategies RENAME TO studio_composer_packs")
    elif has_old and has_new:
        # 部分迁移残留 ：两张表都在。把旧表里的行 merge 到新表（按主键
        # 去重保留新表的版本），然后 drop 旧表。INSERT OR IGNORE 保证不
        # 覆盖已存在的新表数据。
        con.execute(
            "INSERT OR IGNORE INTO studio_composer_packs "
            "SELECT * FROM studio_strategies"
        )
        con.execute("DROP TABLE studio_strategies")
    # else (only new, or neither): 不动 ；neither 的情况 migrations 001-013
    # 还没跑完，不归这个迁移管。

    # ── 2. studio_strategy_performance → studio_composer_pack_performance ──
    has_old_p = "studio_strategy_performance" in tables
    has_new_p = "studio_composer_pack_performance" in tables
    if has_old_p and not has_new_p:
        con.execute(
            "ALTER TABLE studio_strategy_performance "
            "RENAME TO studio_composer_pack_performance"
        )
    elif has_old_p and has_new_p:
        con.execute(
            "INSERT OR IGNORE INTO studio_composer_pack_performance "
            "SELECT * FROM studio_strategy_performance"
        )
        con.execute("DROP TABLE studio_strategy_performance")

    # ── 3. view studio_performance_rollup ─────────────────────────────
    # 老 view 引用了已经被 rename 的表名 — DROP + 重建指向新表。
    # 只在底层 performance 表存在时才重建，否则 view 创建会失败。
    has_perf = "studio_composer_pack_performance" in {
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    has_draft_perf = "studio_draft_performance" in {
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    con.execute("DROP VIEW IF EXISTS studio_performance_rollup")
    if has_perf and has_draft_perf:
        con.execute("""
            CREATE VIEW studio_performance_rollup AS
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
            FROM studio_draft_performance dp
        """)
