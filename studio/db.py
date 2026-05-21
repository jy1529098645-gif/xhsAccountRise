"""Thin SQLite DAO. Connects to data/xhs.db, applies migrations, exposes helpers.

Read-only paths (notes/comments/images/authors) are reused from the upstream
crawler schema. Write paths target the new studio_* tables defined in
studio/migrations/001_init.sql.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import config, library


def _connect(read_only: bool = False, with_adapter: bool = True) -> sqlite3.Connection:
    db_path = library.current_db_path()
    if read_only:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row

    # Schema adapter: if this library has a schema_map.json, apply CREATE TEMP
    # VIEW statements so the rest of the studio can query the canonical
    # `notes` / `comments` tables regardless of what the source schema looks
    # like. TEMP views are scoped to this connection, so re-applied each time.
    #
    # v0.64.3 ：with_adapter=False 让 migration 连接跳过这一步。原因 ：adapter
    # 在 temp.notes 上挂 VIEW 会 shadow main.notes ，当 migration 跑 ALTER
    # TABLE RENAME 时 SQLite 扫现存 idx_notes_author 重新解析 → 解到 VIEW →
    # "views may not be indexed" → migration 永远跑不过。migration 全部只动
    # studio_* 表 ，不需要 adapter view。
    if with_adapter:
        try:
            from . import adapt
            active_lib_id = library.active_lib_id()
            mapping = adapt.load_map(active_lib_id)
            if mapping:
                adapt.apply_views(con, mapping)
        except Exception:
            # Never let adapter logic break the connection itself.
            pass

    return con


@contextmanager
def connect(read_only: bool = False, with_adapter: bool = True) -> Iterator[sqlite3.Connection]:
    con = _connect(read_only=read_only, with_adapter=with_adapter)
    try:
        yield con
        if not read_only:
            con.commit()
    except Exception:
        if not read_only:
            con.rollback()
        raise
    finally:
        con.close()


# --- migrations -----------------------------------------------------------

def _applied_migrations(con: sqlite3.Connection) -> set[str]:
    con.execute(
        "CREATE TABLE IF NOT EXISTS studio_migrations ("
        "  name TEXT PRIMARY KEY,"
        "  applied_at INTEGER NOT NULL"
        ")"
    )
    return {r[0] for r in con.execute("SELECT name FROM studio_migrations")}


# v0.64.2 ：用户报告 Railway 死循环 — migration 013 的 ALTER TABLE 上次成功了
# 但 studio_migrations 那行 INSERT 没落库（Railway 中途重启 / volume 部分恢复 /
# executescript 边界），后续启动一直重 ALTER → "duplicate column" → migrate 永
# 远不过 → uvicorn 永远不启 → healthcheck 不通 → Railway 拒收。
#
# 防御方案 ：所有 .sql 迁移逐句执行 ，对幂等性错误（duplicate column / table
# already exists / index already exists）吞掉日志继续 — 而不是让单条失败 rollback
# 整个迁移。这只放过"重复 DDL"，其它错误照常 raise 让 migrate 失败可见。
_IDEMPOTENT_ERR_PHRASES = (
    "duplicate column",
    "already exists",      # CREATE TABLE / VIEW / INDEX 重复
)


def _split_sql_statements(sql: str) -> list[str]:
    """SQL state-machine splitter — 只在"真正"的语句分隔 ; 处切。

    v0.64.4 ：原版本 sql.split(";") 太笨 ，碰到 ; 在注释里（e.g.
    "-- has 0..N items; pipelines read all"）或字符串里（e.g.
    DEFAULT 'a;b'）会切错 ，下游 SQLite 报 incomplete input。

    跟踪状态 ：line comment (--) / block comment (/* */) / single quote /
    double quote。只在所有状态都 closed 的时候 ; 才是真分隔符。
    """
    out: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    # 状态机变量
    in_line_comment = False  # 直到 \n
    in_block_comment = False  # 直到 */
    in_single = False         # 直到下一个 ' (sqlite 不支持 \' 转义 ， '' 是 escape)
    in_double = False         # 直到下一个 "
    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if in_line_comment:
            buf.append(c)
            if c == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            buf.append(c)
            if c == "*" and nxt == "/":
                buf.append(nxt); i += 2; in_block_comment = False
                continue
            i += 1
            continue
        if in_single:
            buf.append(c)
            if c == "'":
                # SQLite 转义 ：两个连续单引号 = 字面单引号 ，不结束字符串
                if nxt == "'":
                    buf.append(nxt); i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(c)
            if c == '"':
                if nxt == '"':
                    buf.append(nxt); i += 2
                    continue
                in_double = False
            i += 1
            continue
        # 正常状态 ：看是否进入注释 / 字符串 ， 或者遇到分隔符 ;
        if c == "-" and nxt == "-":
            in_line_comment = True
            buf.append(c); i += 1
            continue
        if c == "/" and nxt == "*":
            in_block_comment = True
            buf.append(c); buf.append(nxt); i += 2
            continue
        if c == "'":
            in_single = True; buf.append(c); i += 1
            continue
        if c == '"':
            in_double = True; buf.append(c); i += 1
            continue
        if c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                # 全是注释 / 空白的块跳过
                meaningful = [
                    line for line in stmt.split("\n")
                    if line.strip() and not line.strip().startswith("--")
                ]
                if meaningful:
                    out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(c); i += 1
    # 尾巴 ：最后一条语句可能没 ;
    tail = "".join(buf).strip()
    if tail:
        meaningful = [
            line for line in tail.split("\n")
            if line.strip() and not line.strip().startswith("--")
        ]
        if meaningful:
            out.append(tail)
    return out


def _execute_sql_tolerant(con: sqlite3.Connection, sql: str, migration_name: str) -> None:
    """逐句执行 .sql 迁移 ，吞掉幂等错误。"""
    for stmt in _split_sql_statements(sql):
        try:
            con.execute(stmt)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if any(p in msg for p in _IDEMPOTENT_ERR_PHRASES):
                # 这一句之前跑过了 — 安静继续
                print(f"[migrate] {migration_name} ：跳过已应用语句 ({e})")
                continue
            raise


def apply_migrations(verbose: bool = True) -> list[str]:
    """Apply pending migrations from studio/migrations/*.{sql,py}.

    v0.62.10 ：支持 .py 迁移 — 为了写出 idempotent 的复杂迁移（e.g. 014
    需要根据当前 schema 状态决定 ALTER / COPY / DROP，纯 SQL 表达不了）。
    .py 文件必须有 `def up(con):` 函数。

    v0.64.2 ：.sql 也走 idempotent — 逐句执行 + 吞 duplicate column / already
    exists ，避免 Railway 等环境下 studio_migrations 跟实际 schema 不同步时
    永久 500。

    v0.64.3 ：with_adapter=False — 跳过 adapter 在 temp.notes 上挂 VIEW 的
    步骤。否则当 migration 做 ALTER TABLE RENAME（e.g. 014） ，SQLite 扫现
    存 idx_notes_author 重新解析时优先解到 temp.view → "views may not be
    indexed" → 永远跑不过。
    """
    applied_now: list[str] = []
    files = sorted(
        list(config.MIGRATIONS_DIR.glob("*.sql"))
        + list(config.MIGRATIONS_DIR.glob("*.py")),
        key=lambda p: p.name,
    )
    with connect(with_adapter=False) as con:
        done = _applied_migrations(con)
        for f in files:
            if f.name in done:
                continue
            if f.suffix == ".sql":
                sql = f.read_text(encoding="utf-8")
                _execute_sql_tolerant(con, sql, f.name)
            elif f.suffix == ".py":
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    f"_migration_{f.stem}", f,
                )
                if spec is None or spec.loader is None:
                    raise RuntimeError(f"can't load migration {f.name}")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if not hasattr(mod, "up"):
                    raise RuntimeError(
                        f"migration {f.name} must define `def up(con):`"
                    )
                mod.up(con)
            else:
                continue
            con.execute(
                "INSERT INTO studio_migrations (name, applied_at) VALUES (?, ?)",
                (f.name, int(time.time())),
            )
            applied_now.append(f.name)
            if verbose:
                print(f"[migrate] applied {f.name}")
    return applied_now


# --- read helpers ---------------------------------------------------------

def fetch_notes_for_analysis(
    min_body_len: int = config.SUBSTANTIVE_BODY_MIN,
) -> list[dict[str, Any]]:
    """Return notes with enough body to analyse. Includes parsed tags list."""
    rows: list[dict[str, Any]] = []
    with connect(read_only=True) as con:
        cur = con.execute(
            "SELECT note_id, title, body, type, author_id, author_nickname,"
            " publish_time_ms, ip_location, liked_count, collected_count,"
            " comment_count, share_count, image_count, video_duration_ms,"
            " tags_json, url"
            " FROM notes"
            " WHERE title IS NOT NULL AND title != ''"
            " AND LENGTH(COALESCE(body, '')) >= ?",
            (min_body_len,),
        )
        for r in cur:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.pop("tags_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
            rows.append(d)
    return rows


def fetch_notes_titles_only() -> list[dict[str, Any]]:
    """All notes with titles regardless of body length (for hook stats)."""
    with connect(read_only=True) as con:
        cur = con.execute(
            "SELECT note_id, title, liked_count, collected_count, comment_count,"
            " share_count, image_count, type, publish_time_ms"
            " FROM notes WHERE title IS NOT NULL AND title != ''"
        )
        return [dict(r) for r in cur]


def fetch_comments() -> list[dict[str, Any]]:
    with connect(read_only=True) as con:
        cur = con.execute(
            "SELECT comment_id, note_id, content, like_count, publish_time_ms,"
            " ip_location FROM comments WHERE content IS NOT NULL"
        )
        return [dict(r) for r in cur]


def fetch_keyword_coverage() -> list[dict[str, Any]]:
    """source_value (search keyword) → discovered count, from discover_queue."""
    with connect(read_only=True) as con:
        cur = con.execute(
            "SELECT source_value AS keyword, source_type, COUNT(*) AS discovered,"
            " SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done_count"
            " FROM discover_queue WHERE source_value IS NOT NULL"
            " GROUP BY source_value, source_type"
        )
        return [dict(r) for r in cur]


# --- write helpers (studio_*) --------------------------------------------

def upsert_hook_template(template: dict[str, Any]) -> None:
    cols = (
        "template_id", "category", "pattern", "example_note_ids_json",
        "avg_likes", "sample_size", "last_updated", "active",
    )
    with connect() as con:
        con.execute(
            f"INSERT OR REPLACE INTO studio_hook_templates ({','.join(cols)})"
            f" VALUES ({','.join('?' * len(cols))})",
            tuple(template.get(c) for c in cols),
        )


def insert_dna_artifact(version: str, payload_json: str, summary: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO studio_dna_artifacts"
            " (version, created_at, payload_json, summary)"
            " VALUES (?, ?, ?, ?)",
            (version, int(time.time()), payload_json, summary),
        )
