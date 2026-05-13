"""Critic pool: each critic LLM scores *every* drafted candidate.

Why multi-LLM critics:
    - One LLM rating its own work overstates quality.
    - Cross-LLM consensus is a much stronger signal than self_score alone.
    - Surface contradictions between critics → uncertainty signal for the
      Refiner / Synthesizer / human reviewer.

Score axes (0-10 each):
    hook            — 标题钩子强度
    language_fit    — 是否贴合下沉学生语言（避免书面语/学术腔）
    shareability    — 转发欲望（FOMO / 共鸣 / 干货）
    brand_safety    — 不虚假宣传、不踩品牌雷
    structural_clarity — 正文是否分点清晰、首尾对齐 brief

Overall = mean(scores) with brand_safety as a hard floor (if <5, overall *= 0.5).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from ..generators.base import Generator
from .base import Agent, AgentContext, Critique


_SYSTEM = """\
你是一位严苛的小红书内容审稿人，专评下沉学生赛道的稿件。

给定一份稿件草稿（含 title / body / tags / cover_prompt / hook_type），
按下列 5 个维度打分 0-10：
- hook：标题钩子是否强（数字/痛点/工具/故事）
- language_fit：语气是否够口语化、像真人留学生/研究生说的话
- shareability：用户会不会想转发或收藏
- brand_safety：是否虚假承诺、错引品牌、踩学术不端线
- structural_clarity：正文是否分点清晰、有节奏、首尾对齐主题

另外输出：
- risk_flags：list[str]，列出最严重的 1-3 个风险（无风险 → 空列表）
- suggestion：一句话改进建议（最该改的那一点）

输出格式：JSON，键如下，不要任何其他文本：
{
  "scores": {
    "hook": <0-10>,
    "language_fit": <0-10>,
    "shareability": <0-10>,
    "brand_safety": <0-10>,
    "structural_clarity": <0-10>
  },
  "risk_flags": ["<flag1>", "..."],
  "suggestion": "<single most-impactful change>"
}"""


def _format_candidate(c) -> str:
    p = c.payload
    return (
        f"title: {p.title}\n"
        f"hook_type: {p.hook_type}\n"
        f"tags: {p.tags}\n"
        f"body:\n{p.body}\n"
        f"cover_prompt: {p.cover_prompt}"
    )


def _overall(scores: dict[str, float]) -> float:
    if not scores:
        return 0.0
    avg = sum(scores.values()) / len(scores)
    safety = float(scores.get("brand_safety", 10))
    if safety < 5:
        avg *= 0.5
    return round(avg, 2)


async def _critique(
    gen: Generator,
    candidate,
    brief_topic: str,
) -> Critique:
    user = (
        f"【brief 主题】{brief_topic}\n\n"
        f"【待评候选稿件】\n{_format_candidate(candidate)}\n\n"
        "请按 system 给的 schema 输出 JSON。"
    )

    # Same trick as strategist: reuse the Generator's SDK.
    family = gen.name
    try:
        client = gen._ensure_client()  # noqa: SLF001
        if family == "claude":
            resp = await client.messages.create(
                model=gen.model,
                max_tokens=1024,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user}],
                tools=[
                    {
                        "name": "submit_critique",
                        "description": "Submit critique JSON.",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "scores": {
                                    "type": "object",
                                    "properties": {
                                        "hook": {"type": "number"},
                                        "language_fit": {"type": "number"},
                                        "shareability": {"type": "number"},
                                        "brand_safety": {"type": "number"},
                                        "structural_clarity": {"type": "number"},
                                    },
                                },
                                "risk_flags": {"type": "array", "items": {"type": "string"}},
                                "suggestion": {"type": "string"},
                            },
                            "required": ["scores", "risk_flags", "suggestion"],
                        },
                    }
                ],
                tool_choice={"type": "tool", "name": "submit_critique"},
            )
            parsed: dict[str, Any] | None = None
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    parsed = block.input
                    break
            if not parsed:
                raise RuntimeError("no tool_use in critic response")
        else:
            resp = await client.chat.completions.create(
                model=gen.model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
            )
            parsed = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        return Critique(
            critique_id=uuid.uuid4().hex[:16],
            candidate_id=candidate.candidate_id,
            critic_llm=gen.model,
            scores={},
            risk_flags=[f"critic_error: {e!r}"],
            suggestion="",
            overall=0.0,
        )

    scores_raw = parsed.get("scores") or {}
    scores = {k: float(v) for k, v in scores_raw.items() if isinstance(v, (int, float))}
    return Critique(
        critique_id=uuid.uuid4().hex[:16],
        candidate_id=candidate.candidate_id,
        critic_llm=gen.model,
        scores=scores,
        risk_flags=list(parsed.get("risk_flags") or []),
        suggestion=str(parsed.get("suggestion") or ""),
        overall=_overall(scores),
    )


class CriticPoolAgent(Agent):
    name = "critic"

    def __init__(self, critics: list[Generator]):
        if not critics:
            raise ValueError("at least one critic required")
        self.critics = critics

    async def run(self, ctx: AgentContext) -> None:
        if not ctx.drafts:
            step = self._new_step(len(ctx.trace), self.name)
            step.error = "no drafts to critique"
            ctx.record(step)
            return

        good_drafts = [c for c in ctx.drafts if not c.error]
        if not good_drafts:
            step = self._new_step(len(ctx.trace), self.name)
            step.error = "all drafts failed; skipping critique"
            ctx.record(step)
            return

        tasks = []
        for critic in self.critics:
            for cand in good_drafts:
                tasks.append(_critique(critic, cand, ctx.brief.topic))

        t0 = self._ms()
        all_critiques: list[Critique] = await asyncio.gather(*tasks)
        elapsed = self._ms() - t0

        for cr in all_critiques:
            ctx.critiques.setdefault(cr.candidate_id, []).append(cr)

        # Single rolled-up trace step (per critic LLM × candidate count).
        base_idx = len(ctx.trace)
        for i, critic in enumerate(self.critics):
            step = self._new_step(base_idx + i, f"{self.name}:{critic.name}")
            step.llm = critic.model
            step.latency_ms = elapsed
            for cand in good_drafts:
                # cost estimation per critic — coarse but useful
                pass
            step.input_summary = f"评了 {len(good_drafts)} 份候选"
            summaries = []
            for cand in good_drafts:
                crits = [c for c in ctx.critiques.get(cand.candidate_id, [])
                         if c.critic_llm == critic.model]
                if crits:
                    summaries.append({
                        "cand": cand.payload.title[:30],
                        "overall": crits[0].overall,
                        "flags": crits[0].risk_flags[:2],
                    })
            step.output_summary = json.dumps(summaries, ensure_ascii=False)
            ctx.record(step)
