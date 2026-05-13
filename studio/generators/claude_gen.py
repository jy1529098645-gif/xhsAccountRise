"""Anthropic Claude adapter. Uses tool_use to enforce JSON output."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from .. import config
from .base import (
    CandidatePayload,
    GeneratedCandidate,
    Generator,
    PromptBundle,
)


# Per-1M-token prices (USD) — rough, used for cost_estimate only.
_PRICE: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}


class ClaudeGenerator(Generator):
    def __init__(self, model: str | None = None):
        self.name = "claude"
        self.model = model or os.environ.get(
            "ANTHROPIC_MODEL_OPUS", "claude-opus-4-7"
        )
        self._client = None  # lazy

    def _ensure_client(self):
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as e:
                raise RuntimeError(
                    "anthropic SDK not installed. Run `pip install anthropic`."
                ) from e
            key = config.anthropic_key()
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY missing from environment.")
            self._client = AsyncAnthropic(api_key=key)
        return self._client

    async def generate(self, prompt: PromptBundle) -> GeneratedCandidate:
        t0 = self._measure()
        try:
            client = self._ensure_client()
        except Exception as e:
            return GeneratedCandidate.failed(self.model, str(e))

        tool = {
            "name": "submit_candidate",
            "description": "Submit one xhs note draft candidate.",
            "input_schema": prompt.expected_schema,
        }

        try:
            resp = await client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=prompt.system,
                messages=[{"role": "user", "content": prompt.user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": "submit_candidate"},
            )
        except Exception as e:
            return GeneratedCandidate.failed(
                self.model, f"api error: {e}", latency_ms=self._measure() - t0
            )

        latency = self._measure() - t0

        tool_input: dict[str, Any] | None = None
        raw_text_parts: list[str] = []
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                tool_input = block.input
            elif getattr(block, "type", None) == "text":
                raw_text_parts.append(block.text)
        raw = "\n".join(raw_text_parts) if raw_text_parts else str(tool_input)

        if not tool_input:
            return GeneratedCandidate.failed(
                self.model, "no tool_use block in response", latency, raw
            )

        try:
            payload = CandidatePayload.from_dict(tool_input)
        except Exception as e:
            return GeneratedCandidate.failed(
                self.model, f"payload parse failed: {e}", latency, raw
            )

        usage = getattr(resp, "usage", None)
        token_in = getattr(usage, "input_tokens", 0) if usage else 0
        token_out = getattr(usage, "output_tokens", 0) if usage else 0
        in_p, out_p = _PRICE.get(self.model, (0.0, 0.0))
        cost = (token_in / 1_000_000) * in_p + (token_out / 1_000_000) * out_p

        return GeneratedCandidate.new(
            llm=self.model,
            payload=payload,
            latency_ms=latency,
            token_usage={"input": token_in, "output": token_out},
            cost_estimate_usd=round(cost, 4),
            raw_response=raw[:2000],
        )
