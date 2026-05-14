"""URL-paste → reingest pipeline.

Closes the loop on retrospective: instead of asking the user to manually paste
likes/saves/comments into the PerformanceWidget, they paste the published URL
and we auto-fetch fresh metrics from xhs's SSR HTML.

Public API:
    parse_note_id(url) -> str | None
        Extract the note_id from an xhs URL (multiple formats supported).
    fetch_metrics(url) -> dict
        Fetch latest counts. Returns {status, likes, saves, comments,
        shares, raw_summary, note_id}. Status='ok' if all four counts
        succeeded, else an error code the UI can show ('rate_limited' /
        'login_wall' / 'no_crawler' / 'no_ssr' / 'parse_err').
    refresh_draft(draft_id, *, force_url=None) -> dict
        End-to-end: read draft.published_url, call fetch_metrics, write a
        studio_url_fetches row + studio_draft_performance row, return summary.

Crawler dependency:
    Primary path uses curl_cffi (Chrome TLS impersonation) — required because
    xhs sniffs JA3/JA4 fingerprint. If curl_cffi is not installed we degrade
    gracefully: return status='no_crawler' with a hint to install it. The
    user can still manually log metrics in PerformanceWidget.
"""
from .xhs_fetch import (
    parse_note_id,
    fetch_metrics,
    refresh_draft,
    list_fetches,
    crawler_available,
)

__all__ = [
    "parse_note_id", "fetch_metrics", "refresh_draft", "list_fetches",
    "crawler_available",
]
