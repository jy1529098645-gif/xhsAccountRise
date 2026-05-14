"""Schema adapter: turn any SQLite into our canonical (notes / comments) schema
via AI-inferred column mappings + SQLite TEMP VIEWs.

Why TEMP VIEWs and not "copy into a new DB":
    - Source file stays untouched (user expectations).
    - Mapping JSON lives in data/libraries/<lib_id>/schema_map.json — a
      hand-editable contract the user can fix when the AI guesses wrong.
    - TEMP VIEWs are per-connection, so we re-create them on every db.connect()
      call. That's a few ms overhead and lets us pick up edits to schema_map.json
      without restarting the server.
    - A TEMP VIEW named `notes` shadows a real `notes` table on the same
      connection, which lets us override a half-correct source schema by
      tweaking only the mapping file.

Canonical schema (what every analysis function expects):
    notes(note_id, title, body, liked_count, collected_count, comment_count,
          share_count, image_count, type, publish_time_ms, tags_json,
          author_id, author_nickname, url)
    comments(comment_id, note_id, content, like_count, publish_time_ms)

The AI is asked to look at the source schema + sample rows and produce a
column-by-column mapping with optional SQL expressions for type/format
conversion.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from . import config


SCHEMA_MAP_FILENAME = "schema_map.json"

# Canonical target columns. The dict value is a human-readable description that
# the AI uses to decide which source column matches.
CANONICAL_NOTES_COLUMNS: dict[str, str] = {
    "note_id":           "唯一 ID（必须）",
    "title":             "标题 / 视频描述 / 主文案（必须）",
    "body":              "正文 / 详细文案（可空）",
    "liked_count":       "点赞数（integer）",
    "collected_count":   "收藏数（integer，没有就用 0）",
    "comment_count":     "评论数（integer）",
    "share_count":       "转发数（integer，没有就用 0）",
    "image_count":       "图片张数（integer，没有就用 0）",
    "type":              "'normal' 或 'video'（没有就用 'normal'）",
    "publish_time_ms":   "发布时间 unix 毫秒时间戳（**注意：源若是秒级要乘 1000**）",
    "last_update_ms":    "最后更新时间毫秒（可 NULL）",
    "tags_json":         "标签 JSON 数组字符串（可空）",
    "author_id":         "作者 ID",
    "author_nickname":   "作者昵称",
    "url":               "永久链接（可空）",
    "video_url":         "视频地址（可 NULL）",
    "video_duration_ms": "视频时长毫秒（可 NULL）",
    "ip_location":       "IP 归属地（可 NULL）",
    "at_users_json":     "@用户 JSON 数组（可 NULL）",
    "xsec_token":        "（仅 xhs）xsec_token，没有就 NULL",
}

CANONICAL_COMMENTS_COLUMNS: dict[str, str] = {
    "comment_id":         "评论 ID（必须）",
    "note_id":            "外键 → notes.note_id（必须）",
    "parent_id":          "父评论 ID（可 NULL）",
    "user_id":            "评论用户 ID（可 NULL）",
    "nickname":           "评论用户昵称（可 NULL）",
    "content":            "评论内容文本",
    "like_count":         "评论点赞数",
    "sub_comment_count":  "子评论数（可 NULL）",
    "publish_time_ms":    "评论发布时间 unix 毫秒（**秒级要乘 1000**）",
    "ip_location":        "IP 归属地（可 NULL）",
    "pictures_json":      "评论附图 JSON 数组（可 NULL）",
}


# Tables whose presence indicates the canonical xhs schema (no adapter needed).
_CANONICAL_NOTES_REQUIRED = {"note_id", "title", "liked_count"}


def schema_map_path(lib_id: str) -> Path:
    from . import library
    return library.LIBRARIES_DIR / lib_id / SCHEMA_MAP_FILENAME


def load_map(lib_id: str) -> dict[str, Any] | None:
    p = schema_map_path(lib_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_map(lib_id: str, mapping: dict[str, Any]) -> Path:
    p = schema_map_path(lib_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---- Source schema inspection ------------------------------------------

_ENGAGEMENT_COL_HINTS = (
    "like", "digg", "fav", "collect", "share", "view", "play",
    "comment_count", "repost", "click", "engagement",
)
_CONTENT_COL_HINTS = (
    "title", "desc", "body", "content", "text", "caption", "message",
)


def _pick_engagement_col(cols: list[dict[str, Any]]) -> str | None:
    """Find a column that looks like an engagement signal (likes/views/etc)
    so we can ORDER BY it and surface the most useful sample rows."""
    for c in cols:
        n = (c["name"] or "").lower()
        if any(h in n for h in _ENGAGEMENT_COL_HINTS):
            t = (c["type"] or "").upper()
            if "INT" in t or "REAL" in t or "NUM" in t or t == "":
                return c["name"]
    return None


def _truncate(v: Any, n: int = 200) -> Any:
    if v is None:
        return None
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"


def inspect_source(
    db_path: Path | str, *,
    sample_rows: int = 5,
    include_top_rows: bool = False,
    include_aggregates: bool = False,
    text_max: int = 200,
) -> dict[str, Any]:
    """Read an SQLite file and dump its tables, columns, and sample rows.

    Args:
        sample_rows: how many random/leading rows per table.
        include_top_rows: also include rows sorted by an engagement-like column
            (likes/views/etc) when one is detected. Helpful for AI context.
        include_aggregates: add COUNT/MIN/MAX/AVG/distinct on numeric cols.
        text_max: per-cell text length cap (so the prompt stays manageable).
    """
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    cur = con.cursor()
    tables_meta: list[dict[str, Any]] = []
    try:
        table_names = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%'"
        )]
        for name in table_names:
            try:
                cols_info = [
                    {"name": row[1], "type": row[2], "notnull": bool(row[3])}
                    for row in cur.execute(f"PRAGMA table_info('{name}')")
                ]
            except sqlite3.OperationalError:
                cols_info = []
            col_names = [c["name"] for c in cols_info]

            try:
                row_count = cur.execute(f"SELECT COUNT(*) FROM '{name}'").fetchone()[0]
            except sqlite3.OperationalError:
                row_count = 0

            try:
                samples = list(cur.execute(
                    f"SELECT * FROM '{name}' LIMIT ?", (sample_rows,)
                ))
                sample_rows_dicts = [
                    {col_names[i]: _truncate(v, text_max)
                     for i, v in enumerate(row)}
                    for row in samples
                ]
            except sqlite3.OperationalError:
                sample_rows_dicts = []

            top_rows: list[dict[str, Any]] = []
            eng_col = _pick_engagement_col(cols_info)
            if include_top_rows and eng_col:
                try:
                    quoted = '"' + eng_col.replace('"', '""') + '"'
                    rows = list(cur.execute(
                        f"SELECT * FROM '{name}' "
                        f"WHERE {quoted} IS NOT NULL "
                        f"ORDER BY {quoted} DESC LIMIT 10"
                    ))
                    top_rows = [
                        {col_names[i]: _truncate(v, text_max)
                         for i, v in enumerate(row)}
                        for row in rows
                    ]
                except sqlite3.OperationalError:
                    pass

            aggs: dict[str, Any] = {}
            if include_aggregates:
                for c in cols_info:
                    cn = c["name"]
                    ct = (c["type"] or "").upper()
                    is_num = "INT" in ct or "REAL" in ct or "NUM" in ct
                    is_text = "TEXT" in ct or "CHAR" in ct or "CLOB" in ct or ct == ""
                    try:
                        if is_num:
                            r = cur.execute(
                                f"SELECT COUNT(*), AVG({cn!r}), MIN({cn!r}), MAX({cn!r})"
                                f" FROM '{name}' WHERE {cn!r} IS NOT NULL"
                                .replace("'", '"')
                            ).fetchone()
                            if r and r[0]:
                                aggs[cn] = {"non_null": r[0],
                                            "avg": float(r[1]) if r[1] is not None else None,
                                            "min": r[2], "max": r[3]}
                        elif is_text:
                            r = cur.execute(
                                f'SELECT COUNT(DISTINCT "{cn}"), '
                                f'AVG(LENGTH("{cn}")) FROM "{name}" '
                                f'WHERE "{cn}" IS NOT NULL'
                            ).fetchone()
                            if r and r[0]:
                                aggs[cn] = {"distinct": r[0],
                                            "avg_len": float(r[1]) if r[1] is not None else None}
                    except sqlite3.OperationalError:
                        continue

            tables_meta.append({
                "name": name,
                "columns": cols_info,
                "row_count": row_count,
                "samples": sample_rows_dicts,
                "top_rows": top_rows,
                "engagement_col": eng_col,
                "aggregates": aggs,
            })
    finally:
        con.close()
    return {"tables": tables_meta}


def is_canonical(source_info: dict[str, Any]) -> bool:
    """Quick check: does the source already have a usable `notes` table?"""
    for t in source_info.get("tables", []):
        if t["name"] == "notes":
            cols = {c["name"].lower() for c in t["columns"]}
            return _CANONICAL_NOTES_REQUIRED.issubset(cols)
    return False


# ---- AI inference ------------------------------------------------------

_SYSTEM_PROMPT = """\
你是数据库 schema 适配专家。用户给你一个 SQLite 数据库的表结构 + 样本行，请把它映射到我们的标准 schema：

【目标 schema · notes 表】每个字段都要尽量映射；找不到对应字段就返回 null。
{notes_cols}

【目标 schema · comments 表】可选；如果没有评论表就整体返回 null。
{comments_cols}

【输入】
你会收到 JSON：{{"tables": [{{"name": "...", "columns": [...], "samples": [...]}}]}}

【输出】
严格 JSON，结构：

{{
  "notes": {{
    "source_table": "<选哪个源表来映射 notes>",
    "columns": {{
      "note_id":      {{"source": "<源列名 or null>", "expr": "<可选 SQL 表达式>"}},
      "title":        {{"source": "...", "expr": null}},
      "...":          ...
    }},
    "extra_filters": "<可选 WHERE 子句，例如 WHERE deleted=0>"
  }},
  "comments": {{
    "source_table": "...",
    "columns": {{"comment_id": {{...}}, ...}},
    "extra_filters": "..."
  }} or null,
  "reasoning": "<2-3 句话解释你怎么选的 source_table>"
}}

【强制规则】
- 必须存在 note_id、title、liked_count 的映射，否则报告无法分析。
- "source" 字段：源 column 名（区分大小写要按 PRAGMA 输出来）。
- "expr" 字段（可选）：如果需要类型转换，给一段 SELECT 列表里能用的 SQL 片段，比如：
    - 时间戳转毫秒：`CAST(strftime('%s', publish_time) AS INTEGER) * 1000`
    - tag 数组转 JSON：`json_array(tag1, tag2)` 或直接 `tags`（如果已经是 JSON 串）
    - 整数转换：`CAST(digg_count AS INTEGER)`
  写表达式时**不要**带 `AS xxx`（我们会自己加别名）。
- type 字段：如果源是抖音/快手 → 多数行 `'video'`；如果源是图文 → `'normal'`。如果有 video_url 字段就 `CASE WHEN video_url IS NOT NULL THEN 'video' ELSE 'normal' END`。
- 不知道的字段返回 `{{"source": null, "expr": "0"}}`（数值）或 `{{"source": null, "expr": "''"}}`（字符串）或 `{{"source": null, "expr": "NULL"}}`（可空）。
- url 字段没有就用 NULL。
"""


def _format_canonical(cols: dict[str, str]) -> str:
    return "\n".join(f"  - {k}: {v}" for k, v in cols.items())


def _build_user_message(source_info: dict[str, Any]) -> str:
    # Trim sample rows to keep the prompt compact (~3 rows per table is enough).
    tables = []
    for t in source_info.get("tables", []):
        tables.append({
            "name": t["name"],
            "row_count": t["row_count"],
            "columns": [{"name": c["name"], "type": c["type"]} for c in t["columns"]],
            "samples": t["samples"][:3],
        })
    return (
        f"【源数据库表结构 + 样本】\n"
        f"{json.dumps({'tables': tables}, ensure_ascii=False, indent=2)}\n\n"
        f"请按 system 输出 JSON 映射。"
    )


async def propose_with_llm(source_info: dict[str, Any],
                            llm_spec: str = "claude:sonnet") -> dict[str, Any]:
    """Ask an LLM to propose a schema mapping. Returns the mapping dict."""
    from .generators import registry

    system = _SYSTEM_PROMPT.format(
        notes_cols=_format_canonical(CANONICAL_NOTES_COLUMNS),
        comments_cols=_format_canonical(CANONICAL_COMMENTS_COLUMNS),
    )
    user = _build_user_message(source_info)

    gen = registry.build(llm_spec)[0]
    client = gen._ensure_client()  # noqa: SLF001
    family = gen.name

    schema = {
        "type": "object",
        "required": ["notes"],
        "properties": {
            "notes": {
                "type": "object",
                "properties": {
                    "source_table": {"type": "string"},
                    "columns": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "properties": {
                                "source": {"type": ["string", "null"]},
                                "expr": {"type": ["string", "null"]},
                            },
                        },
                    },
                    "extra_filters": {"type": ["string", "null"]},
                },
            },
            "comments": {
                "type": ["object", "null"],
                "properties": {
                    "source_table": {"type": "string"},
                    "columns": {"type": "object"},
                    "extra_filters": {"type": ["string", "null"]},
                },
            },
            "reasoning": {"type": "string"},
        },
    }

    if family == "claude":
        resp = await client.messages.create(
            model=gen.model, max_tokens=3000, system=system,
            messages=[{"role": "user", "content": user}],
            tools=[{"name": "submit_map", "description": "Submit schema map JSON.",
                    "input_schema": schema}],
            tool_choice={"type": "tool", "name": "submit_map"},
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return block.input
        raise RuntimeError("no tool_use in adapter response")
    # OpenAI-compatible
    resp = await client.chat.completions.create(
        model=gen.model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"}, max_tokens=3000,
    )
    return json.loads(resp.choices[0].message.content or "{}")


# ---- SQL VIEW generation -----------------------------------------------

_DEFAULT_EXPRS: dict[str, str] = {
    # notes
    "note_id":           "NULL",
    "title":             "''",
    "body":              "''",
    "liked_count":       "0",
    "collected_count":   "0",
    "comment_count":     "0",
    "share_count":       "0",
    "image_count":       "0",
    "type":              "'normal'",
    "publish_time_ms":   "NULL",
    "last_update_ms":    "NULL",
    "tags_json":         "NULL",
    "author_id":         "NULL",
    "author_nickname":   "''",
    "url":               "NULL",
    "video_url":         "NULL",
    "video_duration_ms": "NULL",
    "ip_location":       "NULL",
    "at_users_json":     "NULL",
    "xsec_token":        "NULL",
    # comments
    "comment_id":        "NULL",
    "parent_id":         "NULL",
    "user_id":           "NULL",
    "nickname":          "''",
    "content":           "''",
    "like_count":        "0",
    "sub_comment_count": "0",
    "pictures_json":     "NULL",
}


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _build_select(spec: dict[str, Any], canonical_cols: list[str]) -> tuple[str, str, str]:
    """Returns (column_select_list, source_table, where_clause)."""
    cols = spec.get("columns") or {}
    source_table = spec.get("source_table")
    if not source_table:
        raise ValueError("schema map missing source_table")
    parts: list[str] = []
    for target in canonical_cols:
        cm = cols.get(target) or {}
        src = cm.get("source")
        expr = cm.get("expr")
        if expr:  # explicit expression takes precedence
            sql_expr = expr
        elif src:
            sql_expr = _quote_ident(src)
        else:
            sql_expr = _DEFAULT_EXPRS.get(target, "NULL")
        parts.append(f"{sql_expr} AS {target}")
    where = spec.get("extra_filters") or ""
    if where and not where.strip().lower().startswith("where"):
        where = "WHERE " + where
    return ",\n  ".join(parts), source_table, where


def _empty_view_sql(view_name: str, canonical_cols: list[str]) -> str:
    """0-row view with all canonical columns. Lets downstream SELECTs succeed."""
    parts = [f"{_DEFAULT_EXPRS.get(c, 'NULL')} AS {c}" for c in canonical_cols]
    return (
        f"DROP VIEW IF EXISTS temp.{view_name};\n"
        f"CREATE TEMP VIEW {view_name} AS\n"
        f"SELECT\n  {', '.join(parts)}\n"
        f"WHERE 0;"
    )


def build_view_sql(mapping: dict[str, Any]) -> list[str]:
    """Always emits BOTH `notes` and `comments` views — real if the mapping
    has a source_table, else a 0-row placeholder. Downstream SQL never
    crashes from a missing view."""
    statements: list[str] = []

    notes_spec = mapping.get("notes")
    if notes_spec and notes_spec.get("source_table"):
        try:
            sel, table, where = _build_select(
                notes_spec, list(CANONICAL_NOTES_COLUMNS.keys()),
            )
            statements.append(
                f"DROP VIEW IF EXISTS temp.notes;\n"
                f"CREATE TEMP VIEW notes AS\n"
                f"SELECT\n  {sel}\n"
                f"FROM {_quote_ident(table)}"
                + (f"\n{where}" if where else "")
                + ";"
            )
        except Exception:
            statements.append(_empty_view_sql("notes", list(CANONICAL_NOTES_COLUMNS.keys())))
    else:
        statements.append(_empty_view_sql("notes", list(CANONICAL_NOTES_COLUMNS.keys())))

    comments_spec = mapping.get("comments")
    if comments_spec and comments_spec.get("source_table"):
        try:
            sel, table, where = _build_select(
                comments_spec, list(CANONICAL_COMMENTS_COLUMNS.keys()),
            )
            statements.append(
                f"DROP VIEW IF EXISTS temp.comments;\n"
                f"CREATE TEMP VIEW comments AS\n"
                f"SELECT\n  {sel}\n"
                f"FROM {_quote_ident(table)}"
                + (f"\n{where}" if where else "")
                + ";"
            )
        except Exception:
            statements.append(_empty_view_sql("comments", list(CANONICAL_COMMENTS_COLUMNS.keys())))
    else:
        statements.append(_empty_view_sql("comments", list(CANONICAL_COMMENTS_COLUMNS.keys())))

    return statements


def apply_views(con: sqlite3.Connection, mapping: dict[str, Any]) -> None:
    """Execute all CREATE TEMP VIEW statements on an open connection."""
    for stmt in build_view_sql(mapping):
        try:
            con.executescript(stmt)
        except sqlite3.OperationalError as e:
            # Don't kill the connection — record on a side channel via PRAGMA?
            # For now log via a custom table; the import endpoint already
            # captures section_errors so unmapped sections degrade gracefully.
            print(f"[adapt] view setup failed: {e}\n  stmt={stmt[:240]}")


# ---- Orchestrator ------------------------------------------------------

async def adapt_library(lib_id: str, *, llm_spec: str = "claude:sonnet") -> dict[str, Any]:
    """End-to-end: inspect → AI-propose map → save map. Even on total failure
    we save an empty mapping so placeholder views exist."""
    from . import library
    meta = library.get_meta(lib_id)
    if meta is None:
        raise LookupError(f"library not found: {lib_id}")
    db_path = library.LIBRARIES_DIR / lib_id / "xhs.db"
    source = inspect_source(db_path)
    if is_canonical(source):
        p = schema_map_path(lib_id)
        if p.exists():
            p.unlink()
        return {"adapted": False, "reason": "canonical xhs schema, no map needed",
                "source_tables": [t["name"] for t in source["tables"]]}

    if not source.get("tables"):
        save_map(lib_id, {"notes": None, "comments": None,
                          "reasoning": "源数据库无表 — 仅创建占位 view"})
        return {"adapted": True, "mapping": {}, "notes_rows": 0,
                "source_tables": [], "view_error": None,
                "reasoning": "源数据库无表"}

    try:
        mapping = await propose_with_llm(source, llm_spec=llm_spec)
    except Exception as e:
        save_map(lib_id, {"notes": None, "comments": None,
                          "reasoning": f"AI 推断失败 ({e!r})，使用占位 view"})
        return {"adapted": True, "mapping": {}, "notes_rows": 0,
                "view_error": f"ai_failed: {e!r}",
                "source_tables": [t["name"] for t in source["tables"]]}

    save_map(lib_id, mapping)
    test_con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    apply_views(test_con, mapping)
    try:
        n = test_con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    except sqlite3.OperationalError as e:
        test_con.close()
        return {"adapted": True, "mapping": mapping, "view_error": str(e),
                "notes_rows": 0,
                "source_tables": [t["name"] for t in source["tables"]]}
    test_con.close()
    return {"adapted": True, "mapping": mapping, "notes_rows": n,
            "source_tables": [t["name"] for t in source["tables"]]}
