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


def _connect(read_only: bool = False) -> sqlite3.Connection:
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
def connect(read_only: bool = False) -> Iterator[sqlite3.Connection]:
    con = _connect(read_only=read_only)
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


def apply_migrations(verbose: bool = True) -> list[str]:
    """Apply pending migrations from studio/migrations/*.{sql,py}.

    v0.62.10 ：支持 .py 迁移 — 为了写出 idempotent 的复杂迁移（e.g. 014
    需要根据当前 schema 状态决定 ALTER / COPY / DROP，纯 SQL 表达不了）。
    .py 文件必须有 `def up(con):` 函数。
    """
    applied_now: list[str] = []
    files = sorted(
        list(config.MIGRATIONS_DIR.glob("*.sql"))
        + list(config.MIGRATIONS_DIR.glob("*.py")),
        key=lambda p: p.name,
    )
    with connect() as con:
        done = _applied_migrations(con)
        for f in files:
            if f.name in done:
                continue
            if f.suffix == ".sql":
                sql = f.read_text(encoding="utf-8")
                con.executescript(sql)
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
