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


def _augmented_user(brief: Brief, ctx: AgentContext, angle_override: str | None = None) -> str:
    base = g_prompts.build_user_message(
        brief, ctx.refs, ctx.comments, ctx.hooks,
        angle_override=angle_override,
    )
    # Pull in the latest insight report's consensus so each drafter aligns
    # with what both AIs already agreed about the corpus.
    from ..insight.pipeline import full_reference_block_for_prompt
    report_ctx = full_reference_block_for_prompt()
    report_block = (
        f"\n\n{report_ctx}\n（以上是这个语料库的双 AI 共识 + 用户上传整合的报告，是你创作的强参考。）\n"
        if report_ctx else ""
    )
    angle_directive = (
        f"\n【本次起草的角度 — 必须按此写】{angle_override}\n"
        f"（用户选了多个角度，整个起草团会一人一个角度并发出稿；你这一份只写「{angle_override}」角度，"
        f"不要写成其它角度。）\n"
        if angle_override else ""
    )
    return (
        "【上层 Strategist 已经定的策略 — 必须遵从】\n"
        f"{_strategy_block(ctx.strategy)}"
        f"{report_block}"
        f"{angle_directive}\n\n"
        f"{base}"
    )


class DrafterPoolAgent(Agent):
    name = "drafter"

    def __init__(self, generators: list[Generator],
                 angle_models: dict[str, str] | None = None):
        if not generators:
            raise ValueError("at least one generator required")
        self.generators = generators
        # v0.61.22 ：可选 角度→model spec 覆写映射。运行时 spec → 实际 Generator
        # 通过 registry.build 解析；解析失败的 spec 会被忽略走默认 round-robin。
        self.angle_models: dict[str, str] = dict(angle_models or {})

    async def run(self, ctx: AgentContext) -> None:
        # v0.53: pick the active prompt version from DB so retrospective-driven
        # diffs take effect on next run. Falls back to the hardcoded constant
        # when no version row exists.
        _version, system = g_prompts.active_title_body_prompt()
        # v0.52: multi-angle. We produce one draft per requested angle. LLMs
        # are cycled round-robin so a single-LLM pool still produces N
        # different-angle candidates (each is a fresh call). For multi-LLM
        # pools, the assignment also rotates so each angle hits a different
        # family if possible.
        # v0.61.22 ：angle_models 里有的角度用专属 spec，其它走 round-robin。
        from ..generators import registry as _registry
        angles = list(ctx.brief.all_angles())

        def _generator_for_angle(angle: str, fallback_idx: int) -> Generator:
            spec = (self.angle_models.get(angle) or "").strip()
            if spec and spec.lower() not in ("auto", ""):
                try:
                    gens = _registry.build(spec)
                    if gens:
                        return gens[0]
                except Exception:
                    pass  # fall through to round-robin
            return self.generators[fallback_idx % len(self.generators)]

        tasks: list[tuple[str, Generator]] = [
            (angles[i], _generator_for_angle(angles[i], i))
            for i in range(len(angles))
        ]

        async def _one(angle: str, gen: Generator) -> tuple[str, GeneratedCandidate, str]:
            user = _augmented_user(ctx.brief, ctx, angle_override=angle)
            bundle = PromptBundle(
                system=system, user=user,
                expected_schema=g_prompts.JSON_SCHEMA,
            )
            try:
                cand = await asyncio.wait_for(gen.generate(bundle), timeout=180)
            except asyncio.TimeoutError:
                cand = GeneratedCandidate.failed(gen.model, "timeout (180s)")
            except Exception as e:
                cand = GeneratedCandidate.failed(gen.model, f"unhandled: {e!r}")
            return angle, cand, user

        t0 = self._ms()
        results = await asyncio.gather(*(_one(a, g) for a, g in tasks))
        elapsed = self._ms() - t0

        # Annotate each candidate with its assigned angle so downstream
        # (synthesizer, UI) can show "candidate A — 故事 / B — 教程".
        for angle, cand, _ in results:
            try:
                cand.payload.angle = angle  # type: ignore[attr-defined]
            except Exception:
                pass
            ctx.drafts.append(cand)

        base_idx = len(ctx.trace)
        for i, ((angle, cand, user_msg), (_, gen)) in enumerate(zip(results, tasks)):
            step = self._new_step(base_idx + i, f"{self.name}:{gen.name}[{angle}]")
            step.llm = cand.llm
            step.latency_ms = cand.latency_ms
            step.cost_estimate_usd = cand.cost_estimate_usd
            step.error = cand.error
            step.input_summary = self._truncate(user_msg, 800)
            if cand.error:
                step.output_summary = cand.error
            else:
                step.output_summary = json.dumps(
                    {
                        "angle": angle,
                        "title": cand.payload.title,
                        "self_score": cand.payload.self_score,
                        "hook_type": cand.payload.hook_type,
                    },
                    ensure_ascii=False,
                )
            step.raw_response = self._truncate(cand.raw_response, 4000)
            ctx.record(step)
        _ = elapsed
