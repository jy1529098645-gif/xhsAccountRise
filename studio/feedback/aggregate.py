"""Aggregate per-draft performance up to pack-level — so iterate.py can use
draft data even when the pack-level performance row was never explicitly saved.

Why both feeds exist:
    - studio_strategy_performance (pack-level, manual paste from operator)
    - studio_draft_performance    (per-draft, recorded when each post's
                                    metrics come back via UI or tracking/)
Historically these were independent. Now retrospective.py and iterate.py both
want a "what happened in this cycle?" view; we provide it here.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .. import db, project


def rollup_for_pack(pack_id: str) -> dict[str, Any]:
    """Build a pack-level perf view by joining drafts → slot_idx via the
    persisted brief's `topic` matching `schedule[*].title`.

    Returns:
        {
          "pack_id": ...,
          "explicit_feedbacks": int,    # rows directly saved to strategy_performance
          "derived_per_slot": list[dict],
                                        # each: {slot_idx, title, latest_metrics, draft_id}
          "totals": {likes, comments, saves, shares},
          "draft_count": int,
        }
    """
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()

    with db.connect(read_only=True) as con:
        prow = con.execute(
            "SELECT pack_json, library_id FROM studio_strategies WHERE pack_id = ?",
            (pack_id,),
        ).fetchone()
        if not prow or not prow["pack_json"]:
            return {
                "pack_id": pack_id, "explicit_feedbacks": 0,
                "derived_per_slot": [], "totals": {}, "draft_count": 0,
                "error": "pack not found or not expanded",
            }
        pack = json.loads(prow["pack_json"])
        schedule = pack.get("schedule") or []

        explicit = con.execute(
            "SELECT COUNT(*) FROM studio_strategy_performance WHERE pack_id = ?",
            (pack_id,),
        ).fetchone()[0]

        # For each schedule slot, find a draft that matches by topic == title
        # (the Strategy.tsx prefill sets brief.topic = slot.title), then take
        # that draft's latest perf row.
        derived: list[dict[str, Any]] = []
        totals = {"likes": 0, "comments": 0, "saves": 0, "shares": 0}
        n_with_data = 0
        for idx, slot in enumerate(schedule):
            slot_title = (slot.get("title") or "").strip()
            if not slot_title:
                continue
            # find a draft whose brief_json.topic matches; use most-recent.
            drow = con.execute(
                "SELECT draft_id FROM studio_drafts"
                " WHERE json_extract(brief_json, '$.topic') = ?"
                " ORDER BY generated_at DESC LIMIT 1",
                (slot_title,),
            ).fetchone()
            if not drow:
                derived.append({
                    "slot_idx": idx, "title": slot_title,
                    "draft_id": None, "latest_metrics": None,
                })
                continue
            draft_id = drow["draft_id"]
            perf_row = con.execute(
                "SELECT likes, comments, saves, shares, views, follower_delta,"
                " recorded_at FROM studio_draft_performance"
                " WHERE draft_id = ? ORDER BY recorded_at DESC LIMIT 1",
                (draft_id,),
            ).fetchone()
            metrics = dict(perf_row) if perf_row else None
            if metrics:
                n_with_data += 1
                for k in ("likes", "comments", "saves", "shares"):
                    v = metrics.get(k)
                    if v is not None:
                        totals[k] += v
            derived.append({
                "slot_idx": idx, "title": slot_title,
                "draft_id": draft_id, "latest_metrics": metrics,
            })

    return {
        "pack_id": pack_id,
        "explicit_feedbacks": int(explicit),
        "derived_per_slot": derived,
        "totals": totals,
        "draft_count": n_with_data,
    }


def rollup_for_project(project_id: str | None = None) -> dict[str, Any]:
    """Project-wide rollup — all drafts + their latest perf, regardless of
    pack linkage. Drives the unified feedback dashboard.
    """
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project_id or project.active_project_id()

    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            "SELECT d.draft_id, d.published, d.published_at,"
            "  d.published_title, json_extract(d.brief_json,'$.topic') AS topic,"
            "  json_extract(d.brief_json,'$.angle') AS angle,"
            "  d.published_url,"
            "  (SELECT title FROM studio_draft_candidates"
            "   WHERE candidate_id = d.final_candidate_id) AS final_title"
            " FROM studio_drafts d"
            " WHERE (d.project_id = ? OR d.project_id IS NULL)"
            " ORDER BY COALESCE(d.published_at, d.generated_at) DESC",
            (pid,),
        ))
        out: list[dict[str, Any]] = []
        for r in rows:
            r = dict(r)
            perf = con.execute(
                "SELECT likes, comments, saves, shares, views, follower_delta,"
                " recorded_at FROM studio_draft_performance"
                " WHERE draft_id = ? ORDER BY recorded_at DESC LIMIT 1",
                (r["draft_id"],),
            ).fetchone()
            r["latest_metrics"] = dict(perf) if perf else None
            out.append(r)
    return {"project_id": pid, "drafts": out}


def list_performance_rollup(library_id: str | None = None,
                            limit: int = 100) -> list[dict[str, Any]]:
    """Raw query from the SQL view — for debugging and the unified UI."""
    db.apply_migrations(verbose=False)
    where = ""
    args: list[Any] = []
    if library_id:
        where = " WHERE library_id = ?"
        args.append(library_id)
    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            f"SELECT * FROM studio_performance_rollup{where}"
            f" ORDER BY created_at DESC LIMIT ?",
            (*args, limit),
        ))
    out = []
    for r in rows:
        d = dict(r)
        for k in ("per_slot_json", "overall_json"):
            v = d.pop(k, None)
            if v:
                try: d[k.replace("_json", "")] = json.loads(v)
                except Exception: pass
        out.append(d)
    return out
