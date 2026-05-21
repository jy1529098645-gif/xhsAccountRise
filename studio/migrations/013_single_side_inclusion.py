# v0.64.2 ：把原 013_single_side_inclusion.sql 重写为幂等 .py 迁移。
#
# 老 .sql 单一语句 `ALTER TABLE studio_integrated_reports ADD COLUMN
# included_single_side_view_indices TEXT` 不可重入 ：如果上一次 deploy 这
# 一 ALTER 跑成功了但 studio_migrations 那行 INSERT 没落库（Railway 中途
# 重启 / volume 部分恢复 / executescript 边界），下次启动会再 ALTER → "
# duplicate column" → migrate 永远不过 → uvicorn 永远不启 → healthcheck
# 永远不通 → Railway 拒收 deploy → 死循环。
#
# 修法 ：先 PRAGMA table_info 看列在不在，在了就 no-op。同时兼容旧的 .sql
# 文件名 — 如果 studio_migrations 里已经有 013_single_side_inclusion.sql ，
# 这个 .py 会被当成新的迁移再跑一次 idempotent check，确认列存在后挂上
# .py 名字，从此两个名字都视为 done。
#
# 注 ：删了原 013_single_side_inclusion.sql ，避免 glob 把它再拉进来。

from __future__ import annotations
import sqlite3


def up(con: sqlite3.Connection) -> None:
    # studio_integrated_reports 表本身存不存在 — 不存在就跳过（更早的 006
    # external reports 还没跑或失败的话，这里 ALTER 也会炸）
    has_table = con.execute(
        "SELECT name FROM sqlite_master"
        " WHERE type='table' AND name='studio_integrated_reports'"
    ).fetchone()
    if not has_table:
        return

    cols = {r[1] for r in con.execute(
        "PRAGMA table_info(studio_integrated_reports)"
    )}
    if "included_single_side_view_indices" in cols:
        return  # 已经加过 ；幂等 no-op

    con.execute(
        "ALTER TABLE studio_integrated_reports"
        " ADD COLUMN included_single_side_view_indices TEXT"
    )
