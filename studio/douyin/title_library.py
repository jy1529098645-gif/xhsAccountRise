"""Title-library DB ingest + retrieval for Douyin compose.

The library is the 1380 hand-curated hook lines from
assets/title_library.json (extracted from the source PDF via
extract_title_library.py). At Douyin compose time we retrieve top-K
matching titles for the topic and inject them into the prompt as
'study these hook patterns, then write a fresh one in the same
voice — do NOT copy verbatim.'

Storage: per-library SQLite. The same `notes`-shaped DB that holds
crawled videos also gets a `studio_douyin_titles` table seeded from
the JSON. Re-seeding is a no-op when row count matches the JSON length.

Retrieval: FTS5 trigram MATCH (re-using the studio FTS infrastructure)
+ optional category filter. When the topic is short (no trigrams), we
fall back to LIKE on title.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from .. import db


_ASSETS = Path(__file__).parent / "assets"
_JSON_PATH = _ASSETS / "title_library.json"

# Trigram tokenizer wants ≥3 chars per token. Below that we LIKE-fallback.
_NON_TOKEN = re.compile(r"[\s,，、;；·]+", flags=re.UNICODE)
_FTS_SCRUB = re.compile(r"[\"'`~!@#\$%\^&\*\+=\-\./\\:;<>\?|\(\)\{\}\[\]]")


def load_titles_from_json() -> list[dict[str, Any]]:
    if not _JSON_PATH.exists():
        return []
    return json.loads(_JSON_PATH.read_text(encoding="utf-8"))


def is_seeded(con: sqlite3.Connection) -> bool:
    try:
        row = con.execute("SELECT COUNT(*) FROM studio_douyin_titles").fetchone()
        return (row[0] or 0) > 0
    except sqlite3.OperationalError:
        return False


def seed_if_empty(con: sqlite3.Connection, *, force: bool = False) -> int:
    """Insert the JSON title library into `studio_douyin_titles` + FTS.

    Returns the number of rows inserted (0 when already seeded and not
    forced). Safe to call on every connection — guarded by is_seeded().
    """
    if not force and is_seeded(con):
        return 0
    rows = load_titles_from_json()
    if not rows:
        return 0
    now = int(time.time())
    if force:
        con.execute("DELETE FROM studio_douyin_titles")
        con.execute("DELETE FROM studio_fts_douyin_titles")
    con.executemany(
        "INSERT INTO studio_douyin_titles "
        "(category, title, hashtags_json, char_len, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (
                r["category"],
                r["title"],
                json.dumps(r.get("hashtags", []), ensure_ascii=False),
                int(r.get("char_len") or len(r["title"])),
                now,
            )
            for r in rows
        ],
    )
    con.executemany(
        "INSERT INTO studio_fts_douyin_titles (title_id, title, category) "
        "SELECT title_id, title, category FROM studio_douyin_titles "
        "WHERE title = ? AND category = ?",
        [(r["title"], r["category"]) for r in rows],
    )
    return len(rows)


def ensure_seeded() -> int:
    """Seed the active library's DB with the title library if not already.
    Safe to call from API/CLI entrypoints — does nothing on subsequent calls."""
    with db.connect() as con:
        return seed_if_empty(con)


# ---- retrieval ---------------------------------------------------------

def _trigrams(piece: str) -> list[str]:
    if len(piece) < 3:
        return []
    return [piece[i : i + 3] for i in range(len(piece) - 2)]


def _fts_query(topic: str) -> str:
    pieces = [p for p in _NON_TOKEN.split(topic or "") if p]
    pieces = [_FTS_SCRUB.sub("", p) for p in pieces]
    pieces = [p for p in pieces if len(p) >= 3]
    if not pieces:
        return ""
    seen: set[str] = set()
    grams: list[str] = []
    for piece in pieces:
        if len(piece) == 3:
            if piece not in seen:
                seen.add(piece); grams.append(piece)
            continue
        for tri in _trigrams(piece):
            if tri not in seen:
                seen.add(tri); grams.append(tri)
    return " OR ".join(f'"{g}"' for g in grams)


def search(
    topic: str | None,
    *,
    categories: list[str] | None = None,
    k: int = 20,
) -> list[dict[str, Any]]:
    """Return up to `k` title-library entries matching the topic.

    - If `categories` is provided, restrict to those categories.
    - If the topic has no trigram-able tokens (or is empty), return the
      first `k` titles from the requested categories (or all categories).
    """
    seed_if_empty_active_db_safely()
    fts_q = _fts_query(topic or "")
    args: list[Any] = []
    with db.connect(read_only=True) as con:
        if fts_q:
            sql = (
                "SELECT t.title_id, t.category, t.title, t.hashtags_json, t.char_len,"
                "       bm25(studio_fts_douyin_titles) AS bm"
                "  FROM studio_fts_douyin_titles f"
                "  JOIN studio_douyin_titles t ON t.title_id = f.title_id"
                " WHERE studio_fts_douyin_titles MATCH ?"
            )
            args.append(fts_q)
            if categories:
                placeholders = ",".join(["?"] * len(categories))
                sql += f" AND t.category IN ({placeholders})"
                args.extend(categories)
            sql += " ORDER BY bm LIMIT ?"
            args.append(k * 3)
            try:
                rows = list(con.execute(sql, args))
            except sqlite3.OperationalError:
                rows = []
        else:
            sql = (
                "SELECT title_id, category, title, hashtags_json, char_len,"
                "       0 AS bm"
                "  FROM studio_douyin_titles"
            )
            if categories:
                placeholders = ",".join(["?"] * len(categories))
                sql += f" WHERE category IN ({placeholders})"
                args.extend(categories)
            sql += " ORDER BY title_id LIMIT ?"
            args.append(k)
            rows = list(con.execute(sql, args))
    out: list[dict[str, Any]] = []
    for r in rows[:k]:
        d = dict(r)
        try:
            d["hashtags"] = json.loads(d.pop("hashtags_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["hashtags"] = []
        out.append(d)
    return out


def seed_if_empty_active_db_safely() -> None:
    """Wrapper around ensure_seeded that swallows errors — safe to call
    from read-path code where a failure shouldn't propagate."""
    try:
        ensure_seeded()
    except Exception:
        pass


def render_for_prompt(titles: list[dict[str, Any]], max_n: int = 18) -> str:
    """Pretty-print retrieved titles as a prompt-context block.

    The LLM sees these as 'study these hook structures, write a fresh
    title in the same family — do NOT copy verbatim'. We include the
    category as a hint so the model knows what tonal mode each came from.
    """
    if not titles:
        return ""
    lines = ["【参考标题库（hand-curated 抖音 hook 池, 共 1380 条）选段】"]
    for t in titles[:max_n]:
        cat_short = t["category"].replace("标题", "").replace("池", "")
        lines.append(f"  · [{cat_short}] {t['title']}")
    lines.append(
        "请用这些 hook 的句式 + 节奏作为参考，但写出新的版本 — 不要逐字照搬，"
        "也不要拼接两条已有标题。最终 caption 必须是原创。"
    )
    return "\n".join(lines)


def stats() -> dict[str, Any]:
    """Diagnostic — count per category for the active DB. Used by /api/status."""
    try:
        with db.connect(read_only=True) as con:
            rows = list(con.execute(
                "SELECT category, COUNT(*) AS n FROM studio_douyin_titles"
                " GROUP BY category ORDER BY n DESC"
            ))
        return {
            "total": sum(r["n"] for r in rows),
            "by_category": {r["category"]: r["n"] for r in rows},
        }
    except sqlite3.OperationalError:
        return {"total": 0, "by_category": {}}
