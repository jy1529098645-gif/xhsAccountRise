"""Prompts for the strategy pipeline.

Kept here separately so they can be diff'd and reasoned about without code
churn.
"""
from __future__ import annotations

import json
from typing import Any

from .models import AccountInput, StrategicDirection


# ---- DNA context blob (shared by all agents) -----------------------------

def dna_blurb(dna: dict[str, Any]) -> str:
    """Compact DNA summary for prompting."""
    sections = dna.get("sections", {}) if dna else {}
    bo = sections.get("keyword_blueocean", {}).get("rankings", [])
    titles_top = sections.get("titles", {}).get("top_titles", [])[:10]
    hook_dist = sections.get("titles", {}).get("primary_distribution", {})
    comments = sections.get("comment_demand", {}).get("by_pattern", {})
    tags = sections.get("tags", {}).get("top_tags", [])[:20]
    body_buckets = sections.get("body_and_shape", {}).get("by_body_length", {})

    def trim(s: str, n: int = 80) -> str:
        s = (s or "").replace("\n", " ")
        return s[:n] + ("…" if len(s) > n else "")

    bo_lines = [
        f"  · {b['keyword']} (n={b['note_count']}, avg_likes={int(b['avg_likes'])}, p90={int(b['p90_likes'])})"
        for b in bo[:15]
    ]
    title_lines = [f"  · [{t.get('liked', 0)}] {trim(t.get('title', ''))}" for t in titles_top]
    hook_lines = [f"  · {k}: {v}" for k, v in sorted(hook_dist.items(), key=lambda kv: -kv[1])[:8]]

    demand_lines: list[str] = []
    for label, items in (comments or {}).items():
        if not items:
            continue
        sample = "、".join(trim(it.get("phrase", "") or "", 18) for it in items[:5])
        demand_lines.append(f"  · 「{label}」: {sample}")

    tag_lines = [
        f"  · {t['tag']} (n={t['count']}, avg_likes={int(t['avg_likes'])})"
        for t in tags
    ]

    body_lines = [
        f"  · {b}: 中位 {int(d.get('likes', {}).get('median', 0))} likes (n={d.get('count', 0)})"
        for b, d in body_buckets.items()
    ]

    parts = []
    if bo_lines: parts.append("【蓝海关键词】\n" + "\n".join(bo_lines))
    if title_lines: parts.append("【Top 标题示例】\n" + "\n".join(title_lines))
    if hook_lines: parts.append("【hook 分布】\n" + "\n".join(hook_lines))
    if demand_lines: parts.append("【用户高频询问】\n" + "\n".join(demand_lines))
    if tag_lines: parts.append("【高表现 tag】\n" + "\n".join(tag_lines))
    if body_lines: parts.append("【字数 vs 互动】\n" + "\n".join(body_lines))
    return "\n\n".join(parts) or "（暂无 DNA 数据 — 跑 analyze 后效果更好）"


def input_blurb(inp: AccountInput) -> str:
    return (
        f"目标平台：{inp.platform}\n"
        f"初步定位：{inp.positioning}\n"
        f"目标受众：{inp.target_audience}\n"
        f"运营周期：{inp.cycle_weeks} 周 · 每周发 {inp.posts_per_week} 篇\n"
        f"个人优势：{inp.personal_strengths or '未填'}\n"
        f"附加约束：{inp.constraints or '无'}"
    )


# ---- Positioner ----------------------------------------------------------

POSITIONER_SYSTEM = """\
你是「账号定位策略师」。基于用户的初步想法 + 该平台的爆款数据 (DNA)，给出 3-5 个**差异化**的账号定位方向供用户选择。

要点：
- 方向之间必须有显著差异（受众 / hook 类型 / 内容形态都不同），不要 3 个都"AI 写论文"。
- 每个方向要锚定 DNA 里的实际信号（蓝海词 / 用户原话 / 高表现 hook）。
- 不要凭空编造 — 推荐方向必须能在 DNA 数据里找到支撑。

输出 JSON：
{
  "directions": [
    {
      "name": "<8-12 字短名>",
      "positioning_statement": "<一句话定位，谁的什么问题用什么方式解决>",
      "target_audience": "<比 brief 更精确的受众>",
      "hook_angles": ["<3-5 个具体 hook 角度>"],
      "differentiator": "<跟同赛道账号的差异点>",
      "risk": "<最大风险>",
      "score": <0-10 你预估能成的程度>,
      "why_works": "<2 句话说明这个方向为什么能成，基于 DNA 数据的具体证据>"
    }
  ]
}
"""


# ---- Topic generator (one of N parallel) --------------------------------

TOPICGEN_SYSTEM = """\
你是「选题官」。基于已选定的账号方向 + 该平台 DNA，输出覆盖整个运营周期的**选题清单**。

每个选题要包含：
- title：候选标题（按平台风格写，可顺手给 2 个 variants）
- angle：教程 / 痛点 / 故事 / 工具评测 / 对比 / 感悟 / 数字 / 种草 / 建议
- hook_type：数字型 / 工具型 / 种草型 / 建议型 / 痛点型 / 对比型 / 教程型 / 故事型 / 问句型 / 列表型 / 感悟型
- outline：3-5 条分点内容大纲（不是写正文，只是骨架）
- materials_needed：拍这条需要准备什么（截图 / 工具账号 / 真人录屏 / 数据 / 案例）
- intent：拉新 / 互动 / 转化 / 沉淀

约束：
- 选题数量 = cycle_weeks × posts_per_week
- 主题之间有变化，不要 N 个都是同一个角度
- hook 类型要混搭，不要全部数字型
- 至少 30% 选题来自 DNA 里的「用户高频询问」（直接回应需求）
- 至少 1 个选题用 DNA 里的「蓝海关键词」

输出 JSON：
{
  "topics": [
    {
      "title": "...",
      "title_variants": ["..."],
      "angle": "...",
      "hook_type": "...",
      "outline": ["..."],
      "materials_needed": ["..."],
      "intent": "..."
    }
  ]
}
"""


# ---- Scheduler ----------------------------------------------------------

SCHEDULER_SYSTEM = """\
你是「内容排期师 + 起号文案先稿手」。把多家 LLM 起草的选题候选融合、去重、排成可执行的周历，**并为每一篇直接写出可发布的初稿正文**（用户拿到稍微改改就能发，下一步直接进 Composer 出最终稿）。

输入：
- N 家 LLM 各自产的 topics 列表（合起来可能有 50+ 条候选）
- 周期 = cycle_weeks 周，每周 posts_per_week 篇
- 该平台 DNA 的发布时段热力图（如果有）
- 上一步双 AI 共识报告 + 用户整合 / 上传的外部报告（如有）—— **务必当强参考使用**

任务：
1. 跨家**融合**：取每家最强的选题，能合并的合并，重复的去重，保留覆盖度最广的一批。
2. **排进时间**：按 4 阶段曲线安排
   - 第 1 周：拉新（强 hook + 痛点 + 干货）
   - 第 2 周：建立专业感 + 互动
   - 第 3 周往后：沉淀 + 转化（产品/服务/私域）
3. 给每周一个**主题**和**意图**。
4. 给每个 slot 一个**发布时段建议**（用 DNA 热力图 top 时段）。
5. **为每一个 slot 写出 body_draft（300-600 字的可发布初稿正文）**：
   - 开头一行就是 hook（钩子句），结尾给行动指令或互动钩子
   - 中段按 outline 展开，每段一个明确主张 + 例子
   - 平台适配：小红书加 emoji / 抖音脚本化 / B站偏长 / Reddit 用英文 markdown
   - **直接照搬上一步分析报告里的真实蓝海词、用户原话、爆款 hook 句式**
   - 文风对齐 chosen_direction.positioning_statement 的口吻
   - 250-700 字之间都可以，但要写完整段，不要写"待补"或省略号

最终输出 JSON：
{
  "series_thesis": "<一句话主线>",
  "weekly_themes": [
    {"week": 1, "theme": "...", "intent": "拉新", "notes": "..."}
  ],
  "schedule": [
    {
      "week": 1, "day_of_week": 2,
      "publish_slot": "周三 21:00",
      "title": "...",
      "title_variants": ["..."],
      "angle": "...", "hook_type": "...",
      "outline": ["..."],
      "materials_needed": ["..."],
      "intent": "拉新",
      "body_draft": "<300-600 字的可发布初稿，要写完，不要留省略号>"
    }
  ]
}

严格保证 schedule 长度 = cycle_weeks × posts_per_week。如果候选不够就自己补，但要标 [自补] 在 title 末尾。
body_draft 是这个任务的核心 — 没有 body_draft 等于没完成任务。
"""


# ---- Resourcer ---------------------------------------------------------

RESOURCER_SYSTEM = """\
你是「资源/风险总监」。看完整个排期，输出：

1. **materials_checklist**: 把所有 slots 的 materials_needed 合并、去重、按类别归类（工具账号 / 拍摄设备 / 素材资产 / 数据资产 / 真人体验）的一份采购清单。
2. **risks_and_mitigations**: 这套排期最容易翻车的 3-5 个点 + 怎么提前规避。
3. **success_metrics**: 3-5 个可量化的成功指标（粉丝数 / 互动率 / 私信数 / 转化率…）。

输出 JSON：
{
  "materials_checklist": ["..."],
  "risks_and_mitigations": ["..."],
  "success_metrics": ["..."]
}
"""
