"""Synthesizer agent: picks the final candidate.

Default strategy: if a Refiner produced output, that becomes `final`. Else
falls back to the highest-overall critic-scored draft.

This is intentionally simple — the heavy lifting is the Refiner. A future
version could cross-pollinate (best title from A, best body from B), but for
v0.1 the editorial cost outweighs the gain.
"""
from __future__ import annotations

import json

from .base import Agent, AgentContext


class SynthesizerAgent(Agent):
    name = "synthesizer"

    async def run(self, ctx: AgentContext) -> None:
        step = self._new_step(len(ctx.trace), self.name)
        chosen = ctx.refined
        rationale = "refined" if chosen else None

        if chosen is None:
            best, best_score = None, -1.0
            for cand in ctx.drafts:
                if cand.error:
                    continue
                crits = ctx.critiques.get(cand.candidate_id, [])
                if not crits:
                    continue
                avg = sum(c.overall for c in crits) / len(crits)
                if avg > best_score:
                    best_score, best = avg, cand
            chosen = best
            rationale = f"top-critic ({best_score:.1f})" if best else None

        if chosen is None:
            # Final fallback — pick any successful draft.
            for cand in ctx.drafts:
                if not cand.error:
                    chosen = cand
                    rationale = "first-successful"
                    break

        ctx.final = chosen
        if chosen:
            step.input_summary = rationale
            step.output_summary = json.dumps(
                {"final_title": chosen.payload.title}, ensure_ascii=False
            )
        else:
            step.error = "no usable candidate"
        ctx.record(step)
