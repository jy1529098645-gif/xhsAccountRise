"""Shared LLM-call helper.

Every agent / strategy / insight module needs to issue "give me JSON" requests
to one of Claude / OpenAI / DeepSeek. They were all duplicating the same
SDK-poking code, with subtle drift between copies. This module is the single
implementation.

Key features:
    - Claude: uses tool_use to force a schema when tool_name + schema given,
      else asks for JSON in prose + coerces.
    - OpenAI / DeepSeek: uses response_format=json_object, with model fallback
      on access errors. So if .env says OPENAI_MODEL=gpt-5 but the account
      isn't org-verified, we silently retry on gpt-4o and remember the
      successful pick for the rest of the process.
    - Secrets are redacted from any error string before they're bubbled up.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from .generators.base import Generator


# OpenAI fallback chain — only used when the configured model isn't accessible.
# gpt-4o is universally available without org verification.
_OPENAI_FALLBACK_CHAIN: tuple[str, ...] = ("gpt-4o",)

# v0.62.7 ：429 rate limit retry. Multi-agent pipelines fire 3-5 LLMs in
# parallel — when one provider's RPM cap trips, the whole job dies. Soft
# retry with exponential backoff so a single throttle doesn't bring down
# Strategy expand / Composer run.
_RATE_LIMIT_RETRY_DELAYS = (3.0, 8.0, 20.0)  # seconds; total wait ≤ 31s


def _mask_secret(text: str) -> str:
    """Strip secret tokens from error text."""
    return re.sub(r"(sk-(?:ant-|proj-)?\w{6})\w+", r"\1***",
                  re.sub(r"(gho_)\w+", r"\1***", text or ""))


def _is_rate_limit_error(err: Exception) -> bool:
    """Detect transient 429 / rate-limit errors across SDKs (OpenAI/Anthropic/DeepSeek).

    Matches by string because all 3 SDKs surface the status code + reason in
    the exception str, and we don't want SDK-specific imports here.

    NOT matched ：402 Insufficient Balance, 401/403 auth errors — those are
    permanent, retrying just burns time.
    """
    s = str(err).lower()
    if "insufficient balance" in s or "insufficient_balance" in s:
        return False  # 402 余额不足，重试无意义
    if "insufficient_quota" in s:
        return False  # OpenAI quota 撞月度顶，重试无意义
    return any(t in s for t in (
        "rate limit", "rate_limit", "ratelimit",
        "too many requests",
        "429",
    ))


def _coerce_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a possibly-fenced LLM reply."""
    t = (text or "").strip()
    if t.startswith("```"):
        # Strip ```json / ``` fences
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    s, e = t.find("{"), t.rfind("}")
    if s == -1 or e == -1 or e <= s:
        return {}
    try:
        return json.loads(t[s:e + 1])
    except json.JSONDecodeError:
        return {}


def _is_model_access_error(err: Exception) -> bool:
    msg = str(err).lower()
    return any(s in msg for s in (
        "must be verified", "does not exist", "model_not_found",
        "model not found", "invalid model", "404",
    ))


async def call_for_json(
    gen: Generator,
    system: str,
    user: str,
    *,
    max_tokens: int = 3000,
    tool_name: str | None = None,
    schema: dict | None = None,
) -> dict[str, Any]:
    """Issue a JSON-output call. Handles family-specific quirks + fallback.

    Args:
        gen: Generator instance with .name in {claude,openai,deepseek}.
        system: System prompt.
        user: User message.
        max_tokens: Output cap.
        tool_name, schema: For Claude only — uses tool_use mode if both given,
            yielding strict JSON-schema enforcement. Ignored by OpenAI/DeepSeek.

    Returns: parsed JSON dict. Empty dict on irrecoverable parse failure.
    Raises: RuntimeError on irrecoverable API failure (after fallback attempts).
    """
    client = gen._ensure_client()  # noqa: SLF001 — intentional
    family = gen.name

    # ---- Anthropic / Claude ---------------------------------------------
    if family == "claude":
        async def _claude_call():
            if tool_name and schema:
                return await client.messages.create(
                    model=gen.model, max_tokens=max_tokens, system=system,
                    messages=[{"role": "user", "content": user}],
                    tools=[{
                        "name": tool_name,
                        "description": "Submit structured JSON.",
                        "input_schema": schema,
                    }],
                    tool_choice={"type": "tool", "name": tool_name},
                )
            return await client.messages.create(
                model=gen.model, max_tokens=max_tokens, system=system,
                messages=[{
                    "role": "user",
                    "content": user + "\n\n严格输出一个 JSON 对象，不要任何其它文字。",
                }],
            )
        resp = await _with_rate_limit_retry(family, _claude_call)
        if tool_name and schema:
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    return block.input
            raise RuntimeError("no tool_use block in Claude response")
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        )
        return _coerce_json(text)

    # ---- OpenAI / DeepSeek (OpenAI-compatible) -------------------------
    models_to_try: list[str] = [gen.model]
    if family in ("openai", "gpt"):
        models_to_try.extend(m for m in _OPENAI_FALLBACK_CHAIN if m != gen.model)

    last_err: Exception | None = None
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user + "\n\n严格输出 JSON 对象。"},
    ]

    for model in models_to_try:
        async def _oai_call(m=model):
            try:
                return await client.chat.completions.create(
                    model=m, messages=messages,
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                )
            except Exception as fmt_err:
                msg = str(fmt_err)
                if "response_format" in msg or "json_object" in msg:
                    return await client.chat.completions.create(
                        model=m, messages=messages, max_tokens=max_tokens,
                    )
                raise
        try:
            resp = await _with_rate_limit_retry(family, _oai_call)
            if model != gen.model:
                gen.model = model
            return _coerce_json(resp.choices[0].message.content or "{}")
        except Exception as e:
            last_err = e
            if _is_model_access_error(e) and len(models_to_try) > 1:
                continue
            raise RuntimeError(_mask_secret(f"{family} call failed: {e}"))

    raise RuntimeError(
        _mask_secret(f"all models exhausted ({models_to_try!r}): {last_err}")
    )


async def _with_rate_limit_retry(family: str, fn):
    """Retry transient 429s with exponential backoff. Other errors bubble up.

    Used by call_for_json wrappers around the actual SDK invocations. Total
    retry budget ~31s ：3s, 8s, 20s. After that the error surfaces to user
    (frontend humaniser will say 「限速、等一会再点」 instead of 「余额不足」).
    """
    last_err: Exception | None = None
    for attempt, delay in enumerate((0.0,) + _RATE_LIMIT_RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await fn()
        except Exception as e:
            last_err = e
            if not _is_rate_limit_error(e):
                raise
            if attempt == len(_RATE_LIMIT_RETRY_DELAYS):
                # exhausted retries — let the 429 bubble up so the frontend
                # gets the 「等一会」 message instead of pretending it worked
                raise
            # else loop and wait the next delay
    raise last_err if last_err else RuntimeError(f"{family} no response")
