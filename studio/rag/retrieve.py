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

All public search calls degrade gracefully when their tables are missing
(xlsx-imported libs may lack FTS until auto_build_fts runs). Each branch
returns its data type's empty value rather than raising, so the StrategyRefsPanel
can render partial results instead of "加载参考素材失败".
"""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from typing import Any

from .. import benchmarks, config, db

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
        # v0.65 ：偏好顺序反过来 ─ 之前先挑 WB_PRV preview URL ，但 xhs 的 preview
        # 经常是 http:// 协议 ，被 https 页面 mixed content 拦掉。urlDefault 一般
        # https + 全尺寸 ，更稳。还是没有就回退 WB_PRV / WB_DFT。
        chosen = str(img.get("urlDefault") or img.get("url") or "")
        if not chosen:
            for info in (img.get("infoList") or []):
                if isinstance(info, dict) and info.get("imageScene") in ("WB_DFT", "WB_PRV"):
                    chosen = str(info.get("url") or "")
                    if chosen:
                        break
        if not chosen:
            continue
        # v0.65 ：强制 https ─ 否则 mixed content 时浏览器静默拦截，
        # 用户只看到占位渐变 + 空白卡片。
        if chosen.startswith("http://"):
            chosen = "https://" + chosen[len("http://"):]
        if chosen.startswith("https://"):
            urls.append(chosen)
        if len(urls) >= 4:
            break
    return urls


def search_notes(
    topic: str,
    k: int = 8,
    candidate_pool: int = 200,
    # v0.65 ：boosted from 0.5 → 1.5。原值导致一篇 6 赞但高相关的笔记打败一篇
    # 10000 赞中等相关的笔记 — 用户看到 「AI 参考的真实素材」 全是 1-6 赞的小帖 ，
    # 一脸黑盒。1.5 让 10000 赞的引擎力压相关度 +0.45，明显倾向 「爆款 + 还算相关」。
    likes_weight: float = 1.5,
    include_images: bool = True,
    # v0.65 ：相对池中位数的最低互动闸门。pool 里取中位赞数 ，把低于 median/2 + 绝对
    # 阈值 10 的笔记筛掉（避免「6 赞」「3 赞」当 ref）。pool 不足时自动放宽 ─
    # 小库 / 冷门主题 不会因此空结果。
    min_likes_floor_pct: float = 0.5,
    min_likes_abs: int = 10,
) -> list[dict[str, Any]]:
    """Return up to `k` notes ranked by FTS relevance × engagement.

    When include_images is True (default), also extracts up to 4 image URLs
    per note from the crawler's raw_json payload so the frontend can show
    a thumbnail strip. Adds an extra column to the SELECT — small cost for
    the visual ROI of "AI 参考了这些真实的图文帖" in DraftDetail.
    """
    fts_q = _fts_query(topic)
    with db.connect(read_only=True) as con:
        # Feature-detect columns: older crawler DBs lack video_duration_ms /
        # share_count / video_url, and xlsx-imported libs lack raw_json.
        try:
            cols = {r["name"] for r in con.execute("PRAGMA table_info(notes)")}
        except sqlite3.OperationalError:
            cols = set()
        if not cols:
            return []
        extra_cols_wanted = ["video_duration_ms", "share_count", "video_url"]
        if include_images and "raw_json" in cols:
            extra_cols_wanted.append("raw_json")
        extra_cols = [c for c in extra_cols_wanted if c in cols]
        extra_sql = (", " + ", ".join(f"n.{c}" for c in extra_cols)) if extra_cols else ""
        extra_sql_no_prefix = (", " + ", ".join(extra_cols)) if extra_cols else ""
        # Try FTS first and catch OperationalError on missing table / bad
        # MATCH — saves a probe query per call. Falls through to LIKE on
        # short queries, no FTS, or 0 hits.
        rows: list[dict[str, Any]] = []
        if fts_q:
            try:
                cur = con.execute(
                    "SELECT n.note_id, n.title, n.body, n.liked_count,"
                    "       n.collected_count, n.comment_count, n.image_count,"
                    "       n.tags_json, n.author_id, n.author_nickname, n.url"
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
                _log.warning("FTS unavailable for %r: %s — falling back to LIKE", topic, exc)
        if not rows:
            # Topic too short for trigram, no FTS table, or FTS returned 0:
            # fall back to LIKE on title.
            like = f"%{topic}%"
            try:
                cur = con.execute(
                    "SELECT note_id, title, body, liked_count, collected_count,"
                    "       comment_count, image_count, tags_json, author_id,"
                    "       author_nickname, url"
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
    # v0.64 ：对标账号 boost — 一次性拿 active id 集合（表不在就空集，下面
    # 的 in 运算自动退化为全部 False，零开销）。bonus 加在 hybrid_score 上，
    # 0.5 量级 = 比"engagement 满分"还重一点，但相关度趋零的帖子仍排不上来。
    bench_ids = benchmarks.get_active_ids()
    benchmark_bonus = 0.5
    bms = [r["bm"] for r in rows]
    bm_min, bm_max = min(bms), max(bms)
    bm_span = max(1e-6, bm_max - bm_min)
    for r in rows:
        # bm25 is negative; lower = better. Normalise to [0,1] where 1 = best.
        relevance = (bm_max - r["bm"]) / bm_span if bm_max != bm_min else 1.0
        engagement = math.log10((r["liked_count"] or 0) + 1) / 6.0  # 100K → ~0.83
        is_bench = bool(r.get("author_id") and r["author_id"] in bench_ids)
        r["is_benchmark"] = is_bench
        r["hybrid_score"] = relevance + likes_weight * engagement + (
            benchmark_bonus if is_bench else 0.0
        )

    # v0.65 ：池内相对互动闸门 ─ 过滤掉「相关但根本不是爆款」的低赞 ref ，
    # 避免「AI 参考的真实素材」面板里出现 1/3/6 赞这种明显不是爆款的笔记。
    # 1) 算池中位赞 ；2) floor = max(median * 0.5 , 10) ；3) 若过滤后还有
    #    >= max(k, 8) 篇就用过滤结果 ，否则放弃过滤（避免小库/冷门主题清空）。
    pool_likes = sorted([(r.get("liked_count") or 0) for r in rows])
    pool_median = pool_likes[len(pool_likes) // 2] if pool_likes else 0
    floor = max(int(pool_median * min_likes_floor_pct), min_likes_abs)
    survivors = [r for r in rows if (r.get("liked_count") or 0) >= floor or r["is_benchmark"]]
    use_filtered = len(survivors) >= max(k, 8)
    ranked = (survivors if use_filtered else rows)
    ranked.sort(key=lambda r: r["hybrid_score"], reverse=True)
    # 把决策信息塞进每条 ref（前端可调试 / 渲染）：哪条是经过 floor 过滤的、池中位多少。
    final = ranked[:k]
    for r in final:
        r["pool_median_likes"] = pool_median
        r["likes_floor_applied"] = floor if use_filtered else 0
    return final


def search_comments(topic: str, n: int = 15) -> list[dict[str, Any]]:
    """v0.65.3 ：之前只走 FTS，2 字中文（"论文 / 教程 / 写作 / 标题"）被 `_split_topic`
    扔掉 → fts_q 为空 → 返回 []。这是用户在出稿页看到「0 条用户原话」的根因。

    新策略 ：
      1) 先跑 FTS（如果 fts_q 非空）
      2) FTS 返回 < n / 2 → 追加 LIKE 模糊匹配补齐（每个 2-3 char piece 在 content 里 LIKE）
      3) 按 like_count 降序排
    """
    fts_q = _fts_query(topic)
    fts_rows: list[dict[str, Any]] = []
    like_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    if fts_q:
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
                fts_rows = [dict(r) for r in cur]
                seen_ids.update(r["comment_id"] for r in fts_rows if r.get("comment_id"))
        except sqlite3.OperationalError as exc:
            _log.warning("search_comments FTS unavailable: %s", exc)

    # LIKE fallback for short / no-token topics OR when FTS underperforms。
    # 把 topic 切成 2+ 字 pieces ，每个跟 content LIKE 一下。短 topic 也可用（如 "论文"）。
    if len(fts_rows) < n // 2:
        pieces = [
            _FTS_SCRUB.sub("", p)
            for p in _NON_TOKEN.split(topic)
            if p and len(p.strip()) >= 2
        ]
        pieces = [p for p in pieces if len(p) >= 2][:6]   # cap to keep WHERE manageable
        if pieces:
            try:
                with db.connect(read_only=True) as con:
                    or_clauses = " OR ".join("content LIKE ?" for _ in pieces)
                    sql = (
                        f"SELECT comment_id, note_id, content, like_count, 0 AS bm"
                        f" FROM comments WHERE ({or_clauses})"
                        f" AND content IS NOT NULL AND content != ''"
                        f" ORDER BY like_count DESC LIMIT ?"
                    )
                    args = [f"%{p}%" for p in pieces] + [n * 3]
                    cur = con.execute(sql, args)
                    for r in cur:
                        d = dict(r)
                        cid = d.get("comment_id")
                        if cid and cid not in seen_ids:
                            like_rows.append(d)
                            seen_ids.add(cid)
            except sqlite3.OperationalError as exc:
                _log.warning("search_comments LIKE fallback failed: %s", exc)

    rows = fts_rows + like_rows
    if not rows:
        return []
    # Prefer comments with higher likes（FTS 已有相关度 ，再按互动 fine-tune）。
    rows.sort(key=lambda r: (r.get("like_count") or 0), reverse=True)
    rows = rows[:n]

    # v0.65.3 ：用户在出稿页要看「参考的原话 + 原贴链接 + 原贴数据」 ─ 给每条
    # comment 补一个 source_note 子对象 ，包含来源 note 的 title / url / 互动数。
    # 一次性 IN 查询补齐 ，避免 N+1。
    note_ids = sorted({r.get("note_id") for r in rows if r.get("note_id")})
    note_map: dict[str, dict[str, Any]] = {}
    if note_ids:
        try:
            with db.connect(read_only=True) as con:
                # feature-detect columns the way search_notes does
                cols = {c["name"] for c in con.execute("PRAGMA table_info(notes)")}
                wanted_extra = ["share_count", "video_duration_ms", "raw_json"]
                extras = [c for c in wanted_extra if c in cols]
                extras_sql = (", " + ", ".join(extras)) if extras else ""
                placeholders = ",".join("?" for _ in note_ids)
                cur = con.execute(
                    f"SELECT note_id, title, url, liked_count, collected_count,"
                    f"       comment_count, author_nickname, image_count{extras_sql}"
                    f" FROM notes WHERE note_id IN ({placeholders})",
                    note_ids,
                )
                for r in cur:
                    d = dict(r)
                    d["image_urls"] = _extract_image_urls_from_raw(
                        d.get("raw_json") or "", d.get("note_id") or "",
                    ) if "raw_json" in d else []
                    d.pop("raw_json", None)
                    d["duration_sec"] = int((d.pop("video_duration_ms", 0) or 0) / 1000)
                    note_map[d["note_id"]] = d
        except sqlite3.OperationalError as exc:
            _log.warning("search_comments source-note enrich failed: %s", exc)

    for r in rows:
        src = note_map.get(r.get("note_id"))
        if src:
            r["source_note"] = {
                "note_id": src.get("note_id"),
                "title": src.get("title") or "",
                "url": src.get("url") or "",
                "liked_count": src.get("liked_count") or 0,
                "collected_count": src.get("collected_count") or 0,
                "comment_count": src.get("comment_count") or 0,
                "share_count": src.get("share_count") or 0,
                "author_nickname": src.get("author_nickname") or "",
                "duration_sec": src.get("duration_sec") or 0,
                "image_count": src.get("image_count") or 0,
                "cover_image": (src.get("image_urls") or [None])[0],
            }
    return rows


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
    """One branch failing (missing table, malformed query) must not poison
    the others — the panel can still render the data that did come back."""
    refs: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    hooks: list[dict[str, Any]] = []
    try:
        refs = search_notes(topic, k=k_notes)
    except Exception as exc:  # noqa: BLE001
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
