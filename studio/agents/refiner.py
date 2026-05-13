"""Refiner agent: takes the top-scoring candidate and consolidated critiques,
produces an improved version.

Picks the best candidate by mean(critic.overall). Feeds (original, critiques,
strategy) to a strong LLM that returns a revised CandidatePayload.
"""
from __future__ import annotations

import json
from typing import Any

from ..generators.base import (
    CandidatePayload,
    GeneratedCandidate,
    Generator,
)
from .base import Agent, AgentContext, Critique


_SYSTEM = """\
你是小红书爆款写手中的「改稿主笔」。给你：
1. 一份已生成的候选稿件
2. 多个 LLM critic 对它的评分 + 风险点 + 改进建议
3. 上层 Strategist 定的策略

你的任务：在尊重原 hook 类型和策略的前提下，**针对 critic 的具体改进建议重写**，把致命缺陷修掉。

允许：
- 改标题（如果 hook 弱）
- 改正文段落（如果结构差）
- 调整 tags（如果不相关）
- 改 CTA（如果生硬或缺失）

绝对不要：
- 换 hook 类型
- 偏离 brief 主题
- 加任何虚假/夸大数字
- 增加任何品牌名（除非 brief 给了）

输出 JSON（同 drafter schema）：
{"title","body","tags","cover_prompt","hook_type","predicted_likes","self_score","self_critique"}"""


def _format_critiques(critiques: list[Critique]) -> str:
    if not critiques:
        return "（无 critique）"
    blocks = []
    for cr in critiques:
        scores = ", ".join(f"{k}={v:.1f}" for k, v in cr.scores.items())
        flags = "；".join(cr.risk_flags) or "无"
        blocks.append(
            f"- {cr.critic_llm} (overall {cr.overall:.1f}): {scores}\n"
            f"  风险：{flags}\n"
            f"  建议：{cr.suggestion}"
        )
    return "\n".join(blocks)


def _pick_top(ctx: AgentContext) -> tuple[GeneratedCandidate, list[Critique]] | None:
    best = None
    best_score = -1.0
    for cand in ctx.drafts:
        if cand.error:
            continue
        crits = ctx.critiques.get(cand.candidate_id, [])
        if not crits:
            continue
        avg = sum(c.overall for c in crits) / len(crits)
        if avg > best_score:
            best_score = avg
            best = (cand, crits)
    return best


def _strategy_lines(strategy: dict[str, Any]) -> str:
    if not strategy:
        return "（无）"
    return (
        f"hook：{strategy.get('recommended_hook', '')} / "
        f"开头：{strategy.get('opening_hook', '')} / "
        f"CTA：{strategy.get('cta_phrase', '')}"
    )


async def _call(gen: Generator, system: str, user: str) -> dict[str, Any]:
    from ..generators import prompts as g_prompts
    from ..llm_call import call_for_json
    return await call_for_json(
        gen, system, user, max_tokens=2048,
        tool_name="submit_revision", schema=g_prompts.JSON_SCHEMA,
    )


class RefinerAgent(Agent):
    name = "refiner"

    def __init__(self, generator: Generator):
        self.generator = generator

    async def run(self, ctx: AgentContext) -> None:
        step = self._new_step(len(ctx.trace), self.name)
        step.llm = self.generator.model

        picked = _pick_top(ctx)
        if picked is None:
            step.error = "no scorable candidate to refine"
            ctx.record(step)
            return
        cand, crits = picked

        user = (
            f"【brief 主题】{ctx.brief.topic}\n"
            f"【策略】{_strategy_lines(ctx.strategy)}\n\n"
            f"【原候选 by {cand.llm}】\n"
            f"title：{cand.payload.title}\n"
            f"tags：{cand.payload.tags}\n"
            f"hook_type：{cand.payload.hook_type}\n"
            f"body：\n{cand.payload.body}\n\n"
            f"【critic 反馈】\n{_format_critiques(crits)}\n\n"
            "请按 system 给的 schema 输出修订版 JSON。"
        )

        t0 = self._ms()
        try:
            parsed = await _call(self.generator, _SYSTEM, user)
            payload = CandidatePayload.from_dict(parsed)
        except Exception as e:
            step.error = f"refine failed: {e!r}"
            step.latency_ms = self._ms() - t0
            ctx.record(step)
            return
        step.latency_ms = self._ms() - t0
        refined = GeneratedCandidate.new(
            llm=f"{self.generator.model}+refined",
            payload=payload,
            latency_ms=step.latency_ms,
        )
        ctx.refined = refined
        step.input_summary = f"refined {cand.payload.title[:30]} (avg {sum(c.overall for c in crits)/len(crits):.1f})"
        step.output_summary = json.dumps(
            {"new_title": payload.title, "self_score": payload.self_score},
            ensure_ascii=False,
        )
        ctx.record(step)
