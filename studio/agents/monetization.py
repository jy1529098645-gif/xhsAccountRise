"""Monetization scoring + private-domain hook generation.

Given a finalized draft, two LLM calls produce ：

1) commercial_score（商单评估）：
   - score 0-10 是否适合恰饭 / 接广告
   - factors: 真实感损失 / 用户反感预测 / 转化路径自然度 / 风险点
   - estimated_price_band: 建议商单价位区间（粉丝量未知时给相对评级）

2) private_lead_scripts（私域引流话术）：
   - 3-5 个 「评论区引导」 话术（口语化、引发互动 + 引向私信）
   - 3-5 个 「私信开场」 话术
   - 1 个 「主页简介」短描述（可放到 bio 或下一篇引流卡）

不写死。每条都让 LLM 据 draft 内容 + 选定的 monetization_intent 生成。
"""
from __future__ import annotations

import json
import time
from typing import Any

from ..generators import registry
from ..llm_call import call_for_json


COMMERCIAL_SYSTEM = """\
你是「内容变现评估师」。看一条已完成的稿件 + 用户的变现意图，给出 ：
1) 商单评估（0-10 分这条稿适合接广告 / 恰饭吗）
2) 关键判断因子
3) 建议商单价位区间（相对评级，因为不知道账号真实粉丝量）

评估维度（每项 0-10）：
- authenticity_preservation ：恰饭这条会损害账号「真实感」吗？低 = 损害严重，高 = 几乎不损害
- audience_friction ：粉丝读完会反感吗（「这是广告」检测度）？低 = 显眼广告腔，高 = 软到看不出
- conversion_path ：转化路径是否自然（评论 / 收藏 / 点击都对吗）？低 = 突兀，高 = 自然
- compliance_risk ：触发平台限流 / 风控 / 标记的概率？低 = 高风险，高 = 安全
- pricing_leverage ：这种风格的稿件议价空间？低 = 难卖，高 = 易卖好价

综合 commercial_score（0-10）：是否值得用这条接商单（不是稿件本身质量，是恰饭适配度）。

输出 JSON ：
{
  "commercial_score": <0-10>,
  "factors": {
    "authenticity_preservation": <0-10>,
    "audience_friction": <0-10>,
    "conversion_path": <0-10>,
    "compliance_risk": <0-10>,
    "pricing_leverage": <0-10>
  },
  "estimated_price_band": "<低 / 中 / 高 三档>",
  "verdict": "<≤80 字坦诚说这条恰饭合不合适 + 主要理由>",
  "risks": ["<具体风险点 1>", "<具体风险点 2>"],
  "suggestions": ["<如要恰饭可以这样改 1>", "<改进点 2>"]
}
"""

COMMERCIAL_SCHEMA = {
    "type": "object",
    "required": ["commercial_score", "factors", "estimated_price_band",
                 "verdict", "risks", "suggestions"],
    "properties": {
        "commercial_score": {"type": "number"},
        "factors": {
            "type": "object",
            "properties": {
                "authenticity_preservation": {"type": "number"},
                "audience_friction": {"type": "number"},
                "conversion_path": {"type": "number"},
                "compliance_risk": {"type": "number"},
                "pricing_leverage": {"type": "number"},
            },
        },
        "estimated_price_band": {"type": "string"},
        "verdict": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "suggestions": {"type": "array", "items": {"type": "string"}},
    },
}


LEAD_SCRIPTS_SYSTEM = """\
你是「私域引流话术专家」。给你一条小红书 / 抖音稿件，生成配套的私域引流
话术。目标 ：自然引导用户去评论 → 私信 → 加微信 / 进社群，**不踩平台违规线**
（不能明示「加微信 / 加我 V / QQ群号」 这种导流硬词，但可以「私信我 / 评论扣关键词」）。

3 类输出 ：

1) comment_prompts（评论区引导）：3-5 个一句话，是稿件作者**自己评论自己稿件**
   引发讨论 + 引到私信的话。例 ：「评论区扣『资料』我整理发你」「家人们我是不是
   被坑了，求骂醒」
2) dm_opener（私信开场白）：3-5 个 ，当用户私信后作者第一句回复，软引到下一步
   （送资料 / 加社群 / 接深度咨询）。例 ：「家人来啦~ 我先发你完整版的
   PDF，等下还有我整理的 prompt 模板要不要？」
3) bio_oneliner（主页简介）：1 个 ≤25 字主页 bio 描述，让用户看到主页就有
   私域引流钩子。例 ：「领资料 ↓ 主页第一条置顶」「干货 / 福利 / 答疑 → 看
   评论区置顶」

🚫 严守底线 ：
- 不出现「加微信 / 加 V / QQ / 联系电话 / 加群号」 这种硬导流词
- 不承诺「保过 / 包过 / 100% 通过」
- 不编造身份 / 资质 / 案例数据
- 「私信扣 + 关键词」是平台默许的最强引流形式 ，多用这个

输出 JSON ：
{
  "comment_prompts": ["<话术 1>", "<话术 2>", ...],
  "dm_opener": ["<开场 1>", "<开场 2>", ...],
  "bio_oneliner": "<≤25 字>"
}
"""

LEAD_SCRIPTS_SCHEMA = {
    "type": "object",
    "required": ["comment_prompts", "dm_opener", "bio_oneliner"],
    "properties": {
        "comment_prompts": {"type": "array", "items": {"type": "string"}},
        "dm_opener": {"type": "array", "items": {"type": "string"}},
        "bio_oneliner": {"type": "string"},
    },
}


async def evaluate_commercial(
    payload: dict[str, Any],
    *,
    monetization_intent: str = "soft_lead",
    model_spec: str = "openai:gpt-4o",
) -> dict[str, Any]:
    """商单评估 ：score + factors + price band + 风险 + 改进建议。

    Args:
        payload: candidate payload dict（含 title / body / tags 等）
        monetization_intent: "none" | "soft_lead" | "hard_sell" | "brand_collab"
        model_spec: LLM spec
    """
    title = str(payload.get("title", ""))
    body = str(payload.get("body", ""))
    tags = payload.get("tags") or []
    user_msg = (
        f"【稿件】\n"
        f"标题 ：{title}\n"
        f"正文 ：\n{body}\n\n"
        f"tags ：{tags}\n\n"
        f"【用户变现意图】{monetization_intent}\n"
        f"  · none = 纯涨粉，不想恰饭\n"
        f"  · soft_lead = 软引流到私域 / 评论 / 收藏\n"
        f"  · hard_sell = 直接挂商单 / 卖货\n"
        f"  · brand_collab = 品牌植入 / 软广\n\n"
        f"请按 system 给的 schema 输出商单评估 JSON。"
    )
    gen = registry.build(model_spec)[0]
    return await call_for_json(
        gen, COMMERCIAL_SYSTEM, user_msg,
        max_tokens=2000,
        tool_name="submit_commercial_evaluation",
        schema=COMMERCIAL_SCHEMA,
    )


async def generate_lead_scripts(
    payload: dict[str, Any],
    *,
    model_spec: str = "claude:sonnet",
) -> dict[str, Any]:
    """生成私域引流话术（评论 / 私信开场 / 主页简介）。"""
    title = str(payload.get("title", ""))
    body = str(payload.get("body", ""))
    user_msg = (
        f"【稿件】\n"
        f"标题 ：{title}\n"
        f"正文 ：\n{body}\n\n"
        f"请按 system 给的 schema 输出私域引流话术 JSON。"
    )
    gen = registry.build(model_spec)[0]
    return await call_for_json(
        gen, LEAD_SCRIPTS_SYSTEM, user_msg,
        max_tokens=1500,
        tool_name="submit_lead_scripts",
        schema=LEAD_SCRIPTS_SCHEMA,
    )
