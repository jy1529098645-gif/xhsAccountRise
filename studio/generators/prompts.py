"""Prompt templates. Kept as plain strings so they can be diff'd, versioned in
studio_prompt_versions, and reasoned about without code changes.

The orchestrator pulls the active version of each template at generation time.
For v0.1 we hardcode v1.0.0. W4 introduces forking.
"""
from __future__ import annotations

import json
from typing import Any

from ..brief import Brief

# ---- versions ---- (bump on substantive change; W4 will persist these)
TITLE_BODY_GEN_VERSION = "title_body_gen-1.0.0"


SYSTEM_TITLE_BODY = """\
你是小红书爆款写手，专攻下沉学生群体（赶 ddl 的留子、写毕业论文的本科/研究生、查重党）的内容。

你的稿件必须满足：
1. 标题：15-22 字最佳，强 hook（数字 / 痛点 / 工具种草 / 故事开头 / 建议）。避免学术腔。
2. 正文：口语化、第一人称、emoji 节奏自然（每 80-150 字 1 个），分点用「1️⃣2️⃣」或「①②」。
3. 结尾自然引导互动（求评论 / 求关注 / 求私信），强度按 brief 指定。
4. tag：选 6-10 个，含 1-2 个赛道大词 + 3-5 个细分场景词 + 1-2 个工具/产品词。
5. cover_prompt：英文描述封面图（xhs 用户先看封面再看标题），明确文字版面 + 风格关键词。

绝对不要：
- 编造产品名 / 工具名 / 链接 / 数字（除非 brief 给了）
- 学术八股 / 客套话 / 「希望本文对你有帮助」式收尾
- "本文"、"本人"、"特此" 等书面语

输出格式：纯 JSON，schema 见 user 消息末尾。不要任何 markdown 围栏。"""


USER_TEMPLATE = """\
【brief】
{brief_block}

【参考爆款（同赛道高互动）】
{refs_block}

【目标用户原话（来自高赞评论）】
{comments_block}

【可选 hook 模板】
{hooks_block}

请基于上述材料生成一份候选稿件。务必输出 JSON 对象，键齐全：
{{
  "title": "<15-22 字>",
  "body": "<约 {target_length} 字，分点>",
  "tags": ["<tag1>", "..."],
  "cover_prompt": "<英文封面图描述>",
  "hook_type": "<数字型|痛点型|故事型|工具型|教程型|种草型|建议型|对比型|问句型|感悟型>",
  "predicted_likes": <整数，你对这条稿件能拿到的 likes 的预估>,
  "self_score": <0-10 浮点，对此稿质量的自评>,
  "self_critique": "<一句话坦诚指出最大风险点>"
}}"""


def _format_brief(brief: Brief) -> str:
    cta_map = {
        "none": "无明显引导",
        "soft": "结尾轻引导评论/收藏",
        "strong": "结尾强转化（求私信/求资源/求关注）",
    }
    lines = [
        f"主题：{brief.topic}",
        f"角度：{brief.angle}",
        f"目标正文字数：{brief.target_length}",
        f"CTA 强度：{cta_map.get(brief.cta_strength, brief.cta_strength)}",
    ]
    if brief.niche:
        lines.append(f"赛道：{brief.niche}")
    if brief.extra_constraints:
        lines.append(f"附加要求：{brief.extra_constraints}")
    return "\n".join(lines)


def _format_refs(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "（无参考；按主题自由发挥）"
    blocks = []
    for i, r in enumerate(refs, 1):
        title = r.get("title", "")
        body = (r.get("body") or "")[:400]
        likes = r.get("liked_count") or 0
        collects = r.get("collected_count") or 0
        cmts = r.get("comment_count") or 0
        blocks.append(
            f"#{i} [{likes} likes · {collects} 收藏 · {cmts} 评论]\n"
            f"  标题：{title}\n"
            f"  正文（节选）：{body}"
        )
    return "\n\n".join(blocks)


def _format_comments(comments: list[dict[str, Any]]) -> str:
    if not comments:
        return "（暂无相关评论数据）"
    lines = []
    for c in comments[:15]:
        likes = c.get("like_count") or 0
        text = (c.get("content") or "").strip().replace("\n", " ")[:160]
        lines.append(f"- ({likes}👍) {text}")
    return "\n".join(lines)


def _format_hooks(hooks: list[dict[str, Any]]) -> str:
    if not hooks:
        return "（自由选用 hook 类型）"
    lines = []
    for h in hooks[:5]:
        examples = h.get("examples", [])
        ex_str = " | ".join(e.get("title", "") for e in examples[:3])
        lines.append(
            f"- {h['category']}（n={h['count']}, median_likes={int(h.get('median_likes', 0))}）"
            f"  示例：{ex_str}"
        )
    return "\n".join(lines)


def build_user_message(
    brief: Brief,
    refs: list[dict[str, Any]],
    comments: list[dict[str, Any]],
    hooks: list[dict[str, Any]],
) -> str:
    return USER_TEMPLATE.format(
        brief_block=_format_brief(brief),
        refs_block=_format_refs(refs),
        comments_block=_format_comments(comments),
        hooks_block=_format_hooks(hooks),
        target_length=brief.target_length,
    )


JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "title", "body", "tags", "cover_prompt", "hook_type",
        "predicted_likes", "self_score", "self_critique",
    ],
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "cover_prompt": {"type": "string"},
        "hook_type": {"type": "string"},
        "predicted_likes": {"type": "integer"},
        "self_score": {"type": "number"},
        "self_critique": {"type": "string"},
    },
}
