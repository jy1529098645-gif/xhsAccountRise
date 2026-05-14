"""Iterate a strategy pack: feed performance back, get a smarter next-cycle.

User runs cycle N (using a pack we generated), then comes back with the actual
results — likes/comments/saves per post, follower delta, qualitative notes.
This module:
  1. Stores that feedback.
  2. Asks the LLM to analyse what worked / what didn't.
  3. Outputs a NEW strategy pack for cycle N+1, anchored on the lessons learned.

The new pack is a fresh studio_strategies row, linked to the parent via
parent_pack_id + iteration_n.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .. import db, library, project
from ..generators import registry
from ..llm_call import call_for_json
from .models import (AccountInput, StrategicDirection, StrategyPack, TopicSlot,
                     WeekTheme, to_jsonable)


# ---- Performance feedback CRUD --------------------------------------------

def save_performance(
    *, pack_id: str,
    raw_notes: str = "",
    per_slot: list[dict[str, Any]] | None = None,
    overall: dict[str, Any] | None = None,
) -> dict[str, Any]:
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    feedback_id = "perf_" + uuid.uuid4().hex[:14]
    now = int(time.time())
    # Resolve library_id from the pack
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT library_id FROM studio_strategies WHERE pack_id = ?",
            (pack_id,),
        ).fetchone()
    lib_id = row["library_id"] if row else None
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_strategy_performance"
            " (feedback_id, pack_id, project_id, library_id, created_at,"
            "  raw_notes, per_slot_json, overall_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                feedback_id, pack_id, pid, lib_id, now,
                raw_notes,
                json.dumps(per_slot or [], ensure_ascii=False),
                json.dumps(overall or {}, ensure_ascii=False),
            ),
        )
    return {
        "feedback_id": feedback_id, "pack_id": pack_id,
        "created_at": now,
        "per_slot": per_slot or [], "overall": overall or {},
        "raw_notes": raw_notes,
    }


def list_performance(pack_id: str) -> list[dict[str, Any]]:
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            "SELECT feedback_id, pack_id, created_at, raw_notes,"
            " per_slot_json, overall_json"
            " FROM studio_strategy_performance"
            " WHERE pack_id = ? ORDER BY created_at DESC",
            (pack_id,),
        ))
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try: d["per_slot"] = json.loads(d.pop("per_slot_json") or "[]")
        except Exception: d["per_slot"] = []
        try: d["overall"] = json.loads(d.pop("overall_json") or "{}")
        except Exception: d["overall"] = {}
        out.append(d)
    return out


# ---- Iteration: feedback + parent pack → next-cycle pack ------------------

_ITERATE_SYSTEM = """\
你是「起号策略迭代师」。用户已经按上一轮策略发了 N 篇，现在拿着真实数据回来了。请基于：

1. 上一轮策略（包括 chosen_direction、weekly_themes、schedule[N 篇标题/角度/意图]）
2. 上一轮**实际表现数据**（每篇的点赞/评论/收藏；可能还有用户自己写的复盘）
3. 平台 DNA 报告 + 用户上传的外部报告（如有）

输出**下一轮策略包**，要做到：
- **明确指出上一轮哪个角度/hook 类型/时段/选题方向真正涨粉/起量**，下一轮**加大投入**
- **明确指出哪个翻车了**（数据低 / 互动差），下一轮**砍掉或调整**
- 如果某条爆了（远超均值），**复制成功配方**：拆解它的 hook / 标题 / 大纲，下一轮排 2-3 篇同模式的衍生
- 不要重复上一轮已经发过的标题
- 周期默认沿用上一轮（用户能在下一步改）；如果数据显示频率不可持续就降一档并解释

输出 JSON（和首次策略一样的结构）：

{
  "iteration_summary": "<3-5 句话：上一轮哪些信号最强、哪些翻车了、本轮怎么调>",
  "wins_to_double_down": [
    {"signal": "<上一轮哪个东西成功了，引用数据>", "next_action": "<本轮怎么放大>"}
  ],
  "losses_to_drop": [
    {"signal": "<上一轮翻车点 + 数据>", "next_action": "<本轮怎么避>"}
  ],
  "chosen_direction": {
    "name": "<延续 or 微调的方向名>",
    "positioning_statement": "...",
    "target_audience": "...",
    "hook_angles": ["..."],
    "differentiator": "...",
    "risk": "...",
    "why_works": "<基于实测数据为什么这条路继续走>"
  },
  "series_thesis": "<下一轮主线>",
  "weekly_themes": [
    {"week": 1, "theme": "...", "intent": "拉新|互动|转化|沉淀", "notes": "..."}
  ],
  "schedule": [
    {
      "week": 1, "day_of_week": 2, "publish_slot": "周三 21:00",
      "title": "...", "title_variants": ["..."],
      "angle": "...", "hook_type": "...",
      "outline": ["...", "...", "..."],
      "materials_needed": ["..."],
      "intent": "...",
      "content_format": "图文|短视频|长视频|直播|纯文本",
      "publish_rationale": "<≤30 字 为什么这个时段>",
      "decision_rationale": "<≤40 字 为什么这周 + 这个角度，引用本轮哪个信号>",
      "alternative_versions": [
        {
          "label": "次选 A · 不同时段",
          "publish_slot": "...", "angle": "...", "hook_type": "...",
          "content_format": "...", "title": "...",
          "mini_outline": ["...", "..."],
          "why_alt": "<≤30 字 为啥这是个值得考虑的备选>"
        },
        {
          "label": "次选 B · 不同角度",
          "publish_slot": "...", "angle": "...", "hook_type": "...",
          "content_format": "...", "title": "...",
          "mini_outline": ["...", "..."],
          "why_alt": "..."
        }
      ]
    }
  ]
}

严格保证：
- schedule 长度 = 用户在 parent 中设置的 cycle_weeks × posts_per_week
- 每个 slot 必须给 **正好 2 个** alternative_versions（v0.62 架构 — 用户得 ≥ 3 选项后进 Composer 写正文）
- **这一步只输出结构 + alternatives，不要写 body_draft**（正文交 Composer 多 agent 流程逐篇写）
"""

_ITERATE_SCHEMA = {
    "type": "object",
    "required": ["chosen_direction", "schedule"],
    "properties": {
        "iteration_summary": {"type": "string"},
        "wins_to_double_down": {"type": "array", "items": {"type": "object"}},
        "losses_to_drop": {"type": "array", "items": {"type": "object"}},
        "chosen_direction": {"type": "object"},
        "series_thesis": {"type": "string"},
        "weekly_themes": {"type": "array", "items": {"type": "object"}},
        "schedule": {"type": "array", "items": {"type": "object"}},
    },
}


def _pack_summary_for_prompt(pack: dict[str, Any]) -> str:
    d = pack.get("chosen_direction") or {}
    schedule = pack.get("schedule") or []
    lines = [
        f"【上一轮方向】{d.get('name')} — {d.get('positioning_statement')}",
        f"【受众】{d.get('target_audience')}",
        f"【上一轮排了 {len(schedule)} 篇】",
    ]
    for i, s in enumerate(schedule):
        lines.append(
            f"  [{i+1}] W{s.get('week')}·D{s.get('day_of_week')} "
            f"{s.get('publish_slot') or ''} | {s.get('title')} | "
            f"angle={s.get('angle')} | hook={s.get('hook_type')} | "
            f"intent={s.get('intent')}"
        )
    return "\n".join(lines)


def _perf_block_for_prompt(feedback: dict[str, Any], schedule: list[dict]) -> str:
    parts = ["【上一轮实际表现】"]
    raw = feedback.get("raw_notes") or ""
    if raw.strip():
        parts.append(f"用户复盘 ：\n{raw.strip()}")
    overall = feedback.get("overall") or {}
    if overall:
        parts.append(f"整体 ：{json.dumps(overall, ensure_ascii=False)}")
    per_slot = feedback.get("per_slot") or []
    if per_slot:
        parts.append("逐篇数据 ：")
        for entry in per_slot:
            idx = entry.get("slot_idx")
            slot = schedule[idx] if isinstance(idx, int) and 0 <= idx < len(schedule) else {}
            metrics = {k: v for k, v in entry.items() if k != "slot_idx"}
            parts.append(
                f"  [{(idx or 0) + 1}] {slot.get('title', '?')[:40]} "
                f"({slot.get('angle','?')} / {slot.get('hook_type','?')}) → "
                f"{json.dumps(metrics, ensure_ascii=False)}"
            )
    return "\n".join(parts)


async def iterate_strategy(
    parent_pack_id: str,
    feedback_id: str,
    *,
    iterator_spec: str = "openai:gpt-4o",
) -> dict[str, Any]:
    """Build a new strategy pack from a parent + performance feedback."""
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()

    # Load parent pack.
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT input_json, pack_json, library_id, platform, iteration_n"
            " FROM studio_strategies WHERE pack_id = ?",
            (parent_pack_id,),
        ).fetchone()
    if not row:
        raise LookupError(f"parent pack not found: {parent_pack_id}")
    parent_input_data = json.loads(row["input_json"])
    parent_pack_data = json.loads(row["pack_json"] or "null")
    if not parent_pack_data:
        raise ValueError("parent pack has no expanded strategy — can't iterate yet")

    # Load feedback.
    with db.connect(read_only=True) as con:
        fb_row = con.execute(
            "SELECT raw_notes, per_slot_json, overall_json"
            " FROM studio_strategy_performance WHERE feedback_id = ?",
            (feedback_id,),
        ).fetchone()
    if not fb_row:
        raise LookupError(f"feedback row not found: {feedback_id}")
    feedback = {
        "raw_notes": fb_row["raw_notes"] or "",
        "per_slot": json.loads(fb_row["per_slot_json"] or "[]"),
        "overall": json.loads(fb_row["overall_json"] or "{}"),
    }

    # Reference reports (consensus + integrated + raw externals).
    from ..insight.pipeline import full_reference_block_for_prompt
    ref = full_reference_block_for_prompt()

    inp = AccountInput(**parent_input_data)
    user_msg = (
        _pack_summary_for_prompt(parent_pack_data)
        + "\n\n"
        + _perf_block_for_prompt(feedback, parent_pack_data.get("schedule") or [])
        + (f"\n\n【参考报告】\n{ref}" if ref else "")
        + f"\n\n【用户运营约束】 cycle_weeks={inp.cycle_weeks}, posts_per_week={inp.posts_per_week}\n"
        + "请按 system schema 输出下一轮策略包。"
    )
    gen = registry.build(iterator_spec)[0]
    parsed = await call_for_json(
        gen, _ITERATE_SYSTEM, user_msg,
        max_tokens=8000,
        tool_name="submit_iterated_strategy",
        schema=_ITERATE_SCHEMA,
    )

    # Build StrategyPack.
    cd_raw = parsed.get("chosen_direction") or {}
    chosen = StrategicDirection(
        name=str(cd_raw.get("name", "")),
        positioning_statement=str(cd_raw.get("positioning_statement", "")),
        target_audience=str(cd_raw.get("target_audience", "")),
        hook_angles=[str(x) for x in (cd_raw.get("hook_angles") or [])],
        differentiator=str(cd_raw.get("differentiator", "")),
        risk=str(cd_raw.get("risk", "")),
        why_works=str(cd_raw.get("why_works", "")),
        score=float(cd_raw.get("score", 0.0) or 0.0),
    )
    new_pack = StrategyPack.new(
        library_id=row["library_id"] or library.active_lib_id(),
        platform=row["platform"] or inp.platform,
        input=inp, chosen=chosen,
    )
    new_pack.series_thesis = str(parsed.get("series_thesis", ""))

    # Defensive coercion — same reason as pipeline.expand's defense:
    # Claude tool_use occasionally returns array items as raw strings.
    def _theme_dict(item: Any, hint: int) -> dict[str, Any]:
        return item if isinstance(item, dict) else {"week": hint, "theme": str(item)}

    def _slot_dict(item: Any) -> dict[str, Any]:
        return item if isinstance(item, dict) else {"title": str(item)}

    new_pack.weekly_themes = [
        WeekTheme(
            week=int(w.get("week", i + 1)),
            theme=str(w.get("theme", "")),
            intent=str(w.get("intent", "")),
            notes=str(w.get("notes", "")),
        )
        for i, _raw in enumerate(parsed.get("weekly_themes") or [])
        for w in [_theme_dict(_raw, i + 1)]
    ]
    schedule_raw = parsed.get("schedule") or []
    new_pack.schedule = [
        TopicSlot(
            week=int(s.get("week", 1)),
            day_of_week=int(s.get("day_of_week", 0)),
            publish_slot=str(s.get("publish_slot", "")),
            title=str(s.get("title", "")),
            title_variants=[str(x) for x in (s.get("title_variants") or [])],
            angle=str(s.get("angle", "")),
            hook_type=str(s.get("hook_type", "")),
            outline=[str(x) for x in (s.get("outline") or [])],
            materials_needed=[str(x) for x in (s.get("materials_needed") or [])],
            intent=str(s.get("intent", "")),
            content_format=str(s.get("content_format", "")),
            publish_rationale=str(s.get("publish_rationale", "")),
            decision_rationale=str(s.get("decision_rationale", "")),
            # v0.62 ：next-iteration packs must carry alternatives so SchedulePanel can
            # render 主推荐 + 2 备选 just like the first-round pack.
            alternative_versions=[a for a in (s.get("alternative_versions") or [])
                                  if isinstance(a, dict)],
        )
        for _raw in schedule_raw
        for s in [_slot_dict(_raw)]
    ]

    # v0.62 ：iterate no longer pre-generates body drafts. Same rationale as
    # pipeline.expand — Composer writes per-slot with critic + refiner. Saves
    # ~$0.30 + 2-3 min per iteration and keeps quality higher (single-slot
    # focus vs batched).

    # Persist.
    iteration_n = int(row["iteration_n"] or 1) + 1
    now = int(time.time())
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_strategies"
            " (pack_id, library_id, platform, project_id, parent_pack_id, iteration_n,"
            "  status, created_at, updated_at, input_json, directions_json,"
            "  chosen_direction_idx, pack_json, elapsed_s)"
            " VALUES (?, ?, ?, ?, ?, ?, 'expanded', ?, ?, ?, ?, ?, ?, ?)",
            (
                new_pack.pack_id, new_pack.library_id, new_pack.platform, pid,
                parent_pack_id, iteration_n,
                now, now,
                json.dumps(parent_input_data, ensure_ascii=False),
                json.dumps([to_jsonable(chosen)], ensure_ascii=False),
                0,
                json.dumps(to_jsonable(new_pack), ensure_ascii=False),
                0,
            ),
        )

    return {
        "pack_id": new_pack.pack_id,
        "parent_pack_id": parent_pack_id,
        "iteration_n": iteration_n,
        "iteration_summary": parsed.get("iteration_summary", ""),
        "wins_to_double_down": parsed.get("wins_to_double_down", []),
        "losses_to_drop": parsed.get("losses_to_drop", []),
        "pack": to_jsonable(new_pack),
    }
