"""16 大起号目标分类 — 决定 prompt voice + required fields + 阶段强度。

不同 goal 的整套打法差异巨大 ：
  · personal_share / emotional → 弱转化，强真实感，无产品依赖
  · academic / teaching → 干货深度，需要专业领域 context
  · product_saas → 强转化路径，强依赖产品上下文
  · physical_product → 实物体验，对比测评为主
  · tech_review → 专业但易懂，避免技术黑话
  · career_business → 干货 + 案例，避免成功学
  · food / travel / fitness → 生活方式垂类，重图 / 重视频
  · fashion / beauty → 强配图 + 真人出镜，对比 + 测评
  · parenting / pets / home → 场景化 + 真情实感

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
            "(c) 避免「打工人觉醒 / 财富自由」类陈词淘调。"
        ),
    ),
    # ───────────────────── 生活方式 / 视觉垂类（v0.61.16 新增） ─────────────────────
    GoalType(
        key="food",
        emoji="🍳",
        name="美食 · 探店 · 厨艺",
        description="探店打卡 / 自制料理 / 菜谱教程 / 美食 vlog",
        voice_hint=(
            "馋人 + 接地气，少形容多细节。"
            "标题用「绝了 / 必吃 / 闭眼冲 / 自制级 / 翻车」类。"
            "正文要给具体地址 / 价格 / 步骤 / 第一口味道。"
        ),
        phase_emphasis="全 4 阶段，强配图 / 短视频比例",
        requires_product_context=False,
        recommended_product_context=False,
        example_directions=(
            "城市探店清单", "家常菜教程", "网红店实测",
            "便宜大碗合集", "翻车 / 避雷",
        ),
        prompt_addendum=(
            "美食内容 ：(a) 每篇必须有真实细节（价格 / 地址 / 食材克数 / 第一口的味道），"
            "(b) 推荐图文或短视频形式，不要纯文字，"
            "(c) 避雷帖比安利更易爆（用户对踩坑信任度高），"
            "(d) 别用「人均不到 50」这类已被烂用的套话，给具体单价 + 推荐单品。"
        ),
    ),
    GoalType(
        key="travel",
        emoji="✈️",
        name="旅游 · 攻略 · vlog",
        description="旅行 vlog / 行程攻略 / 小众目的地 / 民宿安利",
        voice_hint=(
            "真实第一视角 + 美景描述，避免广告腔。"
            "标题用「N 天 N 夜 / 小众绝美 / 反向旅游 / 穷游」类。"
            "正文给完整行程 + 预算 + 实拍图 + 个人感受。"
        ),
        phase_emphasis="全 4 阶段，强视觉（图 / 视频）",
        requires_product_context=False,
        recommended_product_context=False,
        example_directions=(
            "城市深度攻略", "小众目的地", "民宿 / 酒店探店",
            "穷游 / 反向旅游", "旅行 vlog",
        ),
        prompt_addendum=(
            "旅游内容 ：(a) 每篇必须给总预算 / 天数 / 季节 / 推荐人群，"
            "(b) 推 ≥ 1 个「打卡误区 / 避雷点」（透明感涨粉），"
            "(c) 推荐图文 + 多图 + 地图标记，纯文字不利于扩散，"
            "(d) 旺季 / 淡季差异要点明，别让用户去了发现关门。"
        ),
    ),
    GoalType(
        key="fitness",
        emoji="💪",
        name="健身 · 减脂 · 运动",
        description="健身打卡 / 减脂日记 / 训练动作教学 / 运动装备测评",
        voice_hint=(
            "真实进度 + 实拍 before/after，避免假大空。"
            "标题用「30 天减 N 斤 / 实测 / 0 基础 / 在家就能练」类。"
            "正文给完整训练计划 / 饮食 / 翻车点。"
        ),
        phase_emphasis="全 4 阶段；2-3 周复盘 + 数据图爆款率高",
        requires_product_context=False,
        recommended_product_context=False,
        example_directions=(
            "减脂日记", "动作教学", "健身房 vs 居家",
            "饮食搭配", "before/after 复盘",
        ),
        prompt_addendum=(
            "健身内容 ：(a) 数据必须真实可核（体脂 / 围度 / 体重），别瞎吹「3 天瘦 10 斤」，"
            "(b) 每个动作必须给注意事项 + 错误示例，避免伤人，"
            "(c) 推荐图 / 视频混合 ：动作要视频，复盘要图文，"
            "(d) 严守医学合规 ：不开药、不诊断、不替代医生建议。"
        ),
    ),
    GoalType(
        key="fashion",
        emoji="👗",
        name="穿搭 · 时尚",
        description="OOTD / 穿搭技巧 / 单品测评 / 风格分析 / 身材适配",
        voice_hint=(
            "真人出镜 + 不同角度实拍。语气：「微胖救星 / 显瘦 10 斤 / 这样穿绝了」。"
            "正文给身材 / 价格 / 哪里买 / 搭配公式。"
        ),
        phase_emphasis="全 4 阶段，强配图（≥ 4 张）",
        requires_product_context=False,
        recommended_product_context=True,
        example_directions=(
            "微胖 / 高个 / 小个穿搭", "通勤穿搭", "风格教学",
            "单品平替", "OOTD 周记",
        ),
        prompt_addendum=(
            "穿搭内容 ：(a) 必须给身材数据（身高 / 体重 / 三围之一），不然没说服力，"
            "(b) 每件单品给价格 + 渠道，平替版本更涨粉，"
            "(c) 推荐图文形式，≥ 4 张多角度配图，纯文字不出爆款，"
            "(d) 别用「2024 巴黎流行」这种空头话，给本地可买可穿的真实方案。"
        ),
    ),
    GoalType(
        key="beauty",
        emoji="💄",
        name="美妆 · 护肤",
        description="化妆教程 / 护肤心得 / 单品测评 / 肤质适配 / 平价好物",
        voice_hint=(
            "真人出镜 + before/after。语气：「平价宝藏 / 烂脸自救 / 一支搞定」。"
            "正文给肤质 + 步骤 + 价格 + 持妆时长。"
        ),
        phase_emphasis="全 4 阶段；测评 + before/after 爆款率最高",
        requires_product_context=False,
        recommended_product_context=True,
        example_directions=(
            "化妆教程", "护肤步骤", "单品测评", "肤质适配",
            "平替合集 / 翻车避雷",
        ),
        prompt_addendum=(
            "美妆 / 护肤 ：(a) 必须自报肤质 + 痛点（油皮 / 干皮 / 敏感肌 / 闭口 / 痘印），"
            "(b) 推荐时给「我适合 / 不适合」而非绝对推荐，"
            "(c) 强烈推荐图文 + 多张实拍（妆前妆后 / 上脸效果），"
            "(d) 严守合规 ：不夸药效 / 不写「医美级」/ 不替代医生建议。"
        ),
    ),
    GoalType(
        key="parenting",
        emoji="👶",
        name="母婴 · 育儿",
        description="孕期 / 新生儿 / 早教 / 亲子互动 / 育儿避雷 / 妈妈复盘",
        voice_hint=(
            "真情实感 + 真实场景，避免育儿专家腔。"
            "标题用「N 个月宝宝 / 二胎妈妈 / 别这样做 / 哭着写完」类。"
            "正文给月龄 / 具体场景 / 走过的弯路。"
        ),
        phase_emphasis="情感连接 + 共鸣 > 转化",
        requires_product_context=False,
        recommended_product_context=False,
        example_directions=(
            "孕期记录", "新生儿日常", "早教方法",
            "育儿避雷 / 翻车", "亲子互动",
        ),
        prompt_addendum=(
            "母婴内容 ：(a) 必须自报宝宝月龄 + 妈妈身份（一胎 / 二胎 / 全职 / 双职工），"
            "(b) 严守医学合规 ：不开药、不诊断婴儿症状、强调「不舒服立刻就医」，"
            "(c) 共鸣类「我也这样过」比建议类「你应该这样」更涨粉，"
            "(d) 推产品时说自己「踩过的坑」更可信，纯安利容易掉粉。"
        ),
    ),
    GoalType(
        key="pets",
        emoji="🐾",
        name="宠物 · 萌宠",
        description="宠物日常 / 养宠经验 / 训练 / 宠物用品测评 / 萌宠 vlog",
        voice_hint=(
            "宠物视角 + 主人口吻交替，可爱 + 沙雕 + 真实。"
            "标题用「我家狗 / 这只猫 / 主子又 / 铲屎官血泪」类。"
            "正文给品种 / 年龄 / 性格 / 具体糗事或经验。"
        ),
        phase_emphasis="全 4 阶段，强视觉（短视频爆款率高）",
        requires_product_context=False,
        recommended_product_context=False,
        example_directions=(
            "宠物日常 vlog", "养宠避雷", "训练教程",
            "宠物用品测评", "搞笑沙雕段子",
        ),
        prompt_addendum=(
            "宠物内容 ：(a) 必须自报品种 + 年龄 + 性格（柴 / 中华田园犬 / 布偶 等），"
            "(b) 推荐短视频形式，宠物日常 + 表情 + 反应 = 流量密码，"
            "(c) 涉及医疗 / 健康一律加「具体情况请咨询兽医」，"
            "(d) 别用「我家最聪明」这种夸张吹，真实糗事比吹牛更涨粉。"
        ),
    ),
    GoalType(
        key="home",
        emoji="🛋️",
        name="家居 · 装修 · 收纳",
        description="装修过程 / 家居好物 / 收纳整理 / 改造 / 选品避雷",
        voice_hint=(
            "真实预算 + 实拍对比。语气：「N 万搞定 / 后悔买 / 闭眼入 / 全屋」。"
            "正文给预算 / 户型 / 品牌 / 购买渠道。"
        ),
        phase_emphasis="全 4 阶段，强配图（before/after / 多角度）",
        requires_product_context=False,
        recommended_product_context=True,
        example_directions=(
            "装修日记", "家居好物", "收纳整理",
            "小户型改造", "踩坑避雷",
        ),
        prompt_addendum=(
            "家居 / 装修 ：(a) 必须给户型 + 面积 + 总预算 + 风格，"
            "(b) 「后悔系列 / 踩坑系列」比纯安利更易爆，"
            "(c) 推荐图文 ：before / after / 局部细节 / 全屋俯视，"
            "(d) 涉及具体品牌 / 师傅给联系方式时要注明「我自费 / 非广告」。"
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
