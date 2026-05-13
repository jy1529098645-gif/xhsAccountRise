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

import json
import re
from typing import Any

from .generators.base import Generator


# OpenAI fallback chain — only used when the configured model isn't accessible.
# gpt-4o is universally available without org verification.
_OPENAI_FALLBACK_CHAIN: tuple[str, ...] = ("gpt-4o",)


def _mask_secret(text: str) -> str:
    """Strip secret tokens from error text."""
    return re.sub(r"(sk-(?:ant-|proj-)?\w{6})\w+", r"\1***",
                  re.sub(r"(gho_)\w+", r"\1***", text or ""))


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
        if tool_name and schema:
            resp = await client.messages.create(
                model=gen.model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
                tools=[{
                    "name": tool_name,
                    "description": "Submit structured JSON.",
                    "input_schema": schema,
                }],
                tool_choice={"type": "tool", "name": tool_name},
            )
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    return block.input
            raise RuntimeError("no tool_use block in Claude response")
        # Plain JSON via prose
        resp = await client.messages.create(
            model=gen.model, max_tokens=max_tokens, system=system,
            messages=[{
                "role": "user",
                "content": user + "\n\n严格输出一个 JSON 对象，不要任何其它文字。",
            }],
        )
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
        try:
            try:
                resp = await client.chat.completions.create(
                    model=model, messages=messages,
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                )
            except Exception as fmt_err:
                # Some models don't accept response_format; retry without it.
                msg = str(fmt_err)
                if "response_format" in msg or "json_object" in msg:
                    resp = await client.chat.completions.create(
                        model=model, messages=messages, max_tokens=max_tokens,
                    )
                else:
                    raise
            # If we ended up using a fallback, pin it for the rest of the run
            if model != gen.model:
                gen.model = model
            return _coerce_json(resp.choices[0].message.content or "{}")
        except Exception as e:
            last_err = e
            if _is_model_access_error(e) and len(models_to_try) > 1:
                continue  # try next fallback
            raise RuntimeError(_mask_secret(f"{family} call failed: {e}"))

    raise RuntimeError(
        _mask_secret(f"all models exhausted ({models_to_try!r}): {last_err}")
    )
