"""Autofill the starter AccountInput by debating Claude × OpenAI over the
library's DNA.

Phase 1 (parallel):
    - Claude Opus  → independent proposal (positioning / audience / cycle /
      frequency / personal_strengths_template / rationale)
    - OpenAI GPT-4o → independent proposal

Phase 2 (moderator, Claude Opus):
    - Reads both proposals + rationales
    - Produces a *consensus* AccountInput where each field comes from
      whichever side has stronger DNA evidence (or a synthesised middle)
    - For each field, returns:
        - value (the consensus pick)
        - rationale (one-line WHY, anchored in DNA data)
        - alternatives (alternative picks the user might prefer)

The user sees these pre-filled in the Strategy form with a little 💡 chip
showing the rationale, and can edit any field freely. This is exactly the
"AI 先拟一版，用户再改" pattern the user asked for.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .. import db, library, project
from ..generators import registry
from .pipeline import _call_json, _latest_dna_payload
from . import prompts as strat_prompts


_INDEPENDENT_SYSTEM = """\
你是「账号起号策略师」。基于该平台的爆款数据 (DNA)，**为这个用户**拟一版起号初稿。

注意：是给一个**新人**的初稿，不是给老账号优化。所以你的提议要：
- 锚定 DNA 数据里能拿到的真实信号（蓝海词 / 用户原话 / 高表现 hook）
- 平台可行（一般用户的更新频率 + 周期都要现实）
- 给出 2-3 个**备选**让用户挑（比如 cycle 给 4 周也给 8 周）

输出 JSON：
{
  "positioning": "<8-40 字的账号一句话定位>",
  "positioning_alts": ["<备选 1>", "<备选 2>"],
  "target_audience": "<精确受众>",
  "target_audience_alts": ["<备选受众>"],
  "cycle_weeks": <2|4|8|12>,
  "cycle_alts": [<其它合理周期>],
  "posts_per_week": <1|2|3|5|7>,
  "posts_alts": [<其它频率>],
  "personal_strengths_template": "<给用户的填写提示，例：'比如：985 在读 / 已经用 ChatGPT 写过 5 篇论文 / 有真实降重案例可分享'>",
  "constraints_template": "<给用户的填写提示>",
  "rationale": {
    "positioning_why": "<2-3 句话，引用 DNA 具体数据>",
    "audience_why": "<引用 DNA 数据>",
    "cycle_why": "<为啥这个周期>",
    "frequency_why": "<为啥这个频率，考虑可持续性>"
  }
}

要求每个 rationale 都引用 DNA 里具体能查到的数字（蓝海排名、avg_likes、hook 分布百分比、用户高频询问等）。
不要泛泛而谈。
"""


_MODERATOR_SYSTEM = """\
你是「编辑主审」。两位 AI 分析师各自基于同一份 DNA 数据，给同一个用户拟了起号初稿。

你的任务：综合两版本，给用户一个**最终初稿** + 备选项。

规则：
- 双方相近或一致的字段：直接采纳（标 'consensus': true）
- 双方分歧的字段：选证据更强的那一版（标 source: 'claude'|'openai'|'merged'）并把另一方的版本放进 alternatives
- positioning / audience 这两个核心字段，如果差异大就直接 merge 出一个新的（融合两家长处）

输出 JSON：
{
  "input": {
    "positioning": "...",
    "target_audience": "...",
    "cycle_weeks": <num>,
    "posts_per_week": <num>,
    "personal_strengths": "<用户提示文案，可空>",
    "constraints": "<用户提示文案，可空>",
    "platform": "<继承>"
  },
  "field_rationale": {
    "positioning":      {"source": "claude|openai|merged|consensus", "rationale": "...", "alternatives": ["..."]},
    "target_audience":  {"source": "...", "rationale": "...", "alternatives": ["..."]},
    "cycle_weeks":      {"source": "...", "rationale": "...", "alternatives": [<num>, <num>]},
    "posts_per_week":   {"source": "...", "rationale": "...", "alternatives": [<num>]}
  },
  "consensus_notes": [
    "<两家 AI 都强调的几条 DNA 信号>"
  ],
  "single_side_views": [
    {"side": "claude|openai", "field": "...", "point": "...", "note": "为什么没进 final"}
  ]
}

要求：rationale 必须保留对 DNA 具体数据的引用。"""


_PROPOSAL_SCHEMA = {
    "type": "object",
    "required": ["positioning", "target_audience", "cycle_weeks",
                 "posts_per_week", "rationale"],
    "properties": {
        "positioning": {"type": "string"},
        "positioning_alts": {"type": "array", "items": {"type": "string"}},
        "target_audience": {"type": "string"},
        "target_audience_alts": {"type": "array", "items": {"type": "string"}},
        "cycle_weeks": {"type": "integer"},
        "cycle_alts": {"type": "array", "items": {"type": "integer"}},
        "posts_per_week": {"type": "integer"},
        "posts_alts": {"type": "array", "items": {"type": "integer"}},
        "personal_strengths_template": {"type": "string"},
        "constraints_template": {"type": "string"},
        "rationale": {
            "type": "object",
            "properties": {
                "positioning_why": {"type": "string"},
                "audience_why": {"type": "string"},
                "cycle_why": {"type": "string"},
                "frequency_why": {"type": "string"},
            },
        },
    },
}


_CONSENSUS_SCHEMA = {
    "type": "object",
    "required": ["input", "field_rationale"],
    "properties": {
        "input": {
            "type": "object",
            "properties": {
                "positioning": {"type": "string"},
                "target_audience": {"type": "string"},
                "cycle_weeks": {"type": "integer"},
                "posts_per_week": {"type": "integer"},
                "personal_strengths": {"type": "string"},
                "constraints": {"type": "string"},
                "platform": {"type": "string"},
            },
        },
        "field_rationale": {"type": "object"},
        "consensus_notes": {"type": "array", "items": {"type": "string"}},
        "single_side_views": {"type": "array", "items": {"type": "object"}},
    },
}


async def autofill(
    personal_hint: str = "",
    constraints_hint: str = "",
    claude_spec: str = "claude:opus",
    openai_spec: str = "openai",
    moderator_spec: str = "claude:opus",
) -> dict[str, Any]:
    """Run the 2-LLM debate and return a starter AccountInput + rationale."""
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()

    dna = _latest_dna_payload()
    lib_meta = library.get_meta(library.active_lib_id())
    platform = lib_meta.platform if lib_meta else "xiaohongshu"

    if not dna:
        # Don't hard-fail — build an ad-hoc artifact so we always have *something*
        from .. import adapt as _adapt
        try:
            db_path = library.current_db_path()
            raw = _adapt.inspect_source(
                db_path, sample_rows=10, include_top_rows=True, include_aggregates=True,
            ) if db_path.exists() else {}
        except Exception:
            raw = {}
        dna = {"version": "ad-hoc", "sections": {}, "summary": {},
               "raw_schema": raw}

    t0 = time.time()
    dna_context = strat_prompts.dna_blurb(dna)

    # Include latest insight report (Claude × OpenAI consensus) as 强参考
    from ..insight.pipeline import latest_completed_for_current_library, consensus_summary_for_prompt
    report_ctx = consensus_summary_for_prompt(latest_completed_for_current_library())

    report_block = f"\n\n{report_ctx}\n" if report_ctx else ""

    user_msg = (
        f"【激活的语料库】 lib_id={lib_meta.lib_id if lib_meta else 'unknown'},"
        f" platform={platform}, notes={lib_meta.notes_count if lib_meta else 0}\n"
        f"{report_block}"
        f"【该平台爆款 DNA】\n{dna_context}\n\n"
        f"【用户可选提示】\n"
        f"  - 个人优势 (用户可能填这个): {personal_hint or '未填'}\n"
        f"  - 偏好约束 (用户可能填这个): {constraints_hint or '未填'}\n\n"
        "请按 system 给的 schema 拟一版起号初稿。"
        + (" **务必结合上面的「共识分析报告」内容**——它是上一步双 AI 协作的产物，权重很高。"
           if report_ctx else "")
    )

    # ---- Phase 1: parallel proposals ----
    claude_gen = registry.build(claude_spec)[0]
    openai_gen = registry.build(openai_spec)[0]

    async def propose(gen):
        try:
            return await _call_json(
                gen, _INDEPENDENT_SYSTEM, user_msg,
                max_tokens=2500,
                tool_name="submit_starter", schema=_PROPOSAL_SCHEMA,
            )
        except Exception as e:
            return {"_error": f"{gen.model}: {e!r}"}

    claude_p, openai_p = await asyncio.gather(propose(claude_gen), propose(openai_gen))

    # If either failed, fall back to the surviving one alone.
    if "_error" in claude_p and "_error" in openai_p:
        raise RuntimeError(
            f"both proposers failed.\nClaude: {claude_p['_error']}\nOpenAI: {openai_p['_error']}"
        )

    # ---- Phase 2: moderator ----
    mod_gen = registry.build(moderator_spec)[0]
    mod_user = (
        f"【该平台爆款 DNA 摘要】\n{dna_context}\n\n"
        f"【Claude 的起号初稿提议】\n{json.dumps(claude_p, ensure_ascii=False, indent=2)}\n\n"
        f"【OpenAI 的起号初稿提议】\n{json.dumps(openai_p, ensure_ascii=False, indent=2)}\n\n"
        f"【平台】{platform}（继承到 input.platform）\n\n"
        "请按 system 给的 schema 输出共识初稿。"
    )
    try:
        consensus = await _call_json(
            mod_gen, _MODERATOR_SYSTEM, mod_user,
            max_tokens=3000,
            tool_name="submit_consensus_starter", schema=_CONSENSUS_SCHEMA,
        )
    except Exception as e:
        # Last-resort fallback: pick whichever proposer succeeded.
        winner = claude_p if "_error" not in claude_p else openai_p
        consensus = {
            "input": {
                "positioning": winner.get("positioning", ""),
                "target_audience": winner.get("target_audience", ""),
                "cycle_weeks": int(winner.get("cycle_weeks") or 4),
                "posts_per_week": int(winner.get("posts_per_week") or 3),
                "personal_strengths": personal_hint or "",
                "constraints": constraints_hint or "",
                "platform": platform,
            },
            "field_rationale": {},
            "consensus_notes": [f"moderator failed: {e!r} — fell back to single-side"],
            "single_side_views": [],
        }

    # Ensure platform is set + carry user hints into input
    inp = consensus.setdefault("input", {})
    inp.setdefault("platform", platform)
    if personal_hint and not inp.get("personal_strengths"):
        inp["personal_strengths"] = personal_hint
    if constraints_hint and not inp.get("constraints"):
        inp["constraints"] = constraints_hint

    # Coerce numeric types (Claude may return as strings sometimes).
    try: inp["cycle_weeks"] = int(inp.get("cycle_weeks") or 4)
    except (TypeError, ValueError): inp["cycle_weeks"] = 4
    try: inp["posts_per_week"] = int(inp.get("posts_per_week") or 3)
    except (TypeError, ValueError): inp["posts_per_week"] = 3

    return {
        "input": inp,
        "field_rationale": consensus.get("field_rationale") or {},
        "consensus_notes": consensus.get("consensus_notes") or [],
        "single_side_views": consensus.get("single_side_views") or [],
        "claude_proposal": claude_p,
        "openai_proposal": openai_p,
        "elapsed_s": int(time.time() - t0),
    }
