"""Retrieve references for a Brief.

Strategy:
    1. Build an FTS5 MATCH query from the brief's topic (and optionally extras).
    2. For notes: pull top-100 candidates by FTS rank, then re-rank by a hybrid
       score `bm25_norm + alpha * log10(likes + 1)`. Return top-K.
    3. For comments: same query, top-N by FTS rank (relevance is enough).
    4. Hook templates: read top-N from studio_hook_templates (W2 baseline
       falls back to the latest DNA artifact's by_category section).

Trigram tokenizer requires query tokens >= 3 chars. We auto-explode topics
into 3-gram windows when they are >= 3 chars, and fall back to title LIKE
substring search for shorter queries.

v0.63.1: every public search call is wrapped to survive missing FTS / notes /
comments tables — libraries imported from xlsx without `auto_build_fts=1`
used to crash the StrategyRefsPanel with a 500. Now we degrade to LIKE on
notes (or empty for comments) and let the panel render the data we do have.
"""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from typing import Any

from .. import config, db

_log = logging.getLogger(__name__)


_NON_TOKEN = re.compile(r"[\s,，、;；]+", flags=re.UNICODE)
# Strip characters FTS5 treats as operators or punctuation when building the
# phrase. Brace, paren, asterisk, quote, hyphen, plus etc.
_FTS_SCRUB = re.compile(r'[\(\)\{\}\[\]"\'`~!@#\$%\^&\*\+=\-\.,/\\:;<>\?|]')


def _trigrams(piece: str) -> list[str]:
    """Sliding 3-char windows. Trigram tokenizer needs the *exact* trigram so
    we OR every window we can extract."""
    if len(piece) < 3:
        return []
    return [piece[i : i + 3] for i in range(len(piece) - 2)]


def _split_topic(topic: str) -> list[str]:
    """Break a topic into search-friendly chunks (>= 3 chars each)."""
    pieces = [p.strip() for p in _NON_TOKEN.split(topic)]
    pieces = [_FTS_SCRUB.sub("", p) for p in pieces if p]
    return [p for p in pieces if len(p) >= 3]


def _fts_query(topic: str) -> str:
    pieces = _split_topic(topic)
    if not pieces:
        return ""
    seen: set[str] = set()
    grams: list[str] = []
    for piece in pieces:
        if len(piece) == 3:
            if piece not in seen:
                seen.add(piece)
                grams.append(piece)
            continue
        # > 3 chars: emit overlapping trigrams to match any inflection.
        for tri in _trigrams(piece):
            if tri not in seen:
                seen.add(tri)
                grams.append(tri)
    return " OR ".join(f'"{g}"' for g in grams)


def _extract_image_urls_from_raw(raw_json_str: str, note_id: str) -> list[str]:
    """Pull image URLs from a crawler's stashed raw JSON payload.

    xhs crawler shape: raw_json["note"]["noteDetailMap"][note_id]["note"]
        ["imageList"][i]["urlDefault"] (or ["infoList"][j]["url"] for
    a smaller preview size). Returns up to 4 URLs, preferring the
    smaller-size preview when available so the DraftDetail grid loads fast.

    Wrapped in broad try so a non-xhs raw_json or schema variant just
    silently returns []."""
    if not raw_json_str:
        return []
    try:
        d = json.loads(raw_json_str)
    except (json.JSONDecodeError, TypeError):
        return []
    try:
        detail_map = (d.get("note") or {}).get("noteDetailMap") or {}
        entry = detail_map.get(note_id)
        if not entry:
            # Some crawler dumps key by a different note_id (or just one entry).
            entry = next(iter(detail_map.values()), None)
        if not entry:
            return []
        inner = entry.get("note") or {}
        images = inner.get("imageList") or inner.get("image_list") or []
        if not isinstance(images, list):
            return []
    except Exception:
        return []

    urls: list[str] = []
    for img in images[:6]:
        if not isinstance(img, dict):
            continue
        # Prefer a preview-size URL from infoList when present (smaller =
        # faster page load); fall back to urlDefault (full-size).
        chosen = ""
        for info in (img.get("infoList") or []):
            if isinstance(info, dict) and info.get("imageScene") == "WB_PRV":
                chosen = str(info.get("url") or "")
                if chosen:
                    break
        if not chosen:
            chosen = str(img.get("urlDefault") or img.get("url") or "")
        if chosen and chosen.startswith("http"):
            urls.append(chosen)
        if len(urls) >= 4:
            break
    return urls


def search_notes(
    topic: str,
    k: int = 8,
    candidate_pool: int = 200,
    likes_weight: float = 0.5,
    include_images: bool = True,
) -> list[dict[str, Any]]:
    """Return up to `k` notes ranked by FTS relevance × engagement.

    When include_images is True (default), also extracts up to 4 image URLs
    per note from the crawler's raw_json payload so the frontend can show
    a thumbnail strip. Adds an extra column to the SELECT — small cost for
    the visual ROI of "AI 参考了这些真实的图文帖" in DraftDetail.
    """
    fts_q = _fts_query(topic)
    with db.connect(read_only=True) as con:
        # v0.57: also pull video_duration_ms + share_count + video_url so
        # Douyin/BiliBili refs carry their key signals down to the UI.
        # v0.63: pull raw_json too so we can extract image URLs for xhs
        # photo posts. Older crawler DBs may not have these columns —
        # feature-detect via PRAGMA so we don't 500 on legacy libs.
        try:
            cols = {r["name"] for r in con.execute("PRAGMA table_info(notes)")}
        except sqlite3.OperationalError:
            cols = set()
        if not cols:
            # `notes` table not present in this library — nothing to search.
            return []
        extra_cols_wanted = ["video_duration_ms", "share_count", "video_url"]
        if include_images and "raw_json" in cols:
            extra_cols_wanted.append("raw_json")
        extra_cols = [c for c in extra_cols_wanted if c in cols]
        extra_sql = (", " + ", ".join(f"n.{c}" for c in extra_cols)) if extra_cols else ""
        extra_sql_no_prefix = (", " + ", ".join(extra_cols)) if extra_cols else ""
        # v0.63.1: probe FTS table once. If missing (library imported via
        # xlsx without `auto_build_fts=1`), skip straight to LIKE fallback so
        # the panel still renders something useful.
        has_fts = True
        try:
            con.execute("SELECT 1 FROM studio_fts_notes LIMIT 1")
        except sqlite3.OperationalError:
            has_fts = False
        rows: list[dict[str, Any]] = []
        if fts_q and has_fts:
            try:
                cur = con.execute(
                    "SELECT n.note_id, n.title, n.body, n.liked_count,"
                    "       n.collected_count, n.comment_count, n.image_count,"
                    "       n.tags_json, n.author_nickname, n.url"
                    f"{extra_sql},"
                    "       bm25(studio_fts_notes) AS bm"
                    " FROM studio_fts_notes"
                    " JOIN notes n ON n.note_id = studio_fts_notes.note_id"
                    " WHERE studio_fts_notes MATCH ?"
                    " ORDER BY bm LIMIT ?",
                    (fts_q, candidate_pool),
                )
                rows = [dict(r) for r in cur]
            except sqlite3.OperationalError as exc:
                # Malformed MATCH (rare — _fts_query scrubs operators) or
                # the FTS table got dropped mid-session. Fall through to
                # LIKE so the user gets *something*.
                _log.warning("FTS MATCH failed for %r: %s — falling back to LIKE", topic, exc)
        if not rows:
            # Topic too short for trigram, no FTS table, or FTS returned 0:
            # fall back to LIKE on title.
            like = f"%{topic}%"
            try:
                cur = con.execute(
                    "SELECT note_id, title, body, liked_count, collected_count,"
                    "       comment_count, image_count, tags_json, author_nickname, url"
                    f"{extra_sql_no_prefix},"
                    "       0 AS bm"
                    " FROM notes WHERE title LIKE ? OR body LIKE ?"
                    " ORDER BY liked_count DESC LIMIT ?",
                    (like, like, candidate_pool),
                )
                rows = [dict(r) for r in cur]
            except sqlite3.OperationalError as exc:
                _log.warning("notes LIKE fallback failed: %s", exc)
                return []

    if not rows:
        return []
    # Extract image URLs from raw_json now so persistence/downstream sees them
    # as a clean `image_urls` list. raw_json itself is dropped (too big).
    for r in rows:
        if "raw_json" in r:
            r["image_urls"] = _extract_image_urls_from_raw(
                r.get("raw_json") or "", r.get("note_id") or "",
            )
            r.pop("raw_json", None)
        else:
            r["image_urls"] = []
    bms = [r["bm"] for r in rows]
    bm_min, bm_max = min(bms), max(bms)
    bm_span = max(1e-6, bm_max - bm_min)
    for r in rows:
        # bm25 is negative; lower = better. Normalise to [0,1] where 1 = best.
        relevance = (bm_max - r["bm"]) / bm_span if bm_max != bm_min else 1.0
        engagement = math.log10((r["liked_count"] or 0) + 1) / 6.0  # 100K → ~0.83
        r["hybrid_score"] = relevance + likes_weight * engagement
    rows.sort(key=lambda r: r["hybrid_score"], reverse=True)
    return rows[:k]


def search_comments(topic: str, n: int = 15) -> list[dict[str, Any]]:
    fts_q = _fts_query(topic)
    if not fts_q:
        return []
    try:
        with db.connect(read_only=True) as con:
            cur = con.execute(
                "SELECT c.comment_id, c.note_id, c.content, c.like_count,"
                "       bm25(studio_fts_comments) AS bm"
                " FROM studio_fts_comments"
                " JOIN comments c ON c.comment_id = studio_fts_comments.comment_id"
                " WHERE studio_fts_comments MATCH ?"
                " ORDER BY bm LIMIT ?",
                (fts_q, n * 3),
            )
            rows = [dict(r) for r in cur]
    except sqlite3.OperationalError as exc:
        # FTS or comments table missing for this lib — degrade gracefully.
        _log.warning("search_comments unavailable: %s", exc)
        return []
    # Prefer comments with higher likes.
    rows.sort(key=lambda r: (r["like_count"] or 0), reverse=True)
    return rows[:n]


def fetch_hook_summaries(top_n: int = 5) -> list[dict[str, Any]]:
    """Pull hook templates from the latest DNA artifact (W2 fallback) or
    from studio_hook_templates if any have been promoted."""
    try:
        con_cm = db.connect(read_only=True)
    except sqlite3.OperationalError:
        return []
    with con_cm as con:
        # Prefer curated templates table if populated. Order by per-post payoff
        # (avg_likes), not sample size — we want the LLM to anchor on what
        # actually performs, not what's just frequent.
        try:
            rows = list(
                con.execute(
                    "SELECT category, pattern, example_note_ids_json, avg_likes,"
                    "       sample_size FROM studio_hook_templates WHERE active = 1"
                    " ORDER BY avg_likes DESC LIMIT ?",
                    (top_n,),
                )
            )
        except sqlite3.OperationalError:
            rows = []
        if rows:
            return [
                {
                    "category": r["category"],
                    "pattern": r["pattern"],
                    "count": r["sample_size"],
                    "median_likes": r["avg_likes"],
                    "examples": json.loads(r["example_note_ids_json"] or "[]"),
                }
                for r in rows
            ]
        # Fallback: read latest DNA artifact.
        try:
            row = con.execute(
                "SELECT payload_json FROM studio_dna_artifacts"
                " ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
    if not row:
        return []
    try:
        artifact = json.loads(row["payload_json"])
        by_cat = artifact["sections"]["titles"]["by_category"]
    except (KeyError, json.JSONDecodeError):
        return []
    items = []
    for cat, data in by_cat.items():
        if cat in {"其他", "无标题"}:
            continue
        items.append(
            {
                "category": cat,
                "pattern": f"{cat} 标题",
                "count": data["count"],
                "median_likes": data.get("likes", {}).get("median", 0),
                "examples": data.get("examples", [])[:3],
            }
        )
    items.sort(key=lambda i: i["median_likes"] or 0, reverse=True)
    return items[:top_n]


def retrieve_for_brief(topic: str, k_notes: int = 8, n_comments: int = 15) -> dict[str, Any]:
    """Triple-branch retrieve. v0.63.1: each branch is wrapped so a missing
    table / malformed query in one of them doesn't 500 the others — the
    StrategyRefsPanel still gets to render whatever data IS available."""
    refs: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    hooks: list[dict[str, Any]] = []
    try:
        refs = search_notes(topic, k=k_notes)
    except Exception as exc:  # noqa: BLE001 — last-line defence
        _log.exception("retrieve_for_brief.search_notes failed for %r: %s", topic, exc)
    try:
        comments = search_comments(topic, n=n_comments)
    except Exception as exc:  # noqa: BLE001
        _log.exception("retrieve_for_brief.search_comments failed for %r: %s", topic, exc)
    try:
        hooks = fetch_hook_summaries()
    except Exception as exc:  # noqa: BLE001
        _log.exception("retrieve_for_brief.fetch_hook_summaries failed: %s", exc)
    return {"refs": refs, "comments": comments, "hooks": hooks}
