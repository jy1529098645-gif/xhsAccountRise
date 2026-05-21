"""对标账号 ：用户从已上传 library 里挑出"想参考的"作者 ；/api/rag/search
在 hybrid_score 上对这些 author_id 的帖子加 boost。

存储在当前 library 的 db 里 (studio_benchmark_accounts，见 migration 015)
— 切 library 对标列表自动跟着切。
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

from . import db


def list_accounts() -> list[dict[str, Any]]:
    """返回所有对标账号 + 在当前 library 里的笔记统计。

    join 用 LEFT 是因为允许用户标了之后切到新 library，那批 author_id 在新
    库里没帖子也得显示出来（不然用户以为列表丢了）。
    """
    with db.connect(read_only=True) as con:
        # 笔记统计 ：count + max(liked) + top note title (赞最高那条)
        # 先查 benchmark，再循环查 notes 统计。一次性 JOIN GROUP_CONCAT 也行
        # 但 SQLite 没好用的 first-of-group，分开查清晰一点。
        try:
            rows = list(con.execute(
                "SELECT account_id, nickname, note, added_at"
                " FROM studio_benchmark_accounts"
                " ORDER BY added_at DESC"
            ))
        except sqlite3.OperationalError:
            return []
        out: list[dict[str, Any]] = []
        for r in rows:
            stats = _stats_for_author(con, r["account_id"])
            out.append({
                "account_id": r["account_id"],
                "nickname": r["nickname"] or stats.get("nickname") or "",
                "note": r["note"] or "",
                "added_at": r["added_at"],
                "note_count": stats["note_count"],
                "top_likes": stats["top_likes"],
                "top_title": stats["top_title"],
                "top_url": stats["top_url"],
                "missing_in_library": stats["note_count"] == 0,
            })
        return out


def _stats_for_author(con: sqlite3.Connection, author_id: str) -> dict[str, Any]:
    """跑两个 query ：count，和"赞最高那条"的快照。
    notes 表不存在 / author_id 列不存在时 graceful 退化成空统计。"""
    try:
        c = con.execute(
            "SELECT COUNT(*) AS n FROM notes WHERE author_id = ?",
            (author_id,),
        ).fetchone()
        n = int(c["n"] or 0) if c else 0
    except sqlite3.OperationalError:
        return {"note_count": 0, "top_likes": 0, "top_title": "", "top_url": "", "nickname": ""}
    if n == 0:
        return {"note_count": 0, "top_likes": 0, "top_title": "", "top_url": "", "nickname": ""}
    try:
        top = con.execute(
            "SELECT title, liked_count, url, author_nickname"
            " FROM notes WHERE author_id = ?"
            " ORDER BY liked_count DESC LIMIT 1",
            (author_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        top = None
    if not top:
        return {"note_count": n, "top_likes": 0, "top_title": "", "top_url": "", "nickname": ""}
    return {
        "note_count": n,
        "top_likes": int(top["liked_count"] or 0),
        "top_title": (top["title"] or "")[:120],
        "top_url": top["url"] or "",
        "nickname": top["author_nickname"] or "",
    }


def add_account(account_id: str, nickname: str = "", note: str = "") -> dict[str, Any]:
    """INSERT OR REPLACE — 重复加同一个 account_id 视为编辑 nickname/note。"""
    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("account_id 不能为空")
    nickname = (nickname or "").strip()[:200]
    note = (note or "").strip()[:500]
    # 如果用户没填 nickname，自动从 library 里抓最近一条该 author 的 nickname
    if not nickname:
        try:
            with db.connect(read_only=True) as con:
                row = con.execute(
                    "SELECT author_nickname FROM notes WHERE author_id = ?"
                    " AND author_nickname IS NOT NULL AND author_nickname != ''"
                    " ORDER BY publish_time_ms DESC LIMIT 1",
                    (account_id,),
                ).fetchone()
                if row:
                    nickname = (row["author_nickname"] or "").strip()[:200]
        except sqlite3.OperationalError:
            pass
    with db.connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO studio_benchmark_accounts"
            " (account_id, nickname, note, added_at) VALUES (?, ?, ?, ?)",
            (account_id, nickname, note, int(time.time())),
        )
    return {"account_id": account_id, "nickname": nickname, "note": note}


def remove_account(account_id: str) -> bool:
    with db.connect() as con:
        cur = con.execute(
            "DELETE FROM studio_benchmark_accounts WHERE account_id = ?",
            (account_id,),
        )
        return cur.rowcount > 0


def get_active_ids() -> set[str]:
    """供 retrieve.search_notes 调用 — 一次性拿全集，调用方自己做 set membership。
    表不存在时返回空集，retrieve 自然退化成无 boost。"""
    try:
        with db.connect(read_only=True) as con:
            rows = con.execute(
                "SELECT account_id FROM studio_benchmark_accounts"
            ).fetchall()
            return {r["account_id"] for r in rows if r["account_id"]}
    except sqlite3.OperationalError:
        return set()


def search_authors(q: str, limit: int = 20) -> list[dict[str, Any]]:
    """在当前 library 的 notes 里搜作者 — 按 nickname LIKE 或 author_id 精确匹配。
    GROUP BY author_id 聚合 (note_count, max liked)，按笔记数 + 最高赞综合排序。
    """
    q = (q or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    try:
        with db.connect(read_only=True) as con:
            cur = con.execute(
                "SELECT author_id, author_nickname,"
                "       COUNT(*) AS note_count,"
                "       MAX(liked_count) AS top_likes,"
                "       SUM(liked_count) AS total_likes"
                " FROM notes"
                " WHERE author_id IS NOT NULL AND author_id != ''"
                "   AND (author_nickname LIKE ? OR author_id = ?)"
                " GROUP BY author_id, author_nickname"
                " ORDER BY top_likes DESC, note_count DESC"
                " LIMIT ?",
                (like, q, limit),
            )
            rows = [dict(r) for r in cur]
    except sqlite3.OperationalError:
        return []
    # 已经在对标列表里的标 already_added，前端 disabled 按钮
    active = get_active_ids()
    for r in rows:
        r["already_added"] = r["author_id"] in active
        r["top_likes"] = int(r["top_likes"] or 0)
        r["total_likes"] = int(r["total_likes"] or 0)
        r["note_count"] = int(r["note_count"] or 0)
        r["author_nickname"] = r["author_nickname"] or ""
    return rows
