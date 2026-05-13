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


# Order of preferred OpenAI models if a 404 (org-not-verified for gpt-5)
# kicks in. gpt-4o is universally available without organization verification.
_OPENAI_FALLBACK_CHAIN = ("gpt-4o",)


class OpenAIGenerator(Generator):
    def __init__(self, model: str | None = None):
        self.name = "openai"
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-5")
        self._client = None
        # Mutable: if the configured model 404s with org-verification, we
        # silently swap to a known-good fallback for the rest of the process.
        self._effective_model: str | None = None

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

    def _model_chain(self) -> list[str]:
        """Models to try, in order. Primary first, then fallbacks."""
        if self._effective_model:
            return [self._effective_model]
        return [self.model, *(m for m in _OPENAI_FALLBACK_CHAIN if m != self.model)]

    def _is_model_access_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return ("must be verified" in msg
                or "does not exist" in msg
                or "model_not_found" in msg
                or "the model `" in msg and "not found" in msg
                or "404" in msg)

    async def _create_chat(self, client, model: str, messages: list, *,
                           json_mode: bool):
        """Try to call OpenAI with optional JSON mode; falls back if model
        doesn't support response_format."""
        try:
            if json_mode:
                return await client.chat.completions.create(
                    model=model, messages=messages,
                    response_format={"type": "json_object"},
                )
            return await client.chat.completions.create(model=model, messages=messages)
        except Exception as e:
            err = str(e)
            if json_mode and ("response_format" in err or "json_object" in err):
                return await client.chat.completions.create(model=model, messages=messages)
            raise

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
        messages = [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": user_with_hint},
        ]

        resp = None
        used_model: str | None = None
        last_err: Exception | None = None
        for model in self._model_chain():
            try:
                resp = await self._create_chat(client, model, messages, json_mode=True)
                used_model = model
                if self._effective_model is None and model != self.model:
                    self._effective_model = model
                break
            except Exception as e:
                last_err = e
                if not self._is_model_access_error(e):
                    # Real error (timeout / quota / etc) — don't try fallbacks
                    return GeneratedCandidate.failed(
                        self.model, f"api error: {e}",
                        latency_ms=self._measure() - t0,
                    )
                # else: 404/access error → try next model in the chain
                continue

        if resp is None:
            return GeneratedCandidate.failed(
                self.model,
                f"all OpenAI models in fallback chain failed: {last_err}",
                latency_ms=self._measure() - t0,
            )

        self.model = used_model or self.model  # remember which one we used

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
