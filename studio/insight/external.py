"""External reports + GPT-4o integration.

Users sometimes have analysis reports from elsewhere — consulting decks,
competitor teardowns, things they had ChatGPT generate, etc. Letting them
upload those and have the tool reference them downstream is far more valuable
than re-running our own analysis when they already trust an outside source.

Flow:
    1. Upload text (paste / .md / .txt) → studio_external_reports row.
    2. Integrate button → gpt-4o reads all uploaded reports (and optionally
       the latest tool-generated consensus) → emits a consensus-shaped JSON
       that the existing InsightReport renderer can display unchanged.
    3. Strategy / Composer prompts now include both:
        - tool-generated consensus (if any), and
        - any integrated report (if any) — both contribute to downstream.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .. import db, project
from ..generators import registry
from ..llm_call import call_for_json


# ---- Upload / list / delete ---------------------------------------------

def save_external_report(
    *, name: str, content: str,
    library_id: str | None = None,
    source: str = "粘贴文本",
    format: str = "text",
) -> dict[str, Any]:
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    report_id = "ext_" + uuid.uuid4().hex[:14]
    now = int(time.time())
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_external_reports"
            " (report_id, project_id, library_id, name, source, format, content, uploaded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (report_id, pid, library_id, name, source, format, content, now),
        )
    return {
        "report_id": report_id, "project_id": pid, "library_id": library_id,
        "name": name, "source": source, "format": format,
        "content_chars": len(content), "uploaded_at": now,
    }


def list_external_reports(library_id: str | None = None) -> list[dict[str, Any]]:
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    where = "WHERE (project_id = ? OR project_id IS NULL)"
    args: list[Any] = [pid]
    if library_id:
        where += " AND (library_id = ? OR library_id IS NULL)"
        args.append(library_id)
    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            f"SELECT report_id, project_id, library_id, name, source, format,"
            f" LENGTH(content) AS content_chars, uploaded_at"
            f" FROM studio_external_reports {where}"
            f" ORDER BY uploaded_at DESC",
            args,
        ))
    return [dict(r) for r in rows]


def get_external_report(report_id: str) -> dict[str, Any] | None:
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT * FROM studio_external_reports WHERE report_id = ?",
            (report_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_external_report(report_id: str) -> bool:
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    with db.connect() as con:
        cur = con.execute(
            "DELETE FROM studio_external_reports"
            " WHERE report_id = ? AND (project_id = ? OR project_id IS NULL)",
            (report_id, pid),
        )
        return cur.rowcount > 0


# ---- Integration via gpt-4o ---------------------------------------------

INTEGRATION_SYSTEM = """\
你是「报告整合主编」。用户给你扔了若干份外部分析报告（可能来自咨询、竞品拆解、ChatGPT、自己写的笔记等），可能还附带本工具自动生成的一份双 AI 共识报告。

你的任务：**融合所有这些报告**，输出**一份统一的起号分析共识报告**，让用户后面在做策略、写稿时只需要看这一份。

要求：
1. **不要简单拼接** — 同一论点出现在多份里要合并，相互矛盾的要标注。
2. **保留每个观点的出处**，标在 evidence 或 note 字段里（比如"咨询稿 + ChatGPT 都提到…"）。
3. **launch_mode**（冷启动 / 硬启动 / 混合启动）必须从输入材料里推断并明确给出。如果材料里有相关讨论就综合，没有就基于内容判断。
4. **charts_to_show** 从给定枚举里挑（即使外部报告不带原始数据，挑的是用户后面会看到的图表 keys，方便统一渲染）。
5. 如果某个外部报告只是数据 dump 没有结论，把它当作信号源，提炼为 findings。

输出 JSON：
{
  "title": "<整合后的报告标题>",
  "executive_summary": "<3-6 句融合后的核心结论>",
  "launch_mode": {
    "recommendation": "cold_start" | "hot_start" | "hybrid",
    "rationale": "<综合多份报告的理由>",
    "first_week_plan": "<第一周怎么发>",
    "agreement_level": "both_agree" | "leaned" | "split"
  },
  "consensus_findings": [
    {"title": "...", "evidence": "<引用具体哪份报告 + 内容>", "implication": "..."}
  ],
  "consensus_opportunities": [
    {"opportunity": "...", "why": "...", "suggested_angle": "..."}
  ],
  "consensus_risks": ["..."],
  "consensus_next_steps": ["..."],
  "single_side_views": [
    {"side": "<报告名或 'external_only'>", "point": "...", "note": "<为什么没合并>"}
  ],
  "charts_to_show": [
    "blue_ocean_top15" | "hook_distribution" | "timing_heatmap" |
    "top_tags" | "body_length" | "top_titles" | "comment_demand"
  ],
  "source_breakdown": [
    {"name": "<外部报告名>", "contributed": ["<它贡献了哪些点>"]}
  ]
}

严格按 JSON schema 输出，至少 4 条 consensus_findings、3 条 opportunities。
"""

_INTEGRATION_SCHEMA = {
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
        "source_breakdown": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["consensus_findings", "executive_summary"],
}


async def integrate(
    source_ids: list[str],
    *,
    library_id: str | None = None,
    include_consensus_report_id: str | None = None,
    model_spec: str = "openai:gpt-4o",
) -> dict[str, Any]:
    """Fuse external reports + (optionally) one tool-generated consensus.

    Persists an integrated_reports row and returns the full record.
    """
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()

    # Gather external bodies.
    ext_blocks: list[str] = []
    ext_names: list[str] = []
    for sid in source_ids:
        r = get_external_report(sid)
        if not r:
            continue
        ext_names.append(r["name"])
        body = r["content"]
        ext_blocks.append(f"━━━━━ 外部报告 ：《{r['name']}》"
                          f" (来源 ：{r.get('source','—')}, 格式 ：{r.get('format','text')})\n{body}")
    if not ext_blocks:
        raise ValueError("no valid external reports referenced")

    # Optionally append the tool's own consensus for fusion.
    tool_consensus_block = ""
    if include_consensus_report_id:
        from . import pipeline as insight_pipeline
        rep = insight_pipeline.get_report(include_consensus_report_id)
        if rep and rep.get("consensus"):
            tool_consensus_block = (
                "\n\n━━━━━ 本工具自动生成的双 AI 共识报告 (供融合参考)\n"
                + json.dumps(rep["consensus"], ensure_ascii=False, indent=2)
            )

    user_msg = (
        f"共有 {len(ext_blocks)} 份外部报告需要整合"
        + (f" + 工具自身的双 AI 共识报告" if tool_consensus_block else "")
        + " ：\n\n"
        + "\n\n".join(ext_blocks)
        + tool_consensus_block
        + "\n\n请按 system 的 schema 输出整合后的统一共识报告。"
    )

    integrated_id = "int_" + uuid.uuid4().hex[:14]
    now = int(time.time())
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_integrated_reports"
            " (integrated_id, project_id, library_id, created_at, status,"
            "  source_ids, include_consensus_report_id)"
            " VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (integrated_id, pid, library_id, now,
             json.dumps(source_ids), include_consensus_report_id),
        )

    t0 = time.time()
    try:
        gen = registry.build(model_spec)[0]
        consensus = await call_for_json(
            gen, INTEGRATION_SYSTEM, user_msg,
            max_tokens=6500,
            tool_name="submit_integrated_consensus",
            schema=_INTEGRATION_SCHEMA,
        )
        elapsed = int(time.time() - t0)
        with db.connect() as con:
            con.execute(
                "UPDATE studio_integrated_reports SET status='completed',"
                " consensus_json=?, elapsed_s=? WHERE integrated_id=?",
                (json.dumps(consensus, ensure_ascii=False), elapsed, integrated_id),
            )
        return {
            "integrated_id": integrated_id, "project_id": pid, "library_id": library_id,
            "created_at": now, "status": "completed", "elapsed_s": elapsed,
            "source_ids": source_ids,
            "include_consensus_report_id": include_consensus_report_id,
            "source_names": ext_names,
            "consensus": consensus,
        }
    except Exception as e:
        with db.connect() as con:
            con.execute(
                "UPDATE studio_integrated_reports SET status='failed', error=?"
                " WHERE integrated_id=?",
                (repr(e), integrated_id),
            )
        raise


def get_integrated_report(integrated_id: str) -> dict[str, Any] | None:
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT * FROM studio_integrated_reports WHERE integrated_id = ?",
            (integrated_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["source_ids"] = json.loads(d.get("source_ids") or "[]")
    d["consensus"] = json.loads(d.get("consensus_json") or "null")
    d.pop("consensus_json", None)
    return d


def list_integrated_reports(library_id: str | None = None) -> list[dict[str, Any]]:
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    where = "WHERE (project_id = ? OR project_id IS NULL)"
    args: list[Any] = [pid]
    if library_id:
        where += " AND (library_id = ? OR library_id IS NULL)"
        args.append(library_id)
    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            f"SELECT integrated_id, project_id, library_id, created_at, status,"
            f" source_ids, elapsed_s, error FROM studio_integrated_reports"
            f" {where} ORDER BY created_at DESC",
            args,
        ))
    out = []
    for r in rows:
        d = dict(r)
        d["source_ids"] = json.loads(d.get("source_ids") or "[]")
        out.append(d)
    return out


def latest_integrated_for_current_library() -> dict[str, Any] | None:
    """Most recent successful integrated report — used by Strategy / Composer
    to include it in prompts alongside (or instead of) the tool's own consensus.
    """
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    from .. import library as _library
    lib = _library.active_lib_id()
    with db.connect(read_only=True) as con:
        # Match on library if set; also accept library_id=NULL (global integration).
        row = con.execute(
            "SELECT consensus_json FROM studio_integrated_reports"
            " WHERE (project_id = ? OR project_id IS NULL)"
            " AND (library_id = ? OR library_id IS NULL)"
            " AND status = 'completed'"
            " ORDER BY created_at DESC LIMIT 1",
            (pid, lib),
        ).fetchone()
    if not row or not row["consensus_json"]:
        return None
    try:
        return json.loads(row["consensus_json"])
    except json.JSONDecodeError:
        return None
