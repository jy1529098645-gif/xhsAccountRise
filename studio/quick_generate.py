"""Quick-generate: one-shot post from a title.

Why this exists
---------------
The Composer pipeline is a 7-agent orchestration (Strategist → Researcher →
Drafter × N → Critic × N → Refiner → Synthesizer → Planner) that produces a
publish-ready bundle in 60-180 s. It's the right tool when the user wants the
full workflow.

But sometimes the user already has a title in mind, knows the platform, and
just wants ONE draft fast — no agent team, no strategy debate, no critique
loops. This module is that path:

  Inputs:  title + platform + voice_style + word_count + extra + model_spec
  Pulls:   the same `full_reference_block_for_prompt()` the drafter uses
           (user-uploaded reports + DNA consensus + integrated summary)
  Output:  single LLM call → {title, body, tags, cover_prompt}

Independence: this module imports only the LLM plumbing (Generator,
PromptBundle, registry) and the insight reference block. It does NOT touch
the agent pipeline, brief schema, project state, or job tracker — calling it
has zero side effects on Composer / Strategy / Drafts state.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Any

from .generators import registry
from .generators.base import GeneratedCandidate, PromptBundle


# Allowed model specs. We accept anything the registry.build() can parse —
# this list is just for the frontend dropdown / docs.
ALLOWED_MODEL_SPECS = (
    "claude:opus",
    "claude:sonnet",
    "claude:haiku",
    "deepseek",
    "deepseek:reasoner",
    # OpenAI lineup. Bare "openai" → env-configured default (gpt-5 in this
    # repo's .env.example). The :gpt-5* explicit variants pin a specific
    # model regardless of env.
    "openai",
    "openai:gpt-5",
    "openai:gpt-5-mini",
    "openai:gpt-5-nano",
    "openai:gpt-4o",
)


PLATFORM_LABELS = {
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "kuaishou": "快手",
    "bilibili": "B站",
    "youtube": "YouTube",
    "reddit": "Reddit",
    "x": "X / Twitter",
    "other": "通用",
}


# Predefined voice styles. "自定义" lets the user pass a free-form string in
# `voice_custom` — falls through and is appended as-is to the prompt.
VOICE_STYLES = {
    "口语自然": "口语化、第一人称、emoji 自然节奏；像朋友聊天，不端架子",
    "干货输出": "信息密度高、行动可执行、句式简洁，少废话多重点",
    "段子玩梗": "情绪化、emoji 高密度、自嘲反差、梗多 — 但梗要从用户上传报告 / 库的真实方言里学，不要硬塞「破防 / 蚌埠」这类通用学术党黑话",
    "走心叙事": "第一人称叙事、画面感强、情绪起伏明显；偶尔留白",
    "学术严谨": "理性陈述、术语准确、引用具体；避免口语化情绪词",
    "种草热情": "「闭眼入 / 真心推 / 平价宝藏」类语气、强烈个人立场、emoji 自然",
}


@dataclass
class QuickGenInput:
    title: str
    platform: str          # "xiaohongshu" | "douyin" | ...
    voice_style: str       # key from VOICE_STYLES, or "自定义"
    voice_custom: str = "" # used when voice_style == "自定义"
    target_length: int = 500
    extra: str = ""
    model_spec: str = "openai"   # default = cheap + decent

    def validate(self) -> str | None:
        if not self.title.strip():
            return "请填写标题"
        if not self.platform:
            return "请选择平台"
        if self.target_length < 80 or self.target_length > 4000:
            return "字数范围 80-4000"
        if self.model_spec not in ALLOWED_MODEL_SPECS:
            return f"不支持的模型 spec：{self.model_spec}（可选：{', '.join(ALLOWED_MODEL_SPECS)}）"
        return None


_SYSTEM = """\
你是一个社交媒体爆款写手。任务很简单 ：给定一个标题、平台、语气风格、字数、附加要求，
+ 用户已经上传 / 工具已经分析出的报告内容（强参考），直接输出一份可发布的完整帖子。

**关键约束**：

1. 严格基于用户上传的报告 / 工具的双 AI 共识 / 整合稿生成 ：
   - 报告里有的语气词 / 梗 / 案例 / 数字，要在文章里看到痕迹（不是照抄）
   - 报告里指出该平台爆款的开头 / 结构 / CTA 模板，要用上
   - 用户上传的报告比 system prompt 的通用建议更优先

2. 字数要求是硬指标，不是约莫：
   - 输出 body 字数必须 ≥ target_length × 0.9
   - 短了等于稿件没完成
   - 长了 10-20% 可以接受

3. 输出格式：纯 JSON 对象，键如下，**不要 markdown 围栏，不要前后任何说明文字**：
   {
     "title": "<最终标题（可微调用户给的标题以更上口），15-25 字>",
     "body": "<完整正文，按目标字数 + 平台风格写，分段分点>",
     "tags": ["<6-10 个 tag>"],
     "cover_prompt": "<英文，给生图工具的封面图描述>"
   }

4. 平台风格对照（如果 user 报告里有更具体的对照，以报告为准）：
   - 小红书 ：标题 hook 强、emoji 节奏自然、第一人称、CTA 软引导评论 / 收藏
   - 抖音 / 快手 ：脚本风、分镜可见、口播口吻、CTA 求点赞 / 关注
   - B站 ：标题信息量大、可带数字 / 时间锚、互动语气
   - YouTube / Reddit / X ：英文 / 中英混合 OK，节奏松一点
   - 通用 ：选一个最贴近用户报告里描述的风格

绝对不要 ：
- 学术八股、客套话、「希望本文对你有帮助」式收尾
- 编造产品名 / 链接 / 数字（除非用户标题或附加要求里给了）
- 跑题或改写用户给的主题方向 — 标题就是主线
"""


# JSON schema for tool_use / response_format. Kept simple — quick-generate
# doesn't need the predicted_likes / self_score that the drafter pool tracks.
_QUICK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "body", "tags", "cover_prompt"],
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "cover_prompt": {"type": "string"},
    },
}


def _voice_block(inp: QuickGenInput) -> str:
    """Compose the voice/style guidance block."""
    if inp.voice_style == "自定义" or inp.voice_style not in VOICE_STYLES:
        custom = (inp.voice_custom or inp.voice_style or "").strip()
        if custom:
            return f"语气风格：{custom}"
        return "语气风格：跟随平台默认风格（用户没指定）"
    return f"语气风格：{inp.voice_style} — {VOICE_STYLES[inp.voice_style]}"


def _build_user_message(inp: QuickGenInput, report_ctx: str) -> str:
    platform_label = PLATFORM_LABELS.get(inp.platform, inp.platform)
    parts = [
        f"【标题（这篇文章必须围绕这个写）】{inp.title}",
        f"【目标平台】{platform_label}（{inp.platform}）",
        _voice_block(inp),
        f"【目标正文字数】{inp.target_length} 字（必须达到这个数字 ±10%）",
    ]
    if (inp.extra or "").strip():
        parts.append(f"【附加要求】{inp.extra.strip()}")

    if report_ctx:
        parts.append(
            "\n【⭐⭐⭐ 用户上传的报告 / 工具的库分析（强参考 — 文章语气 / 梗 / 数据要从这里学）】\n"
            f"{report_ctx}"
        )
    else:
        parts.append(
            "\n【⚠️ 暂无用户报告 / 库分析数据】\n"
            "  没有上传报告也没有跑 DNA 分析。按平台通用风格写即可，但效果会显著弱于有报告时。"
        )

    parts.append(
        "\n按 system 给的 JSON schema 输出一份可发布的完整帖子。"
        f" body 字数必须 ≥ {int(inp.target_length * 0.9)} 字 — 短了不算完成。"
    )
    return "\n\n".join(parts)


# v0.66 (item7) ：快速生成多方向对比时，每个版本注入一个不同的方向倾向。
# 每个方向都**规定了标题句式**，从构造上强制 N 篇拉开差异 —— 否则各版本是
# 独立 LLM 调用、互相不知道，很容易收敛到同一个吸睛标题（实测翻车点）。
# 形状 ：(简短标签, 给 LLM 的强约束指令)。
QUICK_VARIANT_SPECS: tuple[tuple[str, str], ...] = (
    ("教程干货向",
     "写成「N 步 / N 个方法」清单式干货。标题必须是「X个步骤/方法/技巧」这种清单句式，"
     "正文按编号步骤展开，每步可直接执行。"),
    ("真实故事向",
     "写成第一人称真实经历叙事。标题必须是经历式（如「我…那天」「亲测…后」），"
     "**禁止**用数字清单式标题；正文有情绪起伏和画面感，结尾才落到方法。"),
    ("数据盘点向",
     "写成数据/盘点式。标题**必须以一个具体数字或百分比开头或包含**（如「92%→8%」「3个月」），"
     "正文用对比数字、前后差异、量化结果支撑。"),
    ("避坑警示向",
     "写成反面警示。标题必须是警示句式（如「别再…」「千万别…」「这样做会…」），"
     "正文先列踩过的坑/错误做法，再给正确姿势。"),
)
# 兼容旧引用 ：只取标签列表。
QUICK_VARIANT_ANGLES: tuple[str, ...] = tuple(label for label, _ in QUICK_VARIANT_SPECS)


@dataclass
class QuickGenResult:
    title: str
    body: str
    tags: list[str]
    cover_prompt: str
    model_used: str
    elapsed_s: float
    cost_estimate_usd: float
    error: str | None = None
    used_report_context: bool = False
    # v0.66 (item7) ：多方向对比时标注这是哪个方向（单篇生成时为空）。
    variant_label: str = ""
    # v0.65 (P2) ：让 quick_generate 也回 RAG provenance + grounding ，UI 一致显示。
    rag: dict[str, Any] | None = None
    grounding_score: float = 0.0
    grounding_breakdown: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "tags": self.tags,
            "cover_prompt": self.cover_prompt,
            "model_used": self.model_used,
            "elapsed_s": round(self.elapsed_s, 2),
            "cost_estimate_usd": round(self.cost_estimate_usd, 6),
            "error": self.error,
            "used_report_context": self.used_report_context,
            "rag": self.rag,
            "grounding_score": self.grounding_score,
            "grounding_breakdown": self.grounding_breakdown,
            "variant_label": self.variant_label,
        }


async def quick_generate(inp: QuickGenInput) -> QuickGenResult:
    """Run a single-LLM quick-generate call. No agent orchestration.

    Pulls report context via `full_reference_block_for_prompt()` and threads
    it through one LLM call. Returns a `QuickGenResult` with the post fields
    + cost/latency metadata. Errors are returned in the result, not raised —
    the caller decides whether to surface as 500 or as a partial response.
    """
    err = inp.validate()
    if err:
        return QuickGenResult(
            title="", body="", tags=[], cover_prompt="",
            model_used=inp.model_spec, elapsed_s=0.0, cost_estimate_usd=0.0,
            error=err,
        )

    # Lazy import to keep this module's import surface minimal.
    from .insight.pipeline import full_reference_block_for_prompt

    try:
        report_ctx = full_reference_block_for_prompt() or ""
    except Exception:
        report_ctx = ""

    # v0.65 (P2) ：quick-generate 也跑 RAG —— 之前完全没用 ，纯靠 system prompt
    # 里的 voice 指引 + report_ctx 自由发挥。现在按 title 拉 refs / comments /
    # hooks 喂进 prompt + 持久化到 studio_drafts.rag_json ，UI 可显示 provenance。
    from .composer.pipeline import (
        _retrieve_for_slot as _qg_retrieve,
        _format_refs_for_prompt as _qg_format_refs,
        _compute_grounding as _qg_grounding,
    )
    rag_payload = _qg_retrieve(inp.title, k_refs=6, n_comments=6)
    refs_block = _qg_format_refs(
        rag_payload.get("refs") or [],
        rag_payload.get("comments") or [],
        rag_payload.get("hooks") or [],
    )

    user_msg = _build_user_message(inp, report_ctx)
    if refs_block:
        user_msg += "\n\n" + refs_block

    # Resolve model spec → Generator. registry.build always returns a list;
    # for quick-generate we want exactly one model.
    try:
        gens = registry.build(inp.model_spec)
    except Exception as e:
        return QuickGenResult(
            title="", body="", tags=[], cover_prompt="",
            model_used=inp.model_spec, elapsed_s=0.0, cost_estimate_usd=0.0,
            error=f"模型解析失败：{e!r}",
        )
    if not gens:
        return QuickGenResult(
            title="", body="", tags=[], cover_prompt="",
            model_used=inp.model_spec, elapsed_s=0.0, cost_estimate_usd=0.0,
            error=f"模型 spec 无效：{inp.model_spec}",
        )
    gen = gens[0]

    # Dynamic max_tokens — matches the drafter's heuristic (target_length × 3
    # + 500 token JSON envelope), but min-clamped to 1500 since quick-gen
    # outputs are typically shorter than full drafts.
    dynamic_max_tokens = max(1500, inp.target_length * 3 + 500)
    bundle = PromptBundle(
        system=_SYSTEM,
        user=user_msg,
        expected_schema=_QUICK_SCHEMA,
        max_tokens=dynamic_max_tokens,
    )

    t0 = time.time()
    try:
        cand: GeneratedCandidate = await gen.generate(bundle)
    except Exception as e:
        return QuickGenResult(
            title="", body="", tags=[], cover_prompt="",
            model_used=gen.model, elapsed_s=time.time() - t0,
            cost_estimate_usd=0.0,
            error=f"LLM 调用失败：{e!r}",
            used_report_context=bool(report_ctx),
        )
    elapsed = time.time() - t0

    if cand.error:
        return QuickGenResult(
            title="", body="", tags=[], cover_prompt="",
            model_used=cand.llm, elapsed_s=elapsed,
            cost_estimate_usd=cand.cost_estimate_usd or 0.0,
            error=cand.error,
            used_report_context=bool(report_ctx),
        )

    # Drafter's generator returns CandidatePayload with the same fields we
    # want here. Pull what's available; tolerate missing fields (some models
    # under-populate cover_prompt / tags when not strictly required).
    payload = cand.payload
    body_text = str(getattr(payload, "body", "") or "").strip()

    # v0.65 (P4) ：算 grounding score = [ref:xxx] marker 数 + 蓝海词命中 ÷ 段落数。
    bo_keywords: list[str] = []
    try:
        from .composer.pipeline import _latest_dna_payload
        _dna = _latest_dna_payload()
        bo_keywords = [
            b.get("keyword") or ""
            for b in ((_dna.get("sections", {}).get("keyword_blueocean", {}) or {})
                      .get("rankings") or [])[:20]
            if (b.get("keyword") or "")
        ]
    except Exception:
        pass
    g_score, g_breakdown = _qg_grounding(body_text, rag_payload.get("refs") or [], bo_keywords)

    # v0.65 (P2) ：持久化进 studio_drafts ，UI 走 DraftDetail ProvenancePanel 路径。
    try:
        _persist_quick_generate_draft(
            inp=inp, body=body_text,
            title=str(getattr(payload, "title", "") or inp.title).strip(),
            tags=[str(t) for t in (getattr(payload, "tags", None) or [])],
            cover_prompt=str(getattr(payload, "cover_prompt", "") or "").strip(),
            model_used=cand.llm, elapsed_s=elapsed,
            cost_estimate_usd=cand.cost_estimate_usd or 0.0,
            rag_payload=rag_payload,
            grounding_score=g_score, grounding_breakdown=g_breakdown,
        )
    except Exception:
        pass  # persistence 失败不阻塞返回结果

    return QuickGenResult(
        title=str(getattr(payload, "title", "") or inp.title).strip(),
        body=body_text,
        tags=[str(t) for t in (getattr(payload, "tags", None) or [])],
        cover_prompt=str(getattr(payload, "cover_prompt", "") or "").strip(),
        model_used=cand.llm,
        elapsed_s=elapsed,
        cost_estimate_usd=cand.cost_estimate_usd or 0.0,
        error=None,
        used_report_context=bool(report_ctx),
        rag=rag_payload,
        grounding_score=g_score,
        grounding_breakdown=g_breakdown,
    )


async def quick_generate_multi(
    inp: QuickGenInput, variants: int = 2,
) -> list[QuickGenResult]:
    """v0.66 (item7) ：一次产出 N 个不同方向/主题倾向的版本，供横向对比。

    复用 quick_generate ：为每个版本在 extra 里注入一个不同的方向倾向
    （教程 / 故事 / 数据 / 避坑），让 N 篇在角度和结构上明显拉开差异。
    并发跑，返回带 variant_label 的结果列表。variants=1 时退化为单篇。
    """
    variants = max(1, min(len(QUICK_VARIANT_SPECS), int(variants or 1)))
    if variants == 1:
        return [await quick_generate(inp)]

    specs = list(QUICK_VARIANT_SPECS[:variants])

    async def _one(label: str, directive: str) -> QuickGenResult:
        extra = (inp.extra or "").strip()
        sub_extra = (
            f"{extra}\n" if extra else ""
        ) + (
            f"【本版方向 ：{label}】{directive}\n"
            f"这是同一主题的 {variants} 个对比版本之一，你这一版必须严格守住上面的标题句式和结构，"
            f"和其它方向明显区分 —— 标题尤其不能和其它版本撞。"
        )
        sub = replace(inp, extra=sub_extra.strip())
        res = await quick_generate(sub)
        res.variant_label = label
        return res

    return await asyncio.gather(*[_one(lbl, d) for lbl, d in specs])


def _persist_quick_generate_draft(
    *, inp: QuickGenInput, body: str, title: str, tags: list[str], cover_prompt: str,
    model_used: str, elapsed_s: float, cost_estimate_usd: float,
    rag_payload: dict[str, Any],
    grounding_score: float, grounding_breakdown: dict[str, Any],
) -> str:
    """v0.65 (P2) ：把 quick_generate 结果写进 studio_drafts + studio_draft_candidates ，
    让它跟 compose 路径共享 DraftDetail ProvenancePanel + 历史列表。返回 draft_id。"""
    import json
    import uuid as _uuid
    from . import db, library, project

    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    lib_id = library.active_lib_id()
    draft_id = _uuid.uuid4().hex[:16]
    cand_id = _uuid.uuid4().hex[:16]
    now = int(time.time())

    brief_json = json.dumps({
        "topic": inp.title,
        "platform": inp.platform,
        "target_length": inp.target_length,
        "voice_style": inp.voice_style,
        "voice_custom": inp.voice_custom,
        "extra_constraints": inp.extra,
        "angle": "教程",
        "angles": [],
        "cta_strength": "soft",
        "niche": "",
        "reference_note_ids": [],
        "_source": "quick_generate",
    }, ensure_ascii=False)

    notes_payload = {
        "source": "quick_generate",
        "model_used": model_used,
        "grounding_score": grounding_score,
        "grounding_breakdown": grounding_breakdown,
    }

    meta_payload = {
        "latency_ms": int(elapsed_s * 1000),
        "cost_estimate_usd": float(cost_estimate_usd),
        "source": "quick_generate",
        # v0.65 (P4) ：grounding 也要进 candidate.meta_json ，否则 Drafts list
        # 的 grounding 列读不到（list 走 candidate.meta_json，不读 draft.notes）。
        "grounding_score": grounding_score,
        "grounding_breakdown": grounding_breakdown,
    }
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_drafts"
            " (draft_id, generated_at, prompt_version, brief_json, status,"
            "  mode, library_id, final_candidate_id, notes, project_id,"
            "  rag_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                draft_id, now, "quick_generate.v1", brief_json,
                "generated", "quick-generate", lib_id, cand_id,
                json.dumps(notes_payload, ensure_ascii=False), pid,
                json.dumps(rag_payload, ensure_ascii=False),
            ),
        )
        con.execute(
            "INSERT INTO studio_draft_candidates"
            " (candidate_id, draft_id, llm, title, body, tags_json,"
            "  cover_prompt, hook_type, predicted_likes, self_score,"
            "  self_critique, meta_json, human_score, chosen, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cand_id, draft_id, model_used, title, body,
                json.dumps(tags, ensure_ascii=False), cover_prompt,
                "", 0, 0.0, "",
                json.dumps(meta_payload, ensure_ascii=False),
                None, 1, now,
            ),
        )
    return draft_id
