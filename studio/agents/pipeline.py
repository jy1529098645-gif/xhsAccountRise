"""Multi-agent pipeline orchestrator + persistence.

Wires Strategist → Researcher → DrafterPool → CriticPool → Refiner →
Synthesizer. Persists every step to studio_agent_traces, all candidates to
studio_draft_candidates (with chosen=1 on the final), and critiques to
studio_critiques.

Sane defaults for the agent-LLM assignment:
    Strategist  : Claude Opus 4.7  (best reasoning)
    Researcher  : no LLM
    Drafter pool: Claude Opus + DeepSeek + GPT-5  (diversity)
    Critic pool : Claude Sonnet + DeepSeek        (different from drafters
                                                    where possible)
    Refiner     : Claude Opus
    Synthesizer : no LLM

You can override any role via PipelineConfig.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

from .. import db, library
from ..brief import Brief
from ..generators import prompts as g_prompts
from ..generators import registry
from ..generators.base import Generator
from .base import AgentContext
from .critic import CriticPoolAgent
from .drafter import DrafterPoolAgent
from .planner import PlannerAgent
from .refiner import RefinerAgent
from .researcher import ResearcherAgent
from .strategist import StrategistAgent
from .synthesizer import SynthesizerAgent


@dataclass
class PipelineConfig:
    # LLM tiers — Opus reserved ONLY for the final consumer-facing fusion.
    # Everything else is fast-mid (Sonnet / cheaper). Pre-rebalance the
    # average compose call burned ~6× Opus calls; now it's 1.
    strategist_spec: str = "claude:sonnet"               # strategic planning
    # Drafter pool: dropped OpenAI for default. One strong Sonnet draft is
    # better than two mediocre ones with random LLM-specific quirks. User
    # can add openai/deepseek via the advanced agent config if they want
    # candidate diversity.
    drafter_spec: str = "claude:sonnet"                  # single Sonnet draft
    critic_spec: str = "claude:sonnet"                   # one critic is enough signal
    refiner_spec: str = "claude:sonnet"                  # rewrite on feedback
    # Synthesizer downgraded Opus → Sonnet. Sonnet 4.6 produces final
    # synthesized drafts indistinguishable from Opus 4.7 for 600-char xhs
    # posts; the 2× cost + 3× latency isn't worth it. Heavy-handed users
    # can override to opus via the agent config UI.
    synthesizer_spec: str = "claude:sonnet"
    planner_spec: str = "claude:sonnet"                  # publish schedule, mechanical
    # Refiner is auto-skipped when drafter pool produced ≤1 candidate (the
    # default Sonnet-only setup). Set force_refiner=True to always run it
    # — useful when N>=2 drafters or for high-stakes posts.
    force_refiner: bool = False
    k_refs: int = 8
    n_comments: int = 15
    top_hooks: int = 6
    skip_strategist: bool = False
    skip_critics: bool = False
    skip_refiner: bool = False
    skip_synthesizer: bool = False
    skip_planner: bool = False


def _first(gens: list[Generator]) -> Generator:
    if not gens:
        raise ValueError("empty generator list")
    return gens[0]


async def run_pipeline(brief: Brief, cfg: PipelineConfig | None = None) -> dict[str, Any]:
    cfg = cfg or PipelineConfig()
    db.apply_migrations(verbose=False)

    lib_id = library.active_lib_id()
    # If brief didn't override platform, inherit from active library.
    lib_meta = library.get_meta(lib_id)
    if lib_meta and brief.platform == "xiaohongshu" and lib_meta.platform != "xiaohongshu":
        from dataclasses import replace
        brief = replace(brief, platform=lib_meta.platform)
    ctx = AgentContext(brief=brief, library_id=lib_id)

    # Build agents
    drafters = registry.build(cfg.drafter_spec)
    if not drafters:
        raise ValueError("no drafter LLMs configured")

    strategist = (
        StrategistAgent(_first(registry.build(cfg.strategist_spec)))
        if not cfg.skip_strategist else None
    )
    researcher = ResearcherAgent(
        k_refs=cfg.k_refs, n_comments=cfg.n_comments, top_hooks=cfg.top_hooks,
    )
    drafter_pool = DrafterPoolAgent(drafters)
    critics = registry.build(cfg.critic_spec) if not cfg.skip_critics else []
    critic_pool = CriticPoolAgent(critics) if critics else None
    refiner = (
        RefinerAgent(_first(registry.build(cfg.refiner_spec)))
        if not cfg.skip_refiner else None
    )
    synth_gen = (
        _first(registry.build(cfg.synthesizer_spec))
        if not cfg.skip_synthesizer else None
    )
    synthesizer = SynthesizerAgent(generator=synth_gen)
    planner = (
        PlannerAgent(_first(registry.build(cfg.planner_spec)))
        if not cfg.skip_planner else None
    )

    # Run sequence:
    #   researcher (no LLM) → strategist → drafter pool → critic pool →
    #   refiner → synthesizer in parallel with planner (planner only needs
    #   strategy + drafts, not the synthesized final).
    # Saves ~15-25s by overlapping the last two LLM calls.
    await researcher.run(ctx)
    if strategist:
        await strategist.run(ctx)
    await drafter_pool.run(ctx)
    if critic_pool:
        await critic_pool.run(ctx)
    # Auto-skip refiner when drafter pool produced ≤1 draft. With a single
    # candidate, refiner just rewrites it based on its own critique —
    # essentially the same work synthesizer does (synth reads draft +
    # critique and produces final). Saves ~15-25s on the default
    # single-Sonnet drafter setup.
    skip_refiner_dynamic = (refiner is not None
                            and len(ctx.drafts or []) <= 1
                            and not getattr(cfg, "force_refiner", False))
    if refiner and not skip_refiner_dynamic:
        await refiner.run(ctx)

    # Synthesizer + Planner can run in parallel — neither reads the other's
    # output. Planner uses strategy + drafts (already in ctx); synthesizer
    # fuses drafts + critiques. Wrap both with isolated try/except so a
    # planner failure doesn't tank the user-visible final synthesis.
    async def _run_synth():
        await synthesizer.run(ctx)
    async def _run_planner():
        if planner:
            try:
                await planner.run(ctx)
            except Exception as e:
                # Planner is non-critical (just publish schedule); don't
                # propagate, leave ctx.plan as default if it fails.
                ctx.plan = ctx.plan or {"error": repr(e)}
    await asyncio.gather(_run_synth(), _run_planner())

    bundle = _persist(ctx, cfg)
    return bundle


def _persist(ctx: AgentContext, cfg: PipelineConfig) -> dict[str, Any]:
    draft_id = uuid.uuid4().hex[:16]
    now = int(time.time())
    lib_id = ctx.library_id or library.active_lib_id()

    notes_payload = {
        "config": asdict(cfg),
        "plan": ctx.plan,
        "strategy": ctx.strategy,
    }
    from .. import project as _project
    pid = _project.active_project_id()
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_drafts"
            " (draft_id, generated_at, prompt_version, brief_json, status,"
            "  mode, library_id, final_candidate_id, notes, project_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                draft_id, now,
                g_prompts.TITLE_BODY_GEN_VERSION,
                ctx.brief.to_json(),
                "generated", "multi-agent",
                lib_id,
                (ctx.final.candidate_id if ctx.final else None),
                json.dumps(notes_payload, ensure_ascii=False),
                pid,
            ),
        )

        # Drafter candidates
        inserted_ids: set[str] = set()
        for c in ctx.drafts:
            _insert_candidate(con, draft_id, c, now, chosen=False)
            inserted_ids.add(c.candidate_id)
        if ctx.refined and ctx.refined.candidate_id not in inserted_ids:
            _insert_candidate(con, draft_id, ctx.refined, now, chosen=False)
            inserted_ids.add(ctx.refined.candidate_id)
        # Synthesizer often produces a fresh candidate (its own candidate_id);
        # if so it isn't in ctx.drafts or ctx.refined yet, so insert it too.
        # Otherwise the UPDATE below targets a row that doesn't exist and the
        # 'chosen' marker silently drops.
        if ctx.final and ctx.final.candidate_id not in inserted_ids:
            _insert_candidate(con, draft_id, ctx.final, now, chosen=False)
            inserted_ids.add(ctx.final.candidate_id)
        if ctx.final:
            con.execute(
                "UPDATE studio_draft_candidates SET chosen=1 WHERE candidate_id=?",
                (ctx.final.candidate_id,),
            )

        # Critiques
        for cand_id, crits in ctx.critiques.items():
            for cr in crits:
                con.execute(
                    "INSERT INTO studio_critiques"
                    " (critique_id, candidate_id, draft_id, critic_llm,"
                    "  scores_json, risk_flags_json, suggestion, overall, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        cr.critique_id, cand_id, draft_id, cr.critic_llm,
                        json.dumps(cr.scores, ensure_ascii=False),
                        json.dumps(cr.risk_flags, ensure_ascii=False),
                        cr.suggestion, cr.overall, now,
                    ),
                )

        # Traces
        for s in ctx.trace:
            con.execute(
                "INSERT INTO studio_agent_traces"
                " (trace_id, draft_id, step_index, agent_name, llm,"
                "  input_summary, output_summary, latency_ms, cost_estimate_usd,"
                "  error, raw_response, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex[:16], draft_id, s.step_index,
                    s.agent_name, s.llm, s.input_summary, s.output_summary,
                    s.latency_ms, s.cost_estimate_usd, s.error,
                    s.raw_response[:4000] if s.raw_response else "",
                    now,
                ),
            )

    return {
        "draft_id": draft_id,
        "library_id": lib_id,
        "brief": asdict(ctx.brief),
        "strategy": ctx.strategy,
        "plan": ctx.plan,
        "rag": {
            "refs": [
                {"note_id": r["note_id"], "title": r["title"],
                 "likes": r["liked_count"]}
                for r in ctx.refs
            ],
            "comments_count": len(ctx.comments),
            "hooks": [h["category"] for h in ctx.hooks],
        },
        "drafts": [_serialize(c, ctx.critiques.get(c.candidate_id, []))
                   for c in ctx.drafts],
        "refined": _serialize(ctx.refined, []) if ctx.refined else None,
        "final": _serialize(ctx.final, ctx.critiques.get(ctx.final.candidate_id, []) if ctx.final else []) if ctx.final else None,
        "trace": [_serialize_step(s) for s in ctx.trace],
        "totals": {
            "cost_usd": ctx.total_cost(),
            "elapsed_s": int(time.time()) - ctx.started_at,
        },
        "generated_at": now,
    }


def _insert_candidate(con, draft_id: str, c, now: int, chosen: bool) -> None:
    meta = {
        "latency_ms": c.latency_ms,
        "token_usage": c.token_usage,
        "cost_estimate_usd": c.cost_estimate_usd,
        "error": c.error,
    }
    con.execute(
        "INSERT INTO studio_draft_candidates"
        " (candidate_id, draft_id, llm, title, body, tags_json,"
        "  cover_prompt, hook_type, predicted_likes, self_score,"
        "  self_critique, meta_json, human_score, chosen, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            c.candidate_id, draft_id, c.llm,
            c.payload.title, c.payload.body,
            json.dumps(c.payload.tags, ensure_ascii=False),
            c.payload.cover_prompt, c.payload.hook_type,
            c.payload.predicted_likes, c.payload.self_score,
            c.payload.self_critique,
            json.dumps(meta, ensure_ascii=False),
            None, 1 if chosen else 0, now,
        ),
    )


def _serialize(c, critiques: list) -> dict[str, Any]:
    if c is None:
        return None
    return {
        "candidate_id": c.candidate_id,
        "llm": c.llm,
        "error": c.error,
        "latency_ms": c.latency_ms,
        "cost_estimate_usd": c.cost_estimate_usd,
        "token_usage": c.token_usage,
        "payload": asdict(c.payload),
        "critiques": [
            {
                "critic_llm": cr.critic_llm,
                "scores": cr.scores,
                "risk_flags": cr.risk_flags,
                "suggestion": cr.suggestion,
                "overall": cr.overall,
            }
            for cr in critiques
        ],
        "critique_avg": (sum(cr.overall for cr in critiques) / len(critiques)) if critiques else None,
    }


def _serialize_step(s) -> dict[str, Any]:
    return {
        "step_index": s.step_index,
        "agent_name": s.agent_name,
        "llm": s.llm,
        "input_summary": s.input_summary,
        "output_summary": s.output_summary,
        "latency_ms": s.latency_ms,
        "cost_estimate_usd": s.cost_estimate_usd,
        "error": s.error,
    }
