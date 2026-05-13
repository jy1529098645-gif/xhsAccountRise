import sqlite3
print("sqlite_version:", sqlite3.sqlite_version)
con = sqlite3.connect(":memory:")
try:
    con.execute('CREATE VIRTUAL TABLE fts USING fts5(x, tokenize="trigram")')
    con.execute("INSERT INTO fts(x) VALUES ('小红书写论文神器ChatGPT')")
    for q in ("论文", "写论文", "神器", "ChatGPT"):
        rows = con.execute("SELECT x FROM fts WHERE fts MATCH ?", (q,)).fetchall()
        print(f"  match {q!r}: {len(rows)} rows")
except Exception as e:
    print("trigram FAIL:", e)
