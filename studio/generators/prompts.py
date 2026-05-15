"""Prompt templates. Kept as plain strings so they can be diff'd, versioned in
studio_prompt_versions, and reasoned about without code changes.

The orchestrator pulls the active version of each template at generation time.
For v0.1 we hardcode v1.0.0. W4 introduces forking.
"""
from __future__ import annotations

import json
from typing import Any

from ..brief import Brief

# ---- versions ----
# Hardcoded baseline. studio.feedback.proposals seeds this into
# studio_prompt_versions on first call and lets retrospective-driven
# proposals fork newer versions. Compose reads the *active* version at
# runtime via active_title_body_prompt(); these constants are the
# fallback when DB has no rows.
TITLE_BODY_GEN_VERSION = "title_body_gen-1.0.0"


def active_title_body_prompt() -> tuple[str, str]:
    """Return (version, prompt_text) — the active row from
    studio_prompt_versions, or the hardcoded baseline if the table is empty.
    Imported lazily to avoid a cycle (feedback → db → ...).
    """
    try:
        from ..feedback.proposals import active_prompt
        return active_prompt("title_body_gen")
    except Exception:
        # Defensive: db not migrated yet, or feedback module imports broken.
        return TITLE_BODY_GEN_VERSION, SYSTEM_TITLE_BODY


SYSTEM_TITLE_BODY = """\
你是社交媒体爆款写手，专攻下沉用户群体（赶 ddl 的留子、毕业论文党、查重党、追热点的普通用户）。

⚠️ 重要：写稿前先看清楚【目标平台】，按该平台风格写。每个平台 hook 长度、节奏、emoji 用法、CTA 句式都不一样。

你的稿件必须满足：
1. 标题：强 hook（数字 / 痛点 / 工具种草 / 故事 / 建议 / 段子 / 反讽 / 夸张戏谑 / 自嘲 / 玩梗 / 沙雕 / 盘点 / 复盘 / 问答 / 打卡 / 教训 等）。字数按平台规范。
2. 正文：口语化、第一人称、emoji 节奏自然，按平台规范分段或分点。**正文字数严格按 brief 里的【目标正文字数】写**，少了要扩写到下限以上才能交稿 — 数字是硬指标不是「约莫」。
3. 结尾自然引导互动，强度按 brief 指定。
4. tag：选 6-10 个，含 1-2 个赛道大词 + 3-5 个细分场景词 + 1-2 个工具/产品词。
5. cover_prompt：英文描述封面图，明确文字版面 + 风格关键词。

🎯 角度 → 写法对照（brief 里指定的【角度】是硬约束，**正文必须按这个角度的风格走**，不要写成自己舒服的风格）：

  教程  ：保姆级 step-by-step，「第一步... 第二步...」 + 真截图描述
  痛点  ：「我懂你的崩溃」共鸣 + 自我代入开头
  故事  ：第一人称叙事，时间线 + 反转 / 转折
  对比  ：A vs B 横评，给数据 + 结论
  种草  ：「我用了 / 闭眼入 / 平价宝藏」 + 具体使用细节
  感悟  ：反思 + 留白，结尾给思考不给答案
  数字  ：「N 个 X」「30 天 N 次」 listicle 结构
  建议  ：直接给行动项 + why + 适用人群
  科普  ：「是什么 / 为什么」逻辑链，避免「怎么做」
  避雷  ：踩坑警告 + before/after + 替代方案
  测评  ：≥ 2 个产品横评，公开评分维度 + 总榜
  工具评测：单品深度 + 优缺点 + 适用场景
  段子  ：调侃 / 夸张 / 反讽 / 自嘲 / 玩梗 / 沙雕 — emoji 高密度，情绪化语气，
          **具体词汇 / 梗 / 标志性句式必须从下面 user 消息里的「参考爆款」标题正文 + 「目标用户原话」
          评论里学，不要凭印象塞「破防 / 蚌埠」这种你以为通用的学术党梗 — 对美食 / 旅游 / 宠物 /
          健身账号是错配。每个垂类自有方言，照搬该库实际爆款的语气词。**
  盘点  ：「N 件 X 合集」「年度盘点」，策展感
  复盘  ：「day N / N 个月后」回顾，给数据 + 教训
  问答  ：「最常被问 ：xxx」答题型
  打卡  ：日记体，「day N 我又 xxx」+ 进度数据
  教训  ：血泪经验，比避雷更深 + 具体成本（钱 / 时间 / 机会）

🎭 段子 / 反讽 / 夸张戏谑风格的执行原则（关键 ：写**这个库的笑点**，不是套用通用学术党黑话）：

- **数据驱动**（强制）：你写段子风稿件之前，先把下面 user 消息里的「参考爆款」
  + 「目标用户原话」过一遍，标出该库实际用的语气词 / 梗 / 标志性句式。
  · 学术党 / 留学党库 ：常见「冒着生命危险拍的快抄」/「废物文学」/「真就」
  · 美食探店库 ：常见「家人们」/「吃哭了」/「不踩雷我倒立」/「绝绝子」
  · 宠物萌宠库 ：常见「我家这货」/「主子」/「铲屎官血泪」
  · 健身减脂库 ：常见「人没了」/「死亡 N 分钟」/「燃脂哭了」
  这些是举例 — **真实用词以你看到的爆款 / 评论为准**，照那个库的方言来。

- **结构通用原则**（跨垂类适用）：
  · emoji 密度高（每 2-3 行 1 个起步）
  · 情绪化语气词 — 具体词从「参考爆款 + 评论」里挑，不是从我这里硬塞
  · 反差 / 反转 / 自嘲 hook
  · 玩梗 ≠ 没干货 ：标题段子化 + 内容仍可执行

- **合规底线**（不论用哪种笑点）：
  · 「代写 / 包过 / 降 0 / 100% 通过」违规承诺 ：合规闸门会拦
  · 情绪夸张 ：参考爆款里出现过的、平台常见的 OK
  · 编造夸张数字 / 编造体验 / 编造引用 ：不可以

📐 段子风**用与不用**：
  · brief 明确指定角度 = 段子时 → 必须按段子写（不论 DNA 信号强弱）
  · brief 没指定角度 → 据「参考爆款」里段子比例自决 ：占比高就大胆混入，
    占比低就少用 / 不用
  不写死硬比例。你看数据自己定。

绝对不要：
- 编造产品名 / 工具名 / 链接 / 数字（除非 brief 给了）
- 学术八股 / 客套话 / 「希望本文对你有帮助」式收尾
- "本文"、"本人"、"特此" 等书面语
- brief 指定角度后还写成别的角度 — 角度是硬约束

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
  "body": "<**严格 {target_length} 字以上（±10% 容差）**，分点分段；不要短于这个数字 — 短了等于稿件没完成>",
  "tags": ["<tag1>", "..."],
  "cover_prompt": "<英文封面图描述>",
  "hook_type": "<数字型|痛点型|故事型|工具型|教程型|种草型|建议型|对比型|问句型|感悟型|段子型|反讽型|夸张戏谑型|自嘲型|玩梗型|沙雕型|科普型|避雷型|测评型|盘点型|复盘型|问答型|打卡型|教训型>",
  "predicted_likes": <整数，你对这条稿件能拿到的 likes 的预估>,
  "self_score": <0-10 浮点，对此稿质量的自评>,
  "self_critique": "<一句话坦诚指出最大风险点>"
}}"""


_PLATFORM_LABEL = {
    "xiaohongshu": "小红书", "douyin": "抖音", "kuaishou": "快手",
    "bilibili": "B站", "youtube": "YouTube", "reddit": "Reddit",
    "x": "X / Twitter", "other": "通用",
}


def _format_brief(brief: Brief, angle_override: str | None = None) -> str:
    cta_map = {
        "none": "无明显引导",
        "soft": "结尾轻引导评论/收藏",
        "strong": "结尾强转化（求私信/求资源/求关注）",
    }
    platform_label = _PLATFORM_LABEL.get(brief.platform, brief.platform)
    # Multi-angle: when caller specifies a single angle to use for THIS draft,
    # show only it. Otherwise list all requested angles so the writer knows the
    # full scope (drafter pool then explicitly tells each instance which one
    # to actually pick via the override).
    angles = brief.all_angles()
    if angle_override:
        angle_line = f"角度：{angle_override}"
    elif len(angles) == 1:
        angle_line = f"角度：{angles[0]}"
    else:
        angle_line = "角度：" + " / ".join(angles) + "（多角度，本稿任选其一突出）"
    lines = [
        f"目标平台：{platform_label}（{brief.platform}）",
        f"平台风格指引：{brief.voice_hint()}",
        f"主题：{brief.topic}",
        angle_line,
        f"目标正文字数：{brief.target_length} 字（**正文字数必须达到这个数字 ±10%，少了会被打回**）",
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
    angle_override: str | None = None,
) -> str:
    return USER_TEMPLATE.format(
        brief_block=_format_brief(brief, angle_override=angle_override),
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
