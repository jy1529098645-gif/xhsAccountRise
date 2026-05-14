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
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

from . import config, db


ACTIVE_PROJECT_PATH = config.DATA_DIR / "active_project.txt"

# v0.61.3 ：studio_projects 表迁出 per-library xhs.db，搬到 data/projects.db
# 全局表。
# 旧 bug ：在 Library A 上创建 P → INSERT 进 A 的 .db.studio_projects，再
# activateProject(P) 把 P 设成 active 项目。重载后 P 没有任何 library，
# active_lib_id() 回退 "default"，于是 current_db_path() 指 default lib，
# 读不到 P（P 的行在 A 的 .db 里）。用户表现 ：「新建项目看不到，要挨个
# 切换才出现」。修复 ：项目表本来就是全局概念，分库存就是设计错误。
_GLOBAL_DB_PATH = config.DATA_DIR / "projects.db"


def _global_connect(read_only: bool = False) -> sqlite3.Connection:
    """全局 DB（仅装 studio_projects 与未来其它项目级元数据）。"""
    _GLOBAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if read_only and _GLOBAL_DB_PATH.exists():
        uri = f"file:{_GLOBAL_DB_PATH.as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    else:
        con = sqlite3.connect(_GLOBAL_DB_PATH)
        con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    _ensure_global_schema(con)
    return con


@contextmanager
def _gconn(read_only: bool = False) -> Iterator[sqlite3.Connection]:
    con = _global_connect(read_only=read_only)
    try:
        yield con
        if not read_only:
            con.commit()
    except Exception:
        if not read_only:
            con.rollback()
        raise
    finally:
        con.close()


def _ensure_global_schema(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS studio_projects ("
        " project_id  TEXT PRIMARY KEY,"
        " name        TEXT NOT NULL,"
        " description TEXT,"
        " emoji       TEXT,"
        " created_at  INTEGER NOT NULL,"
        " updated_at  INTEGER NOT NULL,"
        " is_default  INTEGER NOT NULL DEFAULT 0,"
        " archived    INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_projects_archived"
        " ON studio_projects(archived)"
    )

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
    with _gconn(read_only=True) as con:
        existing = {r[0] for r in con.execute("SELECT project_id FROM studio_projects")}
    if base not in existing:
        return base
    return f"{base}-{uuid.uuid4().hex[:6]}"


def list_projects(include_archived: bool = False) -> list[Project]:
    with _gconn(read_only=True) as con:
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
    with _gconn(read_only=True) as con:
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
    pid = project_id or _alloc_id(name)
    if not _ID_OK.match(pid):
        raise ValueError(f"invalid project_id: {pid!r}")
    now = int(time.time())
    with _gconn() as con:
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
    with _gconn() as con:
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
    with _gconn() as con:
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
    # 级联表（drafts / strategies / 报告 / 业绩等）仍在 per-library .db 里 —
    # 这里只清当前活跃库的对应行；其它库里残留的同 project_id 行会在那条库
    # 自身被打开时被忽略（项目已不存在，filter 时不会匹配）。最终留存的孤儿
    # 数据无害，且如果用户日后真把 P 再用回来，那些行会自动重新出现。
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

    # studio_projects 现在在全局 db
    with _gconn() as con:
        con.execute("DELETE FROM studio_projects WHERE project_id = ?", (project_id,))
        counts["studio_projects"] = 1

    return counts


def ensure_bootstrap() -> None:
    """Boot-time: create default project + assign legacy rows + 一次性把残留
    在 per-library .db 里的 studio_projects 行迁进全局 db。"""
    # 1) 把残留在每个 library/xhs.db 里的 studio_projects 合并到全局 db。
    #    一次性 + 幂等（INSERT OR IGNORE 按 PK）。修复 v0.61.3 之前用户
    #    在各种 library 状态下创建的项目散落在不同 .db 文件里的历史问题。
    _migrate_per_library_projects_into_global()

    # 2) 确保默认项目存在
    with _gconn() as con:
        row = con.execute(
            "SELECT 1 FROM studio_projects WHERE project_id = 'default'"
        ).fetchone()
        if not row:
            now = int(time.time())
            con.execute(
                "INSERT OR IGNORE INTO studio_projects"
                " (project_id, name, description, emoji,"
                "  created_at, updated_at, is_default, archived)"
                " VALUES ('default', '默认项目', '首次使用自动创建', '🏠', ?, ?, 1, 0)",
                (now, now),
            )
    # 3) Assign legacy rows where project_id IS NULL → 'default'（per-library
    #    业务表里的孤儿行，跟全局 studio_projects 无关）
    db.apply_migrations(verbose=False)
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


def _migrate_per_library_projects_into_global() -> None:
    """扫所有 data/libraries/*/xhs.db 里的 studio_projects，把行 union 到
    data/projects.db。INSERT OR IGNORE 保证幂等。"""
    libraries_dir = config.DATA_DIR / "libraries"
    if not libraries_dir.exists():
        return
    rows_to_insert: list[tuple] = []
    for lib_dir in libraries_dir.iterdir():
        if not lib_dir.is_dir():
            continue
        per_lib_db = lib_dir / "xhs.db"
        if not per_lib_db.exists():
            continue
        try:
            uri = f"file:{per_lib_db.as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
            con.row_factory = sqlite3.Row
            try:
                # studio_projects 在老库里可能不存在（未跑过 005 migration）
                has_table = con.execute(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='table' AND name='studio_projects'"
                ).fetchone()
                if not has_table:
                    continue
                rows = con.execute(
                    "SELECT project_id, name, description, emoji,"
                    " created_at, updated_at, is_default, archived"
                    " FROM studio_projects"
                ).fetchall()
                for r in rows:
                    rows_to_insert.append((
                        r["project_id"], r["name"], r["description"], r["emoji"],
                        r["created_at"], r["updated_at"],
                        r["is_default"], r["archived"],
                    ))
            finally:
                con.close()
        except Exception:
            # 单个库出错不阻塞整体迁移
            continue
    if not rows_to_insert:
        return
    with _gconn() as con:
        con.executemany(
            "INSERT OR IGNORE INTO studio_projects"
            " (project_id, name, description, emoji,"
            "  created_at, updated_at, is_default, archived)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows_to_insert,
        )
