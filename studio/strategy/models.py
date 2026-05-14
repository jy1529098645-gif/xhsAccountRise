"""Dataclasses for the 起号策略 (account launch strategy) flow.

The flow has two phases:
    1. Propose — given AccountInput + library DNA, output 3-5 distinct
       StrategicDirection options for the user to pick from.
    2. Expand — given the picked direction, generate a full StrategyPack
       (weekly themes + dated topic slots + consolidated materials).

Each StrategyPack is what the user can take and execute against.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass
class AccountInput:
    """What the user fills in on Phase 1."""
    positioning: str           # e.g. "AI 学术工具"
    target_audience: str       # e.g. "下沉学生 + 留学生 + 毕业论文党"
    cycle_weeks: int = 4       # 4 / 8 / 12
    posts_per_week: int = 3
    personal_strengths: str = ""
    constraints: str = ""
    platform: str = "xiaohongshu"
    # v0.52: angles the user wants the schedule to span. When non-empty,
    # the scheduler distributes slots across these angles roughly evenly.
    # Empty = AI picks freely (legacy behaviour).
    expected_angles: list[str] = field(default_factory=list)
    # v0.55: anchor date for the cycle. ISO 'YYYY-MM-DD'. Empty = frontend
    # picks "next Monday" as default. Backend doesn't compute on this — the
    # frontend formats each slot's real date as cycle_start + (week-1)*7 +
    # day_of_week. Stored here so it survives pack reload.
    cycle_start_date: str = ""


@dataclass
class StrategicDirection:
    """One of N candidate positioning directions proposed in Phase 1."""
    name: str                          # short label "降AI率救命包"
    positioning_statement: str         # 1-liner
    target_audience: str               # the specific sub-audience
    hook_angles: list[str] = field(default_factory=list)
    differentiator: str = ""           # vs. competitors in same niche
    risk: str = ""                     # main risk to flag
    score: float = 0.0                 # 0-10 estimated upside
    why_works: str = ""                # rationale anchored in DNA data


@dataclass
class TopicSlot:
    """A scheduled post in the cycle."""
    week: int                          # 1-N
    day_of_week: int = 0               # 0=Mon … 6=Sun (suggestion)
    publish_slot: str = ""             # human-friendly e.g. "周三 21:00"
    title: str = ""
    title_variants: list[str] = field(default_factory=list)
    angle: str = ""                    # 教程 / 痛点 / ...
    hook_type: str = ""
    outline: list[str] = field(default_factory=list)
    materials_needed: list[str] = field(default_factory=list)
    intent: str = ""                   # 拉新 / 互动 / 转化 / 沉淀
    body_draft: str = ""               # 300-600-char first-pass body the
                                       # user can tweak then hand to Composer.
    content_format: str = ""           # 图文 / 短视频 / 长视频 / 直播 / 纯文本
    # v0.55: per-slot timing rationale. Scheduler is told to pick the best
    # (day, hour) cell from the DNA heatmap that matches this slot's
    # angle/hook_type characteristics (emotional → late evening, tutorial →
    # afternoon, etc.), and to explain the choice in one sentence here.
    publish_rationale: str = ""


@dataclass
class WeekTheme:
    week: int
    theme: str
    intent: str                        # 拉新 / 互动 / 转化 / 沉淀
    notes: str = ""


@dataclass
class StrategyPack:
    pack_id: str
    library_id: str
    platform: str
    created_at: int
    input: AccountInput
    chosen_direction: StrategicDirection
    series_thesis: str = ""
    weekly_themes: list[WeekTheme] = field(default_factory=list)
    schedule: list[TopicSlot] = field(default_factory=list)
    materials_checklist: list[str] = field(default_factory=list)
    risks_and_mitigations: list[str] = field(default_factory=list)
    success_metrics: list[str] = field(default_factory=list)

    @classmethod
    def new(cls, library_id: str, platform: str, input: AccountInput,
            chosen: StrategicDirection) -> "StrategyPack":
        return cls(
            pack_id=uuid.uuid4().hex[:16],
            library_id=library_id,
            platform=platform,
            created_at=int(time.time()),
            input=input,
            chosen_direction=chosen,
        )


def to_jsonable(obj: Any) -> Any:
    """Recursive dataclass → dict (drop-in for json.dumps)."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj
