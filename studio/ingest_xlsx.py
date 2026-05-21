"""XLSX → SQLite library adapter.

The studio's RAG / DNA / strategy pipeline all read a canonical `notes` table
(plus the new `studio_note_extras`). xhs comes in as a SQLite from an upstream
crawler, but Douyin (and most third-party report exports) ship as XLSX. This
module turns an XLSX into a `notes`-shaped SQLite that the rest of studio can
consume unchanged.

Currently supports the layout sent by 抖音 video-export tools (see column map
below). Other platforms can add a new mapper here when they show up.

The mapper is column-name driven, not positional — users who renamed columns
or exported a subset still work as long as the core fields are recognised.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

# Lazy-import pandas because the CLI / API entrypoints don't all need it.

# Canonical -> set of source-column names recognised (Chinese + English aliases).
# Order is "first match wins" so put the most specific alias first.
_DOUYIN_COLUMN_MAP: dict[str, tuple[str, ...]] = {
    "title": ("标题", "视频标题", "title"),
    "body": ("视频内容", "字幕", "口播", "transcript", "caption"),
    "url": ("视频链接", "video_url", "url"),
    "author_nickname": ("作者昵称", "作者", "author", "nickname"),
    "author_profile_url": ("作者主页链接", "author_url", "user_url"),
    "author_bio": ("作者简介", "bio", "intro"),
    "author_follower_count": ("粉丝数", "follower_count", "粉丝"),
    "duration_sec": ("时长", "duration", "秒"),
    "liked_count": ("点赞数", "点赞", "likes", "digg_count"),
    "collected_count": ("收藏数", "收藏", "saves", "favorites"),
    "comment_count": ("评论数", "评论", "comments"),
    "share_count": ("分享数", "分享", "shares", "forwards"),
    "publish_time": ("发布时间", "publish_time", "created_at"),
    "search_keyword": ("搜索词", "keyword", "query"),
}


# ---- helpers ------------------------------------------------------------

_HASHTAG_RE = re.compile(r"#([\w一-鿿]+)")
_TRAILING_HASHTAGS_RE = re.compile(r"\s*#[\w一-鿿]+\s*$")


def _extract_hashtags(text: str) -> list[str]:
    """Strip Douyin/xhs-style #hashtags out of a title string. Return the
    list (without the leading #)."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for tag in _HASHTAG_RE.findall(text):
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def _clean_title(text: str) -> str:
    """Drop trailing hashtag run from a title — the hashtags are now in
    tags_json. Keeps the hook-y part of the title readable."""
    if not text:
        return ""
    s = str(text).strip()
    # Repeatedly strip trailing #tag blocks until none left.
    while True:
        new_s = _TRAILING_HASHTAGS_RE.sub("", s).rstrip()
        if new_s == s:
            break
        s = new_s
    return s


_VIDEO_ID_RE = re.compile(r"/video/(\d+)")


def _video_id_from_url(url: str) -> str | None:
    if not url:
        return None
    m = _VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


def _parse_time_ms(raw: Any) -> int | None:
    """Accept a few common publish-time encodings. Returns ms since epoch
    or None when it can't be parsed."""
    if raw is None or raw == "" or (isinstance(raw, float) and raw != raw):
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        # Already in ms?
        return int(v) if v > 10_000_000_000 else int(v * 1000)
    s = str(raw).strip()
    if not s:
        return None
    import datetime as _dt
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return int(_dt.datetime.strptime(s, fmt).timestamp() * 1000)
        except ValueError:
            continue
    return None


def _resolve_columns(df_columns: list[str],
                     column_map: dict[str, tuple[str, ...]]
                     ) -> dict[str, str]:
    """Map canonical-field-name → actual DataFrame column name. Returns only
    the canonical fields that were found in the source."""
    lower_lookup = {str(c).strip().lower(): c for c in df_columns}
    out: dict[str, str] = {}
    for canonical, aliases in column_map.items():
        for a in aliases:
            real = lower_lookup.get(a.strip().lower())
            if real is not None:
                out[canonical] = real
                break
    return out


# ---- main ingest --------------------------------------------------------

def build_douyin_db(xlsx_path: Path, dest_db: Path) -> dict[str, Any]:
    """Read a Douyin-shaped xlsx and write a SQLite at `dest_db` carrying:
        - notes:               canonical fields the rest of studio reads
        - studio_note_extras:  search_keyword + author_follower_count
        - comments:            empty table (schema match; xlsx has no comments)

    Returns ingest stats (rows in, rows out, recognised columns, dropped).
    """
    try:
        import pandas as pd
    except ImportError as e:
        raise RuntimeError(
            "pandas + openpyxl are required for xlsx ingest. "
            "Run: pip install pandas openpyxl"
        ) from e

    if not xlsx_path.exists():
        raise FileNotFoundError(xlsx_path)

    df = pd.read_excel(xlsx_path)
    cols = _resolve_columns(list(df.columns), _DOUYIN_COLUMN_MAP)

    # Sanity check: we MUST have at least title + likes — without those there's
    # nothing the strategy/RAG layers can do.
    if "title" not in cols:
        raise ValueError(
            f"could not find a title column in xlsx (looked for "
            f"{_DOUYIN_COLUMN_MAP['title']}). Found columns: {list(df.columns)}"
        )

    dest_db.parent.mkdir(parents=True, exist_ok=True)
    if dest_db.exists():
        dest_db.unlink()

    con = sqlite3.connect(dest_db)
    con.execute("PRAGMA journal_mode=WAL")

    # Mirror the canonical xhs `notes` schema so the rest of studio's code
    # (db.fetch_notes_for_analysis, rag.retrieve, etc.) can read this exactly
    # the same way it reads an upstream xhs library.
    con.executescript("""
        CREATE TABLE notes (
            note_id           TEXT PRIMARY KEY,
            xsec_token        TEXT,
            url               TEXT,
            type              TEXT,
            title             TEXT,
            body              TEXT,
            author_id         TEXT,
            author_nickname   TEXT,
            publish_time_ms   INTEGER,
            last_update_ms    INTEGER,
            ip_location       TEXT,
            liked_count       INTEGER,
            collected_count   INTEGER,
            comment_count     INTEGER,
            share_count       INTEGER,
            image_count       INTEGER,
            video_url         TEXT,
            video_duration_ms INTEGER,
            tags_json         TEXT,
            at_users_json     TEXT,
            raw_json          TEXT,
            crawled_at        INTEGER,
            updated_at        INTEGER
        );
        CREATE TABLE comments (
            comment_id        TEXT PRIMARY KEY,
            note_id           TEXT,
            user_id           TEXT,
            nickname          TEXT,
            content           TEXT,
            like_count        INTEGER,
            sub_comment_count INTEGER,
            created_at_ms     INTEGER,
            crawled_at        INTEGER
        );
        CREATE INDEX idx_notes_likes ON notes(liked_count DESC);
        CREATE INDEX idx_notes_publish ON notes(publish_time_ms DESC);
        CREATE INDEX idx_comments_note ON comments(note_id);
    """)

    now = int(time.time())
    out_rows = 0
    dropped_no_id = 0
    extras_rows: list[tuple[str, str | None, int | None]] = []
    seen_ids: set[str] = set()

    def _coerce_int(v: Any) -> int | None:
        if v is None or (isinstance(v, float) and v != v):
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def _coerce_str(v: Any) -> str:
        if v is None or (isinstance(v, float) and v != v):
            return ""
        return str(v)

    for _, row in df.iterrows():
        title_raw = _coerce_str(row.get(cols["title"]))
        url = _coerce_str(row.get(cols["url"], "")) if "url" in cols else ""
        video_id = _video_id_from_url(url) or None
        # When url doesn't carry an id (or column missing), synthesise a
        # stable id from the row content so we still write something.
        if not video_id:
            video_id = "syn_" + uuid.uuid5(uuid.NAMESPACE_URL,
                                            f"{url}|{title_raw}").hex[:16]
        if video_id in seen_ids:
            # Duplicate URLs do happen in scraped exports — skip to keep
            # note_id PK clean.
            dropped_no_id += 1
            continue
        seen_ids.add(video_id)

        title_clean = _clean_title(title_raw)
        tags = _extract_hashtags(title_raw)
        body = _coerce_str(row.get(cols["body"], "")) if "body" in cols else ""

        duration_sec = _coerce_int(row.get(cols["duration_sec"])) if "duration_sec" in cols else None
        duration_ms = duration_sec * 1000 if duration_sec is not None else None

        liked = _coerce_int(row.get(cols["liked_count"])) if "liked_count" in cols else None
        collected = _coerce_int(row.get(cols["collected_count"])) if "collected_count" in cols else None
        commented = _coerce_int(row.get(cols["comment_count"])) if "comment_count" in cols else None
        shared = _coerce_int(row.get(cols["share_count"])) if "share_count" in cols else None
        followers = _coerce_int(row.get(cols["author_follower_count"])) if "author_follower_count" in cols else None
        publish_ms = _parse_time_ms(row.get(cols["publish_time"])) if "publish_time" in cols else None

        author_nick = _coerce_str(row.get(cols["author_nickname"], "")) if "author_nickname" in cols else ""
        author_profile = _coerce_str(row.get(cols["author_profile_url"], "")) if "author_profile_url" in cols else ""
        # Pull a stable author_id from the profile URL when possible.
        m = re.search(r"/user/([^/?#]+)", author_profile) if author_profile else None
        author_id = m.group(1) if m else (author_profile or author_nick or "")

        search_kw = _coerce_str(row.get(cols["search_keyword"], "")) if "search_keyword" in cols else ""

        con.execute(
            "INSERT INTO notes ("
            " note_id, url, type, title, body,"
            " author_id, author_nickname, publish_time_ms,"
            " liked_count, collected_count, comment_count, share_count,"
            " image_count, video_url, video_duration_ms,"
            " tags_json, raw_json, crawled_at, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                video_id, url, "video", title_clean, body,
                author_id, author_nick, publish_ms,
                liked, collected, commented, shared,
                0, url, duration_ms,
                json.dumps(tags, ensure_ascii=False),
                None, now, now,
            ),
        )
        if search_kw or followers is not None:
            extras_rows.append((video_id, search_kw or None, followers))
        out_rows += 1

    # studio_note_extras lives in the same DB so a single
    # adoption / migration cycle covers it.
    con.executescript("""
        CREATE TABLE IF NOT EXISTS studio_note_extras (
            note_id                TEXT PRIMARY KEY,
            search_keyword         TEXT,
            author_follower_count  INTEGER,
            created_at             INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_note_extras_search
            ON studio_note_extras(search_keyword);
    """)
    if extras_rows:
        con.executemany(
            "INSERT OR REPLACE INTO studio_note_extras"
            " (note_id, search_keyword, author_follower_count, created_at)"
            " VALUES (?, ?, ?, ?)",
            [(nid, kw, fc, now) for (nid, kw, fc) in extras_rows],
        )

    con.commit()
    con.close()

    return {
        "rows_in": int(len(df)),
        "rows_out": out_rows,
        "dropped_duplicate": dropped_no_id,
        "extras_rows": len(extras_rows),
        "columns_recognised": cols,
        "source_columns": [str(c) for c in df.columns],
    }


def import_xlsx_library(xlsx_path: Path, display_name: str,
                         platform: str = "douyin",
                         project_id: str | None = None) -> tuple[Any, dict[str, Any]]:
    """End-to-end: ingest xlsx → register as a studio library. Returns
    (LibraryMeta, ingest_stats)."""
    from . import library
    lib_id = library._alloc_id(display_name)  # type: ignore[attr-defined]
    dest_dir = library.LIBRARIES_DIR / lib_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_db = dest_dir / "xhs.db"
    stats = build_douyin_db(xlsx_path, dest_db)

    meta = library._ingest_stats(  # type: ignore[attr-defined]
        library.LibraryMeta(
            lib_id=lib_id,
            display_name=display_name,
            uploaded_at=int(time.time()),
            source="xlsx_import",
            size_bytes=dest_db.stat().st_size,
            platform=library.normalise_platform(platform),
            project_id=project_id or library._current_project_id(),  # type: ignore[attr-defined]
        )
    )
    library._write_meta(lib_id, meta)  # type: ignore[attr-defined]
    return meta, stats
