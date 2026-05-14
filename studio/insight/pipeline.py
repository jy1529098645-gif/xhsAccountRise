"""Library analysis report pipeline (Claude × OpenAI debate → consensus).

Flow:
    Phase 1 (parallel, independent analyses):
        - Claude Opus reads DNA artifact + library stats → analysis A
        - OpenAI gpt-4o  reads DNA artifact + library stats → analysis B
    Phase 2 (parallel critique):
        - Claude sees B and produces {agrees, disagrees, extends}
        - OpenAI sees A and produces {agrees, disagrees, extends}
    Phase 3 (moderator synthesis):
        - Claude Opus reads everything and outputs the consensus report
          (only keeps points both LLMs accept; disagreements marked separately)

The final report includes references to specific DNA data points so the
frontend can render the corresponding charts inline.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from .. import db, library, project
from ..generators import registry
from ..generators.base import Generator


# ---- DNA context ---------------------------------------------------------

def _build_dna_context(dna: dict[str, Any]) -> str:
    """Compact, prompt-friendly summary of the DNA artifact.

    Tolerates a completely empty artifact — falls back to dumping the raw
    source schema + sample rows so the AIs still have *something* to say
    about a library that's totally non-xhs-shaped.
    """
    if not dna:
        return "（暂无 DNA 数据，请尽量根据其他线索分析）"
    s = dna.get("sections", {}) or {}
    summary = dna.get("summary", {}) or {}
    bo = (s.get("keyword_blueocean", {}) or {}).get("rankings", [])[:15]
    hook_dist = (s.get("titles", {}) or {}).get("primary_distribution", {})
    top_titles = (s.get("titles", {}) or {}).get("top_titles", [])[:15]
    body_buckets = (s.get("body_and_shape", {}) or {}).get("by_body_length", {})
    timing = (s.get("timing", {}) or {}).get("heatmap", [])
    tags = (s.get("tags", {}) or {}).get("top_tags", [])[:20]
    cd = (s.get("comment_demand", {}) or {}).get("by_pattern", {})
    top_perf = s.get("top_performers", {}) or {}

    def t(x: str, n: int = 90) -> str:
        x = (x or "").replace("\n", " ")
        return x[:n] + ("…" if len(x) > n else "")

    parts: list[str] = []
    parts.append(f"【DNA 版本】v{dna.get('version', '?')}, 分析 {summary.get('total_notes_analysed', 0)} 篇笔记")

    parts.append("【蓝海关键词 top 15】\n" + "\n".join(
        f"  · #{i+1} {b['keyword']}: n={b['note_count']}, avg_likes={int(b['avg_likes'])}, p90={int(b['p90_likes'])}, score={b['blue_ocean_score']:.0f}"
        for i, b in enumerate(bo)
    ))

    parts.append("【hook 类型分布 (主导)】\n" + "\n".join(
        f"  · {k}: {v}" for k, v in sorted(hook_dist.items(), key=lambda kv: -kv[1])[:10]
    ))

    parts.append("【Top 标题样本】\n" + "\n".join(
        f"  · [{x.get('liked', 0)}] {t(x.get('title', ''))}" for x in top_titles
    ))

    if body_buckets:
        parts.append("【字数 vs 互动】\n" + "\n".join(
            f"  · {k}: n={v.get('count', 0)}, median_likes={int((v.get('likes') or {}).get('median', 0))}"
            for k, v in body_buckets.items()
        ))

    # Best time slots
    if timing:
        valid = [c for c in timing if c.get("count", 0) >= 3]
        if valid:
            valid.sort(key=lambda c: c.get("median_likes", 0), reverse=True)
            dow = ["一", "二", "三", "四", "五", "六", "日"]
            parts.append("【高互动发布时段 top 8】\n" + "\n".join(
                f"  · 周{dow[c['dow']]} {c['hour']:02d}:00 — median {int(c['median_likes'])} (n={c['count']})"
                for c in valid[:8]
            ))

    if tags:
        parts.append("【高表现 tags】\n" + "\n".join(
            f"  · {tg['tag']} (n={tg['count']}, avg_likes={int(tg['avg_likes'])})"
            for tg in tags[:15]
        ))

    if cd:
        lines = []
        for label, items in cd.items():
            if not items:
                continue
            sample = "、".join(t((it.get("phrase") or ""), 22) for it in items[:5])
            lines.append(f"  · 「{label}」: {sample}")
        if lines:
            parts.append("【用户高频询问 (评论挖掘)】\n" + "\n".join(lines))

    if top_perf.get("top_collect_rate"):
        parts.append("【高收藏率笔记 (干货信号)】\n" + "\n".join(
            f"  · rate={x.get('collect_rate', 0):.2f}: {t(x.get('title', ''))}"
            for x in top_perf["top_collect_rate"][:8]
        ))

    # ---- Raw-content snapshot ------------------------------------------
    # Show schema + a handful of top-performing samples per table. We used
    # to dump 20+ rows per table with full JSON; that ballooned every prompt
    # to 60-70 KB / 20k+ input tokens and was the #1 cause of insight calls
    # taking 250s. Now ~3-5k chars total.
    raw = dna.get("raw_schema") or {}
    raw_tables = raw.get("tables") or []
    if raw_tables:
        sch_lines: list[str] = []
        # Only the top 3 tables (was 6) — most libs only have 1-2 with notes.
        for tbl in raw_tables[:3]:
            cols = ", ".join(
                f"{c['name']}({c.get('type','')})"
                for c in (tbl.get("columns") or [])[:12]   # was 20
            )
            header = f"━━━ 表 `{tbl['name']}` ({tbl.get('row_count', 0)} 行)"
            if tbl.get("engagement_col"):
                header += f" · 按 `{tbl['engagement_col']}` 排序"
            sch_lines.append(header)
            sch_lines.append(f"  columns: {cols}")

            aggs = tbl.get("aggregates") or {}
            if aggs:
                key_stats = []
                for cn, a in list(aggs.items())[:5]:   # was 8
                    if "avg" in a:
                        key_stats.append(f"{cn}: avg={a.get('avg')} max={a.get('max')}")
                    else:
                        key_stats.append(f"{cn}: distinct={a.get('distinct')}")
                if key_stats:
                    sch_lines.append("  stats: " + " · ".join(key_stats))

            # Only top 5 rows (was unbounded). Trim each row's values to 200
            # chars so a single 8000-char title field doesn't explode prompt.
            def _trim_row(row: dict) -> dict:
                out = {}
                for k, v in row.items():
                    if isinstance(v, str) and len(v) > 200:
                        out[k] = v[:200] + "…"
                    else:
                        out[k] = v
                return out

            top = (tbl.get("top_rows") or [])[:5]   # was unbounded
            if top:
                sch_lines.append(f"  ── top {len(top)} 行 ──")
                for i, r in enumerate(top, 1):
                    sch_lines.append(f"    [#{i}] " + json.dumps(_trim_row(r), ensure_ascii=False))
            # Skip the random-sample block entirely — top_rows is enough
            # signal, and it was the bulk of the 70 KB context.
        parts.append("【原始数据快照（真实数据，请直接看这里下结论）】\n"
                     + "\n".join(sch_lines))

    return "\n\n".join(parts) or "（库里几乎没有可分析的内容，请基于 schema 推测）"


# ---- Prompts ------------------------------------------------------------

INDEPENDENT_SYSTEM = """\
你是「起号策略分析师」。用户给你扔了一个数据库（不限格式：可能是小红书笔记 / 抖音视频 / B站动态 / 任何社交平台爬下来的，也可能完全是别的东西）。

你的任务**不是**做数据健康度评估，**而是**：站在「这个用户要拿这堆数据做起号」的角度，**写一份起号分析报告**。

**怎么读数据**：
- 你拿到的资料里有「原始数据快照」——真实的表 / 列 / 样本行 / top 行。**直接读这些内容**，就像 ChatGPT 接到文件直接读一样。
- 不要先纠结字段对不对齐、有没有缺数据、是不是标准 schema。**有什么读什么**。
- top_rows 是按互动量排过序的真实爆款 — 重点看这些。

**报告要聚焦「起号」**：
- 这个领域 / 赛道当下有什么内容能爆？
- 目标受众是谁、痛点是什么？
- 用户拿这个数据能起一个什么定位的号？
- 哪些 hook / 标题模式有效？
- 起号过程要规避什么？
- 第一篇该写什么、第一周该铺什么节奏？

如果数据真的什么都没有，简单说一句「这数据库无法支撑起号分析」即可，不要假装有洞察。

绝不参考任何其他 AI 的观点（你现在是独立分析阶段）。输出 JSON：

{
  "executive_summary": "<3-5 句话起号判断：这是什么赛道、起号机会有多大、怎么切入>",
  "launch_mode": {
    "recommendation": "cold_start" | "hot_start" | "hybrid",
    "rationale": "<为什么这么建议，引用数据>",
    "first_week_plan": "<第一周该做什么>"
  },
  "key_findings": [
    {"title": "<发现名（针对起号有意义的）>", "evidence": "<具体引用样本行/数字>", "implication": "<对起号的意味>"}
  ],
  "content_opportunities": [
    {"opportunity": "<起号内容方向>", "why": "<数据信号>", "suggested_angle": "<具体切入方式>"}
  ],
  "audience_insight": "<目标受众 + 痛点 + 起号该说人话还是行话>",
  "risks_and_blind_spots": [
    "<起号最容易翻车的点，引用数据>"
  ],
  "recommended_next_steps": [
    "<起号执行下一步>"
  ]
}

【launch_mode 怎么判断】
- "cold_start"（冷启动）= 先用 3-7 篇低门槛、纯垂直、不强转化的内容养号子，让平台知道账号是什么标签的，再发主线内容。**当库里数据显示赛道竞争激烈 / 同质化严重 / 算法对新号严苛 / 用户口味难捉摸时选这个**。
- "hot_start"（硬启动 / 热启动）= 第一篇直接发最有把握的爆款角度，不养号，靠内容力直接撕开流量。**当库里有清晰的蓝海词 / 用户问题密集 / 你手里有现成强差异化素材时选这个**。
- "hybrid" = 前 2 篇冷启动建标签，第 3 篇起直接打爆款角度。**当信号矛盾 / 中等难度赛道时选这个**。
你必须从数据里给出明确的判断 + 第一周的执行节奏。

要求：
- key_findings 至少 3 条，evidence 必须钉到具体样本行 / 数字
- content_opportunities 至少 3 条，每条要有"怎么切入"
- 不要泛泛而谈
"""


CRITIQUE_SYSTEM = """\
你看到了另一位 AI 分析师对同一份 DNA 数据出的报告（在 user 消息里）。请你独立判断：

1. 哪些观点你**赞成**（写明你赞成的原因，最好独立举证）
2. 哪些观点你**不赞成**（说明原因 + 你的反驳证据）
3. 哪些重要点对方**漏掉了**

输出 JSON：
{
  "agreements": [
    {"point": "<赞成的对方观点>", "your_reason": "<你独立的理由>"}
  ],
  "disagreements": [
    {"opposed_point": "<不赞成的对方观点>", "your_objection": "<反驳>"}
  ],
  "missing_points": [
    {"point": "<对方漏掉的洞察>", "evidence": "<DNA 数据支撑>"}
  ]
}

要严格但公允，不要为了反对而反对。
"""


MODERATOR_SYSTEM = """\
你是「起号报告主编」。你拿到的材料：

1. Claude 对数据库的独立起号分析
2. OpenAI 对数据库的独立起号分析
3. Claude 对 OpenAI 报告的赞成/反对/补充
4. OpenAI 对 Claude 报告的赞成/反对/补充

请融合两家的输出，输出一份**给用户的起号报告**：**只保留双方都认可的观点**作为主报告主体，**分歧 / 单方观点**单独列出来标明出处。

输出 JSON：

{
  "title": "<报告标题，简短>",
  "executive_summary": "<3-5 句双方都认同的核心结论>",
  "launch_mode": {
    "recommendation": "cold_start" | "hot_start" | "hybrid",
    "rationale": "<为什么这么建议（综合双方意见）>",
    "first_week_plan": "<第一周该做什么>",
    "agreement_level": "both_agree" | "leaned" | "split"
  },
  "consensus_findings": [
    {"title": "...", "evidence": "...", "implication": "..."}
  ],
  "consensus_opportunities": [
    {"opportunity": "...", "why": "...", "suggested_angle": "..."}
  ],
  "consensus_risks": ["..."],
  "consensus_next_steps": ["..."],
  "single_side_views": [
    {"side": "claude" | "openai", "point": "...", "note": "<为什么没进共识区>"}
  ],
  "charts_to_show": [
    "blue_ocean_top15" | "hook_distribution" | "timing_heatmap" |
    "top_tags" | "body_length" | "top_titles" | "comment_demand"
  ]
}

【launch_mode 的处理】
- 看双方的 launch_mode.recommendation：
  - 两边一致 → agreement_level = "both_agree"，直接用该建议
  - 一边 hybrid / 一边 cold_start 或 hot_start → "leaned"，倾向那个明确的
  - 一边 cold 一边 hot → "split"，给出 "hybrid" 作为折中
- rationale 要融合双方理由，first_week_plan 要具体到「第 1 天发什么 / 第 4 天发什么」级别

要求：
- consensus_findings 至少 3 条，每条都得是双方都认同的
- single_side_views 至少要标 2 条（保留分歧的价值）
- charts_to_show 列出该报告里值得用图表呈现的部分，从给定枚举里挑
"""


# ---- LLM dispatch helpers ----------------------------------------------

# All LLM JSON calls now go through the shared utility with OpenAI fallback.
from ..llm_call import call_for_json as _call_json  # noqa: E402


# Permissive JSON schemas — we only force *shape*, not enumeration, because
# strict enum on launch_mode.recommendation makes Claude refuse on edge cases.
_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "executive_summary": {"type": "string"},
        "launch_mode": {
            "type": "object",
            "properties": {
                "recommendation": {"type": "string"},
                "rationale": {"type": "string"},
                "first_week_plan": {"type": "string"},
            },
        },
        "key_findings": {"type": "array", "items": {"type": "object"}},
        "content_opportunities": {"type": "array", "items": {"type": "object"}},
        "audience_insight": {"type": "string"},
        "risks_and_blind_spots": {"type": "array", "items": {"type": "string"}},
        "recommended_next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["executive_summary", "key_findings"],
}

_CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {
        "agreements": {"type": "array", "items": {"type": "object"}},
        "disagreements": {"type": "array", "items": {"type": "object"}},
        "missing_points": {"type": "array", "items": {"type": "object"}},
    },
}

_CONSENSUS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "executive_summary": {"type": "string"},
        "launch_mode": {"type": "object"},
        "consensus_findings": {"type": "array", "items": {"type": "object"}},
        "consensus_opportunities": {"type": "array", "items": {"type": "object"}},
        "consensus_risks": {"type": "array", "items": {"type": "string"}},
        "consensus_next_steps": {"type": "array", "items": {"type": "string"}},
        "single_side_views": {"type": "array", "items": {"type": "object"}},
        "charts_to_show": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["consensus_findings"],
}


# ---- Pipeline orchestrator ---------------------------------------------

async def run(library_id: str, *,
              claude_spec: str = "claude:opus",
              openai_spec: str = "openai",
              moderator_spec: str = "claude:opus") -> dict[str, Any]:
    """End-to-end run. Persists into studio_insight_reports and returns the
    full record."""
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()

    # Sanity-check the library exists.
    meta = library.get_meta(library_id)
    if meta is None:
        raise LookupError(f"library not found: {library_id}")

    # Load latest DNA artifact for this active library context.
    # (Caller is expected to switch to the lib before calling, or to rely
    #  on the global latest artifact.)
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT payload_json FROM studio_dna_artifacts"
            " ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    dna = json.loads(row["payload_json"]) if row else {}
    if not dna:
        # Build a minimal artifact-shaped dict from raw schema so the LLMs
        # always have something to analyse. We don't *persist* this — it
        # just keeps the pipeline running for the user's UI.
        try:
            from .. import adapt as _adapt, library as _library
            raw = _adapt.inspect_source(_library.current_db_path(), sample_rows=2)
        except Exception:
            raw = {}
        dna = {
            "version": "ad-hoc",
            "sections": {},
            "summary": {"total_notes_analysed": 0, "dominant_hooks": []},
            "raw_schema": raw,
            "section_errors": {"all": "no canonical analysis available — pure schema-based insight"},
        }

    context = _build_dna_context(dna)
    t0 = time.time()
    report_id = uuid.uuid4().hex[:16]
    pid = project.active_project_id()
    now = int(time.time())

    # Pre-insert pending row so the frontend can poll if streaming later.
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_insight_reports"
            " (report_id, project_id, library_id, created_at, status)"
            " VALUES (?, ?, ?, ?, 'pending')",
            (report_id, pid, library_id, now),
        )

    try:
        # ---- Phase 1: independent analyses in parallel ----
        claude_gen = registry.build(claude_spec)[0]
        openai_gen = registry.build(openai_spec)[0]
        analysis_user = f"【该平台爆款数据 (DNA)】\n{context}\n\n请按 system 给的 schema 独立分析。"

        async def analyze(gen: Generator) -> dict[str, Any]:
            try:
                return await _call_json(
                    gen, INDEPENDENT_SYSTEM, analysis_user,
                    max_tokens=5000,
                    tool_name="submit_launch_analysis",
                    schema=_ANALYSIS_SCHEMA,
                )
            except Exception as e:
                return {"_error": f"{gen.model}: {e!r}"}

        claude_a, openai_a = await asyncio.gather(analyze(claude_gen), analyze(openai_gen))

        # ---- Phase 2: cross-critique in parallel ----
        async def critique(gen: Generator, other_output: dict[str, Any]) -> dict[str, Any]:
            other_blob = json.dumps(other_output, ensure_ascii=False, indent=2)
            user = (
                f"【该平台爆款数据 (DNA)】\n{context}\n\n"
                f"【对方 AI ({'OpenAI' if gen.name == 'claude' else 'Claude'}) 出的报告】\n{other_blob}\n\n"
                "请按 system 给的 schema 输出你的赞成/反对/补充。"
            )
            try:
                return await _call_json(
                    gen, CRITIQUE_SYSTEM, user,
                    max_tokens=3500,
                    tool_name="submit_critique",
                    schema=_CRITIQUE_SCHEMA,
                )
            except Exception as e:
                return {"_error": f"{gen.model}: {e!r}"}

        claude_crit_task = critique(claude_gen, openai_a)
        openai_crit_task = critique(openai_gen, claude_a)
        claude_crit, openai_crit = await asyncio.gather(claude_crit_task, openai_crit_task)

        debate = {
            "claude_critique_of_openai": claude_crit,
            "openai_critique_of_claude": openai_crit,
        }

        # ---- Phase 3: moderator synthesis ----
        moderator_gen = registry.build(moderator_spec)[0]
        moderator_user = (
            f"【DNA 摘要】\n{context}\n\n"
            f"【Claude 独立分析】\n{json.dumps(claude_a, ensure_ascii=False, indent=2)}\n\n"
            f"【OpenAI 独立分析】\n{json.dumps(openai_a, ensure_ascii=False, indent=2)}\n\n"
            f"【Claude 对 OpenAI 的赞成/反对/补充】\n{json.dumps(claude_crit, ensure_ascii=False, indent=2)}\n\n"
            f"【OpenAI 对 Claude 的赞成/反对/补充】\n{json.dumps(openai_crit, ensure_ascii=False, indent=2)}\n\n"
            "请按 system 给的 schema 输出共识报告。只把双方都认可的点放进 consensus_*，分歧放 single_side_views。"
        )
        try:
            consensus = await _call_json(
                moderator_gen, MODERATOR_SYSTEM, moderator_user,
                max_tokens=6500,
                tool_name="submit_consensus",
                schema=_CONSENSUS_SCHEMA,
            )
        except Exception as e:
            consensus = {"_error": f"moderator failed: {e!r}"}

        elapsed = int(time.time() - t0)

        with db.connect() as con:
            con.execute(
                "UPDATE studio_insight_reports SET status='completed',"
                " claude_analysis=?, openai_analysis=?, debate_json=?,"
                " consensus_json=?, elapsed_s=?"
                " WHERE report_id=?",
                (
                    json.dumps(claude_a, ensure_ascii=False),
                    json.dumps(openai_a, ensure_ascii=False),
                    json.dumps(debate, ensure_ascii=False),
                    json.dumps(consensus, ensure_ascii=False),
                    elapsed,
                    report_id,
                ),
            )

        return {
            "report_id": report_id,
            "library_id": library_id,
            "project_id": pid,
            "status": "completed",
            "elapsed_s": elapsed,
            "claude_analysis": claude_a,
            "openai_analysis": openai_a,
            "debate": debate,
            "consensus": consensus,
        }
    except Exception as e:
        with db.connect() as con:
            con.execute(
                "UPDATE studio_insight_reports SET status='failed', error=? WHERE report_id=?",
                (repr(e), report_id),
            )
        raise


def get_report(report_id: str) -> dict[str, Any] | None:
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT * FROM studio_insight_reports WHERE report_id=?",
            (report_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["claude_analysis"] = json.loads(d.get("claude_analysis") or "null")
    d["openai_analysis"] = json.loads(d.get("openai_analysis") or "null")
    d["debate"] = json.loads(d.get("debate_json") or "null")
    d["consensus"] = json.loads(d.get("consensus_json") or "null")
    d.pop("debate_json", None)
    d.pop("consensus_json", None)
    return d


def latest_completed_for_current_library() -> dict[str, Any] | None:
    """Return the most recent completed insight report for the active library
    in the active project. Used by Strategy / Composer to include the report's
    consensus findings + opportunities in their prompts.
    """
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    lib = library.active_lib_id()
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT consensus_json FROM studio_insight_reports"
            " WHERE library_id = ?"
            " AND (project_id = ? OR project_id IS NULL)"
            " AND status = 'completed'"
            " ORDER BY created_at DESC LIMIT 1",
            (lib, pid),
        ).fetchone()
    if not row or not row["consensus_json"]:
        return None
    try:
        return json.loads(row["consensus_json"])
    except json.JSONDecodeError:
        return None


def consensus_summary_for_prompt(consensus: dict[str, Any] | None) -> str:
    """Compact text rendering of a consensus report, for embedding into
    downstream agent prompts (Strategy / Composer). Returns '' if nothing
    available."""
    if not consensus:
        return ""
    parts: list[str] = []
    if consensus.get("title"):
        parts.append(f"【上一份共识分析报告 · 起号洞察】《{consensus['title']}》")
    if consensus.get("executive_summary"):
        parts.append(f"总览：{consensus['executive_summary']}")
    lm = consensus.get("launch_mode") or {}
    if lm.get("recommendation"):
        mode_label = {
            "cold_start": "冷启动（先养号 3-7 篇低门槛内容）",
            "hot_start": "硬启动 / 热启动（第一篇直接打爆款角度）",
            "hybrid": "混合启动（前 2 篇养号 + 第 3 篇起直接打爆款）",
        }.get(lm["recommendation"], lm["recommendation"])
        parts.append(f"建议起号方式：{mode_label}")
        if lm.get("rationale"):
            parts.append(f"  理由：{lm['rationale']}")
        if lm.get("first_week_plan"):
            parts.append(f"  第一周执行：{lm['first_week_plan']}")
    cf = consensus.get("consensus_findings") or []
    if cf:
        parts.append("关键发现（双 AI 共识）：")
        for f in cf[:5]:
            parts.append(f"  · {f.get('title')}")
            ev = f.get("evidence")
            if ev: parts.append(f"    证据: {ev[:160]}")
            im = f.get("implication")
            if im: parts.append(f"    意义: {im[:160]}")
    co = consensus.get("consensus_opportunities") or []
    if co:
        parts.append("内容机会：")
        for o in co[:5]:
            parts.append(f"  · {o.get('opportunity')} → {o.get('suggested_angle')}")
    cr = consensus.get("consensus_risks") or []
    if cr:
        parts.append("风险：" + "；".join(r for r in cr[:4]))
    return "\n".join(parts)


def full_reference_block_for_prompt() -> str:
    """Combined reference block: the tool's own consensus *and* any integrated
    report (gpt-4o-fused external uploads), *and* — if no integration done
    yet — the raw text of any externally uploaded reports (each trimmed).
    Used by Strategy / Composer prompts so downstream agents see everything
    the user has assembled, even if they haven't pressed 「整合」 yet.
    """
    parts: list[str] = []
    own = latest_completed_for_current_library()
    own_summary = consensus_summary_for_prompt(own)
    if own_summary:
        parts.append(own_summary)
    try:
        from .external import latest_integrated_for_current_library, list_external_reports
        integ = latest_integrated_for_current_library()
    except Exception:
        integ = None
        list_external_reports = None  # type: ignore[assignment]
    integ_summary = consensus_summary_for_prompt(integ)
    if integ_summary:
        parts.append("【用户上传 / 整合的报告 · GPT-4o 融合】\n" + integ_summary)
    elif list_external_reports is not None:
        # No integration done yet — splice in raw external reports (capped).
        try:
            rows = list_external_reports()  # type: ignore[misc]
        except Exception:
            rows = []
        if rows:
            from .external import get_external_report
            blocks: list[str] = []
            CHAR_CAP = 3000  # per report
            for r in rows[:5]:  # first 5 by upload time
                full = get_external_report(r["report_id"]) or {}
                body = (full.get("content") or "").strip()
                if not body:
                    continue
                clipped = body if len(body) <= CHAR_CAP else (
                    body[:CHAR_CAP] + f"\n\n…[已截断，原文 {len(body)} 字]"
                )
                blocks.append(f"━ 用户上传报告《{r['name']}》━\n{clipped}")
            if blocks:
                parts.append(
                    "【用户上传的外部报告 · 原文，尚未做整合，先直接当参考】\n\n"
                    + "\n\n".join(blocks)
                )
    return "\n\n".join(parts)


def list_reports(library_id: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    where = "WHERE (project_id = ? OR project_id IS NULL)"
    args: list[Any] = [pid]
    if library_id:
        where += " AND library_id = ?"
        args.append(library_id)
    args.append(limit)
    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            f"SELECT report_id, library_id, project_id, created_at, status,"
            f" elapsed_s, error FROM studio_insight_reports {where}"
            f" ORDER BY created_at DESC LIMIT ?",
            args,
        ))
    return [dict(r) for r in rows]
