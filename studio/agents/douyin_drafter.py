"""Douyin-shape drafter agent. Replaces the xhs DrafterPoolAgent when
`Brief.platform == "douyin"`.

Differences vs xhs DrafterPoolAgent:
  - Uses studio.douyin.prompts (structured shot-script schema) instead of
    generators.prompts (xhs title+body schema).
  - Goes through llm_call.call_for_json (LLM-family-agnostic JSON helper)
    rather than the generator.generate() pipeline that bakes in xhs shape.
  - Maps the returned structured Douyin payload onto a CandidatePayload so
    the rest of the pipeline (synthesizer, persistence, DB read) works
    unchanged. The full structured payload lives on `payload.douyin_meta`
    and gets persisted into studio_douyin_drafts_meta by _persist().
  - Pulls retrieved title-library entries + real-video refs into the prompt
    so the LLM has 18 hand-curated hooks to learn from per draft.

Returns: appended to ctx.drafts. Same interface as DrafterPoolAgent so the
pipeline can swap freely.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from ..brief import Brief
from ..generators.base import (
    Generator, GeneratedCandidate, CandidatePayload,
)
from ..llm_call import call_for_json
from ..douyin import prompts as douyin_prompts
from ..douyin import title_library, playbook
from .base import Agent, AgentContext


def _shots_to_body(meta: dict[str, Any]) -> str:
    """Render the structured Douyin payload as a readable script string.
    Stored in `payload.body` so the existing UI keeps rendering something
    sensible, while the structured fields live on payload.douyin_meta."""
    lines: list[str] = []
    hook = meta.get("hook_3s") or ""
    dur = meta.get("duration_sec_target") or "?"
    bucket_label = (meta.get("content_bucket") or {}).get("label") or meta.get("content_bucket_id") or ""
    lines.append(f"📹 抖音视频脚本 · 目标时长 {dur}s · 内容桶 {bucket_label}")
    if hook:
        lines.append("")
        lines.append(f"⚡ 前 3 秒钩子（hook）：{hook}")
    shots = meta.get("shots") or []
    if shots:
        lines.append("")
        lines.append("🎬 分镜：")
        for s in shots:
            t = s.get("t") or ""
            voice = s.get("voice") or ""
            visual = s.get("visual") or ""
            lines.append(f"  [{t}] 口播：{voice}")
            lines.append(f"        画面：{visual}")
    cta = meta.get("cta_voice") or ""
    if cta:
        lines.append("")
        lines.append(f"🎯 结尾口播 CTA：{cta}")
    cover = meta.get("cover_text") or ""
    if cover:
        lines.append("")
        lines.append(f"🖼️ 封面贴片：{cover}")
    return "\n".join(lines)


def _predict_likes(predicted_metrics: dict[str, Any], baseline_total: int) -> int:
    """Convert the 4 predicted ratios into a single `predicted_likes` int
    so the xhs-shaped pipeline (which sorts by predicted_likes for fallback
    ranking) still works. Heuristic: take 赞粉比 × assumed follower base."""
    try:
        like_ratio = float(predicted_metrics.get("赞粉比") or 0)
    except (TypeError, ValueError):
        like_ratio = 0
    # Use the bucket's median_total as the floor and scale by 赞粉比.
    return max(int(baseline_total * (1 + like_ratio * 5)), 0)


def _adapt_payload(
    raw: dict[str, Any], *, angle: str
) -> CandidatePayload:
    """Map Douyin-shape LLM output → CandidatePayload (xhs-compat + extras)."""
    bucket_id = str(raw.get("content_bucket_id") or "ai_tutorial")
    bucket = playbook.CONTENT_BUCKET_BY_ID.get(bucket_id) or playbook.CONTENT_BUCKETS[2]
    hashtags = [str(t).lstrip("#").strip()
                for t in (raw.get("hashtags") or []) if t]
    predicted = raw.get("predicted_metrics") or {}
    library_ids = [int(x) for x in (raw.get("library_title_ids") or [])
                   if isinstance(x, (int, str)) and str(x).isdigit()]
    full_meta = {
        "caption": str(raw.get("caption") or "").strip(),
        "hashtags": hashtags,
        "duration_sec_target": int(raw.get("duration_sec_target") or 30),
        "hook_3s": str(raw.get("hook_3s") or "").strip(),
        "shots": [
            {
                "t": str(s.get("t") or "").strip(),
                "voice": str(s.get("voice") or "").strip(),
                "visual": str(s.get("visual") or "").strip(),
            }
            for s in (raw.get("shots") or []) if isinstance(s, dict)
        ],
        "cta_voice": str(raw.get("cta_voice") or "").strip(),
        "cover_text": str(raw.get("cover_text") or "").strip(),
        "content_bucket_id": bucket_id,
        "content_bucket": {
            "id": bucket["id"], "label": bucket["label"],
            "median_total": bucket["median_total"],
            "p90_total": bucket["p90_total"],
            "viral_rate": bucket["viral_rate"],
            "median_save_ratio": bucket["median_save_ratio"],
            "median_share_ratio": bucket["median_share_ratio"],
        },
        "predicted_metrics": {
            "赞粉比":   float(predicted.get("赞粉比")   or 0),
            "收藏赞比": float(predicted.get("收藏赞比") or 0),
            "分享赞比": float(predicted.get("分享赞比") or 0),
            "评论赞比": float(predicted.get("评论赞比") or 0),
        },
        "library_title_ids": library_ids,
    }
    # Map structured payload → xhs-shape fields for back-compat:
    #   title  = caption (+ cover_text appended if any — useful in DraftDetail header)
    #   body   = pretty-rendered shot script string
    #   tags   = hashtags
    title = full_meta["caption"] or full_meta["cover_text"] or ""
    body = _shots_to_body(full_meta)
    return CandidatePayload(
        title=title,
        body=body,
        tags=hashtags[:12],
        cover_prompt=full_meta["cover_text"],
        hook_type=bucket["label"],
        predicted_likes=_predict_likes(
            full_meta["predicted_metrics"], bucket["median_total"]
        ),
        self_score=float(raw.get("self_score") or 0),
        self_critique=str(raw.get("self_critique") or "").strip(),
        angle=angle,
        douyin_meta=full_meta,
    )


class DouyinDrafterPoolAgent(Agent):
    name = "drafter"  # same name so trace UI groups it identically

    def __init__(self, generators: list[Generator]):
        if not generators:
            raise ValueError("at least one generator required")
        self.generators = generators

    async def run(self, ctx: AgentContext) -> None:
        # Make sure the title library is seeded for the active DB (no-op
        # after first run). This is cheap idempotent setup.
        try:
            title_library.ensure_seeded()
        except Exception:
            pass

        angles = list(ctx.brief.all_angles())
        tasks: list[tuple[str, Generator]] = [
            (angles[i], self.generators[i % len(self.generators)])
            for i in range(len(angles))
        ]

        # Suggested target duration — short-form wins. If the brief specified
        # target_length explicitly, respect it (cap at sensible bounds);
        # otherwise default to 30s which sits at the sweet spot.
        tl = int(getattr(ctx.brief, "target_length", 30) or 30)
        target_dur = max(7, min(120, tl if tl <= 180 else 30))

        async def _one(angle: str, gen: Generator) -> tuple[str, GeneratedCandidate, str]:
            t0 = self._ms()
            user = douyin_prompts.build_user_prompt(
                ctx.brief,
                refs=ctx.refs or [],
                target_duration_sec=target_dur,
            )
            try:
                raw = await asyncio.wait_for(
                    call_for_json(
                        gen, douyin_prompts.SYSTEM, user,
                        max_tokens=4000,
                        tool_name="submit_douyin_draft",
                        schema=douyin_prompts.DOUYIN_DRAFT_SCHEMA,
                    ),
                    timeout=180,
                )
            except asyncio.TimeoutError:
                return angle, GeneratedCandidate.failed(gen.model, "timeout (180s)"), user
            except Exception as e:
                return angle, GeneratedCandidate.failed(gen.model, f"unhandled: {e!r}"), user
            try:
                payload = _adapt_payload(raw, angle=angle)
            except Exception as e:
                return angle, GeneratedCandidate.failed(gen.model, f"adapt failed: {e!r}",
                                                         raw=json.dumps(raw, ensure_ascii=False)[:4000]), user
            elapsed = self._ms() - t0
            cand = GeneratedCandidate.new(
                gen.model, payload, latency_ms=elapsed,
                raw_response=json.dumps(raw, ensure_ascii=False)[:8000],
            )
            return angle, cand, user

        results = await asyncio.gather(*(_one(a, g) for a, g in tasks))

        for angle, cand, _ in results:
            ctx.drafts.append(cand)

        base_idx = len(ctx.trace)
        for i, ((angle, cand, user_msg), (_, gen)) in enumerate(zip(results, tasks)):
            step = self._new_step(base_idx + i, f"{self.name}:{gen.name}[{angle}]·douyin")
            step.llm = cand.llm
            step.latency_ms = cand.latency_ms
            step.cost_estimate_usd = cand.cost_estimate_usd
            step.error = cand.error
            step.input_summary = self._truncate(user_msg, 800)
            if cand.error:
                step.output_summary = cand.error
            else:
                m = cand.payload.douyin_meta or {}
                step.output_summary = json.dumps({
                    "angle": angle,
                    "caption": m.get("caption", "")[:50],
                    "bucket": m.get("content_bucket_id"),
                    "duration": m.get("duration_sec_target"),
                    "shots": len(m.get("shots") or []),
                    "self_score": cand.payload.self_score,
                }, ensure_ascii=False)
            step.raw_response = self._truncate(cand.raw_response, 4000)
            ctx.record(step)
