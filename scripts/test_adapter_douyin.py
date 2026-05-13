"""Build a 'douyin-style' SQLite (videos/aweme_id/desc/digg_count schema)
and verify the AI adapter normalises it to the canonical schema, the DNA
pipeline runs, and the insight report can be generated."""
import io, json, os, sqlite3, sys, tempfile
import urllib.request as ur

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Build a douyin-like schema deliberately different from xhs
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
con = sqlite3.connect(path)
con.executescript("""
CREATE TABLE videos (
    aweme_id     TEXT PRIMARY KEY,
    desc         TEXT,           -- maps to title
    author_uid   TEXT,           -- maps to author_id
    nickname     TEXT,           -- maps to author_nickname
    create_time  INTEGER,        -- unix SECONDS — needs *1000
    digg_count   INTEGER,        -- liked_count
    comment_count INTEGER,
    share_count  INTEGER,
    share_url    TEXT,
    music_id     TEXT,
    duration_ms  INTEGER
);
CREATE TABLE aweme_comments (
    cid          TEXT PRIMARY KEY,
    aweme_id     TEXT,
    text         TEXT,           -- maps to content
    digg_count   INTEGER,        -- maps to like_count
    create_time  INTEGER         -- unix SECONDS
);
""")
import random
for i in range(80):
    con.execute(
        "INSERT INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (f"aw{i}", f"我把AI率从{60+i%30}降到{5+i%15}的方法", f"u{i%20}",
         f"作者{i%20}", 1700000000 + i*86400,
         100 + i*15, 5 + i, 3, f"https://douyin/v/{i}", "m1", 30000),
    )
for i in range(40):
    con.execute(
        "INSERT INTO aweme_comments VALUES (?,?,?,?,?)",
        (f"c{i}", f"aw{i % 80}", f"求一下你用的指令啊{i}", 3 + i, 1700000000),
    )
con.commit(); con.close()
print(f"built douyin-style db: {path} ({os.path.getsize(path)} bytes)")
print()


def call(method, url, body=None, headers=None, timeout=300):
    h = headers or {}
    if body is not None and isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    req = ur.Request(f"http://127.0.0.1:8765{url}", data=body, method=method, headers=h)
    with ur.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# Multipart upload
boundary = "----TestBoundary"
fields = [
    ("display_name", "douyin-test"),
    ("platform", "auto"),
    ("activate", "1"),
    ("analyze", "1"),
    ("auto_adapt", "1"),
]
parts = []
for k, v in fields:
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
with open(path, "rb") as fh:
    blob = fh.read()
parts.append(
    f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
    f"filename=\"douyin.db\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
)
parts.append(blob); parts.append(f"\r\n--{boundary}--\r\n".encode())
body = b"".join(parts)

print("POST /api/libraries/import (with adapter)...")
imp = call("POST", "/api/libraries/import", body=body,
           headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})

print(f"  lib_id: {imp['lib_id']}")
print(f"  platform detected: {imp.get('detected_platform')}")
print(f"  adapter: {imp.get('adapter')}")
print(f"  analyzed: {imp.get('analyzed')}")
print(f"  dna_version: {imp.get('dna_version')}")
print(f"  section_errors: {list((imp.get('section_errors') or {}).keys())}")
print()

# Confirm notes/comments are queryable through the adapter views.
print("Verifying canonical views work via /api/rag/search...")
try:
    r = call("GET", "/api/rag/search?q=AI&k=3&n=3", timeout=30)
    print(f"  rag.refs: {len(r.get('refs') or [])}")
    print(f"  rag.comments: {len(r.get('comments') or [])}")
except Exception as e:
    print(f"  rag failed: {e}")
print()

# Run insight report
print("POST /api/insight/run (Haiku for both, fast)...")
try:
    rep = call("POST", "/api/insight/run", body={
        "library_id": imp["lib_id"],
        "claude_spec": "claude:haiku",
        "openai_spec": "openai",
        "moderator_spec": "claude:haiku",
    }, timeout=600)
    print(f"  report_id: {rep['report_id']}, elapsed: {rep['elapsed_s']}s")
    c = rep.get("consensus") or {}
    print(f"  title: {c.get('title')}")
    print(f"  consensus_findings: {len(c.get('consensus_findings') or [])}")
except Exception as e:
    print(f"  insight failed: {e}")

os.unlink(path)
