"""Planner agent — converts the final draft + corpus signals into an execution plan.

This is the third pillar (alongside Strategy + Content) the user explicitly
asked for: 给策略、计划、内容. Strategist owns strategy, Drafter+Synthesizer
own content; Planner owns the *plan* — when to post, what to post next, and
how to operate the community around this note.

Inputs:
    - the final synthesized candidate (title + body + tags + hook_type)
    - the active library's DNA timing heatmap (top time slots)
    - top user-demand phrases from comments (for follow-up topics)
    - the brief (niche, platform)

Output schema (returned in ctx.plan and the bundle):
    {
      "publish_schedule": [
        {"slot": "周三 21:00 (Beijing)", "median_likes": 14123, "why": "..."}
      ],
      "follow_up_angles": [
        {"title": "...", "angle": "教程", "hook_type": "数字型", "why": "..."}
      ],
      "engagement_tactics": ["...", "..."],
      "series_thesis": "..."
    }
"""
from __future__ import annotations

import json
from typing import Any

from ..generators.base import Generator
from .base import Agent, AgentContext


_SYSTEM = """\
你是「小红书账号运营总监」。给你一篇已经定稿的笔记 + 该账号过去爆款的发布时段统计 + 用户评论里高频问的问题。

你的任务：为这篇笔记生成一个完整的「执行计划」，不写正文。

输出三大块：

1. **publish_schedule** —— 推荐 1-3 个最佳发布时段，每个带 median likes 数据 + 为什么这个时段适合本帖（结合 hook 类型 / 主题 / 用户活跃）。

2. **follow_up_angles** —— 基于本帖 + 评论需求，给 3-5 个后续选题，每个包含：
   - title: 候选标题
   - angle: 教程 / 痛点 / 故事 / 工具评测 / 对比 / 感悟 / 数字 / 种草 / 建议
   - hook_type
   - why: 为什么接这个（吃本帖流量？补强 niche？回应用户问题？）

3. **engagement_tactics** —— 3-5 条具体的互动运营建议（如何在评论区钓互动、引私信、置顶哪条评论、用户提问如何模板化回复）。

4. **series_thesis** —— 一句话，说明这条+follow-ups 共同讲了一个什么主线故事。

输出格式：纯 JSON，不要任何额外文本。"""


def _format_timing(timing_heatmap: list[dict]) -> str:
    if not timing_heatmap:
        return "（暂无发布时段数据）"
    # Top 5 slots by median_likes where count >= 3 (filter spurious)
    valid = [c for c in timing_heatmap if c.get("count", 0) >= 3]
    if not valid:
        valid = sorted(timing_heatmap, key=lambda c: c.get("count", 0), reverse=True)[:5]
    valid.sort(key=lambda c: c.get("median_likes", 0), reverse=True)
    dow_label = ["一", "二", "三", "四", "五", "六", "日"]
    lines = []
    for c in valid[:8]:
        d = dow_label[c["dow"]]
        h = c["hour"]
        lines.append(
            f"  周{d} {h:02d}:00 — median likes {int(c.get('median_likes', 0))}"
            f" (n={c['count']})"
        )
    return "\n".join(lines)


def _format_comments(comments: list[dict]) -> str:
    if not comments:
        return "（无评论数据）"
    lines = []
    for c in comments[:12]:
        text = (c.get("content") or "").strip().replace("\n", " ")[:140]
        likes = c.get("like_count") or 0
        lines.append(f"  ({likes}👍) {text}")
    return "\n".join(lines)


def _load_timing_from_dna() -> list[dict]:
    """Pull the timing heatmap from the latest DNA artifact."""
    from .. import db
    try:
        with db.connect(read_only=True) as con:
            row = con.execute(
                "SELECT payload_json FROM studio_dna_artifacts"
                " ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return []
        artifact = json.loads(row["payload_json"])
        return artifact.get("sections", {}).get("timing", {}).get("heatmap", [])
    except Exception:
        return []


_PLAN_SCHEMA = {
    "type": "object",
    "required": ["publish_schedule", "follow_up_angles",
                 "engagement_tactics", "series_thesis"],
    "properties": {
        "publish_schedule": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slot": {"type": "string"},
                    "median_likes": {"type": "integer"},
                    "why": {"type": "string"},
                },
            },
        },
        "follow_up_angles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "angle": {"type": "string"},
                    "hook_type": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        },
        "engagement_tactics": {"type": "array", "items": {"type": "string"}},
        "series_thesis": {"type": "string"},
    },
}


async def _call_for_plan(gen: Generator, system: str, user: str) -> dict[str, Any]:
    from ..llm_call import call_for_json
    return await call_for_json(
        gen, system, user, max_tokens=2048,
        tool_name="submit_plan", schema=_PLAN_SCHEMA,
    )


def _coerce_str_list(value: Any) -> list[str]:
    """Normalise an LLM-returned "list of plain items" into list[str].

    Non-Claude models (DeepSeek, GPT in json-mode) don't honor the schema
    as strictly as Claude's tool_use does. `engagement_tactics` typed as
    `array<string>` can come back as `[{tactic: "..."}]`, `{tactics: [...]}`,
    or even a bare string. Coerce defensively so the frontend can rely on
    `string[]` and `.map()` never crashes.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_coerce_str_list(item))
        return out
    if isinstance(value, dict):
        for k in ("tactic", "text", "content", "item", "value"):
            if isinstance(value.get(k), str) and value[k].strip():
                return [value[k]]
        for v in value.values():
            if isinstance(v, list):
                return _coerce_str_list(v)
        try:
            return [json.dumps(value, ensure_ascii=False)]
        except Exception:
            return []
    return [str(value)]


def _coerce_object_list(value: Any, *, object_keys: tuple[str, ...]) -> list[dict]:
    """Normalise an LLM-returned "list of objects" into list[dict].

    Same contract as `_coerce_str_list`, but for object-list cases
    (publish_schedule, follow_up_angles). String items are wrapped as
    `{first_key: <string>}`; other shapes are silently dropped — better
    than letting the UI crash on `.map`.
    """
    if value is None:
        return []
    if isinstance(value, list):
        out: list[dict] = []
        for item in value:
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, str) and item.strip():
                out.append({object_keys[0]: item})
        return out
    if isinstance(value, dict):
        for v in value.values():
            if isinstance(v, list):
                return _coerce_object_list(v, object_keys=object_keys)
        return [value]
    return []


class PlannerAgent(Agent):
    name = "planner"

    def __init__(self, generator: Generator):
        self.generator = generator

    async def run(self, ctx: AgentContext) -> None:
        step = self._new_step(len(ctx.trace), self.name)
        step.llm = self.generator.model

        if ctx.final is None or ctx.final.error:
            step.error = "no final draft → skipping planner"
            ctx.record(step)
            return

        timing = _load_timing_from_dna()
        final = ctx.final.payload
        platform_label = {
            "xiaohongshu": "小红书", "douyin": "抖音", "kuaishou": "快手",
            "bilibili": "B站", "youtube": "YouTube", "reddit": "Reddit",
            "x": "X / Twitter", "other": "通用",
        }.get(ctx.brief.platform, ctx.brief.platform)

        user = (
            f"【目标平台】{platform_label}\n"
            f"【已定稿笔记】\n"
            f"标题: {final.title}\n"
            f"hook 类型: {final.hook_type}\n"
            f"tags: {final.tags}\n"
            f"正文（节选）: {final.body[:600]}\n\n"
            f"【brief】主题={ctx.brief.topic} · 角度={'/'.join(ctx.brief.all_angles())}"
            f" · niche={ctx.brief.niche or '无'}\n\n"
            f"【该平台过往爆款发布时段统计（按 median likes 排序）】\n"
            f"{_format_timing(timing)}\n\n"
            f"【相关用户原话评论（找后续选题灵感）】\n"
            f"{_format_comments(ctx.comments)}\n\n"
            "请按 system 给的 schema 输出执行计划 JSON。"
        )

        t0 = self._ms()
        try:
            plan = await _call_for_plan(self.generator, _SYSTEM, user)
        except Exception as e:
            step.error = f"planner failed: {e!r}"
            step.latency_ms = self._ms() - t0
            ctx.record(step)
            return

        step.latency_ms = self._ms() - t0
        # Normalise every array-typed field before stashing — non-Claude
        # models don't honor the schema strictly and can return strings /
        # objects where arrays are expected. Catching it here means the
        # frontend (Composer/DraftDetail PlanCard) never has to defend
        # against weird shapes at render time.
        plan["engagement_tactics"] = _coerce_str_list(plan.get("engagement_tactics"))
        plan["publish_schedule"] = _coerce_object_list(
            plan.get("publish_schedule"),
            object_keys=("slot", "median_likes", "why"),
        )
        plan["follow_up_angles"] = _coerce_object_list(
            plan.get("follow_up_angles"),
            object_keys=("title", "angle", "hook_type", "why"),
        )
        if not isinstance(plan.get("series_thesis"), str):
            plan["series_thesis"] = str(plan.get("series_thesis") or "")
        # Stash on ctx for the pipeline to surface in the bundle.
        setattr(ctx, "plan", plan)
        step.input_summary = (
            f"final='{final.title[:30]}' · {len(timing)} timing slots · "
            f"{len(ctx.comments)} comments"
        )
        out_summary = {
            "publish_slots": len(plan.get("publish_schedule", [])),
            "follow_ups": len(plan.get("follow_up_angles", [])),
            "tactics": len(plan.get("engagement_tactics", [])),
        }
        step.output_summary = json.dumps(out_summary, ensure_ascii=False)
        ctx.record(step)
