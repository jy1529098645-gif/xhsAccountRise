"""Extract 爆款 DNA from the xhs corpus.

Produces a single JSON artifact under exports/analysis/v{date}.json that the
frontend (or any downstream LLM prompt) can consume.

This pass deliberately avoids LLM calls so it is cheap to re-run after each
new crawl. LLM-based hook semantics + comment-demand clustering land in W2.
"""
from __future__ import annotations

import json
import math
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .. import config, db
from . import hooks

# Beijing time (xhs users are mostly CN-based). Heatmap bucketing uses this TZ.
_TZ = timezone(timedelta(hours=8))


def _bucket_likes(liked: int) -> str:
    if liked is None:
        return "unknown"
    if liked >= 100_000:
        return "100K+ (capped)"
    if liked >= 10_000:
        return "10K-100K"
    if liked >= 1_000:
        return "1K-10K"
    if liked >= 100:
        return "100-1K"
    return "<100"


def _length_bucket(n: int) -> str:
    if n < 10:
        return "<10"
    if n < 15:
        return "10-14"
    if n < 20:
        return "15-19"
    if n < 25:
        return "20-24"
    if n < 30:
        return "25-29"
    return "30+"


def _body_bucket(n: int) -> str:
    if n < 100:
        return "<100"
    if n < 300:
        return "100-300"
    if n < 600:
        return "300-600"
    if n < 1000:
        return "600-1000"
    if n < 2000:
        return "1000-2000"
    return "2000+"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def _summary_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    s = sorted(values)
    return {
        "n": len(s),
        "mean": sum(s) / len(s),
        "median": _percentile(s, 0.5),
        "p25": _percentile(s, 0.25),
        "p75": _percentile(s, 0.75),
        "p90": _percentile(s, 0.90),
        "p99": _percentile(s, 0.99),
        "max": s[-1],
        "min": s[0],
    }


# --- 1. Title hook analysis ----------------------------------------------

def analyse_titles() -> dict[str, Any]:
    notes = db.fetch_notes_titles_only()
    if not notes:
        return {"note_count": 0}

    results = []
    by_cat_likes: dict[str, list[int]] = defaultdict(list)
    by_cat_examples: dict[str, list[dict]] = defaultdict(list)
    matched_multi: list[tuple[tuple[str, ...], int]] = []
    by_len_likes: dict[str, list[int]] = defaultdict(list)

    for n in notes:
        title = n["title"] or ""
        h = hooks.classify(title)
        results.append(h)
        likes = n["liked_count"] or 0
        by_cat_likes[h.primary].append(likes)
        by_len_likes[_length_bucket(len(title))].append(likes)
        matched_multi.append((h.matched, likes))
        bucket = by_cat_examples[h.primary]
        # Maintain top-5 by likes inline.
        bucket.append(
            {
                "note_id": n["note_id"],
                "title": title,
                "liked": likes,
                "matched": list(h.matched),
            }
        )

    # Trim each category's examples to top 5.
    for cat, lst in by_cat_examples.items():
        lst.sort(key=lambda r: r["liked"], reverse=True)
        del lst[5:]

    cat_stats = {}
    for cat, likes in by_cat_likes.items():
        cat_stats[cat] = {
            "count": len(likes),
            "share": len(likes) / len(results),
            "likes": _summary_stats([float(x) for x in likes]),
            "examples": by_cat_examples[cat],
        }

    length_stats = {
        bucket: {
            "count": len(v),
            "likes": _summary_stats([float(x) for x in v]),
        }
        for bucket, v in by_len_likes.items()
    }

    # Co-occurrence among matched (not primary) categories.
    co = hooks.co_occurrence(results)

    # Top 50 titles overall — what a human should read first.
    top = sorted(
        ((n, h) for n, h in zip(notes, results)),
        key=lambda nh: nh[0]["liked_count"] or 0,
        reverse=True,
    )[:50]
    top_titles = [
        {
            "note_id": n["note_id"],
            "title": n["title"],
            "liked": n["liked_count"],
            "collected": n["collected_count"],
            "commented": n["comment_count"],
            "hook_primary": h.primary,
            "hook_matched": list(h.matched),
            "char_count": h.char_count,
            "emoji_count": h.emoji_count,
        }
        for n, h in top
    ]

    return {
        "note_count": len(results),
        "primary_distribution": dict(Counter(r.primary for r in results)),
        "by_category": cat_stats,
        "by_title_length": length_stats,
        "co_occurrence": co,
        "top_titles": top_titles,
    }


# --- 2. Body length / image count / shape vs engagement ------------------

def analyse_body_and_shape() -> dict[str, Any]:
    rows = db.fetch_notes_for_analysis(min_body_len=0)
    out_body: dict[str, list[int]] = defaultdict(list)
    out_imgs: dict[int, list[int]] = defaultdict(list)
    has_video_likes: list[int] = []
    no_video_likes: list[int] = []

    for r in rows:
        likes = r["liked_count"] or 0
        body_len = len(r["body"] or "")
        out_body[_body_bucket(body_len)].append(likes)
        ic = r["image_count"] or 0
        # Cap bucket at 9+ for sanity.
        out_imgs[min(ic, 10)].append(likes)
        if r["type"] == "video" or (r["video_duration_ms"] or 0) > 0:
            has_video_likes.append(likes)
        else:
            no_video_likes.append(likes)

    return {
        "by_body_length": {
            b: {"count": len(v), "likes": _summary_stats([float(x) for x in v])}
            for b, v in out_body.items()
        },
        "by_image_count": {
            str(k): {"count": len(v), "likes": _summary_stats([float(x) for x in v])}
            for k, v in sorted(out_imgs.items())
        },
        "video_vs_image": {
            "video": _summary_stats([float(x) for x in has_video_likes]),
            "image": _summary_stats([float(x) for x in no_video_likes]),
        },
    }


# --- 3. Publish-time heatmap (hour-of-day × day-of-week) ------------------

def analyse_timing() -> dict[str, Any]:
    rows = db.fetch_notes_titles_only()
    # heatmap[dow][hour] = list of likes
    heat: dict[int, dict[int, list[int]]] = {d: {h: [] for h in range(24)} for d in range(7)}
    monthly: dict[str, list[int]] = defaultdict(list)

    for r in rows:
        ms = r["publish_time_ms"]
        if not ms:
            continue
        try:
            dt = datetime.fromtimestamp(ms / 1000, tz=_TZ)
        except (OSError, ValueError):
            continue
        likes = r["liked_count"] or 0
        heat[dt.weekday()][dt.hour].append(likes)
        monthly[dt.strftime("%Y-%m")].append(likes)

    flat = []
    for d in range(7):
        for h in range(24):
            v = heat[d][h]
            flat.append(
                {
                    "dow": d,
                    "hour": h,
                    "count": len(v),
                    "mean_likes": (sum(v) / len(v)) if v else 0.0,
                    "median_likes": _percentile([float(x) for x in v], 0.5),
                }
            )

    return {
        "heatmap": flat,
        "monthly": {
            m: {"count": len(v), "likes": _summary_stats([float(x) for x in v])}
            for m, v in sorted(monthly.items())
        },
    }


# --- 4. Tag co-occurrence + frequency ------------------------------------

def analyse_tags() -> dict[str, Any]:
    rows = db.fetch_notes_for_analysis(min_body_len=0)
    freq: Counter[str] = Counter()
    likes_by_tag: dict[str, list[int]] = defaultdict(list)
    pair_freq: Counter[tuple[str, str]] = Counter()

    for r in rows:
        tags_raw = r.get("tags") or []
        # tags_json shapes vary; normalise to list of strings.
        tags: list[str] = []
        for t in tags_raw:
            if isinstance(t, dict):
                name = t.get("name") or t.get("title") or t.get("tag_name")
                if name:
                    tags.append(str(name))
            elif isinstance(t, str):
                tags.append(t)
        tags = [re.sub(r"^#", "", t).strip() for t in tags if t]
        tags = [t for t in tags if t]
        if not tags:
            continue
        unique = list(dict.fromkeys(tags))
        likes = r["liked_count"] or 0
        for t in unique:
            freq[t] += 1
            likes_by_tag[t].append(likes)
        for i, a in enumerate(unique):
            for b in unique[i + 1 :]:
                key = tuple(sorted((a, b)))
                pair_freq[key] += 1

    top_tags = []
    for tag, c in freq.most_common(100):
        ls = likes_by_tag[tag]
        top_tags.append(
            {
                "tag": tag,
                "count": c,
                "avg_likes": sum(ls) / len(ls) if ls else 0,
                "median_likes": _percentile([float(x) for x in ls], 0.5),
            }
        )
    top_pairs = [
        {"a": a, "b": b, "count": c}
        for (a, b), c in pair_freq.most_common(80)
    ]
    return {"top_tags": top_tags, "top_pairs": top_pairs}


# --- 5. Keyword coverage & 蓝海 score -------------------------------------

def analyse_keyword_blueocean() -> dict[str, Any]:
    """Map source keywords to crawled notes; rank by 'blue-ocean' score.

    Blue-ocean = high *average* likes per note in that keyword bucket while the
    keyword itself has relatively *few* notes (low saturation). Score:
        avg_likes / log2(note_count + 2)
    Higher means: under-supplied niche with high payoff per post.
    """
    cov = db.fetch_keyword_coverage()
    # Join keyword → notes by re-scanning discover_queue x notes.
    with db.connect(read_only=True) as con:
        kw_to_notes: dict[str, list[int]] = defaultdict(list)
        cur = con.execute(
            "SELECT dq.source_value AS kw, n.liked_count AS likes"
            " FROM discover_queue dq"
            " JOIN notes n ON n.note_id = dq.note_id"
            " WHERE dq.source_value IS NOT NULL"
            " AND dq.source_type IN ('search','topic')"
            " AND n.title IS NOT NULL"
        )
        for row in cur:
            kw_to_notes[row["kw"]].append(row["likes"] or 0)

    out = []
    for kw, likes_list in kw_to_notes.items():
        n = len(likes_list)
        if n < 5:
            continue
        avg = sum(likes_list) / n
        med = _percentile([float(x) for x in likes_list], 0.5)
        score = avg / math.log2(n + 2)
        out.append(
            {
                "keyword": kw,
                "note_count": n,
                "avg_likes": avg,
                "median_likes": med,
                "p90_likes": _percentile([float(x) for x in likes_list], 0.9),
                "blue_ocean_score": score,
            }
        )
    out.sort(key=lambda r: r["blue_ocean_score"], reverse=True)
    return {"rankings": out}


# --- 6. Comment demand mining (statistical baseline) ---------------------

_DEMAND_PATTERNS = [
    ("求", re.compile(r"求(.{1,8})")),
    ("怎么", re.compile(r"怎么(.{1,10})")),
    ("怎样", re.compile(r"怎样(.{1,10})")),
    ("如何", re.compile(r"如何(.{1,10})")),
    ("有没有", re.compile(r"有没有(.{1,10})")),
    ("哪里", re.compile(r"哪里(.{1,10})")),
    ("能不能", re.compile(r"能不能(.{1,10})")),
    ("可以", re.compile(r"可以(.{1,10})吗")),
]

def analyse_comment_demand() -> dict[str, Any]:
    comments = db.fetch_comments()
    counters: dict[str, Counter[str]] = {k: Counter() for k, _ in _DEMAND_PATTERNS}
    total = 0
    for c in comments:
        text = (c["content"] or "").strip()
        if not text:
            continue
        total += 1
        for label, pat in _DEMAND_PATTERNS:
            for m in pat.finditer(text):
                snippet = m.group(0)[:20]
                counters[label][snippet] += 1

    out = {
        "total_comments": total,
        "by_pattern": {
            label: [{"phrase": p, "count": c} for p, c in cnt.most_common(40)]
            for label, cnt in counters.items()
        },
    }
    return out


# --- 7. Top performers -------------------------------------------------

def analyse_top_performers(n: int = 50) -> dict[str, Any]:
    with db.connect(read_only=True) as con:
        top_likes = [dict(r) for r in con.execute(
            "SELECT note_id, title, author_nickname, liked_count, collected_count,"
            " comment_count, image_count, type, LENGTH(body) AS body_len, url"
            " FROM notes WHERE title IS NOT NULL ORDER BY liked_count DESC LIMIT ?",
            (n,),
        )]
        top_collects = [dict(r) for r in con.execute(
            "SELECT note_id, title, author_nickname, liked_count, collected_count,"
            " comment_count, image_count, type, LENGTH(body) AS body_len, url"
            " FROM notes WHERE title IS NOT NULL ORDER BY collected_count DESC LIMIT ?",
            (n,),
        )]
        top_comments = [dict(r) for r in con.execute(
            "SELECT note_id, title, author_nickname, liked_count, collected_count,"
            " comment_count, image_count, type, LENGTH(body) AS body_len, url"
            " FROM notes WHERE title IS NOT NULL ORDER BY comment_count DESC LIMIT ?",
            (n,),
        )]
        top_collect_rate = [dict(r) for r in con.execute(
            "SELECT note_id, title, author_nickname, liked_count, collected_count,"
            " (CAST(collected_count AS REAL) / NULLIF(liked_count, 0)) AS collect_rate"
            " FROM notes WHERE liked_count >= 1000 AND title IS NOT NULL"
            " ORDER BY collect_rate DESC LIMIT ?",
            (n,),
        )]
    return {
        "top_likes": top_likes,
        "top_collects": top_collects,
        "top_comments": top_comments,
        "top_collect_rate": top_collect_rate,
    }


# --- main orchestrator ---------------------------------------------------

def build_dna(version: str | None = None) -> dict[str, Any]:
    """Run all sections, return the assembled artifact dict.

    Each section is wrapped in its own try/except so a missing optional table
    (e.g. `discover_queue` in a small exported corpus) doesn't kill the whole
    DNA build. Failed sections emit `{"_error": "..."}` so the frontend can
    show what was skipped without losing the rest of the analysis.
    """
    t0 = time.time()
    if version is None:
        version = datetime.now(_TZ).strftime("%Y-%m-%d")

    artifact: dict[str, Any] = {
        "version": version,
        "generated_at": int(time.time()),
        "sections": {},
        "section_errors": {},
    }

    sections: list[tuple[str, callable]] = [
        ("titles",            analyse_titles),
        ("body_and_shape",    analyse_body_and_shape),
        ("timing",            analyse_timing),
        ("tags",              analyse_tags),
        ("keyword_blueocean", analyse_keyword_blueocean),
        ("comment_demand",    analyse_comment_demand),
        ("top_performers",    analyse_top_performers),
    ]
    for name, fn in sections:
        try:
            artifact["sections"][name] = fn()
        except Exception as e:
            artifact["sections"][name] = {"_error": str(e)}
            artifact["section_errors"][name] = repr(e)

    # Headline summary numbers — be tolerant of a missing/empty titles section.
    titles_sec = artifact["sections"].get("titles") or {}
    primary = titles_sec.get("primary_distribution") or {}
    top_cats = sorted(primary.items(), key=lambda kv: kv[1], reverse=True)[:3]
    artifact["summary"] = {
        "total_notes_analysed": titles_sec.get("note_count", 0),
        "dominant_hooks": [{"category": c, "count": n} for c, n in top_cats],
        "generated_in_seconds": round(time.time() - t0, 2),
        "section_errors": list(artifact["section_errors"].keys()),
    }
    return artifact


def attach_raw_schema(artifact: dict[str, Any]) -> dict[str, Any]:
    """Best-effort: include the library's raw table/column structure as a
    fallback context for Insight when the canonical analysis is sparse.

    This makes the system genuinely 'just accept any SQLite' — even if DNA
    sections all fail, the Insight LLMs have *something* (raw schema +
    sample rows) to reason over.
    """
    try:
        from .. import adapt as _adapt, library as _library
        db_path = _library.current_db_path()
        if db_path.exists():
            artifact["raw_schema"] = _adapt.inspect_source(db_path, sample_rows=2)
    except Exception as e:
        artifact["raw_schema_error"] = str(e)
    return artifact


def persist(artifact: dict[str, Any]) -> Path:
    """Write the JSON artifact to disk *and* upsert into studio_dna_artifacts.

    Always persists — even if every section failed. The insight pipeline
    needs *something* to work with, and an empty/sparse artifact is still
    better than a missing one.
    """
    attach_raw_schema(artifact)
    version = artifact["version"]
    fp = config.ANALYSIS_DIR / f"v{version}.json"
    fp.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = json.dumps(artifact, ensure_ascii=False)
    summary = json.dumps(artifact.get("summary", {}), ensure_ascii=False)
    db.insert_dna_artifact(version=version, payload_json=payload, summary=summary)
    return fp
