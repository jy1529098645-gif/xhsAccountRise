"""Rule-based title-hook classifier.

Statistical baseline. W2 will overlay an LLM pass to refine ambiguous cases,
but the regex engine stays useful as a fast pre-filter forever.

Categories tuned against the actual top-1000 xhs notes in this corpus —
xhs-native slang (主包, yyds, 神器) and emotional-laconic titles (我好难受,
向前看放轻松) needed explicit handling, otherwise ~42% of titles fell into
"其他", masking real signal.

A title can match multiple categories; we expose the full match list plus a
priority-ordered "primary".
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

# Order matters: higher-up wins the "primary" slot when multiple match.
_PRIORITY = [
    "数字型",
    "工具型",
    "种草型",
    "建议型",
    "痛点型",
    "对比型",
    "教程型",
    "故事型",
    "问句型",
    "列表型",
    "感悟型",
    "emoji型",
    "无标题",
    "其他",
]

_TOOL_BRANDS = (
    # English brands
    "ChatGPT", "Chatgpt", "chatgpt", "GPT", "gpt",
    "DeepSeek", "deepseek", "Deepseek", "deep seek", "Deep Seek",
    "Claude", "claude",
    "Gemini", "gemini",
    "Kimi", "kimi", "Notion", "notion", "Obsidian", "obsidian",
    "Zotero", "zotero", "EndNote", "endnote",
    "Turnitin", "turnitin", "Perplexity", "perplexity", "Felo", "felo",
    "LaTeX", "latex", "WPS", "ProQuest",
    # CN brands & sites
    "豆包", "文心一言", "通义千问", "讯飞星火", "秘塔", "橙篇",
    "知网", "万方", "维普", "格子达", "PaperPass", "paperpass",
    "雨课堂", "学习通",
    # Prompts / instructions
    "提示词", "Prompt", "prompt", "PROMPT", "指令", "AIGC",
)
# "AI" alone is too noisy as a substring (matches "AI 写论文" but also "WaIt").
# Use regex with word-ish boundary on Chinese context.
_AI_RE = re.compile(r"(?:^|[^A-Za-z])[Aa][Ii](?:[^A-Za-z]|$)")

_PAIN_WORDS = (
    "救命", "崩溃", "天塌了", "绝绝子", "谁懂", "谁知道", "破防",
    "哭了", "哭死", "焦虑", "痛苦", "翻车", "踩雷", "踩坑", "避雷", "避坑",
    "求求", "求救", "ddl", "DDL", "deadline", "烦死", "气死", "卷死",
    "熬夜", "通宵", "爆肝", "我好难受", "难受", "崩了",
    "心态崩", "想死", "好累", "太难", "好难",
)

_STORY_MARKERS = (
    "我", "主包", "学姐", "学长", "导师", "室友", "朋友", "同学",
    "博士", "研一", "研二", "研三", "大一", "大二", "大三", "大四",
    "本科生", "留学生", "留子", "学长", "学姐", "妈妈", "闺蜜",
)

_TUTORIAL_WORDS = (
    "教程", "方法", "步骤", "技巧", "攻略", "手把手",
    "指南", "保姆级", "保姆", "教学", "干货", "经验",
    "怎么", "怎样", "如何", "教你", "带你", "学会",
)

_COMPARE_WORDS = (
    "对比", "vs", "VS", "比较", "区别", "选谁",
    "哪个好", "哪个更", "好用还是", "不要再",
)

_LIST_WORDS = (
    "盘点", "合集", "分享", "推荐", "清单", "TOP", "top",
    "Top", "总结", "汇总", "整理", "大全", "笔记合集",
)

_SEED_WORDS = (  # 种草型 — 强转化情绪 + 工具/资源种草 + 现成可抄
    "神器", "好用", "逆天", "腻害", "厉害", "牛逼", "牛皮",
    "yyds", "YYDS", "Yyds", "永远的神",
    "必备", "必学", "必背", "必看", "必读", "必收藏",
    "宝藏", "压箱底", "私藏", "独家",
    "真心好用", "真的有用", "真的可以",
    "免费", "0元", "白嫖", "省钱", "便宜",
    "高效", "效率",
    # 干货型：直接抄/拿去/照着念 —— xhs 高转化模式
    "直接抄", "可以抄", "照着念", "照着抄", "抱走", "快抱走",
    "拿去", "拿走", "送你", "送给", "分享给", "免登录",
    "无脑", "傻瓜", "一键",
)

_RECO_WORDS = (  # 建议型 — 强烈推荐句式
    "建议", "强烈建议", "真心建议", "强推", "墙裂推荐", "建议收藏",
    "一定要", "千万别", "千万不要", "不要再", "请一定",
    "敢说", "敢用", "敢冲",
)

_INSIGHT_WORDS = (  # 感悟型 — 情绪短句 / 鸡汤金句 / 共感独白
    "向前看", "放轻松", "你永远", "其实你", "我好",
    "不能只在", "我才知道", "突然明白", "终于懂了",
    "走过去就好", "慢慢来", "别着急",
    "终于", "刚刚", "原来", "才发现",
    "玻璃心", "低能量",
)

_CHALLENGE_WORDS = (  # 挑战型 — 时间约束 + 完成动作（独立类，触发即归入故事型）
    "挑战", "肝完", "爆肝", "通宵写", "速通", "速成",
)

# Numeric leading: "5个", "3天", "100页", "60秒"...
_NUM_LEAD = re.compile(r"^[\s\W]*\d+[^\d]")
_NUM_ANY = re.compile(r"\d+\s?(?:个|天|步|分钟|秒|小时|页|篇|份|招|条|大|w|W|万|k|K)")
# Chinese-numeral form: "三天", "五个", "一招"…
_CN_NUM = re.compile(
    r"(?:一|两|二|三|四|五|六|七|八|九|十|百|千|万)\s?"
    r"(?:个|天|步|分钟|秒|小时|页|篇|份|招|条|大|招式|绝招|节)"
)

_Q_END = re.compile(r"[?？]$")

# Emoji + decorative-punctuation heuristic.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000027BF"
    "]"
)
_DECO = re.compile(r"[‼❗⭐✨💯🔥👏💕😭😂✅✓]+")


def _contains_any(text: str, words: Iterable[str]) -> bool:
    return any(w in text for w in words)


@dataclass(frozen=True)
class HookResult:
    primary: str
    matched: tuple[str, ...]
    emoji_count: int
    char_count: int
    has_question: bool
    has_number: bool


def classify(title: str) -> HookResult:
    t = (title or "").strip()
    if not t:
        return HookResult("无标题", ("无标题",), 0, 0, False, False)

    matched: list[str] = []

    has_num_lead = bool(_NUM_LEAD.match(t))
    has_num_any = bool(_NUM_ANY.search(t))
    has_cn_num = bool(_CN_NUM.search(t))
    if has_num_lead or has_num_any or has_cn_num:
        matched.append("数字型")

    if _contains_any(t, _TOOL_BRANDS) or _AI_RE.search(t):
        matched.append("工具型")

    if _contains_any(t, _SEED_WORDS):
        matched.append("种草型")

    if _contains_any(t, _RECO_WORDS):
        matched.append("建议型")

    if _contains_any(t, _PAIN_WORDS):
        matched.append("痛点型")

    if _contains_any(t, _STORY_MARKERS) and len(t) >= 4:
        matched.append("故事型")
    elif _contains_any(t, _CHALLENGE_WORDS):
        matched.append("故事型")

    if _contains_any(t, _TUTORIAL_WORDS):
        matched.append("教程型")

    if any(re.search(re.escape(w), t) for w in _COMPARE_WORDS):
        matched.append("对比型")

    if _Q_END.search(t):
        matched.append("问句型")

    if _contains_any(t, _LIST_WORDS):
        matched.append("列表型")

    if _contains_any(t, _INSIGHT_WORDS):
        matched.append("感悟型")

    emoji_count = len(_EMOJI_RE.findall(t)) + len(_DECO.findall(t))
    # Single decorative mark + an exclamation tail still reads as emoji-heavy.
    if emoji_count >= 2 or (emoji_count >= 1 and ("！！" in t or "!!" in t)):
        matched.append("emoji型")

    if not matched:
        matched.append("其他")

    primary = next((c for c in _PRIORITY if c in matched), "其他")

    return HookResult(
        primary=primary,
        matched=tuple(matched),
        emoji_count=emoji_count,
        char_count=len(t),
        has_question=bool(_Q_END.search(t)),
        has_number=has_num_lead or has_num_any,
    )


def classify_many(titles: Iterable[str]) -> list[HookResult]:
    return [classify(t) for t in titles]


def distribution(results: Iterable[HookResult]) -> dict[str, int]:
    return dict(Counter(r.primary for r in results))


def co_occurrence(results: Iterable[HookResult]) -> dict[str, dict[str, int]]:
    out: dict[str, Counter[str]] = {}
    for r in results:
        cats = list(r.matched)
        for c in cats:
            bucket = out.setdefault(c, Counter())
            for other in cats:
                if other != c:
                    bucket[other] += 1
    return {k: dict(v) for k, v in out.items()}
