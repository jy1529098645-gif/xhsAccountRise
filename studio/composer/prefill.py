# v0.62.13 ：Brief 智能预填。
#
# 用户在起号策略板块点「✍️ 写这个」跳到出稿板块时，前端默认只能机械
# 复制 slot 字段（topic = slot.title, angles = [slot.angle]）。但出稿
# 表单需要更多上下文 ：目标字数、CTA 强度、niche、额外约束（含目标受众/
# 大纲/材料/意图等）。让 AI 综合 pack 方向 + slot 元数据 + DNA 摘要，
# 一次生成一份**完整的可发布前 brief**，前端拿到后填表单 — 用户随便改。

from __future__ import annotations

import json
from typing import Any

from .. import db, library
from ..generators import registry
from ..llm_call import call_for_json


_PREFILL_SYSTEM = """\
你是「出稿 brief 准备员」。用户已经在「起号策略」板块选定了一篇要写的 slot，
现在需要把这篇的 brief 一次性填好，让 multi-agent 团队接着写正文。

输入：账号方向（定位 / 受众 / 主线）、本篇 slot 的元数据（标题 / 角度 /
hook 类型 / 内容形式 / 大纲 / 材料 / 意图 / 时段）、DNA 摘要（库里同赛道
高赞特征）、可能还有用户选的备选方案（次选 A / B）。

输出严格 JSON ：
{
  "topic":             "<这篇要写什么 — 比 slot.title 更口语化 / 适合 brief 一句话>",
  "angles":            ["<从 ANGLES 枚举里选 1-3 个最适合这篇的，第 1 个是主角度>"],
  "target_length":     <int 字数 ：图文 400-600 / 短视频脚本 200-350 / 长视频 700-1200>,
  "cta_strength":      "<none|soft|strong - 根据 intent 选 ：拉新 soft，转化 strong，沉淀 none>",
  "niche":             "<2-4 句话 ：账号定位 + 受众 + 这篇在策略里的位置>",
  "extra_constraints": "<6-12 行的细节指导 ：hook 风格 / 大纲骨架 / 必须包含的材料 / 红线避坑 / 发布时段 / 目标受众原话 / 备选方案的差异点 等>",
  "rationale":         "<≤40 字 一句话 ：这份 brief 为什么这么填，最关键的策略点>"
}

ANGLES 枚举 ：[教程, 痛点, 故事, 工具评测, 对比, 感悟, 数字, 种草, 建议,
吐槽, 实测, 翻车, 经验, 反差, 数据]

规则 ：
- topic 必须是用户能直接读懂的一句话，**不要重复 slot.title 原文** —
  用更口语化 / 更具体的表达。如果原 title 已经足够口语化，可以微调即可。
- angles 主角度必须匹配 slot.angle（如果 slot.angle 在枚举里）。可以多
  选 1-2 个补角度（比如「教程」+「对比」混搭）。
- target_length 看 content_format 决定：图文 400-600；短视频脚本 200-350；
  长视频章节大纲 700-1200；纯文本走图文档位。
- cta_strength 看 slot.intent ：拉新 / 互动 ：soft；转化 / 私域 ：strong；
  沉淀 / 专业感 ：none。
- niche 用主语「这是一个 X 账号，受众 Y，本期 Z」格式，简洁。
- extra_constraints 是给写手看的细节卡片。**每行一个独立指令**，多行用
  \\n 分隔。要明确具体 ：「不要 AI 文风」「开头 1 句反问」「中段加一个真
  实数据」等。
- rationale 一句话，让用户知道 AI 为什么这么填。
"""


_PREFILL_SCHEMA = {
    "type": "object",
    "required": ["topic", "angles", "target_length", "cta_strength", "niche", "extra_constraints"],
    "properties": {
        "topic":             {"type": "string"},
        "angles":            {"type": "array", "items": {"type": "string"}},
        "target_length":     {"type": "integer"},
        "cta_strength":      {"type": "string", "enum": ["none", "soft", "strong"]},
        "niche":             {"type": "string"},
        "extra_constraints": {"type": "string"},
        "rationale":         {"type": "string"},
    },
}


def _build_user_prompt(pack: dict[str, Any], slot: dict[str, Any], alt: dict[str, Any] | None, dna_excerpt: str) -> str:
    direction = pack.get("chosen_direction") or {}
    parts = [
        "【账号方向】",
        f"- 名称 ：{direction.get('name', '')}",
        f"- 定位 ：{direction.get('positioning_statement', '')}",
        f"- 受众 ：{direction.get('target_audience', '')}",
        f"- 差异化 ：{direction.get('differentiator', '')}",
        f"- 风险 ：{direction.get('risk', '')}",
        "",
        f"【系列主线】{pack.get('series_thesis', '')}",
        f"【平台】{pack.get('platform', '')}",
        "",
        "【这篇 slot】",
        f"- 标题 ：{slot.get('title', '')}",
        f"- 角度 ：{slot.get('angle', '')}",
        f"- hook 类型 ：{slot.get('hook_type', '')}",
        f"- 内容形式 ：{slot.get('content_format', '')}",
        f"- 意图 ：{slot.get('intent', '')}",
        f"- 时段 ：{slot.get('publish_slot', '')}",
        f"- 时段理由 ：{slot.get('publish_rationale', '')}",
        f"- 排期判断 ：{slot.get('decision_rationale', '')}",
    ]
    outline = slot.get("outline") or []
    if outline:
        parts.append(f"- 大纲 ：{' / '.join(outline)}")
    materials = slot.get("materials_needed") or []
    if materials:
        parts.append(f"- 需要材料 ：{', '.join(materials)}")

    if alt:
        parts.extend([
            "",
            "【用户选的备选方案（非主推荐）】",
            f"- 标签 ：{alt.get('label', '')}",
            f"- 角度 ：{alt.get('angle', '')}",
            f"- 内容形式 ：{alt.get('content_format', '')}",
            f"- 时段 ：{alt.get('publish_slot', '')}",
            f"- 备选标题 ：{alt.get('title', '')}",
        ])
        mini = alt.get("mini_outline") or []
        if mini:
            parts.append(f"- 备选大纲 ：{' / '.join(mini)}")
        why = alt.get("why_alt")
        if why:
            parts.append(f"- 为啥选这个备选 ：{why}")

    if dna_excerpt:
        parts.extend(["", "【DNA 摘要（同赛道高赞特征）】", dna_excerpt[:1500]])

    parts.append("\n请输出一份完整的 brief JSON。")
    return "\n".join(parts)


def _load_dna_excerpt() -> str:
    """Pull dominant hooks + top performers from latest DNA artifact, if any."""
    try:
        with db.connect(read_only=True) as con:
            row = con.execute(
                "SELECT payload_json FROM studio_dna_artifacts"
                " ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return ""
        data = json.loads(row["payload_json"])
        sections = data.get("sections") or {}
        lines: list[str] = []
        titles = sections.get("titles") or {}
        if titles:
            hooks = titles.get("dominant_hooks") or []
            if hooks:
                lines.append("- 主流 hook 类型 ：" + " / ".join(
                    f"{h.get('category', '?')}({h.get('count', 0)}条)" for h in hooks[:5]
                ))
        body = sections.get("body_and_shape") or {}
        if body and body.get("median_chars"):
            lines.append(f"- 中位长度 ：{body.get('median_chars')} 字")
        top = sections.get("top_performers") or []
        if top:
            lines.append("- 高赞例子 ：" + " / ".join(
                f'"{(t.get("title") or "")[:30]}"({t.get("liked_count", 0)}👍)' for t in top[:3]
            ))
        humor = sections.get("humor_signals") or {}
        if humor and humor.get("lift") and humor["lift"] > 1.0:
            lines.append(f"- 调侃/段子 lift ：{humor['lift']:.2f}（>1 表示这类内容平均更高赞）")
        return "\n".join(lines)
    except Exception:
        return ""


async def prefill_brief(pack_id: str, slot_idx: int, alt_idx: int = -1,
                        spec: str = "openai:gpt-4o-mini") -> dict[str, Any]:
    """生成一份给 Composer 写每篇用的完整 brief。

    Args:
        pack_id: studio_composer_packs.pack_id
        slot_idx: 用户在 pack.schedule 里选了第几个 slot
        alt_idx: 如果用了备选方案，第几个 ；-1 = 主推荐
        spec: LLM。默认 gpt-4o-mini ：便宜快，brief 生成不需要顶级模型。

    Returns: brief 字典 ；上层填进表单。
    """
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT pack_json FROM studio_composer_packs WHERE pack_id = ?",
            (pack_id,),
        ).fetchone()
    if not row:
        pack_lib_id = library.find_pack_lib_id(pack_id)
        if pack_lib_id:
            try:
                library.set_active(pack_lib_id)
            except Exception:
                pass
            db.apply_migrations(verbose=False)
            with db.connect(read_only=True) as con:
                row = con.execute(
                    "SELECT pack_json FROM studio_composer_packs WHERE pack_id = ?",
                    (pack_id,),
                ).fetchone()
    if not row:
        raise LookupError(f"pack not found: {pack_id}")
    if not row["pack_json"]:
        raise LookupError(f"pack not yet expanded: {pack_id}")
    pack = json.loads(row["pack_json"])
    schedule = pack.get("schedule") or []
    if slot_idx < 0 or slot_idx >= len(schedule):
        raise IndexError(f"slot_idx {slot_idx} out of range (len={len(schedule)})")
    slot = schedule[slot_idx]
    alts = slot.get("alternative_versions") or []
    alt = alts[alt_idx] if (alt_idx >= 0 and alt_idx < len(alts)) else None

    dna_excerpt = _load_dna_excerpt()
    user_prompt = _build_user_prompt(pack, slot, alt, dna_excerpt)
    gen = registry.build(spec)[0]

    try:
        parsed = await call_for_json(
            gen, _PREFILL_SYSTEM, user_prompt,
            max_tokens=1200,
            tool_name="submit_brief",
            schema=_PREFILL_SCHEMA,
        )
    except Exception as e:
        # 失败 ：返回机械版兜底（前端拿到也能填，不至于完全空白）
        return {
            "topic": slot.get("title") or "",
            "angles": [slot.get("angle")] if slot.get("angle") else [],
            "target_length": 500,
            "cta_strength": "soft",
            "niche": (pack.get("chosen_direction") or {}).get("positioning_statement", ""),
            "extra_constraints": (
                f"内容形式 ：{slot.get('content_format', '')}\n"
                f"hook 类型 ：{slot.get('hook_type', '')}\n"
                f"意图 ：{slot.get('intent', '')}"
            ),
            "rationale": f"AI 生成失败兜底机械填 ：{type(e).__name__}",
            "_fallback": True,
        }

    # Sanitize / clamp
    parsed["target_length"] = max(100, min(2000, int(parsed.get("target_length") or 500)))
    if parsed.get("cta_strength") not in ("none", "soft", "strong"):
        parsed["cta_strength"] = "soft"
    return parsed
