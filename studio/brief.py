"""Brief — the input contract for any generation request.

A Brief carries everything a generator needs:
    - topic: 主题，决定 RAG 检索的核心词（e.g. "降 AI 率技巧"）
    - angle: 角度类型（教程/痛点/故事/工具评测/对比/感悟）
    - target_length: 目标正文字数
    - cta_strength: 转化引导强度 (none|soft|strong)
    - niche: 内部分类（用于复盘聚合）
    - reference_note_ids: 强制参考的笔记 id（可空，retriever 会自动补）
    - extra_constraints: 自由文本，附加要求（"不要露出品牌名"等）

Persisted into studio_drafts.brief_json as-is, so future replays are exact.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Literal


Angle = Literal["教程", "痛点", "故事", "工具评测", "对比", "感悟", "数字", "种草", "建议"]
CtaStrength = Literal["none", "soft", "strong"]


_PLATFORM_VOICE: dict[str, str] = {
    "xiaohongshu": "面向下沉学生群体，第一人称，emoji 节奏自然，分点用 1️⃣2️⃣，标题 15-22 字强 hook",
    "douyin": "短视频脚本风：前 3 秒钩子 + 一句一冲突 + 反转结尾，正文按镜头切分，适合口播",
    "kuaishou": "老铁文化，接地气真实感，短句多，避免学术腔和过度修饰",
    "bilibili": "二次元/学生友好，长内容能 hold，可加梗和 emoji，分章节式书写",
    "youtube": "国际化，可英文混编，长文 ok，开头明确 hook + outline，结尾 CTA 订阅",
    "reddit": "长文论证体，自嘲幽默 ok，禁营销腔，提供数据/源链接，逐步推理",
    "x": "极短（每段 ≤ 280 字符），强 hook，可拆 thread，提名引用",
    "other": "通用风格，按主题写得清晰、口语化",
}


@dataclass(frozen=True)
class Brief:
    topic: str
    angle: Angle = "教程"
    target_length: int = 600
    cta_strength: CtaStrength = "soft"
    niche: str = ""
    reference_note_ids: tuple[str, ...] = ()
    extra_constraints: str = ""
    platform: str = "xiaohongshu"

    def voice_hint(self) -> str:
        return _PLATFORM_VOICE.get(self.platform, _PLATFORM_VOICE["other"])

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, payload: str) -> "Brief":
        d = json.loads(payload)
        d["reference_note_ids"] = tuple(d.get("reference_note_ids") or ())
        d.setdefault("platform", "xiaohongshu")
        return cls(**d)
