"""Promote hook categories from the latest DNA artifact into
studio_hook_templates so RAG retrieval has curated, editable templates rather
than falling back to inline DNA JSON.

Idempotent: overwrites any existing template with the same template_id.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .. import db


_SKIP_CATEGORIES = {"其他", "无标题"}


def promote(min_sample_size: int = 30) -> dict[str, Any]:
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT version, payload_json FROM studio_dna_artifacts"
            " ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        raise RuntimeError("no DNA artifact yet — run `studio analyze` first")

    artifact = json.loads(row["payload_json"])
    by_cat: dict[str, Any] = artifact["sections"]["titles"]["by_category"]

    written: list[str] = []
    skipped: list[str] = []
    now = int(time.time())
    for cat, data in by_cat.items():
        if cat in _SKIP_CATEGORIES:
            skipped.append(cat)
            continue
        sample = data.get("count", 0)
        if sample < min_sample_size:
            skipped.append(cat)
            continue

        examples = data.get("examples", [])[:5]
        template_id = f"hook-{cat}"  # stable id so re-promote upserts.
        avg = data.get("likes", {}).get("median", 0)
        db.upsert_hook_template(
            {
                "template_id": template_id,
                "category": cat,
                "pattern": _describe_pattern(cat),
                "example_note_ids_json": json.dumps(examples, ensure_ascii=False),
                "avg_likes": float(avg),
                "sample_size": int(sample),
                "last_updated": now,
                "active": 1,
            }
        )
        written.append(cat)

    return {
        "promoted": written,
        "skipped": skipped,
        "artifact_version": row["version"],
        "min_sample_size": min_sample_size,
    }


def _describe_pattern(cat: str) -> str:
    """Plain-Chinese pattern description for the prompt. Curate freely later."""
    return {
        "数字型": "数字 + 量词 + 成果或时间约束（'5 天搞定毕业论文'）",
        "工具型": "提到具体工具/品牌 + 使用场景（'ChatGPT 写论文超绝指令'）",
        "种草型": "强烈推荐用词（神器/必备/逆天/抱走）+ 产品/资源",
        "建议型": "建议 / 强烈建议 / 千万别 + 行动建议",
        "痛点型": "情绪词（救命/崩了/谁懂）+ 具体痛点描述",
        "对比型": "A vs B / 选谁 / 哪个更好 + 选择困难场景",
        "教程型": "教程 / 手把手 / 保姆级 / 怎么做 + 技能",
        "故事型": "第一人称（我/主包/学姐）+ 经历或成果",
        "问句型": "以问号结尾的钩子（'本科毕业论文可以抄吗？'）",
        "列表型": "盘点 / 合集 / 分享 / 推荐 + 数量",
        "感悟型": "情绪短句 / 鸡汤金句 / 共感独白",
        "emoji型": "多 emoji 装饰 + 强情绪 + 简短信息密度",
    }.get(cat, cat)


if __name__ == "__main__":
    print(json.dumps(promote(), ensure_ascii=False, indent=2))
