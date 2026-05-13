"""OpenAI adapter via the official SDK. JSON response_format for structure."""
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


# Rough per-1M-token prices for estimation only.
_PRICE: dict[str, tuple[float, float]] = {
    "gpt-5": (2.50, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}


class OpenAIGenerator(Generator):
    def __init__(self, model: str | None = None):
        self.name = "openai"
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as e:
                raise RuntimeError(
                    "openai SDK not installed. Run `pip install openai`."
                ) from e
            key = config.openai_key()
            if not key:
                raise RuntimeError("OPENAI_API_KEY missing from environment.")
            base = os.environ.get("OPENAI_BASE_URL")
            self._client = AsyncOpenAI(api_key=key, base_url=base) if base \
                else AsyncOpenAI(api_key=key)
        return self._client

    async def generate(self, prompt: PromptBundle) -> GeneratedCandidate:
        t0 = self._measure()
        try:
            client = self._ensure_client()
        except Exception as e:
            return GeneratedCandidate.failed(self.model, str(e))

        user_with_hint = (
            f"{prompt.user}\n\n"
            "请直接输出 JSON 对象（symbol: title/body/tags/cover_prompt/hook_type/"
            "predicted_likes/self_score/self_critique），不要任何额外文本。"
        )

        # gpt-5 family supports response_format json_object; for non-supporting
        # variants we fall back to plain text + best-effort parsing.
        try:
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": user_with_hint},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as e:
            # If response_format unsupported, retry without it.
            err = str(e)
            if "response_format" in err or "json_object" in err:
                try:
                    resp = await client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": prompt.system},
                            {"role": "user", "content": user_with_hint},
                        ],
                    )
                except Exception as e2:
                    return GeneratedCandidate.failed(
                        self.model, f"api error: {e2}",
                        latency_ms=self._measure() - t0,
                    )
            else:
                return GeneratedCandidate.failed(
                    self.model, f"api error: {e}",
                    latency_ms=self._measure() - t0,
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
