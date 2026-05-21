"""Douyin operations playbook — codified from a 10,091-video analysis.

The numbers here are NOT made up. They come from a structured analysis of
10,091 Douyin videos in the academic-writing / AI-tools / 留学 niche over
2018-05 → 2026-05 (see assets/抖音视频起号数据分析报告.pdf).

We use this playbook in two ways:
  1. As prompt-context: when a Composer/Strategy run targets `platform=douyin`,
     we splice key sections into the LLM's system prompt so it writes against
     known baselines (e.g. "AI工具教程 类内容的中位收藏赞比 = 48.5%, 远超 emotion-driven
     content 的 4.9%; 该桶适合走可保存清单, 不要堆情绪").
  2. As scoring context: after generation, we ask the LLM to predict the new
     draft's 赞粉比 / 收藏赞比 / 分享赞比 vs the relevant bucket baseline, and
     the frontend renders the prediction with color-coded vs-threshold pills.

Re-distill this file when new analysis reports come in — DON'T let it drift
silently from the source data.
"""
from __future__ import annotations

from typing import Any


# ----------------------------------------------------------------------------
# §1. Sample-wide engagement distribution
# ----------------------------------------------------------------------------

GLOBAL_DISTRIBUTION = {
    "sample_size": 10091,
    "search_keywords": 85,
    "authors": 6926,
    "date_range": "2018-05-26 → 2026-05-17",
    "median_follower_count": 1433,
    "median_duration_sec": 55,
    "median_likes": 34,
    "median_total_interaction": 53,
    "p90_total_interaction": 13_000,
    "p95_total_interaction": 49_000,
    "max_total_interaction": 2_494_000,
    "key_insight": (
        "极端长尾分布。绝大多数视频在 P50 以下；爆款率定义 = 总互动 ≥ P90 (13k)"
        " 的视频占比。起号决策必须看中位/P90/低粉爆款率，不能看均值。"
    ),
}


# ----------------------------------------------------------------------------
# §2. Indicator definitions (the language the AI uses to discuss performance)
# ----------------------------------------------------------------------------

METRICS = {
    "总互动":     {"formula": "点赞 + 收藏 + 评论 + 分享", "use": "视频整体反馈强度"},
    "互动粉丝比": {"formula": "总互动 / max(粉丝数, 1)",   "use": "低粉账号破圈能力"},
    "赞粉比":     {"formula": "点赞 / max(粉丝数, 1)",     "use": "内容是否超出账号基础盘"},
    "收藏赞比":   {"formula": "收藏数 / 点赞数",            "use": "教程/清单/工具的沉淀价值"},
    "分享赞比":   {"formula": "分享数 / 点赞数",            "use": "情绪共鸣、社交货币、转发传播"},
    "评论赞比":   {"formula": "评论数 / 点赞数",            "use": "争议、共鸣、故事性和讨论强度"},
    "爆款率":     {"formula": "总互动 ≥ P90(13k) 的视频占比", "use": "方向是否容易出现上限"},
}


# ----------------------------------------------------------------------------
# §3. Content-type buckets — the SIX baseline performance profiles
# ----------------------------------------------------------------------------
# Each entry: 中位总互动 / P90 总互动 / 爆款率 / 中位收藏赞比 / 中位分享赞比
# + the "起号建议" — how to use this bucket strategically.
# Sorted by 中位总互动 desc (heaviest performers first), NOT by bucket size.

CONTENT_BUCKETS: list[dict[str, Any]] = [
    {
        "id": "emotion_drama",
        "label": "情绪段子/剧情娱乐",
        "video_count": 10,
        "median_total": 2104,
        "p90_total": 64_000,
        "viral_rate": 0.40,
        "median_save_ratio": 0.049,
        "median_share_ratio": 0.093,
        "playbook": (
            "极高 P90 (40% 爆款率！)，但样本小、强情绪驱动。适合早期账号做"
            " 1-2 条破圈测试；不可作为主线 — 同质化快、转化弱。"
        ),
        "best_for": ["拉新", "破圈测试"],
        "tip": "靠夸张反差、真实尴尬、deadline 戏剧化场景出片",
    },
    {
        "id": "service_conversion",
        "label": "服务/辅导商业转化",
        "video_count": 23,
        "median_total": 254,
        "p90_total": 12_000,
        "viral_rate": 0.087,
        "median_save_ratio": 0.202,
        "median_share_ratio": 0.096,
        "playbook": (
            "样本少但收藏赞比 20% — 用户看完会保存。适合账号有一定信任度后"
            " 引入服务/产品介绍（例如 AcademiCats 工作台的展示），别在冷启动期跑。"
        ),
        "best_for": ["转化", "私信"],
        "tip": "对比 before/after、客户案例、'不要这样问 AI'",
    },
    {
        "id": "ai_tutorial",
        "label": "AI论文/降AI/工具教程",
        "video_count": 3523,
        "median_total": 155,
        "p90_total": 28_000,
        "viral_rate": 0.139,
        "median_save_ratio": 0.485,  # ★ 最高
        "median_share_ratio": 0.118,
        "playbook": (
            "★ 起号主力桶 — 样本最多(3523)、收藏赞比 48.5% 全样本最高、爆款率 13.9%。"
            " 用户看完会保存，反复看 → 完播率好 → 自然推荐。适合 'DeepSeek 降 AI"
            " 三步指令'、'Turnitin 前自查 checklist'、'1分钟把 DeepSeek 接进 Word'"
            " 这类干货视频。新号 35% 流量分给这桶。"
        ),
        "best_for": ["拉新", "沉淀", "完播后私信"],
        "tip": "1-3 个具体步骤 + 截图/演示 + 收藏引导",
    },
    {
        "id": "academic_writing",
        "label": "论文写作/学术技能",
        "video_count": 225,
        "median_total": 103,
        "p90_total": 50_000,
        "viral_rate": 0.258,  # 仅次于情绪桶
        "median_save_ratio": 0.175,
        "median_share_ratio": 0.080,
        "playbook": (
            "25.8% 爆款率！样本中等(225)但 P90 5w — 真正能打的细分方向。"
            " 适合 'reference list 最常见 5 个错误'、'一段 literature review 怎么改'"
            " 这类硬技能拆解。15% 流量分给这桶。"
        ),
        "best_for": ["拉新", "主页停留"],
        "tip": "学术细节型 (rubric/reference/methodology)，要求真实可执行",
    },
    {
        "id": "ddl_panic",
        "label": "赶due/ddl/拖延崩溃",
        "video_count": 3005,
        "median_total": 53,
        "p90_total": 17_000,
        "viral_rate": 0.110,
        "median_save_ratio": 0.062,
        "median_share_ratio": 0.055,
        "playbook": (
            "样本量大(3005)、爆款率 11%，但收藏赞比仅 6.2% — 用户共鸣分享但不保存。"
            " 适合做情绪开头钩子，**必须叠加 AI工具桶的解决方案**才能完成 '情绪 →"
            " 干货 → 转化' 闭环。40% 流量分给这桶，且严格 30 天 30 条以内别全做这个。"
        ),
        "best_for": ["拉新", "互动"],
        "tip": "前 3 秒戏剧化 + 中段引出工具 + 结尾求救/共鸣引导",
    },
    {
        "id": "lifestyle_identity",
        "label": "留学生活/身份共鸣",
        "video_count": 3090,
        "median_total": 23,
        "p90_total": 1529,
        "viral_rate": 0.038,
        "median_save_ratio": 0.133,
        "median_share_ratio": 0.063,
        "playbook": (
            "样本最大(3090)但中位互动最低(23)、爆款率最低(3.8%) — 看起来量多其实"
            " 难做。适合用来贴标签（让算法知道账号是留学方向）+ 偶尔做共鸣段子，"
            " 但**不要作为主线**。10% 流量分给这桶。"
        ),
        "best_for": ["标签巩固", "粉丝沉淀"],
        "tip": "真实 vlog、海外/国内对比、不卷推荐位",
    },
]

CONTENT_BUCKET_BY_ID = {b["id"]: b for b in CONTENT_BUCKETS}


# ----------------------------------------------------------------------------
# §4. Top opportunity keywords (机会分 = 中位互动 + 爆款率 + 低粉爆款数 加权)
# ----------------------------------------------------------------------------
# Higher 机会分 = better for a new account: not just "high engagement" but
# "low-follower accounts can break through here".

OPPORTUNITY_KEYWORDS: list[dict[str, Any]] = [
    {"keyword": "一站式写论文",   "videos": 124, "median_total": 35_000, "p90_total": 248_000, "viral_rate": 0.734, "low_fan_viral": 55, "score": 7.44},
    {"keyword": "AI写论文",       "videos": 162, "median_total":  7_710, "p90_total": 132_000, "viral_rate": 0.383, "low_fan_viral": 49, "score": 5.65},
    {"keyword": "ChatGPT写论文",  "videos": 143, "median_total":  3_187, "p90_total":  51_000, "viral_rate": 0.301, "low_fan_viral": 32, "score": 4.92},
    {"keyword": "AI写作工具",     "videos": 141, "median_total":  4_399, "p90_total":  45_000, "viral_rate": 0.234, "low_fan_viral": 24, "score": 4.72},
    {"keyword": "AI论文工具",     "videos":  72, "median_total":  3_369, "p90_total":  44_000, "viral_rate": 0.222, "low_fan_viral": 13, "score": 4.41},
    {"keyword": "DeepSeek写论文", "videos": 146, "median_total":  1_544, "p90_total":  55_000, "viral_rate": 0.219, "low_fan_viral": 29, "score": 4.36},
    {"keyword": "rubric看不懂",   "videos":  93, "median_total":  2_114, "p90_total":  48_000, "viral_rate": 0.237, "low_fan_viral": 10, "score": 4.23},
    {"keyword": "写论文工具",     "videos":  91, "median_total":  1_166, "p90_total":  23_000, "viral_rate": 0.165, "low_fan_viral": 11, "score": 3.79},
    {"keyword": "final week崩溃", "videos":  93, "median_total":    685, "p90_total":  66_000, "viral_rate": 0.215, "low_fan_viral": 11, "score": 3.78},
    {"keyword": "一晚上写论文",   "videos": 126, "median_total":    111, "p90_total": 117_000, "viral_rate": 0.302, "low_fan_viral": 19, "score": 3.61},
    {"keyword": "留子破防",       "videos": 130, "median_total":    290, "p90_total":  66_000, "viral_rate": 0.215, "low_fan_viral": 16, "score": 3.59},
    {"keyword": "留子精神状态",   "videos":   5, "median_total":  1_857, "p90_total": 152_000, "viral_rate": 0.200, "low_fan_viral":  1, "score": 3.54},
    {"keyword": "留子paper",      "videos": 141, "median_total":    189, "p90_total":  77_000, "viral_rate": 0.220, "low_fan_viral": 20, "score": 3.52},
    {"keyword": "AI率",           "videos": 128, "median_total":    388, "p90_total":  21_000, "viral_rate": 0.141, "low_fan_viral": 15, "score": 3.41},
    {"keyword": "留子ddl",        "videos": 137, "median_total":    246, "p90_total":  46_000, "viral_rate": 0.182, "low_fan_viral": 15, "score": 3.40},
    {"keyword": "最后一晚写论文", "videos": 100, "median_total":    150, "p90_total": 111_000, "viral_rate": 0.230, "low_fan_viral": 13, "score": 3.35},
    {"keyword": "AI降重",         "videos": 109, "median_total":  1_068, "p90_total":  11_000, "viral_rate": 0.073, "low_fan_viral":  7, "score": 3.32},
    {"keyword": "留子崩溃",       "videos": 155, "median_total":    141, "p90_total":  31_000, "viral_rate": 0.148, "low_fan_viral": 19, "score": 3.15},
    {"keyword": "paper没写完",    "videos": 106, "median_total":     80, "p90_total": 124_000, "viral_rate": 0.217, "low_fan_viral": 11, "score": 3.05},
    {"keyword": "paper急救",      "videos": 113, "median_total":    361, "p90_total":  19_000, "viral_rate": 0.142, "low_fan_viral":  4, "score": 3.04},
]


# ----------------------------------------------------------------------------
# §5. Hashtag priors — pick from the top frequency band
# ----------------------------------------------------------------------------
# Distinct from keyword opportunity. Hashtags drive discovery via Douyin's
# topic graph; pick 3-5 per video balanced across:
#   - 1 broad-audience tag (留学生 / 大学生)
#   - 1 specific-pain tag (赶due / turnitin / 毕业论文)
#   - 1 tool/scene tag (DeepSeek / AI / essay)
#   - 0-1 emotion tag (留子破防 / 留子精神状态)

HASHTAG_PRIOR: list[tuple[str, int]] = [
    ("留学生",     1502),
    ("留学",       1451),
    ("essay",       839),
    ("英国留学",    688),
    ("论文",        645),
    ("赶due",       641),
    ("毕业论文",    553),
    ("留学日常",    469),
    ("papermaster", 417),
    ("留子",        399),
    ("大学生",      369),
    ("出国留学",    309),
    ("降ai",        299),
    ("论文写作",    282),
    ("澳洲留学",    269),
    ("留学生活",    257),
    ("研究生",      254),
    ("英语",        241),
]


# ----------------------------------------------------------------------------
# §6. Duration buckets — short-form is the winner on engagement efficiency
# ----------------------------------------------------------------------------

DURATION_BUCKETS: list[dict[str, Any]] = [
    {"range": "≤7s",    "min_sec":   0, "max_sec":   7, "videos":  212, "median_total":  86, "p90_total": 76_000, "best_for": "钩子测试 / 反转梗"},
    {"range": "8-15s",  "min_sec":   8, "max_sec":  15, "videos":  954, "median_total":  36, "p90_total": 29_000, "best_for": "情绪段子 / 一句话痛点"},
    {"range": "16-30s", "min_sec":  16, "max_sec":  30, "videos": 1815, "median_total":  40, "p90_total": 20_000, "best_for": "工具演示 1 个 / 3 步法"},
    {"range": "31-60s", "min_sec":  31, "max_sec":  60, "videos": 2366, "median_total":  69, "p90_total": 13_000, "best_for": "★ 中位互动最高桶 — 完整教程"},
    {"range": "61-120s","min_sec":  61, "max_sec": 120, "videos": 2291, "median_total":  60, "p90_total":  9_955, "best_for": "深度拆解 / 案例展示"},
    {"range": ">120s",  "min_sec": 121, "max_sec": None, "videos": 2446, "median_total":  48, "p90_total":  9_032, "best_for": "长教程 / 课程预告 (P90 持续下降)"},
]

DURATION_RECOMMENDATION = (
    "起号前 30 天以 7-30 秒为主（钩子+选题+账号标签测试）。当某类内容连续跑出"
    "高收藏(收藏赞比 >15%) 或高分享(分享赞比 >10%) 时，扩展到 45-90 秒做教程、"
    "案例拆解和转化内容。"
)

PUBLISH_TIME_WINDOWS = (
    "12:00-13:30、17:00-20:30、21:00-23:00 三个窗口优先测试。留学方向可额外"
    "测试海外时区深夜/清晨情绪场景。发布时间是次级变量，选题和前 3 秒钩子更关键。"
)


# ----------------------------------------------------------------------------
# §7. KPI thresholds (起号阶段)
# ----------------------------------------------------------------------------
# These thresholds drive the "predicted_metrics" pills in DraftDetail —
# AI estimates a draft's 赞粉比/收藏赞比/分享赞比, frontend renders each
# threshold-band as a colored chip.

KPI_THRESHOLDS = {
    "赞粉比": {
        "weak":   (0.0,  0.20, "<20% 基础盘内部"),
        "good":   (0.20, 1.00, "20-100% 选题有效"),
        "strong": (1.00, None, ">100% 强破圈"),
        "action": "命中 good 以上 → 同主题复拍 3-5 条；strong → 整理成系列",
    },
    "收藏赞比": {
        "weak":   (0.00, 0.15, "<15% 沉淀价值低"),
        "good":   (0.15, 0.30, "15-30% 有工具价值"),
        "strong": (0.30, None, ">30% 强教程价值 — 做模板/清单/合集"),
        "action": "命中 good 以上 → 把这条做成可下载模板的引子",
    },
    "分享赞比": {
        "weak":   (0.00, 0.10, "<10% 社交价值弱"),
        "good":   (0.10, 0.20, "10-20% 有共鸣"),
        "strong": (0.20, None, ">20% 强社交货币 — 扩成系列梗"),
        "action": "命中 good 以上 → 同情绪/痛点二次创作",
    },
    "评论赞比": {
        "weak":   (0.00, 0.05, "<5% 讨论冷"),
        "good":   (0.05, 0.15, "5-15% 有共鸣/求助"),
        "strong": (0.15, None, ">15% 强讨论 — 评论区收集痛点二创"),
        "action": "评论区收集痛点 → 下一条直接回答",
    },
}


# ----------------------------------------------------------------------------
# §8. Title library categories
# ----------------------------------------------------------------------------
# The 1380-entry title library breaks into these 15 categories. When the AI
# picks a hook style for a draft, it picks from one of these — and we tell
# the frontend which category came in for transparency.

TITLE_CATEGORIES = (
    "DDL急救夸张标题",
    "带梗夸张标题池",
    "留子精神状态梗标题",
    "Essay/Paper没写完标题",
    "文献检索标题",
    "引用格式标题",
    "Rubric/Assignment标题",
    "论文框架标题",
    "查重自查与修改标题",
    "AI工具与工作台标题",
    "毕业论文/开题标题",
    "评论互动标题",
    "反差故事标题",
    "黑色幽默标题",
    "效率对比标题",
)


# ----------------------------------------------------------------------------
# §9. Content matrix — recommended traffic distribution for a new account
# ----------------------------------------------------------------------------

CONTENT_MATRIX = [
    {"pillar": "A. 情绪共鸣短视频",  "share": 0.40, "buckets": ["emotion_drama", "ddl_panic"],
     "target_kpis": ["分享赞比", "评论赞比"],
     "sample_topics": ["论文 due tomorrow 但我还在改标题", "留子赶 due 的五个精神阶段"]},
    {"pillar": "B. AI/工具教程",     "share": 0.35, "buckets": ["ai_tutorial"],
     "target_kpis": ["收藏赞比", "完播后私信"],
     "sample_topics": ["DeepSeek 降 AI 味三步指令", "Turnitin 前自查 checklist"]},
    {"pillar": "C. 学术写作拆解",    "share": 0.15, "buckets": ["academic_writing"],
     "target_kpis": ["收藏", "主页停留"],
     "sample_topics": ["reference list 最常见 5 个错误", "一段 literature review 怎么改"]},
    {"pillar": "D. 服务信任内容",    "share": 0.10, "buckets": ["service_conversion"],
     "target_kpis": ["私信", "转化"],
     "sample_topics": ["改论文前后对比", "不要这样问 AI 写 essay"]},
]


# ----------------------------------------------------------------------------
# §10. Helpers used by prompts.py and the API
# ----------------------------------------------------------------------------

def bucket_for_text(text: str) -> dict[str, Any] | None:
    """Cheap keyword routing — pick a content bucket for a topic/title.
    Returns the bucket dict or None when nothing matches confidently.

    Used so the AI's prompt can include the right baseline numbers (e.g.
    "你这条归 AI工具教程 桶 — 中位收藏赞比 48%，目标命中 >30%"). Falls back
    to the AI-tutorial bucket as the most likely fit for our niche.

    Order matters: most-specific context wins over generic emotion words.
    "final week 崩溃" should route to ddl_panic, NOT emotion_drama —
    "崩溃" alone is too generic vs the DDL signal in "final week".
    """
    if not text:
        return None
    t = text.lower()
    # Most specific first ↓
    if any(k in t for k in ("辅导", "代写", "降重服务", "代改", "找我", "私信咨询",
                            "商单", "服务介绍")):
        return CONTENT_BUCKET_BY_ID["service_conversion"]
    if any(k in t for k in ("ai", "chatgpt", "deepseek", "工具", "降ai", "turnitin",
                            "降重", "指令", "prompt", "插件", "gpt")):
        return CONTENT_BUCKET_BY_ID["ai_tutorial"]
    if any(k in t for k in ("reference", "literature", "rubric", "methodology",
                            "essay", "paper", "论文", "abstract", "conclusion",
                            "discussion", "thesis", "dissertation",
                            "查重", "引用", "文献", "开题")):
        return CONTENT_BUCKET_BY_ID["academic_writing"]
    if any(k in t for k in ("ddl", "due", "deadline", "final week", "赶due", "拖延",
                            "通宵", "最后", "急救", "ddl前夜", "最后一晚")):
        return CONTENT_BUCKET_BY_ID["ddl_panic"]
    # Most generic last ↓ — emotion / lifestyle only catch when nothing
    # more specific matched.
    if any(k in t for k in ("段子", "崩溃", "破防", "反差", "尴尬", "搞笑")):
        return CONTENT_BUCKET_BY_ID["emotion_drama"]
    if any(k in t for k in ("留学", "留子", "海外", "国外", "宿舍", "图书馆",
                            "vlog", "日常", "生活")):
        return CONTENT_BUCKET_BY_ID["lifestyle_identity"]
    return CONTENT_BUCKET_BY_ID["ai_tutorial"]  # default to the main bucket


def classify_kpi(metric_name: str, value: float) -> dict[str, Any]:
    """Map a predicted ratio to its band ('weak'|'good'|'strong')."""
    thresholds = KPI_THRESHOLDS.get(metric_name)
    if not thresholds or value is None:
        return {"band": "unknown", "label": "—"}
    for band in ("strong", "good", "weak"):
        lo, hi, label = thresholds[band]
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return {"band": band, "label": label}
    return {"band": "weak", "label": thresholds["weak"][2]}


def playbook_context_for_prompt(topic: str | None = None) -> str:
    """Compact text rendering of the playbook to splice into a Douyin
    generation prompt. Keep it scoped to the chosen bucket so we don't bloat
    the prompt with all 6 buckets every time."""
    bucket = bucket_for_text(topic or "") if topic else None
    lines: list[str] = [
        "【抖音运营 playbook（来自 10091 条真实视频分析）】",
        f"全样本中位总互动 = {GLOBAL_DISTRIBUTION['median_total_interaction']}，"
        f"P90 = {GLOBAL_DISTRIBUTION['p90_total_interaction']:,}（爆款阈值）。",
        f"中位时长 = {GLOBAL_DISTRIBUTION['median_duration_sec']}s。",
    ]
    if bucket:
        lines.append(
            f"\n你这条视频归属内容桶: 【{bucket['label']}】"
            f"（样本 {bucket['video_count']} 条）"
        )
        lines.append(
            f"  · 该桶基线: 中位总互动 {bucket['median_total']:,} / "
            f"P90 {bucket['p90_total']:,} / 爆款率 {bucket['viral_rate']:.1%}"
        )
        lines.append(
            f"  · 中位收藏赞比 {bucket['median_save_ratio']:.1%} / "
            f"分享赞比 {bucket['median_share_ratio']:.1%}"
        )
        lines.append(f"  · 桶内 playbook: {bucket['playbook']}")
        lines.append(f"  · 写作建议: {bucket['tip']}")
    lines.append("\n【关键 KPI 阈值（起号阶段）】")
    for name, t in KPI_THRESHOLDS.items():
        lines.append(
            f"  · {name}: weak <{t['weak'][1]:.0%} / "
            f"good {t['good'][0]:.0%}-{t['good'][1] or 1:.0%} / "
            f"strong >{t['strong'][0]:.0%}"
        )
    lines.append("\n【时长建议】" + DURATION_RECOMMENDATION)
    return "\n".join(lines)


def opportunity_keywords_summary(top_n: int = 10) -> str:
    """Compact list of high-机会分 keywords to feed into the prompt as
    'these are the underserved topics — bias your titles toward them'."""
    rows = OPPORTUNITY_KEYWORDS[:top_n]
    lines = ["【机会分 Top 关键词（综合中位互动+爆款率+低粉爆款）】"]
    for r in rows:
        lines.append(
            f"  · {r['keyword']} (机会 {r['score']:.2f}, "
            f"爆款率 {r['viral_rate']:.0%}, 低粉爆款 {r['low_fan_viral']} 条)"
        )
    return "\n".join(lines)


def hashtag_prior_summary(top_n: int = 12) -> str:
    """Hashtag picks for the prompt. We tell the AI to compose 3-5 tags
    distributed across (broad audience / specific pain / tool / emotion)."""
    rows = HASHTAG_PRIOR[:top_n]
    lines = ["【高频 hashtag 池（按频次降序）】"]
    lines.append("  " + " / ".join(f"#{t} ({n})" for t, n in rows))
    lines.append(
        "  挑选规则：3-5 个 hashtag，覆盖 1 个泛人群 + 1 个具体痛点 + 1 个工具/场景"
        " + 可选 1 个情绪标签。不要全部堆同一类。"
    )
    return "\n".join(lines)
