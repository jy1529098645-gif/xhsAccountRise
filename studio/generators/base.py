"""Generator abstract base + GeneratedCandidate dataclass.

Each LLM family (Anthropic, DeepSeek, OpenAI...) provides a subclass that:
    - knows its own model id and pricing
    - converts a structured prompt request into JSON output matching
      CandidatePayload's shape
    - returns a GeneratedCandidate on success
"""
from __future__ import annotations

import abc
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class CandidatePayload:
    """The JSON shape every generator must produce."""
    title: str
    body: str
    tags: list[str]
    cover_prompt: str
    hook_type: str
    predicted_likes: int
    self_score: float       # 0-10 confidence
    self_critique: str
    angle: str = ""         # v0.52: which angle this draft was written for

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CandidatePayload":
        return cls(
            title=str(d.get("title", "")).strip(),
            body=str(d.get("body", "")).strip(),
            tags=[str(t).strip() for t in (d.get("tags") or [])][:12],
            cover_prompt=str(d.get("cover_prompt", "")).strip(),
            hook_type=str(d.get("hook_type", "")).strip(),
            predicted_likes=int(d.get("predicted_likes") or 0),
            self_score=float(d.get("self_score") or 0),
            self_critique=str(d.get("self_critique", "")).strip(),
            angle=str(d.get("angle", "")).strip(),
        )


@dataclass
class GeneratedCandidate:
    candidate_id: str
    llm: str
    payload: CandidatePayload
    latency_ms: int
    token_usage: dict[str, int] = field(default_factory=dict)
    cost_estimate_usd: float = 0.0
    raw_response: str = ""    # raw text, for forensics
    error: str = ""           # non-empty if generation failed

    @classmethod
    def new(cls, llm: str, payload: CandidatePayload, **kw) -> "GeneratedCandidate":
        return cls(candidate_id=uuid.uuid4().hex[:16], llm=llm, payload=payload, **kw)

    @classmethod
    def failed(cls, llm: str, error: str, latency_ms: int = 0, raw: str = "") -> "GeneratedCandidate":
        return cls(
            candidate_id=uuid.uuid4().hex[:16],
            llm=llm,
            payload=CandidatePayload(title="", body="", tags=[], cover_prompt="",
                                     hook_type="", predicted_likes=0,
                                     self_score=0.0, self_critique=""),
            latency_ms=latency_ms,
            error=error,
            raw_response=raw,
        )


@dataclass
class PromptBundle:
    """What the orchestrator hands every Generator. LLM-agnostic."""
    system: str
    user: str
    expected_schema: dict[str, Any]


class Generator(abc.ABC):
    """One LLM family. Stateless w.r.t. brief; constructed per process."""

    name: str = "base"
    model: str = ""

    @abc.abstractmethod
    async def generate(self, prompt: PromptBundle) -> GeneratedCandidate:
        """Run the LLM, return a candidate. Must not raise — return failed() on errors."""
        raise NotImplementedError

    def _measure(self) -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _try_parse_json(text: str) -> dict[str, Any] | None:
        """Best-effort JSON extraction from a model reply."""
        t = text.strip()
        if not t:
            return None
        # Strip ```json fences if present.
        if t.startswith("```"):
            lines = t.split("\n", 1)
            t = lines[1] if len(lines) > 1 else ""
            if t.endswith("```"):
                t = t.rsplit("```", 1)[0]
        # Find first { and last matching }.
        start = t.find("{")
        end = t.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = t[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
