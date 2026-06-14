# v0.66 (bugfix) ：修复历史 pack 的 pack_id 不一致。
#
# 病根 ：expand() 里 StrategyPack.new() 会生成一个**新的随机 pack_id**，而不是
# 复用 propose 创建的 DB 行 pack_id。结果 studio_composer_packs 行的 pack_id（来自
# propose）和它 pack_json 内部的 pack_id（来自 expand 的 new()）对不上。前端拿到
# pack_json 里的内部 id 后，regenerate_slot / IterateCard 等按 pack_id 的调用全部
# 命中不存在的 id → 404。
#
# 代码侧已在 pipeline.expand() 里 `pack.pack_id = pack_id` 修掉源头；这个迁移把
# 已经存在的历史 pack 的 pack_json.pack_id 回填成行 pack_id，让老数据也自愈。
#
# 幂等 ：已经一致的行直接跳过；表/列不存在直接返回。

from __future__ import annotations

import json
import sqlite3


def up(con: sqlite3.Connection) -> None:
    has_table = con.execute(
        "SELECT name FROM sqlite_master"
        " WHERE type='table' AND name='studio_composer_packs'"
    ).fetchone()
    if not has_table:
        return

    rows = con.execute(
        "SELECT pack_id, pack_json FROM studio_composer_packs"
        " WHERE pack_json IS NOT NULL AND pack_json != ''"
    ).fetchall()
    for row in rows:
        row_id = row[0]
        try:
            payload = json.loads(row[1])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("pack_id") == row_id:
            continue  # 已一致
        payload["pack_id"] = row_id
        con.execute(
            "UPDATE studio_composer_packs SET pack_json = ? WHERE pack_id = ?",
            (json.dumps(payload, ensure_ascii=False), row_id),
        )
