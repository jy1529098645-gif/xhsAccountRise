"""Project layer (multi-tenant container for libraries / drafts / strategies).

A project groups everything related to one account or one strategic effort.
Switching projects flips the active library too, so users can hop between
isolated workspaces without leakage.

Layout:
    data/active_project.txt        # one-line pointer to project_id
    studio_projects (SQLite)       # metadata
    libraries:                     # meta.json now carries project_id
    drafts / strategies / posts    # row-level project_id

A 'default' project is bootstrapped on first run and absorbs any legacy
data (rows where project_id IS NULL).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import config, db


ACTIVE_PROJECT_PATH = config.DATA_DIR / "active_project.txt"

_ID_OK = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,32}$")
_NAME_SANITIZER = re.compile(r"[^\w一-鿿\-]+", re.UNICODE)


@dataclass
class Project:
    project_id: str
    name: str
    description: str = ""
    emoji: str = "📁"
    created_at: int = 0
    updated_at: int = 0
    is_default: bool = False
    archived: bool = False


def _slug(text: str) -> str:
    s = _NAME_SANITIZER.sub("-", text.lower()).strip("-")[:24]
    return s or "proj"


def _alloc_id(hint: str | None) -> str:
    base = _slug(hint or "proj")
    # Avoid colliding with reserved id
    if base == "default":
        base = "proj"
    with db.connect(read_only=True) as con:
        existing = {r[0] for r in con.execute("SELECT project_id FROM studio_projects")}
    if base not in existing:
        return base
    return f"{base}-{uuid.uuid4().hex[:6]}"


def list_projects(include_archived: bool = False) -> list[Project]:
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        if include_archived:
            rows = list(con.execute(
                "SELECT * FROM studio_projects ORDER BY created_at ASC"
            ))
        else:
            rows = list(con.execute(
                "SELECT * FROM studio_projects WHERE archived = 0"
                " ORDER BY created_at ASC"
            ))
    return [
        Project(
            project_id=r["project_id"],
            name=r["name"],
            description=r["description"] or "",
            emoji=r["emoji"] or "📁",
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            is_default=bool(r["is_default"]),
            archived=bool(r["archived"]),
        )
        for r in rows
    ]


def get_project(project_id: str) -> Project | None:
    db.apply_migrations(verbose=False)
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT * FROM studio_projects WHERE project_id = ?", (project_id,)
        ).fetchone()
    if not row:
        return None
    return Project(
        project_id=row["project_id"],
        name=row["name"],
        description=row["description"] or "",
        emoji=row["emoji"] or "📁",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_default=bool(row["is_default"]),
        archived=bool(row["archived"]),
    )


def active_project_id(default: str = "default") -> str:
    if ACTIVE_PROJECT_PATH.exists():
        text = ACTIVE_PROJECT_PATH.read_text(encoding="utf-8").strip()
        if text:
            return text
    return default


def set_active(project_id: str) -> None:
    if not get_project(project_id):
        raise ValueError(f"project not found: {project_id}")
    ACTIVE_PROJECT_PATH.write_text(project_id, encoding="utf-8")


def create(name: str, description: str = "", emoji: str = "📁",
           project_id: str | None = None) -> Project:
    db.apply_migrations(verbose=False)
    pid = project_id or _alloc_id(name)
    if not _ID_OK.match(pid):
        raise ValueError(f"invalid project_id: {pid!r}")
    now = int(time.time())
    with db.connect() as con:
        try:
            con.execute(
                "INSERT INTO studio_projects"
                " (project_id, name, description, emoji,"
                "  created_at, updated_at, is_default, archived)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, name, description, emoji, now, now, 0, 0),
            )
        except Exception as e:
            raise ValueError(f"project create failed: {e}") from e
    return get_project(pid)  # type: ignore


def update_meta(project_id: str, *, name: str | None = None,
                description: str | None = None,
                emoji: str | None = None) -> Project:
    p = get_project(project_id)
    if not p:
        raise ValueError(f"project not found: {project_id}")
    if name is not None: p.name = name
    if description is not None: p.description = description
    if emoji is not None: p.emoji = emoji
    p.updated_at = int(time.time())
    with db.connect() as con:
        con.execute(
            "UPDATE studio_projects SET name=?, description=?, emoji=?, updated_at=?"
            " WHERE project_id=?",
            (p.name, p.description, p.emoji, p.updated_at, project_id),
        )
    return p


def archive(project_id: str) -> None:
    p = get_project(project_id)
    if not p:
        raise ValueError(f"project not found: {project_id}")
    if p.is_default:
        raise RuntimeError("cannot archive the default project")
    with db.connect() as con:
        con.execute(
            "UPDATE studio_projects SET archived=1, updated_at=? WHERE project_id=?",
            (int(time.time()), project_id),
        )
    # If active, fall back to default.
    if active_project_id() == project_id:
        set_active("default")


def hard_delete(project_id: str) -> dict[str, int]:
    """PERMANENTLY delete a project and all its data (drafts, strategies,
    reports, performance, etc.). Returns counts of rows deleted per table.
    Refuses to delete the default project."""
    p = get_project(project_id)
    if not p:
        raise ValueError(f"project not found: {project_id}")
    if p.is_default:
        raise RuntimeError("cannot delete the default project")

    if active_project_id() == project_id:
        set_active("default")

    counts: dict[str, int] = {}
    # Tables that have a project_id column. Delete by exact match — never
    # cascade across other projects.
    cascading_tables = [
        "studio_strategies",
        "studio_drafts",
        "studio_my_posts",
        "studio_dna_artifacts",
        "studio_insight_reports",
        "studio_external_reports",
        "studio_integrated_reports",
        "studio_strategy_performance",
        "studio_retrospective_reports",
        "studio_draft_performance",
    ]
    with db.connect() as con:
        # First snapshot draft_ids in this project so we can drop their
        # candidates + critiques + traces too (those tables don't carry
        # project_id directly).
        draft_ids = [r[0] for r in con.execute(
            "SELECT draft_id FROM studio_drafts WHERE project_id = ?",
            (project_id,),
        ).fetchall()]
        pack_ids = [r[0] for r in con.execute(
            "SELECT pack_id FROM studio_strategies WHERE project_id = ?",
            (project_id,),
        ).fetchall()]

        # Cascade through draft children
        if draft_ids:
            qmarks = ",".join("?" * len(draft_ids))
            counts["draft_candidates"] = con.execute(
                f"DELETE FROM studio_draft_candidates WHERE draft_id IN ({qmarks})",
                draft_ids,
            ).rowcount
            counts["critiques"] = con.execute(
                f"DELETE FROM studio_critiques WHERE draft_id IN ({qmarks})",
                draft_ids,
            ).rowcount
            counts["agent_traces"] = con.execute(
                f"DELETE FROM studio_agent_traces WHERE draft_id IN ({qmarks})",
                draft_ids,
            ).rowcount

        for table in cascading_tables:
            try:
                cur = con.execute(
                    f"DELETE FROM {table} WHERE project_id = ?", (project_id,),
                )
                counts[table] = cur.rowcount
            except Exception:
                # Table might not exist on older DBs — skip.
                counts[table] = 0

        # Finally, drop the project row itself.
        con.execute("DELETE FROM studio_projects WHERE project_id = ?", (project_id,))
        counts["studio_projects"] = 1

    return counts


def ensure_bootstrap() -> None:
    """Boot-time: create default project + assign legacy rows."""
    db.apply_migrations(verbose=False)
    with db.connect() as con:
        row = con.execute(
            "SELECT 1 FROM studio_projects WHERE project_id = 'default'"
        ).fetchone()
    if not row:
        now = int(time.time())
        with db.connect() as con:
            # OR IGNORE: race-safe — two concurrent bootstraps won't crash on
            # PRIMARY KEY uniqueness violation.
            con.execute(
                "INSERT OR IGNORE INTO studio_projects"
                " (project_id, name, description, emoji,"
                "  created_at, updated_at, is_default, archived)"
                " VALUES ('default', '默认项目', '首次使用自动创建', '🏠', ?, ?, 1, 0)",
                (now, now),
            )
    # Assign legacy rows where project_id IS NULL → 'default'
    with db.connect() as con:
        for tbl in ("studio_drafts", "studio_strategies",
                    "studio_my_posts", "studio_dna_artifacts"):
            try:
                con.execute(
                    f"UPDATE {tbl} SET project_id='default' WHERE project_id IS NULL"
                )
            except Exception:
                pass
    if not ACTIVE_PROJECT_PATH.exists():
        ACTIVE_PROJECT_PATH.write_text("default", encoding="utf-8")
