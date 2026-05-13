"""Strategist agent: enriches the user brief with a concrete strategy.

Inputs: brief, latest DNA artifact summary, hook templates.
Output (written to ctx.strategy):
    {
      "recommended_hook": "数字型" | ...,
      "opening_hook": "<one-line hook for the very first sentence>",
      "structure": ["<step 1>", "<step 2>", ...],
      "cta_phrase": "<exact closing line>",
      "tone": "<one-paragraph tonal direction>",
      "avoid": ["<thing to avoid 1>", ...]
    }

This is the only "thinking" step that runs once per brief, before any drafters
fire. Doing it up-front lets every drafter share the same strategic frame —
otherwise each LLM invents its own and the cohort drifts apart.
"""
from __future__ import annotations

import json
from typing import Any

from ..generators.base import Generator, PromptBundle
from .base import Agent, AgentContext, TraceStep


_SYSTEM = """\
你是「小红书爆款战略师」。给定一个内容简报和该赛道的爆款数据，输出可执行的写稿策略。

要求：
- 不要写正文，只输出策略。后面有别的 agent 据此动笔。
- hook 类型必须从给定的清单里选。
- opening_hook 必须是一句话能直接放进标题或正文第一句的钩子。
- structure 是 4-6 个分点的骨架（不是详写）。
- 避坑列表针对该 brief 容易翻车的点（学术腔、虚假承诺、品牌错配）。

输出格式：JSON，键如下，不要任何额外文本：
{
  "recommended_hook": "<hook 类型>",
  "opening_hook": "<一句话钩子>",
  "structure": ["<分点1>", "<分点2>", "..."],
  "cta_phrase": "<结尾引导文案>",
  "tone": "<一段话的语气指导，30-80 字>",
  "avoid": ["<避坑1>", "<避坑2>"]
}"""


def _user(brief, hooks: list[dict[str, Any]], top_refs: list[dict[str, Any]]) -> str:
    hooks_block = "\n".join(
        f"- {h['category']}（avg {int(h.get('median_likes') or h.get('avg_likes') or 0)} likes, "
        f"n={h.get('count') or h.get('sample_size') or 0}）{h.get('pattern', '')}"
        for h in hooks
    ) or "（暂无）"
    refs_block = "\n".join(
        f"- [{r.get('liked_count', 0)} likes] {r.get('title', '')}"
        for r in top_refs[:6]
    ) or "（暂无）"
    cta = {"none": "无引导", "soft": "轻引导评论收藏", "strong": "强引导私信/求资源"}.get(
        brief.cta_strength, brief.cta_strength
    )
    return (
        f"【brief】\n主题：{brief.topic}\n角度：{brief.angle}\n"
        f"目标字数：{brief.target_length}\nCTA：{cta}\n"
        f"赛道：{brief.niche or '未指定'}\n"
        f"附加要求：{brief.extra_constraints or '无'}\n\n"
        f"【可选 hook 类型 + 该赛道平均表现】\n{hooks_block}\n\n"
        f"【同主题已知爆款参考】\n{refs_block}\n\n"
        "请按 system 给的 schema 输出策略 JSON。"
    )


class StrategistAgent(Agent):
    name = "strategist"

    def __init__(self, generator: Generator):
        self.generator = generator

    async def run(self, ctx: AgentContext) -> None:
        step = self._new_step(len(ctx.trace), self.name)
        step.llm = self.generator.model

        user = _user(ctx.brief, ctx.hooks or [], ctx.refs or [])
        bundle = PromptBundle(system=_SYSTEM, user=user, expected_schema={})

        t0 = self._ms()
        try:
            # Hijack the generator: same network plumbing, different prompt
            # shape (we want JSON, not a CandidatePayload). Use the SDK directly.
            strategy = await _call_for_json(self.generator, _SYSTEM, user)
        except Exception as e:
            step.latency_ms = self._ms() - t0
            step.error = f"strategist failed: {e!r}"
            step.output_summary = step.error
            ctx.record(step)
            return
        step.latency_ms = self._ms() - t0
        step.input_summary = self._truncate(user, 1200)
        step.output_summary = self._truncate(json.dumps(strategy, ensure_ascii=False))
        step.raw_response = step.output_summary
        ctx.strategy = strategy
        ctx.record(step)


_STRATEGY_SCHEMA = {
    "type": "object",
    "properties": {
        "recommended_hook": {"type": "string"},
        "opening_hook": {"type": "string"},
        "structure": {"type": "array", "items": {"type": "string"}},
        "cta_phrase": {"type": "string"},
        "tone": {"type": "string"},
        "avoid": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["recommended_hook", "opening_hook", "structure",
                 "cta_phrase", "tone", "avoid"],
}


async def _call_for_json(gen: Generator, system: str, user: str) -> dict[str, Any]:
    """Shared JSON call (Claude tool_use / OpenAI JSON mode with fallback)."""
    from ..llm_call import call_for_json
    return await call_for_json(
        gen, system, user, max_tokens=1024,
        tool_name="submit_strategy", schema=_STRATEGY_SCHEMA,
    )
