"""Multi-LLM orchestrator: fan-out, persist, score.

Given a Brief + a list of Generators, runs them concurrently and persists each
candidate into studio_drafts + studio_draft_candidates. Returns a dict with the
draft_id and the candidate list (for downstream rendering).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict
from typing import Any

from .. import db
from ..brief import Brief
from ..rag import retrieve
from . import prompts
from .base import GeneratedCandidate, Generator, PromptBundle


async def _run_one(gen: Generator, prompt: PromptBundle) -> GeneratedCandidate:
    try:
        return await asyncio.wait_for(gen.generate(prompt), timeout=120)
    except asyncio.TimeoutError:
        return GeneratedCandidate.failed(gen.model, "timeout (120s)")
    except Exception as e:
        return GeneratedCandidate.failed(gen.model, f"unhandled: {e!r}")


async def generate(
    brief: Brief,
    generators: list[Generator],
    k_refs: int = 8,
    n_comments: int = 15,
) -> dict[str, Any]:
    if not generators:
        raise ValueError("at least one generator required")

    # 1. Retrieve references.
    rag = retrieve.retrieve_for_brief(
        brief.topic, k_notes=k_refs, n_comments=n_comments
    )

    # 2. Build prompt bundle.
    system = prompts.SYSTEM_TITLE_BODY
    user = prompts.build_user_message(
        brief, rag["refs"], rag["comments"], rag["hooks"]
    )
    bundle = PromptBundle(
        system=system, user=user, expected_schema=prompts.JSON_SCHEMA
    )

    # 3. Fan-out.
    tasks = [_run_one(g, bundle) for g in generators]
    results: list[GeneratedCandidate] = await asyncio.gather(*tasks)

    # 4. Persist.
    draft_id = uuid.uuid4().hex[:16]
    now = int(time.time())
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_drafts"
            " (draft_id, generated_at, prompt_version, brief_json, status)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                draft_id,
                now,
                prompts.TITLE_BODY_GEN_VERSION,
                brief.to_json(),
                "generated",
            ),
        )
        for c in results:
            meta = {
                "latency_ms": c.latency_ms,
                "token_usage": c.token_usage,
                "cost_estimate_usd": c.cost_estimate_usd,
                "error": c.error,
                "rag": {
                    "ref_count": len(rag["refs"]),
                    "comment_count": len(rag["comments"]),
                    "hook_count": len(rag["hooks"]),
                },
            }
            con.execute(
                "INSERT INTO studio_draft_candidates"
                " (candidate_id, draft_id, llm, title, body, tags_json,"
                "  cover_prompt, hook_type, predicted_likes, self_score,"
                "  self_critique, meta_json, human_score, chosen, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    c.candidate_id,
                    draft_id,
                    c.llm,
                    c.payload.title,
                    c.payload.body,
                    json.dumps(c.payload.tags, ensure_ascii=False),
                    c.payload.cover_prompt,
                    c.payload.hook_type,
                    c.payload.predicted_likes,
                    c.payload.self_score,
                    c.payload.self_critique,
                    json.dumps(meta, ensure_ascii=False),
                    None,
                    0,
                    now,
                ),
            )

    return {
        "draft_id": draft_id,
        "brief": asdict(brief),
        "rag": {
            "refs": [
                {"note_id": r["note_id"], "title": r["title"], "likes": r["liked_count"]}
                for r in rag["refs"]
            ],
            "comments_count": len(rag["comments"]),
            "hooks": [h["category"] for h in rag["hooks"]],
        },
        "candidates": [_serialize(c) for c in results],
        "generated_at": now,
    }


def _serialize(c: GeneratedCandidate) -> dict[str, Any]:
    return {
        "candidate_id": c.candidate_id,
        "llm": c.llm,
        "error": c.error,
        "latency_ms": c.latency_ms,
        "cost_estimate_usd": c.cost_estimate_usd,
        "token_usage": c.token_usage,
        "payload": asdict(c.payload),
    }
