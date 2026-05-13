"""Debug what FTS5 trigram returns for various query shapes."""
import sqlite3
con = sqlite3.connect("H:/xhsAccountRise/data/xhs.db")
con.row_factory = sqlite3.Row

cases = [
    '"降AI率技巧"',
    '降AI率技巧',
    '"降AI率"',
    '降AI率',
    '"AI率"',
    '"降A"',
    '"降AI"',
    '"AI率"',
    '"降AI率" OR "AI率技" OR "率技巧"',
    '"降AI" OR "AI率" OR "率技"',
    '"降ai率"',
]
for q in cases:
    try:
        n = con.execute("SELECT COUNT(*) FROM studio_fts_notes WHERE studio_fts_notes MATCH ?", (q,)).fetchone()[0]
        print(f"  {q!r:40}  -> {n} hits")
    except Exception as e:
        print(f"  {q!r:40}  -> ERROR {e}")

print()
print("sample titles containing '降AI':")
for r in con.execute("SELECT title FROM notes WHERE title LIKE '%降AI%' OR title LIKE '%降ai%' LIMIT 8"):
    print(f"  {r['title']}")
print()
print("sample titles containing '降':")
for r in con.execute("SELECT title FROM notes WHERE title LIKE '%降%' ORDER BY liked_count DESC LIMIT 8"):
    print(f"  {r['title']}")
