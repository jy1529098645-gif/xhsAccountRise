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


# v0.60: phase rules 从「硬约束」改成「冷启动经验规律 + AI 自己据数据调整」。
# 旧设计的问题：W1≤10% / W2≤30% / 50% 评论原话 / 75-85% content_format /
# 笑点 15-25% / 角度均匀 ... 7-8 条硬约束互相矛盾，LLM 退化到执行最简单
# 的那条（往往是 "全篇套产品上下文模板" = 微商化）。
# 新设计：把规律作为「经验建议 + 决策框架」呈现，要求 AI 自己输出
# decision_rationale 解释为什么这么排，保留 3 条不可商量底线（合规 /
# verbatim / 不许 100% 同质化）。
def _build_phase_rules(cycle_weeks: int) -> str:
    """Phase pacing guidance — **suggestion, not hard constraint**. LLM is
    expected to read DNA + product context + goal + reports and form its own
    judgement on how to pace the cycle. Output decision_rationale per slot."""
    if cycle_weeks <= 1:
        return (
            "【📐 单周冲刺 · 经验建议】\n"
            "  · 一周内拉新 + 转化兼顾。每篇都要强 hook + 转化路径预埋。\n"
            "  · 但具体节奏你自己据数据 + goal 判断"
        )
    return (
        f"【📐 起号经验规律（{cycle_weeks} 周 · 建议，不是硬约束 — 你看数据自己决定）】\n\n"
        "**通用冷启动节奏（适用于多数账号，你可按 DNA / goal_type / 产品类型微调）**：\n"
        "  · 早期（前 1/4 周期）：账号「人设建立期」\n"
        "      - 用户对你完全陌生，建立信任优先\n"
        "      - 痛点共鸣 / 真实经历 / 干货 hook 是主调\n"
        "      - 产品/卖货语言极弱（不是不能提，而是建立信任前提下偶尔自然带出）\n"
        "  · 中期（中间 1/2 周期）：「专业感 + 沉淀期」\n"
        "      - 深度方法论 / 系列内容 / 工具流让粉丝记住你\n"
        "      - 产品可以作为「我自己用的工具」自然露出，但不是主推\n"
        "  · 后期（最后 1/4 周期）：「转化期」\n"
        "      - 强卖点 / 用户证言 / 评论引导 / 私域 CTA\n"
        "      - 把前期积累的粉丝变成线索 / 试用 / 付费\n\n"
        "**但通用规律只是出发点，AI 应该据以下信号调整**：\n"
        "  · goal_type=个人分享 / 情感 → 全程弱转化，「转化期」也可以淡化\n"
        "  · goal_type=产品种草 / SaaS → 转化期可显著强化\n"
        "  · 产品本身是日常工具（笔记 / 学习 APP / 工具流）→ 早期可顺手提，因为「我每天都在用」本身就是真实人设\n"
        "  · 产品是付费课 / 训练营 / 高客单 → 后期才转化，前期严守人设\n"
        "  · DNA 笑点信号 prevalence > 25% → 段子 / 反讽 / 沙雕 hook 是该库爆款主流，应大胆混入（包括早期），别被「专业感」框死\n"
        "  · DNA 段子信号低 → 该库读者不吃这套，主走干货 / 教程\n"
        "  · 用户外部报告里若推荐了具体节奏 → 优先听报告，本规律退让\n\n"
        "**输出要求**：每个 slot 加 decision_rationale 字段（1 句话），说清这一篇\n"
        "为什么排在这一周 + 为什么选这个 hook/angle。**让运营能看出你的策略思路**。"
    )


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


async def propose(inp: AccountInput, positioner_spec: str = "openai:gpt-4o") -> dict[str, Any]:
    """Phase 1: propose strategic directions. Persists a 'directions' pack."""
    db.apply_migrations(verbose=False)
    pack_id = uuid.uuid4().hex[:16]
    t0 = time.time()
    lib_id = library.active_lib_id()
    dna = _latest_dna_payload()

    from ..insight.pipeline import full_reference_block_for_prompt
    from .. import product_context as _pc
    from .goals import goal_voice_block
    report_ctx = full_reference_block_for_prompt()
    report_block = f"\n\n{report_ctx}\n" if report_ctx else ""
    pctx = _pc.context_block()
    pctx_block = (
        f"\n\n【⭐ 你的产品/账号定位（强约束 — 反复引用其中的真实功能 / 用户原话 / 经典叙事）】\n{pctx}\n"
        if pctx else ""
    )
    # v0.59: goal-type aware voice + addendum.
    goal_block = goal_voice_block(getattr(inp, "goal_type", "") or "")
    goal_section = f"\n\n{goal_block}\n" if goal_block else ""

    user_text = (
        f"【用户初步定位】\n{prompts.input_blurb(inp)}"
        f"{goal_section}"
        f"{pctx_block}"
        f"{report_block}\n"
        f"【该平台爆款 DNA】\n{prompts.dna_blurb(dna)}\n\n"
        f"输出 12-20 个差异化的账号定位方向（宁多勿少 ；用户会多选 2-5 个组合执行）。"
        + ("\n\n⭐ **产品上下文强约束** ：每个方向必须能映射到产品上下文里的至少 1 个真实功能 / 1 句经典叙事 / "
           "1 类目标用户。**绝对不要发明产品上下文里没提的功能名 / 工具名 / 数字**（这些必须 verbatim）。"
           "对核心叙事 / 场景金句 ：why_works 里要锚定它们传达的立意（解决什么痛点、给什么承诺），"
           "但用你自己的话重新表达 — 不要 12-20 个方向都套同一句，否则 LLM 退化成模板复读。"
           "对禁忌词 ：完全 verbatim 避开（这是合规底线）。"
           if pctx else "")
        + ("\n\n⭐⭐⭐ **核心约束** ⭐⭐⭐\n"
           "用户上传的外部报告是这个任务的**最强参考**，不是参考之一。请仔细阅读上面每一份报告的「关键论点 / 数据 / 案例 / 建议方向」，每一个独到观点都要在你的方向候选里找到落地点。\n"
           "  - 报告里推荐的具体方向 → 你必须有对应的候选，不要泛化\n"
           "  - 报告里引用的数字 → 在 why_works 里 verbatim 引用作为证据，不要四舍五入或概括\n"
           "  - 报告里的用户原话评论 → 可在 why_works 里 1-2 句作为社会证据引用，但每个方向引用不同的评论（不要 12 个方向都引同一句）\n"
           "  - 报告之间互相矛盾的点 → **保留两种方向作为不同候选**，不要折中\n"
           "  - 报告里的独到观点（只有一份提到）→ 必须出对应方向，不要因为「另一份没提」就丢\n"
           "8-12 个方向里，至少有 70% 要能直接溯源到上传的报告内容。"
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
            "  input_json, directions_json, elapsed_s, project_id, goal_type)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pack_id, lib_id, inp.platform, now, now, "directions",
                json.dumps(asdict(inp), ensure_ascii=False),
                json.dumps([asdict(d) for d in directions], ensure_ascii=False),
                elapsed, pid,
                getattr(inp, "goal_type", "") or None,
            ),
        )

    return {
        "pack_id": pack_id,
        "directions": [asdict(d) for d in directions],
        "elapsed_s": elapsed,
    }


# ---- Phase 1 streaming variant ------------------------------------------
# Same prompt + persistence as propose(), but uses Claude's text-streaming
# API and yields SSE-formatted bytes. Frontend renders directions
# incrementally as they appear. Compared to the blocking variant the
# wall-clock isn't faster, but perceived latency drops a lot (first
# direction visible in ~5-10s instead of waiting 30-50s for the whole
# blob).
#
# Note: not using tool_use here because streaming tool_use deltas is
# clunkier — we just ask the LLM for prose JSON and parse on completion.

async def propose_stream(
    inp: AccountInput,
    positioner_spec: str = "openai:gpt-4o",
):
    """Async generator yielding SSE-formatted bytes ('event: ...\\ndata: ...\\n\\n').

    Events:
      - 'delta'    {text}              text chunk arrived
      - 'progress' {n_complete, total} estimated complete direction count
      - 'complete' {pack_id, directions, elapsed_s}  done; full structured result
      - 'error'    {message}           irrecoverable failure
    """
    # Streaming text deltas only work with the Anthropic SDK currently. For
    # OpenAI / DeepSeek specs, fall back to the blocking propose() and emit a
    # single 'complete' event — the frontend treats that the same as a
    # finished stream. This keeps the /api/strategy/propose/stream endpoint
    # usable with any LLM family even if the user can't see incremental text.
    if not positioner_spec.lower().startswith("claude"):
        try:
            result = await propose(inp, positioner_spec=positioner_spec)
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': repr(e)}, ensure_ascii=False)}\n\n".encode()
            return
        yield (
            f"event: complete\ndata: "
            + json.dumps(result, ensure_ascii=False)
            + "\n\n"
        ).encode("utf-8")
        return

    db.apply_migrations(verbose=False)
    pack_id = uuid.uuid4().hex[:16]
    t0 = time.time()
    lib_id = library.active_lib_id()
    dna = _latest_dna_payload()

    from ..insight.pipeline import full_reference_block_for_prompt
    from .. import product_context as _pc
    from .goals import goal_voice_block
    report_ctx = full_reference_block_for_prompt()
    report_block = f"\n\n{report_ctx}\n" if report_ctx else ""
    pctx = _pc.context_block()
    pctx_block = (
        f"\n\n【⭐ 你的产品/账号定位（强约束 — 反复引用其中的真实功能 / 用户原话 / 经典叙事）】\n{pctx}\n"
        if pctx else ""
    )
    goal_block = goal_voice_block(getattr(inp, "goal_type", "") or "")
    goal_section = f"\n\n{goal_block}\n" if goal_block else ""

    user_text = (
        f"【用户初步定位】\n{prompts.input_blurb(inp)}"
        f"{goal_section}"
        f"{pctx_block}"
        f"{report_block}\n"
        f"【该平台爆款 DNA】\n{prompts.dna_blurb(dna)}\n\n"
        f"输出 12-20 个差异化的账号定位方向（宁多勿少 ；用户会多选 2-5 个组合执行）。\n"
        f"**直接输出一个 JSON 对象** ：{{\"directions\": [ ... ]}}，每条方向格式参考 system prompt。"
        f" 不要任何额外文字、解释、markdown 包裹。"
        + ("\n\n⭐ 报告强约束（同 propose 非流式版）：每个独到观点都要在方向里找到落地点 ；"
           "矛盾观点保留两种 ；70% 候选可溯源到报告。"
           if report_ctx else "")
        + ("\n\n⭐ 产品上下文强约束 ：每个方向必须能映射到产品上下文里的至少 1 个真实功能 / 1 句经典叙事 / "
           "1 类目标用户。**不要发明产品上下文里没提的功能名**（绝对禁止 hallucinate 工具名）。"
           if pctx else "")
    )

    gen = registry.build(positioner_spec)[0]
    try:
        client = gen._ensure_client()  # noqa: SLF001
    except Exception as e:
        yield f"event: error\ndata: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n".encode()
        return

    full_text = ""
    last_progress = -1
    try:
        async with client.messages.stream(
            model=gen.model,
            max_tokens=8000,
            system=prompts.POSITIONER_SYSTEM,
            messages=[{"role": "user", "content": user_text}],
        ) as stream:
            async for chunk in stream.text_stream:
                if not chunk:
                    continue
                full_text += chunk
                yield (
                    f"event: delta\ndata: "
                    + json.dumps({"text": chunk}, ensure_ascii=False)
                    + "\n\n"
                ).encode("utf-8")
                # Lightweight progress: count complete `"name": "..."` keys
                # which signal one direction's preamble is done.
                n = full_text.count('"name"')
                if n != last_progress and n > 0:
                    last_progress = n
                    yield (
                        f"event: progress\ndata: "
                        + json.dumps({"n_seen": n}, ensure_ascii=False)
                        + "\n\n"
                    ).encode("utf-8")
    except Exception as e:
        yield f"event: error\ndata: {json.dumps({'message': repr(e)}, ensure_ascii=False)}\n\n".encode()
        return

    # Parse the accumulated JSON.
    parsed: dict[str, Any] = {}
    try:
        s, e = full_text.find("{"), full_text.rfind("}")
        if s != -1 and e != -1 and e > s:
            parsed = json.loads(full_text[s:e + 1])
    except Exception as e:
        yield (
            f"event: error\ndata: "
            + json.dumps({"message": f"JSON parse failed: {e!r}", "raw": full_text[:500]},
                         ensure_ascii=False)
            + "\n\n"
        ).encode()
        return

    raw_directions = parsed.get("directions") or []
    directions: list[StrategicDirection] = []
    for d in raw_directions:
        if not isinstance(d, dict):
            continue
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
            "  input_json, directions_json, elapsed_s, project_id, goal_type)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pack_id, lib_id, inp.platform, now, now, "directions",
                json.dumps(asdict(inp), ensure_ascii=False),
                json.dumps([asdict(d) for d in directions], ensure_ascii=False),
                elapsed, pid,
                getattr(inp, "goal_type", "") or None,
            ),
        )

    payload = {
        "pack_id": pack_id,
        "directions": [asdict(d) for d in directions],
        "elapsed_s": elapsed,
    }
    yield (
        f"event: complete\ndata: "
        + json.dumps(payload, ensure_ascii=False)
        + "\n\n"
    ).encode("utf-8")


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

_BODY_DRAFT_BATCH_SCHEMA = {
    "type": "object",
    "required": ["drafts"],
    "properties": {
        "drafts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["idx", "body_draft"],
                "properties": {
                    "idx": {"type": "integer"},
                    "body_draft": {"type": "string"},
                },
            },
        },
    },
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
                    "publish_rationale": {"type": "string"},
                    "content_format": {"type": "string"},
                    "direction_idx": {"type": "integer"},
                    "decision_rationale": {"type": "string"},
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
    # v0.51: Claude defaults dropped. Topic creativity → gpt-4o + deepseek
    # for diversity. Scheduler (planning reasoning) → gpt-4o. Resourcer +
    # body drafter (volume / mechanical) → deepseek. Net cost ≈ 1/5 vs
    # Sonnet, latency similar or faster.
    topicgen_spec: str = "openai:gpt-4o,deepseek",
    scheduler_spec: str = "openai:gpt-4o",
    resourcer_spec: str = "deepseek",
    drafter_spec: str = "deepseek",
    # If True, cancel any in-flight expand for the same pack_id and start
    # fresh. Without this, the idempotency guard would 409 the duplicate
    # POST. Useful when user clicked the direction again because the
    # previous run was stuck or producing unsatisfactory results.
    restart: bool = False,
    # v0.59: multi-direction support. When provided, slots distribute across
    # ALL chosen directions instead of being locked to a single one. Frontend
    # passes this as a list of indices. None = legacy behavior (single
    # chosen_idx).
    chosen_idxs: list[int] | None = None,
) -> dict[str, Any]:
    """Phase 2: turn N chosen directions into a full StrategyPack."""
    db.apply_migrations(verbose=False)
    t0 = time.time()
    # v0.59.4: studio_strategies lives inside the active library's .db file,
    # so a pack created in lib A becomes invisible once user switches active
    # lib to B. Auto-recover: if not found in active lib, scan all libs.
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT input_json, directions_json, library_id, platform"
            " FROM studio_strategies WHERE pack_id = ?", (pack_id,),
        ).fetchone()
    if not row:
        # Search every library for this pack_id; if found, switch active lib
        # to it so the rest of expand reads/writes the correct DB.
        pack_lib_id = library.find_pack_lib_id(pack_id)
        if pack_lib_id:
            try:
                library.set_active(pack_lib_id)
            except Exception:
                pass  # best-effort; if can't switch, raise as before
            db.apply_migrations(verbose=False)
            with db.connect(read_only=True) as con:
                row = con.execute(
                    "SELECT input_json, directions_json, library_id, platform"
                    " FROM studio_strategies WHERE pack_id = ?", (pack_id,),
                ).fetchone()
    if not row:
        raise LookupError(f"strategy pack not found: {pack_id}")
    inp_data = json.loads(row["input_json"])
    directions_data = json.loads(row["directions_json"])

    # v0.59: resolve chosen_idxs (multi-direction) or fall back to chosen_idx.
    if chosen_idxs:
        # Validate every idx.
        for idx in chosen_idxs:
            if idx < 0 or idx >= len(directions_data):
                raise IndexError(f"chosen direction out of range: {idx}")
        # Dedupe preserve order.
        chosen_idxs = list(dict.fromkeys(chosen_idxs))
        if len(chosen_idxs) == 0:
            raise IndexError("chosen_idxs is empty")
        if len(chosen_idxs) > 8:
            raise IndexError("too many chosen directions (max 8)")
    else:
        if chosen_idx < 0 or chosen_idx >= len(directions_data):
            raise IndexError(f"chosen direction out of range: {chosen_idx}")
        chosen_idxs = [chosen_idx]
    # The "primary" chosen direction (for backward compat fields) is the first.
    chosen = StrategicDirection(**directions_data[chosen_idxs[0]])
    chosen_directions: list[StrategicDirection] = [
        StrategicDirection(**directions_data[i]) for i in chosen_idxs
    ]
    inp = AccountInput(**inp_data)
    lib_id = row["library_id"] or library.active_lib_id()
    platform = row["platform"] or inp.platform

    from .. import jobs as _jobs
    job_id = f"expand:{pack_id}"

    # Idempotency guard: reject a duplicate expand if one is already in flight.
    # Without this, repeated clicks / network-retry loops accumulate parallel
    # LLM call storms (12 body-drafters × N concurrent expands) that hammer
    # Anthropic rate limits and wedge uvicorn's connection pool.
    #
    # When restart=True, cancel the existing run first + drop its partial
    # state so this attempt starts truly fresh (no leftover topic-pool or
    # half-written drafter results from the previous attempt's checkpoint).
    if restart:
        try:
            _jobs.cancel(job_id)
        except Exception:
            pass
        # Wait a moment for the cancelled task's cleanup to land in DB
        # so the idempotency check below doesn't see 'expanding'.
        await asyncio.sleep(2)
        # Clear partial state so we don't accidentally resume from a stale
        # checkpoint when the user explicitly asked for a restart.
        try:
            with db.connect() as con:
                con.execute(
                    "UPDATE studio_strategies"
                    " SET partial_state_json = NULL, paused_at_stage = NULL,"
                    " status = 'directions', updated_at = ?"
                    " WHERE pack_id = ?",
                    (int(time.time()), pack_id),
                )
        except Exception:
            pass

    with db.connect(read_only=True) as con:
        cur_row = con.execute(
            "SELECT status, updated_at FROM studio_strategies WHERE pack_id = ?",
            (pack_id,),
        ).fetchone()
    if cur_row and cur_row["status"] == "expanding" and not restart:
        age_s = int(time.time()) - int(cur_row["updated_at"] or 0)
        if age_s < 8 * 60:  # 8 min stale-protect window
            raise RuntimeError(
                f"expand 已经在跑了（已 {age_s}s）。前端会自动 poll 等结果，请不要重复点。"
                f" 如果你想从头重跑，传 restart=true。"
            )

    # Mark as 'expanding' so the frontend can detect "still in progress" if
    # its HTTPS-to-localhost connection drops mid-call (60-180s requests are
    # routinely killed by browser / mixed-content / wifi blip).
    # v0.59: persist chosen_direction_idxs (multi) alongside legacy single
    # chosen_direction_idx so old readers still work.
    with db.connect() as con:
        con.execute(
            "UPDATE studio_strategies"
            " SET status='expanding', chosen_direction_idx=?,"
            " chosen_direction_idxs=?, updated_at=? WHERE pack_id=?",
            (chosen_idxs[0], json.dumps(chosen_idxs),
             int(time.time()), pack_id),
        )

    from .. import jobs
    job_id = f"expand:{pack_id}"
    label = (
        chosen.name if len(chosen_directions) == 1
        else f"{chosen.name} +{len(chosen_directions)-1} 个方向"
    )
    try:
        async with jobs.tracked(job_id, kind="expand", label=label):
            return await _expand_inner(
                pack_id, chosen_idxs[0], chosen, inp, lib_id, platform,
                topicgen_spec, scheduler_spec, resourcer_spec,
                drafter_spec, t0, job_id=job_id,
                chosen_directions=chosen_directions,
                chosen_idxs=chosen_idxs,
            )
    except (jobs.CancelRequested, asyncio.CancelledError):
        # User pressed pause. partial_state_json was already checkpointed
        # by _expand_inner at each stage boundary.
        try:
            with db.connect() as con:
                con.execute(
                    "UPDATE studio_strategies SET status='paused',"
                    " updated_at=? WHERE pack_id=?",
                    (int(time.time()), pack_id),
                )
        except Exception:
            pass
        return {
            "pack_id": pack_id, "status": "paused",
            "message": "expand 已暂停。点恢复继续会从断点接上。",
        }
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
    drafter_spec: str, t0: float, job_id: str | None = None,
    chosen_directions: list[StrategicDirection] | None = None,
    chosen_idxs: list[int] | None = None,
) -> dict[str, Any]:
    # v0.59: multi-direction support. If caller passed N directions, slots
    # distribute across them. Otherwise fall back to single-direction (legacy).
    chosen_directions = chosen_directions or [chosen]
    chosen_idxs = chosen_idxs or [chosen_idx]
    from .. import jobs

    def _check_cancel():
        if job_id:
            jobs.check(job_id)

    def _save_checkpoint(stage: str, payload: dict[str, Any]) -> None:
        try:
            with db.connect() as con:
                # Merge into existing partial_state_json so each stage adds
                # its own chunk (topicgen / scheduler / drafter / resourcer).
                row = con.execute(
                    "SELECT partial_state_json FROM studio_strategies WHERE pack_id=?",
                    (pack_id,),
                ).fetchone()
                cur = {}
                if row and row["partial_state_json"]:
                    try: cur = json.loads(row["partial_state_json"])
                    except Exception: cur = {}
                cur[stage] = payload
                con.execute(
                    "UPDATE studio_strategies SET partial_state_json=?,"
                    " paused_at_stage=?, updated_at=? WHERE pack_id=?",
                    (json.dumps(cur, ensure_ascii=False), stage, int(time.time()), pack_id),
                )
        except Exception:
            pass

    def _load_checkpoints() -> dict[str, Any]:
        try:
            with db.connect(read_only=True) as con:
                row = con.execute(
                    "SELECT partial_state_json FROM studio_strategies WHERE pack_id=?",
                    (pack_id,),
                ).fetchone()
            if row and row["partial_state_json"]:
                return json.loads(row["partial_state_json"]) or {}
        except Exception:
            pass
        return {}

    def _clear_checkpoints():
        try:
            with db.connect() as con:
                con.execute(
                    "UPDATE studio_strategies SET partial_state_json=NULL,"
                    " paused_at_stage=NULL WHERE pack_id=?", (pack_id,))
        except Exception:
            pass

    saved = _load_checkpoints()

    dna = _latest_dna_payload()
    topic_count = inp.cycle_weeks * inp.posts_per_week

    from ..insight.pipeline import full_reference_block_for_prompt
    report_ctx = full_reference_block_for_prompt()
    report_block = f"\n\n{report_ctx}\n" if report_ctx else ""

    # --- Unified scheduler: generate + arrange in ONE Sonnet call ---
    # Used to be two stages: a parallel topicgen pool (2 LLMs × 12 topics =
    # 24+ candidates) followed by a scheduler that filtered + arranged them.
    # The pool added ~40-60s without much value — scheduler was filtering
    # anyway and a focused single call writes 12 differentiated topics fine.
    # Net: ~30-60s saved off expand. Quality holds because the scheduler
    # now sees direction+DNA+reports directly and generates topics tuned
    # for the schedule positions instead of going through a filter dance.
    topicgen_errors: list[str] = []
    all_topics: list[dict[str, Any]] = []  # kept for response shape compat

    timing_heatmap = (dna.get("sections", {}).get("timing", {}) or {}).get("heatmap", [])
    # v0.52: distribute slots across user-selected angles. With K angles + N
    # slots, each angle gets ~N/K slots (rounded). Forces variety in the
    # generated schedule instead of the LLM's natural collapse to a couple
    # of comfortable angles.
    angle_directive = ""
    exp_angles = list(inp.expected_angles or [])
    if exp_angles:
        per_angle = max(1, topic_count // len(exp_angles))
        angle_directive = (
            f"\n\n⭐ **角度分布约束** ：用户希望本期 {topic_count} 篇覆盖以下 {len(exp_angles)} 个角度："
            f" {', '.join(exp_angles)}。\n"
            f"  - 每个 slot 的 angle 字段必须**严格等于**这几个角度之一（不要写其它角度）\n"
            f"  - 大致每个角度 ≈ {per_angle} 篇（最后剩余 slot 可在角度间灵活分配）\n"
            f"  - 同一周内尽量混合不同角度，避免一周全是同一个角度\n"
        )
    # v0.58: pull product context into scheduler too — the most important
    # injection point since this generates the actual 30 篇排期 content.
    from .. import product_context as _pc
    from .goals import goal_voice_block
    pctx = _pc.context_block()
    pctx_block = (
        f"\n\n【⭐⭐⭐ 你的产品/账号定位（每篇必须真正引用其中的功能/叙事/受众）】\n{pctx}\n"
        if pctx else ""
    )
    goal_block = goal_voice_block(getattr(inp, "goal_type", "") or "")
    goal_section = f"\n\n{goal_block}\n" if goal_block else ""

    # v0.58 phase rules — 4 阶段硬性约束（之前只是软建议，导致 4 周内容看不出差异化）。
    # 按 cycle_weeks 切分阶段并把规则塞进 prompt。
    cw = inp.cycle_weeks
    phase_rules = _build_phase_rules(cw)

    # v0.59: multi-direction block. When user picked N directions, build a
    # detailed block listing all of them + assignment guidance.
    if len(chosen_directions) > 1:
        dir_lines = ["【⭐ 用户已选 {n} 个方向 — 排期必须把 slots 均匀分布到这几个方向上】".format(
            n=len(chosen_directions))]
        for i, d in enumerate(chosen_directions):
            dir_lines.append(
                f"\n  ▸ direction #{i + 1} ：{d.name}\n"
                f"    positioning: {d.positioning_statement}\n"
                f"    target_audience: {d.target_audience}\n"
                f"    hook_angles: {d.hook_angles}\n"
                f"    differentiator: {d.differentiator}"
            )
        chosen_block = "\n".join(dir_lines)
        per_dir = max(1, topic_count // len(chosen_directions))
        multi_dir_directive = (
            f"\n\n⭐ **多方向分配硬约束** ：\n"
            f"  · 用户选了 {len(chosen_directions)} 个方向，总共 {topic_count} 篇 slot\n"
            f"  · 大致每个方向 ≈ {per_dir} 篇（最后 1-2 篇可以混搭跨方向）\n"
            f"  · 每个 slot 必须在 schedule 输出里加一个 `direction_idx` 字段（0-indexed，指向用户选的方向）\n"
            f"  · 同一周内尽量混合不同方向，**不要一周全是同一个方向**（这正是用户想避免的「一周锁死一主题」）\n"
            f"  · 但同一阶段意图（拉新/专业感/沉淀/转化）内的不同方向可以互相借势"
        )
    else:
        chosen_block = (
            f"【已选定的账号方向】\n"
            f"name: {chosen.name}\n"
            f"positioning: {chosen.positioning_statement}\n"
            f"target_audience: {chosen.target_audience}\n"
            f"hook_angles: {chosen.hook_angles}\n"
            f"differentiator: {chosen.differentiator}"
        )
        multi_dir_directive = ""

    sched_user = (
        f"{chosen_block}"
        f"{goal_section}"
        f"{pctx_block}"
        f"{report_block}\n"
        f"【运营约束】cycle_weeks={inp.cycle_weeks}, posts_per_week={inp.posts_per_week}"
        f" ⇒ 需要排出 {topic_count} 篇\n"
        f"【用户其它约束】\n{prompts.input_blurb(inp)}\n\n"
        f"【该平台 DNA】\n{prompts.dna_blurb(dna)}\n\n"
        f"【该平台发布时段热力图】\n{_format_timing(timing_heatmap)}\n\n"
        f"{phase_rules}\n\n"
        f"请基于以上信息**直接出 {topic_count} 篇排期**（不需要先列候选池再筛 — 直接出 final）。"
        + ("\n\n⭐ **产品上下文使用 — 数据驱动 + 你自己判断**：\n"
           "  ✅ **底线（不可商量）**：\n"
           "      · 产品/工具/品牌名必须 verbatim（不能编造、不能改名）\n"
           "      · 具体数字 / 数据必须 verbatim（不能换算或近似）\n"
           "      · 平台红线词 / 禁忌词不能触碰（合规底线）\n"
           "  🔁 **核心叙事 / 场景金句的处理**：\n"
           "      · 立意保留，每篇用不同表达 / 句式 / 切入角度（30 篇里同一句金句最多用 1-2 次）\n"
           "      · 例 ：「以前要开 8 个网页」→ 可变体成「5 个 tabs 轮转太累」/「换工具像换 tab 一样崩」\n"
           "  📐 **产品权重 — 让你自己据情况判断**（不写死 X% 硬数字）：\n"
           "      · **冷启动通用原则** ：早期弱 → 后期强（粉丝从 0 → 1000 时直接卖货 = 死号；信任建立后才好转化）\n"
           "      · **但具体节奏看你综合判断**：\n"
           "          - 产品本身是日常工具（笔记/学习/工作流类）→ 早期就能自然带出（「我每天都在用 XX」是真实人设，不是硬广）\n"
           "          - 产品是付费课/训练营/高客单 → 严守前期人设，后期才转化\n"
           "          - goal_type=个人分享/情感号 → 整个周期都弱化产品权重\n"
           "          - goal_type=产品种草/SaaS → 后期可显著强化卖点\n"
           "          - 用户外部报告里推荐的节奏 → 优先听报告\n"
           "      · ⚠️ 唯一硬底线 ：**不能 100% slot 全推产品**（同质化 → 算法降权 + 用户疲劳）"
           if pctx else "")
        + ("\n\n⭐ **强约束** ：上面用户上传的原始报告里写的选题方向 / 案例 / 数字 / hook，"
           "每一个都必须能在你的 schedule 里找到对应位置。报告里点名的方向 → 必须有 slot。"
           "不要因为「这条不像亮点」就丢。schedule 里至少 60% 要能直接溯源到上传报告的具体内容。"
           if report_ctx else "")
        + angle_directive
        + multi_dir_directive
    )
    scheduler_gen = registry.build(scheduler_spec)[0]
    async def _try_scheduler(user_payload: str, max_tokens: int = 6000):
        return await _call_json(
            scheduler_gen, prompts.SCHEDULER_SYSTEM, user_payload,
            max_tokens=max_tokens, tool_name="submit_schedule",
            schema=_SCHEDULE_SCHEMA,
        )
    scheduler_gen = registry.build(scheduler_spec)[0]
    async def _try_scheduler(user_payload: str, max_tokens: int = 6000):
        return await _call_json(
            scheduler_gen, prompts.SCHEDULER_SYSTEM, user_payload,
            max_tokens=max_tokens, tool_name="submit_schedule",
            schema=_SCHEDULE_SCHEMA,
        )

    # Resume: skip scheduler if already done.
    if "scheduler" in saved:
        sched_parsed = saved["scheduler"]
    else:
        # Three-level scheduler fallback chain so we never end up with empty
        # schedule (the '0 篇' bug recurring users see):
        #   1. Sonnet with full candidate pool
        #   2. Sonnet with minimal prompt (just direction + count)
        #   3. Different LLM (OpenAI gpt-4o) with minimal prompt
        # Each level kicks in only if the previous returned schedule=[].
        sched_parsed: dict[str, Any] = {}
        _check_cancel()
        try:
            sched_parsed = await _try_scheduler(sched_user)
        except Exception as e:
            sched_parsed = {"_error": f"L1: {e!r}"}

        if not (sched_parsed.get("schedule") or []):
            # L2: simpler prompt with same model
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
                sched_parsed["_error"] = f"L2: {e!r}"

        if not (sched_parsed.get("schedule") or []):
            # L3: cross-family fallback (DeepSeek). Primary is now gpt-4o,
            # so DeepSeek is the cross-family option when an empty result
            # suggests a model-specific tool_use quirk rather than a prompt
            # problem.
            try:
                fallback_gen = registry.build("deepseek")[0]
                async def _ds_fallback(user_payload: str):
                    return await _call_json(
                        fallback_gen, prompts.SCHEDULER_SYSTEM, user_payload,
                        max_tokens=8000, tool_name="submit_schedule",
                        schema=_SCHEDULE_SCHEMA,
                    )
                sched_parsed = await _ds_fallback(short_user)
            except Exception as e:
                sched_parsed["_error"] = f"L3 (deepseek fallback): {e!r}"

        # L4: still nothing → synthesize a minimal schedule client-side from
        # the topic-gen pool's output so the user gets *something* usable.
        if not (sched_parsed.get("schedule") or []) and all_topics:
            picked = all_topics[:topic_count] or []
            synthetic = []
            for i, t in enumerate(picked):
                week = (i // inp.posts_per_week) + 1
                synthetic.append({
                    "week": week, "day_of_week": (i % 7),
                    "publish_slot": "",
                    "title": str(t.get("title") or t.get("name") or f"待补 #{i+1}") + " [自补]",
                    "title_variants": [str(x) for x in (t.get("title_variants") or [])],
                    "angle": str(t.get("angle", "")),
                    "hook_type": str(t.get("hook_type", "")),
                    "outline": [str(x) for x in (t.get("outline") or [])],
                    "materials_needed": [str(x) for x in (t.get("materials_needed") or [])],
                    "intent": str(t.get("intent", "")),
                    "content_format": str(t.get("content_format", "")),
                })
            sched_parsed = {
                "series_thesis": f"基于 {chosen.name} 的紧急自补排期",
                "weekly_themes": [
                    {"week": w, "theme": f"第 {w} 周", "intent": "", "notes": ""}
                    for w in range(1, inp.cycle_weeks + 1)
                ],
                "schedule": synthetic,
                "_warning": "scheduler LLM fell through 3 levels; synthesized from topic pool",
            }

        _save_checkpoint("scheduler", sched_parsed)
        _check_cancel()

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
            content_format=str(s.get("content_format", "")),
            publish_rationale=str(s.get("publish_rationale", "")),
            direction_idx=int(s.get("direction_idx", -1)) if s.get("direction_idx") is not None else -1,
            decision_rationale=str(s.get("decision_rationale", "")),
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

    def _slot_fmt_default(slot: TopicSlot) -> str:
        return slot.content_format or (
            "图文" if platform == "xiaohongshu"
            else "短视频" if platform in ("douyin", "kuaishou")
            else "图文"
        )

    def _slot_block(idx: int, slot: TopicSlot) -> str:
        fmt = _slot_fmt_default(slot)
        return (
            f"--- slot #{idx} ---\n"
            f"- 标题：{slot.title}\n"
            f"- 备选标题：{', '.join(slot.title_variants[:3])}\n"
            f"- 角度：{slot.angle} · hook 类型：{slot.hook_type} · 意图：{slot.intent}\n"
            f"- content_format：{fmt}\n"
            f"- 大纲：" + " / ".join(slot.outline) + "\n"
            + (f"- 需要的素材：{', '.join(slot.materials_needed)}\n" if slot.materials_needed else "")
        )

    BATCH_SIZE = 5  # slots per Sonnet call (was 3 — bumped because Sonnet
                    # handles 5×600 char output cleanly and we want fewer
                    # round-trips. For 12 slots: 12/5 → 3 batches instead of 4.)

    async def _draft_batch(slots_with_idx: list[tuple[int, TopicSlot]]) -> list[tuple[int, str, str | None]]:
        if not slots_with_idx:
            return []
        slot_blocks = "\n\n".join(_slot_block(i, s) for i, s in slots_with_idx)
        batch_prompt = (
            f"{direction_block}\n\n"
            f"【一次性给你 {len(slots_with_idx)} 个 slot，请同时为每个写 body_draft】\n\n"
            f"{slot_blocks}\n\n"
            f"按 schema 输出 ：drafts 数组，每项 {{ idx: <对应 slot 编号>, body_draft: <完整正文> }}。"
            f" 每个 body_draft 必须按它自己的 content_format 写（图文 vs 短视频脚本 vs 长视频章节差别很大）。"
            f" 不同 slot 之间的口吻和内容要有差异化，不要互相重复。"
        )
        idx_to_slot = {i: s for i, s in slots_with_idx}
        last_err: str | None = None
        for attempt in (1, 2):
            try:
                r = await asyncio.wait_for(
                    _call_json(
                        drafter_chosen, prompts.BODY_DRAFTER_BATCH_SYSTEM, batch_prompt,
                        max_tokens=8000,
                        tool_name="submit_body_draft_batch",
                        schema=_BODY_DRAFT_BATCH_SCHEMA,
                    ),
                    timeout=180,
                )
                drafts = r.get("drafts") or []
                out: list[tuple[int, str, str | None]] = []
                returned = {int(d.get("idx", -1)): str(d.get("body_draft", "")).strip()
                            for d in drafts if isinstance(d, dict)}
                for i, _ in slots_with_idx:
                    body = returned.get(i, "")
                    out.append((i, body, None if body else "empty body_draft in batch"))
                if any(b for _, b, _ in out):
                    return out
                last_err = "all drafts in batch were empty"
            except Exception as e:
                last_err = repr(e)
            await asyncio.sleep(2)
        # Both attempts failed/empty — return empty per-slot with the error.
        return [(i, "", last_err) for i, _ in slots_with_idx]

    # --- Body-drafter pool + Resourcer in parallel ---
    # Resourcer only reads titles/materials from the schedule, NOT body_drafts,
    # so it can run concurrently with the body-drafter pool. This saves ~20s
    # off expand total (resourcer is ~15-25s and used to block sequentially).
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

    async def _drafter_pool():
        if "drafter" in saved and isinstance(saved["drafter"], list):
            return [(item.get("idx"), item.get("body") or "", item.get("err"))
                    for item in saved["drafter"] if isinstance(item, dict)]
        if not schedule:
            return []
        # Batch slots by BATCH_SIZE; run batches in parallel. For 12 slots
        # with BATCH_SIZE=3 → 4 parallel calls instead of 12 (no rate-limit
        # storms, smoother latency).
        batches: list[list[tuple[int, TopicSlot]]] = []
        cur: list[tuple[int, TopicSlot]] = []
        for i, s in enumerate(schedule):
            cur.append((i, s))
            if len(cur) >= BATCH_SIZE:
                batches.append(cur); cur = []
        if cur: batches.append(cur)
        batch_results = await asyncio.gather(*[_draft_batch(b) for b in batches])
        results: list[tuple[int, str, str | None]] = []
        for br in batch_results:
            results.extend(br)
        _save_checkpoint("drafter", [
            {"idx": idx, "body": d, "err": e} for idx, d, e in results
        ])
        return results

    async def _resourcer_call():
        if "resourcer" in saved:
            return saved["resourcer"]
        try:
            r = await _call_json(
                resourcer_gen, prompts.RESOURCER_SYSTEM, res_user,
                max_tokens=2048, tool_name="submit_resources", schema=_RESOURCES_SCHEMA,
            )
        except Exception as e:
            r = {"_error": str(e)}
        _save_checkpoint("resourcer", r)
        return r

    _check_cancel()
    draft_results, res_parsed = await asyncio.gather(_drafter_pool(), _resourcer_call())
    _check_cancel()

    drafter_errors: list[str] = []
    for idx, draft, err in draft_results:
        if idx is None or idx >= len(schedule):
            continue
        if draft:
            schedule[idx].body_draft = draft
        if err:
            drafter_errors.append(f"slot #{idx + 1} ({schedule[idx].title[:40]}): {err}")

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
    # v0.59: persist ALL chosen directions for multi-direction packs.
    # Legacy clients can still read pack.chosen_direction (=first one).
    pack.chosen_directions = chosen_directions

    elapsed_total = int(time.time() - t0)
    now = int(time.time())
    pack_json_str = json.dumps(to_jsonable(pack), ensure_ascii=False)

    with db.connect() as con:
        con.execute(
            "UPDATE studio_strategies SET status=?, chosen_direction_idx=?,"
            " pack_json=?, updated_at=?, elapsed_s=?,"
            " partial_state_json=NULL, paused_at_stage=NULL"
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
