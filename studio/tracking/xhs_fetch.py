"""Note-detail fetcher — parse xhs SSR HTML, extract interactInfo.

Mirrors `H:\\xhs\\crawler\\httpx_detail.py` (upstream) but operates only on
*our own* drafts' published URLs, so we don't need the discover_queue plumbing
or image downloads — just the four counts.

Why curl_cffi:
    xhs serves the full note JSON in `window.__INITIAL_STATE__`, but plain
    httpx/requests get a 302 redirect to login because xhs sniffs TLS JA3/JA4
    and rejects non-browser clients. curl_cffi's `impersonate='chrome131'`
    presents a real-Chrome handshake.

Graceful degradation:
    If curl_cffi is not installed, all fetches return status='no_crawler' with
    instructions. The UI then falls back to manual entry. We deliberately do
    NOT raise — tracking should be optional convenience, not a hard dep.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any
from urllib.parse import urlparse, parse_qs

from .. import db, project
from .. import retrospective as retro


# Try to import curl_cffi at module load. If absent, _CRAWLER_OK stays False.
try:
    from curl_cffi import requests as cffi_requests  # type: ignore
    _CRAWLER_OK = True
except Exception:  # pragma: no cover — environment-dependent
    cffi_requests = None  # type: ignore
    _CRAWLER_OK = False


_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", re.DOTALL
)
_UNDEFINED_RE = re.compile(r":\s*undefined([,}\]])")
_NOTE_ID_RE = re.compile(r"/explore/([0-9a-fA-F]{16,32})")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
IMPERSONATE = "chrome131"
XHS_HOST = "https://www.xiaohongshu.com"


def crawler_available() -> bool:
    return _CRAWLER_OK


def parse_note_id(url: str) -> str | None:
    """Pull note_id out of an xhs URL. Accepts:
        https://www.xiaohongshu.com/explore/{note_id}?...
        https://xhslink.com/...   → returns None (short links would need a
                                    HEAD request to resolve; skip for v1)
        bare note_id strings      → return as-is if 16-32 hex.
    """
    if not url:
        return None
    url = url.strip()
    # bare id?
    if re.fullmatch(r"[0-9a-fA-F]{16,32}", url):
        return url
    m = _NOTE_ID_RE.search(url)
    if m:
        return m.group(1)
    return None


def _extract_state(html: str) -> dict | None:
    m = _STATE_RE.search(html)
    if not m:
        return None
    js = _UNDEFINED_RE.sub(r":null\1", m.group(1))
    try:
        return json.loads(js)
    except Exception:
        return None


def _coerce_count(v: Any) -> int | None:
    """xhs interactInfo counts come as either int or '1.2万' style strings."""
    if v is None or v == "":
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        v = v.strip()
        try:
            return int(v)
        except ValueError:
            pass
        # '1.2万' / '23万' → 12000 / 230000
        m = re.match(r"^(\d+(?:\.\d+)?)\s*万$", v)
        if m:
            return int(float(m.group(1)) * 10000)
        m = re.match(r"^(\d+(?:\.\d+)?)\s*[kK]$", v)
        if m:
            return int(float(m.group(1)) * 1000)
        # bare digits w/ commas
        cleaned = v.replace(",", "")
        if cleaned.isdigit():
            return int(cleaned)
    return None


def _interact_from_state(state: dict, note_id: str) -> dict[str, Any] | None:
    """Drill into the __INITIAL_STATE__ shape — multiple keys depending on
    where the page was loaded from. Returns interactInfo dict or None.
    """
    # path 1: state.note.noteDetailMap[note_id].note.interactInfo
    try:
        ndm = state["note"]["noteDetailMap"]
        if note_id in ndm:
            return ndm[note_id]["note"].get("interactInfo")
    except Exception:
        pass
    # path 2: state.note.firstNoteId + state.note.noteDetailMap
    try:
        ndm = state["note"]["noteDetailMap"]
        first = state["note"].get("firstNoteId")
        if first and first in ndm:
            return ndm[first]["note"].get("interactInfo")
    except Exception:
        pass
    # path 3: legacy single-note shape
    try:
        return state["noteData"]["data"]["noteData"]["interactInfo"]
    except Exception:
        pass
    return None


def fetch_metrics(url: str, *, xsec_token: str | None = None) -> dict[str, Any]:
    """Fetch current likes/saves/comments/shares from xhs.

    Returns dict with at minimum {status, note_id, raw_summary}. On status='ok'
    also includes likes/saves/comments/shares. Status codes:
        'ok'           : everything parsed
        'no_crawler'   : curl_cffi not installed — fallback path
        'no_url'       : empty/invalid URL
        'no_note_id'   : couldn't extract note_id
        'http_err'     : network / TLS / non-200
        'rate_limited' : 302 redirect or login wall
        'no_ssr'       : 200 OK but no __INITIAL_STATE__ in HTML
        'parse_err'    : SSR present but interactInfo missing
    """
    note_id = parse_note_id(url)
    if not url:
        return {"status": "no_url", "note_id": None,
                "raw_summary": "URL 为空"}
    if not note_id:
        return {"status": "no_note_id", "note_id": None,
                "raw_summary": f"无法从 URL 中解析 note_id: {url[:80]}"}
    if not _CRAWLER_OK:
        return {
            "status": "no_crawler", "note_id": note_id,
            "raw_summary": "curl_cffi 未安装。pip install curl_cffi 后即可一键刷新。"
                           "现在请手动在下方录入数据。",
        }

    # Build the URL. If caller didn't pass xsec_token, try a bare /explore/{id}
    # — works for public posts.
    fetch_url = url
    if "://" not in fetch_url:
        fetch_url = f"{XHS_HOST}/explore/{note_id}"

    try:
        r = cffi_requests.get(
            fetch_url,
            impersonate=IMPERSONATE,
            timeout=25,
            allow_redirects=False,
            headers={
                "User-Agent": UA,
                "Referer": XHS_HOST + "/",
            },
        )
    except Exception as e:
        return {"status": "http_err", "note_id": note_id,
                "raw_summary": f"网络错误: {type(e).__name__}: {e}"}

    if r.status_code in (301, 302):
        loc = r.headers.get("Location", "")
        if "/login" in loc or "error_code" in loc:
            return {"status": "rate_limited", "note_id": note_id,
                    "raw_summary": f"被风控：302 → {loc[:80]}"}
        return {"status": "http_err", "note_id": note_id,
                "raw_summary": f"302 redirect: {loc[:80]}"}
    if r.status_code != 200:
        return {"status": "http_err", "note_id": note_id,
                "raw_summary": f"HTTP {r.status_code}"}
    html = r.text or ""
    if "/login" in html[:500] and "__INITIAL_STATE__" not in html:
        return {"status": "rate_limited", "note_id": note_id,
                "raw_summary": "页面被引导到登录墙"}
    state = _extract_state(html)
    if not state:
        return {"status": "no_ssr", "note_id": note_id,
                "raw_summary": "页面没有 __INITIAL_STATE__"}
    interact = _interact_from_state(state, note_id)
    if not interact:
        return {"status": "parse_err", "note_id": note_id,
                "raw_summary": "SSR 中没有 interactInfo 字段（可能笔记已删/不可见）"}

    likes    = _coerce_count(interact.get("likedCount"))
    saves    = _coerce_count(interact.get("collectedCount"))
    comments = _coerce_count(interact.get("commentCount"))
    shares   = _coerce_count(interact.get("shareCount"))

    return {
        "status": "ok", "note_id": note_id,
        "likes": likes, "saves": saves, "comments": comments, "shares": shares,
        "raw_summary": f"likes={likes} saves={saves} comments={comments} shares={shares}",
    }


def refresh_draft(draft_id: str, *, force_url: str | None = None) -> dict[str, Any]:
    """End-to-end: fetch + persist a perf row + persist a fetch log.

    If force_url is given, use it instead of draft.published_url. Useful when
    the user pastes a freshly published URL into a draft that wasn't yet
    marked-published.

    Returns the fetch result dict + 'fetch_id' + 'perf_id' (if ok).
    """
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()

    # Load the URL we should fetch.
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT published_url FROM studio_drafts WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
    if not row:
        raise LookupError(f"draft not found: {draft_id}")
    url = force_url or row["published_url"]
    if not url:
        return {"status": "no_url", "draft_id": draft_id,
                "raw_summary": "草稿还没标记为已发布，先填 published_url"}

    result = fetch_metrics(url)
    fetch_id = "fch_" + uuid.uuid4().hex[:12]
    now = int(time.time())

    perf_id: str | None = None
    if result["status"] == "ok":
        # Write a normal perf row (so it shows up in PerformanceWidget history).
        try:
            p = retro.record_performance(
                draft_id,
                likes=result.get("likes"),
                comments=result.get("comments"),
                saves=result.get("saves"),
                shares=result.get("shares"),
                notes=f"auto-fetched: {result['raw_summary']}",
            )
            perf_id = p.get("perf_id")
        except Exception as e:  # noqa: BLE001
            result["status"] = "parse_err"
            result["raw_summary"] += f" | record_performance failed: {e!r}"

    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_url_fetches"
            " (fetch_id, draft_id, url, note_id, fetched_at, status,"
            "  likes, saves, comments, shares, raw_summary, perf_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fetch_id, draft_id, url, result.get("note_id"), now,
                result["status"], result.get("likes"), result.get("saves"),
                result.get("comments"), result.get("shares"),
                result.get("raw_summary"), perf_id,
            ),
        )

    return {
        "fetch_id": fetch_id, "perf_id": perf_id, "draft_id": draft_id,
        "fetched_at": now, **result,
    }


def list_fetches(draft_id: str, limit: int = 20) -> list[dict[str, Any]]:
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            "SELECT fetch_id, url, note_id, fetched_at, status,"
            " likes, saves, comments, shares, raw_summary, perf_id"
            " FROM studio_url_fetches"
            " WHERE draft_id = ? ORDER BY fetched_at DESC LIMIT ?",
            (draft_id, limit),
        ))
    return [dict(r) for r in rows]
