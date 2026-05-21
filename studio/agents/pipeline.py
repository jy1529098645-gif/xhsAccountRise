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
from .douyin_drafter import DouyinDrafterPoolAgent
from .planner import PlannerAgent
from .refiner import RefinerAgent
from .researcher import ResearcherAgent
from .strategist import StrategistAgent
from .synthesizer import SynthesizerAgent


@dataclass
class PipelineConfig:
    # v0.62.20 ：reasoning/quality roles → gpt-5（最新旗舰，质量上限最高）。
    # critic / planner 走 deepseek（cheap judgment is enough）。frontend 总
    # 发送 spec，这些默认主要服务直接调 API 的用户。
    strategist_spec: str = "openai:gpt-5"                # strategic planning
    drafter_spec: str = "openai:gpt-5"                   # consumer-facing post body
    critic_spec: str = "deepseek"                        # cheap judgment is enough
    refiner_spec: str = "openai:gpt-5"                   # rewrite on critique
    synthesizer_spec: str = "openai:gpt-5"               # final fusion (user-facing)
    planner_spec: str = "deepseek"                       # publish schedule, mechanical
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
    # v0.61.19 ：默认 Synthesizer 不调 LLM 融合 — 跑 _pick_best 直接挑 critic
    # 最高分的候选作为 final。这样保留单家原 voice（融合常常把 Claude 的活人感
    # + 4o 结构 + DeepSeek 下沉摊平成中庸版）。用户在 UI 上还能一键选其它候选
    # 覆盖。设 fuse_synthesizer=True 恢复旧的 LLM 融合行为。
    fuse_synthesizer: bool = False
    # v0.61.22 ：角度 → 专属 model spec 映射。{} = 全部走 drafter_spec 轮转
    # （默认）。映射里有的角度会用对应 spec 覆写 round-robin。
    # 例 ：{"教程": "claude:sonnet", "段子": "deepseek"} 钉死这两条角度。
    angle_models: dict[str, str] = field(default_factory=dict)
    # v0.50 fast_mode: collapse to 2 LLM calls.
    #   Stage 1 = drafter (does its own strategy decisions inline)
    #   Stage 2 = synthesizer ∥ planner (no separate critic, no refiner)
    # Saves 30-50s vs the full 6-stage pipeline. Quality risk is low for
    # default single-Sonnet drafter; multi-drafter or high-stakes runs
    # should set fast_mode=False to restore the full pipeline.
    fast_mode: bool = True


def _first(gens: list[Generator]) -> Generator:
    if not gens:
        raise ValueError("empty generator list")
    return gens[0]


async def run_pipeline(
    brief: Brief,
    cfg: PipelineConfig | None = None,
    *,
    parent_draft_id: str | None = None,
    variant_label: str | None = None,
) -> dict[str, Any]:
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
    # v0.57: Douyin gets its own drafter (structured shot-script schema +
    # title-library retrieval + playbook prompt context). xhs / kuaishou /
    # bilibili etc. keep the xhs-shaped DrafterPoolAgent.
    is_douyin = brief.platform == "douyin"
    drafter_pool = (
        DouyinDrafterPoolAgent(drafters)
        if is_douyin
        else DrafterPoolAgent(drafters, angle_models=cfg.angle_models)
    )
    critics = registry.build(cfg.critic_spec) if not cfg.skip_critics else []
    critic_pool = CriticPoolAgent(critics) if critics else None
    # v0.57: refiner + synthesizer run xhs-shaped prompts. If they touched
    # Douyin candidates they'd flatten the structured payload (shots /
    # hook_3s / hashtags / predicted_metrics / library title IDs) into a
    # single xhs body string. For Douyin: skip refiner entirely; keep
    # synthesizer in the pipeline but force generator=None so it falls
    # back to _pick_best (picks the best Douyin candidate AS-IS).
    refiner = (
        RefinerAgent(_first(registry.build(cfg.refiner_spec)))
        if (not cfg.skip_refiner and not is_douyin) else None
    )
    # v0.61.19 ：只有 fuse_synthesizer=True 才传 generator 给 SynthesizerAgent，
    # 否则 generator=None → 走 _pick_best 自动挑 critic 最高分（保 voice）。
    # v0.57: Douyin always uses _pick_best (no LLM fusion) to preserve the
    # structured douyin_meta.
    synth_gen = (
        _first(registry.build(cfg.synthesizer_spec))
        if (not cfg.skip_synthesizer and cfg.fuse_synthesizer and not is_douyin)
        else None
    )
    synthesizer = SynthesizerAgent(generator=synth_gen)
    planner = (
        PlannerAgent(_first(registry.build(cfg.planner_spec)))
        if not cfg.skip_planner else None
    )

    await researcher.run(ctx)

    if cfg.fast_mode:
        # v0.50 FAST mode: skip strategist (drafter does it inline), skip
        # critic, skip refiner. Stages collapse to:
        #   researcher → drafter (with strategy-aware prompt) → synth ∥ planner
        # Saves ~30-50s vs the full pipeline.
        await drafter_pool.run(ctx)
    else:
        # Full pipeline: researcher → strategist → drafter → critic → refiner
        if strategist:
            await strategist.run(ctx)
        await drafter_pool.run(ctx)
        if critic_pool:
            await critic_pool.run(ctx)
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

    bundle = _persist(
        ctx, cfg,
        parent_draft_id=parent_draft_id,
        variant_label=variant_label,
    )
    return bundle


def _persist(ctx: AgentContext, cfg: PipelineConfig,
             parent_draft_id: str | None = None,
             variant_label: str | None = None) -> dict[str, Any]:
    draft_id = uuid.uuid4().hex[:16]
    now = int(time.time())
    lib_id = ctx.library_id or library.active_lib_id()

    notes_payload = {
        "config": asdict(cfg),
        "plan": ctx.plan,
        "strategy": ctx.strategy,
    }
    # v0.53: persist RAG context so DraftDetail provenance panel can show
    # "this draft references these specific top-K notes / comments / hooks"
    # without re-running the retriever.
    rag_payload = {
        "refs": [
            {
                "note_id": r.get("note_id"),
                "title": r.get("title"),
                "liked_count": r.get("liked_count"),
                "collected_count": r.get("collected_count"),
                "comment_count": r.get("comment_count"),
                "url": r.get("url"),
                "body_excerpt": (r.get("body") or "")[:400],
                # v0.57: video-platform fields so ProvenancePanel shows
                # ▶︎ duration + 🔁 shares for Douyin/BiliBili refs.
                "duration_sec": int((r.get("video_duration_ms") or 0) / 1000),
                "share_count": r.get("share_count") or 0,
                "image_count": r.get("image_count") or 0,
                "author_nickname": r.get("author_nickname") or "",
            }
            for r in (ctx.refs or [])
        ],
        "comments": [
            {
                "comment_id": c.get("comment_id"),
                "content": (c.get("content") or "")[:300],
                "like_count": c.get("like_count"),
                "note_id": c.get("note_id"),
            }
            for c in (ctx.comments or [])[:30]
        ],
        "hooks": [
            {
                "category": h.get("category"),
                "count": h.get("count"),
                "median_likes": h.get("median_likes"),
                "examples": [
                    {"title": e.get("title"), "liked_count": e.get("liked_count")}
                    for e in (h.get("examples") or [])[:5]
                ],
            }
            for h in (ctx.hooks or [])
        ],
    }

    # Resolve the active prompt version (may have been bumped by an approved
    # feedback proposal). Falls back to the constant if seed migration hasn't
    # run yet.
    try:
        prompt_version, _ = g_prompts.active_title_body_prompt()
    except Exception:
        prompt_version = g_prompts.TITLE_BODY_GEN_VERSION

    from .. import project as _project
    pid = _project.active_project_id()
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_drafts"
            " (draft_id, generated_at, prompt_version, brief_json, status,"
            "  mode, library_id, final_candidate_id, notes, project_id,"
            "  parent_draft_id, variant_label, rag_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                draft_id, now,
                prompt_version,
                ctx.brief.to_json(),
                "generated", "multi-agent",
                lib_id,
                (ctx.final.candidate_id if ctx.final else None),
                json.dumps(notes_payload, ensure_ascii=False),
                pid,
                parent_draft_id, variant_label,
                json.dumps(rag_payload, ensure_ascii=False),
            ),
        )

        # Drafter candidates
        inserted_ids: set[str] = set()
        for c in ctx.drafts:
            _insert_candidate(con, draft_id, c, now, chosen=False)
            _insert_douyin_meta(con, draft_id, c, now)
            inserted_ids.add(c.candidate_id)
        if ctx.refined and ctx.refined.candidate_id not in inserted_ids:
            _insert_candidate(con, draft_id, ctx.refined, now, chosen=False)
            _insert_douyin_meta(con, draft_id, ctx.refined, now)
            inserted_ids.add(ctx.refined.candidate_id)
        # Synthesizer often produces a fresh candidate (its own candidate_id);
        # if so it isn't in ctx.drafts or ctx.refined yet, so insert it too.
        # Otherwise the UPDATE below targets a row that doesn't exist and the
        # 'chosen' marker silently drops.
        if ctx.final and ctx.final.candidate_id not in inserted_ids:
            _insert_candidate(con, draft_id, ctx.final, now, chosen=False)
            _insert_douyin_meta(con, draft_id, ctx.final, now)
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

        # v0.53: hard compliance gate. Scan every candidate (incl. synth/
        # refined) and persist hits so the UI can render a red highlight +
        # blocker modal before the user marks-published.
        try:
            from ..compliance import check_candidate as _check_cand
        except Exception:
            _check_cand = None  # defensive
        if _check_cand is not None:
            # Dedup by candidate_id (same convention as the candidates loop
            # above) — using object identity instead would silently break
            # if a future refactor ever reuses GeneratedCandidate objects.
            seen_cids: set[str] = set()
            all_cands: list = []
            for c in (list(ctx.drafts)
                      + ([ctx.refined] if ctx.refined else [])
                      + ([ctx.final] if ctx.final else [])):
                if c is None or c.candidate_id in seen_cids:
                    continue
                seen_cids.add(c.candidate_id)
                all_cands.append(c)
            for c in all_cands:
                if c is None or c.error:
                    continue
                try:
                    payload = {
                        "title": c.payload.title,
                        "body": c.payload.body,
                        "tags": c.payload.tags,
                        "cover_prompt": c.payload.cover_prompt,
                        "self_critique": c.payload.self_critique,
                    }
                    result = _check_cand(payload)
                except Exception:
                    continue
                con.execute(
                    "INSERT INTO studio_compliance_checks"
                    " (check_id, candidate_id, draft_id, checked_at,"
                    "  severity, hits_json)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        uuid.uuid4().hex[:16], c.candidate_id, draft_id, now,
                        result["severity"],
                        json.dumps(result["hits"], ensure_ascii=False),
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

    # Build a compact compliance summary for the return bundle so the
    # Composer UI can pop a warning modal immediately without a refetch.
    try:
        from ..compliance import check_candidate as _check_cand
        compliance_summary = (
            _check_cand({
                "title": ctx.final.payload.title,
                "body": ctx.final.payload.body,
                "tags": ctx.final.payload.tags,
                "cover_prompt": ctx.final.payload.cover_prompt,
                "self_critique": ctx.final.payload.self_critique,
            }) if (ctx.final and not ctx.final.error) else {"severity": "pass", "hits": [], "hit_count": 0}
        )
    except Exception:
        compliance_summary = {"severity": "pass", "hits": [], "hit_count": 0}

    return {
        "draft_id": draft_id,
        "library_id": lib_id,
        "parent_draft_id": parent_draft_id,
        "variant_label": variant_label,
        "prompt_version": prompt_version,
        "brief": asdict(ctx.brief),
        "strategy": ctx.strategy,
        "plan": ctx.plan,
        "compliance": compliance_summary,
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


def _insert_douyin_meta(con, draft_id: str, c, now: int) -> None:
    """Persist the structured Douyin payload (when the draft was generated
    via DouyinDrafterPoolAgent) into studio_douyin_drafts_meta. No-op for
    xhs candidates whose payload.douyin_meta is empty. Wrapped in try so
    a missing 011 migration on an older library doesn't break persistence."""
    meta = getattr(c.payload, "douyin_meta", None) or {}
    if not meta:
        return
    bucket = meta.get("content_bucket") or {}
    try:
        con.execute(
            "INSERT OR REPLACE INTO studio_douyin_drafts_meta"
            " (candidate_id, draft_id, content_bucket_id, content_bucket_label,"
            "  predicted_metrics_json, library_title_ids_json,"
            "  duration_sec, hashtags_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                c.candidate_id, draft_id,
                meta.get("content_bucket_id"),
                bucket.get("label") if isinstance(bucket, dict) else None,
                json.dumps(meta.get("predicted_metrics") or {}, ensure_ascii=False),
                json.dumps(meta.get("library_title_ids") or [], ensure_ascii=False),
                meta.get("duration_sec_target"),
                json.dumps(meta.get("hashtags") or [], ensure_ascii=False),
                now,
            ),
        )
    except Exception:
        pass


def _insert_candidate(con, draft_id: str, c, now: int, chosen: bool) -> None:
    meta = {
        "latency_ms": c.latency_ms,
        "token_usage": c.token_usage,
        "cost_estimate_usd": c.cost_estimate_usd,
        "error": c.error,
    }
    # v0.57: when the draft came from the Douyin pipeline, stash the full
    # structured payload (shots, hook_3s, cta_voice, caption, cover_text)
    # alongside the latency/cost meta. The DouyinProvenancePanel reads
    # this on the frontend to render the shot-list timeline.
    dy_meta = getattr(c.payload, "douyin_meta", None)
    if dy_meta:
        meta["douyin_meta"] = dy_meta
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
