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


@dataclass(frozen=True)
class Brief:
    topic: str
    angle: Angle = "教程"
    target_length: int = 600
    cta_strength: CtaStrength = "soft"
    niche: str = ""
    reference_note_ids: tuple[str, ...] = ()
    extra_constraints: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, payload: str) -> "Brief":
        d = json.loads(payload)
        d["reference_note_ids"] = tuple(d.get("reference_note_ids") or ())
        return cls(**d)
