"""v0.66 (item4) ：星标收藏库。

用户把满意的「方向 / 排期 slot」收藏起来，之后起号 / 排期时可以从自己的库里
复用 —— 解决「起号策略给的方向是一次性的，返回上层调整就拿不到相同结果」的痛点。

存储在当前 library 的 db 里（studio_favorites，见 migration 016），按 project_id
作用域。payload_json 存完整数据，复用时直接读回，不依赖原 pack 还在。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from . import db, project


def add_favorite(kind: str, payload: dict[str, Any], label: str = "") -> dict[str, Any]:
    """收藏一条方向 / slot。kind ∈ {'direction','slot'}。返回新建的 favorite 行。"""
    kind = (kind or "").strip()
    if kind not in ("direction", "slot"):
        raise ValueError(f"invalid kind: {kind!r}（只接受 direction / slot）")
    if not isinstance(payload, dict) or not payload:
        raise ValueError("payload 不能为空")
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    fav_id = uuid.uuid4().hex[:16]
    now = int(time.time())
    if not label:
        label = str(payload.get("name") or payload.get("title") or "未命名")[:80]
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_favorites"
            " (fav_id, project_id, kind, label, payload_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (fav_id, pid, kind, label,
             json.dumps(payload, ensure_ascii=False), now),
        )
    return {
        "fav_id": fav_id, "project_id": pid, "kind": kind,
        "label": label, "payload": payload, "created_at": now,
    }


def list_favorites(kind: str | None = None) -> list[dict[str, Any]]:
    """列出当前 project 的收藏。kind=None 返回全部 ；否则只返回该类。"""
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    with db.connect(read_only=True) as con:
        try:
            if kind in ("direction", "slot"):
                rows = list(con.execute(
                    "SELECT fav_id, project_id, kind, label, payload_json, created_at"
                    " FROM studio_favorites WHERE project_id = ? AND kind = ?"
                    " ORDER BY created_at DESC",
                    (pid, kind),
                ))
            else:
                rows = list(con.execute(
                    "SELECT fav_id, project_id, kind, label, payload_json, created_at"
                    " FROM studio_favorites WHERE project_id = ?"
                    " ORDER BY created_at DESC",
                    (pid,),
                ))
        except sqlite3.OperationalError:
            return []
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        out.append({
            "fav_id": r["fav_id"], "project_id": r["project_id"],
            "kind": r["kind"], "label": r["label"] or "",
            "payload": payload, "created_at": r["created_at"],
        })
    return out


def delete_favorite(fav_id: str) -> bool:
    """删一条收藏。返回是否真的删到（False = 不存在 / 不属于当前 project）。"""
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    with db.connect() as con:
        cur = con.execute(
            "DELETE FROM studio_favorites WHERE fav_id = ? AND project_id = ?",
            (fav_id, pid),
        )
        return cur.rowcount > 0
