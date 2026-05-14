"""Compliance scan + safe-rewrite.

Public API:
    check_text(text, where='body') -> list[Hit]
        Scan a single string. `where` is metadata ('title'|'body'|'tags'|...)
        used by the UI to show "标题里命中：…".

    rewrite_safe(text, hits) -> str
        Apply each hit's safe_alternative substitution in reverse order so
        earlier spans don't shift later spans' offsets.

    check_candidate(payload) -> dict
        Bundle title + body + tags + cover_prompt + self_critique into one
        sweep. Returns {severity, hits, hit_count, by_severity} ready for
        persistence to studio_compliance_checks.

    list_redlines() -> list[dict]
        For the rules-catalogue endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable

from .redlines import REDLINES, RedlineRule, all_compiled, as_dicts


Severity = str  # 'pass' | 'warn' | 'block'

# Pre-compile at import — there's only one rule set per process.
_COMPILED = all_compiled()


@dataclass
class Hit:
    term: str               # the exact substring that matched
    rule_id: str
    category: str
    severity: str           # 'block' | 'warn'
    span_start: int         # 0-based char offset in the scanned text
    span_end: int
    where: str              # 'title' | 'body' | 'tags' | 'cover_prompt' | etc.
    safe_alternative: str
    rationale: str

    def to_dict(self) -> dict:
        return asdict(self)


def check_text(text: str, where: str = "body") -> list[Hit]:
    """Scan a single string. Empty / None text → []."""
    if not text:
        return []
    out: list[Hit] = []
    for rule, patterns in _COMPILED:
        for pat in patterns:
            for m in pat.finditer(text):
                out.append(Hit(
                    term=m.group(0),
                    rule_id=rule.rule_id,
                    category=rule.category,
                    severity=rule.severity,
                    span_start=m.start(),
                    span_end=m.end(),
                    where=where,
                    safe_alternative=rule.safe_alternative,
                    rationale=rule.rationale,
                ))
    # Deduplicate: when two rules match the exact same span, keep the harsher
    # one. (block > warn). Otherwise keep both — overlapping spans usually
    # signal different issues and the UI can show both highlights.
    seen: dict[tuple[int, int], Hit] = {}
    for h in out:
        key = (h.span_start, h.span_end)
        prev = seen.get(key)
        if prev is None or (prev.severity == "warn" and h.severity == "block"):
            seen[key] = h
    return sorted(seen.values(), key=lambda x: x.span_start)


def rewrite_safe(text: str, hits: Iterable[Hit]) -> str:
    """Apply safe_alternative substitutions in reverse order so offsets stay
    valid. Returns the modified string. If hits is empty → returns text as-is.
    """
    text = text or ""
    sorted_hits = sorted(hits, key=lambda h: h.span_start, reverse=True)
    for h in sorted_hits:
        text = text[:h.span_start] + h.safe_alternative + text[h.span_end:]
    return text


def check_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    """Scan title + body + tags + cover_prompt + self_critique.

    Returns:
        {
          "severity":  'pass'|'warn'|'block',
          "hits":       list[dict],
          "hit_count":  int,
          "by_severity": {block: int, warn: int},
        }
    """
    title = (payload.get("title") or "")
    body  = (payload.get("body") or "")
    tags  = payload.get("tags") or []
    cover = (payload.get("cover_prompt") or "")
    crit  = (payload.get("self_critique") or "")

    hits: list[Hit] = []
    hits += check_text(title, where="title")
    hits += check_text(body,  where="body")
    for i, t in enumerate(tags):
        hits += check_text(str(t), where=f"tag[{i}]")
    hits += check_text(cover, where="cover_prompt")
    # self_critique is for our pipeline's eyes only — not user-facing — so
    # only flag the most severe category there; warn-level hits inside the
    # critique are noise (the model talks about risks).
    crit_hits = [h for h in check_text(crit, where="self_critique") if h.severity == "block"]
    hits += crit_hits

    blocks = sum(1 for h in hits if h.severity == "block")
    warns  = sum(1 for h in hits if h.severity == "warn")
    severity = "block" if blocks else ("warn" if warns else "pass")
    return {
        "severity": severity,
        "hits": [h.to_dict() for h in hits],
        "hit_count": len(hits),
        "by_severity": {"block": blocks, "warn": warns},
    }


def list_redlines() -> list[dict]:
    return as_dicts()
