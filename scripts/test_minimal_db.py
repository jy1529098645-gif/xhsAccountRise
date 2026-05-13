"""Build a minimal xhs-schema .db (notes only, no discover_queue) to
reproduce the user's 'no DNA artifact' bug and verify the fix."""
import io, os, sqlite3, sys, tempfile, urllib.request, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Create an in-memory minimal xhs schema, then export to bytes.
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
con = sqlite3.connect(path)
con.executescript("""
CREATE TABLE notes (
    note_id TEXT PRIMARY KEY,
    title TEXT,
    body TEXT,
    type TEXT,
    author_id TEXT,
    author_nickname TEXT,
    publish_time_ms INTEGER,
    liked_count INTEGER,
    collected_count INTEGER,
    comment_count INTEGER,
    share_count INTEGER,
    image_count INTEGER,
    tags_json TEXT
);
CREATE TABLE comments (
    comment_id TEXT PRIMARY KEY,
    note_id TEXT,
    content TEXT,
    like_count INTEGER,
    publish_time_ms INTEGER
);
-- intentionally NO discover_queue table to mimic xhs3000-like exports
""")
# Insert a few rows so the analyse_titles section has data
for i in range(50):
    con.execute(
        "INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"n{i}", f"测试标题 {i}：3个降AI率神器", f"正文 {i}" * 50,
         "normal", f"u{i}", f"作者{i}", 1700000000000 + i * 86400000,
         100 + i * 10, 50 + i, 5 + i, 1, 6, '["AI","降重"]'),
    )
for i in range(20):
    con.execute(
        "INSERT INTO comments VALUES (?,?,?,?,?)",
        (f"c{i}", f"n{i % 50}", f"评论内容{i} 求 prompt", 3 + i, 1700000000000),
    )
con.commit(); con.close()

print(f"Created minimal db at {path}, size {os.path.getsize(path)} bytes")

# Upload via /api/libraries/import
import urllib.request as ur
import mimetypes
boundary = "----Boundary123"
fields = [
    ("display_name", "minimal-test"),
    ("platform", "auto"),
    ("activate", "1"),
    ("analyze", "1"),
]
body_parts: list[bytes] = []
for k, v in fields:
    body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
with open(path, "rb") as fh:
    file_bytes = fh.read()
body_parts.append(
    f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"minimal.db\"\r\n"
    f"Content-Type: application/octet-stream\r\n\r\n".encode()
)
body_parts.append(file_bytes)
body_parts.append(f"\r\n--{boundary}--\r\n".encode())
body = b"".join(body_parts)

req = ur.Request(
    "http://127.0.0.1:8765/api/libraries/import",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    method="POST",
)
try:
    with ur.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
except Exception as e:
    print("Upload failed:", e)
    sys.exit(1)
print()
print("=== /api/libraries/import response ===")
print(json.dumps(result, ensure_ascii=False, indent=2))
print()
print("analyzed:", result.get("analyzed"))
print("dna_version:", result.get("dna_version"))
print("section_errors:", result.get("section_errors"))
print("schema_warnings:", result.get("schema_warnings"))

# Try insight
if result.get("analyzed"):
    print()
    print("=== /api/insight/run ===")
    req = ur.Request(
        "http://127.0.0.1:8765/api/insight/run",
        data=json.dumps({
            "library_id": result["lib_id"],
            "claude_spec": "claude:haiku",
            "openai_spec": "openai",
            "moderator_spec": "claude:haiku",
        }).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with ur.urlopen(req, timeout=300) as resp:
            r = json.loads(resp.read().decode("utf-8"))
        print(f"report_id: {r['report_id']}, elapsed: {r['elapsed_s']}s, status: {r['status']}")
        print(f"consensus title: {(r.get('consensus') or {}).get('title')}")
        print(f"consensus_findings: {len((r.get('consensus') or {}).get('consensus_findings') or [])}")
    except Exception as e:
        print("Insight call failed:", e)

os.unlink(path)
