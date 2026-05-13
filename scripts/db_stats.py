"""Quick stats on xhs.db to understand current data shape."""
import io
import sqlite3
import sys
from pathlib import Path

# Force UTF-8 stdout so xhs titles (full unicode) print correctly on Windows GBK consoles.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DB = Path(__file__).resolve().parent.parent / "data" / "xhs.db"

def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()

    def q(sql: str, *args):
        return cur.execute(sql, args).fetchone()

    print("=== notes ===")
    print(f"  total:           {q('SELECT COUNT(*) FROM notes')[0]}")
    print(f"  with body>100:   {q('SELECT COUNT(*) FROM notes WHERE LENGTH(body) > 100')[0]}")
    print(f"  with title:      {q('SELECT COUNT(*) FROM notes WHERE title IS NOT NULL AND title != \"\"')[0]}")
    print(f"  type=normal:     {q('SELECT COUNT(*) FROM notes WHERE type = \"normal\"')[0]}")
    print(f"  type=video:      {q('SELECT COUNT(*) FROM notes WHERE type = \"video\"')[0]}")
    print(f"  distinct authors: {q('SELECT COUNT(DISTINCT author_id) FROM notes')[0]}")

    print()
    print("=== engagement totals ===")
    row = cur.execute(
        "SELECT SUM(liked_count), SUM(collected_count), SUM(comment_count), SUM(share_count) FROM notes"
    ).fetchone()
    print(f"  likes:     {row[0]:,}")
    print(f"  collected: {row[1]:,}")
    print(f"  comments:  {row[2]:,}")
    print(f"  shares:    {row[3]:,}")

    print()
    print("=== top 10 by likes ===")
    rows = cur.execute(
        "SELECT liked_count, title, author_nickname FROM notes "
        "WHERE title IS NOT NULL ORDER BY liked_count DESC LIMIT 10"
    ).fetchall()
    for r in rows:
        title = (r[1] or "")[:60]
        print(f"  {r[0]:>6}  {title}  — {r[2]}")

    print()
    print("=== discover_queue source_value top 30 ===")
    rows = cur.execute(
        "SELECT source_value, COUNT(*) c FROM discover_queue "
        "GROUP BY source_value ORDER BY c DESC LIMIT 30"
    ).fetchall()
    for sv, c in rows:
        print(f"  {c:>5}  {sv}")

    print()
    print("=== comments ===")
    print(f"  total:            {q('SELECT COUNT(*) FROM comments')[0]}")
    print(f"  distinct note_id: {q('SELECT COUNT(DISTINCT note_id) FROM comments')[0]}")

    con.close()

if __name__ == "__main__":
    main()
