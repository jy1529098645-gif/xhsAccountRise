"""Quick schema dump for xhs.db."""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "xhs.db"

def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()
    rows = cur.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for name, sql in rows:
        print(f"-- {name}")
        print(sql)
        print()
        count = cur.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"-- rows: {count}")
        print()
    con.close()

if __name__ == "__main__":
    main()
