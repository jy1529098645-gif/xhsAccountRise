"""Synthesizer agent (LLM-driven consensus).

This is *the* differentiating step — instead of merely picking the highest-
scored draft, the Synthesizer sees every drafter's output + every critic's
feedback (+ the refined version if any) and produces a *fused* final draft
that combines the strongest elements from each:

    - the punchiest title (or a merged hybrid)
    - the structural skeleton of the best-rated body
    - the most resonant tags, deduplicated
    - the cover-prompt that matches the chosen title's tone
    - explicit address of any critic risk_flags

The model also returns a `rationale` explaining what it took from where, so
the human reviewer can audit the synthesis without re-reading every candidate.

If the LLM call fails, we fall back to the old "pick top-critic-scored
candidate" behaviour — never break the pipeline.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from ..generators import prompts as g_prompts
from ..generators.base import (
    CandidatePayload,
    GeneratedCandidate,
    Generator,
)
from .base import Agent, AgentContext, Critique


_SYSTEM = """\
你是「小红书爆款总编辑」。给你 N 份候选稿件（不同 LLM 写的）和它们各自的 critic 评分 + 风险点。

你的任务：**不是从中选一个**，而是**综合各家优点**写出一篇新的最终稿——
- 标题：取最强 hook（哪家的钩子最炸就用哪家的，必要时混搭）
- 正文骨架：选评分最高的结构作为底，把其他家的金句/数据/案例融进去
- tags：合并去重，挑赛道相关 + 高表现的 6-10 个
- cover_prompt：与最终标题语气匹配
- 必须主动修掉所有 critic 标出的 risk_flags

绝对不要：
- 凭空捏造没在任何候选里出现过的事实/数字/品牌名
- 偏离 brief 主题
- 学术腔 / 客套话

输出格式：JSON，键如下，不要任何额外文本：
{
  "title": "<最终标题>",
  "body": "<融合后的正文>",
  "tags": ["..."],
  "cover_prompt": "<英文封面图描述>",
  "hook_type": "<对应类型>",
  "predicted_likes": <整数>,
  "self_score": <0-10>,
  "self_critique": "<一句话风险点>",
  "rationale": {
    "title_from": "<取自哪家或 'merged from A+B'>",
    "body_from": "<取自哪家或 'fused'>",
    "tags_from": "<策略>",
    "addresses_risks": ["<已主动解决的 critic 风险点>"]
  }
}"""


def _format_candidate_block(idx: int, cand: GeneratedCandidate,
                            critiques: list[Critique]) -> str:
    p = cand.payload
    crit_lines = []
    for cr in critiques:
        scores_str = ", ".join(f"{k}={v:.1f}" for k, v in cr.scores.items())
        flags = "；".join(cr.risk_flags) or "无"
        crit_lines.append(
            f"  - {cr.critic_llm} (overall {cr.overall:.1f}): {scores_str}\n"
            f"    风险: {flags}\n"
            f"    建议: {cr.suggestion}"
        )
    crit_block = "\n".join(crit_lines) if crit_lines else "  (无 critique)"

    return (
        f"━━━ 候选 #{idx} · LLM = {cand.llm} ━━━\n"
        f"title: {p.title}\n"
        f"hook_type: {p.hook_type}\n"
        f"tags: {p.tags}\n"
        f"cover_prompt: {p.cover_prompt}\n"
        f"body:\n{p.body}\n\n"
        f"critic 评审:\n{crit_block}"
    )


def _build_user(ctx: AgentContext) -> str:
    blocks = []
    for i, c in enumerate(ctx.drafts, 1):
        if c.error:
            blocks.append(f"━━━ 候选 #{i} · {c.llm} · FAILED ━━━\n{c.error}")
            continue
        blocks.append(_format_candidate_block(i, c, ctx.critiques.get(c.candidate_id, [])))
    if ctx.refined and not ctx.refined.error:
        blocks.append(_format_candidate_block(
            len(ctx.drafts) + 1, ctx.refined,
            ctx.critiques.get(ctx.refined.candidate_id, []),
        ))

    strategy = ctx.strategy or {}
    strategy_lines = (
        f"推荐 hook: {strategy.get('recommended_hook', '')}\n"
        f"开头钩子: {strategy.get('opening_hook', '')}\n"
        f"结尾 CTA: {strategy.get('cta_phrase', '')}\n"
        f"语气: {strategy.get('tone', '')}\n"
        f"避坑: {'；'.join(strategy.get('avoid', []))}"
    )

    angles = ctx.brief.all_angles()
    angle_line = (f"角度: {angles[0]}" if len(angles) == 1
                  else "覆盖角度: " + " / ".join(angles)
                       + "（候选已按角度分别出稿，融合时保留多角度优点而不是收敛到单一角度）")
    return (
        f"【brief】\n主题: {ctx.brief.topic}\n{angle_line}\n"
        f"**字数硬指标: {ctx.brief.target_length} 字以上（±10% 容差）**"
        f" — 融合版正文必须达到这个字数，少了等于稿件没完成。\n\n"
        f"【上层 Strategist 已定的策略】\n{strategy_lines}\n\n"
        f"【{len([c for c in ctx.drafts if not c.error])} 份候选 + critic 评审】\n\n"
        + "\n\n".join(blocks)
        + "\n\n现在请综合所有优点写出最终融合版。"
        + "**重要 ：写完核对字数，少于目标必须扩写到目标以上**。务必输出完整 JSON。"
    )


_SYNTH_SCHEMA = {
    "type": "object",
    "required": [
        "title", "body", "tags", "cover_prompt", "hook_type",
        "predicted_likes", "self_score", "self_critique", "rationale",
    ],
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "cover_prompt": {"type": "string"},
        "hook_type": {"type": "string"},
        "predicted_likes": {"type": "integer"},
        "self_score": {"type": "number"},
        "self_critique": {"type": "string"},
        "rationale": {
            "type": "object",
            "properties": {
                "title_from": {"type": "string"},
                "body_from": {"type": "string"},
                "tags_from": {"type": "string"},
                "addresses_risks": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}


async def _call_synth(gen: Generator, system: str, user: str,
                       target_length: int = 600) -> dict[str, Any]:
    from ..llm_call import call_for_json
    # v0.62.17 ：synthesizer 把多份候选融合，需要的 token 比单候选多 ：
    # base 3000 + target_length × 3，保证最终稿不被截。
    max_tokens = max(3000, target_length * 3 + 1000)
    return await call_for_json(
        gen, system, user, max_tokens=max_tokens,
        tool_name="submit_synthesis", schema=_SYNTH_SCHEMA,
    )


class SynthesizerAgent(Agent):
    name = "synthesizer"

    def __init__(self, generator: Generator | None = None):
        self.generator = generator  # None → fall back to pick-best behaviour

    async def run(self, ctx: AgentContext) -> None:
        step = self._new_step(len(ctx.trace), self.name)

        live_drafts = [c for c in ctx.drafts if not c.error]
        if not live_drafts and not ctx.refined:
            step.error = "no usable candidates to synthesize"
            ctx.record(step)
            return

        # No LLM configured → legacy pick-best.
        if self.generator is None:
            chosen, why = _pick_best(ctx)
            ctx.final = chosen
            step.input_summary = "no LLM (fallback to pick-best)"
            step.output_summary = json.dumps(
                {"final_title": chosen.payload.title if chosen else None,
                 "rationale": why},
                ensure_ascii=False,
            )
            ctx.record(step)
            return

        step.llm = self.generator.model
        user = _build_user(ctx)
        t0 = self._ms()
        try:
            parsed = await _call_synth(self.generator, _SYSTEM, user,
                                        target_length=int(getattr(ctx.brief, "target_length", 600) or 600))
        except Exception as e:
            step.error = f"synthesis failed: {e!r}"
            step.latency_ms = self._ms() - t0
            chosen, why = _pick_best(ctx)
            ctx.final = chosen
            step.output_summary = f"fallback pick: {chosen.payload.title if chosen else 'none'}"
            ctx.record(step)
            return
        step.latency_ms = self._ms() - t0

        try:
            payload = CandidatePayload.from_dict(parsed)
            # carry rationale into self_critique so it persists, since DB
            # schema doesn't have a dedicated rationale column yet.
            rat = parsed.get("rationale") or {}
            extra = f"\n\n[synth rationale]\n{json.dumps(rat, ensure_ascii=False)}"
            payload.self_critique = (payload.self_critique or "") + extra
        except Exception as e:
            step.error = f"payload parse failed: {e!r}"
            chosen, why = _pick_best(ctx)
            ctx.final = chosen
            ctx.record(step)
            return

        final = GeneratedCandidate.new(
            llm=f"{self.generator.model}+synthesis",
            payload=payload,
            latency_ms=step.latency_ms,
        )
        ctx.final = final
        step.input_summary = f"fused {len(live_drafts)} drafts"
        step.output_summary = json.dumps(
            {"final_title": payload.title, "rationale": parsed.get("rationale", {})},
            ensure_ascii=False,
        )[:2000]
        ctx.record(step)


def _pick_best(ctx: AgentContext) -> tuple[GeneratedCandidate | None, str]:
    """Fallback: take the refined version if any, else the top-critic-scored draft."""
    if ctx.refined and not ctx.refined.error:
        return ctx.refined, "refined"
    best, best_score = None, -1.0
    for cand in ctx.drafts:
        if cand.error:
            continue
        crits = ctx.critiques.get(cand.candidate_id, [])
        if not crits:
            continue
        avg = sum(c.overall for c in crits) / len(crits)
        if avg > best_score:
            best, best_score = cand, avg
    if best:
        return best, f"top-critic ({best_score:.1f})"
    for cand in ctx.drafts:
        if not cand.error:
            return cand, "first-successful"
    return None, "no usable candidate"
