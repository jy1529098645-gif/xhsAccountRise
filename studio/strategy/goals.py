"""8 大起号目标分类 — 决定 prompt voice + required fields + 阶段强度。

不同 goal 的整套打法差异巨大 ：
  · personal_share / emotional → 弱转化，强真实感，无产品依赖
  · academic / teaching → 干货深度，需要专业领域 context
  · product_saas → 强转化路径，强依赖产品上下文
  · physical_product → 实物体验，对比测评为主
  · tech_review → 专业但易懂，避免技术黑话
  · career_business → 干货 + 案例，避免成功学

每个 goal 有 ：
  · key            ：id（前后端共享）
  · emoji + name   ：UI 展示
  · description    ：选择卡片上的一句话
  · voice_hint     ：注入到 Strategist / Drafter prompts 的语气描述
  · phase_emphasis ：4 阶段权重的偏好（个人分享类轻转化，产品类强转化）
  · requires_product_context  ：是否强制要求产品上下文
  · recommended_product_context：是否推荐产品上下文（弱依赖）
  · example_directions：示例方向，供 UI 卡片预览
  · prompt_addendum ：注入到 POSITIONER + SCHEDULER 的额外约束
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class GoalType:
    key: str
    emoji: str
    name: str
    description: str
    voice_hint: str
    phase_emphasis: str   # 简短说明 4 阶段权重
    requires_product_context: bool
    recommended_product_context: bool
    example_directions: tuple[str, ...]
    prompt_addendum: str   # 注入 prompt 的目标特定约束

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["example_directions"] = list(self.example_directions)
        return d


GOAL_TYPES: tuple[GoalType, ...] = (
    GoalType(
        key="personal_share",
        emoji="👤",
        name="个人分享 · 生活记录",
        description="记录日常生活 / 心情 / 成长经历 / 建立个人 IP，弱转化导向",
        voice_hint=(
            "真实第一人称、不带卖货语气、共鸣型 hook 优先。"
            "标题可以用「最近 / 我发现 / 突然想说」起头。"
            "结尾不强推产品，引导评论区互动。"
        ),
        phase_emphasis="拉新 + 沉淀为主（弱转化）",
        requires_product_context=False,
        recommended_product_context=False,
        example_directions=(
            "日常 vlog 切片", "周记 / 月度复盘", "个人成长心得",
            "情绪记录与解读",
        ),
        prompt_addendum=(
            "目标是建立**个人 IP**而非卖产品。每篇必须有真实感、"
            "有具体细节（人 / 事 / 时 / 地），避免空泛抒情。"
            "禁止把每篇都收口到产品 / 课程 / 私信引导。"
        ),
    ),
    GoalType(
        key="emotional",
        emoji="💗",
        name="情感共鸣 · 治愈系",
        description="情感故事 / 心理建议 / 治愈日常，连接相似经历的人",
        voice_hint=(
            "真情实感、共鸣优先、避免说教。"
            "标题可以「破防了 / 我懂你 / 终于」起头。"
            "结尾留白，让评论区帮你完成情感闭环。"
        ),
        phase_emphasis="情感连接 > 转化（建议不做强转化）",
        requires_product_context=False,
        recommended_product_context=False,
        example_directions=(
            "情感答疑", "治愈系日常", "心理小知识 / 自我疗愈",
            "失恋 / 友情 / 亲情复盘",
        ),
        prompt_addendum=(
            "情感账号最忌「假大空」。每篇必须 ：(a) 至少 1 个真实场景 / 对话 / 心境，"
            "(b) 不要给「解决方案」，给「我也这样想过」的共鸣，"
            "(c) 禁止「私信我帮你」类硬转化。"
        ),
    ),
    GoalType(
        key="academic",
        emoji="🎓",
        name="学术 · 学习方法",
        description="论文 / 考研 / 留学 / 知识方法论 / 研究分享",
        voice_hint=(
            "学姐学长腔、专业但口语化、避免学究气。"
            "标题用「3 步 / 保姆级 / 学长说」开头。"
            "正文要给真干货 + 工具 / 模板，可执行性强。"
        ),
        phase_emphasis="全 4 阶段适用（拉新强 hook → 专业感 → 系列 → 工具转化）",
        requires_product_context=False,
        recommended_product_context=True,
        example_directions=(
            "文献综述方法", "留学申请文书", "考研复习经验",
            "论文写作流程", "学术工具种草",
        ),
        prompt_addendum=(
            "学术账号 ：(a) 标题要有「保姆级 / 一文搞懂 / 实测」类信任锚点，"
            "(b) 正文必须给具体步骤 / 工具名 / 模板，不能停在「要注意 XX」级别，"
            "(c) 推荐产品时强调「我自己用的」而非「你必须用」，"
            "(d) 严守合规底线 ：不代写、不包过、不降 0、不绕过查重。"
        ),
    ),
    GoalType(
        key="product_saas",
        emoji="🚀",
        name="产品种草 · SaaS / 工具",
        description="推广软件、APP、SaaS 产品、AI 工具",
        voice_hint=(
            "卖点拆解 + 真实使用案例，避免硬广腔。"
            "标题用「实测 / 用了 30 天 / 终于找到」类前缀。"
            "正文要演示产品在具体场景里怎么解决问题。"
        ),
        phase_emphasis="全 4 阶段适用，第 4 周强转化（私信引导 / 链接）",
        requires_product_context=True,
        recommended_product_context=True,
        example_directions=(
            "产品功能种草", "工作流演示（搜→选→用→出）",
            "vs 竞品横评", "用户证言体", "before/after 对比",
        ),
        prompt_addendum=(
            "⭐⭐ 产品/SaaS 账号必读：(a) 每篇必须真实引用「产品上下文」里的功能名 / 卖点 / "
            "经典叙事，**绝对禁止 hallucinate 不存在的功能名**；"
            "(b) 第 1-2 周稿件不要直接卖（建信任），第 3-4 周才开始强卖点 + CTA；"
            "(c) 至少 30% 选题要用「产品上下文」里的「核心叙事三句话」作为正文片段。"
        ),
    ),
    GoalType(
        key="physical_product",
        emoji="📦",
        name="实物种草 · 商品 / 品牌",
        description="推广实物（美妆 / 数码 / 家居 / 服装等）",
        voice_hint=(
            "真实使用体验、对比突出、配图重于文字。"
            "标题用「闭眼入 / 踩雷过 / 性价比之王」类。"
            "正文给具体场景 + 真实细节（材质 / 重量 / 价格）。"
        ),
        phase_emphasis="全 4 阶段适用，强配图 / 视频比例",
        requires_product_context=False,
        recommended_product_context=True,
        example_directions=(
            "单品深度测评", "多产品横评", "使用场景演示",
            "开箱 / unboxing", "好物合集",
        ),
        prompt_addendum=(
            "实物种草 ：(a) 每篇必须给 ≥ 3 个真实使用细节（不能只说「好用」），"
            "(b) 标价格 / 适用人群 / 缺点（透明感是种草最大的信任锚点），"
            "(c) 强烈推荐用「图文 + 多张配图」格式，不要纯文字。"
        ),
    ),
    GoalType(
        key="tech_review",
        emoji="💻",
        name="科技 · 数码 · 行业洞察",
        description="科技新闻 / AI 测评 / 数码 / 行业趋势 / 技能解析",
        voice_hint=(
            "专业但易懂，避免技术黑话和过度术语。"
            "标题用「拆解 / 实测 / 你不知道的」类。"
            "正文要给原理 + 对比 + 主观判断，不只罗列参数。"
        ),
        phase_emphasis="全 4 阶段适用",
        requires_product_context=False,
        recommended_product_context=True,
        example_directions=(
            "AI 新闻拆解", "数码测评", "工具流对比",
            "行业趋势观察", "技能教程",
        ),
        prompt_addendum=(
            "科技账号 ：(a) 必须有「主观判断」段落（"
            "「我觉得 / 个人最爱 / 不推荐买」），别变成参数搬运工，"
            "(b) 涉及具体产品时引用真实型号 / 价格 / 厂商，"
            "(c) 至少 1 篇用「冷知识 / 拆解 / 反常识」hook。"
        ),
    ),
    GoalType(
        key="teaching",
        emoji="📚",
        name="教学 · 课程 · 技能传授",
        description="单点教学 / 系列课 / 学员案例 / 学习方法",
        voice_hint=(
            "老师腔但亲切，强结构化。"
            "标题用「N 步搞定 / 零基础 / 保姆级」类。"
            "正文必须有清晰编号步骤 + 可执行行动项。"
        ),
        phase_emphasis="全 4 阶段适用，第 4 周转化课程 / 训练营",
        requires_product_context=False,
        recommended_product_context=True,
        example_directions=(
            "技能单点教学", "系列课预告", "学员案例 / before-after",
            "学习方法论", "知识图谱",
        ),
        prompt_addendum=(
            "教学账号 ：(a) 每篇结构必须 ：问题 → 步骤 → 实操 → 收尾，"
            "(b) 每个步骤必须可独立执行，禁止「然后你就会了」式跳跃，"
            "(c) 转化时强调「跟谁学」（老师人设）而非「买什么课」。"
        ),
    ),
    GoalType(
        key="career_business",
        emoji="💼",
        name="职业 · 副业 · 商业",
        description="职场 / 副业方法 / 求职 / 商业案例 / 自媒体变现",
        voice_hint=(
            "干货 + 真实案例，避免成功学口吻。"
            "标题用「我月入 / 副业 N 个月 / 这样做」类。"
            "正文给具体方法 + 数字 + 真实场景，禁吹牛。"
        ),
        phase_emphasis="全 4 阶段适用",
        requires_product_context=False,
        recommended_product_context=True,
        example_directions=(
            "职场技能", "副业方法实操", "求职面试经验",
            "商业案例拆解", "自媒体变现",
        ),
        prompt_addendum=(
            "职场 / 副业 ：(a) 数字必须真实可核（"
            "「副业第一个月赚 800」比「副业月入过万」可信 10 倍），"
            "(b) 给完整流程，不留「私信给你方法」式钓鱼钩子，"
            "(c) 避免「打工人觉醒 / 财富自由」类陈词滥调。"
        ),
    ),
)


def get_goal(key: str | None) -> GoalType | None:
    """Look up a goal by key. Returns None for unknown / empty."""
    if not key:
        return None
    for g in GOAL_TYPES:
        if g.key == key:
            return g
    return None


def list_goals_as_dicts() -> list[dict[str, Any]]:
    return [g.to_dict() for g in GOAL_TYPES]


def goal_voice_block(key: str | None) -> str:
    """Build a prompt-friendly voice + addendum block for the given goal."""
    g = get_goal(key)
    if not g:
        return ""
    return (
        f"【⭐ 起号目标 ：{g.emoji} {g.name}】\n"
        f"  · voice ：{g.voice_hint}\n"
        f"  · 阶段权重 ：{g.phase_emphasis}\n"
        f"  · {g.prompt_addendum}"
    )
