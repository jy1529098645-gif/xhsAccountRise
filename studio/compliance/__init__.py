"""Hard compliance gate for generated drafts.

The Critic + Synthesizer agents already do *soft* compliance (LLM judgment via
risk_flags), but soft alone is too loose: in fast_mode the critic is skipped
entirely, and even when it runs the LLM occasionally misses red-line phrases
that the策略报告 6.4 雷区清单 explicitly bans.

This module is the deterministic backstop: a hardcoded dictionary of banned
terms + safe replacements, scanned via regex over both title and body. Two
hit severities:
    - 'block' : phrases that mean an instant ban / shadow-ban on the platform
                (代写 / 包过 / 降到 0 / 破解 Turnitin etc.). Pipeline marks
                severity='block' on the candidate; UI shows a red blocker
                modal before mark-published.
    - 'warn'  : softer red flags worth a yellow highlight but not blocking
                (specific brand mentions / absolute claims / extreme numbers).

Callers:
    - studio.agents.pipeline._persist  → check the synthesized final + each
      candidate after Compose, write studio_compliance_checks rows.
    - API /api/compliance/check        → ad-hoc check from frontend on edit.
    - API /api/compliance/rewrite      → return a safe-rewritten string by
      substituting each hit with its first safe_alternative.
"""
from .check import (
    Hit,
    Severity,
    check_text,
    rewrite_safe,
    check_candidate,
    list_redlines,
)
from .redlines import REDLINES, RedlineRule

__all__ = [
    "Hit", "Severity", "check_text", "rewrite_safe", "check_candidate",
    "list_redlines", "REDLINES", "RedlineRule",
]
