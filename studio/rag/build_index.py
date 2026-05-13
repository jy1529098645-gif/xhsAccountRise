"""Rebuild the FTS5 indices from the canonical notes / comments tables.

Cheap at our scale (~10K notes, 1.5K comments → <2s). Idempotent: deletes &
re-inserts every time, so it can be safely re-run after a fresh crawl.
"""
from __future__ import annotations

import time

from .. import db


def rebuild_notes() -> int:
    with db.connect() as con:
        con.execute("DELETE FROM studio_fts_notes")
        cur = con.execute(
            "SELECT note_id, title, body FROM notes"
            " WHERE (title IS NOT NULL AND title != '')"
            " OR (body IS NOT NULL AND body != '')"
        )
        n = 0
        rows: list[tuple] = []
        for r in cur:
            rows.append((r["note_id"], r["title"] or "", r["body"] or ""))
            n += 1
            if len(rows) >= 1000:
                con.executemany(
                    "INSERT INTO studio_fts_notes (note_id, title, body)"
                    " VALUES (?, ?, ?)",
                    rows,
                )
                rows.clear()
        if rows:
            con.executemany(
                "INSERT INTO studio_fts_notes (note_id, title, body)"
                " VALUES (?, ?, ?)",
                rows,
            )
    return n


def rebuild_comments() -> int:
    with db.connect() as con:
        con.execute("DELETE FROM studio_fts_comments")
        cur = con.execute(
            "SELECT comment_id, note_id, content FROM comments"
            " WHERE content IS NOT NULL AND content != ''"
        )
        n = 0
        rows: list[tuple] = []
        for r in cur:
            rows.append((r["comment_id"], r["note_id"], r["content"]))
            n += 1
            if len(rows) >= 1000:
                con.executemany(
                    "INSERT INTO studio_fts_comments"
                    " (comment_id, note_id, content) VALUES (?, ?, ?)",
                    rows,
                )
                rows.clear()
        if rows:
            con.executemany(
                "INSERT INTO studio_fts_comments"
                " (comment_id, note_id, content) VALUES (?, ?, ?)",
                rows,
            )
    return n


def rebuild_all() -> dict[str, int]:
    t0 = time.time()
    notes_n = rebuild_notes()
    comments_n = rebuild_comments()
    return {
        "notes_indexed": notes_n,
        "comments_indexed": comments_n,
        "elapsed_s": round(time.time() - t0, 2),
    }
