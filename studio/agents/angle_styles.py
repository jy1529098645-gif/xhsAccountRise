"""Per-angle writing playbooks for the Drafter pool.

Why this exists
---------------
The Drafter pool fans out one candidate per user-selected angle. Each draft
historically received THE SAME Strategist-produced strategy block (one
recommended_hook, one structure, one tone, one opening_hook). That block
was then prepended as a "必须遵从" directive — which homogenised every
candidate's hook + structure regardless of the angle chosen.

Result: user picks `教程 + 故事 + 段子` → got three candidates that all
share the strategist's "数字型 hook + 列表结构 + soft CTA" → indistinguishable
in feel.

This module gives the Drafter an angle-specific override: when an
`angle_override` is set, we generate a writing playbook tailored to that
angle's natural hook + structure + tone, and instruct the LLM that the
Strategist's block is *reference only* — the angle playbook wins on
conflicts. Result: candidates diverge visibly across the hook / opening /
structure / tone dimensions, which is what "multiple candidates" is
supposed to deliver.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnglePlaybook:
    hook_type: str          # the hook_type label this angle should output
    opening_template: str   # one-sentence guidance for the first line
    structure: tuple[str, ...]  # 4-7 step skeleton
    tone: str               # paragraph-level tonal direction
    avoid: tuple[str, ...] = ()  # angle-specific anti-patterns


# Keep angles aligned with frontend/src/pages/Composer.tsx ANGLES + the
# SYSTEM prompt's longer list. Anything missing here falls back to a
# generic "free-form" block — better than silently overriding to a wrong
# style.
ANGLE_PLAYBOOKS: dict[str, AnglePlaybook] = {
    "教程": AnglePlaybook(
        hook_type="教程型",
        opening_template="问题/需求点出 → 「保姆级教程来了」式承诺",
        structure=(
            "标题包含「N 步 / 保姆级 / 手把手」类信号",
            "开头：先点出读者卡在哪一步（共鸣 1-2 句）",
            "第一步 + 关键截图/数据描述",
            "第二步 + 实操细节",
            "第 N 步 + 收尾",
            "总结 + 适用人群 + 软 CTA",
        ),
        tone="保姆级、清晰、行动导向；句式以「第一步... 第二步...」串联；避免抽象抒情",
        avoid=("不要写成故事化叙事", "不要把步骤压成一段话，必须分点"),
    ),
    "痛点": AnglePlaybook(
        hook_type="痛点型",
        opening_template="第一句直接「我懂你」式共鸣 — 「你是不是也...」",
        structure=(
            "标题点中具体痛点场景（不抽象）",
            "开头：代入痛点细节，画面感强",
            "为什么会发生（共情而不是指责）",
            "解决思路 1 + 即时收益",
            "解决思路 2 + 长期收益",
            "结尾求 +1 共鸣（轻 CTA）",
        ),
        tone="强共情、画面感、口语化、「真的太懂了」「破防了」类情绪词高频；忌教训口吻",
        avoid=("不要立刻给方法 — 先共鸣再给", "不要写成教程的步骤体"),
    ),
    "故事": AnglePlaybook(
        hook_type="故事型",
        opening_template="时间/场景锚点 — 「上周三深夜 2 点」式具体定位",
        structure=(
            "标题带情绪钩或反转预告",
            "开头：场景 + 我（or 朋友）+ 困境",
            "起因 → 经过（动作 + 情绪起伏）",
            "转折/反转（不可预测的那一刻）",
            "结局 + 收获/反思留白",
            "结尾轻引导（求评论分享类似经历）",
        ),
        tone="第一人称叙事，时间线推进，画面感强，情绪起伏明显；避免说教",
        avoid=("不要写成 listicle", "不要中途插入抽象总结，剧情连续走完再总结"),
    ),
    "对比": AnglePlaybook(
        hook_type="对比型",
        opening_template="「A vs B 我都用过 N 个月」类直接对比承诺",
        structure=(
            "标题：A vs B（具体名 + 一句话立场）",
            "开头：我为什么要对比这两个",
            "评测维度公开（3-5 个具体维度）",
            "A 详评 — 每个维度",
            "B 详评 — 每个维度",
            "横评结论 + 适用场景 + CTA",
        ),
        tone="客观、对比明确、数据+结论双轨；句式「A 的优势是... 但 B 在...」",
        avoid=("不要单方面种草其中一个", "不要用主观情绪覆盖维度结论"),
    ),
    "种草": AnglePlaybook(
        hook_type="种草型",
        opening_template="「这个 XX 我必须强推 / 闭眼入 / 平价宝藏」式情绪钩",
        structure=(
            "标题带情绪词 + 产品/工具具体名",
            "开头：发现这个之前我的痛点",
            "使用细节 1 + 真实场景",
            "细节 2 + 反差感受",
            "对比类似品 / 替代方案（简短）",
            "购买/使用建议 + CTA",
        ),
        tone="热情、私人体验、emoji 自然密度高、「闭眼入 / 真心推 / 平价宝藏」类语气",
        avoid=("不要写成测评的评分表", "不要客观中立 — 必须有立场"),
    ),
    "感悟": AnglePlaybook(
        hook_type="感悟型",
        opening_template="具体事件触发 → 「最近我突然意识到」式内省钩",
        structure=(
            "标题克制、不喊话、留白感",
            "开头：触发感悟的具体场景",
            "我观察到了什么",
            "我学到了什么（不上升大道理）",
            "留白思考 — 不给答案",
            "（可选）软 CTA 邀请讨论",
        ),
        tone="克制、留白、情绪内敛；不喊话不说教；句式短，节奏慢",
        avoid=("不要用 emoji 洪水", "不要给方法论步骤", "不要 listicle"),
    ),
    "数字": AnglePlaybook(
        hook_type="数字型",
        opening_template="「N 个 X」「30 天 N 次」类数字 hook",
        structure=(
            "标题以数字打头（N 个 / N 招 / N 件）",
            "开头：一句话总结这 N 个的共同收益",
            "1️⃣ 要点 + 简评",
            "2️⃣ 要点 + 简评",
            "... N 要点",
            "总结排序 / 推荐顺序 + CTA",
        ),
        tone="信息密度高、listicle 结构、每点紧凑、节奏快；少废话",
        avoid=("不要每点写成段落抒情", "数字必须真实可核 — 不凑数"),
    ),
    "建议": AnglePlaybook(
        hook_type="建议型",
        opening_template="问题/误区点出 → 「建议你...」式行动钩",
        structure=(
            "标题：建议 + 受众（如「给 ddl 党的 5 个建议」）",
            "开头：常见误区/痛点导出建议必要性",
            "建议 1 + 为什么 + 怎么做",
            "建议 2 + 为什么 + 怎么做",
            "... N 建议",
            "总结 + 适用人群 + CTA",
        ),
        tone="直白、行动可执行、句式「建议你...」「记得...」「别再...」",
        avoid=("不要写成抽象大道理", "每条建议都要有 why + how"),
    ),
    "工具评测": AnglePlaybook(
        hook_type="工具型",
        opening_template="「我用了 N 天/N 个月」+ 工具具体名 hook",
        structure=(
            "标题：工具名 + 我用了多久 + 一句话立场",
            "开头：为什么开始用这个工具",
            "优点 1 + 真实场景举证",
            "优点 2 + ...",
            "缺点 / 局限（具体不洗白）",
            "对比类似工具 + 适合谁用 + CTA",
        ),
        tone="单品深度、第一人称、优劣并列；避免空夸或全黑",
        avoid=("不要写成横评（那是「对比/测评」角度）", "缺点不可省略"),
    ),
    # ---- 扩展角度（SYSTEM prompt 提到但 UI 没列的） ----
    "科普": AnglePlaybook(
        hook_type="科普型",
        opening_template="「是什么 / 为什么」反常识钩",
        structure=(
            "标题：现象/概念 + 「为什么」",
            "开头：现象描述",
            "原理 / 机制",
            "举例 / 类比",
            "影响 / 应用",
            "CTA（轻问答邀请）",
        ),
        tone="信息为主、逻辑链清晰、句式偏陈述；不写「怎么做」",
        avoid=("不要给 step-by-step（那是教程）", "不要主观抒情"),
    ),
    "避雷": AnglePlaybook(
        hook_type="避雷型",
        opening_template="「别踩 X」「我踩了你别再踩」式警告钩",
        structure=(
            "标题：避雷 + 具体场景/产品",
            "开头：我踩的坑（具体细节）",
            "为什么会坑（机制/陷阱）",
            "替代方案 1",
            "替代方案 2",
            "CTA（求 +1 经验）",
        ),
        tone="警告语气、before/after 对比、句式「不要... 应该...」；情绪化但不夸张",
        avoid=("不要写成单纯吐槽 — 必须给替代方案", "避免编造黑料"),
    ),
    "测评": AnglePlaybook(
        hook_type="测评型",
        opening_template="「N 款 X 横评」+ 公开评分维度钩",
        structure=(
            "标题：N 款产品横评 + 维度",
            "开头：评测背景 + 维度公开",
            "产品 1 详评 + 各维度分",
            "产品 2 详评 + 各维度分",
            "... N",
            "总榜 + 适用场景 + CTA",
        ),
        tone="评分维度透明、对比公正、数据驱动；句式「在 X 维度，A 拿 8 分」",
        avoid=("不要单边种草", "维度数必须一致"),
    ),
    "段子": AnglePlaybook(
        hook_type="段子型",
        opening_template="反差/夸张/自嘲 hook — 第一句就要让人想发笑",
        structure=(
            "标题：段子化（反差/玩梗/自嘲）",
            "开头：情景设置 + 自嘲/吐槽",
            "梗的递进 + emoji 高密度",
            "更夸张的反转",
            "punchline / 落点",
            "CTA（玩梗式邀请评论）",
        ),
        tone="情绪化、emoji 高密度、黑话/梗多、自嘲反差；语气词从「参考爆款」里学",
        avoid=("不要学术八股", "不要照搬通用「破防 / 蚌埠」— 用该库实际的方言"),
    ),
    "盘点": AnglePlaybook(
        hook_type="盘点型",
        opening_template="「N 件 X 合集」「年度盘点」式策展钩",
        structure=(
            "标题：N 件/N 个 + X 合集",
            "开头：策展角度（按什么标准盘点）",
            "#1 + 1-2 句简评",
            "#2 + ...",
            "... #N",
            "总结策展观点 + CTA",
        ),
        tone="策展感、节奏紧凑、每条简评 1-2 句；不展开抒情",
        avoid=("不要每条写成段落（那是 listicle 教程）", "盘点要有逻辑顺序"),
    ),
    "复盘": AnglePlaybook(
        hook_type="复盘型",
        opening_template="「day N / N 个月后」时间锚 + 数据钩",
        structure=(
            "标题：复盘 + 时间跨度 + 数据/结果",
            "开头：目标 + 起点",
            "过程关键节点",
            "数据 + 教训（诚实复盘）",
            "下一步 / 修正计划",
            "CTA（求 +1 经验或问答）",
        ),
        tone="诚恳、数据透明、句式「这 N 个月我...」「结果是...」；避免炫耀或丧气",
        avoid=("不要只列成功不写失败", "数字必须真实"),
    ),
    "问答": AnglePlaybook(
        hook_type="问答型",
        opening_template="「最常被问 ：xxx」「私信问爆了」式钩",
        structure=(
            "标题：N 个最常问的问题 + 一次答完",
            "开头：为什么集中答这些（被问爆了类）",
            "Q1 + A1",
            "Q2 + A2",
            "... QN + AN",
            "总结 + CTA（继续问）",
        ),
        tone="答题型、句式「Q：... A：...」；A 段答得具体不打太极",
        avoid=("Q 太长", "A 给空话敷衍"),
    ),
    "打卡": AnglePlaybook(
        hook_type="打卡型",
        opening_template="「day N 我又 XX 了」日记体钩",
        structure=(
            "标题：day N + 行为 + 进度",
            "开头：今天的具体场景",
            "做了什么（具体动作）",
            "进度数据（数字透明）",
            "感受 / 反思",
            "明天计划 + CTA",
        ),
        tone="日记体、第一人称、进度透明、轻松不卖惨",
        avoid=("不要写成总结复盘（那是复盘）", "数字要日复一日有连续性"),
    ),
    "教训": AnglePlaybook(
        hook_type="教训型",
        opening_template="「我花了 X 才懂 / 多花 N 块才学会」式血泪钩",
        structure=(
            "标题：教训 + 具体代价",
            "开头：事件经过简述",
            "代价（钱/时间/机会，具体数字）",
            "教训 1 + 为什么这样",
            "教训 2 + ...",
            "CTA（求 +1 经验）",
        ),
        tone="诚恳、自我批评、具体成本透明；比避雷更深一层（不只是避免，而是已经栽过）",
        avoid=("不要写成单纯吐槽（那是段子）", "代价必须有具体数字"),
    ),
}


def angle_playbook_block(angle: str) -> str | None:
    """Return the per-angle writing playbook as a prompt-ready text block.

    Returns None for unknown angles — caller should fall through to the
    generic SYSTEM prompt's angle list. Better to silently skip than to
    fabricate a wrong style.
    """
    pb = ANGLE_PLAYBOOKS.get(angle)
    if pb is None:
        return None
    structure_lines = "\n".join(f"  · {s}" for s in pb.structure)
    avoid_block = (
        "\n- 本角度避坑：" + "；".join(pb.avoid)
        if pb.avoid else ""
    )
    return (
        f"【本稿角度专属写法 — 必须按这个走，与 Strategist 通用策略冲突时以此为准】\n"
        f"- 角度：{angle}\n"
        f"- hook_type 必须是：{pb.hook_type}\n"
        f"- 开头第一句的写法：{pb.opening_template}\n"
        f"- 结构骨架（按顺序铺）：\n{structure_lines}\n"
        f"- 语气方向：{pb.tone}"
        f"{avoid_block}"
    )
