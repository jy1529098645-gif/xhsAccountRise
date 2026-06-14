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


# v0.65 ：composer 自己跑 RAG ─ 之前 schedule + body_drafter 都没用 RAG ，
# 全凭 LLM 据 DNA 摘要瞎写。下面这些 helper 让每个 slot 都拿到真实 refs，
# 同时把 「用了哪几个 DNA 数据点」 显式写回 slot.decision_anchors 让 UI 可追溯。

def _slim_ref(r: dict[str, Any]) -> dict[str, Any]:
    """Trim a notes row → compact ref dict (跟 studio_drafts.rag_json 同形状)."""
    return {
        "note_id": r.get("note_id"),
        "title": r.get("title") or "",
        "liked_count": r.get("liked_count") or 0,
        "collected_count": r.get("collected_count") or 0,
        "comment_count": r.get("comment_count") or 0,
        "share_count": r.get("share_count") or 0,
        "url": r.get("url") or "",
        "body_excerpt": (r.get("body") or "")[:400],
        "duration_sec": int((r.get("video_duration_ms") or 0) / 1000),
        "image_urls": r.get("image_urls") or [],
        "cover_image": (r.get("image_urls") or [None])[0],
        "author_nickname": r.get("author_nickname") or "",
        "tags": _safe_tags(r.get("tags_json")),
    }


def _top_benchmark_examples(
    rag_by_slot: dict[int, dict[str, Any]], limit: int = 5,
) -> list[dict[str, Any]]:
    """v0.66 (item1) ：聚合所有 slot 已检索的 RAG refs ，按 note_id 去重、按赞数
    降序取 top-N ，作为「材料清单旁的图文对标帖」。复用已有检索结果 ，零额外成本。
    返回的就是 _slim_ref 形状 ，前端 RagReferenceGrid 可直接渲染。"""
    by_id: dict[str, dict[str, Any]] = {}
    for payload in (rag_by_slot or {}).values():
        for r in (payload.get("refs") or []):
            nid = r.get("note_id")
            if not nid or nid in by_id:
                continue
            by_id[nid] = r
    ranked = sorted(by_id.values(), key=lambda r: r.get("liked_count") or 0, reverse=True)
    return ranked[:max(0, limit)]


def _safe_tags(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw][:8]
    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return [str(t) for t in v][:8]
    except Exception:
        pass
    return []


def _slim_comment(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "comment_id": c.get("comment_id"),
        "content": (c.get("content") or "")[:240],
        "like_count": c.get("like_count") or 0,
        "note_id": c.get("note_id"),
        # v0.65.3 ：来源原贴信息（出稿页用） ─ retrieve.search_comments 已经把
        # title / url / 互动数据 / cover_image 拼好 ，这里直接透传。
        "source_note": c.get("source_note"),
    }


def _slim_hook(h: dict[str, Any]) -> dict[str, Any]:
    return {
        "category": h.get("category") or "",
        "count": h.get("count") or 0,
        "median_likes": h.get("median_likes") or 0,
        "examples": [
            {"title": e.get("title") or "", "liked_count": e.get("liked_count") or 0}
            for e in (h.get("examples") or [])[:3]
        ],
    }


def _retrieve_for_slot(query: str, k_refs: int = 4, n_comments: int = 5) -> dict[str, Any]:
    """Run FTS retrieval for one slot. Empty / no-match → returns empty payload
    (caller still wants the dict shape, just empty)."""
    if not query or len(query.strip()) < 3:
        return {"refs": [], "comments": [], "hooks": []}
    try:
        from ..rag import retrieve as _retrieve
        out = _retrieve.retrieve_for_brief(query, k_notes=k_refs, n_comments=n_comments)
    except Exception:
        return {"refs": [], "comments": [], "hooks": []}
    return {
        "refs": [_slim_ref(r) for r in (out.get("refs") or [])],
        "comments": [_slim_comment(c) for c in (out.get("comments") or [])],
        "hooks": [_slim_hook(h) for h in (out.get("hooks") or [])],
    }


def _format_refs_for_prompt(refs: list[dict[str, Any]], comments: list[dict[str, Any]],
                            hooks: list[dict[str, Any]]) -> str:
    """Render the slot's RAG payload as a prompt-context block.
    Keeps the note_id visible so drafter can quote `[ref:<note_id>]` inline."""
    if not refs and not comments and not hooks:
        return ""
    parts: list[str] = ["【⭐ 本 slot 的真实参考素材（必须引用 ：body 里出现的"
                        "数字/工具名/案例/句式 → 必须从这里来 ，且加 [ref:<note_id>] 标记）】"]
    if refs:
        parts.append("\n  ▸ 同主题真实爆款（按相关度 × 互动量）：")
        for r in refs[:8]:
            tags = "/".join(r.get("tags") or [])
            tag_block = f" [tags: {tags}]" if tags else ""
            body_block = ""
            if r.get("body_excerpt"):
                body_block = f"\n      正文片段 ：{r['body_excerpt'][:160]}"
            parts.append(
                f"    · [ref:{r['note_id']}] @{r.get('author_nickname','?')} "
                f"👍{r['liked_count']:,} ⭐{r['collected_count']:,} 💬{r['comment_count']:,}"
                f"{tag_block}\n      标题 ：{r.get('title','')[:80]}"
                f"{body_block}"
            )
    if comments:
        parts.append("\n  ▸ 真实用户原话（高赞评论 ：写作时尽量复用其情绪/痛点表达 ，不抄字）：")
        for c in comments[:8]:
            parts.append(f"    · ({c.get('like_count',0)}👍) {(c.get('content') or '')[:120]}")
    if hooks:
        parts.append("\n  ▸ 可借鉴的 hook 模板：")
        for h in hooks[:5]:
            ex = " / ".join(e.get("title","")[:30] for e in (h.get("examples") or [])[:2])
            parts.append(
                f"    · {h.get('category')} (n={h.get('count')}, "
                f"中位赞 {int(h.get('median_likes', 0))}){' — ' + ex if ex else ''}"
            )
    parts.append(
        "\n💡 写正文时 ：每出现一个具体数字 / 工具名 / 真实案例 ，"
        "**必须在该句末尾打 [ref:<note_id>] marker**（note_id 取上面 ref 的 id）。"
        "评论原话用「<」「>」尖括号包起来。"
        "未引用过任何 ref 的稿件视作未完成。"
    )
    return "\n".join(parts)


def _compute_kpi_baseline(slot: TopicSlot, dna: dict[str, Any]) -> dict[str, Any]:
    """v0.65 (P3) ：从 DNA artifact 里挑出跟本 slot (hook_type, content_format)
    匹配的样本，算出 median / p90 互动量作为该 slot predicted_likes 的对照基线。
    返回 {median, p90, n, source}；查不到就空 dict。"""
    titles_sec = (dna.get("sections", {}) or {}).get("titles", {}) or {}
    by_cat = titles_sec.get("by_category", {}) or {}
    hk = slot.hook_type or slot.angle
    if not hk:
        return {}
    # Try exact match first, then fuzzy contains.
    cat = by_cat.get(hk)
    if not cat:
        for k, v in by_cat.items():
            if hk and (hk in k or k in hk):
                cat = v; break
    if not cat:
        return {}
    likes = cat.get("likes") or {}
    return {
        "median": int(likes.get("median") or 0),
        "p90": int(likes.get("p90") or 0),
        "p75": int(likes.get("p75") or 0),
        "n": int(cat.get("count") or 0),
        "source": f"DNA · hook_type={hk}",
    }


def _compute_grounding(body: str, refs: list[dict[str, Any]],
                        bo_keywords: list[str]) -> tuple[float, dict[str, Any]]:
    """v0.65 (P4) ：算 grounding score。
    分子 ：body 里 [ref:xxx] marker 出现次数 + 蓝海词 verbatim 命中次数
    分母 ：段落数（按 \n\n 切，最少 1）
    返回 (score, breakdown_dict)。
    """
    if not body:
        return 0.0, {"ref_markers": 0, "keyword_hits": 0, "segments": 0}
    import re as _re
    ref_markers = len(_re.findall(r"\[ref:[A-Za-z0-9_\-]+\]", body))
    kw_hits = sum(1 for k in (bo_keywords or []) if k and k in body)
    segments = max(1, len([p for p in body.split("\n\n") if p.strip()]))
    score = round((ref_markers + kw_hits) / segments, 2)
    return score, {
        "ref_markers": ref_markers,
        "keyword_hits": kw_hits,
        "segments": segments,
        "keywords_matched": [k for k in (bo_keywords or []) if k and k in body][:10],
    }


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
            "INSERT INTO studio_composer_packs"
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
            "INSERT INTO studio_composer_packs"
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
                    # v0.65 (P0+P1) ：drafter 必须声明本条 body 用到的真实 ref note_id。
                    # 跟 body 里的 [ref:<note_id>] inline marker 一一对应。
                    "references_used": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
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
                    "flexible_window": {"type": "string"},
                    # v0.62 ：每个 slot 输出 2 个 alternative_versions
                    "alternative_versions": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    # v0.65 (P1) ：结构化锚点。让 publish_rationale / decision_rationale
                    # 不只是自由文本 ，而是带 DNA 数据点引用（蓝海词 / heatmap cell / hook
                    # 类别 / tag / 评论原话）。前端把每个 anchor 渲染成可点 chip ，hover
                    # 显示 n / median / source ，用户能看出 AI 不是凭感觉决定的。
                    "decision_anchors": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "publish_anchors": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
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
        # v0.66 (item5) ：两套可对比成功指标方案（稳健 / 进取）。
        "metrics_plans": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "metrics": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
            },
        },
        # 兼容字段 ：旧模型/旧 prompt 仍可能只回 success_metrics。
        "success_metrics": {"type": "array", "items": {"type": "string"}},
    },
}


def _normalise_metrics_plans(raw: Any) -> list[dict]:
    """v0.66 (item5) ：把 LLM 回的 metrics_plans 规整成
    [{"label": str, "metrics": [str], "rationale": str}]。容错各种走形 ：
    - 不是 list → []
    - item 是 str → 包成单指标方案
    - metrics 是 str → 拆成单元素 list
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            if item.strip():
                out.append({"label": f"方案{i + 1}", "metrics": [item.strip()], "rationale": ""})
            continue
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics")
        if isinstance(metrics, str):
            metrics = [metrics]
        metrics = [str(m).strip() for m in (metrics or []) if str(m).strip()]
        if not metrics:
            continue
        out.append({
            "label": str(item.get("label") or f"方案{i + 1}"),
            "metrics": metrics,
            "rationale": str(item.get("rationale") or ""),
        })
    return out[:3]  # 最多 3 套，避免 UI 拥挤


async def expand(
    pack_id: str,
    chosen_idx: int,
    # v0.51: Claude defaults dropped. Topic creativity → gpt-4o + deepseek
    # for diversity. Scheduler (planning reasoning) → gpt-4o. Resourcer +
    # body drafter (volume / mechanical) → deepseek. Net cost ≈ 1/5 vs
    # Sonnet, latency similar or faster.
    # v0.61: drafter 切回 claude:sonnet。原因 ：deepseek/gpt-4o 写出来的初稿
    # 像「AI 教程口吻」，没有活人感（"本文将介绍 5 款工具..." / "首先..."）。
    # Claude 在 voice / 情绪 / emoji / 自暴弱点 / 真人测评感 上明显更自然，
    # 而 voice 是起号最关键的差异化。其它 agent（topicgen / scheduler /
    # resourcer）仍走便宜模型，因为它们只产结构不产文字。Net cost 约 +30%，
    # 但起号成本里 voice 质量比 token 钱重要得多。
    topicgen_spec: str = "openai:gpt-4o,deepseek",
    scheduler_spec: str = "openai:gpt-4o",
    resourcer_spec: str = "deepseek",
    drafter_spec: str = "claude:sonnet",
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
    # v0.62 ：body_drafter 池开关。默认 False ：Strategy 不再批量生成 body draft，
    # 改为只生 schedule 大纲 + alternatives。每篇的实际正文由用户在 Composer 多
    # agent 流程里逐篇生成（critic + refiner 把关 = 更高质量 + 节省 ~30 篇 LLM 调用）。
    # 老用户想要旧行为可以传 True 强制生成 body drafts。
    generate_body_drafts: bool = False,
) -> dict[str, Any]:
    """Phase 2: turn N chosen directions into a full StrategyPack."""
    db.apply_migrations(verbose=False)
    t0 = time.time()
    # v0.59.4: studio_composer_packs lives inside the active library's .db file,
    # so a pack created in lib A becomes invisible once user switches active
    # lib to B. Auto-recover: if not found in active lib, scan all libs.
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT input_json, directions_json, library_id, platform"
            " FROM studio_composer_packs WHERE pack_id = ?", (pack_id,),
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
                    " FROM studio_composer_packs WHERE pack_id = ?", (pack_id,),
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
                    "UPDATE studio_composer_packs"
                    " SET partial_state_json = NULL, paused_at_stage = NULL,"
                    " status = 'directions', updated_at = ?"
                    " WHERE pack_id = ?",
                    (int(time.time()), pack_id),
                )
        except Exception:
            pass

    with db.connect(read_only=True) as con:
        cur_row = con.execute(
            "SELECT status, updated_at FROM studio_composer_packs WHERE pack_id = ?",
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
            "UPDATE studio_composer_packs"
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
                generate_body_drafts=generate_body_drafts,
            )
    except (jobs.CancelRequested, asyncio.CancelledError):
        # User pressed pause. partial_state_json was already checkpointed
        # by _expand_inner at each stage boundary.
        try:
            with db.connect() as con:
                con.execute(
                    "UPDATE studio_composer_packs SET status='paused',"
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
                    "UPDATE studio_composer_packs SET status='expand_failed',"
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
    # v0.62 ：v0.62.10 修复 NameError — _drafter_pool 引用了这个标志位，
    # 但之前没透传进来，导致 expand 跑到 drafter pool 时 NameError 崩。
    generate_body_drafts: bool = False,
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
                    "SELECT partial_state_json FROM studio_composer_packs WHERE pack_id=?",
                    (pack_id,),
                ).fetchone()
                cur = {}
                if row and row["partial_state_json"]:
                    try: cur = json.loads(row["partial_state_json"])
                    except Exception: cur = {}
                cur[stage] = payload
                con.execute(
                    "UPDATE studio_composer_packs SET partial_state_json=?,"
                    " paused_at_stage=?, updated_at=? WHERE pack_id=?",
                    (json.dumps(cur, ensure_ascii=False), stage, int(time.time()), pack_id),
                )
        except Exception:
            pass

    def _load_checkpoints() -> dict[str, Any]:
        try:
            with db.connect(read_only=True) as con:
                row = con.execute(
                    "SELECT partial_state_json FROM studio_composer_packs WHERE pack_id=?",
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
                    "UPDATE studio_composer_packs SET partial_state_json=NULL,"
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

    # v0.61.5 ：startup_phase 用户偏好。AI 仍可据 DNA / 报告微调，但有了
    # 用户明示倾向应主要遵从。
    sp = (getattr(inp, "startup_phase", "") or "").lower()
    startup_section = ""
    if sp == "cold":
        startup_section = (
            "\n\n【👤 用户启动阶段倾向 ：冷启动（0 粉 / 陌生人）】\n"
            "  · 前 1/2 周期 重点营造人设 + 痛点共鸣，几乎不卖货\n"
            "  · 转化期只在最后 1/4 周期 才上力度\n"
            "  · 产品/课程/品牌名只在合适的 slot 出现，且要包裹在真实场景里\n"
        )
    elif sp == "warm":
        startup_section = (
            "\n\n【🔥 用户启动阶段倾向 ：热启动（已有粉丝 / 行业资源）】\n"
            "  · 早期就可以适度强化卖点 / 转化路径\n"
            "  · 人设建立期可缩短（前 1/4 周期足够）\n"
            "  · 互动 + 转化贯穿整个周期\n"
        )
    elif sp == "hybrid":
        startup_section = (
            "\n\n【🌗 用户启动阶段倾向 ：混合启动】\n"
            "  · 前期人设 + 后期转化，标准 4 阶段曲线\n"
            "  · 据 DNA / 报告信号微调具体比例\n"
        )
    # sp == "" / "auto" → AI 自己据 DNA / 报告决定（无额外 prompt）

    # v0.61.13 ：用户对内容形式（图文 / 短视频 / 混合）的硬偏好。
    # 跟 startup_phase 不同 ：这一项是用户对 content_format 字段的明确约束，
    # 不是软建议 — 用户选了「纯图文」就别给视频 slot。
    cfp = (getattr(inp, "content_format_preference", "") or "").lower()
    cfp_section = ""
    if cfp == "tuwen_only":
        cfp_section = (
            "\n\n【📝 用户硬约束 ：全部图文】\n"
            "  · schedule 里每个 slot 的 content_format 都必须填「图文」\n"
            "  · 即使 DNA 显示短视频高赞，也不要排短视频/长视频/直播\n"
            "  · BODY_DRAFTER 也只会按图文格式写正文（不会出现脚本/分镜）\n"
        )
    elif cfp == "video_only":
        cfp_section = (
            "\n\n【🎬 用户硬约束 ：全部短视频】\n"
            "  · schedule 里每个 slot 的 content_format 都必须填「短视频」\n"
            "  · 即使 DNA 显示图文高赞，也不排图文 — 用户明确要短视频\n"
            "  · BODY_DRAFTER 会写分镜脚本（不是图文文章）\n"
        )
    elif cfp == "mixed":
        cfp_section = (
            "\n\n【🔀 用户硬约束 ：图文 + 短视频混合】\n"
            "  · schedule 里必须**同时**有图文和短视频 slot（不能全部一种）\n"
            "  · 具体比例据 DNA + 选题类型自决 ：教程 / 测评偏图文，\n"
            "    剧情 / 故事 / 段子偏短视频，干货长文留图文\n"
            "  · 至少 30% 一种 + 至少 30% 另一种作平衡\n"
        )
    # cfp == "" / "auto" → AI 按 DNA content_format 真实分布自决（无额外 prompt）

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
        f"{startup_section}"
        f"{cfp_section}"
        f"{pctx_block}"
        f"{report_block}\n"
        f"【运营约束】cycle_weeks={inp.cycle_weeks}, posts_per_week={inp.posts_per_week}"
        f" ⇒ 必须排出 **正好 {topic_count} 篇**（不是「大概 N 篇」，不是「典型 1 篇/周」）\n"
        f"【用户其它约束】\n{prompts.input_blurb(inp)}\n\n"
        f"【该平台 DNA】\n{prompts.dna_blurb(dna)}\n\n"
        f"【该平台发布时段热力图】\n{_format_timing(timing_heatmap)}\n\n"
        f"{phase_rules}\n\n"
        f"🚨 **硬约束 ：schedule 数组长度必须 = {topic_count}**。"
        f"不要因为「一周排 1 篇就够了」的直觉去删减 — 用户明确选了每周 {inp.posts_per_week} 篇，"
        f"就是要 {topic_count} 篇，少一篇都不行。如果同周内题材重复，宁可多写几个角度变体也别少出。\n"
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
    # v0.65 ：大计数时 ，告诉 LLM **跳过 alternative_versions / decision_anchors /
    # publish_anchors** 让 schedule 数组减肥到 ~150 tokens / slot ，避免在 12+ 篇
    # 时 schedule[] 中段被 16K 输出上限截断 ─ 这是 「大量 AI 漏排」 的根因。
    # 之前没 hint 时 LLM 老老实实给每个 slot 写 2 个 alternatives + 锚点 array ，
    # 一个 slot 就 280-400 tokens ，28 slots = 11-14K 已经卡输出上限 + 还要塞
    # weekly_themes / series_thesis → 必然截断。
    if topic_count > 10:
        sched_user += (
            "\n\n⚡ **大计数减肥指令** ：本次有 " + str(topic_count) + " 篇 schedule slot ，"
            "为避免输出 token 超限被截断 ，请 ：\n"
            "  · `alternative_versions` 留空 [] （后续可单独再生成）\n"
            "  · `decision_anchors / publish_anchors` 留空 []（用户能从 publish_rationale "
            "里读到判断逻辑就够）\n"
            "  · `outline` 每篇 3-4 条（不是 5-6 条）\n"
            "  · `weekly_themes` 每周一句话 notes 即可\n"
            "  ▶ 主目标 ：保证 schedule 数组长度 = " + str(topic_count) + " ，每篇 title + outline + angle + "
            "publish_slot 完整 ，其它字段可以简洁。"
        )
    # Default max_tokens scaled to topic_count : reduced from 280 → 200 per slot
    # since we now ask LLM to skip the heavy alternative_versions / anchors when
    # topic_count > 10. Cap at 14000 (gpt-4o output limit is 16K, leave headroom).
    _per_slot = 200 if topic_count > 10 else 280
    _default_sched_tokens = max(6000, min(14000, topic_count * _per_slot + 800))
    async def _try_scheduler(user_payload: str, max_tokens: int = _default_sched_tokens):
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
            flexible_window=str(s.get("flexible_window", "")),
            # v0.62 ：alternative_versions — LLM 给的 2 个次选方案 list of dict。
            # 类型保留为原始 dict，UI 自己 render。空 list 兼容老 pack。
            alternative_versions=[
                dict(a) for a in (s.get("alternative_versions") or [])
                if isinstance(a, dict)
            ],
            # v0.65 (P1) ：结构化锚点 ─ 透传 LLM 输出的 anchors 数组到 slot。
            decision_anchors=[
                dict(a) for a in (s.get("decision_anchors") or [])
                if isinstance(a, dict)
            ],
            publish_anchors=[
                dict(a) for a in (s.get("publish_anchors") or [])
                if isinstance(a, dict)
            ],
        )
        for _raw in schedule_raw
        for s in [_to_slot_dict(_raw)]
    ]

    # ---- v0.62.18 / .19 ：enforce exact slot count ----
    # cycle_weeks × posts_per_week is the count the user picked in step 2.
    # Schedulers tend to under-deliver here — common patterns ：
    #   - LLM mentally collapses to "~1 post / week" prior, ignoring posts_per_week
    #   - Token pressure on big counts (28+ slots × full detail) truncates output
    #
    # Strategy:
    #   1. Trim if over (rare).
    #   2. If under but > 0 ：fire a "gap-fill" second call asking for ONLY
    #      the missing K slots, providing the existing K' titles as anti-
    #      duplication context. Append. This yields REAL slots, not stubs.
    #   3. If still under after gap-fill (rare network/LLM hiccup), pad with
    #      placeholder stubs as a last-resort safety net so the count is
    #      always exact. Placeholder titles flag the gap for the user.
    # v0.63: bug fix — the saved-checkpoint guard used to be just
    # `"scheduler_gap" not in saved`, which skipped the gap-fill block
    # whenever a previous run had RECORDED a scheduler_gap checkpoint, even
    # if that checkpoint was empty / failed. So once a resume happened
    # with an empty gap-fill, the placeholder slots persisted forever.
    # Now we also accept a stale empty checkpoint and re-try the gap-fill.
    saved_gap = saved.get("scheduler_gap")
    saved_gap_has_slots = (
        isinstance(saved_gap, dict) and bool(saved_gap.get("schedule"))
    )
    schedule_short_warning: str | None = None
    if len(schedule) > topic_count:
        schedule = schedule[:topic_count]
    elif 0 < len(schedule) < topic_count and not saved_gap_has_slots:
        missing = topic_count - len(schedule)
        existing_titles = "\n".join(
            f"  - W{s.week}D{s.day_of_week} {s.title}" for s in schedule
        )
        # Build a focused gap-fill prompt. Critical ：tell the LLM NOT to
        # duplicate existing slots + give exact missing count up front so
        # there's no ambiguity. Keep weekly_themes empty in the response —
        # we only consume schedule[].
        gap_user = (
            f"【已经排好的 {len(schedule)} 篇】\n{existing_titles}\n\n"
            f"【缺口】用户原本要 {topic_count} 篇，目前只有 {len(schedule)} 篇。"
            f"请只补出剩下的 **{missing} 篇** schedule slot（不要重复上面已有的标题 / 角度组合）。\n\n"
            f"【方向】{chosen.name} — {chosen.positioning_statement}\n"
            f"【受众】{chosen.target_audience}\n"
            f"【周期】共 {inp.cycle_weeks} 周 × {inp.posts_per_week} 篇/周\n\n"
            f"严格按 system schema 输出 ：schedule 数组长度必须 = {missing}，"
            f"weekly_themes 可留空 []。"
        )

        async def _gap_fill_with_fallback(user_payload: str) -> list[Any]:
            """v0.65.2 ：终极 gap-fill ─ 两阶段。
            阶段 A ：用 _SCHEDULE_SCHEMA 跑 chunk-of-4。schema 重 ，会截断但有概率成。
            阶段 B ：上面没填够时 ，**切换到 _TOPICS_SCHEMA**（极轻量 ：只要 title/
                      title_variants/angle/hook_type/outline/materials_needed/intent，
                      完全没 alternative_versions / anchors），按需要的剩余数量整批 拉一次。
                      _TOPICS_SCHEMA 体积是 _SCHEDULE_SCHEMA 的 ~1/3 ，single-call 出
                      20+ 条都不会截断。这一步几乎 100% 命中。
            阶段 C ：要还有零星缺口 → per-slot 单条 _TOPICS_SCHEMA 调用 ，每次只要 1 条。

            日志输出 ：每个阶段把命中数 / 失败原因打 stderr ，用户终端可见。

            Returns ：合并所有阶段的 schedule items 列表。
            """
            import sys as _sys
            CHUNK = 4
            collected: list[Any] = []
            existing_titles_running = [s.title for s in schedule if s.title]
            target = missing

            primary = scheduler_spec.lower()
            fb_spec = ("deepseek" if ("openai" in primary or "gpt" in primary)
                       else "openai:gpt-4o")
            fb_gen = None
            try:
                fb_gen = registry.build(fb_spec)[0]
            except Exception:
                fb_gen = None

            # ---- 阶段 A ：chunked _SCHEDULE_SCHEMA ----
            attempts_left = max(2, (target + CHUNK - 1) // CHUNK)
            while len(collected) < target and attempts_left > 0:
                attempts_left -= 1
                want_now = min(CHUNK, target - len(collected))
                avoid = "\n".join(f"  - {t}" for t in existing_titles_running[-30:]) or "  - （无）"
                chunk_user = (
                    f"【方向】{chosen.name} — {chosen.positioning_statement}\n"
                    f"【受众】{chosen.target_audience}\n"
                    f"【周期】{inp.cycle_weeks} 周 × {inp.posts_per_week} 篇/周\n\n"
                    f"【已经写过的标题（请勿重复 / 角度也别撞）】\n{avoid}\n\n"
                    f"请只补 **正好 {want_now} 篇** 新的 schedule slot ，跟上面任何标题"
                    f"都明显不同。weekly_themes / alternative_versions / decision_anchors / "
                    f"publish_anchors **全部留空 []**（这是减肥 ，不是偷懒 ，避免输出 token 上限被吃光）。\n"
                    f"严格按 schema 输出 schedule 数组（长度 = {want_now}）。"
                )
                added_this_round = 0
                for gen, label in [(scheduler_gen, "primary"), (fb_gen, "fallback")]:
                    if gen is None:
                        continue
                    try:
                        r = await _call_json(
                            gen, prompts.SCHEDULER_SYSTEM, chunk_user,
                            max_tokens=max(3500, want_now * 800 + 800),
                            tool_name="submit_schedule",
                            schema=_SCHEDULE_SCHEMA,
                        )
                        items = (r.get("schedule") or [])[:want_now]
                        for it in items:
                            if isinstance(it, dict):
                                title = str(it.get("title") or "").strip()
                                if not title:
                                    continue
                                if title in existing_titles_running:
                                    continue
                                collected.append(it)
                                existing_titles_running.append(title)
                                added_this_round += 1
                        if added_this_round > 0:
                            break
                    except Exception as e:
                        print(f"[expand.gap_A] {label} {gen.model} 失败 ：{e!r}", file=_sys.stderr)
                        continue
                print(f"[expand.gap_A] +{added_this_round} (collected={len(collected)}/{target})",
                      file=_sys.stderr)
                if added_this_round == 0:
                    # _SCHEDULE_SCHEMA 这一轮拿不出来 → 跳到阶段 B（轻 schema） ，
                    # 不要在 A 死循环。
                    break

            # ---- 阶段 B ：用 _TOPICS_SCHEMA 一次性补齐剩余 ----
            still_missing = target - len(collected)
            if still_missing > 0:
                avoid = "\n".join(f"  - {t}" for t in existing_titles_running[-40:]) or "  - （无）"
                topics_user = (
                    f"【方向】{chosen.name} — {chosen.positioning_statement}\n"
                    f"【受众】{chosen.target_audience}\n"
                    f"【周期】共需 {still_missing} 篇选题 ，跟下面已有标题完全不同。\n\n"
                    f"【已用过的标题】\n{avoid}\n\n"
                    f"请按 system schema 输出 topics 数组（长度 = {still_missing}）。"
                    f"每条 ：title + 1-2 个 variants + angle + hook_type + 3-4 条 outline + "
                    f"materials_needed + intent。"
                )
                for gen, label in [(scheduler_gen, "primary"), (fb_gen, "fallback")]:
                    if gen is None:
                        continue
                    try:
                        r = await _call_json(
                            gen, prompts.TOPICGEN_SYSTEM, topics_user,
                            max_tokens=max(2500, still_missing * 280 + 600),
                            tool_name="submit_topics",
                            schema=_TOPICS_SCHEMA,
                        )
                        items = (r.get("topics") or [])[:still_missing]
                        for it in items:
                            if isinstance(it, dict):
                                title = str(it.get("title") or "").strip()
                                if not title or title in existing_titles_running:
                                    continue
                                collected.append(it)
                                existing_titles_running.append(title)
                        print(f"[expand.gap_B] {label} {gen.model} ：+{len(items)} topics "
                              f"(collected={len(collected)}/{target})", file=_sys.stderr)
                        if len(collected) >= target:
                            break
                    except Exception as e:
                        print(f"[expand.gap_B] {label} {gen.model} 失败 ：{e!r}", file=_sys.stderr)
                        continue

            # ---- 阶段 C ：per-slot 1-by-1 兜底 ----
            # 阶段 B 也没填够 → 每个缺口单独要 1 条 topic。轻 schema + 单条调用 = 几乎不会失败。
            still_missing = target - len(collected)
            per_slot_attempts = still_missing * 2
            while len(collected) < target and per_slot_attempts > 0:
                per_slot_attempts -= 1
                avoid = "\n".join(f"  - {t}" for t in existing_titles_running[-25:]) or "  - （无）"
                one_user = (
                    f"【方向】{chosen.name} — {chosen.positioning_statement}\n"
                    f"【受众】{chosen.target_audience}\n\n"
                    f"【已用过】\n{avoid}\n\n"
                    f"请只出 **1 条** 跟上面完全不同的选题。按 schema 输出 topics 数组（长度 = 1）。"
                )
                got_one = False
                for gen, label in [(scheduler_gen, "primary"), (fb_gen, "fallback")]:
                    if gen is None:
                        continue
                    try:
                        r = await _call_json(
                            gen, prompts.TOPICGEN_SYSTEM, one_user,
                            max_tokens=1200, tool_name="submit_topics",
                            schema=_TOPICS_SCHEMA,
                        )
                        items = r.get("topics") or []
                        for it in items[:1]:
                            if isinstance(it, dict):
                                title = str(it.get("title") or "").strip()
                                if not title or title in existing_titles_running:
                                    continue
                                collected.append(it)
                                existing_titles_running.append(title)
                                got_one = True
                                break
                        if got_one:
                            break
                    except Exception as e:
                        print(f"[expand.gap_C] {label} {gen.model} 失败 ：{e!r}", file=_sys.stderr)
                        continue
                if not got_one:
                    print(f"[expand.gap_C] 一轮 per-slot 没拿到新条目 ，剩余预算 {per_slot_attempts}",
                          file=_sys.stderr)
            print(f"[expand.gap] 总命中 ：{len(collected)}/{target}", file=_sys.stderr)
            return collected

        try:
            # gap_user 不再被 _gap_fill_with_fallback 用 ，但保留参数避免破坏调用签名。
            new_raw = await _gap_fill_with_fallback(gap_user)
            gap_resp = {"schedule": new_raw}
            for _raw in new_raw[:missing]:
                s = _to_slot_dict(_raw)
                schedule.append(TopicSlot(
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
                    flexible_window=str(s.get("flexible_window", "")),
                    alternative_versions=[
                        dict(a) for a in (s.get("alternative_versions") or [])
                        if isinstance(a, dict)
                    ],
                    decision_anchors=[
                        dict(a) for a in (s.get("decision_anchors") or [])
                        if isinstance(a, dict)
                    ],
                    publish_anchors=[
                        dict(a) for a in (s.get("publish_anchors") or [])
                        if isinstance(a, dict)
                    ],
                ))
            _save_checkpoint("scheduler_gap", gap_resp)
            existing_warn = sched_parsed.get("_warning")
            note = (
                f"gap-fill produced {len(new_raw)} of {missing} missing slot(s)"
                if len(new_raw) >= missing
                else f"gap-fill only produced {len(new_raw)} of {missing} requested missing slot(s)"
            )
            sched_parsed["_warning"] = f"{existing_warn} | {note}" if existing_warn else note
        except Exception as e:
            # Gap-fill failed → fall through to placeholder pad below.
            existing_warn = sched_parsed.get("_warning")
            sched_parsed["_warning"] = (
                f"{existing_warn} | gap-fill error: {e!r}"
                if existing_warn else f"gap-fill error: {e!r}"
            )

    # v0.65.2 ：之前在这里给缺口塞「待补 #N (AI 漏排) 」 占位 stub —— 用户体感差，
    # 看见就疑似产品坏掉。彻底删除占位逻辑 ，改为 ：
    #   1. 极端情况（gap_A + gap_B + gap_C 全失败）这里只剩缺口
    #   2. 不塞假 stub ，转而调用 TOPICGEN_SYSTEM 做一次 LAST-RESORT 整批补齐
    #      （3 家 LLM 轮流试 ：scheduler primary / cross-family / claude:sonnet）
    #   3. 第 2 步也救不回来才接受 schedule 比 topic_count 短 ，但**绝不**塞占位标题
    if len(schedule) < topic_count:
        import sys as _sys
        still_missing = topic_count - len(schedule)
        print(f"[expand] last-resort fill 启动 ，缺口 {still_missing}", file=_sys.stderr)
        existing_titles_running = [s.title for s in schedule if s.title]
        used_positions = {(s.week, s.day_of_week) for s in schedule}
        # 第 3 路 last-resort 备援家 ：claude:sonnet（前面 primary / cross-family
        # 都已经试过 ，这里换一家新的 ，避免「同 1 家 LLM 反复失败导致全程空转」）。
        try:
            claude_fb_gen = registry.build("claude:sonnet")[0]
        except Exception:
            claude_fb_gen = None
        primary_lc = scheduler_spec.lower()
        cross_spec = ("deepseek" if ("openai" in primary_lc or "gpt" in primary_lc)
                       else "openai:gpt-4o")
        try:
            cross_gen = registry.build(cross_spec)[0]
        except Exception:
            cross_gen = None

        last_resort_user = (
            f"【方向】{chosen.name} — {chosen.positioning_statement}\n"
            f"【受众】{chosen.target_audience}\n"
            f"【缺口】用户原本要 {topic_count} 篇 ，目前只有 {len(schedule)} 篇 ，"
            f"请只补 **正好 {still_missing} 篇** 跟下面已用标题完全不同的选题。\n\n"
            f"【已用标题】\n" + (
                "\n".join(f"  - {t}" for t in existing_titles_running[-40:]) or "  - （无）"
            ) + "\n\n"
            f"严格按 schema 输出 topics 数组（长度 = {still_missing}）。每条 ：title + "
            f"1-2 variants + angle + hook_type + 3-4 条 outline + materials_needed + intent。"
        )
        for try_gen, label in [
            (scheduler_gen, "primary"),
            (cross_gen, f"cross={cross_spec}"),
            (claude_fb_gen, "claude:sonnet"),
        ]:
            if try_gen is None or len(existing_titles_running) - len(schedule) >= still_missing:
                continue
            if len(schedule) + (len(existing_titles_running) - len([s for s in schedule if s.title])) >= topic_count:
                break
            try:
                r = await _call_json(
                    try_gen, prompts.TOPICGEN_SYSTEM, last_resort_user,
                    max_tokens=max(2500, still_missing * 280 + 600),
                    tool_name="submit_topics",
                    schema=_TOPICS_SCHEMA,
                )
                items = r.get("topics") or []
                appended = 0
                for it in items[:still_missing - appended]:
                    if not isinstance(it, dict):
                        continue
                    title = str(it.get("title") or "").strip()
                    if not title or title in existing_titles_running:
                        continue
                    # 给这条 last-resort topic 排一个 (week, day_of_week)
                    next_pos = None
                    for w in range(1, max(1, inp.cycle_weeks) + 1):
                        for d in range(7):
                            if (w, d) not in used_positions:
                                next_pos = (w, d); break
                        if next_pos: break
                    if next_pos is None:
                        # 落到最后一周补
                        next_pos = (max(1, inp.cycle_weeks), 0)
                    w, d = next_pos
                    used_positions.add((w, d))
                    schedule.append(TopicSlot(
                        week=w, day_of_week=d, publish_slot="",
                        title=title,
                        title_variants=[str(x) for x in (it.get("title_variants") or [])],
                        angle=str(it.get("angle", "")),
                        hook_type=str(it.get("hook_type", "")),
                        outline=[str(x) for x in (it.get("outline") or [])],
                        materials_needed=[str(x) for x in (it.get("materials_needed") or [])],
                        intent=str(it.get("intent", "")),
                        content_format=(
                            "图文" if platform == "xiaohongshu"
                            else "短视频" if platform in ("douyin", "kuaishou")
                            else "图文"
                        ),
                        # 没 publish_rationale / decision_rationale ─ 用户看到的就是
                        # 「正常 AI 出的选题」，不再有占位文案。
                    ))
                    existing_titles_running.append(title)
                    appended += 1
                print(f"[expand] last-resort {label} {try_gen.model} ：+{appended}", file=_sys.stderr)
                if len(schedule) >= topic_count:
                    break
            except Exception as e:
                print(f"[expand] last-resort {label} 失败 ：{e!r}", file=_sys.stderr)
                continue

        # 排好序方便前端按时间渲染
        schedule.sort(key=lambda s: (s.week, s.day_of_week))

        # 如果**所有 LLM 路径都跑完还是不够** ─ 接受短缺 ，前端会显示「实际 N 篇」。
        # 不再塞 「待补 #N (AI 漏排)」 占位 stub。
        if len(schedule) < topic_count:
            schedule_short_warning = (
                f"final schedule {len(schedule)}/{topic_count} ：所有 AI 路径都尝试过 ，"
                f"短缺 {topic_count - len(schedule)} 篇。可能是 API 配额耗尽或库主题"
                f"过窄。点 「重新生成」 重试 ，或缩小目标 cycle_weeks×posts_per_week。"
            )
        else:
            schedule_short_warning = None

    if schedule_short_warning:
        # Surface in the saved checkpoint warning slot so the response can
        # carry the diagnostic up to the UI without changing the schema.
        existing_warn = sched_parsed.get("_warning")
        sched_parsed["_warning"] = (
            f"{existing_warn} | {schedule_short_warning}" if existing_warn
            else schedule_short_warning
        )

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

    # v0.63: cross-slot text-reuse prevention. Within one strategy cycle the
    # batch drafter occasionally writes very similar openings / hook
    # sentences across different slots (especially when title+angle overlap).
    # We track every written slot's opening line and:
    #   (a) feed already-used openings as a negative-prompt to retries
    #   (b) detect collisions post-hoc and re-draft the duplicate slots
    # Single-character-level signatures keep collision check cheap.
    import re as _re

    def _opening(text: str) -> str:
        """First non-empty content line (the hook). What tends to repeat."""
        for ln in (text or "").splitlines():
            s = ln.strip()
            if s and not s.startswith(("[", "【", "—", "-", "*", "#")):
                return s[:80]
        return (text or "").strip()[:80]

    def _normalise(text: str) -> str:
        """Cheap signature: drop whitespace/punctuation for fuzzy compare."""
        return _re.sub(r"[\s　\.,，。!！？\?\-—·*…]+", "", text or "").lower()

    def _avoid_block(used_openings: list[str], used_titles: list[str]) -> str:
        if not used_openings and not used_titles:
            return ""
        out = ["\n\n⚠️ **同一策略周期内禁止复用文本（硬约束，不是建议）**："]
        if used_openings:
            out.append("已被其它 slot 用过的开头第一句 / hook（请用完全不同的句式 + 不同的具体细节）：")
            out.extend(f"  - {o}" for o in used_openings[-20:])
        if used_titles:
            out.append("已写过的标题（请确保正文角度 / 例子 / 数字与这些篇都不一样）：")
            out.extend(f"  - {t}" for t in used_titles[-20:])
        return "\n".join(out)

    # Cross-family fallback generator for the per-slot retry pass. When the
    # primary drafter family keeps returning empty for a particular slot
    # (e.g. tool_use schema quirks on a specific model), try the other
    # major family before declaring the slot dead.
    def _fallback_spec() -> str:
        primary = drafter_spec.lower()
        if "openai" in primary or "gpt" in primary:
            return "deepseek"
        if "deepseek" in primary:
            return "openai:gpt-5"
        return "openai:gpt-5"

    try:
        drafter_fallback = registry.build(_fallback_spec())[0]
    except Exception:
        drafter_fallback = None

    # v0.65 (P0) ：每个 slot 预先跑一次 RAG ，结果同时
    #   (a) 喂给 body drafter 作 prompt context（=「AI 真看了哪几篇」）
    #   (b) 持久化到 schedule[i].rag_refs / rag_comments / rag_hooks（=「UI 可追溯」）
    rag_by_slot: dict[int, dict[str, Any]] = {}
    for i, s in enumerate(schedule):
        query = " ".join(p for p in [s.title, s.angle, s.hook_type] if p)
        rag_by_slot[i] = _retrieve_for_slot(query, k_refs=4, n_comments=5)

    def _refs_block_for_slot(slot_idx: int) -> str:
        rag = rag_by_slot.get(slot_idx) or {}
        return _format_refs_for_prompt(
            rag.get("refs") or [], rag.get("comments") or [], rag.get("hooks") or [],
        )

    async def _draft_batch(slots_with_idx: list[tuple[int, TopicSlot]],
                            avoid_openings: list[str] | None = None,
                            avoid_titles: list[str] | None = None,
                           ) -> list[tuple[int, str, list[str], str | None]]:
        if not slots_with_idx:
            return []
        # v0.65 (P0) ：把 RAG refs 拼到每个 slot block 后面 ，让 drafter 看到
        # 「这一篇的真实素材就这几条」 ─ 引用必须从这里来 + 标 [ref:<note_id>]。
        slot_blocks_parts: list[str] = []
        for i, s in slots_with_idx:
            block = _slot_block(i, s)
            refs_block = _refs_block_for_slot(i)
            if refs_block:
                block += "\n" + refs_block
            slot_blocks_parts.append(block)
        slot_blocks = "\n\n".join(slot_blocks_parts)
        avoid = _avoid_block(avoid_openings or [], avoid_titles or [])
        batch_prompt = (
            f"{direction_block}\n\n"
            f"【一次性给你 {len(slots_with_idx)} 个 slot，请同时为每个写 body_draft】\n\n"
            f"{slot_blocks}\n\n"
            f"按 schema 输出 ：drafts 数组，每项 {{ idx: <对应 slot 编号>, body_draft: <完整正文>, "
            f"references_used: [<本条用到的 note_id 列表>] }}。"
            f" 每个 body_draft 必须按它自己的 content_format 写（图文 vs 短视频脚本 vs 长视频章节差别很大）。"
            f" 不同 slot 之间的开头 hook、具体例子、数字、案例都要明显不同 — 不要换个词复述同一段。"
            f" **强制 ：每条 body 至少出现 1 个 [ref:<note_id>] inline marker；references_used 必须列出对应 note_id。**"
            f"{avoid}"
        )
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
                out: list[tuple[int, str, list[str], str | None]] = []
                returned: dict[int, tuple[str, list[str]]] = {}
                for d in drafts:
                    if not isinstance(d, dict):
                        continue
                    idx_v = d.get("idx", -1)
                    try:
                        idx_i = int(idx_v)
                    except (TypeError, ValueError):
                        continue
                    body_s = str(d.get("body_draft", "")).strip()
                    refs_used = [str(x) for x in (d.get("references_used") or []) if x]
                    returned[idx_i] = (body_s, refs_used)
                for i, _ in slots_with_idx:
                    body, refs_used = returned.get(i, ("", []))
                    out.append((i, body, refs_used,
                                None if body else "empty body_draft in batch"))
                if any(b for _, b, _, _ in out):
                    return out
                last_err = "all drafts in batch were empty"
            except Exception as e:
                last_err = repr(e)
            await asyncio.sleep(2)
        # Both attempts failed/empty — return empty per-slot with the error.
        return [(i, "", [], last_err) for i, _ in slots_with_idx]

    async def _draft_single(slot_idx: int, slot: TopicSlot,
                             avoid_openings: list[str],
                             avoid_titles: list[str],
                             use_fallback: bool = False) -> tuple[int, str, list[str], str | None]:
        """v0.63: single-slot retry — used when batch left a slot empty OR
        when a duplicate hook needs re-drafting. Carries the avoid-list so
        it can't repeat what was already written."""
        gen = drafter_fallback if (use_fallback and drafter_fallback) else drafter_chosen
        avoid = _avoid_block(avoid_openings, avoid_titles)
        refs_block = _refs_block_for_slot(slot_idx)
        prompt = (
            f"{direction_block}\n\n"
            f"【请为下面这 1 个 slot 写完整 body_draft】\n\n"
            f"{_slot_block(slot_idx, slot)}\n"
            + (refs_block + "\n" if refs_block else "")
            + f"按 schema 输出 ：drafts 数组（长度 1）格式 {{ idx: {slot_idx}, body_draft: <完整正文>, "
            f"references_used: [<note_id 列表>] }}。"
            f" 至少 1 个 [ref:<note_id>] inline marker。"
            f"{avoid}"
        )
        try:
            r = await asyncio.wait_for(
                _call_json(
                    gen, prompts.BODY_DRAFTER_BATCH_SYSTEM, prompt,
                    max_tokens=4000,
                    tool_name="submit_body_draft_batch",
                    schema=_BODY_DRAFT_BATCH_SCHEMA,
                ),
                timeout=120,
            )
            drafts = r.get("drafts") or []
            for d in drafts:
                if isinstance(d, dict):
                    body = str(d.get("body_draft", "")).strip()
                    refs_used = [str(x) for x in (d.get("references_used") or []) if x]
                    if body:
                        return (slot_idx, body, refs_used, None)
            return (slot_idx, "", [], "empty body_draft in single retry")
        except Exception as e:
            return (slot_idx, "", [], repr(e))

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
        # v0.62 ：默认不再批量生成 body drafts。schedule 只输出大纲 + 替代方案，
        # 用户进 Composer 多 agent 流程逐篇写（critic + refiner 把关 = 高质量）。
        # 节省 ：~30 篇 × Sonnet $0.01 ≈ $0.30 / 30s × ~6 batches ≈ 3 分钟。
        if not generate_body_drafts:
            return []
        if "drafter" in saved and isinstance(saved["drafter"], list):
            return [(item.get("idx"), item.get("body") or "",
                     item.get("refs_used") or [], item.get("err"))
                    for item in saved["drafter"] if isinstance(item, dict)]
        if not schedule:
            return []
        # Phase A: batch sweep. Batches run in parallel for latency.
        batches: list[list[tuple[int, TopicSlot]]] = []
        cur: list[tuple[int, TopicSlot]] = []
        for i, s in enumerate(schedule):
            cur.append((i, s))
            if len(cur) >= BATCH_SIZE:
                batches.append(cur); cur = []
        if cur: batches.append(cur)
        batch_results = await asyncio.gather(*[_draft_batch(b) for b in batches])
        results: dict[int, tuple[str, list[str], str | None]] = {}
        for br in batch_results:
            for idx, body, refs_used, err in br:
                results[idx] = (body, refs_used, err)

        # v0.63 Phase B: recover empty slots + de-duplicate similar openings.
        # Two failure modes patched here:
        #   1. Batch returned empty body_draft for some slot → re-draft it
        #      single-slot, then cross-family fallback if still empty.
        #   2. Two slots got nearly identical opening lines → keep the
        #      longer draft, re-draft the other with the loser's signature
        #      added to the avoid-list.
        used_openings: list[str] = []
        used_titles: list[str] = []
        seen_sig_to_idx: dict[str, int] = {}
        to_redraft: list[int] = []
        for idx in sorted(results.keys()):
            body, _refs_used, err = results[idx]
            slot = schedule[idx] if 0 <= idx < len(schedule) else None
            if slot and slot.title:
                used_titles.append(slot.title)
            if not body:
                to_redraft.append(idx)
                continue
            sig = _normalise(_opening(body))
            if sig and sig in seen_sig_to_idx:
                other = seen_sig_to_idx[sig]
                loser = idx if len(body) <= len(results[other][0]) else other
                if loser not in to_redraft:
                    to_redraft.append(loser)
                if loser == other:
                    seen_sig_to_idx[sig] = idx
                    used_openings.append(_opening(body))
                # Else: loser is `idx`, keep `other`'s opening in pool, don't
                # add this one yet (will be added after re-draft).
            else:
                if sig:
                    seen_sig_to_idx[sig] = idx
                used_openings.append(_opening(body))

        # Re-draft sequentially so each call sees the updated avoid-list.
        # Two LLM-call ceiling per redraft (primary + cross-family fallback).
        for idx in to_redraft:
            if not (0 <= idx < len(schedule)):
                continue
            _check_cancel()
            slot = schedule[idx]
            _, body, refs_used, err = await _draft_single(
                idx, slot, used_openings, used_titles, use_fallback=False,
            )
            if not body and drafter_fallback is not None:
                _, body, refs_used, err = await _draft_single(
                    idx, slot, used_openings, used_titles, use_fallback=True,
                )
            results[idx] = (body, refs_used, err)
            if body:
                used_openings.append(_opening(body))

        out_list = [(idx, body, refs_used, err)
                    for idx, (body, refs_used, err) in sorted(results.items())]
        _save_checkpoint("drafter", [
            {"idx": idx, "body": d, "refs_used": ru, "err": e}
            for idx, d, ru, e in out_list
        ])
        return out_list

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
    # v0.65 (P4) ：grounding score 用的蓝海词列表（DNA blue_ocean rankings 前 20）。
    bo_keywords_for_grounding = [
        b.get("keyword") or ""
        for b in ((dna.get("sections", {}).get("keyword_blueocean", {}) or {}).get("rankings") or [])[:20]
        if (b.get("keyword") or "")
    ]
    for idx, draft, refs_used, err in draft_results:
        if idx is None or idx >= len(schedule):
            continue
        slot = schedule[idx]
        rag = rag_by_slot.get(idx) or {}
        # v0.65 (P0) ：把 RAG 数据 + drafter 声明的 references_used 写进 slot ，
        # UI 渲染时不再需要重查。
        slot.rag_refs = rag.get("refs") or []
        slot.rag_comments = rag.get("comments") or []
        slot.rag_hooks = rag.get("hooks") or []
        slot.references_used = refs_used or []
        # v0.65 (P3) ：KPI 基线（同 hook_type 中位 / P90）
        slot.kpi_baseline = _compute_kpi_baseline(slot, dna)
        if draft:
            slot.body_draft = draft
            # v0.65 (P4) ：grounding score
            slot.grounding_score, slot.grounding_breakdown = _compute_grounding(
                draft, slot.rag_refs, bo_keywords_for_grounding,
            )
        if err:
            drafter_errors.append(f"slot #{idx + 1} ({slot.title[:40]}): {err}")

    # v0.65 (P0) ：即使没跑 body drafter（generate_body_drafts=False，默认 ）
    # 也要把每个 slot 的 RAG payload + KPI 基线持久化 ，UI 才能稳定展示。
    drafted_indices = {idx for idx, _b, _r, _e in draft_results if idx is not None}
    for i, slot in enumerate(schedule):
        if i in drafted_indices:
            continue
        rag = rag_by_slot.get(i) or {}
        slot.rag_refs = rag.get("refs") or []
        slot.rag_comments = rag.get("comments") or []
        slot.rag_hooks = rag.get("hooks") or []
        slot.kpi_baseline = _compute_kpi_baseline(slot, dna)

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
    # v0.66 (bugfix) ：复用 propose 创建的行 pack_id，而不是 StrategyPack.new()
    # 新生成的随机 id。否则 pack_json.pack_id ≠ DB 行 pack_id，前端拿到内部 id
    # 后 regenerate_slot / IterateCard / 各种按 pack_id 的调用全部 404。
    pack.pack_id = pack_id
    pack.series_thesis = str(sched_parsed.get("series_thesis", ""))
    pack.weekly_themes = weekly_themes
    pack.schedule = schedule
    pack.materials_checklist = _coerce_list(res_parsed.get("materials_checklist"))
    pack.risks_and_mitigations = _coerce_list(res_parsed.get("risks_and_mitigations"))
    # v0.66 (item5) ：解析两套可对比指标方案。normalise 成 [{label, metrics[], rationale}]。
    pack.metrics_plans = _normalise_metrics_plans(res_parsed.get("metrics_plans"))
    # success_metrics 保留兼容 ：优先取「稳健」方案的 metrics，否则回退旧字段。
    if pack.metrics_plans:
        pack.success_metrics = list(pack.metrics_plans[0].get("metrics") or [])
    else:
        pack.success_metrics = _coerce_list(res_parsed.get("success_metrics"))
    # v0.66 (item1) ：材料清单旁的 1-5 篇图文对标帖（聚合 slot RAG refs，取 top-5）。
    pack.benchmark_examples = _top_benchmark_examples(rag_by_slot, limit=5)
    # v0.59: persist ALL chosen directions for multi-direction packs.
    # Legacy clients can still read pack.chosen_direction (=first one).
    pack.chosen_directions = chosen_directions

    elapsed_total = int(time.time() - t0)
    now = int(time.time())
    pack_json_str = json.dumps(to_jsonable(pack), ensure_ascii=False)

    with db.connect() as con:
        con.execute(
            "UPDATE studio_composer_packs SET status=?, chosen_direction_idx=?,"
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


async def regenerate_slot(
    pack_id: str,
    slot_idx: int,
    scheduler_spec: str = "openai:gpt-4o",
    instruction: str = "",
) -> dict[str, Any]:
    """v0.63 ：用户在 UI 上点「✍️ 写这个」时调这个 endpoint 替换占位 slot。

    用 scheduler LLM 再单独生成 1 个 slot 来替换 schedule[slot_idx]，
    避免和已有 slot 的标题/角度重复（传现有 slot 标题作为 negative
    constraint）。如果主家 LLM 出 0 个 slot，自动跨家 fallback。

    持久化 ：更新 studio_strategies.pack_json 里 schedule[slot_idx]。
    返回 {slot_idx, title, outline, angle, ...} 让前端立刻渲染。
    """
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        # v0.62 renamed studio_strategies → studio_composer_packs.
        row = con.execute(
            "SELECT pack_json, platform FROM studio_composer_packs WHERE pack_id = ?",
            (pack_id,),
        ).fetchone()
    if not row or not row["pack_json"]:
        raise LookupError(f"strategy pack not found or not expanded yet: {pack_id}")
    pack_data = json.loads(row["pack_json"])
    schedule = pack_data.get("schedule") or []
    if slot_idx < 0 or slot_idx >= len(schedule):
        raise IndexError(f"slot_idx out of range: {slot_idx}")

    chosen = pack_data.get("chosen_direction") or {}
    platform = row["platform"] or pack_data.get("platform") or "xiaohongshu"
    inp_data = pack_data.get("input") or {}
    cycle_weeks = int(inp_data.get("cycle_weeks") or 4)
    posts_per_week = int(inp_data.get("posts_per_week") or 3)

    target_slot = schedule[slot_idx]
    avoid_titles = [str(s.get("title") or "") for i, s in enumerate(schedule)
                    if i != slot_idx and s.get("title")]
    avoid_block = (
        "\n【已有标题（请勿重复 / 角度也别撞）】\n"
        + "\n".join(f"  - {t}" for t in avoid_titles[:30])
        if avoid_titles else ""
    )

    # v0.66 (item3) ：用户的调整指令 ─ 让单条重生成能「按我的话改」，而不是
    # 每次都随机出一条。例 ：「太拖沓，压缩到 3 段」「换更冲突的 hook」「这周改成测评角度」。
    instruction = (instruction or "").strip()
    instruction_block = (
        f"\n【⭐ 用户的调整指令（最高优先，必须照做）】\n{instruction}\n"
        if instruction else ""
    )
    cur_block = (
        f"\n【当前这条（仅供参考你要改的对象）】\n"
        f"  标题 ：{target_slot.get('title','')}\n"
        f"  角度 ：{target_slot.get('angle','')}\n"
        f"  大纲 ：{' / '.join(str(x) for x in (target_slot.get('outline') or []))}\n"
        if instruction else ""
    )
    user_prompt = (
        f"【账号方向】{chosen.get('name','')} — {chosen.get('positioning_statement','')}\n"
        f"【目标受众】{chosen.get('target_audience','')}\n"
        f"【平台】{platform}\n"
        f"【周期】{cycle_weeks} 周 × {posts_per_week} 篇/周\n"
        f"{cur_block}{instruction_block}\n"
        f"【缺口】请重新出 **1 篇** 排期 slot 替换原来的占位 — "
        f"目标 week={target_slot.get('week') or 1}, "
        f"day_of_week={target_slot.get('day_of_week') or 0}。"
        f"{'务必体现上面的用户调整指令。' if instruction else ''}"
        f"{avoid_block}\n\n"
        f"严格按 system schema 输出 ：schedule 数组长度 = 1，"
        f"weekly_themes 可留空 []。"
    )

    async def _try_with(spec: str):
        gen = registry.build(spec)[0]
        return await _call_json(
            gen, prompts.SCHEDULER_SYSTEM, user_prompt,
            max_tokens=4000, tool_name="submit_schedule",
            schema=_SCHEDULE_SCHEMA,
        )

    def _looks_like_placeholder(title: str) -> bool:
        """LLM sometimes literally echoes "待补 #N" or "AI 漏排" back if our
        prompt context contained the placeholder. Reject so we retry."""
        t = title or ""
        return ("AI 漏排" in t or "请用 ✍️" in t or t.startswith("待补 #")
                or t.startswith("[自补]") or t == "")

    # L1 primary, L2 cross-family.
    primary = scheduler_spec.lower()
    fb = ("deepseek" if ("openai" in primary or "gpt" in primary)
          else "openai:gpt-4o")
    new_slot_raw: dict[str, Any] | None = None
    last_err: str | None = None
    for spec, label in [(scheduler_spec, "primary"), (fb, "fallback")]:
        try:
            r = await _try_with(spec)
            sch = r.get("schedule") or []
            if sch and isinstance(sch[0], dict):
                title = str(sch[0].get("title") or "").strip()
                if title and not _looks_like_placeholder(title):
                    new_slot_raw = sch[0]
                    last_err = None
                    break
                last_err = (
                    f"{label}: rejected echoed placeholder title {title!r}"
                    if title else f"{label}: empty title"
                )
            else:
                last_err = f"{label}: empty schedule"
        except Exception as e:
            last_err = f"{label}: {e!r}"
    if not new_slot_raw:
        raise RuntimeError(
            f"regenerate_slot 两家 LLM 都没出真实 slot ({last_err})。"
            f"可能 ：API 限速 / 余额 / 服务暂时不可用。稍等再试或换 LLM 预设。"
        )

    # v0.66 (item3) ：regenerate_slot 永远是用户主动重生成 1 条，不是「补缺口」，
    # 所以 scheduler 按 prompt 给标题尾部加的「[自补]」标记在这里是误导，去掉。
    _new_title = str(new_slot_raw.get("title") or "").replace(" [自补]", "").replace("[自补]", "").strip()

    # Preserve original (week, day_of_week, content_format) so the schedule
    # grid doesn't shuffle — replace only the topical content.
    new_slot = {
        **target_slot,
        "title": _new_title,
        "title_variants": [str(x) for x in (new_slot_raw.get("title_variants") or [])],
        "angle": str(new_slot_raw.get("angle") or target_slot.get("angle") or ""),
        "hook_type": str(new_slot_raw.get("hook_type") or ""),
        "outline": [str(x) for x in (new_slot_raw.get("outline") or [])],
        "materials_needed": [str(x) for x in (new_slot_raw.get("materials_needed") or [])],
        "intent": str(new_slot_raw.get("intent") or target_slot.get("intent") or ""),
        "publish_rationale": str(new_slot_raw.get("publish_rationale") or ""),
        "decision_rationale": str(new_slot_raw.get("decision_rationale") or ""),
        "flexible_window": str(new_slot_raw.get("flexible_window") or ""),
        "alternative_versions": [
            dict(a) for a in (new_slot_raw.get("alternative_versions") or [])
            if isinstance(a, dict)
        ],
        # v0.65 ：propagate anchors + 重拉 RAG + 算 baseline 给新 slot
        "decision_anchors": [
            dict(a) for a in (new_slot_raw.get("decision_anchors") or [])
            if isinstance(a, dict)
        ],
        "publish_anchors": [
            dict(a) for a in (new_slot_raw.get("publish_anchors") or [])
            if isinstance(a, dict)
        ],
        # Clear any prior body_draft — user will regenerate via Composer.
        "body_draft": "",
        # Body 没了 ─ grounding 重置；rag_refs 按新 title 拉一次 + KPI 基线
        "references_used": [],
        "grounding_score": 0.0,
        "grounding_breakdown": {},
    }
    try:
        _new_rag = _retrieve_for_slot(
            " ".join(x for x in [new_slot["title"], new_slot["angle"], new_slot["hook_type"]] if x),
            k_refs=4, n_comments=5,
        )
        new_slot["rag_refs"] = _new_rag.get("refs") or []
        new_slot["rag_comments"] = _new_rag.get("comments") or []
        new_slot["rag_hooks"] = _new_rag.get("hooks") or []
    except Exception:
        new_slot.setdefault("rag_refs", [])
        new_slot.setdefault("rag_comments", [])
        new_slot.setdefault("rag_hooks", [])
    try:
        from .models import TopicSlot as _TS
        new_slot["kpi_baseline"] = _compute_kpi_baseline(
            _TS(week=int(new_slot.get("week") or 1),
                hook_type=new_slot.get("hook_type") or "",
                angle=new_slot.get("angle") or ""),
            _latest_dna_payload(),
        )
    except Exception:
        new_slot["kpi_baseline"] = {}
    schedule[slot_idx] = new_slot
    pack_data["schedule"] = schedule
    with db.connect() as con:
        con.execute(
            "UPDATE studio_composer_packs SET pack_json=?, updated_at=? WHERE pack_id=?",
            (json.dumps(pack_data, ensure_ascii=False), int(time.time()), pack_id),
        )
    return {"slot_idx": slot_idx, "slot": new_slot}


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
