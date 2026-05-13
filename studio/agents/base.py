"""Agent primitives.

Each agent is a stateless callable that reads/writes a shared AgentContext.
The pipeline runs agents sequentially (with sub-fanout inside agents like
DrafterPool / CriticPool) and the trace is recorded per step for observability.

Agents that need LLMs accept a `generator` (single LLM) or `generators`
(parallel pool) in their constructor — this keeps the pipeline composable:
the same agent class can be wired with Claude / DeepSeek / GPT.
"""
from __future__ import annotations

import abc
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..brief import Brief
from ..generators.base import GeneratedCandidate


@dataclass
class TraceStep:
    step_index: int
    agent_name: str
    llm: str = ""
    input_summary: str = ""
    output_summary: str = ""
    latency_ms: int = 0
    cost_estimate_usd: float = 0.0
    error: str = ""
    raw_response: str = ""


@dataclass
class Critique:
    critique_id: str
    candidate_id: str
    critic_llm: str
    scores: dict[str, float]            # hook / language_fit / shareability / brand_safety
    risk_flags: list[str]
    suggestion: str
    overall: float                       # weighted aggregate


@dataclass
class AgentContext:
    brief: Brief
    library_id: str = ""
    strategy: dict[str, Any] = field(default_factory=dict)
    refs: list[dict[str, Any]] = field(default_factory=list)
    comments: list[dict[str, Any]] = field(default_factory=list)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    drafts: list[GeneratedCandidate] = field(default_factory=list)
    critiques: dict[str, list[Critique]] = field(default_factory=dict)
    refined: GeneratedCandidate | None = None
    final: GeneratedCandidate | None = None
    plan: dict[str, Any] = field(default_factory=dict)
    trace: list[TraceStep] = field(default_factory=list)
    started_at: int = field(default_factory=lambda: int(time.time()))

    def record(self, step: TraceStep) -> None:
        self.trace.append(step)

    def total_cost(self) -> float:
        return round(sum(s.cost_estimate_usd for s in self.trace), 4)

    def candidate_by_id(self, candidate_id: str) -> GeneratedCandidate | None:
        for c in self.drafts:
            if c.candidate_id == candidate_id:
                return c
        if self.refined and self.refined.candidate_id == candidate_id:
            return self.refined
        if self.final and self.final.candidate_id == candidate_id:
            return self.final
        return None


class Agent(abc.ABC):
    name: str = "agent"

    @abc.abstractmethod
    async def run(self, ctx: AgentContext) -> None:
        """Mutate ctx in place. Append a TraceStep before returning."""
        raise NotImplementedError

    @staticmethod
    def _ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _truncate(text: str, limit: int = 4000) -> str:
        if not text:
            return ""
        return text if len(text) <= limit else text[:limit] + "…"

    @staticmethod
    def _new_step(step_index: int, name: str) -> TraceStep:
        return TraceStep(step_index=step_index, agent_name=name)
