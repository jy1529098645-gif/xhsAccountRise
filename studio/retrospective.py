"""Retrospective ("复盘") pipeline.

Closes the loop on the 起号策略 flow:

  1. User pushes drafts through Composer → marks them as published
     (with the body that was *actually* posted, possibly edited).
  2. After some time, user comes back and logs actual platform metrics
     (likes / comments / saves / views / follower delta) per published
     draft via /api/drafts/{id}/performance.
  3. Retrospective.analyze() rolls everything into an analysis report:
     what hooks/angles actually worked, what flopped, why, and what to
     do next.
  4. Optionally, the next-cycle Strategy pack can be derived from the
     retrospective via strategy.iterate.iterate_strategy (uses the same
     interface, just with richer feedback).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from . import db, library, project
from .generators import registry
from .llm_call import call_for_json


# ---- Publish / performance CRUD ----------------------------------------

def mark_published(
    draft_id: str,
    *,
    published_title: str | None = None,
    published_body: str | None = None,
    published_url: str | None = None,
    published_notes: str | None = None,
) -> dict[str, Any]:
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    now = int(time.time())
    with db.connect() as con:
        cur = con.execute(
            "UPDATE studio_drafts SET published = 1, published_at = ?,"
            " published_title = ?, published_body = ?,"
            " published_url = ?, published_notes = ?"
            " WHERE draft_id = ?",
            (now, published_title, published_body, published_url,
             published_notes, draft_id),
        )
        if cur.rowcount == 0:
            raise LookupError(f"draft not found: {draft_id}")
    return {
        "draft_id": draft_id, "published_at": now,
        "published_url": published_url, "published_title": published_title,
    }


def unmark_published(draft_id: str) -> dict[str, Any]:
    db.apply_migrations(verbose=False)
    with db.connect() as con:
        cur = con.execute(
            "UPDATE studio_drafts SET published = 0, published_at = NULL"
            " WHERE draft_id = ?", (draft_id,),
        )
        if cur.rowcount == 0:
            raise LookupError(f"draft not found: {draft_id}")
    return {"draft_id": draft_id, "published": False}


def record_performance(
    draft_id: str,
    *,
    likes: int | None = None,
    comments: int | None = None,
    saves: int | None = None,
    shares: int | None = None,
    views: int | None = None,
    follower_delta: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    perf_id = "perf_" + uuid.uuid4().hex[:14]
    now = int(time.time())
    with db.connect() as con:
        # Validate draft exists.
        row = con.execute(
            "SELECT 1 FROM studio_drafts WHERE draft_id = ?", (draft_id,),
        ).fetchone()
        if not row:
            raise LookupError(f"draft not found: {draft_id}")
        con.execute(
            "INSERT INTO studio_draft_performance"
            " (perf_id, draft_id, project_id, recorded_at,"
            "  likes, comments, saves, shares, views, follower_delta, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (perf_id, draft_id, pid, now,
             likes, comments, saves, shares, views, follower_delta, notes),
        )
    return {
        "perf_id": perf_id, "draft_id": draft_id, "recorded_at": now,
        "likes": likes, "comments": comments, "saves": saves,
        "shares": shares, "views": views, "follower_delta": follower_delta,
        "notes": notes,
    }


def list_published_with_perf(library_id: str | None = None) -> list[dict[str, Any]]:
    """Return all published drafts for the active project + their latest perf
    snapshot (most recent perf row per draft)."""
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    where = "WHERE d.published = 1 AND (d.project_id = ? OR d.project_id IS NULL)"
    args: list[Any] = [pid]
    if library_id:
        where += " AND d.library_id = ?"
        args.append(library_id)
    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            "SELECT d.draft_id, d.library_id, d.published_at, d.published_title,"
            " d.published_body, d.published_url, d.published_notes,"
            " d.brief_json,"
            " (SELECT title FROM studio_draft_candidates"
            "  WHERE candidate_id = d.final_candidate_id) AS final_title,"
            " (SELECT body  FROM studio_draft_candidates"
            "  WHERE candidate_id = d.final_candidate_id) AS final_body"
            f" FROM studio_drafts d {where}"
            " ORDER BY d.published_at DESC",
            args,
        ))
        out = []
        for r in rows:
            d = dict(r)
            try: d["brief"] = json.loads(d.pop("brief_json") or "{}")
            except Exception: d["brief"] = {}
            perf_rows = list(con.execute(
                "SELECT perf_id, recorded_at, likes, comments, saves, shares,"
                " views, follower_delta, notes FROM studio_draft_performance"
                " WHERE draft_id = ? ORDER BY recorded_at DESC",
                (d["draft_id"],),
            ))
            d["performance"] = [dict(p) for p in perf_rows]
            out.append(d)
    return out


# ---- Retrospective analysis (LLM) --------------------------------------

_REVIEW_SYSTEM = """\
你是「起号复盘师」。用户已经按上一轮策略发了 N 篇，每一篇你能看到 ：
- AI 出的原始稿
- 用户**实际发布的版本**（可能编辑过 — 这是最重要的，因为这是真正发出去的）
- 真实的平台数据 ：点赞 / 评论 / 收藏 / 转发 / 阅读 / 涨粉

你的任务 ：**写一份专业复盘报告**，帮用户搞清楚 ：

1. **哪些篇是赢的（中位以上）—— 拆解共性 hook / 标题模式 / 内容结构 / 时段**
2. **哪些篇是输的（中位以下）—— 拆解共性问题**
3. **AI 原稿 vs 实际发布稿的差异** ：用户改了哪些地方，改完之后表现变好了还是变差了？
4. **数据模式** ：高赞但低评论 = 标题党，高收藏但低赞 = 干货型，等等
5. **下一轮要做什么 / 不做什么** ：具体到 hook 类型、角度、长度、时段

输出 JSON ：

{
  "executive_summary": "<3-5 句话总结这一轮跑下来的核心结论>",
  "wins": [
    {
      "draft_id": "<具体哪一篇>",
      "title": "<标题>",
      "metrics": "<点赞 X / 评论 Y / 收藏 Z>",
      "why_won": "<拆解为什么成功>"
    }
  ],
  "losses": [
    {
      "draft_id": "<具体哪一篇>",
      "title": "<标题>",
      "metrics": "<...>",
      "why_lost": "<拆解为什么翻车>"
    }
  ],
  "ai_vs_human_edits": [
    "<观察 ：用户在哪类内容上编辑得多 / 少 / 哪种编辑后表现变好>"
  ],
  "patterns": [
    "<跨篇的规律 ：如某种 hook 一直涨粉、某个时段表现好等>"
  ],
  "next_cycle_recommendations": {
    "double_down_on": ["<下一轮要加大投入的方向 / hook>"],
    "drop": ["<下一轮要砍掉的方向>"],
    "new_to_try": ["<根据评论 / 数据看到的新机会，下一轮试>"]
  },
  "risk_flags": ["<如果有数据风险 / 异常，例如 1 篇极端高、其它都低，提示>"]
}

要求 ：
- wins / losses 都至少要 1-2 条（如果数据够）
- 每条 win/loss 必须钉到具体 draft_id
- next_cycle_recommendations 是给下一步「迭代策略」的 ground truth
"""

_REVIEW_SCHEMA = {
    "type": "object",
    "required": ["executive_summary"],
    "properties": {
        "executive_summary": {"type": "string"},
        "wins":   {"type": "array", "items": {"type": "object"}},
        "losses": {"type": "array", "items": {"type": "object"}},
        "ai_vs_human_edits": {"type": "array", "items": {"type": "string"}},
        "patterns": {"type": "array", "items": {"type": "string"}},
        "next_cycle_recommendations": {"type": "object"},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
    },
}


def _render_published_for_prompt(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for r in rows:
        perf = r.get("performance") or [{}]
        latest = perf[0] if perf else {}
        metrics = ", ".join(
            f"{k}={v}" for k, v in latest.items()
            if v is not None and k in {"likes","comments","saves","shares","views","follower_delta"}
        ) or "（未录入数据）"
        brief = r.get("brief") or {}
        # Limit body sizes so 12+ drafts don't overflow.
        def _trim(s, n=400):
            s = (s or "").strip()
            return s[:n] + ("…" if len(s) > n else "")
        parts.append(
            f"━━ draft_id={r['draft_id']} · 发布于 {time.strftime('%Y-%m-%d', time.localtime(r.get('published_at',0)))}\n"
            f"原 topic ：{brief.get('topic','')}（angle={brief.get('angle','')}）\n"
            f"AI 原标题 ：{r.get('final_title','')}\n"
            f"用户实际发的标题 ：{r.get('published_title') or '（沿用 AI 原稿）'}\n"
            f"AI 原正文 ：{_trim(r.get('final_body',''))}\n"
            f"用户实际发的正文 ：{_trim(r.get('published_body','')) or '（沿用 AI 原稿）'}\n"
            f"用户复盘备注 ：{r.get('published_notes','') or '—'}\n"
            f"实际表现 ：{metrics}"
        )
    return "\n\n".join(parts) if parts else "（暂无已发布稿件）"


async def analyze(
    draft_ids: list[str] | None = None,
    *,
    library_id: str | None = None,
    model_spec: str = "openai:gpt-4o",
) -> dict[str, Any]:
    """Run a retrospective analysis across published drafts + their perf.

    If draft_ids is None, analyzes all published drafts in current project
    (optionally filtered by library_id).
    """
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()

    rows = list_published_with_perf(library_id=library_id)
    if draft_ids:
        rows = [r for r in rows if r["draft_id"] in set(draft_ids)]
    if not rows:
        raise ValueError(
            "没有已发布的稿件可以复盘。先在出稿那边把发了的稿子标记为「已发布」"
            "并录入数据。"
        )

    review_id = "rev_" + uuid.uuid4().hex[:14]
    now = int(time.time())
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_retrospective_reports"
            " (review_id, project_id, library_id, created_at, status, draft_ids_json)"
            " VALUES (?, ?, ?, ?, 'pending', ?)",
            (review_id, pid, library_id, now,
             json.dumps([r["draft_id"] for r in rows])),
        )

    t0 = time.time()
    try:
        gen = registry.build(model_spec)[0]
        user_msg = (
            f"共 {len(rows)} 篇已发布稿件需要复盘 ：\n\n"
            + _render_published_for_prompt(rows)
            + "\n\n请按 system schema 输出复盘报告。"
        )
        result = await call_for_json(
            gen, _REVIEW_SYSTEM, user_msg,
            max_tokens=6000,
            tool_name="submit_retrospective",
            schema=_REVIEW_SCHEMA,
        )
        elapsed = int(time.time() - t0)
        with db.connect() as con:
            con.execute(
                "UPDATE studio_retrospective_reports SET status='completed',"
                " analysis_json=?, elapsed_s=?  WHERE review_id=?",
                (json.dumps(result, ensure_ascii=False), elapsed, review_id),
            )
        return {
            "review_id": review_id,
            "project_id": pid,
            "library_id": library_id,
            "created_at": now,
            "status": "completed",
            "elapsed_s": elapsed,
            "draft_ids": [r["draft_id"] for r in rows],
            "analysis": result,
        }
    except Exception as e:
        with db.connect() as con:
            con.execute(
                "UPDATE studio_retrospective_reports SET status='failed', error=?"
                " WHERE review_id=?",
                (repr(e), review_id),
            )
        raise


def list_reviews(library_id: str | None = None) -> list[dict[str, Any]]:
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    where = "WHERE (project_id = ? OR project_id IS NULL)"
    args: list[Any] = [pid]
    if library_id:
        where += " AND library_id = ?"
        args.append(library_id)
    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            f"SELECT review_id, library_id, created_at, status, draft_ids_json,"
            f" elapsed_s, error FROM studio_retrospective_reports {where}"
            f" ORDER BY created_at DESC LIMIT 30",
            args,
        ))
    out = []
    for r in rows:
        d = dict(r)
        try: d["draft_ids"] = json.loads(d.pop("draft_ids_json") or "[]")
        except Exception: d["draft_ids"] = []
        out.append(d)
    return out


def get_review(review_id: str) -> dict[str, Any] | None:
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT * FROM studio_retrospective_reports WHERE review_id = ?",
            (review_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try: d["draft_ids"] = json.loads(d.pop("draft_ids_json") or "[]")
    except Exception: d["draft_ids"] = []
    try: d["analysis"] = json.loads(d.pop("analysis_json") or "null")
    except Exception: d["analysis"] = None
    return d
