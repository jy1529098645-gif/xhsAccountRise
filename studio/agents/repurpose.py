"""Cross-platform repurpose — 把一条 final draft 改成另一个平台的格式。

Pipeline:
    1) 找 source draft 的 final candidate（chosen，或 critic 最高分）作为基底
    2) 用一个轻量级 LLM call（claude:sonnet 默认）按 target platform 的
       voice + length + hook 风格改写 ：
         · 小红书 ←→ 抖音 ：图文 ←→ 分镜脚本
         · 小红书 ←→ B站 ：短图文 ←→ 章节式长稿
         · 中文 ←→ Reddit 英文 ：营销腔 ←→ 长论证体
    3) （可选）如果用户提供了 target 平台的 library，先 retriever 拉该
       平台同主题 refs/comments，注入 prompt 作 voice 锚 — 比凭空生成准
    4) 作为 variant child draft 存进 studio_drafts，parent_draft_id 指向
       源稿，variant_label = "repurpose·<target_platform>"

Usage:
    POST /api/drafts/{draft_id}/repurpose
        body: { target_platform: "douyin" | "bilibili" | ..., target_lib_id?: str }
    Returns: child draft_id + the rewritten candidate payload
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .. import db, project
from ..brief import Brief, _PLATFORM_VOICE
from ..generators import registry
from ..llm_call import call_for_json


REPURPOSE_SYSTEM = """\
你是「跨平台改稿专家」。给你一条已发布的爆款稿件，把它改写成另一个平台的版本。

🎯 你的任务 ：保留**核心信息 + 卖点 + 用户痛点 + 数据点**，按目标平台的语态 +
长度 + 格式重写。**不是翻译，是 voice 迁移**。

📐 平台间的关键差异 ：

【小红书 → 抖音】
- 图文 → 短视频分镜脚本（12-15 镜，每镜 3-5 秒）
- 长正文 → 口播文案（1 句 1 镜）
- emoji 密度同样高，但用法不同 ：小红书在段落间，抖音在字幕上
- 钩子前置到第 1 镜（前 1.5 秒砸出来）

【小红书 → B 站】
- 短图文 → 章节式长稿（500 字 → 1500-2500 字）
- 每节加时间戳 [00:00 - 02:00]
- 加 ：「up 主开场 / 核心论证 / 数据支撑 / 总结金句」结构
- 二次元 friendly，可加梗但克制

【小红书 → Reddit】
- 中文 → 英文（除非用户指定保中）
- 营销腔 → 长论证体（不要 emoji 堆砌，给数据 / 源链接）
- 不能 「家人们 / 姐妹们」 那一套，要 「I tried X, here's what I found」

【小红书 → X / Twitter】
- 长图文 → thread（每条 ≤ 280 字符）
- 第 1 条必须是 hook + 引子
- 拆 5-8 条，每条独立可读

【抖音 → 小红书 / B站】（反向）
- 分镜脚本还原成图文（每镜核心句拼成段落）
- 补充背景信息（视频里靠画面交代的细节，图文要文字描述）

🚫 不要 ：
- 把所有平台都写成一种风格（语态必须差异化）
- 编造 source draft 里没有的事实 / 工具名 / 数字
- 在 Reddit / X 用 「家人们」「绝绝子」类 platform-mismatch 词

✅ 必须 ：
- 保留 source 的核心 hook 类型（数字型 / 痛点型 / 段子型 等）
- 保留所有 ：产品名 / 工具名 / 数字 / 引用源
- 重写 ：句式 / 长度 / emoji 节奏 / CTA 表达

输出 JSON ：
{
  "title": "<target 平台风格的标题>",
  "body": "<完整的可发布正文 / 分镜脚本 / 章节大纲>",
  "tags": ["..."],
  "cover_prompt": "<英文封面/封面图描述>",
  "hook_type": "<跟 source 一致或近似>",
  "predicted_likes": <整数预估>,
  "self_score": <0-10>,
  "self_critique": "<一句话坦诚说最大风险>",
  "rationale": "<≤80 字 说你做了哪些 voice/format 改动>"
}
"""


_SCHEMA = {
    "type": "object",
    "required": ["title", "body", "tags", "cover_prompt", "hook_type",
                 "predicted_likes", "self_score", "self_critique"],
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "cover_prompt": {"type": "string"},
        "hook_type": {"type": "string"},
        "predicted_likes": {"type": "integer"},
        "self_score": {"type": "number"},
        "self_critique": {"type": "string"},
        "rationale": {"type": "string"},
    },
}


_PLATFORM_LABELS = {
    "xiaohongshu": "小红书", "douyin": "抖音", "kuaishou": "快手",
    "bilibili": "B站", "youtube": "YouTube",
    "reddit": "Reddit", "x": "X / Twitter", "other": "通用",
}


_CAND_COLS = ("candidate_id, llm, title, body, tags_json, cover_prompt,"
              " hook_type, predicted_likes, self_score, self_critique")


def _row_to_payload(row: Any) -> dict[str, Any]:
    return {
        "title": row["title"] or "",
        "body": row["body"] or "",
        "tags": json.loads(row["tags_json"] or "[]"),
        "cover_prompt": row["cover_prompt"] or "",
        "hook_type": row["hook_type"] or "",
        "predicted_likes": row["predicted_likes"] or 0,
        "self_score": row["self_score"] or 0.0,
        "self_critique": row["self_critique"] or "",
    }


def _load_source_draft(draft_id: str) -> dict[str, Any]:
    """Load source draft + its chosen (or top-scored) candidate."""
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        d = con.execute(
            "SELECT draft_id, brief_json, project_id, library_id, mode,"
            " final_candidate_id"
            " FROM studio_drafts WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        if not d:
            raise LookupError(f"draft not found: {draft_id}")
        # Prefer chosen final; else fall back to top self_score.
        cand_row = None
        if d["final_candidate_id"]:
            cand_row = con.execute(
                f"SELECT {_CAND_COLS} FROM studio_draft_candidates"
                f" WHERE candidate_id = ?",
                (d["final_candidate_id"],),
            ).fetchone()
        if not cand_row:
            cand_row = con.execute(
                f"SELECT {_CAND_COLS} FROM studio_draft_candidates"
                f" WHERE draft_id = ?"
                f" ORDER BY self_score DESC LIMIT 1",
                (draft_id,),
            ).fetchone()
    if not cand_row:
        raise LookupError(f"no usable candidate in draft: {draft_id}")
    brief_dict = json.loads(d["brief_json"]) if d["brief_json"] else {}
    return {
        "draft_id": d["draft_id"],
        "source_platform": brief_dict.get("platform") or "xiaohongshu",
        "topic": brief_dict.get("topic") or "",
        "angle": brief_dict.get("angle") or "",
        "project_id": d["project_id"],
        "library_id": d["library_id"],
        "candidate_id": cand_row["candidate_id"],
        "candidate_payload": _row_to_payload(cand_row),
        "candidate_llm": cand_row["llm"],
    }


async def repurpose_draft(
    draft_id: str,
    target_platform: str,
    *,
    target_lib_id: str | None = None,
    model_spec: str = "claude:sonnet",
) -> dict[str, Any]:
    """Cross-platform repurpose. Returns the new variant draft_id + payload."""
    src = _load_source_draft(draft_id)
    if target_platform == src["source_platform"]:
        raise ValueError(
            f"target_platform ({target_platform}) 跟 source 一样，没必要 repurpose"
        )

    target_voice = _PLATFORM_VOICE.get(target_platform, "通用平台风格")
    target_label = _PLATFORM_LABELS.get(target_platform, target_platform)
    source_label = _PLATFORM_LABELS.get(src["source_platform"], src["source_platform"])

    p = src["candidate_payload"]
    # Optional reference block from target lib's DNA, if user provided one.
    ref_block = ""
    if target_lib_id:
        try:
            ref_block = _build_target_ref_block(target_lib_id, src["topic"])
        except Exception:
            ref_block = ""  # best-effort; if it fails, AI generates from prompt only

    # v0.65 (P2) ：repurpose 也跑 RAG ─ 用 target_lib_id 上下文按 topic + angle
    # 拉同平台 refs / comments / hooks 喂进 prompt + 持久化为 rag_json。
    # 之前的 _build_target_ref_block 只用 LIKE，覆盖不到深层语义；这里走 FTS 路径。
    rag_payload: dict[str, Any] = {"refs": [], "comments": [], "hooks": []}
    rag_refs_block = ""
    try:
        from ..composer.pipeline import (
            _retrieve_for_slot as _rp_retrieve,
            _format_refs_for_prompt as _rp_format_refs,
        )
        from .. import library as _libmod
        original_lib = _libmod.active_lib_id() if target_lib_id else None
        if target_lib_id and target_lib_id != original_lib:
            _libmod.set_active(target_lib_id)
        try:
            query = " ".join(x for x in [src.get("topic", ""), src.get("angle", "")] if x)
            rag_payload = _rp_retrieve(query, k_refs=6, n_comments=6)
            rag_refs_block = _rp_format_refs(
                rag_payload.get("refs") or [],
                rag_payload.get("comments") or [],
                rag_payload.get("hooks") or [],
            )
        finally:
            if target_lib_id and original_lib and target_lib_id != original_lib:
                try: _libmod.set_active(original_lib)
                except Exception: pass
    except Exception:
        pass

    user_msg = (
        f"【源稿信息】\n"
        f"  · 源平台 ：{source_label}（{src['source_platform']}）\n"
        f"  · 主题 ：{src['topic']}\n"
        f"  · 角度 ：{src['angle']}\n"
        f"  · 源 LLM ：{src['candidate_llm']}\n\n"
        f"【源稿正文】\n"
        f"标题 ：{p.get('title', '')}\n"
        f"hook_type ：{p.get('hook_type', '')}\n"
        f"tags ：{p.get('tags', [])}\n"
        f"正文 ：\n{p.get('body', '')}\n\n"
        f"【目标平台】{target_label}（{target_platform}）\n"
        f"【目标平台风格指引】{target_voice}\n"
        f"{ref_block}\n"
        + (rag_refs_block + "\n" if rag_refs_block else "")
        + f"请把源稿改写成 {target_label} 平台版本。**不是翻译，是 voice + format 迁移**。"
        f" 保留源的核心信息 + hook 类型，重写句式 / 长度 / emoji 节奏。\n"
        f" 严格输出 JSON 见 system schema。"
    )

    gen = registry.build(model_spec)[0]
    t0 = time.time()
    parsed = await call_for_json(
        gen, REPURPOSE_SYSTEM, user_msg,
        max_tokens=6000,
        tool_name="submit_repurposed_draft",
        schema=_SCHEMA,
    )
    elapsed = int(time.time() - t0)

    # Persist as variant child draft.
    child_id = uuid.uuid4().hex[:16]
    new_cand_id = uuid.uuid4().hex[:16]
    now = int(time.time())

    # Build a new brief mirroring source but with target platform.
    new_brief = {
        "topic": src["topic"],
        "angle": src["angle"],
        "angles": [src["angle"]] if src["angle"] else [],
        "target_length": len(parsed.get("body", "")) or 600,
        "cta_strength": "soft",
        "niche": "",
        "extra_constraints": f"跨平台改稿 ：从 {source_label} 迁到 {target_label}",
        "platform": target_platform,
    }

    # v0.65 (P4) ：grounding score 同 quick_generate / composer 一致。
    try:
        from ..composer.pipeline import _latest_dna_payload, _compute_grounding
        _dna = _latest_dna_payload()
        _bo_keywords = [
            b.get("keyword") or ""
            for b in ((_dna.get("sections", {}).get("keyword_blueocean", {}) or {})
                      .get("rankings") or [])[:20]
            if (b.get("keyword") or "")
        ]
        _g_score, _g_breakdown = _compute_grounding(
            parsed.get("body", "") or "", rag_payload.get("refs") or [], _bo_keywords,
        )
    except Exception:
        _g_score, _g_breakdown = 0.0, {}

    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_drafts"
            " (draft_id, brief_json, generated_at, mode,"
            "  library_id, project_id,"
            "  parent_draft_id, variant_label,"
            "  final_candidate_id, rag_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                child_id, json.dumps(new_brief, ensure_ascii=False),
                now, "repurpose",
                src["library_id"], src["project_id"],
                src["draft_id"], f"repurpose·{target_platform}",
                new_cand_id,
                json.dumps(rag_payload, ensure_ascii=False),
            ),
        )
        # 用 studio_draft_candidates 真实列名（v0.61.27 修 ：之前用 payload_json）
        meta = {
            "elapsed_ms": elapsed * 1000,
            "source_draft_id": src["draft_id"],
            "source_platform": src["source_platform"],
            "target_platform": target_platform,
            "rationale": parsed.get("rationale", ""),
            "grounding_score": _g_score,
            "grounding_breakdown": _g_breakdown,
        }
        con.execute(
            "INSERT INTO studio_draft_candidates"
            " (candidate_id, draft_id, llm, title, body, tags_json,"
            "  cover_prompt, hook_type, predicted_likes, self_score,"
            "  self_critique, meta_json, human_score, chosen, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_cand_id, child_id, f"{model_spec}+repurpose",
                parsed.get("title", ""),
                parsed.get("body", ""),
                json.dumps(parsed.get("tags") or [], ensure_ascii=False),
                parsed.get("cover_prompt", ""),
                parsed.get("hook_type", ""),
                int(parsed.get("predicted_likes") or 0),
                float(parsed.get("self_score") or 0.0),
                parsed.get("self_critique", ""),
                json.dumps(meta, ensure_ascii=False),
                None,
                1,  # chosen = true，repurpose 出来就 = final
                now,
            ),
        )

    return {
        "child_draft_id": child_id,
        "candidate_id": new_cand_id,
        "target_platform": target_platform,
        "elapsed_s": elapsed,
        "payload": parsed,
        "rationale": parsed.get("rationale", ""),
    }


def _build_target_ref_block(target_lib_id: str, topic: str) -> str:
    """v0.61.26 ：用户提供了 target 平台的 lib → 拉同主题 refs 注入 prompt。
    简化版 ：直接挑该 lib 高赞 + topic 关键词匹配的 top 5 篇。"""
    from .. import library as _lib
    # Switch active lib temporarily — db.connect 会用 current_db_path
    original = _lib.active_lib_id()
    try:
        _lib.set_active(target_lib_id)
        with db.connect(read_only=True) as con:
            rows = list(con.execute(
                "SELECT title, body, liked_count, collected_count"
                " FROM notes"
                " WHERE liked_count > 100"
                " AND (title LIKE ? OR body LIKE ?)"
                " ORDER BY liked_count DESC LIMIT 5",
                (f"%{topic[:6]}%", f"%{topic[:6]}%"),
            ))
    finally:
        try:
            _lib.set_active(original)
        except Exception:
            pass
    if not rows:
        return ""
    parts = ["\n【目标平台同主题真实爆款参考】（用户已提供 target lib，按这些真实 voice 改写）：\n"]
    for r in rows:
        title = r["title"] or ""
        body = (r["body"] or "")[:300]
        likes = r["liked_count"] or 0
        parts.append(f"  · [{likes} likes] {title}\n    正文片段 ：{body}\n")
    return "\n".join(parts)
