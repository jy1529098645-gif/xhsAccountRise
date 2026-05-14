"""Self-learning feedback loop.

Two responsibilities:
    aggregate.py — bridge the two performance feeds (pack-level
                   studio_composer_pack_performance + per-draft
                   studio_draft_performance) into one rollup view, so
                   downstream consumers don't have to know about both.
    proposals.py — turn a retrospective's `next_cycle_recommendations` into
                   a concrete prompt-version diff, queue it for human
                   approval, and bump the active version when approved.
                   Hooks into the existing studio_prompt_versions table.

This is the "W4 prompt versioning" milestone that was scaffolded in
migration 001 (studio_prompt_versions table exists, just unused). v0.53
finally wires it.
"""
from .aggregate import (
    rollup_for_pack,
    rollup_for_project,
    list_performance_rollup,
)
from .proposals import (
    propose_from_retrospective,
    list_proposals,
    approve_proposal,
    reject_proposal,
    get_proposal,
)

__all__ = [
    "rollup_for_pack", "rollup_for_project", "list_performance_rollup",
    "propose_from_retrospective", "list_proposals",
    "approve_proposal", "reject_proposal", "get_proposal",
]
