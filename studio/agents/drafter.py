"""Drafter pool: fans out the same prompt to N Generators in parallel.

Each Generator (Claude / DeepSeek / OpenAI / etc.) gets the same prompt that
embeds:
    - the original brief
    - retrieved refs + comments + hooks
    - the Strategist's enriched strategy (the key delta vs single-LLM mode)

Output: appended to ctx.drafts.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..brief import Brief
from ..generators.base import Generator, GeneratedCandidate, PromptBundle
from ..generators import prompts as g_prompts
from .base import Agent, AgentContext


def _strategy_block(strategy: dict[str, Any]) -> str:
    if not strategy:
        return "（无显式策略，按 brief 自由发挥）"
    parts = [
        f"推荐 hook：{strategy.get('recommended_hook', '')}",
        f"开头钩子：{strategy.get('opening_hook', '')}",
        f"结构：" + " → ".join(strategy.get("structure", [])),
        f"结尾 CTA：{strategy.get('cta_phrase', '')}",
        f"语气：{strategy.get('tone', '')}",
        f"避坑：" + "；".join(strategy.get("avoid", [])),
    ]
    return "\n".join(parts)


def _augmented_user(brief: Brief, ctx: AgentContext) -> str:
    base = g_prompts.build_user_message(
        brief, ctx.refs, ctx.comments, ctx.hooks
    )
    # Pull in the latest insight report's consensus so each drafter aligns
    # with what both AIs already agreed about the corpus.
    from ..insight.pipeline import latest_completed_for_current_library, consensus_summary_for_prompt
    report_ctx = consensus_summary_for_prompt(latest_completed_for_current_library())
    report_block = (
        f"\n\n{report_ctx}\n（以上是这个语料库的双 AI 共识分析报告，是你创作的强参考。）\n"
        if report_ctx else ""
    )
    return (
        "【上层 Strategist 已经定的策略 — 必须遵从】\n"
        f"{_strategy_block(ctx.strategy)}"
        f"{report_block}\n\n"
        f"{base}"
    )


class DrafterPoolAgent(Agent):
    name = "drafter"

    def __init__(self, generators: list[Generator]):
        if not generators:
            raise ValueError("at least one generator required")
        self.generators = generators

    async def run(self, ctx: AgentContext) -> None:
        system = g_prompts.SYSTEM_TITLE_BODY
        user = _augmented_user(ctx.brief, ctx)
        bundle = PromptBundle(
            system=system, user=user,
            expected_schema=g_prompts.JSON_SCHEMA,
        )

        async def _one(gen: Generator) -> GeneratedCandidate:
            try:
                return await asyncio.wait_for(gen.generate(bundle), timeout=180)
            except asyncio.TimeoutError:
                return GeneratedCandidate.failed(gen.model, "timeout (180s)")
            except Exception as e:
                return GeneratedCandidate.failed(gen.model, f"unhandled: {e!r}")

        t0 = self._ms()
        results = await asyncio.gather(*(_one(g) for g in self.generators))
        elapsed = self._ms() - t0

        ctx.drafts.extend(results)

        # Emit one trace step per drafter so the UI timeline shows them all.
        base_idx = len(ctx.trace)
        for i, (gen, cand) in enumerate(zip(self.generators, results)):
            step = self._new_step(base_idx + i, f"{self.name}:{gen.name}")
            step.llm = cand.llm
            step.latency_ms = cand.latency_ms
            step.cost_estimate_usd = cand.cost_estimate_usd
            step.error = cand.error
            step.input_summary = self._truncate(user, 800)
            if cand.error:
                step.output_summary = cand.error
            else:
                step.output_summary = json.dumps(
                    {
                        "title": cand.payload.title,
                        "self_score": cand.payload.self_score,
                        "hook_type": cand.payload.hook_type,
                    },
                    ensure_ascii=False,
                )
            step.raw_response = self._truncate(cand.raw_response, 4000)
            ctx.record(step)
        # Total pool elapsed not recorded as a step — sum of children already
        # in the trace covers it.
        _ = elapsed
