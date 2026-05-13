"""Researcher agent: no LLM — pure RAG.

Reads ctx.brief, populates ctx.refs / ctx.comments / ctx.hooks via the existing
retrieval pipeline. Separated as an agent (not just a function call) so it
shows up in the trace timeline and can be swapped with a smarter retriever
(vector embeddings) later.
"""
from __future__ import annotations

import json

from ..rag import retrieve
from .base import Agent, AgentContext


class ResearcherAgent(Agent):
    name = "researcher"

    def __init__(self, k_refs: int = 8, n_comments: int = 15, top_hooks: int = 6):
        self.k_refs = k_refs
        self.n_comments = n_comments
        self.top_hooks = top_hooks

    async def run(self, ctx: AgentContext) -> None:
        step = self._new_step(len(ctx.trace), self.name)
        t0 = self._ms()
        try:
            res = retrieve.retrieve_for_brief(
                ctx.brief.topic,
                k_notes=self.k_refs,
                n_comments=self.n_comments,
            )
        except Exception as e:
            step.error = f"retrieve failed: {e!r}"
            step.latency_ms = self._ms() - t0
            ctx.record(step)
            return
        ctx.refs = res["refs"]
        ctx.comments = res["comments"]
        ctx.hooks = res["hooks"][: self.top_hooks]
        step.latency_ms = self._ms() - t0
        step.input_summary = ctx.brief.topic
        step.output_summary = json.dumps(
            {
                "refs": [r["title"] for r in ctx.refs],
                "comments_n": len(ctx.comments),
                "hooks": [h["category"] for h in ctx.hooks],
            },
            ensure_ascii=False,
        )
        ctx.record(step)
