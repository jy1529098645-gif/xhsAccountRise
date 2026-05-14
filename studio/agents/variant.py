"""One-click variant fan-out from a parent draft.

When a published post unexpectedly takes off (>500 likes in 48h, per策略报告
4.2 节), the operator wants to *immediately* spawn 2-3 same-topic variants
with different angles. Manually re-entering the brief in Composer wastes the
crucial 24-hour follow-up window.

spawn_variants(parent_draft_id, angles, ...) reads the parent's persisted
brief, replaces `angles`, and runs run_pipeline with parent_draft_id linkage
so ancestry is queryable.

Returns a list of (draft_id, variant_label, error) tuples — one per requested
angle. Failures are recorded in error[i] but don't fail the whole batch.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

from .. import db
from ..brief import Brief, ALL_ANGLES
from . import pipeline as agent_pipeline


async def spawn_variants(
    parent_draft_id: str,
    angles: list[str],
    *,
    cfg: agent_pipeline.PipelineConfig | None = None,
) -> dict[str, Any]:
    """Spawn N variants (one per angle) of `parent_draft_id`.

    Args:
        parent_draft_id: source draft. Must exist and have a valid brief_json.
        angles: 1-9 angle strings (validated against ALL_ANGLES).
        cfg: optional pipeline config override (defaults match parent's mode).
    """
    db.apply_migrations(verbose=False)

    # Validate angles up-front.
    invalid = [a for a in angles if a not in ALL_ANGLES]
    if invalid:
        raise ValueError(
            f"未知 angle: {invalid}. 合法值: {list(ALL_ANGLES)}"
        )
    angles = list(dict.fromkeys(angles))  # dedupe, preserve order
    if not angles:
        raise ValueError("至少需要 1 个 angle")
    if len(angles) > 9:
        raise ValueError("最多同时生成 9 个变体 (一个角度一份)")

    # Load parent.
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT brief_json, library_id, project_id, mode"
            " FROM studio_drafts WHERE draft_id = ?",
            (parent_draft_id,),
        ).fetchone()
    if not row:
        raise LookupError(f"parent draft not found: {parent_draft_id}")

    parent_brief = Brief.from_json(row["brief_json"])
    # Force fast_mode for variants — turnaround speed matters more than
    # critic quality when chasing a hot post.
    cfg = cfg or agent_pipeline.PipelineConfig(fast_mode=True)

    async def _one(angle: str) -> dict[str, Any]:
        # New brief = parent's topic + single angle. We also reset
        # angles=() and angle=<single> so the drafter pool runs *once*
        # per spawn call (not N×N).
        brief = replace(parent_brief, angle=angle, angles=())
        label = f"variant·{angle}"
        try:
            bundle = await agent_pipeline.run_pipeline(
                brief, cfg,
                parent_draft_id=parent_draft_id,
                variant_label=label,
            )
            return {
                "angle": angle,
                "draft_id": bundle["draft_id"],
                "variant_label": label,
                "compliance": bundle.get("compliance"),
                "error": None,
            }
        except Exception as e:  # noqa: BLE001
            return {
                "angle": angle, "draft_id": None,
                "variant_label": label, "compliance": None,
                "error": f"{type(e).__name__}: {e}",
            }

    # Cap concurrency at 3 to avoid hammering provider rate limits.
    sem = asyncio.Semaphore(3)
    async def _bounded(a: str) -> dict[str, Any]:
        async with sem:
            return await _one(a)

    variants = await asyncio.gather(*(_bounded(a) for a in angles))
    return {
        "parent_draft_id": parent_draft_id,
        "angles_requested": angles,
        "variants": variants,
        "succeeded": sum(1 for v in variants if v["error"] is None),
        "failed": sum(1 for v in variants if v["error"] is not None),
    }


def list_variants(parent_draft_id: str) -> list[dict[str, Any]]:
    """All children of a parent draft, with their latest perf if any."""
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            "SELECT d.draft_id, d.generated_at, d.variant_label,"
            " d.published, d.published_at, d.published_url,"
            " json_extract(d.brief_json, '$.angle') AS angle,"
            " json_extract(d.brief_json, '$.topic') AS topic,"
            " (SELECT title FROM studio_draft_candidates"
            "  WHERE candidate_id = d.final_candidate_id) AS final_title,"
            " (SELECT severity FROM studio_compliance_checks"
            "  WHERE draft_id = d.draft_id AND candidate_id = d.final_candidate_id"
            "  ORDER BY checked_at DESC LIMIT 1) AS compliance_severity"
            " FROM studio_drafts d WHERE d.parent_draft_id = ?"
            " ORDER BY d.generated_at DESC",
            (parent_draft_id,),
        ))
    return [dict(r) for r in rows]
