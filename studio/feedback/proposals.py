"""Prompt-version proposal queue (item 8 = the long-promised "W4 prompt fork").

Lifecycle:
    1. User finishes a retrospective review (studio_retrospective_reports).
    2. propose_from_retrospective(review_id) reads the review's
       `next_cycle_recommendations` + `patterns` + `losses`, feeds them into
       an LLM with the CURRENT active prompt, and asks for a concrete diff
       (new full prompt text + 1-sentence rationale + expected gain).
    3. The diff lands in studio_prompt_proposals with status='pending'.
    4. UI surfaces it on the Retrospective page. User clicks approve →
       approve_proposal() writes a new row to studio_prompt_versions (which
       has existed since migration 001 but was never populated), bumps the
       active version, and marks the proposal 'approved'.
    5. Subsequent Compose runs pick up the new version automatically — we
       read the *active* row from studio_prompt_versions instead of the
       hardcoded TITLE_BODY_GEN_VERSION constant.

Safety:
    - Approval is human-gated. The LLM only proposes.
    - We keep parent_version pointers so a bad approval can be reverted by
       activating an older row.
    - Only `title_body_gen` is wired in v0.53; critic / synthesizer prompt
       evolution is left for a later milestone.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .. import db, project
from ..generators import registry
from ..llm_call import call_for_json
from ..generators import prompts as g_prompts


_PROPOSE_SYSTEM = """\
你是「Prompt 工程师」。用户刚跑完一轮起号复盘，拿到了真实数据 + 复盘分析。
现在请基于复盘的 wins / losses / patterns / next_cycle_recommendations，对**当前出稿 prompt** 提出一份**最小可行的修改建议**。

要求：
1. **只动出稿 prompt 中能解决复盘问题的部分**。不要重写整个 prompt——这是迭代不是重启。
2. **必须明确指出**：你改了哪一段，为什么改（引用复盘中的具体证据）。
3. **预期收益**写一句话，定性即可（"下一轮 hook 类型从教程偏向痛点应该能涨评论"）。
4. 如果复盘数据不足以推断 prompt 该怎么改，就直接拒绝——返回 should_propose=false + reason。

输出 JSON：

{
  "should_propose": <true | false>,
  "reason_if_no": "<不建议改时的原因>",
  "diff_summary": "<1-2 句话：改了什么 + 为什么>",
  "proposed_prompt": "<完整的新 prompt 文本（不是 diff，是替换版）>",
  "expected_gain": "<一句话定性预期>",
  "evidence": [
    {"signal": "<复盘中的哪一条结论>", "why_changes_prompt": "<为什么这要求 prompt 改>"}
  ]
}

绝对不要：
- 编造复盘里没有的结论
- 把改稿建议写成「让 LLM 写得更好」之类的空话
- 直接拷贝原 prompt（如果真没什么要改，should_propose=false 即可）
"""


_PROPOSE_SCHEMA = {
    "type": "object",
    "required": ["should_propose"],
    "properties": {
        "should_propose": {"type": "boolean"},
        "reason_if_no": {"type": "string"},
        "diff_summary": {"type": "string"},
        "proposed_prompt": {"type": "string"},
        "expected_gain": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "signal": {"type": "string"},
                    "why_changes_prompt": {"type": "string"},
                },
            },
        },
    },
}


def _current_active_version(generator_name: str = "title_body_gen") -> tuple[str, str]:
    """Return (version_id, prompt_text). Falls back to the hardcoded prompt
    if no row exists (first call ever)."""
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT version, prompt_text FROM studio_prompt_versions"
            " WHERE generator_name = ? AND active = 1"
            " ORDER BY created_at DESC LIMIT 1",
            (generator_name,),
        ).fetchone()
    if row:
        return row["version"], row["prompt_text"]
    # No persisted version yet — seed from the in-code default and return.
    return g_prompts.TITLE_BODY_GEN_VERSION, g_prompts.SYSTEM_TITLE_BODY


def _seed_initial_if_missing(generator_name: str = "title_body_gen") -> None:
    """Insert the hardcoded SYSTEM_TITLE_BODY as v1.0.0 if the table is empty.
    Idempotent. Run before approve_proposal so the parent_version link is valid.
    """
    db.apply_migrations(verbose=False)
    with db.connect() as con:
        row = con.execute(
            "SELECT 1 FROM studio_prompt_versions WHERE generator_name = ?",
            (generator_name,),
        ).fetchone()
        if row:
            return
        con.execute(
            "INSERT INTO studio_prompt_versions"
            " (version, generator_name, prompt_text, created_at,"
            "  parent_version, diff_summary, active)"
            " VALUES (?, ?, ?, ?, ?, ?, 1)",
            (
                g_prompts.TITLE_BODY_GEN_VERSION, generator_name,
                g_prompts.SYSTEM_TITLE_BODY, int(time.time()),
                None, "v0.53 initial seed from hardcoded prompt",
            ),
        )


def _next_version(parent: str) -> str:
    """Bump the last semver segment by 1. E.g. 'title_body_gen-1.0.0' →
    'title_body_gen-1.0.1'. Falls back to appending '-fork-{ts}' for
    non-conforming ids."""
    import re
    m = re.match(r"(.+?)(\d+)\.(\d+)\.(\d+)$", parent)
    if not m:
        return f"{parent}-fork-{int(time.time())}"
    base, major, minor, patch = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return f"{base}{major}.{minor}.{patch + 1}"


async def propose_from_retrospective(
    review_id: str,
    *,
    generator_name: str = "title_body_gen",
    proposer_spec: str = "openai:gpt-4o",
) -> dict[str, Any]:
    """LLM-generate a prompt proposal from a finished retrospective.

    Returns the inserted proposal row (or skipped=True if LLM voted not to
    propose).
    """
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project.active_project_id()
    _seed_initial_if_missing(generator_name)

    # Load review.
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT analysis_json, status, draft_ids_json"
            " FROM studio_retrospective_reports WHERE review_id = ?",
            (review_id,),
        ).fetchone()
    if not row:
        raise LookupError(f"review not found: {review_id}")
    if row["status"] != "completed":
        raise ValueError(f"review not completed yet (status={row['status']})")
    analysis = json.loads(row["analysis_json"] or "{}")

    # Pull current active prompt.
    parent_version, parent_text = _current_active_version(generator_name)

    # Build the user message.
    rec = analysis.get("next_cycle_recommendations") or {}
    wins = analysis.get("wins") or []
    losses = analysis.get("losses") or []
    patterns = analysis.get("patterns") or []
    summary = analysis.get("executive_summary") or ""

    user_msg = (
        f"【当前出稿 prompt（version={parent_version}）】\n"
        f"{parent_text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"【最新一轮复盘结论】\n"
        f"summary: {summary}\n\n"
        f"wins ({len(wins)}):\n"
        + "\n".join(f"- {w}" for w in wins) + "\n\n"
        f"losses ({len(losses)}):\n"
        + "\n".join(f"- {l}" for l in losses) + "\n\n"
        f"patterns:\n"
        + "\n".join(f"- {p}" for p in patterns) + "\n\n"
        f"next_cycle_recommendations:\n{json.dumps(rec, ensure_ascii=False, indent=2)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"现在请按 system 给的 schema 输出 JSON："
    )

    gen = registry.build(proposer_spec)[0]
    parsed = await call_for_json(
        gen, _PROPOSE_SYSTEM, user_msg,
        max_tokens=4000,
        tool_name="submit_prompt_proposal",
        schema=_PROPOSE_SCHEMA,
    )

    if not parsed.get("should_propose"):
        return {
            "skipped": True,
            "reason": parsed.get("reason_if_no") or "LLM 认为不需要改 prompt",
            "review_id": review_id,
        }

    proposed_text = parsed.get("proposed_prompt") or ""
    if not proposed_text.strip():
        return {
            "skipped": True,
            "reason": "LLM 返回了空 prompt 文本，拒绝入队",
            "review_id": review_id,
        }
    if proposed_text.strip() == parent_text.strip():
        return {
            "skipped": True,
            "reason": "LLM 给的新 prompt 和当前版本完全相同",
            "review_id": review_id,
        }

    proposal_id = "prop_" + uuid.uuid4().hex[:12]
    new_version = _next_version(parent_version)
    now = int(time.time())
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_prompt_proposals"
            " (proposal_id, review_id, project_id, generator_name,"
            "  parent_version, proposed_version, diff_summary,"
            "  proposed_prompt, expected_gain, evidence_json,"
            "  created_at, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (
                proposal_id, review_id, pid, generator_name,
                parent_version, new_version,
                parsed.get("diff_summary") or "",
                proposed_text, parsed.get("expected_gain") or "",
                json.dumps(parsed.get("evidence") or [], ensure_ascii=False),
                now,
            ),
        )

    return {
        "skipped": False,
        "proposal_id": proposal_id,
        "review_id": review_id,
        "parent_version": parent_version,
        "proposed_version": new_version,
        "diff_summary": parsed.get("diff_summary") or "",
        "expected_gain": parsed.get("expected_gain") or "",
        "evidence": parsed.get("evidence") or [],
        "created_at": now,
        "status": "pending",
    }


def list_proposals(
    *, status: str | None = None,
    project_id: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project_id or project.active_project_id()
    where = " WHERE (project_id = ? OR project_id IS NULL)"
    args: list[Any] = [pid]
    if status:
        where += " AND status = ?"
        args.append(status)
    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            "SELECT proposal_id, review_id, generator_name, parent_version,"
            " proposed_version, diff_summary, expected_gain, evidence_json,"
            " created_at, status, decided_at, decision_notes"
            f" FROM studio_prompt_proposals{where}"
            " ORDER BY created_at DESC LIMIT ?",
            (*args, limit),
        ))
    out = []
    for r in rows:
        d = dict(r)
        try: d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
        except Exception: d["evidence"] = []
        out.append(d)
    return out


def get_proposal(proposal_id: str) -> dict[str, Any] | None:
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT * FROM studio_prompt_proposals WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try: d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
    except Exception: d["evidence"] = []
    return d


def approve_proposal(proposal_id: str, *, notes: str = "") -> dict[str, Any]:
    """Mark a proposal approved, write the new row to studio_prompt_versions,
    and deactivate the old one. The next Compose run picks up the new prompt
    automatically.
    """
    db.apply_migrations(verbose=False)
    now = int(time.time())
    with db.connect() as con:
        row = con.execute(
            "SELECT * FROM studio_prompt_proposals WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if not row:
            raise LookupError(f"proposal not found: {proposal_id}")
        if row["status"] != "pending":
            raise ValueError(f"proposal not pending (status={row['status']})")
        # Deactivate prior active version(s) for this generator.
        con.execute(
            "UPDATE studio_prompt_versions SET active = 0"
            " WHERE generator_name = ? AND active = 1",
            (row["generator_name"],),
        )
        # Insert new version row.
        con.execute(
            "INSERT INTO studio_prompt_versions"
            " (version, generator_name, prompt_text, created_at,"
            "  parent_version, diff_summary, active)"
            " VALUES (?, ?, ?, ?, ?, ?, 1)",
            (
                row["proposed_version"], row["generator_name"],
                row["proposed_prompt"], now,
                row["parent_version"], row["diff_summary"],
            ),
        )
        # Mark proposal approved.
        con.execute(
            "UPDATE studio_prompt_proposals SET status='approved',"
            " decided_at=?, decision_notes=? WHERE proposal_id=?",
            (now, notes, proposal_id),
        )
    return {
        "proposal_id": proposal_id, "status": "approved",
        "new_active_version": row["proposed_version"],
        "decided_at": now,
    }


def reject_proposal(proposal_id: str, *, notes: str = "") -> dict[str, Any]:
    db.apply_migrations(verbose=False)
    now = int(time.time())
    with db.connect() as con:
        cur = con.execute(
            "UPDATE studio_prompt_proposals SET status='rejected',"
            " decided_at=?, decision_notes=? WHERE proposal_id=? AND status='pending'",
            (now, notes, proposal_id),
        )
        if cur.rowcount == 0:
            raise LookupError(
                f"proposal not found or not pending: {proposal_id}"
            )
    return {"proposal_id": proposal_id, "status": "rejected",
            "decided_at": now}


def active_prompt(generator_name: str = "title_body_gen") -> tuple[str, str]:
    """Public helper for prompts.py to pick the active version at runtime."""
    return _current_active_version(generator_name)
