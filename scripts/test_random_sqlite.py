"""Upload a totally non-social SQLite (an e-commerce-shaped DB) and verify
the whole flow doesn't crash. Worst case the report says 'this is not a
social DB' but the user still gets a response."""
import io, json, os, sqlite3, sys, tempfile
import urllib.request as ur

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
con = sqlite3.connect(path)
con.executescript("""
CREATE TABLE products (
    sku TEXT PRIMARY KEY,
    name TEXT,
    price REAL,
    stock INTEGER,
    description TEXT
);
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    sku TEXT,
    quantity INTEGER,
    ordered_at INTEGER
);
""")
import random
for i in range(40):
    con.execute(
        "INSERT INTO products VALUES (?,?,?,?,?)",
        (f"SKU{i:03d}", f"产品 {i}", round(10 + i*0.5, 2), random.randint(0, 100),
         f"这是产品 {i} 的描述，主打 {random.choice(['性价比', '高端', '入门'])} 路线。"),
    )
for i in range(80):
    con.execute(
        "INSERT INTO orders VALUES (?,?,?,?)",
        (f"O{i:04d}", f"SKU{i % 40:03d}", random.randint(1, 5), 1700000000 + i*3600),
    )
con.commit(); con.close()
print(f"built random non-social db: {path} ({os.path.getsize(path)} bytes)")


def call(m, p, body=None, headers=None, timeout=300):
    h = headers or {}
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode(); h.setdefault("Content-Type", "application/json")
    req = ur.Request(f"http://127.0.0.1:8765{p}", data=body, method=m, headers=h)
    with ur.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# Multipart upload
boundary = "----RandomTest"
parts = []
for k, v in [("display_name", "random-shop"), ("platform", "auto"),
             ("activate", "1"), ("analyze", "1"), ("auto_adapt", "1")]:
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
with open(path, "rb") as fh:
    blob = fh.read()
parts.append(
    f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
    f"filename=\"shop.db\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
)
parts.append(blob); parts.append(f"\r\n--{boundary}--\r\n".encode())
body = b"".join(parts)

print()
print("POST /api/libraries/import ...")
imp = call("POST", "/api/libraries/import", body=body,
           headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
print(f"  lib_id: {imp['lib_id']}")
print(f"  notes_count (reported): {imp.get('notes_count')}")
print(f"  source_tables: {imp.get('source_tables')}")
print(f"  schema_suggestions:")
for s in (imp.get("schema_suggestions") or []):
    print(f"    · {s[:120]}")
print(f"  adapter.adapted: {imp.get('adapter', {}).get('adapted')}")
print(f"  adapter.notes_rows: {imp.get('adapter', {}).get('notes_rows')}")
print(f"  analyze_error: {imp.get('analyze_error')!r}")
print(f"  analyzed: {imp.get('analyzed')}")
print(f"  dna_version: {imp.get('dna_version')}")
print(f"  section_errors: {list((imp.get('section_errors') or {}).keys())}")
print()

# Now run insight
print("POST /api/insight/run (Haiku for speed) ...")
try:
    rep = call("POST", "/api/insight/run", body={
        "library_id": imp["lib_id"],
        "claude_spec": "claude:haiku",
        "openai_spec": "openai",
        "moderator_spec": "claude:haiku",
    }, timeout=600)
    c = rep.get("consensus") or {}
    print(f"  ★ report_id: {rep['report_id']}, status: {rep['status']}, elapsed: {rep['elapsed_s']}s")
    print(f"  ★ title: {c.get('title')}")
    print(f"  ★ executive: {(c.get('executive_summary') or '')[:240]}")
    print(f"  ★ consensus_findings ({len(c.get('consensus_findings') or [])}):")
    for f in (c.get("consensus_findings") or [])[:3]:
        print(f"     · {f.get('title')}")
    print(f"  ★ single_side_views ({len(c.get('single_side_views') or [])})")
except Exception as e:
    print(f"  insight failed: {e}")

os.unlink(path)
