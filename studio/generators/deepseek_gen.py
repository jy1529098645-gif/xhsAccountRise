"""DeepSeek adapter via the OpenAI-compatible API.

DeepSeek supports response_format={"type": "json_object"} so we get structured
JSON without function-calling gymnastics.
"""
from __future__ import annotations

import os
from typing import Any

from .. import config
from .base import (
    CandidatePayload,
    GeneratedCandidate,
    Generator,
    PromptBundle,
)


# DeepSeek public pricing (per 1M tokens, cache-miss). Used for cost_estimate only.
_PRICE: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
}


class DeepSeekGenerator(Generator):
    def __init__(self, model: str | None = None):
        self.name = "deepseek"
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as e:
                raise RuntimeError(
                    "openai SDK not installed. Run `pip install openai`."
                ) from e
            key = config.deepseek_key()
            if not key:
                raise RuntimeError("DEEPSEEK_API_KEY missing from environment.")
            base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            self._client = AsyncOpenAI(api_key=key, base_url=base)
        return self._client

    async def generate(self, prompt: PromptBundle) -> GeneratedCandidate:
        t0 = self._measure()
        try:
            client = self._ensure_client()
        except Exception as e:
            return GeneratedCandidate.failed(self.model, str(e))

        # Append schema reminder so JSON mode produces our keys; DeepSeek's
        # JSON mode does not enforce schemas, it only guarantees valid JSON.
        user_with_hint = (
            f"{prompt.user}\n\n"
            "请直接输出符合上方 schema 的 JSON 对象，不要任何额外文本。"
        )

        try:
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": user_with_hint},
                ],
                response_format={"type": "json_object"},
                max_tokens=prompt.max_tokens,
                temperature=0.85,
            )
        except Exception as e:
            return GeneratedCandidate.failed(
                self.model, f"api error: {e}", latency_ms=self._measure() - t0
            )

        latency = self._measure() - t0
        raw = resp.choices[0].message.content or ""
        parsed = self._try_parse_json(raw)
        if parsed is None:
            return GeneratedCandidate.failed(
                self.model, "json parse failed", latency, raw
            )

        try:
            payload = CandidatePayload.from_dict(parsed)
        except Exception as e:
            return GeneratedCandidate.failed(
                self.model, f"payload parse failed: {e}", latency, raw
            )

        usage = resp.usage
        token_in = getattr(usage, "prompt_tokens", 0) if usage else 0
        token_out = getattr(usage, "completion_tokens", 0) if usage else 0
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
