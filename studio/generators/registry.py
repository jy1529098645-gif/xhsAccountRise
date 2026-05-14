"""Lookup table: --llms claude,deepseek → list[Generator]."""
from __future__ import annotations

from .base import Generator
from .claude_gen import ClaudeGenerator
from .deepseek_gen import DeepSeekGenerator
from .openai_gen import OpenAIGenerator


def build(spec: str) -> list[Generator]:
    """Parse '--llms' spec into Generator instances.

    Examples:
        "claude"                       → [ClaudeGenerator(default opus)]
        "claude:sonnet"                → [ClaudeGenerator(model=sonnet)]
        "claude,deepseek"              → [Claude, DeepSeek]
        "claude:opus,claude:sonnet"    → both Claude variants
    """
    out: list[Generator] = []
    for raw in spec.split(","):
        raw = raw.strip()
        if not raw:
            continue
        family, _, variant = raw.partition(":")
        family = family.lower()
        if family == "claude":
            if variant == "sonnet":
                out.append(ClaudeGenerator(model="claude-sonnet-4-6"))
            elif variant == "haiku":
                out.append(ClaudeGenerator(model="claude-haiku-4-5-20251001"))
            elif variant in ("", "opus"):
                out.append(ClaudeGenerator(model="claude-opus-4-7"))
            else:
                out.append(ClaudeGenerator(model=variant))
        elif family == "deepseek":
            if variant in ("", "chat"):
                out.append(DeepSeekGenerator(model="deepseek-chat"))
            elif variant == "reasoner":
                out.append(DeepSeekGenerator(model="deepseek-reasoner"))
            else:
                out.append(DeepSeekGenerator(model=variant))
        elif family in ("openai", "gpt"):
            if variant == "":
                out.append(OpenAIGenerator())
            else:
                out.append(OpenAIGenerator(model=variant))
        else:
            raise ValueError(f"unknown LLM family: {family}")
    return out


DEFAULT_SPEC = "openai:gpt-4o,deepseek"
