"""Strategy pipeline orchestrator.

Phase 1 (propose):
    DNA artifact + AccountInput → Claude Opus (Positioner) → 3-5 directions

Phase 2 (expand, after user picks a direction):
    [Claude Opus + DeepSeek + GPT-4o] parallel → 3 topic candidate lists
    → Claude Opus (Scheduler) fuses + dedupes + schedules
    → Claude Opus (Resourcer) extracts materials/risks/metrics
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, replace
from typing import Any

from .. import db, library
from ..generators import registry
from ..generators.base import Generator
from .models import (
    AccountInput,
    StrategicDirection,
    StrategyPack,
    TopicSlot,
    WeekTheme,
    to_jsonable,
)
from . import prompts


def _latest_dna_payload() -> dict[str, Any]:
    with db.connect(read_only=True) as con:
        try:
            row = con.execute(
                "SELECT payload_json FROM studio_dna_artifacts"
                " ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        except Exception:
            row = None
    if not row:
        return {}
    try:
        return json.loads(row["payload_json"])
    except Exception:
        return {}


# ---- Phase 1: propose ----------------------------------------------------

# All LLM JSON calls now go through the shared utility, which handles OpenAI
# model fallback (gpt-5 → gpt-4o when org-not-verified) + secret masking.
from ..llm_call import call_for_json as _call_json  # noqa: E402


_DIRECTIONS_SCHEMA = {
    "type": "object",
    "required": ["directions"],
    "properties": {
        "directions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "positioning_statement"],
                "properties": {
                    "name": {"type": "string"},
                    "positioning_statement": {"type": "string"},
                    "target_audience": {"type": "string"},
                    "hook_angles": {"type": "array", "items": {"type": "string"}},
                    "differentiator": {"type": "string"},
                    "risk": {"type": "string"},
                    "score": {"type": "number"},
                    "why_works": {"type": "string"},
                },
            },
        },
    },
}


async def propose(inp: AccountInput, positioner_spec: str = "claude:sonnet") -> dict[str, Any]:
    """Phase 1: propose strategic directions. Persists a 'directions' pack."""
    db.apply_migrations(verbose=False)
    pack_id = uuid.uuid4().hex[:16]
    t0 = time.time()
    lib_id = library.active_lib_id()
    dna = _latest_dna_payload()

    from ..insight.pipeline import full_reference_block_for_prompt
    report_ctx = full_reference_block_for_prompt()
    report_block = f"\n\n{report_ctx}\n" if report_ctx else ""

    user_text = (
        f"【用户初步定位】\n{prompts.input_blurb(inp)}"
        f"{report_block}\n"
        f"【该平台爆款 DNA】\n{prompts.dna_blurb(dna)}\n\n"
        f"输出 8-12 个差异化的账号定位方向（宁多勿少 ；用户需要足够的选项来挑）。"
        + ("\n**优先采纳上面所有「分析 / 整合报告」中提到的方向和机会作为候选**（包括用户自己上传的外部报告整合稿，如有）。"
           if report_ctx else "")
    )
    gen = registry.build(positioner_spec)[0]
    try:
        parsed = await _call_json(
            gen, prompts.POSITIONER_SYSTEM, user_text,
            # 8-12 directions × ~250 char rationale apiece needs more headroom.
            max_tokens=8000,
            tool_name="submit_directions", schema=_DIRECTIONS_SCHEMA,
        )
    except Exception as e:
        raise RuntimeError(f"Positioner LLM failed: {e!r}") from e

    raw_directions = parsed.get("directions") or []
    directions: list[StrategicDirection] = []
    for d in raw_directions:
        directions.append(StrategicDirection(
            name=str(d.get("name", ""))[:48],
            positioning_statement=str(d.get("positioning_statement", "")),
            target_audience=str(d.get("target_audience", "")),
            hook_angles=[str(x) for x in (d.get("hook_angles") or [])],
            differentiator=str(d.get("differentiator", "")),
            risk=str(d.get("risk", "")),
            score=float(d.get("score") or 0),
            why_works=str(d.get("why_works", "")),
        ))

    elapsed = int(time.time() - t0)
    now = int(time.time())
    from .. import project as _project
    pid = _project.active_project_id()
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_strategies"
            " (pack_id, library_id, platform, created_at, updated_at, status,"
            "  input_json, directions_json, elapsed_s, project_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pack_id, lib_id, inp.platform, now, now, "directions",
                json.dumps(asdict(inp), ensure_ascii=False),
                json.dumps([asdict(d) for d in directions], ensure_ascii=False),
                elapsed, pid,
            ),
        )

    return {
        "pack_id": pack_id,
        "directions": [asdict(d) for d in directions],
        "elapsed_s": elapsed,
    }


# ---- Phase 2: expand ----------------------------------------------------

_TOPICS_SCHEMA = {
    "type": "object",
    "required": ["topics"],
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "title_variants": {"type": "array", "items": {"type": "string"}},
                    "angle": {"type": "string"},
                    "hook_type": {"type": "string"},
                    "outline": {"type": "array", "items": {"type": "string"}},
                    "materials_needed": {"type": "array", "items": {"type": "string"}},
                    "intent": {"type": "string"},
                },
            },
        },
    },
}

_BODY_DRAFT_SCHEMA = {
    "type": "object",
    "required": ["body_draft"],
    "properties": {"body_draft": {"type": "string"}},
}

_SCHEDULE_SCHEMA = {
    "type": "object",
    "required": ["series_thesis", "weekly_themes", "schedule"],
    "properties": {
        "series_thesis": {"type": "string"},
        "weekly_themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "week": {"type": "integer"},
                    "theme": {"type": "string"},
                    "intent": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
        },
        "schedule": {
            "type": "array",
            "items": _TOPICS_SCHEMA["properties"]["topics"]["items"] | {
                "properties": {
                    **_TOPICS_SCHEMA["properties"]["topics"]["items"]["properties"],
                    "week": {"type": "integer"},
                    "day_of_week": {"type": "integer"},
                    "publish_slot": {"type": "string"},
                },
            },
        },
    },
}

_RESOURCES_SCHEMA = {
    "type": "object",
    "properties": {
        "materials_checklist": {"type": "array", "items": {"type": "string"}},
        "risks_and_mitigations": {"type": "array", "items": {"type": "string"}},
        "success_metrics": {"type": "array", "items": {"type": "string"}},
    },
}


async def expand(
    pack_id: str,
    chosen_idx: int,
    # Topic generation: keep one Opus (best variety) + 2 cheap fast models.
    topicgen_spec: str = "claude:sonnet,deepseek,openai",
    # Scheduler is mechanical (merge + arrange); Sonnet is plenty.
    scheduler_spec: str = "claude:sonnet",
    # Resourcer aggregates lists; Sonnet handles it.
    resourcer_spec: str = "claude:sonnet",
    # Per-slot body drafts (300-600 chars) — Sonnet plenty.
    drafter_spec: str = "claude:sonnet",
) -> dict[str, Any]:
    """Phase 2: turn a chosen direction into a full StrategyPack."""
    db.apply_migrations(verbose=False)
    t0 = time.time()
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT input_json, directions_json, library_id, platform"
            " FROM studio_strategies WHERE pack_id = ?", (pack_id,),
        ).fetchone()
    if not row:
        raise LookupError(f"strategy pack not found: {pack_id}")
    inp_data = json.loads(row["input_json"])
    directions_data = json.loads(row["directions_json"])
    if chosen_idx < 0 or chosen_idx >= len(directions_data):
        raise IndexError(f"chosen direction out of range: {chosen_idx}")
    chosen = StrategicDirection(**directions_data[chosen_idx])
    inp = AccountInput(**inp_data)
    lib_id = row["library_id"] or library.active_lib_id()
    platform = row["platform"] or inp.platform

    # Idempotency guard: reject a duplicate expand if one is already in flight.
    # Without this, repeated clicks / network-retry loops accumulate parallel
    # LLM call storms (12 body-drafters × N concurrent expands) that hammer
    # Anthropic rate limits and wedge uvicorn's connection pool.
    with db.connect(read_only=True) as con:
        cur_row = con.execute(
            "SELECT status, updated_at FROM studio_strategies WHERE pack_id = ?",
            (pack_id,),
        ).fetchone()
    if cur_row and cur_row["status"] == "expanding":
        age_s = int(time.time()) - int(cur_row["updated_at"] or 0)
        if age_s < 8 * 60:  # 8 min stale-protect window
            raise RuntimeError(
                f"expand 已经在跑了（已 {age_s}s）。前端会自动 poll 等结果，请不要重复点。"
            )

    # Mark as 'expanding' so the frontend can detect "still in progress" if
    # its HTTPS-to-localhost connection drops mid-call (60-180s requests are
    # routinely killed by browser / mixed-content / wifi blip).
    with db.connect() as con:
        con.execute(
            "UPDATE studio_strategies SET status='expanding', chosen_direction_idx=?,"
            " updated_at=? WHERE pack_id=?",
            (chosen_idx, int(time.time()), pack_id),
        )

    try:
        return await _expand_inner(pack_id, chosen_idx, chosen, inp, lib_id, platform,
                                    topicgen_spec, scheduler_spec, resourcer_spec,
                                    drafter_spec, t0)
    except Exception as e:
        # Any uncaught failure → mark pack so frontend stops polling.
        try:
            with db.connect() as con:
                con.execute(
                    "UPDATE studio_strategies SET status='expand_failed',"
                    " updated_at=?, trace_json=?"
                    " WHERE pack_id=?",
                    (int(time.time()), json.dumps({"error": repr(e)}), pack_id),
                )
        except Exception:
            pass
        raise


async def _expand_inner(
    pack_id: str, chosen_idx: int, chosen: StrategicDirection,
    inp: AccountInput, lib_id: str, platform: str,
    topicgen_spec: str, scheduler_spec: str, resourcer_spec: str,
    drafter_spec: str, t0: float,
) -> dict[str, Any]:
    dna = _latest_dna_payload()
    topic_count = inp.cycle_weeks * inp.posts_per_week

    from ..insight.pipeline import full_reference_block_for_prompt
    report_ctx = full_reference_block_for_prompt()
    report_block = f"\n\n{report_ctx}\n" if report_ctx else ""

    # --- Topic-gen pool (parallel) ---
    topicgen_user = (
        f"【已选定的账号方向】\n"
        f"name: {chosen.name}\n"
        f"positioning: {chosen.positioning_statement}\n"
        f"target_audience: {chosen.target_audience}\n"
        f"hook_angles: {chosen.hook_angles}\n"
        f"differentiator: {chosen.differentiator}"
        f"{report_block}\n"
        f"【用户运营约束】\n{prompts.input_blurb(inp)}\n\n"
        f"【该平台 DNA】\n{prompts.dna_blurb(dna)}\n\n"
        f"请输出 {max(topic_count, 12)} 个候选选题。"
        + ("\n**所有上面分析 / 整合报告里提到的内容机会都必须覆盖到选题里。**"
           if report_ctx else "")
    )
    topicgens = registry.build(topicgen_spec)

    async def _one_topicgen(g: Generator):
        try:
            return await asyncio.wait_for(
                _call_json(g, prompts.TOPICGEN_SYSTEM, topicgen_user,
                           max_tokens=4096, tool_name="submit_topics",
                           schema=_TOPICS_SCHEMA),
                timeout=180,
            )
        except Exception as e:
            return {"_error": str(e), "_llm": g.model}

    topic_results = await asyncio.gather(*(_one_topicgen(g) for g in topicgens))

    # Collate all topics with a source-llm tag for visibility.
    all_topics: list[dict[str, Any]] = []
    topicgen_errors: list[str] = []
    for g, r in zip(topicgens, topic_results):
        if "_error" in r:
            topicgen_errors.append(f"{g.model}: {r['_error']}")
            continue
        for t in (r.get("topics") or []):
            t = dict(t); t["_source"] = g.model
            all_topics.append(t)

    # --- Scheduler: fuse + schedule ---
    timing_heatmap = (dna.get("sections", {}).get("timing", {}) or {}).get("heatmap", [])
    sched_user = (
        f"【已选定的账号方向】name={chosen.name} · 定位={chosen.positioning_statement}\n"
        f"【运营约束】cycle_weeks={inp.cycle_weeks}, posts_per_week={inp.posts_per_week} ⇒ "
        f"需要排出 {topic_count} 篇\n\n"
        f"【N 家 LLM 起草的候选选题（共 {len(all_topics)} 条，含来源）】\n"
        + json.dumps(all_topics, ensure_ascii=False, indent=2)
        + f"\n\n【该平台发布时段热力图 (top 时段)】\n"
        + _format_timing(timing_heatmap)
        + "\n\n请按 system 提示，融合 + 去重 + 排成完整周历。"
    )
    scheduler_gen = registry.build(scheduler_spec)[0]
    async def _try_scheduler(user_payload: str, max_tokens: int = 6000):
        return await _call_json(
            scheduler_gen, prompts.SCHEDULER_SYSTEM, user_payload,
            max_tokens=max_tokens, tool_name="submit_schedule",
            schema=_SCHEDULE_SCHEMA,
        )

    try:
        # Scheduler only outputs structure. Body drafts come later from a
        # parallel pool — see v0.29 / v0.30 commits for why we split this.
        sched_parsed = await _try_scheduler(sched_user)
        # Sonnet sometimes returns {"schedule": []} when its tool_use call
        # hits truncation or a content-quirk. Retry once with a shorter
        # prompt (drop the topic candidates pool, keep direction + count).
        if not (sched_parsed.get("schedule") or []):
            short_user = (
                f"【已选定方向】{chosen.name} — {chosen.positioning_statement}\n"
                f"【受众】{chosen.target_audience}\n"
                f"【运营】{inp.cycle_weeks} 周 × {inp.posts_per_week} 篇/周 = 共 {topic_count} 篇\n\n"
                f"请直接基于这个方向 + 周期编排出 {topic_count} 篇排期。"
                f" 不需要再读候选选题池 — 自己写 {topic_count} 个差异化标题就行。"
                f" 严格按 system schema 输出 schedule（长度 = {topic_count}）+ weekly_themes。"
            )
            try:
                sched_parsed = await _try_scheduler(short_user, max_tokens=8000)
            except Exception as e:
                sched_parsed["_error"] = f"retry failed: {e!r}"
    except Exception as e:
        sched_parsed = {"_error": str(e)}

    weekly_themes_raw = sched_parsed.get("weekly_themes") or []
    schedule_raw = sched_parsed.get("schedule") or []

    # Defensive coercion. Claude's tool_use occasionally returns array items
    # as bare strings (especially under truncation), which used to crash the
    # whole expand pipeline with AttributeError. Now we either parse a dict
    # or wrap a string into a minimal dict.
    def _to_theme_dict(item: Any, week_hint: int) -> dict[str, Any]:
        if isinstance(item, dict):
            return item
        return {"week": week_hint, "theme": str(item), "intent": "", "notes": ""}

    def _to_slot_dict(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            return item
        # Bare-string fallback: parse "[自补]" or similar into the title field.
        return {"title": str(item), "outline": [], "materials_needed": []}

    weekly_themes = [
        WeekTheme(
            week=int(w.get("week", i + 1)),
            theme=str(w.get("theme", "")),
            intent=str(w.get("intent", "")),
            notes=str(w.get("notes", "")),
        )
        for i, _raw in enumerate(weekly_themes_raw)
        for w in [_to_theme_dict(_raw, i + 1)]
    ]
    schedule = [
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
        )
        for _raw in schedule_raw
        for s in [_to_slot_dict(_raw)]
    ]

    # --- Body-draft pool: parallel, one call per slot ---
    # Splitting body-drafting out of the scheduler was forced by the
    # observation that a single 12-slot body-draft call would silently
    # return an empty schedule once total tokens crossed ~10k. Each call
    # here is small + focused, and failures isolate per slot.
    # Defaults to claude:sonnet which is ~3x faster than opus and plenty
    # capable for a 300-600 char drop-in draft.
    drafter_chosen = registry.build(drafter_spec)[0]
    direction_block = (
        f"【账号方向】{chosen.name}\n"
        f"【一句话定位】{chosen.positioning_statement}\n"
        f"【目标受众】{chosen.target_audience}\n"
        f"【平台】{platform}\n"
        f"【可参考的爆款 hook 角度】{', '.join(chosen.hook_angles or [])}\n"
        f"{report_block}"
    )

    async def _draft_one(slot_idx: int, slot: TopicSlot) -> tuple[int, str, str | None]:
        slot_prompt = (
            f"{direction_block}\n\n"
            f"【这一篇的 slot】\n"
            f"- 标题：{slot.title}\n"
            f"- 备选标题：{', '.join(slot.title_variants[:3])}\n"
            f"- 角度：{slot.angle} · hook 类型：{slot.hook_type} · 意图：{slot.intent}\n"
            f"- 大纲：\n" + "\n".join(f"  · {o}" for o in slot.outline) + "\n"
            + (f"- 需要的素材：{', '.join(slot.materials_needed)}\n" if slot.materials_needed else "")
            + "\n请按 system 给的 schema 输出这一篇的 body_draft。"
        )
        # Two-shot retry: bursts of N parallel Sonnet calls sometimes return
        # empty body_draft (rate-limit / throttle / SDK quirk). Retry once
        # after a short jittered delay before declaring failure.
        last_err: str | None = None
        for attempt in (1, 2):
            try:
                r = await asyncio.wait_for(
                    _call_json(
                        drafter_chosen, prompts.BODY_DRAFTER_SYSTEM, slot_prompt,
                        max_tokens=2000,
                        tool_name="submit_body_draft", schema=_BODY_DRAFT_SCHEMA,
                    ),
                    timeout=180,
                )
                body = str(r.get("body_draft", "")).strip()
                if body:
                    return slot_idx, body, None
                last_err = "empty body_draft returned"
            except Exception as e:
                last_err = repr(e)
            # Backoff with jitter so retries don't all hammer at once.
            await asyncio.sleep(2 + slot_idx * 0.4)
        return slot_idx, "", last_err

    drafter_errors: list[str] = []
    if schedule:
        draft_results = await asyncio.gather(
            *[_draft_one(i, s) for i, s in enumerate(schedule)]
        )
        for idx, draft, err in draft_results:
            schedule[idx].body_draft = draft
            if err:
                drafter_errors.append(f"slot #{idx + 1} ({schedule[idx].title[:40]}): {err}")

    # --- Resourcer: consolidate ---
    schedule_summary = "\n".join(
        f"- W{slot.week} D{slot.day_of_week} {slot.publish_slot or ''} | "
        f"{slot.title} | 需要：{', '.join(slot.materials_needed)}"
        for slot in schedule
    )
    res_user = (
        f"【方向】{chosen.name}\n"
        f"【已排好的 {len(schedule)} 篇】\n{schedule_summary}\n\n"
        f"请按 system 输出 materials_checklist + risks_and_mitigations + success_metrics。"
    )
    resourcer_gen = registry.build(resourcer_spec)[0]
    try:
        res_parsed = await _call_json(
            resourcer_gen, prompts.RESOURCER_SYSTEM, res_user,
            max_tokens=2048, tool_name="submit_resources", schema=_RESOURCES_SCHEMA,
        )
    except Exception as e:
        res_parsed = {"_error": str(e)}

    # Defensive coercion. Sonnet's tool_use occasionally returns these
    # list fields as a single JSON-encoded string (e.g. '["a","b"]'). If we
    # iterate that, each character ends up as its own list item, and the
    # frontend renders one Chinese character per <li> — see user bug
    # report. Detect string and json.loads / fall back to single-item list.
    def _coerce_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(x) for x in value]
        if isinstance(value, str):
            s = value.strip()
            if s.startswith("[") and s.endswith("]"):
                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, list):
                        return [str(x) for x in parsed]
                except json.JSONDecodeError:
                    pass
            # Last resort: split on newlines, ignore empties.
            lines = [ln.strip(" •·-—") for ln in s.split("\n") if ln.strip()]
            return lines or [s]
        return []

    pack = StrategyPack.new(library_id=lib_id, platform=platform, input=inp, chosen=chosen)
    pack.series_thesis = str(sched_parsed.get("series_thesis", ""))
    pack.weekly_themes = weekly_themes
    pack.schedule = schedule
    pack.materials_checklist = _coerce_list(res_parsed.get("materials_checklist"))
    pack.risks_and_mitigations = _coerce_list(res_parsed.get("risks_and_mitigations"))
    pack.success_metrics = _coerce_list(res_parsed.get("success_metrics"))

    elapsed_total = int(time.time() - t0)
    now = int(time.time())
    pack_json_str = json.dumps(to_jsonable(pack), ensure_ascii=False)

    with db.connect() as con:
        con.execute(
            "UPDATE studio_strategies SET status=?, chosen_direction_idx=?,"
            " pack_json=?, updated_at=?, elapsed_s=?"
            " WHERE pack_id=?",
            ("expanded", chosen_idx, pack_json_str, now, elapsed_total, pack_id),
        )

    return {
        "pack_id": pack_id,
        "pack": to_jsonable(pack),
        "topicgen_errors": topicgen_errors,
        "scheduler_error": sched_parsed.get("_error"),
        "resourcer_error": res_parsed.get("_error"),
        "drafter_errors": drafter_errors,
        "topic_candidate_count": len(all_topics),
        "elapsed_s": elapsed_total,
    }


def _format_timing(heatmap: list[dict]) -> str:
    if not heatmap:
        return "（无数据）"
    valid = [c for c in heatmap if c.get("count", 0) >= 3]
    if not valid:
        valid = sorted(heatmap, key=lambda c: c.get("count", 0), reverse=True)[:5]
    valid.sort(key=lambda c: c.get("median_likes", 0), reverse=True)
    dow = ["一", "二", "三", "四", "五", "六", "日"]
    return "\n".join(
        f"  · 周{dow[c['dow']]} {c['hour']:02d}:00 — median {int(c.get('median_likes', 0))}"
        f" (n={c['count']})"
        for c in valid[:10]
    )
