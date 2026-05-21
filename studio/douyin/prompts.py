"""Douyin-shape generation prompts + output schema.

When `Brief.platform == "douyin"`, the drafter routes through this module
instead of the xhs-shaped {title, body, cover_prompt} schema. The output
is structured around what Douyin actually rewards:

    {
      "caption": "<≤30 字, 出现在视频下方的文案>",
      "hashtags": ["留学生", "AI写论文", ...],          # 3-5 个
      "duration_sec_target": 30,                       # 7-120, sweet spot 7-30
      "hook_3s": "<前 3 秒口播 — 完播率取决于此>",
      "shots": [                                       # 分镜表
        {"t": "00:00", "voice": "...", "visual": "..."},
        ...
      ],
      "cta_voice": "<结尾口播 CTA — 评论引导 / 收藏理由>",
      "cover_text": "<封面贴片 4-7 字, 强 hook>",
      "content_bucket_id": "ai_tutorial",              # 6 桶之一
      "predicted_metrics": {
        "赞粉比":   0.35,
        "收藏赞比": 0.42,
        "分享赞比": 0.10,
        "评论赞比": 0.07
      },
      "self_score": 7.5,
      "self_critique": "..."
    }

The system-prompt context includes (a) the relevant content bucket's
real baselines, (b) opportunity-score keywords, (c) hashtag priors,
(d) retrieved title-library entries with category labels. The drafter
then writes against this structured spec.
"""
from __future__ import annotations

from typing import Any

from . import playbook, title_library


# ---- JSON Schema ----------------------------------------------------------

DOUYIN_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "caption", "hashtags", "duration_sec_target",
        "hook_3s", "shots", "cta_voice",
        "content_bucket_id", "predicted_metrics", "self_score",
    ],
    "properties": {
        "caption": {"type": "string", "maxLength": 60},
        "hashtags": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 5,
        },
        "duration_sec_target": {"type": "integer", "minimum": 5, "maximum": 180},
        "hook_3s": {"type": "string"},
        "shots": {
            "type": "array",
            "minItems": 3,
            "items": {
                "type": "object",
                "required": ["t", "voice", "visual"],
                "properties": {
                    "t": {"type": "string"},        # "00:03" or "00:00-00:03"
                    "voice": {"type": "string"},
                    "visual": {"type": "string"},
                },
            },
        },
        "cta_voice": {"type": "string"},
        "cover_text": {"type": "string", "maxLength": 12},
        "content_bucket_id": {
            "type": "string",
            "enum": [b["id"] for b in playbook.CONTENT_BUCKETS],
        },
        "predicted_metrics": {
            "type": "object",
            "required": ["赞粉比", "收藏赞比", "分享赞比", "评论赞比"],
            "properties": {
                "赞粉比":   {"type": "number", "minimum": 0},
                "收藏赞比": {"type": "number", "minimum": 0},
                "分享赞比": {"type": "number", "minimum": 0},
                "评论赞比": {"type": "number", "minimum": 0},
            },
        },
        "self_score": {"type": "number", "minimum": 0, "maximum": 10},
        "self_critique": {"type": "string"},
        # Optional — the LLM can return which title_ids from the library it
        # used as inspiration. We surface these in DraftDetail's Provenance
        # panel so the user can verify "AI did read the title library".
        "library_title_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "maxItems": 8,
        },
    },
}


# ---- System prompt --------------------------------------------------------

SYSTEM = """\
你是「抖音视频脚本师」，专做留学生/学术 AI 工具赛道（论文 / AI 写作 / 降 AI /
DDL / reference / Turnitin），用户给你 brief 之后**直接出可拍可发的分镜脚本**，
不要写图文文章 — 抖音不是小红书。

【硬规则】
1. 抖音不靠正文。**caption ≤ 30 字**，是出现在视频下方的钓鱼文案，不是正文。
2. **hook_3s = 前 3 秒口播**，必须直接砸出痛点 / 反转 / 数字承诺。完播率绝大部分
   决定于此 3 秒。
3. **shots 是分镜表**，每镜 3-5 秒，含口播 + 画面。短视频 (7-30s) ≈ 4-8 镜；
   中视频 (31-60s) ≈ 8-15 镜；长视频 (>60s) ≈ 15-25 镜。
4. **cta_voice = 最后一句口播**，引导评论 / 收藏 / 关注，不要硬推产品。
5. 选 **content_bucket_id**：必须是 6 桶之一，桶决定了基线 KPI（系统会给你看）。
   AI工具教程 桶 (ai_tutorial) 适合做收藏型；情绪段子 桶 (emotion_drama) 适合做分享型。
6. 输出 **predicted_metrics**：基于桶基线 + brief 的 hook 强度，给出本条预估的
   赞粉比 / 收藏赞比 / 分享赞比 / 评论赞比。诚实预估，不要全部填 0.8。
7. **hashtags**：3-5 个，分布 = 1 个泛人群 + 1 个具体痛点 + 1 个工具/场景 +
   可选 1 个情绪标签。不要全堆同一类。
8. **使用参考标题库** — 系统会给你 15-20 条 hand-curated 真实抖音 hook 句式，
   你必须**学其句式 + 节奏**，写出新的版本。**不要逐字照搬**也不要拼接两条。
9. **不要**写 "你好我是xxx" / "今天给大家分享" / "在视频中我们会..." 这类
   B 站长视频开场 — 抖音 1 秒就要砸钩子。
10. **不要**输出 markdown 表格 / 加粗 / 列表符号 — voice 和 visual 字段都是
    纯文本字符串。

【输出 JSON】
按 schema 输出 douyin draft 对象。所有字段必填。每个字段含义见 schema。
"""


# ---- User-side context builder -------------------------------------------

def _format_refs(refs: list[dict[str, Any]]) -> str:
    """Real-video reference block. Reused from xhs path but with Douyin
    fields (duration, share_count, follower_count) prioritised — those are
    the signals that matter on this platform."""
    if not refs:
        return "（无真实视频参考）"
    lines = []
    for i, r in enumerate(refs[:8], 1):
        dur_ms = r.get("video_duration_ms") or 0
        dur = f"{int(dur_ms / 1000)}s" if dur_ms else "?s"
        likes = r.get("liked_count") or 0
        shares = r.get("share_count") or 0
        comments = r.get("comment_count") or 0
        author = r.get("author_nickname") or ""
        lines.append(
            f"#{i} [{dur}, 👍{likes:,} 🔁{shares:,} 💬{comments:,}] "
            f"@{author}\n    {r.get('title','')}"
        )
    return "\n".join(lines)


def _format_brief(brief: Any, target_length: int) -> str:
    """Brief → prompt block. `brief` is studio.brief.Brief but we accept any
    dataclass-shaped object so this works for both Composer + Strategy."""
    angles = getattr(brief, "all_angles", lambda: ())()
    if not angles:
        angles = (getattr(brief, "angle", "教程"),)
    cta = {"none": "无明显引导", "soft": "结尾轻引导评论/收藏",
           "strong": "结尾强转化"}
    lines = [
        f"主题: {brief.topic}",
        f"角度: {' / '.join(angles)}（选 1 个最适合短视频的）",
        f"目标时长: {target_length}s（≤7s 测钩子 / 8-15s 段子 / 16-30s 工具演示"
        f" / 31-60s 教程主桶 / 60+ 深度）",
        f"CTA 强度: {cta.get(brief.cta_strength, brief.cta_strength)}",
    ]
    if getattr(brief, "niche", ""):
        lines.append(f"赛道: {brief.niche}")
    if getattr(brief, "extra_constraints", ""):
        lines.append(f"附加要求: {brief.extra_constraints}")
    return "\n".join(lines)


def build_user_prompt(
    brief: Any,
    *,
    refs: list[dict[str, Any]] | None = None,
    target_duration_sec: int = 30,
) -> str:
    """Compose the full user-side prompt for a Douyin draft."""
    # Bucket lookup — gives the LLM the baselines for its target bucket.
    bucket_ctx = playbook.playbook_context_for_prompt(brief.topic)
    # Top keyword opportunities — bias title selection.
    kw_ctx = playbook.opportunity_keywords_summary(top_n=10)
    # Hashtag prior.
    ht_ctx = playbook.hashtag_prior_summary(top_n=12)
    # Title library retrieval — 15-20 hand-curated hooks matching the topic.
    titles = title_library.search(brief.topic, k=18)
    title_ctx = title_library.render_for_prompt(titles, max_n=18)

    brief_block = _format_brief(brief, target_duration_sec)
    refs_block = _format_refs(refs or [])

    sections = [
        "【Brief】", brief_block, "",
        bucket_ctx, "",
        kw_ctx, "",
        ht_ctx, "",
        title_ctx, "",
        "【同主题真实抖音视频参考（按互动 × 相关度排序）】",
        refs_block, "",
        "请基于以上材料按 schema 输出 douyin draft 对象。",
        "重点：让 hook_3s 砸钩子；shots 写得可拍；predicted_metrics 诚实估。",
        # Also tell the LLM which library titles' IDs we'd like back when
        # it borrows hook structure — purely metadata, doesn't affect text.
        f"如果你借鉴了上面参考标题库里的句式，把对应的 title_id "
        f"（{[t['title_id'] for t in titles[:6]]}... 等可见的 ID）"
        "填到 library_title_ids 字段里。",
    ]
    return "\n".join(s for s in sections if s != "")
