"""CRUD for studio_product_contexts."""
from __future__ import annotations

import time
import uuid
from typing import Any

from .. import db, project


def _now() -> int:
    return int(time.time())


def list_contexts(
    *, project_id: str | None = None,
    active_only: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project_id or project.active_project_id()
    where = "WHERE project_id = ?"
    args: list[Any] = [pid]
    if active_only:
        where += " AND active = 1"
    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            "SELECT context_id, project_id, name, source_format,"
            " source_filename, chars, created_at, updated_at, active"
            f" FROM studio_product_contexts {where}"
            " ORDER BY created_at DESC LIMIT ?",
            (*args, limit),
        ))
    return [dict(r) for r in rows]


def get_context(context_id: str, *, include_body: bool = True) -> dict[str, Any] | None:
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT * FROM studio_product_contexts WHERE context_id = ?",
            (context_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if not include_body:
        d.pop("body_text", None)
    return d


def create_context(
    *, name: str, body_text: str,
    source_format: str = "paste",
    source_filename: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    if not body_text or not body_text.strip():
        raise ValueError("body_text is empty")
    db.apply_migrations(verbose=False)
    project.ensure_bootstrap()
    pid = project_id or project.active_project_id()
    cid = "pctx_" + uuid.uuid4().hex[:14]
    now = _now()
    with db.connect() as con:
        con.execute(
            "INSERT INTO studio_product_contexts"
            " (context_id, project_id, name, body_text, source_format,"
            "  source_filename, chars, created_at, updated_at, active)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (
                cid, pid, name.strip() or "(未命名)", body_text,
                source_format, source_filename, len(body_text),
                now, now,
            ),
        )
    return {
        "context_id": cid, "project_id": pid, "name": name,
        "source_format": source_format, "source_filename": source_filename,
        "chars": len(body_text), "created_at": now, "updated_at": now,
        "active": 1,
    }


def delete_context(context_id: str) -> dict[str, Any]:
    db.apply_migrations(verbose=False)
    with db.connect() as con:
        cur = con.execute(
            "DELETE FROM studio_product_contexts WHERE context_id = ?",
            (context_id,),
        )
        if cur.rowcount == 0:
            raise LookupError(f"context not found: {context_id}")
    return {"deleted": context_id}


def set_active(context_id: str, active: bool) -> dict[str, Any]:
    db.apply_migrations(verbose=False)
    with db.connect() as con:
        cur = con.execute(
            "UPDATE studio_product_contexts SET active = ?, updated_at = ?"
            " WHERE context_id = ?",
            (1 if active else 0, _now(), context_id),
        )
        if cur.rowcount == 0:
            raise LookupError(f"context not found: {context_id}")
    return {"context_id": context_id, "active": bool(active)}


def context_block(project_id: str | None = None, max_chars: int = 12000) -> str:
    """Return all ACTIVE contexts for the project as a single prompt-friendly
    block. Used by Strategy / Compose / Insight pipelines to inject product
    knowledge into LLM prompts. Empty string if no active contexts.

    Caps at max_chars (per body, distributed across contexts) so a 100KB upload
    doesn't blow the prompt window.
    """
    rows = list_contexts(project_id=project_id, active_only=True, limit=10)
    if not rows:
        return ""
    db.apply_migrations(verbose=False)
    # Load full body for each.
    with db.connect(read_only=True) as con:
        bodies = []
        budget = max_chars
        for r in rows:
            if budget <= 0:
                break
            row = con.execute(
                "SELECT body_text FROM studio_product_contexts WHERE context_id = ?",
                (r["context_id"],),
            ).fetchone()
            if not row:
                continue
            body = (row["body_text"] or "").strip()
            if not body:
                continue
            take = body[:budget]
            bodies.append((r["name"], take))
            budget -= len(take)
    if not bodies:
        return ""
    parts = []
    for name, body in bodies:
        parts.append(f"━━ 上下文：「{name}」 ━━\n{body}")
    return "\n\n".join(parts)
