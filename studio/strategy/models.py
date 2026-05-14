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
    # v0.59: 起号目标分类。8 大目标见 goals.GOAL_TYPES。决定 voice / phase
    # emphasis / product_context 是否必填 / prompt addendum。Empty = 通用（旧行为）。
    goal_type: str = ""
    # v0.61.5 ：启动阶段。"" / "auto" = AI 据 DNA + 报告自己定（默认）；
    # "cold" = 0 粉/陌生人，主营造人设痛点共鸣；
    # "warm" = 已有粉丝基础或行业资源，可强转化；
    # "hybrid" = 中间态，前期人设后期转化。注入到 SCHEDULER prompt，
    # 但仍是软建议（AI 可据其它信号微调）。
    startup_phase: str = ""
    # v0.61.13 ：内容形式偏好。"" / "auto" = AI 按 DNA 真实分布自决（默认）；
    # "tuwen_only" = 全部图文；"video_only" = 全部短视频；
    # "mixed" = 强制图文 + 视频混合，AI 定比例。注入 SCHEDULER + BODY_DRAFTER
    # prompt 作硬约束（用户明示要求时不再让 AI 推翻）。
    content_format_preference: str = ""


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
    # v0.59: which direction (out of the user's multi-selected list) this slot
    # belongs to. 0 = first chosen direction. -1/None = no direction tagged
    # (legacy or single-direction pack). The frontend uses this to render
    # color-coded badges in the schedule view so user sees主题分布。
    direction_idx: int = -1
    # v0.60: AI 的策略思路可见性。LLM 据数据 + 经验规律自己判断后，对每个 slot 解释
    # 「为什么排这一周 + 为什么这个角度」。≤40 字。让用户看得到 AI 的判断逻辑，
    # 也方便事后回看校准。Empty = 老 pack 没有该字段（向后兼容）。
    decision_rationale: str = ""
    # v0.61: publish_slot 不再是死锁的「就周三 21:00」。AI 根据 DNA 热力图给一个
    # 推荐窗口（"周三-周五任一晚 / 21:00-23:00"），告诉用户可以在这个范围内自由选发。
    # 只锁极端早晨/深夜这种明显不合适的时段。空 = 老 pack 或 AI 没给（向后兼容）。
    flexible_window: str = ""
    # v0.62 ：每个 slot 的 2-3 个替代方案。让用户选 ：同一天不同时段 / 同一时段
    # 不同角度 / 不同 content_format。每个 alt 是个 dict ：
    #   { "label": "次选 A", "publish_slot": "周四 21:00", "angle": "段子",
    #     "hook_type": "段子型", "content_format": "短视频",
    #     "outline": ["...", "..."], "why_alt": "AI 一句话说为啥这是个备选" }
    # 用户在 PackView 上选哪个 alt → goCompose 带那个 alt 的 metadata 进 Composer。
    # 空 list = 老 pack 没生成 alternatives（向后兼容）。
    alternative_versions: list[dict] = field(default_factory=list)


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
    # v0.59: multi-direction support. When user picked N directions, all of
    # them go here. Empty list = legacy single-direction pack (use chosen_direction).
    chosen_directions: list[StrategicDirection] = field(default_factory=list)

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
